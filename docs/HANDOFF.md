# Handoff — FPL Points-Maximization Framework

**Status as of 2026-08-20 (updated, post-hotfix + v2 spec §2/§3/§4):**
Phases 0–4(1/N) done, and most of `docs/FPL_V2_DESIGN.md` ("FPL Framework
v2") landed the same day — see §4a/§4b below for the full breakdown. GW1
deadline is **2026-08-21 17:30 UTC**.

The GW1 hotfix (conceded_pts direction fix + XI/captain split into their
own per-gameweek solve) is applied and verified:
[`data/output/gw1_recommendations.json`](../data/output/gw1_recommendations.json)
is regenerated, captain **Haaland**, vice-captain **Cunha**.
`next_gw_expected_points` (the one-week number, now separate from the
5-GW `horizon_weighted_xpts`) reflects everything below — the hotfix,
shrinkage, and calibration all feed the same live projection.

**v2 spec progress:** §2 (availability snapshot) and §2.0 (weekly
automation) done; §3 (measurement layer — actuals collector, squad state,
hindsight/regret engine, backtest repair, dashboard tab) done, built and
tested against synthetic data since no gameweek has finished yet to
generate real actuals; §4.1/§4.2/§4.4 (shrinkage, calibration, bench
weight) done and tuned against real backtest data; §4.3 (multiplier
collapse warning) was already done in the hotfix. **§5 (learned
availability) is explicitly NOT started** — it needs 10-12 gameweeks of
snapshot history to avoid fitting a model on almost nothing, and there are
currently 2 snapshot rows. Not a scoping choice that more effort fixes;
a calendar-time blocker.

This file is for whoever (human or Claude) picks this up next — it captures
what isn't obvious from reading the code cold: why things are built the way
they are, what's been verified and how, and what to watch out for.

---

## 1. What exists right now

```
fpl/collect/fpl_client.py       live FPL API — bootstrap-static, fixtures, element-summary, event-live
fpl/collect/history_loader.py   vaastav archive — 3 historical seasons
fpl/transform/build_players.py  raw bootstrap -> one row per player
fpl/transform/build_fixtures.py fixture table + DGW/BGW detection
fpl/project/identity.py         player/team id <-> code bridge (READ THIS FIRST — see §3)
fpl/project/baseline.py         per-90 channel rates, recency-weighted, new-signing priors
fpl/project/fixtures.py         3 FDR multipliers (attack/defence/defcon)
fpl/project/minutes.py          availability gate, minutes_factor(p)
fpl/project/defcon.py           DEFCON threshold-crossing rate
fpl/project/project.py          assembles xPts(p,g) for GW+1..GW+5
fpl/decide/optimiser.py         MILP squad + XI + captain (PuLP), two-stage, bench weight (spec §4.4)
fpl/decide/squad_state.py       data/state/squad_gw{n}.json writer — spec §3.2
fpl/evaluate/backtest.py        RMSE/rank-corr backtest, repaired (spec §3.5) + calibration factors (§4.2)
fpl/evaluate/hindsight.py       3 hindsight XIs + regret decomposition — spec §3.3/§3.4, untested-live (no GW finished)
fpl/collect/snapshot.py         availability snapshot writer — spec §2
fpl/collect/actuals.py          per-GW actuals collector — spec §3.1, untested-live (no GW finished)
scripts/build_dashboard_data.py dashboard data-prep (reads pipeline outputs, never recomputes model)
dashboard/                      static, self-contained HTML dashboard — Phase 4 (1/N) + Week in Review tab (spec §3.6)
tests/verify_phase1.py          Phase 1 exit-gate check
tests/test_*.py                 44 pytest tests total — hotfix, snapshot, actuals, squad_state, hindsight,
                                 shrinkage, calibration, bench_weight, backtest_calibration (see tests/)
notebooks/01_profile_research.ipynb   Phase 2 exit-gate deliverable, executed with real outputs baked in
config.yaml                     every tunable constant — read this before touching any module
docs/FPL_V2_DESIGN.md           the v2 spec this session mostly implemented — see §4b below
.github/workflows/weekly.yml    scheduled automation — spec §2.0, built, not yet verified against a live run
```

Not built yet: `fpl/decide/transfers.py`, `fpl/decide/chips.py`,
`fpl/evaluate/evaluate.py` (ongoing per-GW health-tracking, distinct from
the one-off `backtest.py`), and spec §5 (learned availability — see the
top of this file for why). `fpl/evaluate/backtest.py`'s three
previously-faulty behaviours (finding 7/8/9) are now fixed — see §4b.

**Environment gotcha:** this machine's default `python` resolves to an
MSYS2/ucrt64 build with no prebuilt wheels for pandas/numpy (pip tries to
compile from source and fails on an SSL cert issue). The project venv was
built from the native Windows Python instead
(`C:\Users\admin\AppData\Local\Microsoft\WindowsApps\python.exe`). **Always
use `.venv\Scripts\python.exe` explicitly** (via PowerShell, not the Bash
tool's `python`) — e.g. `& ".venv\Scripts\python.exe" -m fpl.project.project`.

## 2. How to reproduce the pipeline end to end

```powershell
& ".venv\Scripts\python.exe" -m fpl.collect.fpl_client        # -> data/raw/bootstrap_static.json, fixtures.json
& ".venv\Scripts\python.exe" -m fpl.collect.history_loader     # -> data/raw/history/{season}/...
& ".venv\Scripts\python.exe" -m fpl.transform.build_players    # -> data/processed/players.parquet
& ".venv\Scripts\python.exe" -m fpl.transform.build_fixtures   # -> data/processed/fixtures.parquet
& ".venv\Scripts\python.exe" -m fpl.decide.optimiser           # runs the whole project+decide chain, writes gw{n}_recommendations.json
& ".venv\Scripts\python.exe" -m fpl.evaluate.backtest          # -> data/output/model_health.json
& ".venv\Scripts\python.exe" tests\verify_phase1.py            # Phase 1 exit-gate check
```

`fpl.decide.optimiser` internally calls `fpl.project.project`, which calls
`fpl.project.baseline` / `defcon` / `minutes`, each of which calls
`fpl.transform.build_players.build_players()` itself rather than being
handed a shared DataFrame — see §5 for why this is a known inefficiency,
not a correctness problem.

## 3. The single most important fact about this codebase

**FPL's `id` field is NOT stable across seasons — `code` is.** Verified
directly against real data: 456 of 461 cross-referenced 2025-26/2026-27
players have a *different* `id` between seasons (code matches by
`web_name` 100% of the time). Same for teams: 12 of 17 cross-referenced
teams have a different `id` (3 relegated — Burnley/West Ham/Wolves; 3
promoted — Coventry City/Hull City/Ipswich Town).

Every join between the current season and historical data goes through
`fpl/project/identity.py`. If you add a new module that touches history,
**use `identity.attach_current_player_id` / `attach_current_team_id` — never
join `element`/`team` id columns directly.** This isn't a style
preference; doing it wrong silently mismatches ~99% of players and the
error is not obviously visible in the output (you get plausible-looking
but wrong numbers, not a crash).

## 4. Bugs found and fixed this session (read before extending the model)

Each is a full commit with the failure scenario documented; this is the
short version so you know what's already been hardened and don't
reintroduce the same class of bug elsewhere:

1. **`code` vs `id`** (§3 above) — `commit 173ba63`, `944f74a`.
2. **Confidence gate only checked zero-vs-nonzero history.** A player with
   literally 1 historical minute got treated as high-confidence with a
   personal per-90 rate built on noise. Fixed with a 450-minute floor
   (`config.new_signing.min_historical_minutes`). `commit 944f74a`.
3. **`bootstrap-static`'s season-cumulative fields don't reset at season
   boundary.** Pre-GW1, `starts`/`minutes` still showed the *previous*
   season's final totals. `appearances_this_season` is now gated on
   `events[].finished` and forced to 0 until a real gameweek has actually
   finished. `commit 944f74a`.
4. **Team strength ratings (`strength_attack_*`/`strength_defence_*`) are
   all 0 pre-season** — not a bug, just unpublished yet. `fixtures.py` now
   falls back per-row to FPL's own 1-5 `difficulty` field
   (log-symmetric mapping) whenever a rating is degenerate. `commit b2ee9e5`.
5. **`minutes_factor` was 1.0 for every healthy player.** A null-handling
   substitution (`chance_of_playing` null → 100) made the branch that's
   supposed to separate a nailed starter from a bench player permanently
   unreachable for ~467/595 players. `commit c2c5c02`.
6. **Backup GKs still outranked their own club's #1** even after fixing
   (5), because the rolling-start-rate signal is backward-looking and
   doesn't know "who's the incumbent at your *current* club" if a keeper
   started regularly at a *previous* club. Fixed with a price-based
   override (`config.minutes.backup_gk_factor`) — FPL's own pricing
   reliably signals the incumbent GK at every club (verified across all 20
   teams). `commit c2c5c02`.
7. **Injured players got phantom ~0.3 appearance points** from the
   `(1-minutes_factor)*bench_cameo_rate` term not distinguishing "healthy
   fringe player" from "definitely can't play." Gated on `status`
   directly. `commit 070b458`.
8. **Budget unit bug**: `budget_tenths / 100` (should be `/10`) made the
   optimiser's effective budget £10m instead of £100m → infeasible MILP.
   `commit 9ad1847`.

**Pattern worth noting:** every one of these was caught by actually running
the code against real data and eyeballing the output (the plan's own "top-20
eye test" gate), not by code reading alone. If you change any of `baseline.py`
/ `minutes.py` / `fixtures.py` / `defcon.py` / `project.py` / `optimiser.py`,
**re-run `fpl.decide.optimiser` and sanity-check the squad it produces**
before trusting it — injured players, backup keepers, and thin-sample
outliers ranking highly are the tell.

## 4a. The GW1 hotfix (applied 2026-08-20, branch `v2/hotfix-and-snapshot`)

Two confirmed correctness bugs, fixed together, both regression-tested:

1. **`project.py` `conceded_pts` used `fixture_defence_mult`** (high = clean
   sheet likely); as a *penalty* term it must move the opposite way. Fixed
   to a new, semantically distinct `fixture_concede_mult` rather than
   reusing `fixture_defcon_mult` — the wrong multiplier read as plausible
   precisely because its name didn't say what it meant (decision log D15 in
   `FPL_V2_DESIGN.md`). This is what §5 finding 1 below refers to — now
   fixed, not open.
2. **`optimiser.py` chose the squad, the XI, and the captain all from the
   same 5-GW decay-weighted number.** Owning a player is a 5-GW decision;
   starting and captaining are re-decided every week. Split into two MILP
   solves — `optimise_squad` picks the 15 on the horizon, the new
   `pick_xi_and_captain` picks XI/captain/vice on `next_gw_xpts` alone.
   Verified effect on the real GW1 squad: vice-captain moved Palmer → Cunha;
   `next_gw_expected_points` (66.46) now reported separately from
   `horizon_weighted_xpts` (227.12) — previously a single ambiguous
   `expected_points` field held the 5-GW number next to `"gameweek": 1`.

`fixtures.py` also now warns when `fixture_attack_mult`/`fixture_defence_mult`
collapse onto the same fallback value (currently 760/760 rows, 100% —
expected pre-season since `strength_*` is unpublished; not a bug, but must
not be silent per spec §4.3).

Both regressions are guarded by `tests/test_hotfix_regressions.py`
(verified to fail against the pre-fix code before this was applied).

## 4b. v2 spec build (2026-08-20, same day, on `main`)

`docs/FPL_V2_DESIGN.md` sub-projects, in the order the spec itself
requires (measurement before statistical changes — you can't tune what
you can't measure):

**§2 Availability snapshot + §2.0 automation** — `fpl/collect/snapshot.py`,
`.github/workflows/weekly.yml`. 2 real rows captured so far
(`data/snapshots/availability_2026-27.csv`). The workflow hasn't had its
first live scheduled run yet — only validated locally.

**§3 Measurement layer** — `fpl/collect/actuals.py` (gated on
`finished AND data_checked`), `fpl/decide/squad_state.py`
(`data/state/squad_gw{n}.json`, wired into `build_gw1_squad`),
`fpl/evaluate/hindsight.py` (three XIs — chosen/best-from-15/best-global —
regret decomposed into captaincy/bench/squad), `fpl/evaluate/backtest.py`
repair (findings 7/8/9, see §5 below), a Week in Review dashboard tab.
**None of this has run against real data yet** — GW1 hasn't finished. Every
piece is unit-tested against synthetic data instead (spec §7.3's own bar
makes that possible: no network, no committed artefacts). Run
`python -m fpl.collect.actuals 1` once GW1 is finished+data_checked, then
`python -m fpl.evaluate.hindsight 1` — that's the first real exercise of
this whole layer, and it's exactly the kind of thing this project's own
culture says to re-verify by running against real data before trusting.

**§4 Statistical core**:
- **§4.1 Shrinkage** replaces the binary confidence cliff outright.
  `fpl/project/baseline.py`'s `shrink_rate`/`shrinkage_weight` are shared
  by `defcon.py` (separate `k_defcon`, matches-based — not backtestable,
  2025-26 is the only DEFCON season) and `backtest.py` (so RMSE reflects
  exactly what shrinkage does). `k=1500`, tuned against the repaired
  backtest — swept k from 50 to 50000; RMSE and rank correlation both kept
  improving all the way to k~20000, which would leave even Haaland
  minority-personal, directly contradicting the spec's own stated
  expectation ("Haaland-class large-sample players barely move") — so
  minimising backtest loss alone was the wrong criterion, and k=1500 was
  chosen instead as the point where most of the real gain is already
  banked (RMSE 21.82->20.97 vs the naive k=450 default) while a genuinely
  nailed player stays majority-personal. **Verified against real players**:
  Haaland w=0.79 (and is literally his own price tier at £15.5m — no FWD
  peer exists that high, so his "prior" equals his own rate by
  construction — a real edge case, not a bug); Osula w=0.44,
  `goals_scored_per90` 0.590->0.491, now below the FWD top-decile
  threshold — **the Osula test passes**, confirmed by hand (a mechanical
  per-channel-quantile scan produces false positives for degenerate
  distributions like GK goal rate, so this was checked by inspection
  instead — see `docs/PROJECT_LOG.md` for the full table).
- **§4.2 Calibration** — `backtest.py` now exposes per-(position,channel)
  `calibration_factors` in `model_health.json`; `project.py`'s
  `apply_calibration` applies them as an explicit final step (every raw
  `*_pts` column stays alongside its `*_pts_cal` counterpart — decision
  D11). Excludes `defcon_pts` (no leak-free backtest season), `card_pts`
  and `appearance_pts` (noisy or deterministic, not something a rate
  predicts).
- **§4.3 Multiplier collapse warning** — already done, in the hotfix.
- **§4.4 Bench weight** — `optimiser.py`'s stage-1 objective now adds
  `config.optimiser.bench_weight_epsilon` (0.02) × every squad member's
  xpts, breaking ties among equally-priced bench candidates without
  touching stage 2's separate XI/captain solve. Not yet tuned against real
  bench regret (spec's own instruction) — no gameweek has finished.

**§5 Learned availability — not started, deliberately.** Needs 10-12
gameweeks of (pre-GW belief -> actual minutes) pairs; there are 2 snapshot
rows right now. Attempting it now would fit a model on almost nothing,
dominated by the healthy-and-nailed majority that needs no help — exactly
what spec §5 itself warns against. This is a calendar-time blocker, not an
effort one.

## 5. Known gaps — verified via a full code review (2026-08-20, /code-review high)

A structured 7-angle review (correctness, removed-behavior, cross-file,
reuse, simplification, efficiency, altitude) plus individual verification
passes on every surviving candidate confirmed **10 real findings**, all
CONFIRMED (not speculative). Ranked most-severe first — **none of these
have been fixed yet**, this is the punch list for whoever picks this up
next. Full detail in each finding's failure scenario (surfaced via
`ReportFindings` in that review session); summary here:

1. ~~`fpl/project/project.py:136` — `conceded_pts` uses the WRONG-DIRECTION
   fixture multiplier.~~ **FIXED 2026-08-20** — see §4a above.
2. ~~`fpl/project/baseline.py:188` — confidence gate's `&` lets a
   thin-history player escape the new-signing fallback.~~ **DISSOLVED
   2026-08-20** by spec §4.1's shrinkage (see §4b below) — the binary gate
   this bug lived in doesn't exist any more, so there's no boundary left
   for a thin-history player to escape through.
3. **`fpl/project/minutes.py:140` — GK backup override never re-promotes
   the backup when the price-designated #1 gets injured.** `status`-driven
   zeroing runs first and correctly zeroes an injured #1's own factor, but
   `apply_gk_backup_override` picks "#1" from a static price ranking with
   no re-check — the actual new starter stays clipped at 0.02. **Still
   open** — spec §5.4 names this as its fallback fix if the (not-yet-built)
   learned availability model's gate fails; until then it needs an
   explicit re-rank-after-zeroing fix.
4. ~~`fpl/project/project.py:79` — DEFCON's confidence flag (`defcon_source`)
   is dropped before reaching the optimiser.~~ **DISSOLVED 2026-08-20** —
   spec §4.1's shrinkage applies to `defcon_rate` too (see §4b), so
   confidence is baked into the rate itself now, nothing separate to drop.
5. **`fpl/project/fixtures.py:80` — `fixtures.parquet` has no auto-build
   path.** Unlike every other input (`build_players`/`baseline`/`defcon`/
   `minutes`, all self-building from raw files), `load_fixture_table()`
   just raises `FileNotFoundError` if `fpl.transform.build_fixtures` was
   never separately run. Breaks on a fresh checkout that only runs the two
   COLLECT scripts.
6. **`fpl/project/minutes.py:87` — hard column selection breaks
   `build_players.py`'s own "keep if present" contract.** If FPL ever omits
   `status` or `chance_of_playing_next_round`, `build_players()` degrades
   gracefully per its docstring, but `minutes.py`'s hard `[[...]]` select
   would `KeyError` instead — the contract is honored on the write side
   only.
7. ~~`fpl/evaluate/backtest.py:148` — zero-fills missing training rates.~~
   **FIXED 2026-08-20** — spec §3.5, see §4b below. Now shrinks toward the
   tier prior (same mechanism as live), exercising the cold-start path on
   210/537 test players instead of predicting a bare 0 for them.
8. ~~`fpl/evaluate/backtest.py:47` — omits `save_pts`/`conceded_pts`
   entirely for GK/DEF.~~ **FIXED 2026-08-20** — spec §3.5. GK's
   mean_predicted moved from 55.36 (well under the actual 64.10) to 65.57
   (essentially matched), confirming this was indeed the main driver of
   GK's under-prediction, as this finding predicted.
9. ~~`fpl/evaluate/backtest.py:90` — the core recency-weighted per-90 rate
   formula is duplicated from `baseline.py`.~~ **FIXED 2026-08-20** — spec
   §3.5. Now imports `load_weighted_player_history`/`compute_player_rates`
   from `baseline.py` directly (restricted to the two training seasons via
   a config override), so a future formula fix propagates automatically.
10. **Unavailable-status set `["i","s","u"]` hardcoded identically in
    `minutes.py:107`, `project.py:119`, `optimiser.py:60`** — no shared
    constant. (Checked against real bootstrap-static data: no additional
    real status value is currently missed by this list — the duplication
    itself is the risk, not a missing status today.)

**Also flagged but not in the top-10** (lower severity / cleanup, still
worth doing): `load_config()` copy-pasted verbatim in 9 files;
`baseline.py`/`defcon.py`'s "personal rate + price-tier-prior fallback"
pattern implemented twice independently; `bootstrap_static.json` and
per-season CSVs re-read redundantly (up to 6-7x per pipeline run) across
baseline/defcon/minutes/fixtures with no shared cache; `minutes.py`'s
`compute_minutes_factor` branch logic built via four chained
double-negated `.where()` calls (a `np.select` would be far less
error-prone — this exact pattern is how the null-substitution bug in §4
item 5 happened here once already);
`optimiser.py`'s eligibility filter (`confidence != 'low'` + status
exclusion) is inlined rather than a reusable `eligible_player_pool()` that
`transfers.py`/`chips.py` will also need.

- **Stray debug files** (`gk_check.csv`, `top20_check.csv`,
  `top5gk_check.csv`) got swept into commits by `git add -A` during
  investigation — removed and `.claude/` (agent worktree scratch space)
  added to `.gitignore`.
- **Backtest RMSE has real, documented scope limits that are UNCHANGED by
  the §3.5 repair or §4.1 shrinkage** (both improved the NUMBERS, not the
  scope): single retrospective split (not full walk-forward), no fixture
  adjustment, and DEFCON entirely excluded (2025-26 is the only
  DEFCON-scored season, so there's no leak-free prior season to train it
  from). Current numbers (post repair + shrinkage k=1500): overall RMSE
  20.97 (was 25.43), rank correlation 0.947-0.967 across every position
  (was 0.90-0.93). See `fpl/evaluate/backtest.py`'s module docstring and
  `data/output/model_health.json` for the full breakdown, including
  per-(position,channel) calibration factors (spec §4.2).
- **`fpl/project/defcon.py` uses the player's CURRENT position** to decide
  the DEFCON threshold when reading historical `defensive_contribution`
  values, not their position AT THE TIME the raw stat was recorded.
  Documented as an approximation in the code (a rare edge case — DEF vs
  MID/FWD reclassification between seasons). Cheap to fix properly if it
  ever matters (thresholds keyed by historical position instead).

## 6. What Phase 4+ needs to know

- The dashboard (plan §7) must **read `data/output/*.json` and
  `data/projections/*.parquet` — never recompute.** That contract is
  already respected by `optimiser.py` (writes JSON) and `project.py`
  (writes parquet); don't break it by having the dashboard import and call
  the model directly.
- Chip logic (`chips.py`, not built) needs DGW/BGW detection, which
  already works (`fpl/transform/build_fixtures.py`, verified against a
  real historical DGW — 2023-24 GW7). GW1-5 currently shows 0 DGWs/BGWs
  (expected — pre-season).
- `transfers.py` (not built) needs the `−4 hit` buffer logic from plan
  §6.2 — `config.transfers.buffer` (1.5) is a placeholder pending real
  backtest-derived RMSE; §6.2 says to re-tune it once RMSE is known, which
  it now is (see §5 above) but hasn't been re-tuned yet.
- GitHub Actions (`fpl/evaluate` step in the weekly job) should call a new
  `fpl/evaluate/evaluate.py` (ongoing, per-GW) — distinct from
  `backtest.py` (one-off, retrospective). Don't conflate the two.

## 7. Config reference

Every tunable lives in `config.yaml` — the comments there explain the
*why* for anything non-obvious (recency decay, price-tier width, the GK
backup override, the bench-cameo rate). If a number in the model looks
wrong, check there first before assuming a code bug.

## 8. The plan itself

`docs/FPL_EXECUTION_PLAN.md` is the locked spec everything here follows —
section numbers referenced throughout this file and the code's comments
(e.g. "plan §4.2") point back into it.

`docs/FPL_V2_DESIGN.md` is the follow-on spec (approved 2026-08-20) for
what comes after Phase 4: an availability snapshot (irreversible, started
this update — `data/snapshots/availability_2026-27.csv` has its first row),
a measurement layer (regret decomposition replacing RMSE as the primary
metric), then a statistical core (shrinkage, calibration), then a learned
availability model. **The first two are done as of this update** (§4b) —
only the last item remains, and it can't start for 10-12 gameweeks
(needs that much snapshot history). Section refs written as "spec §x"
throughout this file and the code's comments point into it.

## 9. Not yet done

- **Rotate the Bzzoiro API token.** It's in plaintext in a circulated
  `CHAT_HANDOFF.md` (not in this repo, but in a document that's been
  shared around) — spec §1.4. This needs to happen on the token-issuing
  service directly; nobody picking up this repo can do it from the code.
  **Still not done — do this first, independent of everything else.**
- ~~`.github/workflows/weekly.yml` (spec §2.0)~~ **Built.** Runs the full
  collect→snapshot→transform→decide→dashboard chain on the spec's 4 cron
  touchpoints (Tue/Fri/Sat AM/Sat PM UTC) plus manual dispatch, then commits
  regenerated artefacts back to `main` (`fpl-pipeline-bot`). Deliberately
  excludes `fpl.evaluate.backtest` — that's a Phase 3 one-off retrospective
  deliverable, not the ongoing per-GW `evaluate.py` the plan describes
  (which doesn't exist yet). **Not yet verified live** — needs this repo
  pushed to GitHub with Actions enabled before its first real scheduled
  run; only validated locally (YAML parses, each step runs standalone).
- **Everything in §3 (actuals/squad_state/hindsight) and §4
  (shrinkage/calibration/bench weight) is built and unit-tested but has
  never run against real data** — no gameweek has finished. The first
  real exercise: once GW1 finishes and `data_checked` flips,
  `python -m fpl.collect.actuals 1` then `python -m fpl.evaluate.hindsight 1`.
  Sanity-check the regret numbers by hand before trusting them (spec §3.7's
  own exit gate: "a hand-checked gameweek matches manual calculation").
- **Bench weight epsilon (0.02) and shrinkage k (1500)** are both real,
  data-informed choices but neither is validated against LIVE regret yet
  (only the backtest, for k). Revisit once a few gameweeks of hindsight
  data exist.
- **§5 (learned availability)** — blocked on calendar time, see top of
  this file and §4b.
- **`fpl/decide/transfers.py`, `fpl/decide/chips.py`,
  `fpl/evaluate/evaluate.py`** — still unbuilt, spec §6 explicitly out of
  scope for v2. `squad_state.py` was deliberately shaped to support
  `transfers.py` when it's built.
- **Handoff findings #3, #5, #6, #10** (GK backup re-promotion, no
  fixtures.parquet auto-build path, minutes.py's hard column select,
  hardcoded unavailable-status set in 3 files) remain open — see §5.
