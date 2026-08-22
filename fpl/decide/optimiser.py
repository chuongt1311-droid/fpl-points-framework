"""
optimiser.py — DECIDE layer. Best 15-player squad + starting XI + captain,
within budget and squad rules — plan §6.1.

Constraints (real FPL rules):
  - 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
  - £100.0m budget
  - Max 3 players per club
  - Starting XI: 1 GK, min 3 DEF, min 2 MID, min 1 FWD, 11 total
  - Captain gets 2x (vice-captain = 2nd-highest xPts starter, as a fallback
    if captain doesn't play)

Method: TWO MILP solves via PuLP, because two different questions are being
asked and v1 answered both with the same number:

  Stage 1 — the 15.  Maximise 5-GW decay-weighted xPts of an XI-shaped
            selection, subject to budget / composition / max-3-per-club.
            Owning a player is a multi-week commitment, so it is judged on
            the horizon. The XI variables here exist only to shape the squad
            (11 strong + 4 cheap bench, not 15 mediocre); their values are
            discarded.
  Stage 2 — the XI, the captain, the vice.  Given the 15, maximise xPts for
            the ONE gameweek actually being played. These are re-decided
            every week and must never be chosen on a five-fixture blend.

Both solve in seconds, still "no heuristics needed" per plan §6.1. For GW1
with no existing team, this produces the initial squad directly.

plan §3.3: won't recommend a confidence='low' player unless
config.optimiser.allow_low_confidence is set — squad selection is
"transferring in" 15 players from nothing, so the same rule applies here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import pulp
import yaml

from fpl.decide import squad_state as squad_state_mod
from fpl.project import project as project_mod
from fpl.status import UNAVAILABLE_STATUSES

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _position_blank_rate(pool: pd.DataFrame) -> dict:
    """
    v3 plan §D4: bench value should be weighted by P(a starter in that
    position blanks) x bench_player_xpts, not a flat epsilon regardless of
    position — a bench GK covers a rarer event (GKs blank far less often)
    than a bench DEF/MID/FWD, so flat weighting over-values GK cover and
    under-values outfield cover relative to real autosub odds.

    Proxy (not a full model — no per-starter blank-probability estimator
    exists yet): for each position, 1 - the average minutes_factor (P(60+
    mins), see fpl/project/minutes.py) among that position's ABOVE-MEDIAN-
    PRICE players in the pool — a stand-in for "the players actually likely
    to start," avoiding the endogeneity of using the LP's own start[] result
    (which doesn't exist yet when this constant needs to be computed) or
    depending on a specific squad's chosen starters.

    Degrades gracefully to 1.0 for every position (equivalent to the old
    flat epsilon) if `minutes_factor` isn't present in the pool — e.g. the
    synthetic pools tests/test_bench_weight.py uses, which test the
    tie-breaking mechanism itself, not this position-specific refinement.
    """
    if "minutes_factor" not in pool.columns:
        return {"GK": 1.0, "DEF": 1.0, "MID": 1.0, "FWD": 1.0}
    rates = {}
    for pos in ["GK", "DEF", "MID", "FWD"]:
        pos_pool = pool[pool["position"] == pos]
        if pos_pool.empty:
            rates[pos] = 1.0
            continue
        likely_starters = pos_pool[pos_pool["price"] >= pos_pool["price"].median()]
        if likely_starters.empty or likely_starters["minutes_factor"].isna().all():
            rates[pos] = 1.0
            continue
        rates[pos] = float(1.0 - likely_starters["minutes_factor"].mean())
    return rates


def optimise_squad(
    players: pd.DataFrame, config: Optional[dict] = None, apply_availability_filters: bool = True,
    extra_no_good_cuts: Optional[list] = None,
    locked_ids: Optional[list] = None, banned_ids: Optional[list] = None,
    banned_clubs: Optional[list] = None, budget_override: Optional[float] = None,
    force_formation: Optional[dict] = None, chip: Optional[str] = None,
) -> dict:
    """
    players: one row per player with id, web_name, position, price, team,
    weighted_xpts, confidence, status (as produced by
    fpl.project.project.weighted_horizon_total, merged with status).

    apply_availability_filters: True (default, live decision-making) drops
    confidence='low' (unless config.optimiser.allow_low_confidence) and
    unavailable-status players before solving — the normal, real-world
    "don't recommend transferring in a player you can't confidently trust"
    behaviour. False is for fpl.evaluate.hindsight's retrospective "best
    £100m squad" benchmark (spec §3.3, decision D7): grading against
    REALITY, a player incorrectly flagged unavailable pre-GW who then
    played and hauled must still be eligible for that benchmark, or squad
    regret would be systematically understated.

    extra_no_good_cuts: v3 plan §D1 — list of previously-found squad id
    lists. Each adds Σ_{i in S} squad[i] <= |S| - diversity_d, forbidding
    the new solution from matching a prior one in diversity_d-or-more
    players. None (default) = the single best squad, unchanged behaviour.
    See fpl/decide/kbest.py for the iterative K-best caller.

    The following six are v3 plan §E2's what-if controls — every one
    defaults to None/unset, so the WEEKLY (frozen, GitHub-Actions) call
    path that never passes them is byte-identical to before. Only the
    LIVE Streamlit layer (dashboard/app.py) passes them, and every live
    solve is logged (see that file) — the optimiser itself doesn't know
    or care whether its caller is the frozen pipeline or an exploratory
    live one; per plan §E1, it's a pure function of its inputs either way.

    locked_ids: player ids that MUST be in the squad (x_i = 1). Raises
        RuntimeError (infeasible) if locking violates budget/composition —
        surfaced to the user as "this combination isn't a legal squad",
        not silently dropped.
    banned_ids / banned_clubs: player ids / team ids that MUST NOT be in
        the squad (x_i = 0), e.g. an explicit ban or every player at a
        club whose fixture just turned into a blank.
    budget_override: replaces config.squad_rules.budget_tenths/10.0 as the
        budget constraint's RHS — "what if I had £105m instead of £100m."
    force_formation: {"def": n, "mid": n, "fwd": n} — tightens the
        starting-XI position constraints from ">= min" to "== exact",
        e.g. forcing a 3-5-2. Omit a key to leave that position's
        constraint as the configured minimum.
    chip: "bench_boost" swaps the bench term's weight from
        config.optimiser.bench_weight_epsilon (tie-breaking only) to 1.0
        (full value) — Bench Boost scores all 15, not just the XI+captain,
        per plan §E2's chip table. "free_hit" is accepted but currently a
        no-op: plan §E2 describes it as "drop the transfer-cost term," but
        fpl/decide/transfers.py (the module that WOULD compute a
        transfer-cost term) doesn't exist yet, so there is nothing to
        drop — see dashboard/app.py's own UI note for this honestly
        surfaced limitation, not hidden as if the chip were fully modelled.

    Returns {"squad": [...15 ids...], "starting_xi": [...11...],
    "captain": id, "vice_captain": id, "total_cost": float,
    "expected_points": float} plus the full DataFrame with is_squad/
    is_starting/is_captain flags for inspection.
    """
    config = config or load_config()
    rules = config["squad_rules"]
    allow_low_confidence = config["optimiser"]["allow_low_confidence"]
    bench_weight_epsilon = config["optimiser"].get("bench_weight_epsilon", 0.0)

    # Loud, not silent. If this column ever goes missing, the old behaviour
    # (XI picked on the horizon number) is a plausible-looking wrong answer,
    # not a crash — exactly the failure mode that let the bug live this long.
    if "next_gw_xpts" not in players.columns:
        raise KeyError(
            "optimise_squad requires a 'next_gw_xpts' column — the XI and captain "
            "are per-gameweek decisions and must not fall back to weighted_xpts. "
            "Produce it via fpl.project.project.weighted_horizon_total."
        )

    pool = players.copy()
    if apply_availability_filters:
        if not allow_low_confidence:
            pool = pool[pool["confidence"] != "low"]
        pool = pool[~pool["status"].isin(UNAVAILABLE_STATUSES)]  # never select a definitely-unavailable player
    pool = pool.reset_index(drop=True)

    ids = pool["id"].tolist()
    xpts = dict(zip(pool["id"], pool["weighted_xpts"]))
    price = dict(zip(pool["id"], pool["price"]))
    position = dict(zip(pool["id"], pool["position"]))
    team = dict(zip(pool["id"], pool["team"]))

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", ids, cat="Binary")

    # Spec §4.4 / v3 plan §D4: a small epsilon weight, applied to (squad[i]
    # - start[i]) i.e. ONLY the bench portion (not double-counting starters,
    # who already get full weight via start[i]*xpts[i] below) — breaks ties
    # the solver was previously indifferent to (equally-priced eligible
    # bench players used to be arbitrary) and approximates real autosub
    # value. Scaled by _position_blank_rate(pool)[position] (v3 plan §D4:
    # "weight bench slots by P(a starter in that position blanks) x
    # bench_player_xpts" — a bench GK covers a rarer event than a bench
    # DEF/MID/FWD, so a flat weight over-values GK cover) — degrades to a
    # flat 1.0 multiplier (the old behaviour) when minutes_factor isn't in
    # the pool. Sized to never outbid a genuine starting-XI improvement: a
    # real starter-xpts difference of even a fraction of a point dwarfs
    # epsilon * blank_rate * a bench player's xpts. Doesn't touch stage2's
    # pick_xi_and_captain at all — that's a separate LP on next_gw_xpts
    # alone, so this can't leak into which 11 actually start.
    # plan §E2 chip scenario: Bench Boost scores all 15, not just the XI —
    # swap the bench term's weight from the tie-breaking epsilon to full
    # value (1.0) so the solver actually optimises total-squad points, not
    # just starting-XI points with an epsilon-sized bench nudge.
    effective_bench_weight = 1.0 if chip == "bench_boost" else bench_weight_epsilon

    blank_rate = _position_blank_rate(pool)
    bench_value = {i: blank_rate[position[i]] * xpts[i] for i in ids}
    prob += (
        pulp.lpSum(start[i] * xpts[i] for i in ids)
        + pulp.lpSum(captain[i] * xpts[i] for i in ids)
        + effective_bench_weight * pulp.lpSum((squad[i] - start[i]) * bench_value[i] for i in ids)
    ), "total_expected_points"

    # v3 plan §D1: no-good cuts excluding each previously-found squad, so
    # repeated calls (fpl/decide/kbest.py) walk down the K-best-with-
    # diversity frontier instead of the solver just returning the same
    # optimum again. diversity_d=1 gives pure K-best (next-best legal
    # squad, however similar); diversity_d>=2 forces genuine variety.
    for cut_ids, diversity_d in (extra_no_good_cuts or []):
        prob += pulp.lpSum(squad[i] for i in cut_ids if i in squad) <= len(cut_ids) - diversity_d

    # plan §E2 what-if controls — locks/bans are checked against the POOL,
    # not the full unfiltered input, so a lock request for a player already
    # excluded by apply_availability_filters fails loudly (infeasible) at
    # solve time rather than being silently ignored.
    for i in (locked_ids or []):
        if i in squad:
            prob += squad[i] == 1
    for i in (banned_ids or []):
        if i in squad:
            prob += squad[i] == 0
    if banned_clubs:
        for i in ids:
            if team[i] in banned_clubs:
                prob += squad[i] == 0

    # Squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == rules["total"]
    for pos, count in [("GK", rules["gk"]), ("DEF", rules["def"]), ("MID", rules["mid"]), ("FWD", rules["fwd"])]:
        prob += pulp.lpSum(squad[i] for i in ids if position[i] == pos) == count

    # budget_tenths is in tenths-of-a-million (matches raw now_cost units);
    # `price` here is already converted to real £m by build_players.py, so
    # the budget must be converted the same way: /10, not /100.
    # plan §E2: budget_override replaces the RHS wholesale (a live "what if
    # I had £X instead" question), not an addition to the configured budget.
    budget_limit = budget_override if budget_override is not None else rules["budget_tenths"] / 10.0
    prob += pulp.lpSum(price[i] * squad[i] for i in ids) <= budget_limit

    for club in set(team.values()):
        prob += pulp.lpSum(squad[i] for i in ids if team[i] == club) <= rules["max_per_club"]

    # Starting XI. plan §E2 force_formation tightens a position's ">= min"
    # to "== exact" when the caller specifies it (e.g. {"def": 3, "mid": 5,
    # "fwd": 2} for a 3-5-2) — any position not named keeps its configured
    # minimum, unchanged from the weekly path's behaviour.
    sx = rules["starting_xi"]
    force_formation = force_formation or {}
    prob += pulp.lpSum(start[i] for i in ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "GK") == sx["gk"]
    for pos_key, min_key in [("def", "min_def"), ("mid", "min_mid"), ("fwd", "min_fwd")]:
        pos = pos_key.upper()
        count_expr = pulp.lpSum(start[i] for i in ids if position[i] == pos)
        if pos_key in force_formation:
            prob += count_expr == force_formation[pos_key]
        else:
            prob += count_expr >= sx[min_key]
    for i in ids:
        prob += start[i] <= squad[i]

    # Captain: exactly one, must be a starter
    prob += pulp.lpSum(captain[i] for i in ids) == 1
    for i in ids:
        prob += captain[i] <= start[i]

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Optimiser did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    # The ACTUAL thing being maximised — starting-XI xpts + captain xpts +
    # epsilon*blank_rate*bench xpts — which is NOT the same as
    # horizon_weighted_xpts (the raw sum of all 15 squad members' xpts,
    # reported below for display only). A K-best/diversity caller
    # (fpl/decide/kbest.py) MUST rank alternatives on this value: ranking
    # on the raw 15-man sum instead is not monotonic with what the solver
    # actually optimised — a later, more-constrained solve can easily have
    # a HIGHER raw sum (a stronger bench, weaker starters) while being a
    # genuinely worse squad by the objective that picked it. Caught exactly
    # this while building kbest.py (frontier_spread came out positive for
    # a "later" squad — see docs/PROJECT_LOG.md).
    stage1_objective = pulp.value(prob.objective)

    squad_ids = [i for i in ids if squad[i].value() == 1]

    # STAGE 1 produced the 15. Its XI/captain variables existed only to shape
    # the squad correctly (11 strong + 4 cheap bench, rather than 15 mediocre)
    # and are DELIBERATELY DISCARDED here — see pick_xi_and_captain.
    horizon_xi = [i for i in ids if start[i].value() == 1]

    # STAGE 2: the XI and captain you actually submit, decided on the
    # gameweek actually being played.
    next_gw = dict(zip(pool["id"], pool["next_gw_xpts"]))
    starting_ids, captain_id, vice_captain_id = pick_xi_and_captain(
        squad_ids, next_gw, position, rules, force_formation=force_formation,
    )

    result_df = pool[pool["id"].isin(squad_ids)].copy()
    result_df["is_squad"] = True
    result_df["is_starting"] = result_df["id"].isin(starting_ids)
    result_df["is_captain"] = result_df["id"] == captain_id
    result_df["is_vice_captain"] = result_df["id"] == vice_captain_id

    total_cost = sum(price[i] for i in squad_ids)

    return {
        "squad": squad_ids,
        "starting_xi": starting_ids,
        "captain": captain_id,
        "vice_captain": vice_captain_id,
        "total_cost": total_cost,
        # Two separate numbers, never one ambiguous "expected_points":
        "next_gw_expected_points": sum(next_gw[i] for i in starting_ids) + next_gw[captain_id],
        "horizon_weighted_xpts": sum(xpts[i] for i in squad_ids),
        # What the stage-1 MILP actually maximised — see stage1_objective's
        # definition above. K-best/diversity ranking must use THIS, not
        # horizon_weighted_xpts.
        "stage1_objective": stage1_objective,
        # What stage 2 bought, in points: the same XI decision made on the
        # horizon number vs on the actual gameweek. 0.0 means both agreed.
        "xi_correction_gain": (
            sum(next_gw[i] for i in starting_ids) + next_gw[captain_id]
        ) - (
            sum(next_gw[i] for i in horizon_xi)
            + next_gw[max(horizon_xi, key=lambda i: xpts[i])]
        ),
        "detail": result_df,
    }


def pick_xi_and_captain(
    squad_ids: list, next_gw_xpts: dict, position: dict, rules: dict,
    force_formation: Optional[dict] = None,
) -> tuple[list, int, int]:
    """
    Given a fixed 15, choose the XI + captain + vice for ONE gameweek.

    This is a separate solve on purpose. Squad selection is a 5-GW asset
    decision (weighted_xpts); the XI and the armband are re-decided every
    single week and belong to THIS gameweek only (next_gw_xpts). Solving both
    off the horizon number — as v1 did — systematically benches players who
    are the right start this week and hands the armband to whoever looks best
    on a blend of five different fixtures.

    Kept as its own function rather than inlined because the weekly job,
    Bench Boost evaluation, and the retrospective "best XI of the week"
    all need exactly this operation against different point vectors.

    force_formation: v3 plan §E2 — REAL BUG FOUND AND FIXED while building
    the live layer: optimise_squad's own force_formation param only
    tightened stage 1's XI variables, which that function's own docstring
    says are shaping-only and DISCARDED (the real starting XI always comes
    from THIS function, stage 2). Passing force_formation here too is
    what actually makes a forced formation show up in the result the user
    sees — see optimise_squad's call site below. Same {"def"/"mid"/"fwd":
    n} shape, same "omit a key to keep the configured minimum" semantics.
    """
    sx = rules["starting_xi"]
    force_formation = force_formation or {}
    prob = pulp.LpProblem("fpl_xi", pulp.LpMaximize)
    start = pulp.LpVariable.dicts("xi_start", squad_ids, cat="Binary")
    cap = pulp.LpVariable.dicts("xi_captain", squad_ids, cat="Binary")

    prob += pulp.lpSum((start[i] + cap[i]) * next_gw_xpts[i] for i in squad_ids)

    prob += pulp.lpSum(start[i] for i in squad_ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "GK") == sx["gk"]
    for pos_key, min_key in [("def", "min_def"), ("mid", "min_mid"), ("fwd", "min_fwd")]:
        pos = pos_key.upper()
        count_expr = pulp.lpSum(start[i] for i in squad_ids if position[i] == pos)
        if pos_key in force_formation:
            prob += count_expr == force_formation[pos_key]
        else:
            prob += count_expr >= sx[min_key]
    prob += pulp.lpSum(cap[i] for i in squad_ids) == 1
    for i in squad_ids:
        prob += cap[i] <= start[i]

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"XI selection failed: {pulp.LpStatus[prob.status]}")

    starting_ids = [i for i in squad_ids if start[i].value() == 1]
    captain_id = next(i for i in squad_ids if cap[i].value() == 1)
    by_pts = sorted(starting_ids, key=lambda i: next_gw_xpts[i], reverse=True)
    vice_captain_id = next(i for i in by_pts if i != captain_id)
    return starting_ids, captain_id, vice_captain_id


def build_gw1_squad(config: Optional[dict] = None) -> dict:
    config = config or load_config()
    horizon = config["horizon"]["gameweeks"]
    projections = project_mod.project_gameweeks(horizon, config)
    totals = project_mod.weighted_horizon_total(projections, config)

    players = pd.read_parquet(Path(__file__).resolve().parents[2] / "data" / "processed" / "players.parquet")
    totals = totals.merge(players[["id", "status"]], on="id", how="left")

    result = optimise_squad(totals, config)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gw = projections["event"].min()
    out_path = OUTPUT_DIR / f"gw{gw}_recommendations.json"

    detail = result["detail"].sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
    squad_json = {
        "gameweek": int(gw),
        "total_cost": round(result["total_cost"], 1),
        "budget": config["squad_rules"]["budget_tenths"] / 10.0,
        # Two distinct quantities. v1 emitted a single "expected_points" that
        # was actually the 5-GW decay-weighted sum sitting next to
        # "gameweek": 1 — read by anything downstream as a one-week forecast.
        "next_gw_expected_points": round(result["next_gw_expected_points"], 2),
        "horizon_weighted_xpts": round(result["horizon_weighted_xpts"], 2),
        "xi_correction_gain": round(result["xi_correction_gain"], 2),
        "captain": {
            "id": int(result["captain"]),
            "web_name": detail.loc[detail["id"] == result["captain"], "web_name"].iloc[0],
        },
        "vice_captain": {
            "id": int(result["vice_captain"]),
            "web_name": detail.loc[detail["id"] == result["vice_captain"], "web_name"].iloc[0],
        },
        "squad": [
            {
                "id": int(r["id"]), "web_name": r["web_name"], "position": r["position"],
                "team": int(r["team"]), "price": r["price"], "weighted_xpts": round(r["weighted_xpts"], 2),
                "is_starting": bool(r["is_starting"]), "is_captain": bool(r["is_captain"]),
                "is_vice_captain": bool(r["is_vice_captain"]),
            }
            for _, r in detail.iterrows()
        ],
    }
    import json
    out_path.write_text(json.dumps(squad_json, indent=2, ensure_ascii=False), encoding="utf-8")

    # Spec §3.2: the missing prerequisite for regret — "the 15 you owned
    # that week." bank = leftover budget (no transfer INTO the initial
    # squad, so there's nothing else to compute it from); free_transfers=1
    # is what GW2 starts with, not a transfer made this GW.
    bank = config["squad_rules"]["budget_tenths"] / 10.0 - result["total_cost"]
    squad_state_mod.write_squad_state(gw, result, bank=bank, free_transfers=1)

    return result


if __name__ == "__main__":
    result = build_gw1_squad()
    detail = result["detail"].sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
    print(f"Total cost: £{result['total_cost']:.1f}m")
    print(f"Next-GW expected points (XI + captain): {result['next_gw_expected_points']:.2f}")
    print(f"Squad 5-GW weighted xPts:               {result['horizon_weighted_xpts']:.2f}")
    print(f"Gain from picking XI on the actual GW:  {result['xi_correction_gain']:+.2f}\n")
    print("STARTING XI:")
    starters = detail[detail["is_starting"]]
    print(starters[["web_name", "position", "team", "price", "weighted_xpts"]].to_string(index=False))
    print("\nBENCH:")
    bench = detail[~detail["is_starting"]]
    print(bench[["web_name", "position", "team", "price", "weighted_xpts"]].to_string(index=False))
    cap_name = detail.loc[detail["id"] == result["captain"], "web_name"].iloc[0]
    vc_name = detail.loc[detail["id"] == result["vice_captain"], "web_name"].iloc[0]
    print(f"\nCaptain: {cap_name} | Vice-captain: {vc_name}")
