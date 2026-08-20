"""
build_players.py — TRANSFORM layer. Turns raw bootstrap-static JSON into one
normalised row per player, ready for the PROJECT layer.

Phase 1 exit gate: one row per player, with id/team/position/price/status
plus every season-to-date channel total the model needs (goals, assists,
clean sheets, bonus, cards, saves, xG/xA where FPL exposes it) and the two
availability fields (status, chance_of_playing_next_round).

IMPORTANT: `id` is NOT stable across seasons — FPL reassigns it (verified:
456 of 461 cross-referenced 2025-26/2026-27 players have a different `id`
between seasons). `code` IS stable and must be the join key for anything
that maps a current player to their prior-season history (fpl/project/).

The full v1 new-signing rule (plan §3.3 — team/position/price-tier priors,
`confidence: low` flag) is applied in fpl/project once prior-season history
is joined in; this layer only computes the appearance count the rule keys off.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"

POSITION_MAP = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# Columns pulled straight from bootstrap-static `elements`, kept if present —
# the API adds/renames fields season to season, so we filter to what's there
# rather than fail on a missing one.
ELEMENT_COLUMNS = [
    "id", "code", "web_name", "first_name", "second_name", "team", "element_type",
    "now_cost", "status", "news",
    "chance_of_playing_next_round", "chance_of_playing_this_round",
    "minutes", "starts",
    "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed",
    "yellow_cards", "red_cards", "saves", "bonus", "bps",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded",
    "form", "points_per_game", "total_points", "selected_by_percent",
    "ep_next", "ep_this",
]

TEAM_COLUMNS = [
    "id", "name", "short_name",
    "strength_overall_home", "strength_overall_away",
    "strength_attack_home", "strength_attack_away",
    "strength_defence_home", "strength_defence_away",
]


def _load_bootstrap() -> dict:
    path = RAW_DIR / "bootstrap_static.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m fpl.collect.fpl_client` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def build_players(bootstrap: Optional[dict] = None) -> pd.DataFrame:
    bootstrap = bootstrap or _load_bootstrap()

    elements_cols = [c for c in ELEMENT_COLUMNS if c in bootstrap["elements"][0]]
    elements = pd.DataFrame(bootstrap["elements"])[elements_cols].copy()

    team_cols = [c for c in TEAM_COLUMNS if c in bootstrap["teams"][0]]
    teams = pd.DataFrame(bootstrap["teams"])[team_cols].set_index("id")

    df = elements.copy()
    df["position"] = df["element_type"].map(POSITION_MAP)
    df["name"] = (df["first_name"].fillna("") + " " + df["second_name"].fillna("")).str.strip()
    df["price"] = df["now_cost"] / 10.0

    team_field_map = {
        "name": "team_name",
        "short_name": "team_short_name",
        "strength_overall_home": "team_strength_overall_home",
        "strength_overall_away": "team_strength_overall_away",
        "strength_attack_home": "team_strength_attack_home",
        "strength_attack_away": "team_strength_attack_away",
        "strength_defence_home": "team_strength_defence_home",
        "strength_defence_away": "team_strength_defence_away",
    }
    for src_col, dst_col in team_field_map.items():
        if src_col in teams.columns:
            df[dst_col] = df["team"].map(teams[src_col])

    # v1 new-signing rule input (plan §3.3): appearance count this season.
    # `starts` is the accurate proxy where the API exposes it; otherwise fall
    # back to a minutes-based estimate.
    #
    # VERIFIED QUIRK: bootstrap-static's per-element season-cumulative fields
    # (starts/minutes/goals_scored/...) do NOT reset to 0 as soon as a new
    # season's `events` list appears — they carry over the previous season's
    # final totals until real matches start. Confirmed directly: pulled
    # pre-GW1 with events[0] (Gameweek 1) showing finished=False, is_next=True
    # (deadline still in the future), yet some players already showed
    # starts=3+/minutes>0. Trusting that field here would silently promote
    # players to confidence='high' off a stale prior season, not real
    # current-season form. So: only trust these fields once at least one
    # event has actually finished — until then, force 0. Self-corrects
    # automatically as the season progresses and events start finishing.
    season_has_started = any(e.get("finished") for e in bootstrap.get("events", []))
    if season_has_started and "starts" in df.columns:
        df["appearances_this_season"] = df["starts"].fillna(0).astype(int)
    elif season_has_started:
        df["appearances_this_season"] = (df["minutes"].fillna(0) // 90).astype(int)
    else:
        df["appearances_this_season"] = 0
    df["new_signing_flag"] = df["appearances_this_season"] < 3

    df = df.drop(columns=["first_name", "second_name", "element_type", "now_cost"])

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PROCESSED_DIR / "players.parquet", index=False)
    return df


if __name__ == "__main__":
    players = build_players()
    print(f"Built players table: {len(players)} rows, {len(players.columns)} columns")
    print(players[["web_name", "position", "price", "status"]].head(10).to_string(index=False))
