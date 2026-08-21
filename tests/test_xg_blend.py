"""
Tests for the xG blend (v3 plan §B2, model M2) — fpl/project/xg_blend.py's
blend_weight/apply_xg_blend.

Pure unit tests: synthetic pandas Series/DataFrames, no file I/O, no
network — same bar as tests/test_shrinkage.py, which this deliberately
mirrors (blend_weight is shrinkage_weight's mirror-image: same functional
form, opposite direction).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.project import xg_blend


def test_blend_weight_decreases_monotonically_with_sample_size():
    """Opposite direction to baseline.shrinkage_weight — v starts near 1
    (trust xG) at m=0 and falls as personal goal history accumulates."""
    m = pd.Series([0, 100, 1000, 10000])
    v = xg_blend.blend_weight(m, k_xg=1500)
    assert v.is_monotonic_decreasing


def test_zero_minutes_gives_v_equal_to_one():
    """m=0 -> v=1 exactly: with no personal goal history at all, the blend
    is pure xG (both G90 and xG90 would be their respective priors at this
    point anyway, but xG is still the lower-variance of the two)."""
    v = xg_blend.blend_weight(pd.Series([0.0]), k_xg=1500)
    assert v.iloc[0] == 1.0


def test_a_large_sample_player_stays_majority_personal_not_majority_xg():
    """Haaland-class: weighted_minutes far above k_xg -> v well below 0.5,
    i.e. the blended rate is dominated by his actual scoring record, not
    his shot quality — matches plan §B2's stated expectation."""
    v = xg_blend.blend_weight(pd.Series([5514.0]), k_xg=1500)
    assert v.iloc[0] < 0.3


def test_a_thin_sample_player_is_majority_xg():
    """Osula-class: weighted_minutes well below k_xg -> v above 0.5, i.e.
    the blend leans on the lower-variance shot-based proxy rather than a
    handful of realised goals."""
    v = xg_blend.blend_weight(pd.Series([1159.0]), k_xg=1500)
    assert v.iloc[0] > 0.5


def test_blend_arithmetic_replaces_goal_and_assist_columns_only():
    """The same merge+blend logic apply_xg_blend runs, exercised directly on
    a synthetic xg_rates frame (compute_shrunk_xg_rates itself needs the
    real historical pipeline — covered by the project.py integration, not a
    unit test's job) — must not disturb any other channel column, and must
    drop the intermediate expected_goals_per90/expected_assists_per90
    columns it merges in (caller only wants the standard schema back)."""
    player_inputs = pd.DataFrame([
        {"id": 1, "code": 100, "position": "FWD", "price": 10.0,
         "goals_scored_per90": 0.5, "assists_per90": 0.1,
         "clean_sheets_per90": 0.0, "weighted_minutes": 1000.0},
    ])
    out = player_inputs.copy()
    xg_rates = pd.DataFrame([
        {"id": 1, "weighted_minutes": 1000.0, "expected_goals_per90": 0.8, "expected_assists_per90": 0.05},
    ])
    merged = out.merge(xg_rates, on="id", how="left", suffixes=("", "_xg"))
    v = xg_blend.blend_weight(merged["weighted_minutes"], k_xg=1500)
    for xg_channel, goal_channel in xg_blend.XG_TO_GOAL_CHANNEL.items():
        xg_col = f"{xg_channel}_per90"
        g_col = f"{goal_channel}_per90"
        merged[g_col] = v * merged[xg_col].fillna(0) + (1 - v) * merged[g_col].fillna(0)
        merged = merged.drop(columns=[xg_col])

    assert "expected_goals_per90" not in merged.columns
    assert "expected_assists_per90" not in merged.columns
    assert merged["clean_sheets_per90"].iloc[0] == 0.0  # untouched
    v0 = v.iloc[0]
    expected_goal_rate = v0 * 0.8 + (1 - v0) * 0.5
    assert abs(merged["goals_scored_per90"].iloc[0] - expected_goal_rate) < 1e-9
