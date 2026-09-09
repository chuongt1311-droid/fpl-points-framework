"""
Tests for fpl/evaluate/news_scorecard.py — relative Brier score of M6 vs M0
minutes_factor predictions against the actual `starts` flag (C2 / §22).
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.evaluate import news_scorecard as sc


def test_brier_score_is_mean_squared_error_vs_actual_start():
    pred = pd.Series([0.9, 0.1, 0.5])
    actual = pd.Series([1, 0, 1])
    # (0.1^2 + 0.1^2 + 0.5^2) / 3
    assert sc.brier(pred, actual) == pytest.approx((0.01 + 0.01 + 0.25) / 3)


def test_compute_news_scorecard_skips_a_gw_without_actuals(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "ACTUALS_DIR", tmp_path)
    monkeypatch.setattr(sc, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(sc, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sc, "load_config", lambda: {"season": "test"})
    bootstrap = {"events": [{"id": 3, "finished": True, "data_checked": True}]}
    out = sc.compute_news_scorecard(bootstrap)
    assert out["per_gw"] == []
