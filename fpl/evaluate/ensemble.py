"""
ensemble.py — v3 plan §B5, model M5: "performance-weighted blend of
M0-M4... weighting fitted OUT-OF-SAMPLE only." Blends whichever of
M0/M2/M3 are built (M1 doesn't exist as a separate model — see
docs/PROJECT_LOG.md §11 for why; M4/Sofascore isn't built this session —
see the same file for the robots.txt/consent finding that paused it).

"Out-of-sample only" is the one rule this module exists to enforce
correctly: fitting ensemble weights on the SAME data used to evaluate the
ensemble's performance is circular — the ensemble would look artificially
good simply because its weights were tuned to that exact data, the same
class of mistake as tuning a hyperparameter directly against your test
set. With only one retrospective train/test split available (plan's own
documented scope limit — fpl/evaluate/backtest.py's module docstring),
there's no second, truly independent season to fit weights on. The
practical fix here: split TEST_SEASON's own tested player pool into two
disjoint halves — weights are fit on one half, the ensemble (and every
individual model, for a fair comparison) is evaluated ONLY on the other.
A fixed random seed makes the split reproducible.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from fpl.evaluate import backtest as bt

DEFAULT_MODELS = ["m0_rules", "m2_xg", "m3_understat"]


def _fit_eval_split(ids: pd.Series, seed: int = 42) -> tuple[set, set]:
    shuffled = ids.sample(frac=1.0, random_state=seed).tolist()
    midpoint = len(shuffled) // 2
    return set(shuffled[:midpoint]), set(shuffled[midpoint:])


def _inverse_rmse_squared_weights(rmses: dict[str, float]) -> dict[str, float]:
    """
    Standard inverse-variance-style ensemble weighting: a model with
    HALF the RMSE of another gets 4x its weight (1/rmse^2), not 2x —
    reflects that squared error, not linear error, is what each model is
    actually minimising. Normalised to sum to 1.
    """
    inv = {model: 1.0 / (rmse ** 2) for model, rmse in rmses.items()}
    total = sum(inv.values())
    return {model: v / total for model, v in inv.items()}


def run_ensemble_backtest(
    models: Optional[list[str]] = None, config: Optional[dict] = None, seed: int = 42,
) -> dict:
    """
    Fits inverse-RMSE-squared weights for each of `models` on a FIT half
    of TEST_SEASON's tested players, then evaluates the blended
    prediction (and every individual model, for a fair same-set
    comparison) on the disjoint EVAL half.

    Returns {"weights": {model: weight}, "ensemble": {top40_rank_correlation,
    overall_rmse}, "individual": {model: {top40_rank_correlation,
    overall_rmse}} — all computed on the SAME eval half}.
    """
    models = models or DEFAULT_MODELS
    config = config or bt.load_config()

    test_roster = bt.load_test_roster()
    rates, tier_priors, position_priors = bt.build_training_rates(test_roster, config)
    actuals = bt.load_actuals()

    predictions_by_model = {
        model: bt.predict_points(rates, tier_priors, position_priors, actuals, test_roster, config, model=model)
        for model in models
    }

    # All models share the same test_roster/actuals, so their `id` universes
    # match exactly — align on the FIRST model's ids to build the split.
    all_ids = predictions_by_model[models[0]]["id"]
    fit_ids, eval_ids = _fit_eval_split(all_ids, seed=seed)

    fit_rmse = {}
    for model, preds in predictions_by_model.items():
        fit_preds = preds[preds["id"].isin(fit_ids)]
        fit_rmse[model] = float(np.sqrt(((fit_preds["predicted_points"] - fit_preds["actual_points"]) ** 2).mean()))
    weights = _inverse_rmse_squared_weights(fit_rmse)

    # Blend on the EVAL half only — never the fit half, or this ensemble's
    # own reported performance would be inflated by the same circularity
    # this module's docstring warns against.
    eval_frames = []
    for model, preds in predictions_by_model.items():
        eval_preds = preds[preds["id"].isin(eval_ids)][["id", "predicted_points", "actual_points"]].copy()
        eval_preds["weighted_pred"] = eval_preds["predicted_points"] * weights[model]
        eval_frames.append(eval_preds.set_index("id")[["weighted_pred"]].rename(columns={"weighted_pred": model}))

    blended = pd.concat(eval_frames, axis=1)
    blended["predicted_points"] = blended[models].sum(axis=1)
    actual_lookup = predictions_by_model[models[0]].set_index("id")["actual_points"]
    blended["actual_points"] = actual_lookup.reindex(blended.index)

    ensemble_top40 = bt.compute_top40_rank_correlation(blended.reset_index())
    ensemble_rmse = float(np.sqrt(((blended["predicted_points"] - blended["actual_points"]) ** 2).mean()))

    individual = {}
    for model, preds in predictions_by_model.items():
        eval_preds = preds[preds["id"].isin(eval_ids)]
        individual[model] = {
            "top40_rank_correlation": round(bt.compute_top40_rank_correlation(eval_preds), 4),
            "overall_rmse": float(np.sqrt(((eval_preds["predicted_points"] - eval_preds["actual_points"]) ** 2).mean())),
        }

    return {
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "ensemble": {
            "top40_rank_correlation": round(ensemble_top40, 4),
            "overall_rmse": round(ensemble_rmse, 3),
        },
        "individual": individual,
        "n_fit": len(fit_ids),
        "n_eval": len(eval_ids),
    }


if __name__ == "__main__":
    result = run_ensemble_backtest()
    print(f"Fit set: {result['n_fit']} players, Eval set: {result['n_eval']} players (disjoint)")
    print(f"Fitted weights (inverse-RMSE^2, fit half only): {result['weights']}")
    print(f"\nEvaluated on the EVAL half only (same set for every row below):")
    for model, metrics in result["individual"].items():
        print(f"  {model}: top40_rank_corr={metrics['top40_rank_correlation']:.4f} rmse={metrics['overall_rmse']:.3f}")
    print(f"  ENSEMBLE (M5): top40_rank_corr={result['ensemble']['top40_rank_correlation']:.4f} rmse={result['ensemble']['overall_rmse']:.3f}")
