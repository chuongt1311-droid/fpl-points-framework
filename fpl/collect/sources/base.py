"""
base.py — v3 plan §A0: the source adapter contract every external data
source (FPL itself, Understat, the quarantined Sofascore) implements.
Nothing downstream imports a source module directly — everything goes
through this shape, so the identity-mapping layer, the model registry, and
the dashboard's health view can treat every source uniformly.

Three hard rules (plan §A0), enforced by convention here since Python has
no compile-time interface enforcement — every real adapter's own docstring
must restate how it satisfies each one:

  1. A source failure degrades, never crashes. fetch() must catch its own
     network/parse errors and return an EMPTY DataFrame with health().error
     set, not raise. A weekly cron job that dies because a scraped site
     changed its HTML is worthless (plan's own words).
  2. Every source writes its own append-only raw file,
     data/raw/{source}/{season}.csv — never merged in place, never
     overwritten. Same reasoning as fpl/collect/snapshot.py's availability
     history: a bad merge silently destroys data with no way back.
  3. health() is written to model_health.json every run (not yet wired
     into the dashboard's Health view as of this commit — see
     docs/PROJECT_LOG.md for what's built vs what's a follow-up) so a
     silently-degraded source can't quietly make a model something other
     than what it claims to be.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol

import pandas as pd


@dataclass
class SourceHealth:
    source: str
    rows_returned: int
    # None when coverage isn't meaningful yet (e.g. the reference roster to
    # compute coverage against hasn't been joined) — 0.0 would falsely read
    # as "fetched but matched nothing."
    coverage_pct: Optional[float]
    last_success_ts: Optional[str]  # ISO8601, None if this call failed
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "rows_returned": self.rows_returned,
            "coverage_pct": self.coverage_pct,
            "last_success_ts": self.last_success_ts,
            "error": self.error,
        }


class SourceAdapter(Protocol):
    name: str                    # "fpl" | "understat" | "sofascore"
    tier: Literal["core", "enrichment", "quarantined"]

    def fetch(self, *args, **kwargs) -> pd.DataFrame:
        """Raw per-player rows for this source. Must include
        `source_player_id` — the identity bridge (a future fpl/project/
        identity_multi.py, plan §A5) joins on this, never on inferred
        names. MUST NOT raise on a network/parse failure — catch it,
        return an empty DataFrame, and make sure health() reports the
        error afterward (rule 1 above)."""

    def health(self) -> SourceHealth:
        """Health of the most recent fetch() call on this adapter
        instance. Call fetch() first — an adapter that's never been used
        should still return a SourceHealth (rows_returned=0, error=None
        or a 'not yet fetched' note), never raise."""
