"""
identity.py — PROJECT layer support. Bridges current-season player/team ids
to prior-season history via FPL's stable `code` field.

Not in the plan's original file list (§11 names baseline/fixtures/minutes/
defcon/project only) — added because this join logic is needed identically
by three of those modules and is exactly the kind of subtle-bug-magnet the
plan's principle 3 ("separate concerns so bugs are diagnosable") argues
against duplicating.

CRITICAL, VERIFIED FINDING — FPL's `id` field is NOT stable across seasons;
`code` is:
  - Players: 456 of 461 cross-referenced 2025-26/2026-27 players have a
    DIFFERENT `id` between seasons (code always matches by web_name — 100%).
  - Teams: 12 of 17 cross-referenced 2025-26/2026-27 teams have a DIFFERENT
    `id` between seasons. Only 17 carry over at all: Burnley/West Ham/Wolves
    relegated, Coventry City/Hull City/Ipswich Town promoted for 2026-27.

Every cross-season join anywhere in fpl/project MUST go through this
module's helpers — never join raw history directly on id/element/team.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
HIST_DIR = RAW_DIR / "history"


def current_player_code_map(players_df: pd.DataFrame) -> pd.DataFrame:
    """id, code for every player in the current processed players table."""
    return players_df[["id", "code"]].drop_duplicates().reset_index(drop=True)


def current_team_code_map() -> pd.DataFrame:
    """id, code, name for every team in the current bootstrap-static pull."""
    import json
    path = RAW_DIR / "bootstrap_static.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run fpl.collect.fpl_client first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(data["teams"])[["id", "code", "name"]]


def _players_raw(season: str) -> Optional[pd.DataFrame]:
    path = HIST_DIR / season / "players_raw.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, encoding="utf-8")[["id", "code"]]


def _teams_raw(season: str) -> Optional[pd.DataFrame]:
    path = HIST_DIR / season / "teams.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, encoding="utf-8")[["id", "code", "name"]]


def attach_current_player_id(gw_df: pd.DataFrame, season: str, current_players: pd.DataFrame) -> pd.DataFrame:
    """
    Join a season's merged_gw.csv (keyed by `element`, that season's id) onto
    the CURRENT season's player `id`, via `code`. Rows for players with no
    current-season match (retired, left the league, etc.) are dropped.
    """
    players_raw = _players_raw(season)
    if players_raw is None:
        raise FileNotFoundError(f"players_raw.csv missing for {season} in {HIST_DIR}")

    # Same defensive rename as attach_current_team_id — players_raw's `code`
    # shouldn't collide with anything in gw_df today, but don't rely on that.
    players_bridge = players_raw.rename(columns={"id": "element", "code": "_player_code"})
    bridge = gw_df.merge(players_bridge, on="element", how="inner")

    cur_map = current_player_code_map(current_players).rename(
        columns={"id": "current_id", "code": "_player_code"}
    )
    bridge = bridge.merge(cur_map, on="_player_code", how="inner")
    bridge = bridge.rename(columns={"_player_code": "code"})
    return bridge


def attach_current_team_id(df: pd.DataFrame, season: str, team_col: str = "team") -> pd.DataFrame:
    """
    Join a season's team-indexed data (fixtures.csv uses that season's team
    id in `team_col`) onto the CURRENT season's team `id`, via `code`. Rows
    for teams with no current-season match (relegated out of the league)
    are dropped — by construction, this also correctly excludes newly
    promoted teams from historical-season joins for seasons before they
    were in the top flight, since they simply never appear in older
    teams.csv files in the first place.
    """
    teams_raw = _teams_raw(season)
    if teams_raw is None:
        raise FileNotFoundError(f"teams.csv missing for {season} in {HIST_DIR}")

    # teams_raw's own `code`/`name` columns could collide with columns
    # already on `df` (e.g. fixtures.csv has its own unrelated `code` for
    # the fixture itself) — rename to unique names before merging so a
    # collision can't silently pandas-suffix the key away.
    teams_bridge = teams_raw.rename(columns={"id": team_col, "code": "_team_code", "name": "_team_name"})
    bridge = df.merge(teams_bridge, on=team_col, how="inner")

    cur_map = current_team_code_map().rename(
        columns={"id": "current_team_id", "code": "_team_code", "name": "current_team_name"}
    )
    bridge = bridge.merge(cur_map, on="_team_code", how="inner")
    bridge = bridge.drop(columns=["_team_code", "_team_name"])
    return bridge


if __name__ == "__main__":
    from fpl.transform import build_players

    players = build_players.build_players()
    cur_teams = current_team_code_map()
    print(f"Current players with code: {len(current_player_code_map(players))}")
    print(f"Current teams with code: {len(cur_teams)}")
    print(cur_teams.to_string(index=False))
