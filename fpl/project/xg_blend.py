"""
xg_blend.py — PROJECT layer. v3 plan §B2 (M2 model): blends the shrunk
personal goal/assist rate (baseline.py's goals_scored_per90/assists_per90 —
already M1-equivalent, shrinkage-blended toward the position/price-tier
prior) with a shrunk xG90/xA90 rate, per plan §B2:

    attacking_rate = v * xG90 + (1 - v) * G90,  v declines with minutes

NOT a simple goals->xG replacement. The plan's own reasoning: at LOW
minutes, xG is the LOWER-variance estimator of the same underlying skill —
shots vastly outnumber goals (a striker with 400 mins/3 goals has n=3 goals
but n~18 shots), so trust xG MORE early and let it fade as personal goal
history accumulates. `v` therefore uses the SAME functional form as
shrinkage_weight but the OPPOSITE direction: v = k_xg / (m + k_xg) DECREASES
as weighted_minutes grows, where shrinkage's w INCREASES.

xG90/xA90 themselves are trained the identical way goals_scored_per90 is:
recency-weighted across HIST seasons (merged_gw.csv's `expected_goals` /
`expected_assists` columns, per-match), then shrunk toward a position/
price-tier xG prior with the SAME k as baseline.py (config.shrinkage.k) —
not a separate parameter. Only the G-vs-xG blend weight v is new and gets
its own k_xg (config.xg_blend.k_xg), fit the same sweep methodology as
shrinkage.k was (see docs/PROJECT_LOG.md).

Current-season bootstrap-static ALSO exposes expected_goals_per_90 live
(added in v3 plan §A1) but that field is season-to-date and reads 0 for
every player pre-GW1 — useless at exactly the point (early season, thin
personal history) this blend is supposed to help most. So this module
trains xG90 from HISTORY the same way G90 already is, not from the live
field. The live field becomes usable as an additional input once real
2026-27 matches accumulate — not wired in here, a candidate refinement.

Deliberately independent of build_baseline()'s single combined pass: kept
as its own module (not folded into baseline.py) so M0's production output
is provably unaffected — nothing in baseline.py/project.py imports this
module. It is called explicitly by whatever wires up M2 (fpl/evaluate/
backtest.py's sweep, and eventually fpl/models/m2_xg.py once the Phase B0
model registry exists).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.project import baseline as baseline_mod

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

XG_CHANNELS = ["expected_goals", "expected_assists"]
# expected_goals -> blends with goals_scored_per90; expected_assists -> assists_per90.
XG_TO_GOAL_CHANNEL = {"expected_goals": "goals_scored", "expected_assists": "assists"}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute_shrunk_xg_rates(players_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Personal xG90/xA90, recency-weighted across history exactly like
    baseline.compute_player_rates, then shrunk toward the position/
    price-tier xG prior with the SAME k as goals/assists (config.shrinkage.k)
    — xG's sample-size-to-trust tradeoff is the same shape as goals', it's
    not the G-vs-xG blend (that's v, see blend_weight below).
    Returns one row per current player id: id, expected_goals_per90,
    expected_assists_per90 (both already shrunk), weighted_minutes.
    """
    history = baseline_mod.load_weighted_player_history(players_df, config)
    rates = baseline_mod.compute_player_rates(history, channels=XG_CHANNELS)
    tier_priors, position_priors = baseline_mod.compute_price_tier_priors(
        players_df, rates, config, channels=XG_CHANNELS
    )
    priors = baseline_mod.lookup_priors_for_all(players_df, tier_priors, position_priors, config, channels=XG_CHANNELS)

    out = players_df[["id", "position"]].merge(rates, left_on="id", right_on="current_id", how="left")
    out = out.merge(priors, on=["id", "position"], how="left")

    k = config["shrinkage"]["k"]
    for channel in XG_CHANNELS:
        col = f"{channel}_per90"
        out[col] = baseline_mod.shrink_rate(out[col], out[f"prior_{col}"], out["weighted_minutes"], k)

    return out[["id", "weighted_minutes"] + [f"{c}_per90" for c in XG_CHANNELS]]


def blend_weight(weighted_minutes: pd.Series, k_xg: float) -> pd.Series:
    """
    v = k_xg / (m + k_xg) — DECLINES as weighted_minutes grows (opposite
    direction to baseline.shrinkage_weight's w). m=0 -> v=1 (trust xG
    entirely, since there's no personal goal history to trust either — both
    G90 and xG90 would be pure prior at that point anyway, but xG is still
    the lower-variance one of the two priors). Large m -> v->0 (trust the
    now-substantial personal goal-scoring record over the shot-based proxy).
    """
    m_safe = weighted_minutes.fillna(0)
    return k_xg / (m_safe + k_xg)


def apply_xg_blend(player_inputs: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """
    Takes fpl.project.project.build_player_inputs()'s output (has the
    shrunk goals_scored_per90/assists_per90 columns already — M1-equivalent)
    and returns a COPY with those two columns replaced by the blended
    attacking rate. Caller (M2's project variant) then runs the normal
    compute_channel_pts_per_fixture on the result — no other change needed,
    since goal_pts/assist_pts are computed from those same column names.

    Requires `id` and `weighted_minutes` to already be present in
    player_inputs (both are — see project.build_player_inputs's
    keep_base_cols).
    """
    config = config or load_config()
    k_xg = config["xg_blend"]["k_xg"]

    players_df = player_inputs[["id", "code", "position", "price"]].drop_duplicates()
    xg_rates = compute_shrunk_xg_rates(players_df, config)

    out = player_inputs.merge(xg_rates, on="id", how="left", suffixes=("", "_xg"))
    v = blend_weight(out["weighted_minutes"], k_xg)

    for xg_channel, goal_channel in XG_TO_GOAL_CHANNEL.items():
        xg_col = f"{xg_channel}_per90"
        g_col = f"{goal_channel}_per90"
        out[g_col] = v * out[xg_col].fillna(0) + (1 - v) * out[g_col].fillna(0)
        out = out.drop(columns=[xg_col])

    return out
