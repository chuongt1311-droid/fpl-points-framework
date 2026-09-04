"""
Tests for scripts/build_my_team_data.py — the dashboard's "My Team" view
(PROJECT_LOG §19). Assembles the user's ACTUAL squad + a roll / 1-transfer
/ 2-transfer / wildcard comparison from committed artefacts, and degrades
to market prices when data/private/my_team.json is absent or stale.

Solver correctness itself is covered by tests/test_transfers.py; these
tests cover the new assembly and the sell-price fallback.
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts import build_my_team_data as bmt

CONFIG = {
    "squad_rules": {
        "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3,
        "max_per_club": 3,
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1},
    },
    "optimiser": {"allow_low_confidence": False, "bench_weight_epsilon": 0.02},
    "transfers": {"hit_cost": 4},
}


def _players(upgrade_xpts=40.0):
    rows = []
    plan = ["GK"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    for i, pos in enumerate(plan):
        rows.append({"id": 100 + i, "web_name": f"own{i}", "position": pos,
                     "price": 5.0, "team": i % 5, "confidence": "high", "status": "a",
                     "weighted_xpts": 10.0, "next_gw_xpts": 2.0})
    for j, pos in enumerate(["GK", "DEF", "MID", "FWD", "MID", "DEF"]):
        rows.append({"id": 200 + j, "web_name": f"cand{j}", "position": pos,
                     "price": 5.0, "team": 6 + (j % 2), "confidence": "high", "status": "a",
                     "weighted_xpts": upgrade_xpts if j in (2, 4) else 9.0,
                     "next_gw_xpts": 4.0 if j in (2, 4) else 1.0})
    return pd.DataFrame(rows)


OWNED = [100 + i for i in range(15)]
SELL = {i: 5.0 for i in OWNED}


# ---- sell-price resolution -------------------------------------------------

def test_resolve_sell_prices_uses_pasted_values_when_present():
    players_raw = pd.DataFrame({"id": [1, 2], "now_cost": [55, 60]})
    my_team = {"sell_prices": {1: 5.0, 2: 6.2}}
    prices, source = bmt.resolve_sell_prices([1, 2], players_raw, my_team)
    assert prices == {1: 5.0, 2: 6.2}
    assert source == "my_team_file"


def test_resolve_sell_prices_falls_back_to_market_when_no_file():
    players_raw = pd.DataFrame({"id": [1, 2], "now_cost": [55, 60]})
    prices, source = bmt.resolve_sell_prices([1, 2], players_raw, None)
    assert prices == {1: 5.5, 2: 6.0}
    assert source == "market"


# ---- the roll / 1 / 2 / wildcard comparison -------------------------------

def test_build_analysis_roll_is_the_zero_gain_baseline():
    a = bmt.build_analysis(_players(upgrade_xpts=9.0), OWNED, SELL,
                           bank=0.0, free_transfers=1, config=CONFIG)
    assert a["roll"]["weighted_gain"] == 0.0
    assert a["roll"]["moves"] == []


def test_build_analysis_wildcard_gain_dominates_single_transfer():
    a = bmt.build_analysis(_players(upgrade_xpts=40.0), OWNED, SELL,
                           bank=0.0, free_transfers=1, config=CONFIG)
    assert a["one"]["weighted_gain"] > 0
    assert len(a["one"]["moves"]) == 1
    assert a["wildcard"]["weighted_gain"] >= a["two"]["weighted_gain"] >= a["one"]["weighted_gain"]
    assert a["wildcard"]["hits"] == 0


def test_build_analysis_two_transfer_option_charges_one_hit_with_one_free():
    a = bmt.build_analysis(_players(upgrade_xpts=40.0), OWNED, SELL,
                           bank=0.0, free_transfers=1, config=CONFIG)
    assert a["two"]["n_transfers"] == 2
    assert a["two"]["hits"] == 1


# ---- squad table ---------------------------------------------------------

def test_pair_moves_matches_transfers_within_position():
    """A wildcard returns two sets, not ordered pairs — pairing must be
    position-aware so a sold DEF isn't shown 'becoming' a bought MID."""
    players = _players()
    moves = bmt._pair_moves(
        out_ids=[102, 107],   # own2 = DEF, own7 = MID  (see _players plan)
        in_ids=[201, 202],    # cand1 = DEF, cand2 = MID
        players_df=players,
    )
    pairs = {(m["out"], m["in"]) for m in moves}
    assert pairs == {("own2", "cand1"), ("own7", "cand2")}


def test_squad_table_is_the_owned_fifteen_with_sell_prices():
    rows = bmt.squad_table(OWNED, _players(), SELL)
    assert len(rows) == 15
    assert {r["id"] for r in rows} == set(OWNED)
    assert all(r["sell_price"] == 5.0 for r in rows)
