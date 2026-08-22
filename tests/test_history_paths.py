"""
Tests for fpl/history/paths.py — the single source of truth for the
Phase G archive layout (spec §3.1/§3.2). Four call sites build paths
(archive, query, manifest, migration); if any of them constructs paths
by hand instead of calling these helpers, the layout silently drifts.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fpl.history import paths


def test_format_asof_uses_iso_basic_not_extended():
    """Colons are illegal in Windows path names, so the spec's asof key
    is ISO 8601 BASIC. This is the single most load-bearing formatting
    decision in the archive — partitions are immutable once written."""
    dt = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    assert paths.format_asof(dt) == "20260822T125314Z"
    assert ":" not in paths.format_asof(dt)


def test_parse_asof_round_trips():
    dt = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    assert paths.parse_asof(paths.format_asof(dt)) == dt


def test_parse_asof_rejects_iso_extended():
    with pytest.raises(ValueError):
        paths.parse_asof("2026-08-22T12:53:14Z")


def test_projections_partition_is_hive_partitioned(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    p = paths.projections_partition(1, "20260822T125314Z", "m0_rules")
    assert p == tmp_path / "projections" / "gw=1" / "asof=20260822T125314Z" / "model=m0_rules" / "players.parquet"


def test_decisions_and_health_and_run_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    asof = "20260822T125314Z"
    assert paths.decisions_partition(1, asof) == tmp_path / "decisions" / "gw=1" / f"asof={asof}" / "recommendation.json"
    assert paths.health_partition(1, asof, "m2_xg") == tmp_path / "health" / "gw=1" / f"asof={asof}" / "model=m2_xg" / "model_health.json"
    assert paths.actuals_partition(3) == tmp_path / "actuals" / "gw=3" / "players.parquet"
    assert paths.run_json_path(asof) == tmp_path / "_runs" / f"asof={asof}" / "run.json"
    assert paths.id_code_map_path(asof) == tmp_path / "_runs" / f"asof={asof}" / "id_code_map.parquet"


def test_runs_dir_is_underscore_prefixed_so_hive_globs_skip_it(tmp_path, monkeypatch):
    """_runs must not be picked up by projections/**/*.parquet globs."""
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    assert paths.run_partition("20260822T125314Z").parent.name == "_runs"


def test_models_constant_matches_the_registry():
    assert paths.MODELS == ("m0_rules", "m2_xg", "m3_understat")
