"""
Tests for fpl/collect/actuals.py — spec §3.1.

Pure unit tests: synthetic bootstrap/event_live dicts, no file I/O beyond
tmp_path, no network. Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.collect import actuals


def _bootstrap(finished: bool, data_checked: bool) -> dict:
    return {
        "events": [{"id": 1, "finished": finished, "data_checked": data_checked}],
        "elements": [{"id": 1, "code": 100001}, {"id": 2, "code": 100002}],
    }


def _event_live() -> dict:
    return {"elements": [
        {"id": 1, "stats": {"minutes": 90, "total_points": 8, "goals_scored": 1, "assists": 0,
                             "clean_sheets": 1, "goals_conceded": 0, "saves": 0, "bonus": 2,
                             "bps": 30, "yellow_cards": 0, "red_cards": 0, "defensive_contribution": 0}},
        {"id": 2, "stats": {"minutes": 0, "total_points": 0}},  # unused stats default via .get
    ]}


def test_gameweek_is_ready_requires_both_finished_and_data_checked():
    assert not actuals.gameweek_is_ready(1, _bootstrap(finished=False, data_checked=False))
    assert not actuals.gameweek_is_ready(1, _bootstrap(finished=True, data_checked=False))
    assert not actuals.gameweek_is_ready(1, _bootstrap(finished=False, data_checked=True))
    assert actuals.gameweek_is_ready(1, _bootstrap(finished=True, data_checked=True))


def test_gameweek_is_ready_false_for_an_unknown_event():
    assert not actuals.gameweek_is_ready(99, _bootstrap(finished=True, data_checked=True))


def test_build_actuals_rows_maps_id_to_code_and_fills_missing_stats_with_zero():
    bootstrap = _bootstrap(finished=True, data_checked=True)
    rows = actuals.build_actuals_rows(1, _event_live(), bootstrap)

    row1 = rows[rows["id"] == 1].iloc[0]
    assert row1["code"] == 100001
    assert row1["total_points"] == 8
    assert row1["bonus"] == 2

    row2 = rows[rows["id"] == 2].iloc[0]
    assert row2["code"] == 100002
    assert row2["goals_scored"] == 0  # missing from stats dict, defaults to 0 not NaN


def test_append_actuals_is_append_only_and_rejects_a_duplicate_event(tmp_path):
    path = tmp_path / "actuals_test.csv"
    bootstrap = _bootstrap(finished=True, data_checked=True)
    rows = actuals.build_actuals_rows(1, _event_live(), bootstrap)

    actuals.append_actuals(rows, path=path)
    actuals.append_actuals(rows, path=path)  # same event again — must not duplicate

    on_disk = pd.read_csv(path)
    assert len(on_disk) == 2  # 2 players, one gameweek — not 4
    assert on_disk["event"].nunique() == 1


def test_append_actuals_appends_a_second_distinct_gameweek(tmp_path):
    path = tmp_path / "actuals_test.csv"
    bootstrap = _bootstrap(finished=True, data_checked=True)
    gw1_rows = actuals.build_actuals_rows(1, _event_live(), bootstrap)
    gw2_rows = actuals.build_actuals_rows(2, _event_live(), bootstrap)

    actuals.append_actuals(gw1_rows, path=path)
    actuals.append_actuals(gw2_rows, path=path)

    on_disk = pd.read_csv(path)
    assert len(on_disk) == 4
    assert set(on_disk["event"]) == {1, 2}
