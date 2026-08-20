"""
Regression tests for fpl/evaluate/backtest.py's compute_channel_calibration —
guards a real bug caught while building it: the "insufficient signal" guard
compared actual_sum/predicted_sum directly against 1e-6, which is always
true for a NEGATIVE channel (conceded_pts) regardless of how much real
signal it has, silently forcing that channel's calibration to a no-op 1.0.

Pure unit test: synthetic predictions DataFrame, no file I/O, no network —
same bar as tests/test_hotfix_regressions.py and tests/test_snapshot.py.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.evaluate import backtest


def _predictions_row(position: str, **overrides) -> dict:
    row = {"position": position}
    for channel in backtest.CALIBRATION_CHANNELS:
        row[f"actual_{channel}_pts"] = 0.0
        row[f"predicted_{channel}_pts"] = 0.0
    row.update(overrides)
    return row


def test_calibration_ratio_is_correctly_signed_for_a_negative_channel():
    """THE BUG: conceded_pts sums are negative penalties. A raw
    `actual_sum <= 1e-6` check always trips for them, so before the fix
    every DEF/GK's conceded calibration silently defaulted to 1.0 no matter
    how far off the real penalty was."""
    predictions = pd.DataFrame([
        _predictions_row("DEF", actual_conceded_pts=-20.0, predicted_conceded_pts=-16.0),
        _predictions_row("DEF", actual_conceded_pts=-10.0, predicted_conceded_pts=-8.0),
    ])
    factors = backtest.compute_channel_calibration(predictions)

    # actual/predicted = -30/-24 = 1.25 — model under-penalises, needs a
    # BIGGER (more negative-scaling) correction, not a no-op.
    assert factors["DEF"]["conceded"] == 1.25


def test_calibration_defaults_to_noop_only_when_signal_is_genuinely_near_zero():
    predictions = pd.DataFrame([
        _predictions_row("FWD", actual_conceded_pts=0.0, predicted_conceded_pts=0.0),
    ])
    factors = backtest.compute_channel_calibration(predictions)
    assert factors["FWD"]["conceded"] == 1.0


def test_calibration_ratio_clips_to_the_configured_range():
    predictions = pd.DataFrame([
        _predictions_row("MID", actual_goal_pts=100.0, predicted_goal_pts=1.0),  # ratio 100x, must clip
    ])
    factors = backtest.compute_channel_calibration(predictions)
    assert factors["MID"]["goal"] == backtest.CALIBRATION_CLIP[1]


def test_calibration_ratio_for_a_positive_channel_is_unaffected():
    predictions = pd.DataFrame([
        _predictions_row("GK", actual_save_pts=10.0, predicted_save_pts=8.0),
    ])
    factors = backtest.compute_channel_calibration(predictions)
    assert factors["GK"]["save"] == 1.25
