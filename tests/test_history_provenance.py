"""
Tests for fpl/history/provenance.py — spec §3.6.

hours_to_deadline is the load-bearing field: a projection 4 days out and
one 2 hours out are different events, and it is computable ONLY at
capture time (same reasoning as fpl/collect/snapshot.py's own column).
git_dirty matters because a local run with uncommitted changes is NOT
reproducible from its SHA alone.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone

from fpl.history import provenance


def test_hours_to_deadline_is_negative_after_the_deadline_passed():
    now = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(
        target_gameweek=1, deadline_utc="2026-08-21T17:30:00Z", now=now
    )
    assert meta["hours_to_deadline"] < 0
    assert round(meta["hours_to_deadline"], 1) == -18.5


def test_hours_to_deadline_is_positive_before_the_deadline():
    now = datetime(2026, 8, 20, 17, 30, 0, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(
        target_gameweek=2, deadline_utc="2026-08-21T17:30:00Z", now=now
    )
    assert round(meta["hours_to_deadline"], 1) == 24.0


def test_hours_to_deadline_is_none_when_deadline_unknown():
    """Never guess. A missing deadline is null, not 0."""
    meta = provenance.build_run_metadata(
        target_gameweek=1, deadline_utc=None,
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    assert meta["hours_to_deadline"] is None


def test_asof_is_iso_basic_and_asof_iso_is_extended():
    now = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(1, "2026-08-21T17:30:00Z", now=now)
    assert meta["asof"] == "20260822T125314Z"
    assert meta["asof_iso"] == "2026-08-22T12:53:14+00:00"


def test_trigger_reads_github_event_name_else_local(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["trigger"] == "local"

    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["trigger"] == "workflow_dispatch"


def test_provenance_defaults_to_recorded():
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["provenance"] == "recorded"


def test_config_sha256_is_stable_and_content_dependent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(provenance, "CONFIG_PATH", cfg)
    first = provenance.config_sha256()
    assert provenance.config_sha256() == first

    cfg.write_text("a: 2\n", encoding="utf-8")
    assert provenance.config_sha256() != first


def test_config_sha256_is_none_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "CONFIG_PATH", tmp_path / "nope.yaml")
    assert provenance.config_sha256() is None


def test_run_id_is_unique_per_call():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    a = provenance.build_run_metadata(1, None, now=now)["run_id"]
    b = provenance.build_run_metadata(1, None, now=now)["run_id"]
    assert a != b
