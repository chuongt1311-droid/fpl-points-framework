"""
Tests for fpl/project/project.py::next_n_gameweeks — the horizon's start.

WHY THIS EXISTS (2026-08-25): the selector filtered on `event.finished`
alone. FPL does not flip `finished`/`data_checked` at the final whistle —
it flips them when its own post-gameweek review completes, which can lag by
days. On 2026-08-25, four days after the GW1 deadline and with all ten GW1
fixtures carrying finished=True + provisional bonus, GW1's EVENT still read
finished=False, is_current=True. So `next_n_gameweeks(5)` returned
[1,2,3,4,5] and every scheduled run re-projected, re-solved and re-archived
a gameweek nobody could still transfer into. Commit 037fce9 is a real
instance: it overwrote data/output/gw1_recommendations.json and archived a
decision under gw=1 while its own run.json recorded target_gameweek=2.

The invariant that actually matters is not "has FPL finished grading this"
but "can the manager still act on this" — i.e. is the deadline in the
future. That is what these tests pin.
"""
from __future__ import annotations

import json

import pytest

from fpl.project import project


def _events(spec):
    """spec: list of (id, deadline_iso, finished)."""
    return {"events": [
        {"id": i, "deadline_time": d, "finished": f} for i, d, f in spec
    ]}


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    def _write(spec):
        raw = tmp_path / "raw"
        raw.mkdir(exist_ok=True)
        (raw / "bootstrap_static.json").write_text(
            json.dumps(_events(spec)), encoding="utf-8"
        )
        monkeypatch.setattr(project, "RAW_DIR", raw)
    return _write


NOW = "2026-08-25T12:00:00Z"


def test_skips_played_gameweek_fpl_has_not_marked_finished(bootstrap):
    """The regression. GW1's deadline has passed but finished is still False."""
    bootstrap([
        (1, "2026-08-21T17:30:00Z", False),   # played, not yet graded
        (2, "2026-08-28T17:30:00Z", False),
        (3, "2026-09-04T17:30:00Z", False),
        (4, "2026-09-12T12:30:00Z", False),
        (5, "2026-09-18T17:30:00Z", False),
        (6, "2026-10-10T10:00:00Z", False),
    ])
    assert project.next_n_gameweeks(5, now=NOW) == [2, 3, 4, 5, 6]


def test_still_skips_gameweeks_fpl_has_marked_finished(bootstrap):
    """The original filter must survive: a graded GW is excluded too."""
    bootstrap([
        (1, "2026-08-21T17:30:00Z", True),
        (2, "2026-08-28T17:30:00Z", False),
        (3, "2026-09-04T17:30:00Z", False),
    ])
    assert project.next_n_gameweeks(2, now=NOW) == [2, 3]


def test_deadline_exactly_now_is_already_gone(bootstrap):
    """At the deadline the team is locked — it is not a decision target."""
    bootstrap([
        (1, NOW, False),
        (2, "2026-08-28T17:30:00Z", False),
    ])
    assert project.next_n_gameweeks(1, now=NOW) == [2]


def test_missing_deadline_falls_back_to_the_finished_flag(bootstrap):
    """Degrade the same way the rest of this codebase does: keep if present."""
    bootstrap([
        (1, None, False),
        (2, "2026-08-28T17:30:00Z", False),
    ])
    assert project.next_n_gameweeks(2, now=NOW) == [1, 2]


def test_defaults_to_wall_clock_when_now_is_not_passed(bootstrap):
    """Production calls it with no `now`; the past must still be excluded."""
    bootstrap([
        (1, "2020-01-01T00:00:00Z", False),
        (2, "2099-01-01T00:00:00Z", False),
    ])
    assert project.next_n_gameweeks(5) == [2]


def test_returns_at_most_n(bootstrap):
    bootstrap([(i, f"2099-0{i}-01T00:00:00Z", False) for i in range(1, 6)])
    assert project.next_n_gameweeks(3, now=NOW) == [1, 2, 3]
