"""
backtest.py — EVALUATE layer. Phase 3 exit gate: "Backtest on 2025/26: RMSE
reported by position."

SCOPE (documented, not hidden — plan principle 4): this is a single
retrospective train/test split, not a full walk-forward re-baseline of
every gameweek. Train per-90 channel rates on 2023-24 + 2024-25 ONLY
(strictly before 2025-26 — no leakage), bridged onto the 2025-26 roster as
the "reference" season (fpl.project.identity's reference_teams override,
added for exactly this). Predict each 2025-26 player's points using their
ACTUAL 2025-26 minutes played — this isolates the quality of the trained
per-90 RATES specifically, separate from minutes-prediction accuracy
(already a named limitation, plan §10.6). Fixture difficulty is NOT
modelled in the backtest (neutral fixture_mult=1.0 throughout) — replaying
fixture-by-fixture difficulty for a past season is a bigger undertaking
than this backtest's purpose (rate quality) requires; the LIVE GW1
projection (project.py) does apply fixture multipliers.

DEFCON IS EXCLUDED FROM THIS BACKTEST. Real constraint, not an oversight:
2025-26 is the only DEFCON-scored season that exists at all, so there is no
prior season to train a leak-free DEFCON rate from. DEFCON's estimator was
instead validated a different way — Phase 2's notebook confirmed the
rate-based approach against real 2025-26 data directly. RMSE reported here
reflects the non-DEFCON channels; DEF/MID error may run slightly high for
big defensive-contribution players as a direct, known consequence of this
scope limit, not a defect in the live model (which DOES include DEFCON).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from fpl.project import identity

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
HIST_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "history"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"

TRAIN_SEASONS = ["2023-24", "2024-25"]
TEST_SEASON = "2025-26"
TRAIN_DECAY = 0.6  # same recency decay as baseline.py, one season back = 0.6, two = 0.36... here just 2 seasons

CHANNELS = ["goals_scored", "assists", "clean_sheets", "bonus", "yellow_cards", "red_cards"]


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _season_weight(seasons: list[str], season: str) -> float:
    seasons_ago = len(seasons) - 1 - seasons.index(season)
    return TRAIN_DECAY ** seasons_ago


def load_test_roster() -> pd.DataFrame:
    """2025-26's own players_raw.csv, standing in as the 'reference' roster
    — this is what makes the historical bridge in identity.py work: we
    treat 2025-26 as if it were 'the current season' for bridging purposes."""
    path = HIST_DIR / TEST_SEASON / "players_raw.csv"
    df = pd.read_csv(path, encoding="utf-8")
    cols = {"id": "id", "code": "code", "element_type": "element_type", "web_name": "web_name", "team": "team"}
    df = df[list(cols.keys())].rename(columns=cols)
    position_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    df["position"] = df["element_type"].map(position_map)
    return df


def load_test_teams() -> pd.DataFrame:
    path = HIST_DIR / TEST_SEASON / "teams.csv"
    return pd.read_csv(path, encoding="utf-8")[["id", "code", "name"]]


def build_training_rates(test_roster: pd.DataFrame) -> pd.DataFrame:
    """Per-90 channel rates trained on TRAIN_SEASONS only, bridged onto the
    2025-26 roster (test_roster acts as the 'current' reference)."""
    frames = []
    for season in TRAIN_SEASONS:
        path = HIST_DIR / season / "gws" / "merged_gw.csv"
        gw = pd.read_csv(path, encoding="utf-8")
        bridged = identity.attach_current_player_id(gw, season, test_roster)
        bridged["weight"] = _season_weight(TRAIN_SEASONS, season)
        frames.append(bridged)
    history = pd.concat(frames, ignore_index=True)

    played = history[history["minutes"] > 0].copy()
    played["weighted_minutes"] = played["weight"] * played["minutes"]
    grouped = played.groupby("current_id")

    out = pd.DataFrame({
        "current_id": grouped.size().index,
        "train_matches": grouped.size().values,
        "train_minutes": grouped["minutes"].sum().values,
        "weighted_minutes": grouped["weighted_minutes"].sum().values,
    })
    for channel in CHANNELS:
        weighted_sum = played.assign(_w=played["weight"] * played[channel]).groupby("current_id")["_w"].sum()
        out = out.merge(weighted_sum.rename(f"{channel}_weighted_sum"), on="current_id", how="left")
    for channel in CHANNELS:
        out[f"{channel}_per90"] = (out[f"{channel}_weighted_sum"] * 90 / out["weighted_minutes"]).where(out["weighted_minutes"] > 0)
        out = out.drop(columns=[f"{channel}_weighted_sum"])

    return out.rename(columns={"current_id": "id"})


def load_actuals() -> pd.DataFrame:
    """
    Actual 2025-26 season totals per player (already-current ids, since
    test_roster IS the 2025-26 roster) — including actual_appearance_points,
    computed directly from real per-match minutes (2 if that match's
    minutes >= 60, else 1 if > 0, else 0). Since this backtest is given
    actual minutes rather than predicting them, appearance points follow
    deterministically from minutes and aren't something the trained rate
    needs to predict — see predict_points().
    """
    path = HIST_DIR / TEST_SEASON / "gws" / "merged_gw.csv"
    gw = pd.read_csv(path, encoding="utf-8")
    gw = gw.copy()
    gw["appearance_pts"] = np.where(gw["minutes"] >= 60, 2, np.where(gw["minutes"] > 0, 1, 0))
    return gw.groupby("element", as_index=False).agg(
        actual_minutes=("minutes", "sum"),
        actual_points=("total_points", "sum"),
        actual_appearance_points=("appearance_pts", "sum"),
    ).rename(columns={"element": "id"})


def predict_points(rates: pd.DataFrame, actuals: pd.DataFrame, test_roster: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    predicted_points_per90, applied to ACTUAL minutes played (see module
    docstring for why) — no fixture multiplier, no DEFCON.
    """
    goal_mult = config["position_multipliers"]["goals"]
    assist_mult = config["position_multipliers"]["assists_flat"]
    cs_value = config["position_multipliers"]["clean_sheet_value"]
    rules = config["scoring_rules"]

    df = test_roster[["id", "web_name", "position"]].merge(rates, on="id", how="left")
    df = df.merge(actuals, on="id", how="inner")  # only players with real 2025-26 minutes
    df = df[df["actual_minutes"] > 0]

    df["goal_mult"] = df["position"].map(goal_mult)
    df["cs_value"] = df["position"].map(cs_value)

    df["goal_pts_per90"] = df["goals_scored_per90"].fillna(0) * df["goal_mult"]
    df["assist_pts_per90"] = df["assists_per90"].fillna(0) * assist_mult
    df["cs_pts_per90"] = df["clean_sheets_per90"].fillna(0) * df["cs_value"]
    df["bonus_pts_per90"] = df["bonus_per90"].fillna(0)
    df["card_pts_per90"] = (
        df["yellow_cards_per90"].fillna(0) * rules["yellow_card"] + df["red_cards_per90"].fillna(0) * rules["red_card"]
    )
    # Given actual minutes (not predicting them), appearance points follow
    # deterministically from real per-match minutes — added as-is, not
    # modelled, since that's not what this backtest is testing (see
    # load_actuals). Everything else scales with actual match-equivalents.
    matches = df["actual_minutes"] / 90.0
    df["predicted_points"] = (
        df["goal_pts_per90"] + df["assist_pts_per90"] + df["cs_pts_per90"] + df["bonus_pts_per90"] + df["card_pts_per90"]
    ) * matches + df["actual_appearance_points"]

    df["predicted_points"] = df["predicted_points"].fillna(0)
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


def run_backtest(config: Optional[dict] = None) -> dict:
    config = config or load_config()
    test_roster = load_test_roster()
    rates = build_training_rates(test_roster)
    actuals = load_actuals()
    predictions = predict_points(rates, actuals, test_roster, config)
    rmse_by_position = compute_rmse_by_position(predictions)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Spearman = Pearson on ranks — avoids adding scipy as a dependency for
    # one metric.
    rank_corr_by_position = (
        predictions.groupby("position")[["predicted_points", "actual_points"]]
        .apply(lambda g: g["predicted_points"].rank().corr(g["actual_points"].rank()), include_groups=False)
    )
    summary = {
        "test_season": TEST_SEASON,
        "train_seasons": TRAIN_SEASONS,
        "n_players_tested": int(len(predictions)),
        "scope_note": "Single retrospective split, actual minutes used, no fixture adjustment, DEFCON excluded (no leak-free training season exists) — see module docstring.",
        "rmse_by_position": rmse_by_position.set_index("position").to_dict(orient="index"),
        "overall_rmse": float(np.sqrt(((predictions["predicted_points"] - predictions["actual_points"]) ** 2).mean())),
        "rank_correlation_by_position": rank_corr_by_position.round(3).to_dict(),
    }
    import json
    (OUTPUT_DIR / "model_health.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"predictions": predictions, "rmse_by_position": rmse_by_position, "summary": summary}


if __name__ == "__main__":
    result = run_backtest()
    print(f"Backtest: trained on {TRAIN_SEASONS}, tested on {TEST_SEASON}")
    print(f"Players tested: {result['summary']['n_players_tested']}")
    print(f"\nRMSE / MAE by position:")
    print(result["rmse_by_position"].round(2).to_string(index=False))
    print(f"\nOverall RMSE: {result['summary']['overall_rmse']:.2f}")
    print(f"\nRank correlation (predicted vs actual points), by position — does the model discriminate well even where absolute levels are off:")
    for pos, corr in result["summary"]["rank_correlation_by_position"].items():
        print(f"  {pos}: {corr:.3f}")

    preds = result["predictions"]
    print(f"\nTop-20 by actual 2025-26 points — does the model rank them reasonably?")
    top20 = preds.nlargest(20, "actual_points")[["web_name", "position", "actual_points", "predicted_points"]]
    print(top20.to_string(index=False))
