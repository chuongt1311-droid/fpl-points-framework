"""
Tests for fpl/decide/transfers.py — spec §3.

The subtle one is test_owned_but_filtered_player_can_still_be_sold: an
injured player you OWN is dropped from the candidate pool, but you can
still sell him. If he is absent from the model's id space the linking
constraint squad[p] = current[p] - out[p] + in[p] is unsatisfiable for
him and the solve fails or misprices the squad (spec §3.3).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.decide import transfers

CONFIG = {
    "squad_rules": {
        "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3,
        "max_per_club": 3,
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1},
    },
    "optimiser": {"allow_low_confidence": False, "bench_weight_epsilon": 0.02},
    "transfers": {"hit_cost": 4},
}


def _pool(n_extra=6, upgrade_xpts=30.0):
    """15 owned players + spare candidates, all £5.0m, 5 clubs x plenty."""
    rows = []
    plan = ["GK"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    for i, pos in enumerate(plan):
        rows.append({"id": 100 + i, "web_name": f"own{i}", "position": pos,
                     "price": 5.0, "team": i % 5, "confidence": "high", "status": "a",
                     "weighted_xpts": 10.0, "next_gw_xpts": 2.0})
    spare_plan = ["GK", "DEF", "MID", "FWD", "MID", "DEF"][:n_extra]
    for j, pos in enumerate(spare_plan):
        rows.append({"id": 200 + j, "web_name": f"cand{j}", "position": pos,
                     "price": 5.0, "team": 5 + (j % 3), "confidence": "high", "status": "a",
                     "weighted_xpts": upgrade_xpts if j == 2 else 9.0,
                     "next_gw_xpts": 3.0 if j == 2 else 1.0})
    return pd.DataFrame(rows)


OWNED = [100 + i for i in range(15)]
SELL = {i: 5.0 for i in OWNED}


def test_zero_transfers_is_feasible_and_is_the_roll_baseline():
    players = _pool(upgrade_xpts=9.0)
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert out["n_transfers"] == 0
    assert sorted(out["squad"]) == sorted(OWNED)


def test_a_clear_upgrade_is_taken_with_a_free_transfer():
    players = _pool(upgrade_xpts=30.0)
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert out["n_transfers"] == 1
    assert out["hits"] == 0
    assert 202 in out["squad"]


def test_cannot_buy_a_player_already_owned():
    players = _pool()
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert set(out["transfers_in"]).isdisjoint(set(OWNED))


def test_budget_blocks_an_unaffordable_transfer():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 202, "price"] = 20.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert 202 not in out["squad"]


def test_second_transfer_costs_exactly_one_hit_with_one_free():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 204, "weighted_xpts"] = 30.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG,
                                    force_n_transfers=2)
    assert out["n_transfers"] == 2
    assert out["hits"] == 1
    assert out["hit_points"] == 4


def test_two_free_transfers_means_no_hit():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 204, "weighted_xpts"] = 30.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=2, config=CONFIG,
                                    force_n_transfers=2)
    assert out["hits"] == 0
    assert out["hit_points"] == 0


def test_owned_but_filtered_player_can_still_be_sold():
    """THE trap (spec §3.3). An injured owned player is dropped from the
    pool by availability filters, but must remain sellable — otherwise
    the linking constraint is unsatisfiable for him."""
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 110, "status"] = "i"
    players.loc[players["id"] == 110, "weighted_xpts"] = 0.0
    players.loc[players["id"] == 110, "next_gw_xpts"] = 0.0

    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG,
                                    apply_availability_filters=True)
    assert len(out["squad"]) == 15
    assert 110 in out["transfers_out"]


def test_owned_but_filtered_player_can_be_kept_when_no_upgrade_exists():
    players = _pool(upgrade_xpts=9.0)
    players.loc[players["id"] == 110, "status"] = "i"
    players.loc[players["id"] == 110, "weighted_xpts"] = 0.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=0, config=CONFIG)
    assert 110 in out["squad"]


def test_post_solve_assertion_rejects_an_unaffordable_result():
    """Belt and braces on top of the budget constraint (spec §5.4) —
    the constraint being right in theory is not the same as the input
    numbers being right."""
    bad_sell = {i: 0.0 for i in OWNED}
    with pytest.raises(transfers.InfeasibleBudgetError):
        transfers._assert_affordable(
            transfers_in=[202], transfers_out=[110],
            price={202: 9.0}, sell_prices=bad_sell, bank=0.0,
        )


def test_recommend_reports_both_horizons_and_names_the_move():
    players = _pool(upgrade_xpts=30.0)
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["recommendation"] == "transfer"
    assert rec["n_transfers"] == 1
    assert rec["transfers"][0]["in"]["id"] == 202
    assert rec["transfers"][0]["out"]["id"] in OWNED
    assert rec["weighted_gain"] > 0
    assert "next_gw_gain" in rec
    assert rec["baseline"]["weighted"] < rec["after"]["weighted"]


def test_marginal_gain_is_flagged_as_a_caveat_not_sold_as_a_win():
    """A single-deadline solve values an unused free transfer at zero, so
    it will spend one for any positive gain. That limitation is surfaced
    in the payload rather than left for the reader to discover."""
    players = _pool(upgrade_xpts=10.05)   # barely better than the owned 10.0
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["recommendation"] == "transfer"
    assert 0 < rec["weighted_gain"] < 1.0
    assert rec["caveats"], "a marginal gain must carry an explicit caveat"
    assert "free transfer" in rec["caveats"][0]


def test_a_big_gain_carries_no_caveat():
    players = _pool(upgrade_xpts=30.0)
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["weighted_gain"] > 1.0
    assert rec["caveats"] == []


def test_recommend_says_roll_when_nothing_is_worth_it():
    players = _pool(upgrade_xpts=9.0)
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["recommendation"] == "roll"
    assert rec["n_transfers"] == 0
    assert rec["transfers"] == []
    assert rec["weighted_gain"] == 0.0
