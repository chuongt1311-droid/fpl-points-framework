"""
Tests for dashboard/_pitch — the shared formation-pitch renderer used by
both app2 and the static dashboard (C1 / PROJECT_LOG §23).
"""
from __future__ import annotations

from dashboard import _pitch


def _p(name, pos, **extra):
    return {"web_name": name, "position": pos, "team_short": "ARS", **extra}


XI = (
    [_p("Raya", "GK", xpts=3.0)]
    + [_p(f"D{i}", "DEF", xpts=4.0) for i in range(4)]
    + [_p(f"M{i}", "MID", xpts=5.0) for i in range(3)]
    + [_p(f"F{i}", "FWD", xpts=6.0) for i in range(3)]
)
BENCH = [_p("Sub1", "GK", xpts=1.0)] + [_p(f"S{i}", "DEF", xpts=2.0) for i in range(3)]


def test_render_pitch_lays_out_four_formation_rows_plus_bench():
    html = _pitch.render_pitch(XI, BENCH, mode="pick")
    assert html.count('class="pitch-row"') == 4  # GK, DEF, MID, FWD
    assert 'class="pitch-bench"' in html
    for p in XI:
        assert p["web_name"] in html


def test_render_pitch_points_mode_shows_points_not_xpts():
    html = _pitch.render_pitch([_p("Haaland", "FWD", pts=13)], [], mode="points")
    assert ">13<" in html


def test_render_pitch_transfers_mode_tags_each_chip_with_its_id():
    html = _pitch.render_pitch([_p("Palmer", "MID", xpts=5.5, id=245)], [], mode="transfers")
    assert 'data-id="245"' in html


def test_render_pitch_marks_the_captain_chip():
    html = _pitch.render_pitch([_p("Haaland", "FWD", xpts=6.0, is_captain=True)], [], mode="pick")
    assert "is-cap" in html


def test_pitch_css_is_nonempty_and_scopes_its_selectors():
    assert len(_pitch.PITCH_CSS) > 100
    assert ".pitch" in _pitch.PITCH_CSS
