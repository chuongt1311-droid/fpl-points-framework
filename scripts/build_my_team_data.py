"""
build_my_team_data.py — the dashboard's "My Team" view (PROJECT_LOG §19).

The static dashboard's pitch shows the model's OPTIMAL squad. This adds the
one thing it never had: YOUR actual 15, their projections, and a
roll / 1-transfer / 2-transfer / wildcard comparison so the "should I
wildcard" question can be answered with numbers.

FROZEN/LIVE split, same as build_dashboard_data.py:
  - projections, statuses, prices: committed artefacts only, never recomputed
  - your squad composition: one cheap public entry-picks pull (no auth)
  - sell prices / free transfers / bank: data/private/my_team.json IF it is
    present and < 24h old; otherwise market price is used as a proxy and the
    payload says so.

Writes dashboard/my_team.json (gitignored — carries sell prices).
build_dashboard_data.py inlines it into index.html if present, else null.

Run: .venv\\Scripts\\python.exe scripts/build_my_team_data.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from fpl.decide import squad_state, transfers
from fpl.project import project as project_mod

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "output"
PROCESSED_DIR = ROOT / "data" / "processed"
PROJECTIONS_DIR = ROOT / "data" / "projections"
RAW_DIR = ROOT / "data" / "raw"
DASHBOARD_DIR = ROOT / "dashboard"

# import the GW-selection helper rather than duplicating it
from scripts.build_dashboard_data import select_target_gameweek


def r2(x):
    return round(float(x), 2) if pd.notna(x) else None


def resolve_sell_prices(
    squad_ids: list[int], players_raw: pd.DataFrame, my_team: Optional[dict]
) -> tuple[dict[int, float], str]:
    """Per-player sell price + where it came from. The pasted my_team.json's
    `sell_prices` when we have a fresh file; otherwise current market price
    (`now_cost`) as a proxy — an over-estimate for risers, exact otherwise."""
    if my_team and my_team.get("sell_prices"):
        sp = my_team["sell_prices"]
        return {int(i): float(sp[i]) for i in sp}, "my_team_file"
    market = dict(zip(players_raw["id"], players_raw["now_cost"] / 10.0))
    return {int(i): round(float(market.get(i, 0.0)), 1) for i in squad_ids}, "market"


def _project_players(target_gw: int, config: dict) -> pd.DataFrame:
    """One row per player with weighted_xpts / next_gw_xpts / status, all
    from committed artefacts — the same shape transfers.recommend() wants."""
    proj_path = PROJECTIONS_DIR / f"gw{target_gw}.parquet"
    if not proj_path.exists():
        proj_path = sorted(PROJECTIONS_DIR.glob("gw*.parquet"))[-1]
    proj = pd.read_parquet(proj_path)
    weighted = project_mod.weighted_horizon_total(proj, config)
    players_raw = pd.read_parquet(PROCESSED_DIR / "players.parquet")
    return weighted.merge(players_raw[["id", "status"]], on="id", how="left")


def _pair_moves(out_ids: list, in_ids: list, players_df: pd.DataFrame) -> list[dict]:
    """Pair transfers out<->in WITHIN position. The MILP returns two sets,
    not ordered pairs; a legal transfer set preserves squad composition so
    the positions match as a multiset, and a same-position pairing is the
    only meaningful one (naive index-zip pairs a sold FWD with a bought GK
    on a 6-change wildcard)."""
    meta = players_df.set_index("id")[["web_name", "position"]]
    def by_pos(ids):
        buckets: dict[str, list[str]] = {}
        for i in ids:
            if i in meta.index:
                buckets.setdefault(meta.loc[i, "position"], []).append(meta.loc[i, "web_name"])
        return buckets
    outs, ins = by_pos(out_ids), by_pos(in_ids)
    moves = []
    for pos in ["GK", "DEF", "MID", "FWD"]:
        for o, n in zip(sorted(outs.get(pos, [])), sorted(ins.get(pos, []))):
            moves.append({"out": o, "in": n, "position": pos})
    return moves


def _summarise(result: dict, baseline: dict, players_df: pd.DataFrame) -> dict:
    moves = _pair_moves(result["transfers_out"], result["transfers_in"], players_df)
    return {
        "n_transfers": result["n_transfers"],
        "hits": result["hits"],
        "hit_points": result["hit_points"],
        "weighted_value": r2(result["weighted_value"]),
        "next_gw_value": r2(result["next_gw_value"]),
        "weighted_gain": r2(result["weighted_value"] - baseline["weighted_value"]),
        "next_gw_gain": r2(result["next_gw_value"] - baseline["next_gw_value"]),
        "moves": moves,
    }


def build_analysis(
    players_df: pd.DataFrame, squad_ids: list[int], sell_prices: dict,
    bank: float, free_transfers: int, config: dict,
) -> dict:
    """roll / best-1 / best-2 / wildcard, each vs the roll baseline."""
    def solve(**kw):
        return transfers.solve_transfers(players_df, squad_ids, sell_prices, bank,
                                         config=config, **kw)

    roll = solve(free_transfers=free_transfers, force_n_transfers=0)
    one = solve(free_transfers=free_transfers, force_n_transfers=1)
    two = solve(free_transfers=free_transfers, force_n_transfers=2)
    wildcard = solve(free_transfers=15)  # no hits, full re-optimise within budget

    return {
        "roll": _summarise(roll, roll, players_df),
        "one": _summarise(one, roll, players_df),
        "two": _summarise(two, roll, players_df),
        "wildcard": _summarise(wildcard, roll, players_df),
    }


def squad_table(squad_ids: list[int], players_df: pd.DataFrame, sell_prices: dict) -> list[dict]:
    order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    rows = []
    by_id = players_df.set_index("id")
    for pid in squad_ids:
        if pid not in by_id.index:
            continue
        p = by_id.loc[pid]
        rows.append({
            "id": int(pid),
            "web_name": p["web_name"],
            "position": p["position"],
            "team": int(p["team"]),
            "price": r2(p["price"]),
            "sell_price": r2(sell_prices.get(pid, p["price"])),
            "status": p.get("status", "a"),
            "next_gw_xpts": r2(p["next_gw_xpts"]),
            "weighted_xpts": r2(p["weighted_xpts"]),
        })
    rows.sort(key=lambda r: (order.get(r["position"], 9), -(r["weighted_xpts"] or 0)))
    return rows


def main() -> None:
    config = project_mod.load_config()
    entry_id = config["fpl"]["entry_id"]

    gameweeks = project_mod.next_n_gameweeks(8)
    target_gw = select_target_gameweek(OUTPUT_DIR, gameweeks)

    started = transfers._latest_started_gameweek()
    public = squad_state.parse_entry_picks(squad_state.fetch_entry_picks(entry_id, started))
    squad_ids = [int(i) for i in public["squad"]]

    my_team = None
    my_team_note = None
    try:
        my_team, age_h = squad_state.load_my_team_file()
        my_team_note = f"sell prices from your pasted my_team.json ({age_h:.0f}h old)"
    except squad_state.StaleMyTeamError as e:
        my_team_note = f"my_team.json is stale ({e}); showing market prices — re-save it for exact figures"
    except FileNotFoundError:
        my_team_note = "no my_team.json — showing market prices as a sell-price proxy (paste one and re-run for exact figures)"

    players_raw = pd.read_parquet(PROCESSED_DIR / "players.parquet")
    sell_prices, price_source = resolve_sell_prices(squad_ids, players_raw, my_team)
    bank = my_team["bank"] if my_team else public.get("bank", 0.0)
    free_transfers = my_team["free_transfers"] if my_team else 1

    players_df = _project_players(target_gw, config)
    analysis = build_analysis(players_df, squad_ids, sell_prices, bank, free_transfers, config)

    teams_df = players_raw[["team", "team_short_name"]].drop_duplicates()
    team_short = dict(zip(teams_df["team"], teams_df["team_short_name"]))
    rows = squad_table(squad_ids, players_df, sell_prices)
    for row in rows:
        row["team_short"] = team_short.get(row["team"], "?")

    payload = {
        "meta": {
            "gameweek": target_gw,
            "entry_id": entry_id,
            "entry_name": public.get("entry_name"),
            "price_source": price_source,
            "note": my_team_note,
            "bank": r2(bank),
            "free_transfers": free_transfers,
            "squad_sell_value": r2(sum(sell_prices.get(i, 0.0) for i in squad_ids)),
        },
        "squad": rows,
        "analysis": analysis,
    }

    out_path = DASHBOARD_DIR / "my_team.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    a = analysis
    print(f"Wrote {out_path} — GW{target_gw}, {price_source} prices, "
          f"roll {a['roll']['weighted_value']} | 1tf {a['one']['weighted_gain']:+} | "
          f"2tf {a['two']['weighted_gain']:+} | WC {a['wildcard']['weighted_gain']:+}")


if __name__ == "__main__":
    main()
