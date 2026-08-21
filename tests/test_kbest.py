"""
Tests for fpl/decide/kbest.py — v3 plan §D (K-best with diversity).

Pure unit tests: synthetic pools, no file I/O, no network — same bar as
tests/test_bench_weight.py, which this borrows its pool-construction style
from.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.decide import kbest

CONFIG = {
    "squad_rules": {
        "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3,
        "max_per_club": 3,
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1},
    },
    "optimiser": {"allow_low_confidence": False, "bench_weight_epsilon": 0.02},
}


def _diverse_pool() -> pd.DataFrame:
    """
    A pool with real slack for diversity: 2 GK, 8 DEF, 8 MID, 5 FWD, cheap
    enough and varied enough in xpts that several distinct legal 15-man
    squads exist within budget — unlike test_bench_weight's minimal pool
    (built only to test tie-breaking, not diversity).
    """
    rows = []
    pid = 1
    counts = {"GK": 2, "DEF": 8, "MID": 8, "FWD": 5}
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


def test_find_k_best_squads_frontier_is_monotonically_non_increasing():
    pool = _diverse_pool()
    results = kbest.find_k_best_squads(pool, CONFIG, k=5, diversity_d=3)
    assert len(results) >= 2  # the pool must actually have room for alternatives
    objectives = [r["stage1_objective"] for r in results]
    assert objectives == sorted(objectives, reverse=True)
    assert results[0]["frontier_spread"] == 0.0
    for r in results[1:]:
        assert r["frontier_spread"] <= 0.0


def test_find_k_best_squads_each_pair_differs_by_at_least_diversity_d():
    pool = _diverse_pool()
    results = kbest.find_k_best_squads(pool, CONFIG, k=4, diversity_d=3)
    squad_sets = [set(r["squad"]) for r in results]
    for a in range(len(squad_sets)):
        for b in range(a + 1, len(squad_sets)):
            overlap = squad_sets[a] & squad_sets[b]
            differ_by = 15 - len(overlap)
            assert differ_by >= 3


def _exact_fit_pool() -> pd.DataFrame:
    """Exactly 2 GK/5 DEF/5 MID/3 FWD — zero slack, so only ONE legal squad
    exists at all (every player must be selected)."""
    rows = []
    pid = 1
    team = 1
    for pos, n in [("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        for _ in range(n):
            rows.append({"id": pid, "position": pos, "price": 4.0, "team": team, "xpts": 5.0})
            pid += 1
            team = team % 20 + 1
    df = pd.DataFrame(rows)
    df["web_name"] = df["id"].astype(str)
    df["confidence"] = "high"
    df["status"] = "a"
    df["weighted_xpts"] = df["xpts"]
    df["next_gw_xpts"] = df["xpts"]
    return df


def test_find_k_best_squads_stops_early_rather_than_fabricating_results():
    """A zero-slack pool has exactly one legal squad — asking for more
    diverse alternatives must return just that one, not raise or invent a
    second squad that doesn't actually satisfy the diversity cut."""
    pool = _exact_fit_pool()
    results = kbest.find_k_best_squads(pool, CONFIG, k=5, diversity_d=3)
    assert len(results) == 1


def test_rank_1_squad_matches_the_unconstrained_optimum():
    from fpl.decide import optimiser as opt_mod
    pool = _diverse_pool()
    single_best = opt_mod.optimise_squad(pool, CONFIG)
    k_best = kbest.find_k_best_squads(pool, CONFIG, k=1, diversity_d=3)
    assert set(k_best[0]["squad"]) == set(single_best["squad"])


def test_find_k_best_xis_frontier_is_monotonically_non_increasing():
    pool = _diverse_pool()
    from fpl.decide import optimiser as opt_mod
    squad_result = opt_mod.optimise_squad(pool, CONFIG)
    squad_ids = squad_result["squad"]
    next_gw = dict(zip(pool["id"], pool["next_gw_xpts"]))
    position = dict(zip(pool["id"], pool["position"]))

    results = kbest.find_k_best_xis(squad_ids, next_gw, position, CONFIG["squad_rules"], k=5)
    assert len(results) >= 2
    pts = [r["next_gw_expected_points"] for r in results]
    assert pts == sorted(pts, reverse=True)
    assert results[0]["frontier_spread"] == 0.0
    for r in results[1:]:
        assert r["frontier_spread"] <= 0.0


def test_find_k_best_xis_are_pairwise_distinct():
    pool = _diverse_pool()
    from fpl.decide import optimiser as opt_mod
    squad_result = opt_mod.optimise_squad(pool, CONFIG)
    squad_ids = squad_result["squad"]
    next_gw = dict(zip(pool["id"], pool["next_gw_xpts"]))
    position = dict(zip(pool["id"], pool["position"]))

    results = kbest.find_k_best_xis(squad_ids, next_gw, position, CONFIG["squad_rules"], k=4)
    xi_sets = [frozenset(r["starting_xi"]) for r in results]
    assert len(xi_sets) == len(set(xi_sets))  # every XI is a distinct set


def test_compute_cross_model_agreement_finds_a_squad_reproduced_across_models():
    squads_by_model = {
        "m0": [
            {"squad": [1, 2, 3], "rank": 1},
            {"squad": [1, 2, 4], "rank": 2},
        ],
        "m2": [
            {"squad": [1, 2, 4], "rank": 1},   # this IS m0's rank-2 squad
            {"squad": [1, 2, 3], "rank": 2},   # this IS m0's rank-1 squad
        ],
    }
    agreement = kbest.compute_cross_model_agreement(squads_by_model)
    # m0's #1 squad ([1,2,3]) is m2's #2
    assert agreement["m0"]["m2"] == 2
    # m2's #1 squad ([1,2,4]) is m0's #2
    assert agreement["m2"]["m0"] == 2


def test_compute_cross_model_agreement_reports_none_when_not_found():
    squads_by_model = {
        "m0": [{"squad": [1, 2, 3], "rank": 1}],
        "m2": [{"squad": [9, 9, 9], "rank": 1}],
    }
    agreement = kbest.compute_cross_model_agreement(squads_by_model)
    assert agreement["m0"]["m2"] is None
