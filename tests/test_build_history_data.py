"""
Tests for scripts/build_history_data.py — spec §7.4.

The dashboard contract is unchanged: reads committed artefacts, never
recomputes. This script reads the archive through fpl.history.query and
emits one JSON blob; it must never call the projection pipeline.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpl.history import paths
import scripts.build_history_data as bhd

pytest.importorskip("duckdb")


def _write_run(asof, gw, rows):
    df = pd.DataFrame(rows, columns=["id", "event", "xpts"])
    p = paths.projections_partition(gw, asof, "m0_rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    m = paths.id_code_map_path(asof)
    m.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"id": [1], "code": [111], "web_name": ["A"]}).to_parquet(m, index=False)
    paths.run_json_path(asof).write_text(json.dumps({
        "run_id": "r", "asof": asof, "asof_iso": None, "git_sha": None,
        "git_dirty": None, "config_sha256": None, "trigger": "schedule",
        "target_gameweek": gw, "deadline_utc": None, "hours_to_deadline": -1.0,
        "provenance": "recorded", "archived": [],
    }), encoding="utf-8")


def test_payload_reports_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    payload = bhd.build_history_payload()
    assert payload["coverage"][0]["gw"] == 1
    assert payload["coverage"][0]["n_complete"] == 1


def test_revision_is_marked_insufficient_with_one_run(tmp_path, monkeypatch):
    """Honest empty state, not an invented chart (spec §7.2)."""
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    payload = bhd.build_history_payload()
    assert payload["revision"]["sufficient"] is False
    assert payload["revision"]["n_runs_max_in_a_gameweek"] == 1


def test_revision_becomes_sufficient_with_two_runs_in_one_gameweek(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    _write_run("20260821T090000Z", 1, [(1, 1, 7.0)])
    payload = bhd.build_history_payload()
    assert payload["revision"]["sufficient"] is True
    assert payload["revision"]["n_runs_max_in_a_gameweek"] == 2
    series = payload["revision"]["series"]
    assert series and series[0]["xpts"] == [5.0, 7.0]


def test_empty_archive_yields_honest_empty_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    (tmp_path / "history").mkdir()
    payload = bhd.build_history_payload()
    assert payload["coverage"] == []
    assert payload["revision"]["sufficient"] is False


# --------------------------------------------------------------------------
# Template substitution regression (spec §7.4)
# --------------------------------------------------------------------------
TEMPLATE = Path(__file__).resolve().parents[1] / "dashboard" / "template.html"


def test_history_placeholder_has_no_trailing_fallback_value():
    """REGRESSION: the placeholder was first written as

        const HISTORY = /*__HISTORY__*/null;

    intending `null` as a fallback. Substitution replaces only the comment,
    so a real payload produced `const HISTORY = {...}null;` — a syntax
    error that killed the ENTIRE script block, not just the History view
    (DATA became undefined and every other tab's rendering died with it).
    The placeholder must stand alone; the builder substitutes "null" itself.
    """
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "/*__HISTORY__*/;" in html, "placeholder must stand alone before the semicolon"
    assert "/*__HISTORY__*/null" not in html


def test_substituting_both_placeholders_yields_parseable_assignments():
    """Both placeholders must always be substituted — leaving either in
    place emits `const X = ;`, which is equally fatal."""
    html = TEMPLATE.read_text(encoding="utf-8")
    out = html.replace("/*__DATA__*/", "{}").replace("/*__HISTORY__*/", "null")
    assert "const DATA = {};" in out
    assert "const HISTORY = null;" in out
    assert "/*__DATA__*/" not in out and "/*__HISTORY__*/" not in out
