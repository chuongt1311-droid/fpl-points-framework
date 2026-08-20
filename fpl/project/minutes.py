"""
minutes.py — PROJECT layer. Availability / start-probability gate.

minutes_factor(p), exactly as plan §4.2:

    if status in ('i','s','u'):            0.0
    elif chance_of_playing is not None:     chance/100
    elif status == 'd':                     0.5
    else:                                   rolling start rate over last 6 apps

Plan §3.1: chance_of_playing_next_round null means "no news" and is treated
as 100 when status == 'a' — applied here before the branch above runs.
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

ROLLING_WINDOW = 6
# No history at all (brand new to the league, or literally never started a
# match in the pulled window): neither confident nor a guaranteed bench
# player. Documented limitation, not a silent 0 or 1 — see plan §10 limitation 6.
DEFAULT_START_RATE_NO_HISTORY = 0.5


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_rolling_start_rate(players_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Mean of the `starts` flag over each player's last ROLLING_WINDOW
    real-minutes appearances, chronologically across the pulled seasons
    (oldest to newest). Pre-season (no current-season matches yet), this is
    entirely historical — see plan §10 limitation 6: this is backward-
    looking by construction and will lag an in-season role change.
    """
    seasons = config["history"]["seasons"]  # already oldest -> newest
    frames = []
    for order, season in enumerate(seasons):
        path = HIST_DIR / season / "gws" / "merged_gw.csv"
        if not path.exists():
            continue
        gw = pd.read_csv(path, encoding="utf-8")
        gw = gw[gw["minutes"] > 0]
        bridged = identity.attach_current_player_id(gw, season, players_df)
        bridged["season_order"] = order
        frames.append(bridged[["current_id", "season_order", "GW", "starts"]])

    if not frames:
        rates = pd.DataFrame(columns=["id", "rolling_start_rate", "appearances_in_window"])
    else:
        all_hist = pd.concat(frames, ignore_index=True)
        all_hist = all_hist.sort_values(["current_id", "season_order", "GW"])

        def _last_n_start_rate(group: pd.DataFrame) -> pd.Series:
            tail = group.tail(ROLLING_WINDOW)
            return pd.Series({
                "rolling_start_rate": tail["starts"].mean(),
                "appearances_in_window": len(tail),
            })

        rates = all_hist.groupby("current_id").apply(_last_n_start_rate, include_groups=False).reset_index()
        rates = rates.rename(columns={"current_id": "id"})

    out = players_df[["id"]].merge(rates, on="id", how="left")
    out["rolling_start_rate"] = out["rolling_start_rate"].fillna(DEFAULT_START_RATE_NO_HISTORY)
    out["appearances_in_window"] = out["appearances_in_window"].fillna(0).astype(int)
    return out


def compute_minutes_factor(players_df: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    config = config or load_config()
    start_rates = compute_rolling_start_rate(players_df, config)

    out = players_df[["id", "web_name", "status", "chance_of_playing_next_round"]].merge(
        start_rates, on="id", how="left"
    )

    # plan §3.1: null chance_of_playing_next_round means "no news"; treat as
    # 100 when status == 'a', otherwise leave null (status branch handles it).
    chance = out["chance_of_playing_next_round"].copy()
    chance = chance.where(~(chance.isna() & (out["status"] == "a")), 100)

    unavailable = out["status"].isin(["i", "s", "u"])
    has_chance = chance.notna()
    doubtful_no_chance = (out["status"] == "d") & ~has_chance

    factor = pd.Series(float("nan"), index=out.index)
    factor = factor.where(~unavailable, 0.0)
    factor = factor.where(unavailable | ~has_chance, chance / 100.0)
    factor = factor.where(~doubtful_no_chance, 0.5)
    fallback = ~unavailable & ~has_chance & ~doubtful_no_chance
    factor = factor.where(~fallback, out["rolling_start_rate"])

    out["minutes_factor"] = factor
    return out[["id", "web_name", "status", "minutes_factor", "rolling_start_rate", "appearances_in_window"]]


if __name__ == "__main__":
    players = build_players.build_players()
    cfg = load_config()
    result = compute_minutes_factor(players, cfg)
    print(f"Computed minutes_factor for {len(result)} players")
    print(f"\nBy status:")
    print(result.groupby("status")["minutes_factor"].agg(["mean", "count"]).round(3))
    print(f"\nUnavailable (factor==0): {(result['minutes_factor'] == 0).sum()}")
    print(result.nsmallest(5, "minutes_factor")[["web_name", "status", "minutes_factor"]].to_string(index=False))
