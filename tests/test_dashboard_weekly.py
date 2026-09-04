"""
Tests for the per-gameweek dashboard: build_dashboard_data.py's gameweek
selection (was hardcoded to GW1 — PROJECT_LOG §18) and the new
scripts/archive_dashboard_week.py that snapshots each week's self-contained
index.html into dashboard/weeks/ with a browsable index.
"""
from __future__ import annotations

import json

import pytest

import scripts.archive_dashboard_week as arch
from scripts import build_dashboard_data as bdd


# ---- build_dashboard_data: gameweek selection --------------------------------

def _touch_rec(output_dir, gw: int) -> None:
    (output_dir / f"gw{gw}_recommendations.json").write_text(
        json.dumps({"gameweek": gw}), encoding="utf-8"
    )


def test_select_target_gameweek_prefers_the_upcoming_gameweek(tmp_path):
    for gw in (1, 2, 3):
        _touch_rec(tmp_path, gw)
    assert bdd.select_target_gameweek(tmp_path, [3, 4, 5, 6, 7]) == 3


def test_select_target_gameweek_falls_back_to_latest_solved_when_upcoming_absent(tmp_path):
    """Pipeline hasn't solved the upcoming GW yet (Decide step not run since
    the last one finished) — show the most recent one we do have, not crash."""
    _touch_rec(tmp_path, 1)
    _touch_rec(tmp_path, 2)
    assert bdd.select_target_gameweek(tmp_path, [3, 4, 5, 6, 7]) == 2


def test_select_target_gameweek_raises_when_no_recommendations_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        bdd.select_target_gameweek(tmp_path, [3, 4, 5])


def test_parse_deadline_reads_the_matching_event(tmp_path):
    bootstrap = {"events": [
        {"id": 2, "deadline_time": "2026-08-28T17:30:00Z"},
        {"id": 3, "deadline_time": "2026-09-04T17:30:00Z"},
    ]}
    assert bdd.parse_deadline(bootstrap, 3) == "2026-09-04T17:30:00Z"
    assert bdd.parse_deadline(bootstrap, 9) is None


# ---- archive_dashboard_week -------------------------------------------------

def _write_index(dashboard_dir, gameweek: int, xi_total: float, captain: str) -> None:
    data = {
        "meta": {"gameweek": gameweek, "season": "2026-27",
                 "generated_note": f"snapshot gw{gameweek}"},
        "squad": {"next_gw_expected_points": xi_total,
                  "captain": {"web_name": captain}},
    }
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    (dashboard_dir / "index.html").write_text(
        "<html><body>\nconst DATA = " + json.dumps(data, separators=(",", ":")) + ";\n"
        "</body></html>",
        encoding="utf-8",
    )


def test_archive_week_copies_index_to_a_numbered_snapshot(tmp_path):
    _write_index(tmp_path, 3, 65.5, "Haaland")
    out = arch.archive_week(tmp_path, 3)
    assert out == tmp_path / "weeks" / "gw3.html"
    assert out.read_text(encoding="utf-8") == (tmp_path / "index.html").read_text(encoding="utf-8")


def test_archive_week_rewrites_the_nav_link_for_its_new_location(tmp_path):
    """index.html links to weeks/index.html; the snapshot lives IN weeks/,
    so that link must become a sibling reference or it 404s."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "index.html").write_text(
        '<a href="weeks/index.html">past weeks</a>\nconst DATA = {"meta":{"gameweek":3}};\n',
        encoding="utf-8",
    )
    out = arch.archive_week(tmp_path, 3)
    body = out.read_text(encoding="utf-8")
    assert 'href="weeks/index.html"' not in body
    assert 'href="index.html"' in body


def test_archive_week_overwrites_the_same_gameweek_on_re_run(tmp_path):
    _write_index(tmp_path, 3, 60.0, "Salah")
    arch.archive_week(tmp_path, 3)
    _write_index(tmp_path, 3, 70.0, "Haaland")
    out = arch.archive_week(tmp_path, 3)
    assert "Haaland" in out.read_text(encoding="utf-8")
    assert "Salah" not in out.read_text(encoding="utf-8")


def test_rebuild_index_lists_every_week_newest_first(tmp_path):
    weeks = tmp_path / "weeks"
    for gw, cap in [(2, "Salah"), (3, "Haaland"), (4, "Palmer")]:
        _write_index(tmp_path, gw, 60.0 + gw, cap)
        arch.archive_week(tmp_path, gw)

    index = arch.rebuild_index(tmp_path)
    assert index == weeks / "index.html"
    html = index.read_text(encoding="utf-8")
    assert html.index("gw4.html") < html.index("gw3.html") < html.index("gw2.html")
    assert "Haaland" in html and "Palmer" in html


def test_rebuild_index_handles_no_weeks_yet(tmp_path):
    index = arch.rebuild_index(tmp_path)
    assert index.exists()
    assert "No weekly snapshots yet" in index.read_text(encoding="utf-8")
