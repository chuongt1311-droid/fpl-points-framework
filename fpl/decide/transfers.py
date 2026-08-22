"""
transfers.py — DECIDE layer, v4 plan §5 / spec
docs/superpowers/specs/2026-08-22-transfer-decision-layer-design.md.

Answers the question the squad optimiser cannot: "I own these 15, I have
N free transfers and £X in the bank — what do I do?"

PURE FUNCTION. No network, no reads of data/state/ — ingestion is
squad_state.py's job. Same discipline as optimise_squad, so this is
freely re-solvable offline and from the live dashboards (v3 §E1).

HORIZON (spec §3.2): the decision is made on weighted_xpts (the same
5-GW decay-weighted value squad selection uses), with the hit charged
ONCE. The v4 plan's literal single-gameweek objective would be
systematically hit-averse — a one-week gain would have to clear 4 points
to justify a hit, which almost never happens, making the -4 decorative.
The next-gameweek consequence is always reported alongside.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pulp

from fpl.decide import constraints
from fpl.decide.optimiser import pick_xi_and_captain
from fpl.status import UNAVAILABLE_STATUSES

_EPS = 1e-6


class InfeasibleBudgetError(Exception):
    """A recommended transfer set costs more than bank + sale proceeds."""


def _assert_affordable(transfers_in, transfers_out, price, sell_prices, bank) -> None:
    """
    Post-solve guard (spec §5.4), independent of the MILP constraint.

    The constraint being correct in theory is not the same as the INPUT
    numbers being correct, and this is the failure mode that produces a
    plausible, wrong, unactionable recommendation.
    """
    cost = sum(price[i] for i in transfers_in)
    proceeds = sum(sell_prices[i] for i in transfers_out)
    if cost > bank + proceeds + _EPS:
        raise InfeasibleBudgetError(
            f"Recommended transfers cost {cost:.1f} but only "
            f"{bank + proceeds:.1f} is available (bank {bank:.1f} + proceeds "
            f"{proceeds:.1f}). This recommendation is not executable."
        )


def _build_id_space(players: pd.DataFrame, current_squad_ids, apply_filters, allow_low_conf):
    """
    pool ∪ current_squad (spec §3.3).

    An owned player filtered out by availability MUST still be in the id
    space or `squad[p] = current[p] - out[p] + in[p]` is unsatisfiable for
    him. Note no explicit no-buy flag is needed: for an owned player
    current[p] = 1, so transfer_in[p] <= 1 - current[p] = 0 already
    forbids buying him. The union alone is the whole fix.
    """
    pool = players.copy()
    if apply_filters:
        if not allow_low_conf:
            pool = pool[pool["confidence"] != "low"]
        pool = pool[~pool["status"].isin(UNAVAILABLE_STATUSES)]
    owned = players[players["id"].isin(current_squad_ids)]
    space = pd.concat([pool, owned]).drop_duplicates(subset="id").reset_index(drop=True)
    return space


def solve_transfers(
    players: pd.DataFrame,
    current_squad_ids: list,
    sell_prices: dict,
    bank: float,
    free_transfers: int,
    config: Optional[dict] = None,
    apply_availability_filters: bool = True,
    force_n_transfers: Optional[int] = None,
    force_formation: Optional[dict] = None,
) -> dict:
    """
    One MILP: choose the transfer set maximising weighted squad value net
    of hits. `force_n_transfers` pins the transfer count exactly (used to
    build the roll baseline, and by tests).
    """
    if config is None:
        from fpl.decide.optimiser import load_config
        config = load_config()
    rules = config["squad_rules"]
    hit_cost = config["transfers"]["hit_cost"]
    allow_low_conf = config["optimiser"]["allow_low_confidence"]
    bench_eps = config["optimiser"].get("bench_weight_epsilon", 0.0)

    space = _build_id_space(players, current_squad_ids, apply_availability_filters,
                            allow_low_conf)
    ids = space["id"].tolist()
    missing = set(current_squad_ids) - set(ids)
    if missing:
        raise ValueError(
            f"Owned players missing from the input DataFrame entirely: {sorted(missing)}. "
            f"They cannot be represented, so no legal transfer set exists."
        )

    wx = dict(zip(space["id"], space["weighted_xpts"]))
    nx = dict(zip(space["id"], space["next_gw_xpts"]))
    price = dict(zip(space["id"], space["price"]))
    position = dict(zip(space["id"], space["position"]))
    team = dict(zip(space["id"], space["team"]))
    owned_set = set(current_squad_ids)
    current = {i: (1 if i in owned_set else 0) for i in ids}

    prob = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", ids, cat="Binary")
    t_in = pulp.LpVariable.dicts("t_in", ids, cat="Binary")
    t_out = pulp.LpVariable.dicts("t_out", ids, cat="Binary")
    penalized = pulp.LpVariable("penalized", lowBound=0, cat="Integer")

    # Transfer linking. For an owned player current=1 so t_in <= 0; for an
    # unowned one current=0 so t_out <= 0. Both directions fall out of the
    # same pair of constraints — no separate no-buy flag needed.
    for i in ids:
        prob += squad[i] == current[i] - t_out[i] + t_in[i]
        prob += t_out[i] <= current[i]
        prob += t_in[i] <= 1 - current[i]

    n_transfers_expr = pulp.lpSum(t_in[i] for i in ids)
    if force_n_transfers is not None:
        prob += n_transfers_expr == force_n_transfers
    prob += penalized >= n_transfers_expr - free_transfers
    prob += penalized >= 0

    # Transfer budget: what you buy must be covered by bank + what you sell.
    prob += (pulp.lpSum(price[i] * t_in[i] for i in ids)
             <= bank + pulp.lpSum(sell_prices.get(i, price[i]) * t_out[i] for i in ids))

    constraints.add_squad_composition(prob, squad, ids, position, rules)
    constraints.add_club_limits(prob, squad, ids, team, rules)
    constraints.add_xi_shape(prob, squad, start, ids, position, rules, force_formation)
    constraints.add_captain_rules(prob, start, captain, ids)

    prob += (
        pulp.lpSum(start[i] * wx[i] for i in ids)
        + pulp.lpSum(captain[i] * wx[i] for i in ids)
        + bench_eps * pulp.lpSum((squad[i] - start[i]) * wx[i] for i in ids)
        - hit_cost * penalized
    ), "weighted_points_net_of_hits"

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Transfer solve failed: {pulp.LpStatus[prob.status]}")

    new_squad = [i for i in ids if squad[i].value() == 1]
    t_in_ids = [i for i in ids if t_in[i].value() == 1]
    t_out_ids = [i for i in ids if t_out[i].value() == 1]

    _assert_affordable(t_in_ids, t_out_ids, price, sell_prices, bank)

    n = len(t_in_ids)
    hits = max(0, n - free_transfers)

    # Stage 2: XI + armband are a THIS-WEEK decision, re-picked on
    # next_gw_xpts via the existing solver — the same split optimise_squad
    # uses, reused rather than reimplemented.
    xi, cap_id, vice_id = pick_xi_and_captain(new_squad, nx, position, rules, force_formation)

    return {
        "squad": new_squad,
        "starting_xi": xi,
        "captain": cap_id,
        "vice_captain": vice_id,
        "transfers_in": t_in_ids,
        "transfers_out": t_out_ids,
        "n_transfers": n,
        "hits": hits,
        "hit_points": hits * hit_cost,
        "weighted_value": sum(wx[i] for i in new_squad),
        "weighted_objective": pulp.value(prob.objective),
        "next_gw_value": sum(nx[i] for i in xi) + nx[cap_id],
    }


def recommend(
    players: pd.DataFrame,
    current_squad_ids: list,
    sell_prices: dict,
    bank: float,
    free_transfers: int,
    config: Optional[dict] = None,
    apply_availability_filters: bool = True,
) -> dict:
    """
    Solve twice — a forced-roll baseline and the free optimum — so the
    reported gain is against an identical objective rather than an ad-hoc
    comparison, and "roll it" is a first-class answer rather than a
    special case.
    """
    baseline = solve_transfers(players, current_squad_ids, sell_prices, bank,
                               free_transfers, config, apply_availability_filters,
                               force_n_transfers=0)
    best = solve_transfers(players, current_squad_ids, sell_prices, bank,
                           free_transfers, config, apply_availability_filters)

    indexed = players.set_index("id")

    def _row(i):
        r = indexed.loc[i]
        return {"id": int(i), "web_name": r["web_name"], "position": r["position"],
                "price": round(float(r["price"]), 1)}

    moves = []
    for out_id, in_id in zip(sorted(best["transfers_out"]), sorted(best["transfers_in"])):
        o, n = _row(out_id), _row(in_id)
        o["sell_price"] = round(float(sell_prices.get(out_id, o["price"])), 1)
        moves.append({"out": o, "in": n})

    weighted_gain = round(best["weighted_objective"] - baseline["weighted_objective"], 2)
    next_gw_gain = round(best["next_gw_value"] - baseline["next_gw_value"], 2)

    # HONEST LIMITATION, surfaced in the artefact rather than buried in a
    # doc. This is a single-deadline solve: an UNUSED free transfer is
    # worth exactly zero to the objective, so the model will spend one for
    # any positive gain, however marginal — where a real manager would
    # often roll it. Pricing an unspent FT (the reference solver's
    # `ft_value`) is precisely what H3c's terminal-state valuation adds.
    # Until then, treat a low-gain "transfer" verdict as "this is roughly
    # neutral", not as a recommendation to act.
    caveats = []
    if best["n_transfers"] > 0 and weighted_gain < 1.0:
        caveats.append(
            f"Gain is only {weighted_gain:+.2f} weighted pts. This solve does not "
            f"value an unused free transfer (H3c adds that), so it spends one for "
            f"any positive gain. Rolling is likely at least as good here."
        )

    return {
        "recommendation": "transfer" if best["n_transfers"] > 0 else "roll",
        "caveats": caveats,
        "free_transfers": free_transfers,
        "bank": round(float(bank), 1),
        "n_transfers": best["n_transfers"],
        "hits": best["hits"],
        "hit_points": best["hit_points"],
        "transfers": moves,
        "weighted_gain": weighted_gain,
        "next_gw_gain": next_gw_gain,
        "baseline": {"weighted": round(baseline["weighted_objective"], 2),
                     "next_gw": round(baseline["next_gw_value"], 2)},
        "after": {"weighted": round(best["weighted_objective"], 2),
                  "next_gw": round(best["next_gw_value"], 2)},
        "squad": best["squad"],
        "starting_xi": best["starting_xi"],
        "captain": best["captain"],
        "vice_captain": best["vice_captain"],
    }


# ---------------------------------------------------------------------------
# Output artefact + CLI entry point (spec §6/§7)
# ---------------------------------------------------------------------------
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def write_recommendation(rec: dict, gw: int, entry_id: int, extra: dict) -> Path:
    """
    Writes data/output/gw{n}_transfers.json.

    next_gw_gain is included even when negative: a correct long-game
    transfer that costs points this week is exactly the case a human
    needs to see, not have hidden.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"gameweek": int(gw), "entry_id": int(entry_id),
               "generated_utc": datetime.now(timezone.utc).isoformat()}
    payload.update(rec)
    payload.update(extra)
    path = OUTPUT_DIR / f"gw{gw}_transfers.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _latest_started_gameweek() -> int:
    """The most recent gameweek whose picks are available (deadline passed)."""
    events = json.loads((RAW_DIR / "bootstrap_static.json").read_text(encoding="utf-8"))["events"]
    started = [e["id"] for e in events if e.get("is_current") or e.get("finished")]
    return max(started) if started else 1


def main() -> int:
    """
    Wire ingestion -> solve -> artefact.

    Deliberately NOT in weekly.yml yet (spec §7.3): the recommendation
    must be hand-verified as executable in the real game for two
    gameweeks before it runs unattended.
    """
    import pandas as pd

    from fpl.decide.optimiser import load_config
    from fpl.decide import squad_state
    from fpl.project import project as project_mod

    config = load_config()
    entry_id = config["fpl"]["entry_id"]

    projections = project_mod.project_gameweeks(config["horizon"]["gameweeks"], config)
    totals = project_mod.weighted_horizon_total(projections, config)
    players_raw = pd.read_parquet(
        Path(__file__).resolve().parents[2] / "data" / "processed" / "players.parquet")
    totals = totals.merge(players_raw[["id", "status"]], on="id", how="left")

    public = squad_state.parse_entry_picks(
        squad_state.fetch_entry_picks(entry_id, _latest_started_gameweek()))
    pasted, age_h = squad_state.load_my_team_file()
    state = squad_state.reconcile(public, pasted)

    if state["bank_mismatch"]:
        print(f"[transfers] NOTE: public bank {state['public_bank']} != live bank "
              f"{state['bank']} — you have already transferred this week.")

    target_gw = int(state["event"]) + 1
    rec = recommend(totals, state["squad"], state["sell_prices"],
                    state["bank"], state["free_transfers"], config)

    path = write_recommendation(rec, target_gw, entry_id, {
        "sell_price_source": "my_team_file",
        "my_team_file_age_hours": round(age_h, 2),
    })

    if rec["recommendation"] == "roll":
        print(f"[transfers] GW{target_gw}: ROLL. No transfer clears the hit cost.")
    else:
        for m in rec["transfers"]:
            print(f"[transfers] GW{target_gw}: OUT {m['out']['web_name']} "
                  f"(sell £{m['out']['sell_price']}m) -> IN {m['in']['web_name']} "
                  f"(buy £{m['in']['price']}m)")
        print(f"[transfers] hits={rec['hits']} (-{rec['hit_points']} pts) | "
              f"weighted gain {rec['weighted_gain']:+.2f} | "
              f"next-GW {rec['next_gw_gain']:+.2f}")
    for c in rec.get("caveats", []):
        print(f"[transfers] CAVEAT: {c}")
    print(f"[transfers] wrote {path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
