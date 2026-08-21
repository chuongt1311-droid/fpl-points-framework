# Pre-registered champion-selection rule (v3 plan §C5 / decision D4)

**Written:** 2026-08-21, before the GW1 deadline (2026-08-21T17:30:00Z) has
even passed — no GW2 data exists yet, which is the whole point. Committing
this rule now, before any live result exists to be tempted by, is what
makes it a pre-registration rather than a post-hoc justification. Do not
edit the rule itself after this date except to fix a genuine drafting
error (record any such edit in `docs/PROJECT_LOG.md`, with a diff, same as
any other change) — weakening it once real GW1-11 results are in would
defeat its purpose.

**Current champion: M0 (`rules_v1`)** — the live production model
(`fpl/project/project.py`'s default path, `model="m0_rules"`), which
already includes v2's shrinkage/calibration/bench-weight. Nothing has
displaced it. Nothing is allowed to, except by clearing every condition
below.

## The rule

```
DECISION POINT: GW12 review (2026-11-06, the GW12 deadline — 11 weeks
after GW1's 2026-08-21 deadline at the plan's weekly cadence)

Switch champion from M0 to challenger X only if ALL of:
  1. X's top-40 rank correlation (fpl.evaluate.backtest.
     compute_top40_rank_correlation — the model's own top-40-by-predicted,
     scored against real outcomes; see plan §C1/§C2 for why this is the
     PRIMARY metric, not overall RMSE) exceeds M0's by a margin whose
     gameweek-clustered 95% CI excludes zero, computed over real GW1-11
     hindsight data (fpl/evaluate/hindsight.py) — NOT the retrospective
     2025-26 backtest, which is a single split with no gameweek axis to
     cluster on (see compute_top40_rank_correlation's docstring).
  2. X wins in >= 8 of the 12 individual gameweeks (GW1-12) on
     points-per-week of the model's recommended XI (decision scorecard,
     plan §C1).
  3. X's calibration curve (fpl.evaluate.backtest calibration_factors) is
     no worse than M0's -- no channel's factor has drifted further from
     1.0 than M0's own equivalent factor.
  4. X's required sources (ProjectionModel.requires_sources) reported
     healthy in >= 10 of 12 gameweeks.
  5. The mechanism by which X wins can be stated in one sentence.

If a model wins on metrics but fails (5), it does not get promoted.
It gets investigated. An unexplained win is an unfound bug -- this
project's own history (shootout contamination, the shot-handling error,
the conceded-direction bug, the Osula rate) is four confirmed instances
of exactly this pattern, every one of them a plausible-looking result
with a real bug underneath.

If no challenger clears the bar: M0 remains champion. That is a valid,
reportable outcome, not a failure of the experiment.
```

## Why GW12 and not sooner

Plan §C3's own arithmetic: at the squad level, one observation per
gameweek and a paired noise SD of ~4-6 points means resolving a ~1.5
pt/GW difference between two decent models at 80% power needs 80+
gameweeks — more than two full seasons. **The squad-level decision
scorecard cannot resolve a close call this season.** What resolves faster
is the player-gameweek level (~600 players x 12 GWs ~ 7,000 paired
observations instead of 12), where gameweek-clustered top-40 rank
correlation differences become detectable by roughly GW8-10. GW12 gives a
small buffer past that and lines up with a natural review cadence (~3
months into the season).

**If the two scorecards disagree before GW12**, the statistical scorecard
(condition 1) is more trustworthy — the decision scorecard (condition 2)
is underpowered this season by construction, not a judgment call to be
second-guessed gameweek to gameweek. Watching weekly points and concluding
anything from it before GW12 is exactly the trap plan §C3 names.

## Current candidates and status (informational, not part of the rule)

| Model | Status | Backtest top-40 rank corr (2025-26 retrospective, NOT a live signal) |
|---|---|---|
| M0 `rules_v1` | **Champion** | 0.444 |
| M2 `xg_blend` | Built, not live-evaluated | 0.471 (also better overall RMSE: 20.663 vs 20.973) |

M2's backtest numbers are directionally favourable but this is a single
retrospective split against a different season (plan's own documented
scope limit — see `fpl/evaluate/backtest.py`'s module docstring) and
carries no gameweek-clustered CI. It is exactly the kind of pre-GW12
signal condition 1 above says not to act on. M2 stays a bakeoff candidate,
not a champion, until real GW1-11 data clears the rule above.

Models not yet built (M3 Understat, M4 Sofascore, M5 ensemble) have no row
here — they can't be evaluated until they exist.
