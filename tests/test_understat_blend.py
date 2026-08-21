"""
Tests for fpl/project/understat_blend.py (v3 plan §B3, model M3).

Pure unit tests: synthetic frames + monkeypatched UnderstatAdapter.fetch
(no network, no dependency on the real cached seasons — see
test_understat_adapter.py for the real-data exercise). Same bar as
tests/test_xg_blend.py.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.project import understat_blend as ub


def test_compute_recency_weighted_npxg_weights_recent_season_more(monkeypatch):
    """Two seasons, same player: more recent season's npxG90 should pull
    the weighted average toward it (decay < 1 means older season counts
    for less)."""
    def _fake_fetch(self, season):
        if season == "2023":  # "2023-24" -> older
            return pd.DataFrame({
                "source_player_id": [1], "time": [900], "npxG": [4.5], "xA": [1.0],
            })
        if season == "2024":  # "2024-25" -> more recent
            return pd.DataFrame({
                "source_player_id": [1], "time": [900], "npxG": [9.0], "xA": [2.0],
            })
        return pd.DataFrame()

    monkeypatch.setattr(ub.UnderstatAdapter, "fetch", _fake_fetch)
    result = ub.compute_recency_weighted_npxg(["2023-24", "2024-25"], decay=0.6)

    # older: npxG90 = 4.5/900*90 = 0.45; recent: 9.0/900*90 = 0.9
    # weight: older=0.6, recent=1.0 -> weighted average closer to 0.9 than to 0.45
    row = result[result["source_player_id"] == 1].iloc[0]
    midpoint = (0.45 + 0.9) / 2
    assert row["npxG90"] > midpoint


def test_compute_recency_weighted_npxg_handles_an_empty_fetch_gracefully(monkeypatch):
    monkeypatch.setattr(ub.UnderstatAdapter, "fetch", lambda self, season: pd.DataFrame())
    result = ub.compute_recency_weighted_npxg(["2023-24", "2024-25"], decay=0.6)
    assert result.empty
    assert list(result.columns) == ["source_player_id", "npxG90", "xA90_understat", "understat_weighted_minutes"]


def test_apply_understat_blend_leaves_unmatched_players_unchanged():
    player_inputs = pd.DataFrame([
        {"id": 1, "code": 999, "position": "FWD", "price": 8.0,
         "goals_scored_per90": 0.5, "weighted_minutes": 2000.0},
    ])
    empty_map = pd.DataFrame(columns=["fpl_code", "understat_id"])
    config = {"understat_blend": {"k_npxg": 1500}, "history": {"seasons": [], "recency_decay": 0.6}}

    result = ub.apply_understat_blend(player_inputs, config, player_id_map=empty_map)
    assert result["goals_scored_per90"].iloc[0] == 0.5  # unchanged — no bridge


def test_apply_understat_blend_moves_matched_players_toward_npxg(monkeypatch):
    player_inputs = pd.DataFrame([
        {"id": 1, "code": 999, "position": "FWD", "price": 8.0,
         "goals_scored_per90": 0.5, "weighted_minutes": 2000.0},
    ])
    player_id_map = pd.DataFrame([{"fpl_code": 999, "understat_id": 42}])

    def _fake_compute(seasons, decay):
        return pd.DataFrame([
            {"source_player_id": 42, "npxG90": 0.9, "xA90_understat": 0.1, "understat_weighted_minutes": 500.0},
        ])

    monkeypatch.setattr(ub, "compute_recency_weighted_npxg", _fake_compute)
    config = {"understat_blend": {"k_npxg": 1500}, "history": {"seasons": ["2023-24"], "recency_decay": 0.6}}

    result = ub.apply_understat_blend(player_inputs, config, player_id_map=player_id_map)
    rate = result["goals_scored_per90"].iloc[0]
    # v3 = 1500/(500+1500) = 0.75 -> rate = 0.75*0.9 + 0.25*0.5 = 0.8
    assert abs(rate - 0.8) < 1e-9
