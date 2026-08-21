"""
Tests for fpl/evaluate/backtest.py's compute_top40_rank_correlation — the
v3 plan §C1/§C2 PRIMARY statistical metric (rank quality among the model's
own top-40-by-predicted, not the full ~600-player pool overall RMSE covers).

Pure unit tests: synthetic predictions DataFrame, no file I/O, no network —
same bar as tests/test_backtest_calibration.py.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fpl.evaluate import backtest


def test_perfect_ordering_within_top_n_gives_correlation_one():
    n = 50
    predicted = np.arange(n, 0, -1, dtype=float)  # n, n-1, ..., 1
    predictions = pd.DataFrame({"predicted_points": predicted, "actual_points": predicted})
    result = backtest.compute_top40_rank_correlation(predictions, n=40)
    assert result == 1.0


def test_only_the_top_n_by_predicted_are_considered():
    """A player predicted far outside the top-40 whose actual points would
    have been huge must NOT be pulled into the correlation — it's a
    rank-quality check of the model's OWN selected pool, not the whole set
    (top-40-by-actual would leak the answer into the test)."""
    n = 45
    predicted = list(np.arange(n, 0, -1, dtype=float))
    actual = list(np.arange(n, 0, -1, dtype=float))
    # Player #46: predicted lowest of all (would never be selected), but
    # actual is enormous — must not affect the top-40 correlation.
    predicted.append(0.0)
    actual.append(1000.0)
    predictions = pd.DataFrame({"predicted_points": predicted, "actual_points": actual})

    result_40 = backtest.compute_top40_rank_correlation(predictions, n=40)
    # top 40 of the first 45 rows only, perfectly ordered -> still 1.0
    assert result_40 == 1.0


def test_scrambled_actuals_within_top_n_reduce_correlation_below_one():
    predicted = np.arange(40, 0, -1, dtype=float)
    actual = predicted.copy()
    actual[[0, 1]] = actual[[1, 0]]  # swap top two — imperfect but still strongly correlated
    predictions = pd.DataFrame({"predicted_points": predicted, "actual_points": actual})
    result = backtest.compute_top40_rank_correlation(predictions, n=40)
    assert 0.9 < result < 1.0
