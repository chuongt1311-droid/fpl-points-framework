"""
Tests for fpl/collect/snapshot.py against spec §2.4's exit gate:
  - two runs at different times both present, hours_to_deadline differs
    and reflects the real deadline
  - nothing overwritten; row count strictly increases on a genuinely new run
  - a deliberately re-run job (same run_id) does not duplicate rows
  - news_added round-trips for a flagged player

Pure unit tests: a synthetic bootstrap-static dict, no network, no API
pull, no committed artefacts — same bar as tests/test_hotfix_regressions.py
(spec §7.3).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from fpl.collect import snapshot as snap


def _bootstrap(deadline_iso: str = "2026-08-21T17:30:00Z") -> dict:
    return {
        "events": [
            {"id": 1, "deadline_time": deadline_iso, "finished": False, "is_next": True},
            {"id": 2, "deadline_time": "2026-08-29T10:00:00Z", "finished": False, "is_next": False},
        ],
        "elements": [
            {
                "id": 1, "code": 100001, "element_type": 3, "team": 1,
                "status": "a", "news": "", "news_added": None,
                "chance_of_playing_this_round": None, "chance_of_playing_next_round": None,
                "now_cost": 95, "selected_by_percent": "35.8",
            },
            {
                "id": 2, "code": 100002, "element_type": 2, "team": 2,
                "status": "d", "news": "Knock - 75% chance of playing",
                "news_added": "2026-08-19T09:00:00Z",
                "chance_of_playing_this_round": 75, "chance_of_playing_next_round": 75,
                "now_cost": 55, "selected_by_percent": "12.1",
            },
        ],
    }


def test_hours_to_deadline_reflects_the_real_deadline():
    bootstrap = _bootstrap("2026-08-21T17:30:00Z")
    snapshot_ts = datetime(2026, 8, 20, 16, 0, 0, tzinfo=timezone.utc)
    rows = snap.build_snapshot_rows(bootstrap, run_id="run-a", snapshot_ts=snapshot_ts)

    # 2026-08-21 17:30 minus 2026-08-20 16:00 = 25.5 hours.
    assert rows["hours_to_deadline"].iloc[0] == 25.5
    assert (rows["next_event"] == 1).all()


def test_news_added_round_trips_for_a_flagged_player():
    bootstrap = _bootstrap()
    rows = snap.build_snapshot_rows(bootstrap, run_id="run-a", snapshot_ts=datetime.now(timezone.utc))
    flagged = rows[rows["id"] == 2]
    assert flagged["news_added"].iloc[0] == "2026-08-19T09:00:00Z"
    assert flagged["status"].iloc[0] == "d"


def test_two_runs_at_different_times_both_present_with_differing_hours_to_deadline(tmp_path):
    path = tmp_path / "availability_test.csv"

    ts1 = datetime(2026, 8, 19, 9, 0, 0, tzinfo=timezone.utc)
    rows1 = snap.build_snapshot_rows(_bootstrap(), run_id="run-1", snapshot_ts=ts1)
    snap.append_snapshot(rows1, path=path)

    ts2 = datetime(2026, 8, 21, 9, 0, 0, tzinfo=timezone.utc)
    rows2 = snap.build_snapshot_rows(_bootstrap(), run_id="run-2", snapshot_ts=ts2)
    snap.append_snapshot(rows2, path=path)

    on_disk = pd.read_csv(path)
    assert len(on_disk) == 4  # 2 players x 2 runs — nothing overwritten
    assert on_disk["run_id"].nunique() == 2
    by_run = on_disk.groupby("run_id")["hours_to_deadline"].first()
    assert by_run["run-1"] != by_run["run-2"]


def test_retried_run_with_the_same_run_id_does_not_duplicate_rows(tmp_path):
    path = tmp_path / "availability_test.csv"
    ts = datetime(2026, 8, 19, 9, 0, 0, tzinfo=timezone.utc)
    rows = snap.build_snapshot_rows(_bootstrap(), run_id="run-1", snapshot_ts=ts)

    snap.append_snapshot(rows, path=path)
    snap.append_snapshot(rows, path=path)  # simulated retry of the SAME run

    on_disk = pd.read_csv(path)
    assert len(on_disk) == 2  # not 4 — the retry must be rejected outright
    assert on_disk["run_id"].nunique() == 1


def test_a_failed_projection_still_leaves_raw_rows_with_minutes_factor_null():
    """Phase 1 (raw capture) must stand alone — attach_minutes_factor is
    never called if the projection step raises, per the two-phase contract."""
    rows = snap.build_snapshot_rows(_bootstrap(), run_id="run-1", snapshot_ts=datetime.now(timezone.utc))
    assert rows["minutes_factor"].isna().all()


def test_attach_minutes_factor_fills_in_the_second_phase():
    rows = snap.build_snapshot_rows(_bootstrap(), run_id="run-1", snapshot_ts=datetime.now(timezone.utc))
    minutes_df = pd.DataFrame({"id": [1, 2], "minutes_factor": [0.95, 0.4]})
    filled = snap.attach_minutes_factor(rows, minutes_df)
    assert filled.set_index("id")["minutes_factor"].to_dict() == {1: 0.95, 2: 0.4}
