"""
hindsight.py — EVALUATE layer. FPL_V2_DESIGN.md spec §3.3-3.4: computes
three XIs against ACTUAL points for a finished gameweek, and decomposes the
gap between the worst and best of them into three named regret terms that
map one-to-one onto architectural layers.

    C  = chosen XI actual points, chosen captain, autosubs applied
    C' = chosen XI actual points, BEST captain from within that same XI
    Y  = best XI from your 15, best captain
    G  = best XI from the best legal £100m squad, best captain

    captaincy regret = C' - C     -> armband logic
    bench regret     = Y  - C'    -> XI selection
    squad regret     = G  - Y     -> projection model
    total regret     = G  - C     = captaincy + bench + squad   (by construction)

WHY THIS CAN'T RUN YET: needs data/actuals/actuals_{season}.csv (spec §3.1,
populated once a gameweek finishes AND data_checked flips), which doesn't
exist before GW1 has even been played. Built and unit-tested against
synthetic data now (spec §7.3's own "no committed artefacts" test bar
makes this possible) so it's ready the moment fpl.collect.actuals has
something to read.

AUTOSUB SIMULATION (decision D8) is a documented approximation, not
hidden: FPL's exact autosub algorithm isn't published. This implements the
commonly-understood version — GK swaps if the starting GK got 0 minutes;
outfield bench players are tried in BENCH ORDER, each replacing the first
still-zero-minute starter such that the resulting XI keeps a legal
formation; a bench player who themselves got 0 minutes cannot come on.
Bench order itself isn't stored in squad_gw{n}.json's schema (spec §3.2)
either — derived here from that gameweek's own recommendations JSON
weighted_xpts, descending, the most faithful proxy available for "what a
manager would plausibly have set."
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.decide import squad_state as squad_state_mod
from fpl.decide.optimiser import optimise_squad, pick_xi_and_captain

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"
ACTUALS_DIR = Path(__file__).resolve().parents[2] / "data" / "actuals"
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshots"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _formation_ok(candidate_xi: list[int], position: dict[int, str], sx: dict) -> bool:
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for i in candidate_xi:
        counts[position[i]] += 1
    return (
        counts["GK"] == sx["gk"] and counts["DEF"] >= sx["min_def"]
        and counts["MID"] >= sx["min_mid"] and counts["FWD"] >= sx["min_fwd"]
        and len(candidate_xi) == sx["total"]
    )


def simulate_autosubs(
    starting_ids: list[int], bench_order: list[int], captain_id: int, vice_captain_id: int,
    actual_minutes: dict[int, float], position: dict[int, str], rules: dict,
) -> tuple[list[int], Optional[int]]:
    """Returns (effective_starting_xi, effective_captain_id_or_None) — see
    module docstring for the autosub approximation and the captain
    fallback rule. effective_captain is None only when BOTH the captain
    and vice-captain got 0 minutes (no armband bonus applies that week)."""
    xi = list(starting_ids)
    bench = [i for i in bench_order if i not in xi]
    sx = rules["starting_xi"]

    # GK swap — always legal (doesn't touch outfield formation counts).
    starting_gk = next((i for i in xi if position[i] == "GK"), None)
    if starting_gk is not None and actual_minutes.get(starting_gk, 0) == 0:
        bench_gk = next((i for i in bench if position[i] == "GK" and actual_minutes.get(i, 0) > 0), None)
        if bench_gk is not None:
            xi[xi.index(starting_gk)] = bench_gk
            bench.remove(bench_gk)

    # Outfield swaps, bench tried in order; each replaces the first
    # still-zero-minute starter that keeps the formation legal.
    for sub in list(bench):
        if position[sub] == "GK" or actual_minutes.get(sub, 0) == 0:
            continue
        zero_min_starters = [i for i in xi if position[i] != "GK" and actual_minutes.get(i, 0) == 0]
        for starter in zero_min_starters:
            candidate = [sub if i == starter else i for i in xi]
            if _formation_ok(candidate, position, sx):
                xi = candidate
                bench.remove(sub)
                break

    if actual_minutes.get(captain_id, 0) > 0:
        effective_captain = captain_id
    elif actual_minutes.get(vice_captain_id, 0) > 0:
        effective_captain = vice_captain_id
    else:
        effective_captain = None

    return xi, effective_captain


def _bench_order_from_recommendations(gw: int, squad_ids: list[int], starting_ids: list[int]) -> list[int]:
    """Bench order proxy — see module docstring. Falls back to squad list
    order (excluding starters) if that gameweek's recommendations JSON is
    missing (e.g. computing hindsight for `played`, which may not match
    any single recommendations file exactly)."""
    bench = [i for i in squad_ids if i not in starting_ids]
    path = OUTPUT_DIR / f"gw{gw}_recommendations.json"
    if not path.exists():
        return bench
    rec = json.loads(path.read_text(encoding="utf-8"))
    xpts_by_id = {p["id"]: p["weighted_xpts"] for p in rec["squad"]}
    return sorted(bench, key=lambda i: xpts_by_id.get(i, 0.0), reverse=True)


def _prices_as_of_gw(gw: int, config: dict) -> pd.DataFrame:
    """
    Spec §3.3: 'Prices are taken as of that gameweek, from now_cost in the
    availability snapshot.' — the latest snapshot row captured while THIS
    gameweek was still upcoming (next_event == gw), per player. Falls back
    to data/processed/players.parquet's current price if no snapshot
    covers that gameweek yet (e.g. hindsight run for a GW before automation
    had accumulated snapshot history for it).
    """
    path = SNAPSHOT_DIR / f"availability_{config['season']}.csv"
    if not path.exists():
        return None
    snap = pd.read_csv(path)
    snap = snap[snap["next_event"] == gw]
    if snap.empty:
        return None
    snap = snap.sort_values("snapshot_ts").drop_duplicates("id", keep="last")
    snap["price"] = snap["now_cost"] / 10.0
    return snap[["id", "code", "position", "team", "price"]]


def _global_xi_points(gw: int, actual_points: dict[int, int], config: dict) -> tuple[list[int], Optional[int], float]:
    """G: best legal £100m squad using ACTUALS as the objective (decision
    D7 — budget-constrained, so it's 'the best team you could actually
    have fielded,' not just the 11 highest scorers). Reuses optimise_squad
    itself (decision D7's stronger claim: the SAME optimiser proves both
    directions, so a bug in it can't hide in only one)."""
    prices = _prices_as_of_gw(gw, config)
    if prices is None:
        players = pd.read_parquet(PROCESSED_DIR / "players.parquet")
        prices = players[["id", "code", "position", "team", "price"]]

    pool = prices.copy()
    pool = pool[pool["id"].isin(actual_points.keys())]
    pool["weighted_xpts"] = pool["id"].map(actual_points).fillna(0.0)
    pool["next_gw_xpts"] = pool["weighted_xpts"]
    pool["confidence"] = "high"
    pool["status"] = "a"

    result = optimise_squad(pool, config, apply_availability_filters=False)
    g_points = (
        sum(actual_points.get(i, 0) for i in result["starting_xi"]) + actual_points.get(result["captain"], 0)
    )
    return result["starting_xi"], result["captain"], g_points


def compute_hindsight(gw: int, config: Optional[dict] = None) -> dict:
    config = config or load_config()

    state = squad_state_mod.load_squad_state(gw)
    # decision D6: grade `played` if set, fall back to `recommended`.
    chosen = state["played"] or state["recommended"]

    actuals_path = ACTUALS_DIR / f"actuals_{config['season']}.csv"
    if not actuals_path.exists():
        raise FileNotFoundError(
            f"{actuals_path} not found — run fpl.collect.actuals for GW{gw} once it's finished."
        )
    actuals = pd.read_csv(actuals_path)
    actuals_gw = actuals[actuals["event"] == gw]
    if actuals_gw.empty:
        raise ValueError(f"No actuals recorded for GW{gw} yet.")
    actual_points = dict(zip(actuals_gw["id"], actuals_gw["total_points"]))
    actual_minutes = dict(zip(actuals_gw["id"], actuals_gw["minutes"]))

    players = pd.read_parquet(PROCESSED_DIR / "players.parquet")
    position = dict(zip(players["id"], players["position"]))
    rules = config["squad_rules"]

    squad_ids = chosen["squad"]
    starting_ids = chosen["starting_xi"]
    bench_order = _bench_order_from_recommendations(gw, squad_ids, starting_ids)

    effective_xi, effective_captain = simulate_autosubs(
        starting_ids, bench_order, chosen["captain"], chosen["vice_captain"], actual_minutes, position, rules,
    )

    def _xi_points(ids: list[int]) -> float:
        return sum(actual_points.get(i, 0) for i in ids)

    C = _xi_points(effective_xi) + (actual_points.get(effective_captain, 0) if effective_captain else 0)
    # C': best captain from WITHIN that same (post-autosub) effective XI.
    best_in_xi = max(actual_points.get(i, 0) for i in effective_xi) if effective_xi else 0
    C_prime = _xi_points(effective_xi) + best_in_xi

    y_xi, y_captain, _ = pick_xi_and_captain(
        squad_ids, {i: actual_points.get(i, 0) for i in squad_ids}, position, rules
    )
    Y = _xi_points(y_xi) + actual_points.get(y_captain, 0)

    g_xi, g_captain, G = _global_xi_points(gw, actual_points, config)

    captaincy_regret = C_prime - C
    bench_regret = Y - C_prime
    squad_regret = G - Y
    total_regret = G - C

    result = {
        "gameweek": int(gw),
        "chosen": {"xi": effective_xi, "captain": effective_captain, "points": round(C, 1)},
        "best_captain_from_chosen_xi": {"xi": effective_xi, "captain": None, "points": round(C_prime, 1)},
        "best_xi_from_your_15": {"xi": y_xi, "captain": int(y_captain), "points": round(Y, 1)},
        "best_global_xi": {"xi": g_xi, "captain": int(g_captain), "points": round(G, 1)},
        "regret": {
            "captaincy": round(captaincy_regret, 1),
            "bench": round(bench_regret, 1),
            "squad": round(squad_regret, 1),
            "total": round(total_regret, 1),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"hindsight_gw{gw}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    import sys

    gw = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = compute_hindsight(gw)
    r = result["regret"]
    print(f"GW{gw} regret: captaincy={r['captaincy']:+.1f} bench={r['bench']:+.1f} "
          f"squad={r['squad']:+.1f} | total={r['total']:+.1f}")
