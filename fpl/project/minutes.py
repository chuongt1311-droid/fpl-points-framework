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
from fpl.status import UNAVAILABLE_STATUSES
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

    # HANDOFF.md §5 finding #6: build_players.py's own docstring promises
    # "keep if present" for every ELEMENT_COLUMNS entry — it degrades
    # gracefully (drops the column) if FPL ever omits `status` or
    # `chance_of_playing_next_round` from bootstrap-static. A hard
    # `players_df[[...]]` select here would KeyError instead, honouring
    # that contract on the write side only. Built column-by-column with an
    # explicit fallback so a missing column degrades the SAME way a
    # present-but-null value already does per-row below (no chance data ->
    # the rolling-start-rate fallback branch; no status -> nobody is
    # flagged unavailable, the same as every player individually being
    # status='a' today).
    out = players_df[["id", "web_name"]].copy()
    out["status"] = players_df["status"] if "status" in players_df.columns else "a"
    out["chance_of_playing_next_round"] = (
        players_df["chance_of_playing_next_round"]
        if "chance_of_playing_next_round" in players_df.columns
        else float("nan")
    )
    out = out.merge(start_rates, on="id", how="left")

    # IMPORTANT: branch on the RAW chance_of_playing_next_round, not a
    # null->100 substitution. An earlier version pre-substituted null->100
    # per plan §3.1's "no news" note, which made `chance is not None` true
    # for every available player and permanently skipped the `else` branch
    # below — the one that's supposed to separate a nailed starter from a
    # permanent bench player. Caught via testing: it put backup goalkeepers
    # (0 minutes last season) ahead of clear #1s in the xPts eye test,
    # because literally every healthy player got minutes_factor=1.0. FPL
    # only ever populates chance_of_playing for genuine injury/rotation
    # doubt — "no news" for a backup and "no news" for a nailed starter look
    # identical in this field, so §3.1's note is about how to DISPLAY/READ
    # the raw value elsewhere, not a substitution to make before this
    # formula's own branch order runs. The rolling-start-rate `else` branch
    # is where starter-vs-backup actually gets decided.
    chance = out["chance_of_playing_next_round"]

    unavailable = out["status"].isin(UNAVAILABLE_STATUSES)
    has_chance = chance.notna()
    doubtful_no_chance = (out["status"] == "d") & ~has_chance

    factor = pd.Series(float("nan"), index=out.index)
    factor = factor.where(~unavailable, 0.0)
    factor = factor.where(unavailable | ~has_chance, chance / 100.0)
    factor = factor.where(~doubtful_no_chance, 0.5)
    fallback = ~unavailable & ~has_chance & ~doubtful_no_chance
    factor = factor.where(~fallback, out["rolling_start_rate"])

    out["minutes_factor"] = factor
    out = apply_gk_backup_override(out, players_df, config)
    return out[["id", "web_name", "status", "minutes_factor", "rolling_start_rate", "appearances_in_window"]]


def apply_gk_backup_override(minutes_df: pd.DataFrame, players_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Cap minutes_factor for every GK except the presumed #1 at their team —
    see config.minutes.backup_gk_factor's docstring for why. Never RAISES a
    factor, only caps it down, so an already-injured/unavailable "#1" stays
    at 0 rather than being artificially propped up.

    HANDOFF.md §5 finding #3 (fixed): the "#1" used to be picked from a
    STATIC price ranking with no re-check against current availability.
    status-driven zeroing (compute_minutes_factor, above) correctly
    zeroes an injured #1's OWN factor, but never re-promoted anyone —
    the actual new starter at that club (the real backup, now playing)
    wasn't the price-designated #1, so they stayed clipped at
    backup_factor too, even though they'd become the real starter.
    Fixed by ranking each team's GKs on (currently available, price,
    rolling_start_rate) instead of price alone — an unavailable "#1"
    drops out of contention for the #1 slot, so the next keeper up at
    that club is re-promoted into it. "Currently available" is read off
    minutes_df's own minutes_factor (already status-zeroed by the time
    this runs), not re-derived. If every GK at a club is unavailable,
    falls back to plain price ranking for the #1 slot — moot in
    practice, since none of them will start regardless of this cap.
    """
    backup_factor = config["minutes"]["backup_gk_factor"]
    gk_ids = players_df[players_df["position"] == "GK"][["id", "team", "price"]]
    gk = gk_ids.merge(minutes_df[["id", "rolling_start_rate", "minutes_factor"]], on="id", how="left")
    gk["is_available"] = gk["minutes_factor"] > 0

    gk_sorted = gk.sort_values(
        ["team", "is_available", "price", "rolling_start_rate"],
        ascending=[True, False, False, False],
    )
    is_number_one = ~gk_sorted.duplicated(subset="team", keep="first")
    number_one_ids = set(gk_sorted.loc[is_number_one, "id"])

    out = minutes_df.copy()
    is_backup_gk = out["id"].isin(gk_ids["id"]) & ~out["id"].isin(number_one_ids)
    out.loc[is_backup_gk, "minutes_factor"] = out.loc[is_backup_gk, "minutes_factor"].clip(upper=backup_factor)
    return out


if __name__ == "__main__":
    players = build_players.build_players()
    cfg = load_config()
    result = compute_minutes_factor(players, cfg)
    print(f"Computed minutes_factor for {len(result)} players")
    print(f"\nBy status:")
    print(result.groupby("status")["minutes_factor"].agg(["mean", "count"]).round(3))
    print(f"\nUnavailable (factor==0): {(result['minutes_factor'] == 0).sum()}")
    print(result.nsmallest(5, "minutes_factor")[["web_name", "status", "minutes_factor"]].to_string(index=False))
