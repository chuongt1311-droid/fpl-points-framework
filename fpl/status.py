"""
status.py — the FPL player-availability status codes, in one place.

HANDOFF.md §5 finding #10: the "definitely unavailable" status set was
hardcoded identically as `["i", "s", "u"]` in three separate files
(fpl/project/minutes.py, fpl/project/project.py, fpl/decide/optimiser.py)
with no shared constant. Checked against real bootstrap-static data: no
additional real status value is currently missed by this list — the
duplication itself was the risk (three places to update, silently, if
FPL ever adds a code), not a missing status today.

FPL's `status` field (from bootstrap-static `elements[].status`) is one
of:
    "a" — available
    "d" — doubtful (may play, availability uncertain — NOT unavailable)
    "i" — injured
    "s" — suspended
    "u" — unavailable (left the club / not in the squad, e.g. released,
          on loan elsewhere, retired)
    "n" — not available for selection this specific gameweek (rare;
          treated as available here, same as the pre-existing behaviour
          at all three call sites this replaces — not a scope change)
"""
from __future__ import annotations

import pandas as pd

# "Definitely can't play" — as opposed to "d" (doubtful), which is a
# real availability signal (see fpl/project/minutes.py's minutes_factor)
# but not a hard exclusion the way injured/suspended/unavailable are.
UNAVAILABLE_STATUSES = frozenset({"i", "s", "u"})


def is_unavailable(status: pd.Series) -> pd.Series:
    """Boolean mask — True where a player is definitely unavailable
    (injured/suspended/left the club), never a soft signal like doubtful."""
    return status.isin(UNAVAILABLE_STATUSES)
