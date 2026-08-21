"""
Tests for fpl/collect/sources/understat.py (v3 plan §A2).

Network-dependent tests are marked and skip gracefully when offline —
this repo's existing bar (test_snapshot.py etc.) is "no network in tests,"
but the adapter's WHOLE job is fetching real data, so a pure mock would
test nothing real. Split: the caching/degradation contract is tested with
a stubbed _fetch_live (no network); the live parse is a separate,
explicitly-network test that only runs the cache-hit path if a prior
session already populated data/raw/understat/2025.csv (committed once,
per plan §A2's "fetch once, never again" for a completed season).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.collect.sources import understat as understat_mod
from fpl.collect.sources.understat import UnderstatAdapter


def test_fetch_returns_empty_dataframe_and_sets_health_error_on_failure(monkeypatch, tmp_path):
    """Rule 1 (base.py): a source failure degrades, never crashes."""
    monkeypatch.setattr(understat_mod, "RAW_DIR", tmp_path)

    def _boom(self, season):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(UnderstatAdapter, "_fetch_live", _boom)
    adapter = UnderstatAdapter()

    result = adapter.fetch("9999")
    assert result.empty
    assert "source_player_id" in result.columns

    health = adapter.health()
    assert health.rows_returned == 0
    assert health.error is not None
    assert "simulated network failure" in health.error


def test_health_before_any_fetch_call_does_not_raise():
    adapter = UnderstatAdapter()
    health = adapter.health()
    assert health.rows_returned == 0
    assert health.error is not None


def test_fetch_caches_to_disk_and_a_second_call_never_hits_the_network(monkeypatch, tmp_path):
    monkeypatch.setattr(understat_mod, "RAW_DIR", tmp_path)

    call_count = {"n": 0}

    def _fake_fetch_live(self, season):
        call_count["n"] += 1
        return pd.DataFrame({
            "source_player_id": [1, 2], "player_name": ["A", "B"], "team_title": ["X", "Y"],
            "position": ["F", "M"], "games": [10, 10], "time": [900, 900], "goals": [5, 2],
            "assists": [1, 3], "npg": [5, 2], "xG": [4.5, 1.8], "xA": [1.1, 2.9],
            "npxG": [4.5, 1.8], "xGChain": [5.0, 3.0], "xGBuildup": [1.0, 2.0],
            "shots": [30, 15], "key_passes": [10, 20], "yellow_cards": [1, 0], "red_cards": [0, 0],
        })

    monkeypatch.setattr(UnderstatAdapter, "_fetch_live", _fake_fetch_live)
    adapter = UnderstatAdapter()

    first = adapter.fetch("2025")
    assert call_count["n"] == 1
    assert len(first) == 2
    assert (tmp_path / "2025.csv").exists()

    second_adapter = UnderstatAdapter()  # fresh instance, same cache dir
    second = second_adapter.fetch("2025")
    assert call_count["n"] == 1  # NOT incremented — cache hit, no network call
    assert len(second) == 2


@pytest.mark.network
def test_live_fetch_of_a_completed_season_returns_real_shaped_data():
    """
    Real network test (skip if offline — see conftest marker handling, or
    run explicitly: pytest tests/ -v -m network). Understat's robots.txt
    disallows automated access; the user explicitly authorized this
    adapter's use for this private tool — see understat.py's module
    docstring before extending this test's scope.
    """
    adapter = UnderstatAdapter()
    df = adapter.fetch("2025")
    health = adapter.health()

    assert health.error is None
    assert len(df) > 400  # a real Premier League season roster
    assert "source_player_id" in df.columns
    assert "xG" in df.columns
    assert (df["xG"] >= 0).all()
