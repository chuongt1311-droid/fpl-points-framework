"""
Tests for fpl/decide/optimiser.py's bench-weight epsilon (spec §4.4) and
apply_availability_filters (needed by fpl.evaluate.hindsight's global-XI
benchmark, spec §3.3 decision D7).

Pure unit tests: synthetic squad pool, no file I/O, no network — same bar
as tests/test_hotfix_regressions.py.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

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


def _pool_with_bench_tie(bench_epsilon: float) -> pd.DataFrame:
    """
    A minimal-but-legal pool: 11 clearly-best starters (high xpts, forcing
    them into the XI regardless of epsilon) plus exactly 4 bench-eligible
    candidates at the SAME price, two of which are legal alternates for the
    same slot with different xpts. Without bench weighting, the solver is
    indifferent between them (spec §4.4's stated problem); with it, the
    higher-xpts one must win.
    """
    rows = []
    pid = 1
    # 11 strong starters: 1 GK, 4 DEF, 4 MID, 2 FWD — cheap enough to leave
    # budget for the bench, xpts high enough to always start.
    starters = (
        [("GK", 1)] + [("DEF", 4)] + [("MID", 4)] + [("FWD", 2)]
    )
    for pos, n in starters:
        for _ in range(n):
            rows.append({"id": pid, "position": pos, "price": 5.0, "team": pid, "xpts": 20.0})
            pid += 1

    # Bench candidates: 1 more GK, 1 more DEF, 1 more FWD (squad composition
    # needs gk=2/def=5/mid=5/fwd=3 total — 11 starters above are gk1/def4/
    # mid4/fwd2, so exactly 1 bench slot remains per position except MID,
    # which gets TWO interchangeable candidates at an identical price — the
    # tie bench weighting must break.
    rows.append({"id": pid, "position": "GK", "price": 4.0, "team": 90, "xpts": 2.0}); pid += 1
    rows.append({"id": pid, "position": "DEF", "price": 4.0, "team": 91, "xpts": 2.0}); pid += 1
    rows.append({"id": pid, "position": "FWD", "price": 4.0, "team": 94, "xpts": 2.0}); pid += 1
    low_id, high_id = pid, pid + 1
    rows.append({"id": low_id, "position": "MID", "price": 4.0, "team": 92, "xpts": 2.0})
    rows.append({"id": high_id, "position": "MID", "price": 4.0, "team": 93, "xpts": 3.0})

    df = pd.DataFrame(rows)
    df["web_name"] = df["id"].astype(str)
    df["confidence"] = "high"
    df["status"] = "a"
    df["weighted_xpts"] = df["xpts"]
    df["next_gw_xpts"] = df["xpts"]
    return df, low_id, high_id


def test_bench_weight_breaks_a_tie_toward_the_higher_xpts_candidate():
    pool, low_id, high_id = _pool_with_bench_tie(bench_epsilon=0.02)
    result = opt.optimise_squad(pool, CONFIG)
    assert high_id in result["squad"]
    assert low_id not in result["squad"]


def test_zero_bench_weight_reproduces_the_old_indifferent_behaviour():
    """Sanity check that epsilon is actually doing the work above — with it
    zeroed, the solver has no preference (PuLP/CBC's own tie-break, not
    ours, decides which one lands in the squad; either is a legal
    solution, so this just confirms the run still succeeds)."""
    pool, low_id, high_id = _pool_with_bench_tie(bench_epsilon=0.0)
    zero_config = {**CONFIG, "optimiser": {**CONFIG["optimiser"], "bench_weight_epsilon": 0.0}}
    result = opt.optimise_squad(pool, zero_config)
    assert len(result["squad"]) == 15  # still solves; which tie-break winner isn't asserted


def test_apply_availability_filters_false_includes_low_confidence_and_unavailable_players():
    """Flag the higher-xpts MID bench candidate (not a starter — there's no
    slack to remove a starter without breaking feasibility, since squad
    composition needs an exact count per position) as unavailable and
    low-confidence. Under normal filtering it must be excluded, losing the
    tie-break to the lower-xpts candidate; without filtering, availability
    is ignored entirely and it wins the tie-break back."""
    pool, low_id, high_id = _pool_with_bench_tie(bench_epsilon=0.02)
    pool.loc[pool["id"] == high_id, "status"] = "i"
    pool.loc[pool["id"] == high_id, "confidence"] = "low"

    filtered = opt.optimise_squad(pool, CONFIG, apply_availability_filters=True)
    unfiltered = opt.optimise_squad(pool, CONFIG, apply_availability_filters=False)

    assert high_id not in filtered["squad"] and low_id in filtered["squad"]
    assert high_id in unfiltered["squad"]
