"""
Regression test for fpl/evaluate/backtest.py's model="m2_xg" path — guards
a real leakage bug caught while wiring it up: xg_blend.apply_xg_blend
internally reloads player history via config["history"]["seasons"], and
plain `config` (as opposed to the train-only copy _apply_shrinkage's
caller already builds for the goal/assist rates) includes TEST_SEASON
(2025-26) in production's own history.seasons list. Passing it unmodified
would train the xG rates partly on the season being predicted — the exact
class of leakage bug fpl/project/identity.py's docstring warns about.

Pure unit test: monkeypatches xg_blend_mod.apply_xg_blend to capture the
config it was actually called with, no file I/O beyond what
build_training_rates/load_test_roster/load_actuals already need (real
history CSVs — same dependency test_hindsight.py-style integration tests
already accept).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from fpl.evaluate import backtest as bt


def test_m2_xg_backtest_never_passes_a_config_containing_the_test_season(monkeypatch):
    captured_configs = []

    def _fake_apply_xg_blend(df, config):
        captured_configs.append(config)
        return df  # no-op: this test only cares about what config was passed

    monkeypatch.setattr(bt.xg_blend_mod, "apply_xg_blend", _fake_apply_xg_blend)

    config = bt.load_config()
    test_roster = bt.load_test_roster()
    rates, tier_priors, position_priors = bt.build_training_rates(test_roster, config)
    actuals = bt.load_actuals()
    bt.predict_points(rates, tier_priors, position_priors, actuals, test_roster, config, model="m2_xg")

    assert len(captured_configs) == 1
    assert bt.TEST_SEASON not in captured_configs[0]["history"]["seasons"]
    assert captured_configs[0]["history"]["seasons"] == bt.TRAIN_SEASONS
