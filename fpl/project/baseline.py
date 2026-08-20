"""
baseline.py — PROJECT layer. Per-90 scoring rates by channel, per current
player, from historical data.

Pure computation over already-collected inputs (data/processed/players.parquet,
data/raw/history/*) — no network I/O. Every cross-season join goes through
fpl.project.identity (see that module's docstring for why `id` can't be used
directly).

SHRINKAGE (FPL_V2_DESIGN.md spec §4.1, replaces the v1 binary confidence
cliff): every player's per-channel rate is now `w * personal + (1-w) * prior`,
w = weighted_minutes / (weighted_minutes + k) — see shrink_rate() below.
This is a continuous version of the old rule ("a player with too little
history gets a prior instead of a personal rate") that improves smoothly as
data accumulates, rather than flipping a hard switch at
config.new_signing.min_historical_minutes. That old threshold still gates
who's allowed to CONTRIBUTE to the tier-prior average (compute_price_tier_priors)
— a different, narrower job than the confidence gate it used to also do.

fpl.evaluate.backtest.py reuses shrink_rate() and the prior-lookup helpers
here directly, so k is tuned against, and RMSE reflects, exactly what
production does — not a separate reimplementation that could silently drift.
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


def shrink_rate(personal: pd.Series, prior: pd.Series, m: pd.Series, k: float) -> pd.Series:
    """
    Empirical-Bayes shrinkage (spec §4.1): rate = w*personal + (1-w)*prior,
    w = m / (m + k). `m` = weighted historical minutes (or, for defcon.py,
    weighted matches — a different unit, hence that module's own k_defcon).

    m=0 (a player with literally no bridged history) makes w=0 exactly, so
    the result must equal `prior` — personal.fillna(0) makes that safe
    without NaN*0 propagating (pandas: NaN * 0 == NaN, not 0).
    """
    m_safe = m.fillna(0)
    w = m_safe / (m_safe + k)
    return w * personal.fillna(0) + (1 - w) * prior.fillna(0)


def shrinkage_weight(m: pd.Series, k: float) -> pd.Series:
    """The w itself — used as the new continuous `confidence` signal."""
    m_safe = m.fillna(0)
    return m_safe / (m_safe + k)


def confidence_label(w: pd.Series, thresholds: dict) -> pd.Series:
    """Derived categorical label from the continuous shrinkage weight, so
    config.optimiser.allow_low_confidence and the dashboard still have a
    label to read — spec §4.1's 'keep a derived categorical label' note."""
    # right=False: bins are [low, high) style — w exactly AT a threshold
    # counts as having reached it ("low: 0.3" means "w < 0.3 is low", not
    # "w <= 0.3 is low"), matching how the config value reads.
    return pd.cut(
        w, bins=[-0.01, thresholds["low"], thresholds["high"], 1.01],
        labels=["low", "medium", "high"], right=False,
    ).astype(str)


def lookup_priors_for_all(
    players_df: pd.DataFrame, tier_priors: pd.DataFrame, position_priors: pd.DataFrame, config: dict,
) -> pd.DataFrame:
    """
    Per-channel prior value for EVERY player (not just thin-history ones —
    shrinkage blends every player against their prior; even a personal rate
    built on a full 3-season sample still gets a nonzero-but-tiny (1-w)
    weight toward it). Falls back tier -> position, same order build_baseline
    always used for the old hard-replace fallback. Vectorized (two merges),
    not a per-row Python loop, since this now runs for the WHOLE player pool
    instead of just the old low-confidence subset.
    """
    price_tier_width = config["history"]["price_tier_width"]
    channel_cols = [f"{c}_per90" for c in PLAYER_CHANNELS]

    out = players_df[["id", "position", "price"]].copy()
    out["price_tier"] = (out["price"] // price_tier_width) * price_tier_width

    tier_renamed = tier_priors.rename(columns={c: f"prior_{c}" for c in channel_cols})
    out = out.merge(tier_renamed, on=["position", "price_tier"], how="left")

    position_renamed = position_priors.rename(columns={c: f"prior_{c}" for c in channel_cols})
    missing = out[f"prior_{channel_cols[0]}"].isna()
    if missing.any():
        fallback = out.loc[missing, ["id", "position"]].merge(
            position_renamed[["position"] + [f"prior_{c}" for c in channel_cols]], on="position", how="left"
        )
        out.loc[missing, [f"prior_{c}" for c in channel_cols]] = fallback[[f"prior_{c}" for c in channel_cols]].values

    return out.drop(columns=["price", "price_tier"])


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
    priors = lookup_priors_for_all(players_df, tier_priors, position_priors, config)

    k = config["shrinkage"]["k"]
    channel_cols = [f"{c}_per90" for c in PLAYER_CHANNELS]

    out = players_df[["id", "code", "web_name", "position", "price", "team", "appearances_this_season"]].merge(
        rates, left_on="id", right_on="current_id", how="left"
    ).merge(priors, on=["id", "position"], how="left")
    out = out.merge(team_baseline, left_on="team", right_on="current_team_id", how="left")

    # Clean-sheet PRIOR is sourced from the player's own team baseline
    # (clean sheets are a team property), not the position/price-tier peer
    # average — more principled per plan §3.3, kept from the pre-shrinkage
    # version but now feeding the blend for every player rather than
    # hard-replacing only low-confidence ones.
    has_team_cs = out["team_cs_rate"].notna()
    out.loc[has_team_cs, "prior_clean_sheets_per90"] = (
        out.loc[has_team_cs, "team_cs_rate"] * 0.75  # rough per-match-played -> per-90 discount
    )

    # SHRINKAGE (spec §4.1): every channel, every player — w*personal +
    # (1-w)*prior, w = weighted_minutes/(weighted_minutes+k). Replaces the
    # old binary confidence cliff outright; there is no more separate
    # "insufficient" branch to have a boundary bug in (dissolves handoff
    # finding #2 — the buggy `&` this used to have doesn't exist to be buggy
    # in any more). The RAW personal rate is kept (renamed *_per90_personal)
    # so the uncorrected number stays inspectable beside the shrunk one
    # (same visibility principle as calibration's D11).
    for col in channel_cols:
        out[f"{col}_personal"] = out[col]
        out[col] = shrink_rate(out[f"{col}_personal"], out[f"prior_{col}"], out["weighted_minutes"], k)

    out["confidence_weight"] = shrinkage_weight(out["weighted_minutes"], k)
    out["confidence"] = confidence_label(out["confidence_weight"], config["shrinkage"]["confidence_thresholds"])

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
