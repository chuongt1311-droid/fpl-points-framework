"""
Tests for fpl/evaluate/ensemble.py (v3 plan §B5, model M5).

Pure unit tests for the weighting/split mechanics (no network, no real
backtest run) — the real multi-model backtest exercise (M0/M2/M3, real
2025-26 data) is documented in docs/PROJECT_LOG.md, not re-run here.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.evaluate import ensemble as ens


def test_fit_eval_split_is_disjoint_and_covers_every_id():
    ids = pd.Series(range(100))
    fit_ids, eval_ids = ens._fit_eval_split(ids, seed=42)
    assert fit_ids.isdisjoint(eval_ids)
    assert fit_ids | eval_ids == set(range(100))


def test_fit_eval_split_is_reproducible_with_the_same_seed():
    ids = pd.Series(range(50))
    a = ens._fit_eval_split(ids, seed=7)
    b = ens._fit_eval_split(ids, seed=7)
    assert a == b


def test_fit_eval_split_differs_across_seeds():
    ids = pd.Series(range(50))
    a = ens._fit_eval_split(ids, seed=1)
    b = ens._fit_eval_split(ids, seed=2)
    assert a != b


def test_inverse_rmse_squared_weights_favours_the_lower_rmse_model():
    weights = ens._inverse_rmse_squared_weights({"a": 10.0, "b": 20.0})
    assert weights["a"] > weights["b"]
    # 1/10^2=0.01, 1/20^2=0.0025 -> ratio 4:1, not 2:1 (squared, not linear)
    assert abs(weights["a"] / weights["b"] - 4.0) < 1e-9


def test_inverse_rmse_squared_weights_sum_to_one():
    weights = ens._inverse_rmse_squared_weights({"a": 15.0, "b": 22.0, "c": 18.0})
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_equal_rmse_models_get_equal_weight():
    weights = ens._inverse_rmse_squared_weights({"a": 20.0, "b": 20.0})
    assert abs(weights["a"] - weights["b"]) < 1e-9
