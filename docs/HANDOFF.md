# Handoff — FPL Points-Maximization Framework

**Status as of 2026-08-20:** Phases 0–3 complete and pushed to
[github.com/chuongt1311-droid/fpl-points-framework](https://github.com/chuongt1311-droid/fpl-points-framework)
(`main`, commit `7b1480d`). GW1 deadline is **2026-08-21 17:30 UTC** — the
squad this pipeline currently recommends is in
[`data/output/gw1_recommendations.json`](../data/output/gw1_recommendations.json)
and is ready to submit. Phases 4–6 (dashboard, automation, validation) are
not started; nothing about them blocks the deadline (see plan §9).

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
fpl/decide/optimiser.py         MILP squad + XI + captain (PuLP)
fpl/evaluate/backtest.py        RMSE/rank-corr backtest vs 2025-26 actuals
tests/verify_phase1.py          Phase 1 exit-gate check
notebooks/01_profile_research.ipynb   Phase 2 exit-gate deliverable, executed with real outputs baked in
config.yaml                     every tunable constant — read this before touching any module
```

Not built yet: `fpl/decide/transfers.py`, `fpl/decide/chips.py`,
`dashboard/`, `.github/workflows/weekly.yml`. `fpl/evaluate/backtest.py`
exists (Phase 3's one-off exit-gate deliverable); the plan's `evaluate.py`
(the ongoing per-GW health-tracking script referenced in §8's automation
step 6) does not — that's Phase 5 scope.

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

## 5. Known gaps — verified via a full code review (2026-08-20, /code-review high)

A structured 7-angle review (correctness, removed-behavior, cross-file,
reuse, simplification, efficiency, altitude) plus individual verification
passes on every surviving candidate confirmed **10 real findings**, all
CONFIRMED (not speculative). Ranked most-severe first — **none of these
have been fixed yet**, this is the punch list for whoever picks this up
next. Full detail in each finding's failure scenario (surfaced via
`ReportFindings` in that review session); summary here:

1. **`fpl/project/project.py:136` — `conceded_pts` uses the WRONG-DIRECTION
   fixture multiplier.** Uses `fixture_defence_mult` (high = easy fixture),
   same as `cleansheet_pts`, but as a *penalty* term this should move the
   opposite way — `fixture_defcon_mult` (`1/fixture_defence_mult`, already
   computed one line away) is correct. Every GK/DEF projection's
   goals-conceded penalty currently points backwards relative to fixture
   difficulty. **Highest-priority fix — affects every live projection.**
2. **`fpl/project/baseline.py:188` — confidence gate's `&` lets a
   thin-history player escape the new-signing fallback** once
   `appearances_this_season >= 3`, even with NaN per-90 rates (which
   silently become 0 by the time they reach `weighted_xpts`, via
   `groupby().sum()`'s default `skipna=True` — not a crash, just an
   invisible "this player is worth 0 points"). Currently dormant (pre-season
   appearances are forced to 0 by a different, already-fixed bug) but will
   activate the moment any rookie gets 3 real 2026-27 appearances.
3. **`fpl/project/minutes.py:140` — GK backup override never re-promotes
   the backup when the price-designated #1 gets injured.** `status`-driven
   zeroing runs first and correctly zeroes an injured #1's own factor, but
   `apply_gk_backup_override` picks "#1" from a static price ranking with
   no re-check — the actual new starter stays clipped at 0.02.
4. **`fpl/project/project.py:79` — DEFCON's confidence flag (`defcon_source`)
   is dropped before reaching the optimiser.** A player can be
   `confidence='high'` overall while their DEFCON rate specifically is a
   tier-prior guess (<10 DEFCON-season matches) — the plan §3.3 "don't
   confidently recommend a low-data player" rule doesn't cover this channel.
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
7. **`fpl/evaluate/backtest.py:148` — zero-fills missing training rates**
   instead of the position/price-tier prior the live model uses for
   exactly this population (cold-start players) — undocumented, and it
   means the backtest never actually exercises the new-signing fallback
   path it should validate.
8. **`fpl/evaluate/backtest.py:47` — omits `save_pts`/`conceded_pts`
   entirely for GK/DEF**, unlike the live model. Undocumented; plausibly
   contributes to GK showing the second-worst under-prediction ratio
   (86.4%) in the actual backtest output.
9. **`fpl/evaluate/backtest.py:90` — the core recency-weighted per-90 rate
   formula is duplicated from `baseline.py`'s `compute_player_rates()`**
   almost line-for-line (plus a second `_season_weight()`). A future fix to
   the live formula won't propagate here — the backtest would silently stop
   validating what production actually does.
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
- **Backtest RMSE (Phase 3) has real, documented scope limits** beyond the
  two gaps above: single retrospective split (not full walk-forward), no
  fixture adjustment, and DEFCON entirely excluded (2025-26 is the only
  DEFCON-scored season, so there's no leak-free prior season to train it
  from). RMSE 20-31 by position, systematic ~15% under-prediction — but
  rank correlation 0.90-0.93 across every position. See
  `fpl/evaluate/backtest.py`'s module docstring and
  `data/output/model_health.json` for the full numbers.
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
