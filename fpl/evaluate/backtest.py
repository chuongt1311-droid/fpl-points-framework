"""
backtest.py — EVALUATE layer. Phase 3 exit gate: "Backtest on 2025/26: RMSE
reported by position." Repaired 2026-08-20 per FPL_V2_DESIGN.md spec §3.5
(three fixes, in priority order — see each function's docstring for detail):

  1. The per-90 rate formula is now IMPORTED from baseline.py
     (compute_player_rates / load_weighted_player_history), not duplicated.
     A fix to the live formula now propagates here automatically instead of
     silently drifting out of sync (handoff finding #9).
  2. Missing training rates use the position/price-tier prior baseline.py
     itself uses for cold-start players, instead of zero-filling — the
     backtest now actually exercises the cold-start fallback path it's
     supposed to validate (handoff finding #7).
  3. save_pts and conceded_pts are now included for GK/DEF, matching the
     live model (handoff finding #8) — GK's previously-worst
     under-prediction ratio was plausibly this omission.

SCOPE (documented, not hidden — plan principle 4): still a single
retrospective train/test split, not a full walk-forward re-baseline. Train
per-90 channel rates on 2023-24 + 2024-25 ONLY (strictly before 2025-26 —
no leakage), bridged onto the 2025-26 roster as the "reference" season
(fpl.project.identity's reference_teams override, added for exactly this).
Predict each 2025-26 player's points using their ACTUAL 2025-26 minutes
played — isolates the quality of the trained per-90 RATES specifically,
separate from minutes-prediction accuracy (plan §10.6). Fixture difficulty
is NOT modelled (neutral fixture_mult=1.0 throughout) — replaying
fixture-by-fixture difficulty for a past season is a bigger undertaking
than this backtest's purpose (rate quality) requires.

DEFCON IS STILL EXCLUDED. Real constraint, not an oversight: 2025-26 is the
only DEFCON-scored season that exists, so there is no leak-free prior
season to train a DEFCON rate from. Phase 2's notebook validated DEFCON's
rate-based estimator a different way, against real 2025-26 data directly.

NEW (spec §4.2): also exposes per-(position, channel) calibration ratios
(actual / predicted, on the repaired predictions) — fpl/project/project.py
reads these to correct measured under-prediction as an explicit final
step, never folded into the channel rates themselves.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from fpl.project import baseline as baseline_mod
from fpl.project import identity
from fpl.project import understat_blend as understat_blend_mod
from fpl.project import xg_blend as xg_blend_mod

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
HIST_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "history"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"

TRAIN_SEASONS = ["2023-24", "2024-25"]
TEST_SEASON = "2025-26"

# Channels calibration is fit for — the ones with a stable, meaningful
# actual-vs-predicted points comparison. Deliberately excludes:
#   appearance_pts — follows deterministically from actual minutes here
#                    (see load_actuals), not something a rate predicts.
#   card_pts       — small and noisy; a multiplicative "correction" on a
#                    mostly-zero, occasionally-negative channel is more
#                    likely to overfit noise than fix real bias.
CALIBRATION_CHANNELS = ["goal", "assist", "cleansheet", "bonus", "save", "conceded"]
# Guard against an unstable ratio (e.g. a position/channel with almost no
# predicted signal) blowing up into a huge multiplier — D11's own warning
# that "a calibration factor that starts growing is itself a signal
# something upstream broke" cuts both ways: clip rather than trust blindly.
CALIBRATION_CLIP = (0.5, 2.0)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _train_only_config(config: dict) -> dict:
    """A shallow copy of config with history.seasons restricted to
    TRAIN_SEASONS — lets baseline.py's own history-loading functions be
    reused verbatim (finding #9) without ever touching TEST_SEASON's data,
    which would leak the answer into training."""
    train_config = {**config, "history": {**config["history"], "seasons": TRAIN_SEASONS}}
    return train_config


def load_test_roster() -> pd.DataFrame:
    """2025-26's own players_raw.csv, standing in as the 'reference' roster
    — this is what makes the historical bridge in identity.py work: we
    treat 2025-26 as if it were 'the current season' for bridging purposes.
    Also carries `price` (now_cost/10, same convention as build_players.py)
    so baseline.compute_price_tier_priors can be reused as-is."""
    path = HIST_DIR / TEST_SEASON / "players_raw.csv"
    df = pd.read_csv(path, encoding="utf-8")
    cols = {"id": "id", "code": "code", "element_type": "element_type", "web_name": "web_name",
            "team": "team", "now_cost": "now_cost"}
    df = df[list(cols.keys())].rename(columns=cols)
    position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    df["position"] = df["element_type"].map(position_map)
    df["price"] = df["now_cost"] / 10.0
    return df


def load_test_teams() -> pd.DataFrame:
    path = HIST_DIR / TEST_SEASON / "teams.csv"
    return pd.read_csv(path, encoding="utf-8")[["id", "code", "name"]]


def build_training_rates(test_roster: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Per-90 channel rates trained on TRAIN_SEASONS only, bridged onto the
    2025-26 roster — via baseline.py's OWN functions (finding #9), not a
    reimplementation. Also returns the tier/position prior tables baseline
    itself falls back to for cold-start players, so predict_points can use
    the identical fallback instead of zero-filling (finding #7).
    """
    train_config = _train_only_config(config)
    history = baseline_mod.load_weighted_player_history(test_roster, train_config)
    rates = baseline_mod.compute_player_rates(history)
    tier_priors, position_priors = baseline_mod.compute_price_tier_priors(test_roster, rates, train_config)
    return rates, tier_priors, position_priors


def load_actuals() -> pd.DataFrame:
    """
    Actual 2025-26 season totals per player (already-current ids, since
    test_roster IS the 2025-26 roster), including per-CHANNEL raw sums —
    needed for calibration (spec §4.2), not just the total. Also
    actual_appearance_points, computed directly from real per-match minutes
    (2 if that match's minutes >= 60, else 1 if > 0, else 0) — since this
    backtest is given actual minutes rather than predicting them,
    appearance points follow deterministically and aren't something the
    trained rate needs to predict (see predict_points).
    """
    path = HIST_DIR / TEST_SEASON / "gws" / "merged_gw.csv"
    gw = pd.read_csv(path, encoding="utf-8")
    gw = gw.copy()
    gw["appearance_pts"] = np.where(gw["minutes"] >= 60, 2, np.where(gw["minutes"] > 0, 1, 0))
    channel_cols = ["goals_scored", "assists", "clean_sheets", "bonus", "saves", "goals_conceded"]
    agg = {c: (c, "sum") for c in channel_cols}
    return gw.groupby("element", as_index=False).agg(
        actual_minutes=("minutes", "sum"),
        actual_points=("total_points", "sum"),
        actual_appearance_points=("appearance_pts", "sum"),
        **agg,
    ).rename(columns={"element": "id", **{c: f"actual_{c}" for c in channel_cols}})


def _apply_shrinkage(
    rates: pd.DataFrame, test_roster: pd.DataFrame, tier_priors: pd.DataFrame,
    position_priors: pd.DataFrame, config: dict,
) -> pd.DataFrame:
    """
    Merges roster onto rates, then SHRINKS every player's channel rates
    toward the position/price-tier prior via baseline.shrink_rate — the
    exact same function and k production uses (fpl/project/baseline.py),
    not a separate zero-fill or hard-replace. This is what makes it valid
    to tune config.shrinkage.k against this backtest's RMSE (spec §4.1) —
    the backtest is measuring exactly what shrinkage does, not some other
    approximation of it. Also the fix for finding #7: a cold-start player
    used to predict exactly 0 for every channel (or, in an earlier repair
    pass, a hard-replaced prior with no personal signal at all) — now a
    continuous blend, same as live.
    """
    df = test_roster.merge(rates, left_on="id", right_on="current_id", how="left")
    priors = baseline_mod.lookup_priors_for_all(test_roster, tier_priors, position_priors, config)
    df = df.merge(priors, on=["id", "position"], how="left")

    k = config["shrinkage"]["k"]
    channel_cols = [f"{c}_per90" for c in baseline_mod.PLAYER_CHANNELS]
    for col in channel_cols:
        df[col] = baseline_mod.shrink_rate(df[col], df[f"prior_{col}"], df["weighted_minutes"], k)

    return df


def predict_points(
    rates: pd.DataFrame, tier_priors: pd.DataFrame, position_priors: pd.DataFrame,
    actuals: pd.DataFrame, test_roster: pd.DataFrame, config: dict, model: str = "m0_rules",
) -> pd.DataFrame:
    """
    predicted_points_per90, applied to ACTUAL minutes played (see module
    docstring for why) — no fixture multiplier, no DEFCON. Now includes
    save_pts and conceded_pts for GK/DEF (finding #8), and per-channel
    predicted-points columns (predicted_{channel}_pts) alongside the total,
    so compute_channel_calibration can compare them to actuals directly.

    `model` mirrors fpl.project.project.build_player_inputs' param (plan
    §B1): "m0_rules" (default, unchanged), "m2_xg", or "m3_understat"
    (nests on m2_xg) — after shrinkage, _apply_shrinkage's output already
    has exactly the columns xg_blend.apply_xg_blend /
    understat_blend.apply_understat_blend need (id, code, position, price,
    weighted_minutes, goals_scored_per90, assists_per90), so both are
    reused directly rather than re-implemented for the backtest.
    """
    goal_mult = config["position_multipliers"]["goals"]
    assist_mult = config["position_multipliers"]["assists_flat"]
    cs_value = config["position_multipliers"]["clean_sheet_value"]
    rules = config["scoring_rules"]

    df = _apply_shrinkage(rates, test_roster, tier_priors, position_priors, config)
    if model in ("m2_xg", "m3_understat"):
        # LEAKAGE GUARD: apply_xg_blend/apply_understat_blend internally
        # re-load history via config["history"]["seasons"] — the plain
        # `config` includes TEST_SEASON (2025-26, production's own seasons
        # list). Must pass the train-only config here, same as
        # build_training_rates does for the goal/assist rates, or the xG/
        # npxG rates would be trained partly on the season being predicted.
        df = xg_blend_mod.apply_xg_blend(df, _train_only_config(config))
    if model == "m3_understat":
        # M3 nests on M2 (plan §B1) — same train-only-config guard applies
        # to the npxG blend. player_id_map is 2025-26-built (see
        # understat_blend.py) but joins on the STABLE fpl_code/understat_id
        # bridge, so reusing it here for the historical train seasons is
        # legitimate identity-wise; only the RATE TRAINING WINDOW (history
        # seasons) needs restricting, not the map itself.
        df = understat_blend_mod.apply_understat_blend(df, _train_only_config(config))
    elif model not in ("m0_rules", "m2_xg"):
        raise ValueError(f"Unknown model {model!r} — expected 'm0_rules' or 'm2_xg'")
    df = df.merge(actuals, on="id", how="inner")  # only players with real 2025-26 minutes
    df = df[df["actual_minutes"] > 0]

    df["goal_mult"] = df["position"].map(goal_mult)
    df["cs_value"] = df["position"].map(cs_value)
    matches = df["actual_minutes"] / 90.0

    df["predicted_goal_pts"] = df["goals_scored_per90"].fillna(0) * df["goal_mult"] * matches
    df["predicted_assist_pts"] = df["assists_per90"].fillna(0) * assist_mult * matches
    df["predicted_cleansheet_pts"] = df["clean_sheets_per90"].fillna(0) * df["cs_value"] * matches
    df["predicted_bonus_pts"] = df["bonus_per90"].fillna(0) * matches
    df["predicted_card_pts"] = (
        df["yellow_cards_per90"].fillna(0) * rules["yellow_card"] + df["red_cards_per90"].fillna(0) * rules["red_card"]
    ) * matches
    # save_pts / conceded_pts: GK/DEF only, matching project.py's own gating
    # (finding #8 — these were missing entirely before).
    df["predicted_save_pts"] = (df["saves_per90"].fillna(0) / rules["saves_per_point"]) * matches
    df["predicted_save_pts"] = df["predicted_save_pts"].where(df["position"] == "GK", 0.0)
    df["predicted_conceded_pts"] = (
        -1.0 / rules["conceded_per_point"] * df["goals_conceded_per90"].fillna(0) * matches
    )
    df["predicted_conceded_pts"] = df["predicted_conceded_pts"].where(df["position"].isin(["GK", "DEF"]), 0.0)

    # Given actual minutes (not predicting them), appearance points follow
    # deterministically from real per-match minutes — added as-is, not
    # modelled, since that's not what this backtest is testing (see
    # load_actuals).
    df["predicted_points"] = (
        df["predicted_goal_pts"] + df["predicted_assist_pts"] + df["predicted_cleansheet_pts"]
        + df["predicted_bonus_pts"] + df["predicted_card_pts"] + df["predicted_save_pts"] + df["predicted_conceded_pts"]
        + df["actual_appearance_points"]
    )
    df["predicted_points"] = df["predicted_points"].fillna(0)

    # Matching actual-points-per-channel, for calibration (spec §4.2).
    df["actual_goal_pts"] = df["actual_goals_scored"] * df["goal_mult"]
    df["actual_assist_pts"] = df["actual_assists"] * assist_mult
    df["actual_cleansheet_pts"] = df["actual_clean_sheets"] * df["cs_value"]
    df["actual_bonus_pts"] = df["actual_bonus"]
    df["actual_save_pts"] = np.where(df["position"] == "GK", df["actual_saves"] / rules["saves_per_point"], 0.0)
    df["actual_conceded_pts"] = np.where(
        df["position"].isin(["GK", "DEF"]), -1.0 / rules["conceded_per_point"] * df["actual_goals_conceded"], 0.0
    )

    return df


def compute_rmse_by_position(predictions: pd.DataFrame) -> pd.DataFrame:
    predictions = predictions.copy()
    predictions["error"] = predictions["predicted_points"] - predictions["actual_points"]
    predictions["abs_error"] = predictions["error"].abs()

    def _rmse(g):
        return np.sqrt((g["error"] ** 2).mean())

    out = predictions.groupby("position").apply(
        lambda g: pd.Series({
            "n_players": len(g),
            "rmse": _rmse(g),
            "mae": g["abs_error"].mean(),
            "mean_actual": g["actual_points"].mean(),
            "mean_predicted": g["predicted_points"].mean(),
        }),
        include_groups=False,
    ).reset_index()
    return out


def compute_top40_rank_correlation(predictions: pd.DataFrame, n: int = 40) -> float:
    """
    v3 plan §C1/§C2: the PRIMARY statistical metric, not full-pool MAE/RMSE
    (compute_rmse_by_position above — kept, but secondary, per §C2: "Global
    MAE is secondary and reported only for calibration purposes"). The
    optimiser doesn't care about rank quality across the full ~600-player
    pool — it only ever decides among the players actually competing for a
    squad slot. Plan's own worked example: improving every £4.0m bench
    defender's projection by 0.5 pts moves global MAE and changes zero
    decisions; getting one £12m forward's rank wrong changes the captain.

    "Top 40" = the model's OWN top 40 by predicted_points — the pool it
    would actually put in front of the optimiser — not top-40-by-actual
    (which would leak the answer into the definition of the test set).
    Spearman = Pearson on ranks, matching compute_rmse_by_position's
    existing rank_correlation_by_position (no new scipy dependency).

    NOT gameweek-clustered (plan §C1's stated requirement for the LIVE
    decision-scorecard SEs): this backtest predicts one SEASON-cumulative
    total per player from a single train/test split, not per-gameweek
    observations, so there is no gameweek axis to cluster on here. Real
    gameweek-clustered SEs apply once fpl/evaluate/hindsight.py has
    accumulated real per-GW predictions (plan §C3) — see docs/PROJECT_LOG.md.
    """
    top = predictions.nlargest(n, "predicted_points")
    return float(top["predicted_points"].rank().corr(top["actual_points"].rank()))


def compute_channel_calibration(predictions: pd.DataFrame) -> dict:
    """
    Spec §4.2: per-(position, channel) multiplier = sum(actual) / sum(predicted),
    clipped to CALIBRATION_CLIP. Uses summed totals rather than a mean of
    per-player ratios so a handful of near-zero predicted players (ratio
    blowing up toward infinity) can't dominate — matches how RMSE/MAE above
    are already aggregated.
    """
    out: dict[str, dict[str, float]] = {}
    for position, g in predictions.groupby("position"):
        out[position] = {}
        for channel in CALIBRATION_CHANNELS:
            actual_sum = g[f"actual_{channel}_pts"].sum()
            predicted_sum = g[f"predicted_{channel}_pts"].sum()
            # magnitude, not raw value: conceded_pts is a NEGATIVE channel,
            # so a plain `<= 1e-6` check would always trip for it (any real
            # negative penalty sum is "<= 1e-6") and silently no-op a
            # channel that actually has plenty of signal. abs() here is the
            # fix — "no stable signal" means near-ZERO in either direction
            # (e.g. save/conceded for non-GK/DEF positions, both
            # legitimately ~0), not "negative".
            if abs(predicted_sum) <= 1e-6 or abs(actual_sum) <= 1e-6:
                out[position][channel] = 1.0
                continue
            ratio = actual_sum / predicted_sum
            out[position][channel] = float(np.clip(ratio, *CALIBRATION_CLIP))
    return out


def run_backtest(config: Optional[dict] = None, model: str = "m0_rules") -> dict:
    """
    `model` — see predict_points. "m0_rules" (default) writes to
    data/output/model_health.json, the SAME path as always — the file
    project.py's load_calibration_factors reads, so this must stay
    unaffected by model=="m2_xg" ever having been called. Any other model
    writes to data/output/model_health_{model}.json instead (plan §B1: each
    model gets its own artefact, never a shared file) — a bakeoff
    comparison reads both, production only ever reads the m0_rules one.
    """
    config = config or load_config()
    test_roster = load_test_roster()
    rates, tier_priors, position_priors = build_training_rates(test_roster, config)
    actuals = load_actuals()
    predictions = predict_points(rates, tier_priors, position_priors, actuals, test_roster, config, model=model)
    rmse_by_position = compute_rmse_by_position(predictions)
    calibration_factors = compute_channel_calibration(predictions)
    top40_rank_correlation = compute_top40_rank_correlation(predictions)

    n_from_prior = int((predictions["historical_minutes"].isna()
                         | (predictions["historical_minutes"] < config["new_signing"]["min_historical_minutes"])).sum())

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Spearman = Pearson on ranks — avoids adding scipy as a dependency for
    # one metric.
    rank_corr_by_position = (
        predictions.groupby("position")[["predicted_points", "actual_points"]]
        .apply(lambda g: g["predicted_points"].rank().corr(g["actual_points"].rank()), include_groups=False)
    )
    summary = {
        "model": model,
        "test_season": TEST_SEASON,
        "train_seasons": TRAIN_SEASONS,
        "n_players_tested": int(len(predictions)),
        "n_players_from_tier_prior": n_from_prior,
        "scope_note": "Single retrospective split, actual minutes used, no fixture adjustment, DEFCON excluded (no leak-free training season exists) — see module docstring.",
        # PRIMARY metric per plan §C1/§C2 — see compute_top40_rank_correlation's
        # docstring for why this, not overall_rmse, is what a model change
        # should be judged on.
        "top40_rank_correlation": round(top40_rank_correlation, 4),
        "rmse_by_position": rmse_by_position.set_index("position").to_dict(orient="index"),
        "overall_rmse": float(np.sqrt(((predictions["predicted_points"] - predictions["actual_points"]) ** 2).mean())),
        "rank_correlation_by_position": rank_corr_by_position.round(3).to_dict(),
        "calibration_factors": calibration_factors,
    }
    out_name = "model_health.json" if model == "m0_rules" else f"model_health_{model}.json"
    (OUTPUT_DIR / out_name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"predictions": predictions, "rmse_by_position": rmse_by_position, "summary": summary}


if __name__ == "__main__":
    result = run_backtest()
    print(f"Backtest: trained on {TRAIN_SEASONS}, tested on {TEST_SEASON}")
    print(f"Players tested: {result['summary']['n_players_tested']} "
          f"({result['summary']['n_players_from_tier_prior']} from tier prior — cold-start fallback exercised)")
    print(f"\nRMSE / MAE by position:")
    print(result["rmse_by_position"].round(2).to_string(index=False))
    print(f"\nOverall RMSE: {result['summary']['overall_rmse']:.2f}")
    print(f"\nRank correlation (predicted vs actual points), by position:")
    for pos, corr in result["summary"]["rank_correlation_by_position"].items():
        print(f"  {pos}: {corr:.3f}")
    print(f"\nCalibration factors (actual/predicted, clipped to {CALIBRATION_CLIP}):")
    for pos, channels in result["summary"]["calibration_factors"].items():
        print(f"  {pos}: " + ", ".join(f"{c}={v:.2f}" for c, v in channels.items()))

    preds = result["predictions"]
    print(f"\nTop-20 by actual 2025-26 points — does the model rank them reasonably?")
    top20 = preds.nlargest(20, "actual_points")[["web_name", "position", "actual_points", "predicted_points"]]
    print(top20.to_string(index=False))
