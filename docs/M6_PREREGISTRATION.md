# M6 (`m6_news`) — pre-registered promotion criteria

**Locked:** 2026-09-09, before M6 produced a single projection.
**Spec:** `docs/superpowers/specs/2026-09-09-c2-news-minutes-signal-design.md`
**Plan:** `docs/superpowers/plans/2026-09-09-c2-news-minutes-signal.md`
**Why this is not in `docs/DECISION_RULE.md`:** `CLAUDE.md` forbids editing
that file outside drafting fixes. This is a separate, additive
pre-registration for one challenger. Whether it graduates into
`DECISION_RULE.md` is the owner's call, made after reading this.

## The rule

Let **W** = the first full gameweek whose deadline falls after the C2
implementation merges to `main`.

**M6's news-aware minutes path is folded into M0** (as a data-quality
improvement, the way the §17 / §20 rolling-start-rate fixes were) **iff,
evaluated once at GW W+6, ALL THREE hold:**

1. **Minutes accuracy.** Over GW W … W+5, on the *news-signal subset*
   (players with a parsed signal that week — where M6 and M0 differ),
   M6's `minutes_factor` has a **strictly lower** Brier score against the
   actual `starts` flag than M0's. Source: `data/output/news_scorecard.json`
   → `window.brier_m6_newsonly < window.brier_m0_newsonly`.

2. **No decision-quality regression.** Captaincy + squad hindsight regret
   (`fpl.evaluate.hindsight`, summed GW W … W+5) under an M6-fed
   optimiser is **no worse** than under M0. (Requires a one-off M6
   optimiser run at evaluation time — not scheduled before then.)

3. **The parser works.** `NewsHealth.parse_failures / distinct_strings
   < 0.2` averaged over the window (from the per-run `[news] {...}` logs).

If any of the three fails, M6 stays **archived-only** or is dropped. The
outcome — either way — is logged in `docs/PROJECT_LOG.md`.

## What is NOT allowed

- No informal promotion before GW W+6, however good an interim number
  looks. An unexplained interim win gets investigated, not shipped
  (condition 5 of the existing decision rule).
- No change to M0's live path, the MILP, the captain logic, or the
  transfer solver before GW W+6.
- No re-tuning of this rule after results start arriving. If it is
  genuinely mis-drafted, the fix is logged as a diff here with its
  rationale, same discipline as `DECISION_RULE.md`.
