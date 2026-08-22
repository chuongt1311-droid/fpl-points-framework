"""
Tests for fpl/status.py — HANDOFF.md §5 finding #10: the unavailable-
status set used to be hardcoded identically in three files with no
shared constant. This is the single source of truth now; these tests
also double as regression coverage for the three call sites that
import it (fpl/project/minutes.py, fpl/project/project.py,
fpl/decide/optimiser.py all just call is_unavailable / check membership
in UNAVAILABLE_STATUSES, so correctness here covers all three).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl import status


def test_unavailable_statuses_are_exactly_injured_suspended_unavailable():
    assert status.UNAVAILABLE_STATUSES == frozenset({"i", "s", "u"})


def test_is_unavailable_flags_only_the_hard_exclusions():
    s = pd.Series(["a", "d", "i", "s", "u", "n"])
    result = status.is_unavailable(s)
    assert result.tolist() == [False, False, True, True, True, False]


def test_doubtful_is_not_treated_as_unavailable():
    """'d' (doubtful) is a soft signal handled separately by minutes_factor
    — it must never be swept into the hard-exclusion set."""
    assert "d" not in status.UNAVAILABLE_STATUSES
