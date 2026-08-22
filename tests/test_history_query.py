"""
Tests for fpl/history/query.py — spec §5.

The subtlety that gets people (spec §3.3a): `gw` is the gameweek a run
was TARGETING; `event` is the gameweek a row's xPts is FOR. A revision
series fixes `event` and therefore spans MULTIPLE `gw` partitions. Get
this backwards and the whole archive answers the wrong question.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fpl.history import paths, query

duckdb = pytest.importorskip("duckdb")


def _write_run(hist, asof, gw, rows, *, complete=True, provenance="recorded"):
    """rows: list of (id, event, xpts)"""
    df = pd.DataFrame(rows, columns=["id", "event", "xpts"])
    p = paths.projections_partition(gw, asof, "m0_rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)

    m = paths.id_code_map_path(asof)
    m.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"id": [1, 2], "code": [111, 222], "web_name": ["A", "B"]}).to_parquet(m, index=False)

    if complete:
        paths.run_json_path(asof).write_text(json.dumps({
            "run_id": "r", "asof": asof, "asof_iso": None, "git_sha": None,
            "git_dirty": None, "config_sha256": None, "trigger": "schedule",
            "target_gameweek": gw, "deadline_utc": None, "hours_to_deadline": -1.0,
            "provenance": provenance, "archived": [],
        }), encoding="utf-8")


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    # Two runs targeting GW1, both projecting events 1 and 2.
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0), (1, 2, 4.0)])
    _write_run(hist, "20260821T090000Z", 1, [(1, 1, 7.0), (1, 2, 4.5)])
    # A later run targeting GW2, still projecting event 2.
    _write_run(hist, "20260825T090000Z", 2, [(1, 2, 6.0)])
    return hist


def test_runs_lists_complete_runs(archive_root):
    a = query.open_archive(archive_root)
    assert len(a.runs()) == 3


def test_incomplete_runs_are_excluded(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0)], complete=True)
    _write_run(hist, "20260819T090000Z", 1, [(1, 1, 9.9)], complete=False)

    a = query.open_archive(hist)
    assert len(a.runs()) == 1
    assert "20260819T090000Z" not in set(a.projections()["asof"])


def test_projections_carry_code_not_just_id(archive_root):
    """Spec §3.4 — id is not season-stable, code is."""
    a = query.open_archive(archive_root)
    df = a.projections(gw=1)
    assert "code" in df.columns
    assert set(df["code"]) == {111}


def test_revisions_fix_event_and_span_multiple_gw_partitions(archive_root):
    """THE load-bearing semantic (spec §3.3a). Event 2 was projected by
    all three runs — two under gw=1, one under gw=2. A revision series
    must include all three, ordered by asof."""
    a = query.open_archive(archive_root)
    rev = a.revisions(event=2, player_code=111)

    assert list(rev["asof"]) == ["20260818T090000Z", "20260821T090000Z", "20260825T090000Z"]
    assert list(rev["xpts"]) == [4.0, 4.5, 6.0]
    assert set(rev["gw"]) == {1, 2}


def test_revisions_for_event_1_only_span_gw1(archive_root):
    a = query.open_archive(archive_root)
    rev = a.revisions(event=1, player_code=111)
    assert list(rev["xpts"]) == [5.0, 7.0]


def test_coverage_reports_runs_per_gameweek(archive_root):
    a = query.open_archive(archive_root)
    cov = a.coverage().set_index("gw")
    assert int(cov.loc[1, "n_complete"]) == 2
    assert int(cov.loc[2, "n_complete"]) == 1


def test_coverage_flags_incomplete_runs(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0)], complete=True)
    _write_run(hist, "20260819T090000Z", 1, [(1, 1, 9.9)], complete=False)

    cov = query.open_archive(hist).coverage().set_index("gw")
    assert int(cov.loc[1, "n_complete"]) == 1
    assert int(cov.loc[1, "n_incomplete"]) == 1


def test_empty_archive_returns_empty_frames_not_errors(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.mkdir()
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    a = query.open_archive(hist)
    assert a.runs().empty
    assert a.coverage().empty
    assert a.projections().empty


def test_archive_path_containing_an_apostrophe_still_queries(tmp_path, monkeypatch):
    """REGRESSION: this repo lives at "D:\\CT's Portfolio\\FPL Pipeline".

    The first implementation interpolated the glob into SQL with an
    f-string, so the apostrophe terminated the string literal and DuckDB
    raised a ParserException — which a broad `except Exception` then
    swallowed into an empty result. Every tmp_path test passed while
    every real query silently returned nothing. Bind parameters instead.
    """
    hist = tmp_path / "CT's Portfolio" / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0), (1, 2, 4.0)])

    df = query.open_archive(hist).projections()
    assert len(df) == 2
    assert set(df["event"]) == {1, 2}


def test_unreadable_archive_raises_rather_than_looking_empty(tmp_path, monkeypatch):
    """A corrupt parquet must surface, not masquerade as 'nothing archived'
    — silent failure is the exact bug class this archive exists to expose."""
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0)])
    paths.projections_partition(1, "20260818T090000Z", "m0_rules").write_bytes(b"not a parquet file")

    with pytest.raises(Exception):
        query.open_archive(hist).projections()
