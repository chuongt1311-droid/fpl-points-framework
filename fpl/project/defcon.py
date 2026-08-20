"""
defcon.py — PROJECT layer. P(hits DEFCON threshold) for DEF/MID/FWD.

Uses the rate-based estimator validated against real data in Phase 2
(notebooks/01_profile_research.ipynb §4): matches_over_threshold /
matches_played, NOT an average-action-count proxy — the notebook showed the
average-based proxy overestimates the true rate by 0.36 on average.

Only 2025-26 has DEFCON scoring (config.defcon.baseline_season_only) — this
module does not and must not blend in earlier seasons for this channel.
Same small-sample lesson as baseline.py (min match-count floor before a
personal rate is trusted) and same position/price-tier prior fallback for
players without enough personal history — code-bridged per fpl.project.identity.
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

MIN_MATCHES_FOR_PERSONAL_RATE = 10  # same floor used in the Phase 2 notebook


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_defcon_rates(players_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    season = config["defcon"]["baseline_season_only"]
    thresholds = {
        "DEF": config["defcon"]["def_threshold_cbit"],
        "MID": config["defcon"]["mid_fwd_threshold_cbirt"],
        "FWD": config["defcon"]["mid_fwd_threshold_cbirt"],
    }

    path = HIST_DIR / season / "gws" / "merged_gw.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run fpl.collect.history_loader first.")
    gw = pd.read_csv(path, encoding="utf-8")
    gw = gw[gw["minutes"] > 0].copy()
    bridged = identity.attach_current_player_id(gw, season, players_df)
    # gw's own `position` is that season's tag — use the CURRENT position for
    # the threshold instead (drop first so the merge below can't silently
    # suffix it to position_x). Approximation: a rare position swap between
    # DEF and MID/FWD would mean the historical defensive_contribution value
    # was computed by FPL under the OLD position's CBIT/CBIRT formula, so
    # this re-applies a threshold that may not exactly match how the raw
    # number was built. Cheap to fix properly (thresholds per historical
    # position instead), left as a documented simplification for v1.
    bridged = bridged.drop(columns=["position"])
    bridged = bridged.merge(
        players_df[["id", "position"]].rename(columns={"id": "current_id"}), on="current_id", how="left"
    )
    bridged["threshold"] = bridged["position"].map(thresholds)
    bridged = bridged.dropna(subset=["threshold"])  # GK excluded — no DEFCON scoring
    bridged["over_threshold"] = bridged["defensive_contribution"] >= bridged["threshold"]

    personal = bridged.groupby("current_id", as_index=False).agg(
        defcon_matches_played=("minutes", "size"),
        defcon_matches_over=("over_threshold", "sum"),
    )
    personal["defcon_rate_personal"] = personal["defcon_matches_over"] / personal["defcon_matches_played"]
    return personal.rename(columns={"current_id": "id"})


def compute_price_tier_defcon_priors(players_df: pd.DataFrame, personal: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    tier_width = config["history"]["price_tier_width"]
    joined = players_df[["id", "position", "price"]].merge(personal, on="id", how="inner")
    joined = joined[joined["defcon_matches_played"] >= MIN_MATCHES_FOR_PERSONAL_RATE]
    joined["price_tier"] = (joined["price"] // tier_width) * tier_width

    tier_priors = joined.groupby(["position", "price_tier"])["defcon_rate_personal"].mean().reset_index()
    tier_priors = tier_priors.rename(columns={"defcon_rate_personal": "defcon_rate_tier_prior"})
    position_priors = joined.groupby("position")["defcon_rate_personal"].mean().reset_index()
    position_priors = position_priors.rename(columns={"defcon_rate_personal": "defcon_rate_tier_prior"})
    position_priors["price_tier"] = None
    return tier_priors, position_priors


def build_defcon(players_df: Optional[pd.DataFrame] = None, config: Optional[dict] = None) -> pd.DataFrame:
    config = config or load_config()
    players_df = players_df if players_df is not None else build_players.build_players()

    personal = compute_defcon_rates(players_df, config)
    tier_priors, position_priors = compute_price_tier_defcon_priors(players_df, personal, config)

    tier_width = config["history"]["price_tier_width"]
    out = players_df[["id", "web_name", "position", "price"]].merge(personal, on="id", how="left")
    out["price_tier"] = (out["price"] // tier_width) * tier_width

    enough_matches = out["defcon_matches_played"].fillna(0) >= MIN_MATCHES_FOR_PERSONAL_RATE
    out["defcon_rate"] = out["defcon_rate_personal"].where(enough_matches)
    out["defcon_source"] = "personal"
    out.loc[~enough_matches, "defcon_source"] = "tier_prior"

    for idx in out[~enough_matches].index:
        pos = out.loc[idx, "position"]
        if pos == "GK":
            out.loc[idx, "defcon_rate"] = 0.0
            out.loc[idx, "defcon_source"] = "not_applicable"
            continue
        tier = out.loc[idx, "price_tier"]
        match = tier_priors[(tier_priors["position"] == pos) & (tier_priors["price_tier"] == tier)]
        if match.empty:
            match = position_priors[position_priors["position"] == pos]
        out.loc[idx, "defcon_rate"] = match.iloc[0]["defcon_rate_tier_prior"] if not match.empty else 0.0

    out.loc[out["position"] == "GK", ["defcon_rate", "defcon_source"]] = [0.0, "not_applicable"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PROCESSED_DIR / "defcon.parquet", index=False)
    return out


if __name__ == "__main__":
    result = build_defcon()
    print(f"Built defcon rates for {len(result)} players")
    print(result["defcon_source"].value_counts())
    print("\nTop 10 DEF by defcon_rate:")
    print(result[result["position"] == "DEF"].nlargest(10, "defcon_rate")[["web_name", "defcon_rate", "defcon_source", "defcon_matches_played"]].to_string(index=False))
    print("\nTop 10 MID by defcon_rate:")
    print(result[result["position"] == "MID"].nlargest(10, "defcon_rate")[["web_name", "defcon_rate", "defcon_source", "defcon_matches_played"]].to_string(index=False))
