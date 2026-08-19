"""
build_fixtures.py — TRANSFORM layer. Builds the fixture table plus a
team x gameweek grid flagging Double and Blank Gameweeks.

DGW/BGW detection (plan §5): derived purely by counting fixtures per team
per event — a team with 2 fixtures in one event is a DGW, 0 fixtures in an
event that has fixtures scheduled for other teams is a BGW. Rearranged
fixtures carry `event: null` until scheduled, so this must be re-run every
time fixtures.json is refreshed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"


def _load_fixtures_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_fixture_table(fixtures: list[dict]) -> pd.DataFrame:
    """
    Long format: one row per team per fixture (so a fixture contributes two
    rows — one for each side). Accepts raw FPL API fixture dicts using the
    `team_h`/`team_a` field names (also the vaastav archive's fixtures.csv
    column names, so this same function backs the historical verification
    check in tests/verify_phase1.py).
    """
    rows = []
    for fx in fixtures:
        if fx.get("team_h") is None or fx.get("team_a") is None:
            continue
        event = fx.get("event")
        common = {
            "fixture_id": fx.get("id"),
            "event": event,
            "kickoff_time": fx.get("kickoff_time"),
            "finished": bool(fx.get("finished", False)),
        }
        rows.append({
            **common, "team": fx["team_h"], "opponent": fx["team_a"], "is_home": True,
            "difficulty": fx.get("team_h_difficulty"),
        })
        rows.append({
            **common, "team": fx["team_a"], "opponent": fx["team_h"], "is_home": False,
            "difficulty": fx.get("team_a_difficulty"),
        })
    return pd.DataFrame(rows)


def flag_dgw_bgw(fixture_table: pd.DataFrame, all_team_ids: Optional[list[int]] = None) -> pd.DataFrame:
    """
    Returns a team x event grid: fixtures_in_event, is_dgw, is_bgw.
    Only events with at least one scheduled fixture are included — an event
    nothing has been assigned to yet isn't a real gameweek to flag BGWs against.
    """
    scheduled = fixture_table.dropna(subset=["event"]).copy()
    scheduled["event"] = scheduled["event"].astype(int)

    counts = (
        scheduled.groupby(["event", "team"]).size().rename("fixtures_in_event").reset_index()
    )

    all_teams = all_team_ids or sorted(scheduled["team"].unique().tolist())
    all_events = sorted(scheduled["event"].unique().tolist())
    grid = pd.MultiIndex.from_product([all_events, all_teams], names=["event", "team"]).to_frame(index=False)
    grid = grid.merge(counts, on=["event", "team"], how="left")
    grid["fixtures_in_event"] = grid["fixtures_in_event"].fillna(0).astype(int)
    grid["is_dgw"] = grid["fixtures_in_event"] >= 2
    grid["is_bgw"] = grid["fixtures_in_event"] == 0
    return grid


def build_fixtures(fixtures: Optional[list[dict]] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fixtures is None:
        path = RAW_DIR / "fixtures.json"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found — run `python -m fpl.collect.fpl_client` first."
            )
        fixtures = _load_fixtures_json(path)

    fixture_table = build_fixture_table(fixtures)
    dgw_bgw_grid = flag_dgw_bgw(fixture_table)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    fixture_table.to_parquet(PROCESSED_DIR / "fixtures.parquet", index=False)
    dgw_bgw_grid.to_parquet(PROCESSED_DIR / "dgw_bgw_grid.parquet", index=False)
    return fixture_table, dgw_bgw_grid


if __name__ == "__main__":
    ftable, grid = build_fixtures()
    print(f"Fixture table: {len(ftable)} rows ({ftable['fixture_id'].nunique()} fixtures)")
    print(f"DGW team-events: {int(grid['is_dgw'].sum())}, BGW team-events: {int(grid['is_bgw'].sum())}")
    if grid["is_dgw"].any():
        print(grid[grid["is_dgw"]].to_string(index=False))
