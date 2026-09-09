"""
Tests for dashboard/app2/data.py assembly functions (C1 / PROJECT_LOG §23).
The FPL API is stubbed — no network.
"""
from __future__ import annotations

import pandas as pd
import pytest

from dashboard.app2 import data as d

CONFIG = {"fpl": {"entry_id": 999}, "horizon": {"gameweeks": 5}}

_BOOT = {
    "events": [{"id": 1, "finished": True, "is_previous": True},
               {"id": 2, "finished": False, "is_current": True}],
    "elements": [
        {"id": 10, "web_name": "Raya", "team": 1, "element_type": 1, "now_cost": 60,
         "status": "a", "news": "", "chance_of_playing_next_round": None, "event_points": 6,
         "selected_by_percent": "20.0"},
        {"id": 11, "web_name": "Saka", "team": 1, "element_type": 3, "now_cost": 100,
         "status": "a", "news": "", "chance_of_playing_next_round": None, "event_points": 9,
         "selected_by_percent": "40.0"},
    ],
    "teams": [{"id": 1, "short_name": "ARS"}],
}
_PICKS = {"picks": [
    {"element": 10, "is_captain": False, "is_vice_captain": True, "multiplier": 1},
    {"element": 11, "is_captain": True, "is_vice_captain": False, "multiplier": 2},
]}


@pytest.fixture(autouse=True)
def _stub_api(monkeypatch):
    monkeypatch.setattr(d, "live_bootstrap", lambda: _BOOT)
    monkeypatch.setattr(d, "live_entry_picks", lambda eid, ev: _PICKS)
    monkeypatch.setattr(d, "live_entry_history", lambda eid: {
        "current": [{"event": 1, "points": 55, "overall_rank": 1_000_000, "bank": 5}]})
    monkeypatch.setattr(d, "live_event_live", lambda ev: {"elements": [
        {"id": 10, "stats": {"total_points": 6}}, {"id": 11, "stats": {"total_points": 9}}]})
    monkeypatch.setattr(d, "load_config", lambda: CONFIG)


def test_points_view_uses_actual_points_times_multiplier():
    v = d.points_view()
    saka = next(r for r in v["xi"] if r["web_name"] == "Saka")
    assert saka["pts"] == 18  # 9 × 2 (captain)
    assert saka["is_captain"] is True
    assert v["event"] == 1  # last completed


def test_pick_team_view_overlays_weighted_xpts(monkeypatch, tmp_path):
    proj = pd.DataFrame({"id": [10, 11], "event": [2, 2], "xpts": [3.0, 6.0]})
    p = tmp_path / "gw2.parquet"
    proj.to_parquet(p)
    monkeypatch.setattr(d, "PROJECTIONS_DIR", tmp_path)
    monkeypatch.setattr(d.project_mod, "weighted_horizon_total",
                        lambda proj, cfg: pd.DataFrame({
                            "id": [10, 11], "weighted_xpts": [15.0, 30.0],
                            "next_gw_xpts": [3.0, 6.0], "confidence": ["high", "high"]}))
    v = d.pick_team_view()
    saka = next(r for r in v["xi"] if r["web_name"] == "Saka")
    assert saka["xpts"] == pytest.approx(30.0)
    assert saka["verdict"] in ("up", "down", "hold")
