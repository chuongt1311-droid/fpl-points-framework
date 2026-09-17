"""Flask route smoke tests for app2 — the data module is stubbed (C1 §23)."""
from __future__ import annotations

import pytest

from dashboard.app2 import create_app
from dashboard.app2 import data as d


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(d, "points_view", lambda config=None: {
        "event": 3, "total": 61, "rank": 900000, "active_chip": None,
        "xi": [{"web_name": "Haaland", "position": "FWD", "team_short": "MCI",
                "pts": 26, "is_captain": True, "is_vice": False}],
        "bench": [],
    })
    monkeypatch.setattr(d, "pick_team_view", lambda config=None: {
        "event": 4, "formation": "4-3-3", "captain": "Haaland", "bank": 0.4,
        "xi": [{"web_name": "Haaland", "position": "FWD", "team_short": "MCI",
                "xpts": 24.1, "next_gw_xpts": 6.2, "start_prob": 0.99, "status": "a",
                "news": "", "verdict": "hold", "is_captain": True, "is_vice": False}],
        "bench": [],
    })
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_points_route_renders_the_pitch_and_actual_points(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "pitch-row" in body
    assert "Haaland" in body
    assert "26" in body  # captain's actual points


def test_pick_route_renders_model_overlay(client):
    r = client.get("/pick")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "24.1" in body       # weighted xpts on the card
    assert "startbar" in body   # start-probability bar


def test_nav_lists_the_fpl_style_sections(client):
    body = client.get("/").get_data(as_text=True)
    for label in ("Points", "Pick Team", "Transfers", "Fixtures"):
        assert label in body
