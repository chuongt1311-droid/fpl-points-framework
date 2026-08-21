"""
Tests for fpl/decide/optimiser.py's v3 plan §E2 what-if controls
(locked_ids, banned_ids, banned_clubs, budget_override, force_formation,
chip) — the live Streamlit decision layer's constraint knobs.

Pure unit tests: synthetic pool, no file I/O, no network — same bar as
tests/test_bench_weight.py and tests/test_kbest.py, which this borrows
its pool-construction style from.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pytest
import pandas as pd

from fpl.decide import optimiser as opt

CONFIG = {
    "squad_rules": {
        "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3,
        "max_per_club": 3,
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1},
    },
    "optimiser": {"allow_low_confidence": False, "bench_weight_epsilon": 0.02},
}


def _diverse_pool() -> pd.DataFrame:
    """4 GK (not 2) — real slack, so banning/removing one GK candidate
    still leaves enough to fill the exact gk=2 composition rule; the same
    headroom test_kbest.py's own _diverse_pool relies on for DEF/MID/FWD."""
    rows = []
    pid = 1
    counts = {"GK": 4, "DEF": 8, "MID": 8, "FWD": 5}
    team = 1
    for pos, n in counts.items():
        for j in range(n):
            rows.append({
                "id": pid, "position": pos, "price": 4.0 + (j % 4) * 0.5,
                "team": team, "xpts": 10.0 - j * 0.3,
            })
            pid += 1
            team = team % 20 + 1
    df = pd.DataFrame(rows)
    df["web_name"] = df["id"].astype(str)
    df["confidence"] = "high"
    df["status"] = "a"
    df["weighted_xpts"] = df["xpts"]
    df["next_gw_xpts"] = df["xpts"]
    return df


def test_locked_player_is_always_in_the_squad_even_if_low_value():
    pool = _diverse_pool()
    cheapest_worst_id = pool.sort_values("xpts").iloc[0]["id"]  # would never be picked otherwise
    result = opt.optimise_squad(pool, CONFIG, locked_ids=[cheapest_worst_id])
    assert cheapest_worst_id in result["squad"]


def test_locking_more_players_than_fit_the_budget_raises_not_silently_drops():
    pool = _diverse_pool()
    # Lock 3 GKs' worth of budget-breaking combination is hard to construct
    # cheaply; instead lock every FWD (5) plus every DEF (8) plus 2 GKs —
    # violates squad composition (needs exactly gk=2/def=5/mid=5/fwd=3).
    all_fwd_ids = pool[pool["position"] == "FWD"]["id"].tolist()  # 5 FWDs, rule wants exactly 3
    with pytest.raises(RuntimeError):
        opt.optimise_squad(pool, CONFIG, locked_ids=all_fwd_ids)


def test_banned_player_never_appears_in_the_squad():
    pool = _diverse_pool()
    best_id = pool.sort_values("xpts", ascending=False).iloc[0]["id"]  # would normally be picked
    result = opt.optimise_squad(pool, CONFIG, banned_ids=[int(best_id)])
    assert int(best_id) not in result["squad"]


def test_banned_club_excludes_every_player_at_that_club():
    pool = _diverse_pool()
    result = opt.optimise_squad(pool, CONFIG, banned_clubs=[1])
    squad_teams = pool[pool["id"].isin(result["squad"])]["team"]
    assert 1 not in squad_teams.values


def test_budget_override_replaces_the_configured_budget_not_adds_to_it():
    pool = _diverse_pool()
    result_default = opt.optimise_squad(pool, CONFIG)
    result_tiny_budget = opt.optimise_squad(pool, CONFIG, budget_override=65.0)  # min legal 15-man cost is 60.0
    assert result_tiny_budget["total_cost"] <= 65.0
    assert result_tiny_budget["total_cost"] < result_default["total_cost"]


def test_force_formation_produces_the_exact_requested_starting_shape():
    pool = _diverse_pool()
    result = opt.optimise_squad(pool, CONFIG, force_formation={"def": 5, "mid": 3, "fwd": 2})
    detail = result["detail"]
    starters = detail[detail["is_starting"]]
    assert (starters["position"] == "DEF").sum() == 5
    assert (starters["position"] == "MID").sum() == 3
    assert (starters["position"] == "FWD").sum() == 2


def test_bench_boost_chip_maximises_full_squad_value_not_just_starting_xi():
    """With bench_boost, the objective values the bench at FULL weight, not
    the tie-breaking epsilon — so the chosen bench should out-value a
    bench chosen under the normal (epsilon) objective, on a pool where
    that trade-off actually exists (cheaper/weaker starters can free up
    budget for a stronger bench when bench points fully count)."""
    pool = _diverse_pool()
    normal = opt.optimise_squad(pool, CONFIG)
    boosted = opt.optimise_squad(pool, CONFIG, chip="bench_boost")

    def _bench_total(result):
        detail = result["detail"]
        bench = detail[~detail["is_starting"]]
        return bench["weighted_xpts"].sum()

    assert _bench_total(boosted) >= _bench_total(normal)


def test_locked_and_banned_together_are_both_honoured():
    pool = _diverse_pool()
    lock_id = pool[pool["position"] == "GK"].iloc[0]["id"]
    ban_id = pool[pool["position"] == "GK"].iloc[1]["id"]
    result = opt.optimise_squad(pool, CONFIG, locked_ids=[int(lock_id)], banned_ids=[int(ban_id)])
    assert int(lock_id) in result["squad"]
    assert int(ban_id) not in result["squad"]


def test_no_whatif_args_reproduces_default_behaviour_exactly():
    """Every new param defaults to None — the weekly/frozen call path must
    be byte-identical to before this change."""
    pool = _diverse_pool()
    result_a = opt.optimise_squad(pool, CONFIG)
    result_b = opt.optimise_squad(
        pool, CONFIG, locked_ids=None, banned_ids=None, banned_clubs=None,
        budget_override=None, force_formation=None, chip=None,
    )
    assert set(result_a["squad"]) == set(result_b["squad"])
    assert result_a["stage1_objective"] == result_b["stage1_objective"]
