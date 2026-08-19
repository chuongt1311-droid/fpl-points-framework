"""
verify_phase1.py — Phase 1 exit gate (plan §9):

  "One row per player with all needed fields; fixture table with DGW/BGW
   flags correct for a known past DGW."

Part A pulls live bootstrap-static + fixtures and checks the players table.
Part B pulls a real historical season from the vaastav archive, finds an
actual Double Gameweek in it (a team with 2 fixtures in one event — no
hardcoded GW number, since which GW had a DGW varies year to year), and
checks that build_fixtures.flag_dgw_bgw independently reproduces it by
cross-counting raw rows.

Run: python tests/verify_phase1.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpl.collect import fpl_client, history_loader
from fpl.transform import build_fixtures, build_players

REQUIRED_PLAYER_FIELDS = [
    "id", "web_name", "name", "team", "team_name", "position", "price",
    "status", "chance_of_playing_next_round",
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "bonus", "yellow_cards", "red_cards", "saves",
    "form", "total_points", "selected_by_percent",
    "appearances_this_season", "new_signing_flag",
]


def part_a_players_and_live_fixtures() -> bool:
    print("=" * 70)
    print("PART A — live pull: bootstrap-static + fixtures -> players table")
    print("=" * 70)

    cfg = fpl_client.load_config()
    bootstrap = fpl_client.get_bootstrap_static(cfg)
    fixtures = fpl_client.get_fixtures(cfg)
    print(f"  bootstrap-static: {len(bootstrap['elements'])} players, {len(bootstrap['teams'])} teams")
    print(f"  fixtures: {len(fixtures)} fixtures")

    players = build_players.build_players(bootstrap)
    ok = True

    missing_cols = [c for c in REQUIRED_PLAYER_FIELDS if c not in players.columns]
    if missing_cols:
        print(f"  FAIL: players table missing columns: {missing_cols}")
        ok = False
    else:
        print(f"  OK: all {len(REQUIRED_PLAYER_FIELDS)} required fields present")

    n_rows = len(players)
    n_unique_ids = players["id"].nunique()
    if n_rows != n_unique_ids:
        print(f"  FAIL: {n_rows} rows but only {n_unique_ids} unique ids (duplicates)")
        ok = False
    else:
        print(f"  OK: one row per player ({n_rows} players, {n_unique_ids} unique ids)")

    n_expected = len(bootstrap["elements"])
    if n_rows != n_expected:
        print(f"  FAIL: {n_rows} rows built vs {n_expected} elements in bootstrap-static")
        ok = False
    else:
        print(f"  OK: row count matches bootstrap-static element count ({n_rows})")

    critical = ["id", "team", "position", "price", "status"]
    n_null = players[critical].isna().any(axis=1).sum()
    if n_null:
        print(f"  FAIL: {n_null} rows have a null in a critical column {critical}")
        ok = False
    else:
        print(f"  OK: no nulls in critical columns {critical}")

    ftable, grid = build_fixtures.build_fixtures(fixtures)
    print(f"  live fixture table: {len(ftable)} rows ({ftable['fixture_id'].nunique()} fixtures)")
    print(f"  live DGW team-events: {int(grid['is_dgw'].sum())}, BGW team-events: {int(grid['is_bgw'].sum())}"
          f" (GW1 pre-season pull — DGWs/BGWs typically materialise mid-season, so 0/0 here is expected)")

    return ok


def part_b_known_historical_dgw() -> bool:
    print()
    print("=" * 70)
    print("PART B — historical DGW detection check (vaastav archive)")
    print("=" * 70)

    cfg = fpl_client.load_config()
    candidate_seasons = cfg["history"]["seasons"] + ["2021-22"]  # 2021-22 as a fallback known-DGW season

    for season in candidate_seasons:
        print(f"  checking {season} for a real Double Gameweek...")
        tables = history_loader.load_season(season, cfg)
        raw_fixtures = tables.get("fixtures")
        if raw_fixtures is None or raw_fixtures.empty:
            print(f"    skipped ({season} fixtures.csv unavailable)")
            continue

        fixture_dicts = raw_fixtures.to_dict("records")
        ftable = build_fixtures.build_fixture_table(fixture_dicts)
        grid = build_fixtures.flag_dgw_bgw(ftable)

        dgw_rows = grid[grid["is_dgw"]]
        if dgw_rows.empty:
            print(f"    no DGW found in {season}, trying next season")
            continue

        # Independently cross-check the first flagged (event, team) by
        # counting raw rows directly off the untouched source DataFrame.
        event, team = int(dgw_rows.iloc[0]["event"]), int(dgw_rows.iloc[0]["team"])
        raw_count = (
            (raw_fixtures["event"] == event)
            & ((raw_fixtures["team_h"] == team) | (raw_fixtures["team_a"] == team))
        ).sum()
        flagged_count = int(dgw_rows.iloc[0]["fixtures_in_event"])

        print(f"    found: {season} event {event}, team {team} -> "
              f"{len(dgw_rows)} DGW team-events total this season")
        print(f"    cross-check: flag_dgw_bgw says {flagged_count} fixtures; "
              f"independent raw-row count says {raw_count}")

        if flagged_count == raw_count and flagged_count >= 2 and raw_count >= 2:
            print(f"  OK: DGW detection verified against real historical data ({season} GW{event})")
            return True
        else:
            print(f"  FAIL: mismatch between detector and raw count for {season} GW{event}")
            return False

    print("  FAIL: no Double Gameweek found in any candidate season — cannot verify")
    return False


if __name__ == "__main__":
    ok_a = part_a_players_and_live_fixtures()
    ok_b = part_b_known_historical_dgw()

    print()
    print("=" * 70)
    if ok_a and ok_b:
        print("PHASE 1 EXIT GATE: PASS")
        sys.exit(0)
    else:
        print("PHASE 1 EXIT GATE: FAIL")
        sys.exit(1)
