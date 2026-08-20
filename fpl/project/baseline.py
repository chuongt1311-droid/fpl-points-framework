"""
baseline.py — PROJECT layer. Per-90 scoring rates by channel, per current
player, from historical data.

Pure computation over already-collected inputs (data/processed/players.parquet,
data/raw/history/*) — no network I/O. Every cross-season join goes through
fpl.project.identity (see that module's docstring for why `id` can't be used
directly).

v1 new-signing rule (plan §3.3, locked): a player with fewer than
config.new_signing.min_appearances appearances this season AND no bridged
prior-season history gets confidence='low' and a team/position/price-tier
prior instead of a personal rate — never a confidently-wrong personal number
built on near-zero data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.project import identity
from fpl.transform import build_players

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
HIST_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "history"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"

PLAYER_CHANNELS = [
    "goals_scored", "assists", "clean_sheets", "bonus", "saves",
    "goals_conceded", "yellow_cards", "red_cards",
]


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _season_weight(seasons: list[str], season: str, decay: float) -> float:
    seasons_ago = len(seasons) - 1 - seasons.index(season)
    return decay ** seasons_ago


def load_weighted_player_history(players_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    One row per (season, current player id, gameweek) for every player who
    exists in the current squad AND has bridged history in that season, with
    a `weight` column (recency, per _season_weight).
    """
    seasons = config["history"]["seasons"]
    decay = config["history"]["recency_decay"]

    frames = []
    for season in seasons:
        path = HIST_DIR / season / "gws" / "merged_gw.csv"
        if not path.exists():
            continue
        gw = pd.read_csv(path, encoding="utf-8")
        bridged = identity.attach_current_player_id(gw, season, players_df)
        bridged["season"] = season
        bridged["weight"] = _season_weight(seasons, season, decay)
        frames.append(bridged)

    if not frames:
        raise FileNotFoundError(f"No historical seasons found under {HIST_DIR}")
    return pd.concat(frames, ignore_index=True)


def compute_player_rates(history: pd.DataFrame) -> pd.DataFrame:
    """
    Recency-weighted per-90 rate for each channel, per current player id.
    weighted_rate = sum(weight * stat) / sum(weight * minutes / 90)
    Also carries unweighted historical_matches/historical_minutes, which
    gate the new-signing confidence flag downstream.
    """
    played = history[history["minutes"] > 0].copy()
    played["weighted_minutes"] = played["weight"] * played["minutes"]

    grouped = played.groupby("current_id")

    out = pd.DataFrame({
        "current_id": grouped.size().index,
        "historical_matches": grouped.size().values,
        "historical_minutes": grouped["minutes"].sum().values,
        "weighted_minutes": grouped["weighted_minutes"].sum().values,
    })

    for channel in PLAYER_CHANNELS:
        weighted_sum = played.assign(_w=played["weight"] * played[channel]).groupby("current_id")["_w"].sum()
        out = out.merge(weighted_sum.rename(f"{channel}_weighted_sum"), on="current_id", how="left")

    for channel in PLAYER_CHANNELS:
        out[f"{channel}_per90"] = (out[f"{channel}_weighted_sum"] * 90 / out["weighted_minutes"]).where(
            out["weighted_minutes"] > 0
        )
        out = out.drop(columns=[f"{channel}_weighted_sum"])

    return out


def compute_price_tier_priors(players_df: pd.DataFrame, rates: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Mean per-90 rate by (position, price tier) among players who DO have
    personal history — the fallback for new signings, per plan §3.3.
    """
    tier_width = config["history"]["price_tier_width"]
    joined = players_df[["id", "position", "price"]].merge(
        rates, left_on="id", right_on="current_id", how="inner"
    )
    joined = joined[joined["historical_minutes"] >= 450]  # same qualification floor as Phase 2
    joined["price_tier"] = (joined["price"] // tier_width) * tier_width

    channel_cols = [f"{c}_per90" for c in PLAYER_CHANNELS]
    tier_priors = joined.groupby(["position", "price_tier"])[channel_cols].mean().reset_index()
    position_priors = joined.groupby("position")[channel_cols].mean().reset_index()
    position_priors["price_tier"] = None
    return tier_priors, position_priors


def build_team_baseline(config: dict) -> pd.DataFrame:
    """
    Recency-weighted clean-sheet rate and goals-conceded/scored per-90, per
    CURRENT team id, from historical fixtures.csv results. Teams with no
    bridged history (newly promoted — Coventry/Hull/Ipswich for 2026-27)
    simply get no row here; fpl/project/fixtures.py falls back to
    bootstrap-static strength ratings alone for them, which is the primary
    signal per plan §4.2 anyway.
    """
    seasons = config["history"]["seasons"]
    decay = config["history"]["recency_decay"]

    rows = []
    for season in seasons:
        path = HIST_DIR / season / "fixtures.csv"
        if not path.exists():
            continue
        fx = pd.read_csv(path, encoding="utf-8")
        fx = fx[fx["finished"] == True].copy()  # noqa: E712 — pandas bool column
        weight = _season_weight(seasons, season, decay)

        for side, own_col, opp_col in [("team_h", "team_h_score", "team_a_score"), ("team_a", "team_a_score", "team_h_score")]:
            side_df = fx[["id", side, own_col, opp_col]].rename(
                columns={side: "team", own_col: "goals_for", opp_col: "goals_against"}
            )
            side_df["clean_sheet"] = (side_df["goals_against"] == 0).astype(int)
            side_df["weight"] = weight
            bridged = identity.attach_current_team_id(side_df, season, team_col="team")
            rows.append(bridged)

    if not rows:
        return pd.DataFrame(columns=["current_team_id", "team_cs_rate", "team_goals_conceded_per90", "team_goals_scored_per90"])

    all_rows = pd.concat(rows, ignore_index=True)
    grouped = all_rows.groupby("current_team_id")
    out = pd.DataFrame({
        "current_team_id": grouped.size().index,
        "team_matches": grouped.size().values,
        "team_cs_rate": (grouped.apply(lambda g: (g["weight"] * g["clean_sheet"]).sum() / g["weight"].sum(), include_groups=False)).values,
        "team_goals_conceded_per90": (grouped.apply(lambda g: (g["weight"] * g["goals_against"]).sum() / g["weight"].sum(), include_groups=False)).values,
        "team_goals_scored_per90": (grouped.apply(lambda g: (g["weight"] * g["goals_for"]).sum() / g["weight"].sum(), include_groups=False)).values,
    })
    return out


def build_baseline(players_df: Optional[pd.DataFrame] = None, config: Optional[dict] = None) -> pd.DataFrame:
    config = config or load_config()
    players_df = players_df if players_df is not None else build_players.build_players()

    history = load_weighted_player_history(players_df, config)
    rates = compute_player_rates(history)
    tier_priors, position_priors = compute_price_tier_priors(players_df, rates, config)
    team_baseline = build_team_baseline(config)

    min_appearances = config["new_signing"]["min_appearances"]
    min_historical_minutes = config["new_signing"]["min_historical_minutes"]
    out = players_df[["id", "code", "web_name", "position", "price", "team", "appearances_this_season"]].merge(
        rates, left_on="id", right_on="current_id", how="left"
    )
    out["confidence"] = "high"
    # Insufficient = not enough current-season minutes to trust current-season
    # form on its own, AND not enough historical minutes to trust the
    # personal rate either — "some data" isn't "enough data" (a 1-minute
    # cameo last season is not a basis for a personal per-90 rate).
    thin_history = out["historical_minutes"].isna() | (out["historical_minutes"] < min_historical_minutes)
    insufficient = (out["appearances_this_season"] < min_appearances) & thin_history
    out.loc[insufficient, "confidence"] = "low"

    price_tier_width = config["history"]["price_tier_width"]
    out["price_tier"] = (out["price"] // price_tier_width) * price_tier_width

    channel_cols = [f"{c}_per90" for c in PLAYER_CHANNELS]
    low_conf_idx = out[out["confidence"] == "low"].index
    for idx in low_conf_idx:
        pos = out.loc[idx, "position"]
        tier = out.loc[idx, "price_tier"]
        match = tier_priors[(tier_priors["position"] == pos) & (tier_priors["price_tier"] == tier)]
        if match.empty:
            match = position_priors[position_priors["position"] == pos]
        if not match.empty:
            for col in channel_cols:
                out.loc[idx, col] = match.iloc[0][col]

    out = out.merge(team_baseline, left_on="team", right_on="current_team_id", how="left")

    # Clean-sheet channel for low-confidence players is sourced from their
    # OWN team's baseline (clean sheets are a team property), not the
    # position/price-tier peer average — more principled per plan §3.3.
    has_team_cs = out["team_cs_rate"].notna()
    out.loc[low_conf_idx.intersection(out[has_team_cs].index), "clean_sheets_per90"] = out.loc[
        low_conf_idx.intersection(out[has_team_cs].index), "team_cs_rate"
    ] * 0.75  # rough per-match-played -> per-90 discount; refined by minutes_factor downstream

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PROCESSED_DIR / "baseline.parquet", index=False)
    return out


if __name__ == "__main__":
    baseline = build_baseline()
    print(f"Baseline built: {len(baseline)} players")
    print(baseline["confidence"].value_counts())
    print("\nSample high-confidence rows:")
    print(
        baseline[baseline["confidence"] == "high"]
        .nlargest(10, "goals_scored_per90")[["web_name", "position", "goals_scored_per90", "assists_per90", "clean_sheets_per90"]]
        .to_string(index=False)
    )
