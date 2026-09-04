"""
Tests for fpl/collect/history_loader.py.

Regression: the rolling-start-rate lag fix (fpl/project/minutes.py) needs the
CURRENT season's merged_gw.csv present under data/raw/history/. That only
happens if the weekly collect step actually pulls it — so the default season
list history_loader downloads must include config["season"], not just the
completed-seasons list in config["history"]["seasons"].
"""
from __future__ import annotations

from fpl.collect import history_loader


def test_seasons_to_load_appends_current_season():
    config = {
        "season": "2026-27",
        "history": {"seasons": ["2023-24", "2024-25", "2025-26"]},
    }
    assert history_loader.seasons_to_load(config) == [
        "2023-24", "2024-25", "2025-26", "2026-27",
    ]


def test_seasons_to_load_no_duplicate_when_current_already_listed():
    config = {
        "season": "2025-26",
        "history": {"seasons": ["2023-24", "2024-25", "2025-26"]},
    }
    assert history_loader.seasons_to_load(config) == [
        "2023-24", "2024-25", "2025-26",
    ]
