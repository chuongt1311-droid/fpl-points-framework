"""
Tests for calibration (FPL_V2_DESIGN.md spec §4.2) —
fpl/project/project.py's load_calibration_factors/apply_calibration.

Pure unit tests: synthetic DataFrame, no file I/O, no network — same bar
as tests/test_hotfix_regressions.py. compute_channel_pts_per_fixture is
always called with an explicit `calibration=` here rather than letting it
default to reading data/output/model_health.json, so these tests don't
depend on whatever the backtest last wrote to disk.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.project import project as proj

CONFIG = {
    "scoring_rules": {
        "appearance_60plus": 2, "appearance_1to59": 1, "yellow_card": -1,
        "red_card": -3, "saves_per_point": 3, "conceded_per_point": 2,
    },
    "position_multipliers": {
        "goals": {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4},
        "assists_flat": 3,
        "clean_sheet_value": {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
    },
    "defcon": {"points": 2},
    "minutes": {"bench_cameo_rate": 0.3},
}


def _one_defender() -> pd.DataFrame:
    return pd.DataFrame([{
        "id": 1, "web_name": "Test DEF", "position": "DEF", "price": 5.0, "team": 1,
        "confidence": "high", "status": "a", "minutes_factor": 1.0,
        "goals_scored_per90": 0.1, "assists_per90": 0.1, "clean_sheets_per90": 0.3,
        "bonus_per90": 0.2, "saves_per90": 0.0, "goals_conceded_per90": 1.0,
        "yellow_cards_per90": 0.0, "red_cards_per90": 0.0, "defcon_rate": 0.0,
        "team_cs_rate": 0.3, "team_goals_conceded_per90": 1.0,
    }])


def _fixture() -> pd.DataFrame:
    return pd.DataFrame([{
        "team": 1, "event": 1, "fixture_id": 100,
        "fixture_attack_mult": 1.0, "fixture_defence_mult": 1.0,
        "fixture_defcon_mult": 1.0, "fixture_concede_mult": 1.0,
    }])


def test_apply_calibration_scales_the_raw_channel_by_position_and_channel():
    df = pd.DataFrame([{"position": "DEF", "goal_pts": 10.0, "assist_pts": 5.0}])
    calibration = {"DEF": {"goal": 1.5}}
    out = proj.apply_calibration(df.copy(), calibration)
    assert out["goal_pts_cal"].iloc[0] == 15.0


def test_apply_calibration_defaults_to_a_noop_for_unlisted_position_or_channel():
    df = pd.DataFrame([{"position": "MID", "goal_pts": 10.0}])
    out = proj.apply_calibration(df.copy(), calibration={})
    assert out["goal_pts_cal"].iloc[0] == 10.0


def test_apply_calibration_never_mutates_the_raw_column():
    """D11: the uncorrected number must stay visible beside the corrected
    one — calibration must not overwrite goal_pts itself."""
    df = pd.DataFrame([{"position": "DEF", "goal_pts": 10.0}])
    out = proj.apply_calibration(df.copy(), calibration={"DEF": {"goal": 2.0}})
    assert out["goal_pts"].iloc[0] == 10.0
    assert out["goal_pts_cal"].iloc[0] == 20.0


def test_compute_channel_pts_per_fixture_applies_calibration_end_to_end():
    no_cal = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(), CONFIG, calibration={})
    calibrated = proj.compute_channel_pts_per_fixture(
        _one_defender(), _fixture(), CONFIG, calibration={"DEF": {"cleansheet": 1.2}}
    )
    # cleansheet_pts is embedded (not minutes-scaled) but must still respond
    # to calibration via cleansheet_pts_cal, which xpts_fixture now sums.
    assert calibrated["cleansheet_pts_cal"].iloc[0] > no_cal["cleansheet_pts_cal"].iloc[0]
    assert calibrated["xpts_fixture"].iloc[0] > no_cal["xpts_fixture"].iloc[0]


def test_load_calibration_factors_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(proj, "OUTPUT_DIR", tmp_path)
    assert proj.load_calibration_factors() == {}
