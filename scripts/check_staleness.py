# scripts/check_staleness.py — v4 plan §3.3 (Tier 0.3).
#
# "A pipeline that can tell you it's been dead is worth more than one that
# can't." A failed Actions run is an email you learn to ignore; this makes
# staleness loud and visible in the run log itself (and, wired into
# weekly.yml, fails the job) instead of silent for weeks. Checked against
# the availability snapshot specifically because it's the append-only,
# irreversible artefact this project cares most about not silently losing —
# see docs/FPL_V4_PLAN.md §3.3.
import sys
import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_GLOB = "data/snapshots/availability_*.csv"
MAX_AGE_DAYS = 4


def newest_snapshot_ts():
    files = sorted(REPO_ROOT.glob(SNAPSHOT_GLOB))
    if not files:
        return None, None
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["snapshot_ts"])
        except (ValueError, pd.errors.EmptyDataError):
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return None, None
    all_ts = pd.concat(frames)["snapshot_ts"]
    newest = pd.to_datetime(all_ts, utc=True).max()
    return newest, files


def main():
    newest, files = newest_snapshot_ts()
    if newest is None:
        print(
            f"check_staleness.py: no rows found under {SNAPSHOT_GLOB} — "
            "either this is the very first run (expected) or snapshot.py "
            "silently failed to write anything (not expected)."
        )
        # Don't hard-fail on a genuinely empty first run; this is a signal
        # to look, not an automatic red X, since it's indistinguishable
        # from "brand new repo" from here.
        return 0

    now = pd.Timestamp.now(tz="UTC")
    age = now - newest
    age_days = age.total_seconds() / 86400
    print(
        f"check_staleness.py: newest availability snapshot row is "
        f"{newest.isoformat()} ({age_days:.2f} days old), across "
        f"{len(files)} file(s) matching {SNAPSHOT_GLOB}."
    )
    if age_days > MAX_AGE_DAYS:
        print(
            f"STALE: newest snapshot row is {age_days:.2f} days old, "
            f"exceeds the {MAX_AGE_DAYS}-day threshold. The weekly workflow "
            "has likely stopped running (or snapshot.py has started "
            "failing silently) — investigate before trusting any "
            "downstream recommendation.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
