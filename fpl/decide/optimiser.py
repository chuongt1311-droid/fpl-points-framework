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

from fpl.project import project as project_mod

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def optimise_squad(
    players: pd.DataFrame, config: Optional[dict] = None, apply_availability_filters: bool = True,
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
        pool = pool[~pool["status"].isin(["i", "s", "u"])]  # never select a definitely-unavailable player
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

    # Spec §4.4: a small epsilon weight on every squad member (starters
    # included, so this ADDS to their existing full weight rather than
    # replacing it) breaks ties the solver was previously indifferent to —
    # equally-priced eligible bench players used to be arbitrary — and
    # approximates real autosub value. Sized (config.optimiser.
    # bench_weight_epsilon, default near-zero) to never outbid a genuine
    # starting-XI improvement: it only matters when two candidate squads'
    # STARTING xpts are otherwise tied, since a real starter-xpts
    # difference of even a fraction of a point dwarfs epsilon * a bench
    # player's xpts. Doesn't touch stage2's pick_xi_and_captain at all —
    # that's a separate LP on next_gw_xpts alone, so this can't leak into
    # which 11 actually start.
    prob += (
        pulp.lpSum(start[i] * xpts[i] for i in ids)
        + pulp.lpSum(captain[i] * xpts[i] for i in ids)
        + bench_weight_epsilon * pulp.lpSum(squad[i] * xpts[i] for i in ids)
    ), "total_expected_points"

    # Squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == rules["total"]
    for pos, count in [("GK", rules["gk"]), ("DEF", rules["def"]), ("MID", rules["mid"]), ("FWD", rules["fwd"])]:
        prob += pulp.lpSum(squad[i] for i in ids if position[i] == pos) == count

    # budget_tenths is in tenths-of-a-million (matches raw now_cost units);
    # `price` here is already converted to real £m by build_players.py, so
    # the budget must be converted the same way: /10, not /100.
    prob += pulp.lpSum(price[i] * squad[i] for i in ids) <= rules["budget_tenths"] / 10.0

    for club in set(team.values()):
        prob += pulp.lpSum(squad[i] for i in ids if team[i] == club) <= rules["max_per_club"]

    # Starting XI
    sx = rules["starting_xi"]
    prob += pulp.lpSum(start[i] for i in ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "GK") == sx["gk"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "DEF") >= sx["min_def"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "MID") >= sx["min_mid"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "FWD") >= sx["min_fwd"]
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

    squad_ids = [i for i in ids if squad[i].value() == 1]

    # STAGE 1 produced the 15. Its XI/captain variables existed only to shape
    # the squad correctly (11 strong + 4 cheap bench, rather than 15 mediocre)
    # and are DELIBERATELY DISCARDED here — see pick_xi_and_captain.
    horizon_xi = [i for i in ids if start[i].value() == 1]

    # STAGE 2: the XI and captain you actually submit, decided on the
    # gameweek actually being played.
    next_gw = dict(zip(pool["id"], pool["next_gw_xpts"]))
    starting_ids, captain_id, vice_captain_id = pick_xi_and_captain(
        squad_ids, next_gw, position, rules
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
    squad_ids: list, next_gw_xpts: dict, position: dict, rules: dict
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
    """
    sx = rules["starting_xi"]
    prob = pulp.LpProblem("fpl_xi", pulp.LpMaximize)
    start = pulp.LpVariable.dicts("xi_start", squad_ids, cat="Binary")
    cap = pulp.LpVariable.dicts("xi_captain", squad_ids, cat="Binary")

    prob += pulp.lpSum((start[i] + cap[i]) * next_gw_xpts[i] for i in squad_ids)

    prob += pulp.lpSum(start[i] for i in squad_ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "GK") == sx["gk"]
    prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "DEF") >= sx["min_def"]
    prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "MID") >= sx["min_mid"]
    prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "FWD") >= sx["min_fwd"]
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
