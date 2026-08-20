"""
Tests for the shrinkage mechanism (FPL_V2_DESIGN.md spec §4.1) —
fpl/project/baseline.py's shrink_rate/shrinkage_weight/confidence_label,
reused as-is by fpl/project/defcon.py and fpl/evaluate/backtest.py.

Pure unit tests: synthetic pandas Series, no file I/O, no network — same
bar as tests/test_hotfix_regressions.py.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.project import baseline as baseline_mod

THRESHOLDS = {"low": 0.3, "high": 0.7}


def test_zero_history_falls_through_to_the_prior_exactly():
    """m=0 (no bridged history at all) must give w=0 and the shrunk rate
    must equal the prior exactly — the case this whole mechanism replaces
    (a cold-start player with a raw personal rate of NaN)."""
    personal = pd.Series([float("nan")])
    prior = pd.Series([0.35])
    m = pd.Series([0.0])
    result = baseline_mod.shrink_rate(personal, prior, m, k=450)
    assert result.iloc[0] == 0.35


def test_a_well_sampled_player_stays_close_to_their_personal_rate():
    """Haaland-class: weighted_minutes far above k -> w close to 1, shrunk
    rate close to (not equal to, unless prior==personal) the personal one."""
    personal = pd.Series([0.81])
    prior = pd.Series([0.35])
    m = pd.Series([5500.0])
    w = baseline_mod.shrinkage_weight(m, k=1500)
    result = baseline_mod.shrink_rate(personal, prior, m, k=1500)
    assert w.iloc[0] > 0.75
    assert abs(result.iloc[0] - personal.iloc[0]) < abs(result.iloc[0] - prior.iloc[0])


def test_a_thin_sample_player_pulls_toward_the_prior_not_the_personal_rate():
    """Osula-class: m well below k -> w < 0.5, shrunk rate closer to prior
    than to the (possibly inflated) personal number."""
    personal = pd.Series([0.59])   # Osula's real pre-shrinkage personal rate
    prior = pd.Series([0.30])
    m = pd.Series([1159.0])        # Osula's real weighted_minutes
    w = baseline_mod.shrinkage_weight(m, k=1500)
    result = baseline_mod.shrink_rate(personal, prior, m, k=1500)
    assert w.iloc[0] < 0.5
    assert result.iloc[0] < personal.iloc[0]
    assert abs(result.iloc[0] - prior.iloc[0]) < abs(result.iloc[0] - personal.iloc[0])


def test_shrinkage_weight_increases_monotonically_with_sample_size():
    m = pd.Series([0, 100, 1000, 10000])
    w = baseline_mod.shrinkage_weight(m, k=450)
    assert w.is_monotonic_increasing


def test_confidence_label_thresholds():
    """thresholds are inclusive lower bounds: w=0.3 already counts as
    reaching 'medium', w=0.7 already counts as reaching 'high'."""
    w = pd.Series([0.0, 0.29, 0.3, 0.5, 0.69, 0.7, 1.0])
    labels = baseline_mod.confidence_label(w, THRESHOLDS)
    assert list(labels) == ["low", "low", "medium", "medium", "medium", "high", "high"]


def test_lookup_priors_for_all_falls_back_from_tier_to_position():
    players_df = pd.DataFrame([
        {"id": 1, "position": "FWD", "price": 6.0},   # has a tier match
        {"id": 2, "position": "FWD", "price": 20.0},  # no tier at this price -> position fallback
    ])
    tier_priors = pd.DataFrame([{"position": "FWD", "price_tier": 6.0, "goals_scored_per90": 0.4}])
    # fill in every other PLAYER_CHANNELS column so the merge doesn't KeyError
    for c in baseline_mod.PLAYER_CHANNELS:
        if f"{c}_per90" not in tier_priors.columns:
            tier_priors[f"{c}_per90"] = 0.1
    position_priors = tier_priors.copy()
    position_priors["price_tier"] = None
    position_priors["goals_scored_per90"] = 0.25

    config = {"history": {"price_tier_width": 1.0}}
    out = baseline_mod.lookup_priors_for_all(players_df, tier_priors, position_priors, config)

    assert out.set_index("id").loc[1, "prior_goals_scored_per90"] == 0.4
    assert out.set_index("id").loc[2, "prior_goals_scored_per90"] == 0.25
