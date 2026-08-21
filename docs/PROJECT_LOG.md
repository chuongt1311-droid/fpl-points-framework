# Project Log — FPL Points-Maximization Framework

**Purpose of this file:** a complete, chronological record of every phase,
commit, bug, and design decision made on this project, for later review.
Unlike [`docs/HANDOFF.md`](HANDOFF.md) (forward-looking — what the next
person needs to know to continue), this file is backward-looking — what
actually happened, in order, with commit hashes, so any claim here can be
verified with `git show <hash>`.

Repo: [github.com/chuongt1311-droid/fpl-points-framework](https://github.com/chuongt1311-droid/fpl-points-framework)
Spec: [`docs/FPL_EXECUTION_PLAN.md`](FPL_EXECUTION_PLAN.md) — the locked plan every phase below implements. Section refs (e.g. "§4.2") point into it.

---

## 0. What the project is

A rules-based (not ML) points-projection and squad-optimisation pipeline
for Fantasy Premier League, 2026-27 season. Pulls live FPL data + 3
seasons of historical archive, projects each player's expected points for
the next 5 gameweeks from first-principles scoring-rule components
(appearance, goals, assists, clean sheets, DEFCON, saves, bonus, cards),
adjusts by fixture difficulty, and runs a MILP optimiser to pick a
budget-legal 15-man squad, starting XI, and captain. Phase 9 of the plan
(§9) defines 7 phases; phases 0–4 are done as of this log.

---

## 1. Timeline

### Phase 0 — Scaffold
- `45042a7` (2026-08-19) **Phase 0: repo scaffold** — `.gitignore`,
  `config.yaml` (every tunable constant), `docs/FPL_EXECUTION_PLAN.md`
  (405 lines, the locked spec), `requirements.txt`, empty package
  structure (`fpl/{collect,transform,project,decide,evaluate}/__init__.py`).
- `8bce492` (2026-08-20) Initial commit — README line only (repo init
  artifact, predates the real scaffold commit landing).

### Phase 1 — Data layer
- `60e4c44` **Phase 1: data layer** —
  - `fpl/collect/fpl_client.py` — live FPL API client: bootstrap-static, fixtures, element-summary, event-live.
  - `fpl/collect/history_loader.py` — pulls 3 historical seasons from the vaastav/Fantasy-Premier-League archive.
  - `fpl/transform/build_players.py` — raw bootstrap → one row per player.
  - `fpl/transform/build_fixtures.py` — fixture table + DGW/BGW (Double/Blank Gameweek) detection.
  - `tests/verify_phase1.py` — the Phase 1 exit gate, two parts:
    - **Part A** (live pull): confirms one row per player with every required field, no nulls in critical columns, row count matches `bootstrap-static`'s element count.
    - **Part B** (historical check): finds a real Double Gameweek in the archive (walks back through seasons until one is found) and cross-checks the DGW/BGW detector's flagged-fixture count against a raw count from the same data — verified against 2023-24 GW7.
  - Data artifacts written: `data/processed/{players,fixtures,dgw_bgw_grid}.parquet`.
- `a23047a` **fix: correct season config** — `config.yaml`'s `season` was
  set to `2025-26`; corrected to `2026-27` (the actual upcoming season,
  GW1 ~48h away at the time). One-line config bug, caught before it
  propagated anywhere load-bearing.

### Phase 2 — Research
- `45b2b26` **Phase 2: profile research notebook — exit gate PASS** —
  `notebooks/01_profile_research.ipynb` (591 lines, executed with real
  outputs baked in, not just cell source). Analyses 3 seasons of history
  to find which channel combinations (goals/assists/CS/bonus/DEFCON)
  actually produce high points-per-90 by position, including the DEFCON
  threshold-rate analysis §4.2 depends on. Per the plan, this step could
  have falsified the whole approach — it didn't; the profile findings
  fed directly into `position_multipliers` and the DEFCON thresholds in
  `config.yaml`.

### Phase 3 — Projection model + optimiser + backtest
This is the largest phase — the actual xPts(p, g) formula from plan §4.1
and the MILP squad optimiser, built and hardened incrementally with real
bugs caught by running against live data (see §2 below for the bug
list — every one of these has a commit here).

- `173ba63` **fix: carry stable player `code` through `build_players`** —
  prerequisite for Phase 3: FPL's `id` is not stable across seasons,
  `code` is (see §3 of `HANDOFF.md`). Needed before any historical join
  could be written correctly.
- `944f74a` **Phase 3 (1/N): `baseline.py` + `identity.py`** — per-90
  channel rates, recency-weighted across seasons, with a new-signing
  prior fallback for cold-start players. `identity.py` is the id/code
  bridge every cross-season join must go through. Bundled with this
  commit: the historical-minutes confidence floor (450 min) and the
  season-boundary appearance-count fix (bugs #2 and #3, see §2).
- `479edd3` **Phase 3 (2/N): `minutes.py`** — availability gate,
  `minutes_factor(p)` — P(60+ minutes), per plan §4.2.
- `b2ee9e5` **Phase 3 (3/N): `fixtures.py`** — the three FDR multipliers
  (attack/defence/DEFCON), with the strength-rating→FPL-difficulty
  fallback for pre-season (bug #4, see §2) landing in the same commit.
- `be067ce` **Phase 3 (4/N): `defcon.py`** — DEFCON threshold-crossing
  rate for current players (CBIT for defenders, CBIRT for MID/FWD, per
  `config.yaml`'s `defcon` block).
- `c2c5c02` **Phase 3 (5/N): `project.py`** — assembles xPts(p, g) for
  GW+1..GW+5 per the plan §4.1 formula, decay-weighted across the
  horizon. Bundled: the minutes.py null-substitution fix and the GK
  backup-price override (bugs #5 and #6, see §2). First `data/projections/gw1.parquet`
  written here. (This commit also briefly included three ad-hoc debug
  CSVs — `gk_check.csv`, `top20_check.csv`, `top5gk_check.csv` — swept in
  by `git add -A` during investigation; removed in `324a38c`.)
- `070b458` **fix: injured players were getting phantom ~0.3 appearance points**
  (bug #7, see §2) — narrow, isolated fix to `project.py`.
- `9ad1847` **Phase 3 (6/N): `optimiser.py`** — MILP squad + starting XI
  + captain selection (PuLP). Fixed the budget-unit bug in the same
  commit (bug #8, see §2) — without it the optimiser was infeasible.
  First `data/output/gw1_recommendations.json` written here — **this is
  the GW1 squad the plan's deadline requirement was actually asking for.**
- `7b1480d` **Phase 3 (7/N): `backtest.py` — exit gate PASS** — RMSE and
  rank correlation vs 2025-26 actuals, by position. `identity.py` extended
  (18 lines) to support this. First `data/output/model_health.json`
  written here. Result: RMSE 20-31 pts by position, ~15% systematic
  under-prediction, but rank correlation 0.90-0.93 across every position
  — the model ranks players well even where its point totals run low.

### Interim — documentation + review + cleanup
- `324a38c` **docs: add handoff file; record code-review findings; repo cleanup** —
  `docs/HANDOFF.md` added (263 lines — captures everything not obvious
  from reading the code cold: the `code`-vs-`id` gotcha, the environment
  Python gotcha, the full bug list, and a 10-item ranked punch list from
  a structured `/code-review high` pass — see §3 below). Stray debug CSVs
  from `c2c5c02` deleted. `.claude/` (agent worktree scratch space) added
  to `.gitignore`.

### Phase 4 — Dashboard
- `e5fa065` **Phase 4 (1/N): static dashboard** — see §4 below for full detail.

---

## 2. Bugs found and fixed (chronological, with root cause)

Every one of these was caught by running the code against real data and
eyeballing the output — the plan's own "top-20 eye test" gate — not by
code reading alone.

| # | Commit | File | Root cause | Effect before fix |
|---|---|---|---|---|
| 1 | `173ba63`, `944f74a` | `build_players.py`, `identity.py` | FPL's `id` field is not stable across seasons — `code` is. 456/461 cross-referenced 2025-26/2026-27 players have a different `id`; same for 12/17 cross-referenced teams (promotion/relegation). | Any historical join done on raw `id` silently mismatches ~99% of players — plausible-looking but wrong numbers, not a crash. |
| 2 | `944f74a` | `baseline.py` | Confidence gate only checked zero-vs-nonzero history. | A player with 1 historical minute was treated as high-confidence, with a personal per-90 rate built on noise. Fixed with a 450-minute floor (`config.new_signing.min_historical_minutes`). |
| 3 | `944f74a` | `build_players.py` | `bootstrap-static`'s season-cumulative fields (`starts`/`minutes`) don't reset at the season boundary. | Pre-GW1, these fields still showed the *previous* season's final totals. Fixed by gating `appearances_this_season` on `events[].finished`, forced to 0 until a real gameweek finishes. |
| 4 | `b2ee9e5` | `fixtures.py` | `strength_attack_*`/`strength_defence_*` are all 0 pre-season (unpublished, not a data bug). | Fixture multipliers would be degenerate. Fixed with a per-row fallback to FPL's own 1-5 `difficulty` field (log-symmetric mapping) whenever a rating is 0. |
| 5 | `c2c5c02` | `minutes.py` | Null-handling substitution (`chance_of_playing` null → 100) made the branch separating a nailed starter from a bench player permanently unreachable. | `minutes_factor` was 1.0 for every healthy player (~467/595 players affected). |
| 6 | `c2c5c02` | `minutes.py` | The rolling-start-rate signal is backward-looking and has no notion of "current club incumbent" for a keeper who started elsewhere previously. | Backup GKs still outranked their own club's #1 even after fix #5. Fixed with a price-based override (`config.minutes.backup_gk_factor`) — verified FPL's own pricing correctly signals the incumbent GK across all 20 teams. |
| 7 | `070b458` | `project.py` | The `(1-minutes_factor)*bench_cameo_rate` appearance term didn't distinguish "healthy fringe player" from "definitely can't play." | Injured players got phantom ~0.3 appearance points. Fixed by gating on `status` directly. |
| 8 | `9ad1847` | `optimiser.py` | `budget_tenths / 100` instead of `/10`. | Optimiser's effective budget was £10m instead of £100m → infeasible MILP (no valid squad could be found). |

## 3. Known gaps — from the structured code review (2026-08-20, `/code-review high`)

A 7-angle review (correctness, removed-behavior, cross-file, reuse,
simplification, efficiency, altitude) plus individual verification passes
confirmed **10 real findings, all CONFIRMED**, ranked most-severe first.
**None of these are fixed as of this log** — full detail lives in
`docs/HANDOFF.md` §5; summary:

1. **`project.py:136`** — `conceded_pts` uses the wrong-direction fixture
   multiplier (`fixture_defence_mult` instead of `fixture_defcon_mult`).
   Every GK/DEF projection's goals-conceded penalty currently points
   backwards relative to fixture difficulty. **Highest-priority open bug —
   affects every live projection.**
2. **`baseline.py:188`** — confidence gate's `&` lets a thin-history
   player escape the new-signing fallback once `appearances_this_season >= 3`,
   even with NaN per-90 rates that silently become 0. Currently dormant
   (pre-season appearances are forced to 0), activates once any rookie
   gets 3 real 2026-27 appearances.
3. **`minutes.py:140`** — GK backup override never re-promotes the
   backup when the price-designated #1 gets injured (static price
   ranking, no re-check).
4. **`project.py:79`** — DEFCON's confidence flag (`defcon_source`) is
   dropped before reaching the optimiser — a player can be
   `confidence='high'` overall while their DEFCON rate specifically is a
   tier-prior guess.
5. **`fixtures.py:80`** — `fixtures.parquet` has no auto-build path;
   `load_fixture_table()` raises `FileNotFoundError` on a fresh checkout
   that only ran the two COLLECT scripts.
6. **`minutes.py:87`** — hard column selection breaks `build_players.py`'s
   own "keep if present" degrade-gracefully contract.
7. **`backtest.py:148`** — zero-fills missing training rates instead of
   the position/price-tier prior the live model uses — the backtest
   never exercises the new-signing fallback path it should validate.
8. **`backtest.py:47`** — omits `save_pts`/`conceded_pts` entirely for
   GK/DEF, unlike the live model; plausibly contributes to GK's
   second-worst under-prediction ratio (86.4%) in the actual backtest.
9. **`backtest.py:90`** — the recency-weighted per-90 rate formula is
   duplicated from `baseline.py`'s `compute_player_rates()` almost
   line-for-line — a future formula fix won't propagate here.
10. **Unavailable-status set `["i","s","u"]`** hardcoded identically in
    3 files (`minutes.py`, `project.py`, `optimiser.py`) with no shared
    constant.

**Also flagged, lower severity:** `load_config()` copy-pasted in 9 files;
the "personal rate + price-tier-prior fallback" pattern implemented
twice independently (`baseline.py`/`defcon.py`); `bootstrap_static.json`
and per-season CSVs re-read redundantly up to 6-7x per pipeline run with
no shared cache; `minutes.py`'s branch logic built via four chained
double-negated `.where()` calls (the exact pattern that caused bug #5
above — an `np.select` would be safer); `optimiser.py`'s eligibility
filter inlined rather than a reusable `eligible_player_pool()` function
that `transfers.py`/`chips.py` will also need.

**Backtest scope limits** (documented, not bugs): single retrospective
split (not full walk-forward), no fixture adjustment applied, DEFCON
entirely excluded (2025-26 is the only DEFCON-scored season — no
leak-free prior season exists to train it from).

**Also documented:** `defcon.py` uses a player's *current* position to
read historical `defensive_contribution` thresholds, not their position
*at the time* the stat was recorded — a rare DEF↔MID/FWD reclassification
edge case, cheap to fix if it ever matters.

## 4. Phase 4 — Dashboard, in detail

Built 2026-08-20 in response to a follow-up session. Implements plan
§7's 5 views (This Week, Player Explorer, Fixture Radar, Chip Planner,
Model Health) using the plan's exact design tokens (dusk/floodlight
palette, Archivo Narrow + Inter + JetBrains Mono type stack, the Fixture
Ticker signature element with DEF dual-state clean-sheet/DEFCON split).

**Architecture decision:** the plan (§7.1, §11) specifies a Streamlit app
(`dashboard/app.py`). Built as a static, self-contained HTML file instead
— no server, opens offline in any browser — because this is explicitly a
point-in-time snapshot view (plan's own description of "This Week").
Documented as a deliberate deviation in `dashboard/README.md`, with the
condition under which Streamlit would become worth it (editable,
session-persisted chip state from Phase 5 automation onward).

**Data contract preserved:** the dashboard reads `data/output/*.json`
and `data/processed|projections/*.parquet` — it does not recompute the
model. The one exception (a read, not a recompute): the per-channel
points breakdown (goals/assists/clean-sheets/DEFCON/bonus — powers the
Player Explorer's "why is the model recommending this guy" row-expand)
isn't a saved pipeline artifact anywhere, so `scripts/build_dashboard_data.py`
calls `fpl.project.project`'s own `build_player_inputs` /
`compute_channel_pts_per_fixture` functions directly — the same code
path production uses, not reimplemented.

**Honesty choices, deliberate:**
- Chip Planner has no live recommendations (`fpl/decide/chips.py` isn't
  built) — labelled "not yet computed," shows only the config thresholds
  it will use once it exists.
- This Week's transfer line is hardcoded to "initial squad, no transfer
  to evaluate" (`fpl/decide/transfers.py` isn't built either).
- Model Health's known-limitations list includes the plan's own §10 v1
  limitations **plus** two still-open items from the §3 punch list above
  (finding #1, the `conceded_pts` wrong-direction multiplier; finding #4,
  DEFCON confidence not surfaced) — flagged in red — because both
  currently affect the live GW1 projections this same dashboard displays.

**Files:**
- `dashboard/template.html` — source (CSS/JS + `/*__DATA__*/` placeholder). Edit this.
- `scripts/build_dashboard_data.py` — data-prep: reads pipeline outputs, computes the channel breakdown, writes `dashboard/data.json`, then stitches it into `dashboard/index.html`.
- `dashboard/data.json` — the payload alone (797 KB), for inspection.
- `dashboard/index.html` — **generated**, fully offline-capable (830 KB). Don't hand-edit.
- `dashboard/README.md` — regeneration command + the Streamlit deviation rationale.

**Verified:** live in-browser via a local preview server — all 5 tabs,
filters (position/club/price/confidence/availability/search), sortable
columns, row-expand channel breakdown, Fixture Radar's 3-mode toggle,
DEF dual-state ticker cells, deadline countdown. No console errors.

Commit: `e5fa065`, pushed to `main` same session.

---

## 5. Current module inventory (as of `e5fa065`)

```
fpl/collect/fpl_client.py       live FPL API — bootstrap-static, fixtures, element-summary, event-live
fpl/collect/history_loader.py   vaastav archive — 3 historical seasons
fpl/transform/build_players.py  raw bootstrap -> one row per player
fpl/transform/build_fixtures.py fixture table + DGW/BGW detection
fpl/project/identity.py         player/team id <-> code bridge — read first, see §2 row 1
fpl/project/baseline.py         per-90 channel rates, recency-weighted, new-signing priors
fpl/project/fixtures.py         3 FDR multipliers (attack/defence/defcon)
fpl/project/minutes.py          availability gate, minutes_factor(p)
fpl/project/defcon.py           DEFCON threshold-crossing rate
fpl/project/project.py          assembles xPts(p,g) per plan §4.1, GW1-5
fpl/decide/optimiser.py         MILP squad + XI + captain (PuLP)
fpl/evaluate/backtest.py        RMSE/rank-corr backtest vs 2025-26 actuals
scripts/build_dashboard_data.py dashboard data-prep (reads pipeline outputs, never recomputes model)
dashboard/template.html         dashboard source
dashboard/index.html            dashboard, generated, self-contained
tests/verify_phase1.py          Phase 1 exit-gate check
notebooks/01_profile_research.ipynb   Phase 2 exit-gate deliverable
config.yaml                     every tunable constant
docs/FPL_EXECUTION_PLAN.md      the locked spec
docs/HANDOFF.md                 forward-looking handoff (what's next, what to watch out for)
docs/PROJECT_LOG.md             this file — backward-looking record
```

**Not built yet:** `fpl/decide/transfers.py`, `fpl/decide/chips.py`,
`fpl/evaluate/evaluate.py` (the ongoing per-GW health-tracker distinct from
the one-off `backtest.py`). `.github/workflows/weekly.yml` — see §9 — is
now built. These are Phase 5+ / `FPL_V2_DESIGN.md` scope — nothing about
them blocked the GW1 deadline.

## 6. Environment note

This machine's default `python` resolves to an MSYS2/ucrt64 build with no
prebuilt wheels for pandas/numpy. The project venv was built from the
native Windows Python instead. Always invoke
`.venv\Scripts\python.exe` explicitly (PowerShell, not the Bash tool's
`python`) — e.g. `& ".venv\Scripts\python.exe" -m fpl.project.project`.
Scripts under `scripts/` need `PYTHONPATH=.` set explicitly since they
live outside the `fpl` package (e.g.
`$env:PYTHONPATH="."; & ".venv\Scripts\python.exe" scripts\build_dashboard_data.py`).

## 7. GW1 result (for the record)

**Superseded by the hotfix below — kept for the record, not current.** As
of `e5fa065`, `data/output/gw1_recommendations.json` recommended a 3-4-3
squad, £100.0m spend (full budget used), captain **Haaland**, vice
**Palmer**, with a single `expected_points: 235.22` field that was silently
the 5-GW decay-weighted sum sitting next to `"gameweek": 1`, not a one-week
forecast. GW1 deadline: **2026-08-21 17:30 UTC**. Per plan §10 limitation
8: this is explicitly *the weakest output the model will ever produce* —
least data, most reliance on FDR fallback and priors — a systematic
approach beats guessing, but an early wildcard should be expected.

## 8. GW1 hotfix + availability snapshot (2026-08-20, branch `v2/hotfix-and-snapshot`)

Applied `gw1-hotfix.patch` (two confirmed correctness bugs — see
`docs/HANDOFF.md` §4a for full detail):

1. `conceded_pts` was using the wrong-direction fixture multiplier
   (`fixture_defence_mult` instead of a new `fixture_concede_mult`) —
   every GK/DEF projection's goals-conceded penalty pointed backwards
   relative to fixture difficulty.
2. `optimiser.py` picked the squad, XI, and captain all from one 5-GW
   decay-weighted number. Split into two MILP solves: `optimise_squad`
   picks the 15 on the horizon; the new `pick_xi_and_captain` picks
   XI/captain/vice from `next_gw_xpts` alone, re-decided every gameweek.

`tests/test_hotfix_regressions.py` — the first pytest suite in the repo —
added with the patch; all 5 pass, verified to fail against the pre-fix code.

Re-ran the full chain end to end afterward and regenerated
`gw1_recommendations.json`: **captain Haaland, vice-captain Cunha** (moved
from Palmer), `next_gw_expected_points: 66.46`, `horizon_weighted_xpts:
227.12` — reported as two distinct fields now, not one ambiguous number.

Also built `fpl/collect/snapshot.py` (`FPL_V2_DESIGN.md` spec §2) —
append-only availability history, since `players.parquet` is overwritten
every run and every un-snapshotted gameweek is a training row that can
never be recovered later. Ran it once manually:
`data/snapshots/availability_2026-27.csv`, 599 rows, 113 flagged players,
`hours_to_deadline: 25.67` at capture time. `tests/test_snapshot.py` (6
tests, synthetic bootstrap fixture, no network) covers the exit-gate
requirements: differing `hours_to_deadline` across runs, no overwrite, no
duplicate row on a retried `run_id`.

Merged `v2/hotfix-and-snapshot` into `main` (fast-forward, `acf0be1`).

## 9. Dashboard fix + weekly automation + push (2026-08-20, same session)

Two bugs surfaced while building on top of the hotfix, both fixed
immediately rather than left for later since they were caused by work
already done this session:

1. `dashboard/template.html`'s This Week KPI tile still read
   `sq.expected_points`, a field the hotfix renamed away —
   `next_gw_expected_points` is undefined-safe now, and the tile's
   sub-label (which described the old, buggy semantics) was corrected too.
   `scripts/build_dashboard_data.py`'s `known_limitations` list also still
   claimed the conceded_pts bug was unfixed; removed, replaced with the
   still-open shrinkage gap. Regenerated `dashboard/data.json` +
   `index.html`; verified in-browser (local static server), no console
   errors, KPI tile reads 66.5. Commit `f8ed96e`.
2. `fpl/collect/snapshot.py`'s `__main__` was calling `get_bootstrap_static()`
   a second time, independent of whatever `fpl.collect.fpl_client`'s own
   run in the same job just pulled — wasteful, and risked a different
   bootstrap snapshot than the rest of that pipeline run used. Fixed to
   read the cached `data/raw/bootstrap_static.json`, same pattern as
   `build_players._load_bootstrap()`. The re-run this produced added a
   second real row to `availability_2026-27.csv` (1198 rows, 2 run_ids,
   `hours_to_deadline` 25.67 -> 25.53) — the snapshot's exit gate (spec
   §2.4: two runs, differing `hours_to_deadline`, nothing overwritten) is
   now genuinely satisfied by real data, not just by
   `tests/test_snapshot.py`'s synthetic fixture. Commit `32b6c54`.

Built `.github/workflows/weekly.yml` (`FPL_V2_DESIGN.md` spec §2.0) —
4 scheduled touchpoints (Tue/Fri/Sat-AM/Sat-PM UTC per the spec's table)
plus manual dispatch, running collect -> snapshot -> transform -> decide ->
dashboard-data and committing regenerated artefacts back to `main` under a
`fpl-pipeline-bot` identity. Deliberately excludes `fpl.evaluate.backtest`
(Phase 3 one-off retrospective, not the ongoing per-GW `evaluate.py` the
plan describes, which isn't built). YAML validated locally; **not yet
verified against a live scheduled run** — that only happens once Actions
picks it up on GitHub. Commit `46038cc`.

Pushed `main` to `origin` (`e5fa065..46038cc`, 5 commits) — the repo the
`weekly.yml` schedule depends on now has the workflow file live.

**Not done this part of the session** (deliberately out of scope for a
single sitting, flagged in `docs/HANDOFF.md` §9): rotating the leaked
Bzzoiro token, and the rest of `FPL_V2_DESIGN.md`. Both continued later
the same day — see §10.

## 10. Rest of the v2 spec: measurement layer + statistical core (2026-08-20, same day, on `main`)

Continuing directly from §9 above, same session. Skipped spec §5 (learned
availability) throughout — see the reasoning at the top of `HANDOFF.md`
and in §4b there; it's a calendar-time blocker (needs 10-12 gameweeks of
snapshot history; there are 2 rows), not something more effort resolves.

### §3.5 Backtest repair (done first — everything in §4 tunes against it)

Three fixes (`e3de7be`... actually see commit history — `fpl/evaluate/backtest.py`):
imported `compute_player_rates`/`load_weighted_player_history` from
`baseline.py` instead of a near-duplicate (finding #9); missing training
rates fall back to the tier prior instead of zero-filling, exercising the
cold-start path on 210/537 test players (finding #7); added `save_pts`/
`conceded_pts` for GK/DEF (finding #8). Real result: overall RMSE 25.43 ->
22.73, GK specifically 55.36 -> 65.57 predicted (actual 64.10) — finding
#8's prediction confirmed directly. Caught and fixed a real bug while
building this: the new per-channel calibration guard compared raw sums
against a tiny epsilon, which is always true for the negative
`conceded_pts` channel — every DEF/GK conceded calibration was silently
defaulting to a no-op regardless of real signal, fixed to compare
magnitude. `tests/test_backtest_calibration.py` (4 tests) guards this.

### §4.1 Shrinkage — the k sweep

Implemented `shrink_rate`/`shrinkage_weight`/`confidence_label` in
`baseline.py`, shared by `defcon.py` and `backtest.py`. Swept k against the
repaired backtest before picking a value:

| k | overall RMSE | overall rank corr |
|---|---|---|
| 50 | 23.13 | 0.934 |
| 100 | 22.77 | 0.936 |
| 200 | 22.36 | 0.938 |
| 300 | 22.10 | 0.940 |
| **450 (naive default)** | 21.82 | 0.942 |
| 600 | 21.62 | 0.943 |
| 900 | 21.34 | 0.945 |
| 1500 | 20.97 | 0.947 |
| 2500 | 20.64 | 0.949 |
| 4000 | 20.38 | 0.950 |
| 6000 | 20.21 | 0.950 |
| 10000 | 20.07 | 0.951 (peak) |
| 20000 | 19.98 | 0.951 (peak) |
| 50000 | 19.94 | 0.951 (starts declining) |

Both metrics keep improving all the way to k~20000 before even beginning
to turn over. Taken at face value this says "shrink almost everyone almost
all the way to the position/price-tier prior" — but checked against real
players, weighted_minutes median across the pool is ~2074, 75th percentile
~3659, and even Haaland (a top-decile-sample, 3-season-ever-present
player) sits at 5514. At k=20000, Haaland's shrinkage weight would be
5514/(5514+20000) = 0.216 — MINORITY personal, diluted toward the average
expensive forward — directly contradicting spec §4.1's own stated
expectation ("Haaland-class large-sample players barely move"). Minimising
backtest loss alone was therefore the wrong criterion: the backtest can't
distinguish "the model got better at predicting 2025-26 totals" from "the
model stopped differentiating players at all and is just predicting the
tier average everywhere," and this backtest's specific setup (per-90 rates
trained on two OLDER seasons, applied against a totally different season's
ACTUAL minutes) apparently rewards the latter more than expected — plausibly
because 1-2-year-old personal rates are a genuinely noisy predictor of a
different season, while FPL's own price already encodes fresher
information a stale personal rate doesn't.

**Chosen: k=1500** — solidly inside the range where the backtest already
shows most of its gain (RMSE 21.82->20.97, corr 0.942->0.947 vs the naive
k=450 default) while keeping the mechanism's own stated intent intact.
Verified directly:

- **Haaland**: weighted_minutes=5514.68, w=0.786. `goals_scored_per90`
  unchanged at 0.814846 to 10 decimal places — not a bug: Haaland is
  literally the only FWD in the £15-16m price tier (`£15.5m`, next
  closest FWD nowhere near), so his "tier prior" equals his own personal
  rate by construction (a tier average of one player is that player). A
  real, documented edge case.
- **Osula**: weighted_minutes=1159.48, w=0.436 (down from 0.72 at k=450).
  `goals_scored_per90` 0.58992 (personal) -> 0.491283 (shrunk).
  `bonus_per90` 1.055646 -> 0.802567. Both meaningfully corrected toward
  the FWD tier prior.
- **The Osula test** (spec §4.5 exit gate): 25th percentile of the
  high-confidence pool's weighted_minutes = 3948.72. Osula's is 1159.48 —
  thin-sample, confirmed. FWD top-decile `goals_scored_per90` threshold =
  0.5092. Osula's shrunk rate (0.4913) is now BELOW it — the specific
  failure mode this spec section targets no longer holds. A mechanical
  per-position-quantile scan across ALL channels was tried first and
  produced obvious false positives (GK `goals_scored_per90` is ~0 for
  every goalkeeper, so a "top-decile" threshold there is meaningless) —
  abandoned in favour of checking the real top-20-by-weighted_xpts list by
  hand: 5 thin-sample players appear there (Thiago, Osula, O'Reilly,
  Stach, Dewsbury-Hall), and only Osula ever showed the "personal rate
  suspiciously close to a proven performer's despite a fraction of the
  sample" pattern the test is actually about — Thiago has real volume
  behind his rate (3382 weighted minutes, not a fluke), the other three
  are balanced contributors, not spiking any one channel.

### §4.2 Calibration

`backtest.py` now writes `calibration_factors` (per position, per channel:
goal/assist/cleansheet/bonus/save/conceded — deliberately excluding
`defcon`, `card`, `appearance`) into `model_health.json`, computed as
`sum(actual_channel_pts) / sum(predicted_channel_pts)`, clipped to
0.5-2.0. `project.py`'s `apply_calibration` applies them as an explicit
final step (decision D11) — `goal_pts` stays untouched, `goal_pts_cal` is
what actually feeds `xpts_fixture`. Sample factors at k=1500: DEF
goal=0.95/cleansheet=1.10/conceded=0.94 (model slightly under-penalises
conceded goals), GK save=0.91 (slightly over-predicts saves), MID
goal=0.85 (biggest single correction — MID goal-scoring is meaningfully
under-predicted across the board).

### §4.4 Bench weight

`optimiser.py`'s stage-1 objective gained
`+ bench_weight_epsilon * sum(squad[i] * xpts[i])`, epsilon=0.02 (not yet
tuned against real bench regret — no gameweek has finished). Verified it
breaks bench ties toward the higher-xpts candidate without touching stage
2's separate XI/captain solve (`tests/test_bench_weight.py`, 3 tests).
Also added `apply_availability_filters` to `optimise_squad` — needed by
the hindsight engine's retrospective global-XI benchmark (grading against
reality, not pre-GW confidence/status flags).

### §3.1-3.4 Measurement layer

`fpl/collect/actuals.py` (gated on `finished AND data_checked`, unlike
`snapshot.py`'s run_id dedup this is keyed on `event` already present — a
settled gameweek's results don't need a second row from a re-run).
`fpl/decide/squad_state.py` (`data/state/squad_gw{n}.json`, wired into
`build_gw1_squad` — real `squad_gw1.json` now exists). `fpl/evaluate/
hindsight.py` — three XIs (chosen with autosubs simulated per decision D8,
best-from-your-15, best-legal-£100m-global per decision D7 reusing
`optimise_squad` itself) and the regret decomposition
(captaincy/bench/squad summing to total). **None of this has run against
real data** — GW1 hasn't finished — so it's verified with a full
integration test on synthetic data instead
(`tests/test_hindsight.py::test_regret_decomposition_sums_to_total`),
proving the three components actually sum to the total on real code, not
just by algebraic construction.

### §3.6 Dashboard — Week in Review

Added as a 6th tab, deliberately scoped down from the spec's full
description (pitch graphics for both hindsight XIs, season-cumulative
regret line) to a KPI row + regret decomposition bar chart, plus an
honest "not yet computed" placeholder (same pattern Chip Planner already
uses) when `data/output/hindsight_gw{n}.json` doesn't exist — which is
always, right now. Verified both states in-browser (placeholder with the
real, current null data; a mocked hindsight payload rendering the KPIs
and chart correctly) — no console errors either way.

### Test count

44 tests total by the end of this session (`tests/`), up from 11 at the
start of it: `test_hotfix_regressions.py` (5), `test_snapshot.py` (6),
`test_backtest_calibration.py` (4), `test_shrinkage.py` (6),
`test_calibration.py` (5), `test_bench_weight.py` (3), `test_actuals.py`
(5), `test_squad_state.py` (4), `test_hindsight.py` (6). All pure unit
tests — no network, no committed artefacts (spec §7.3) — except the two
real-artefact-dependent hotfix regression tests, which now explicitly pin
`calibration={}` so they stay deterministic regardless of what
`model_health.json` currently holds on disk.

## 11. v3 plan: Phase 0 verification + A1 + B2 (2026-08-21, GW1 day)

Continuing on `main`, working from `docs/FPL_V3_PLAN.md` (the multi-source/
model-bakeoff follow-on to v2). v3 is explicitly gated on v2's measurement
layer (§Phase 0) being green, and most phases carry hard calendar blockers
(GW12 review, GW20 identity-map refresh, 10-12 GWs for learned
availability) — so this session scoped down to what's actually actionable
today rather than attempting the whole plan.

### Phase 0 verification

Confirmed (not just "code exists" — actually exercised against real data):
`data/state/squad_gw1.json` and 2 real snapshot rows already existed from
the prior session. GW1 deadline (2026-08-21T17:30:00Z) was ~14h away at
session start; the last snapshot was already ~14h stale (25.5h-to-deadline
capture from the day before) — took a fresh one (3rd real row,
`hours_to_deadline: 14.01`) since this data is irreversible once the window
closes. Re-ran the full chain (`fpl_client` -> `build_players` ->
`build_fixtures` -> `optimiser`) against it: recommendation held stable
(captain Haaland, vice Cunha, same 15) — reassuring, not a stale artefact.

Confirmed directly via a fresh `bootstrap-static` pull that GW1 is still
`finished=false`/`data_checked=false`/`is_next=true` — `fpl/collect/
actuals.py` and `fpl/evaluate/hindsight.py` remain genuinely calendar-
blocked (the plan's own prediction), not a scoping gap. First real run:
`actuals.py 1` then `hindsight.py 1` once GW1 settles.

### Phase A1 — FPL xG/xA + set-piece order columns

The plan's "free win": `bootstrap-static` already carries these
(Opta-sourced, no scraping) but `ELEMENT_COLUMNS` didn't collect them.
Added 12 columns to `fpl/transform/build_players.py`: per-90 xG/xA/xGI/xGC,
`starts_per_90`, `defensive_contribution`, CBIT components, and the three
set-piece order fields. Verified purely additive: re-ran
`build_players -> optimiser`, squad/XI/captain output byte-identical to
before. All 44 (then-current) tests pass.

### Phase B2 — M2 (xG blend)

Plan's model M2: `attacking_rate = v * xG90 + (1-v) * G90`, where `v`
DECLINES with minutes (opposite direction to shrinkage's `w` — at low
minutes, trust the lower-variance shot-based proxy more; at high minutes,
trust the now-substantial personal scoring record more).

Built `fpl/project/xg_blend.py`: trains personal xG90/xA90 the identical
way `goals_scored_per90` already is (recency-weighted across
`merged_gw.csv`'s `expected_goals`/`expected_assists` columns — generalized
`baseline.compute_player_rates`/`compute_price_tier_priors`/
`lookup_priors_for_all` to accept a `channels` param rather than
duplicating the recency-weighting logic), shrinks toward a position/
price-tier xG prior with the SAME k as goals (`config.shrinkage.k` —
xG's sample-size-to-trust tradeoff is the same shape as goals', it is not
the G-vs-xG blend itself), then blends with the shrunk personal rate using
a new, separately-fitted `k_xg`.

**k_xg sweep** (same methodology as the k=1500 sweep — repaired backtest,
train 2023-24+2024-25, test 2025-26 actual minutes):

| k_xg | overall RMSE | rank corr | goal RMSE | assist RMSE |
|---|---|---|---|---|
| (no blend, M0/M1) | 20.973 | 0.9471 | 8.646 | 5.069 |
| 50 | 20.909 | 0.9485 | 8.555 | 5.115 |
| 450 | 20.817 | 0.9493 | 8.430 | 5.011 |
| **1500 (chosen)** | 20.663 | 0.9504 | 8.258 | 4.901 |
| 4000 | 20.510 | 0.9517 | 8.111 | 4.846 |
| 10000 | 20.416 | 0.9527 | 8.034 | 4.868 |

Same pattern as the shrinkage.k sweep: RMSE and rank corr keep improving
all the way to k_xg=10000, so minimising backtest loss alone pushes toward
an extreme. Chose k_xg=1500 (matching shrinkage.k) — solidly inside the
range most of the gain is banked, while keeping a large-sample player's
rate majority-personal rather than majority-proxy. Verified directly on
real 2026-27 players: Haaland v=0.214 (mostly his own scoring record, not
his shot quality — weighted_minutes=5514), Osula v=0.564 (majority xG —
weighted_minutes=1159, the thin-sample case this blend targets). The
assist channel's own RMSE optimum sits around k_xg=4000-6000 (4.846 vs
4.901 at k_xg=1500, ~1% difference) — not worth chasing at the cost of an
extreme blend weight for large-sample players.

Wired into `fpl/project/project.py` as an explicit `model` parameter on
`build_player_inputs`/`project_gameweeks` (`"m0_rules"` default,
unchanged; `"m2_xg"` swaps in the blended rate) — per plan §B1, each model
writes its own file (`data/projections/m2_xg/gw{n}.parquet`), never the
shared production path. Verified M0's output is byte-identical whether or
not M2 is ever invoked (`git status` showed zero diff on
`data/projections/gw1.parquet` after running both). Spot-checked the top
15 by M0's weighted_xpts: M2 preserves the same ranking, with every value
shifted modestly (mostly down 0.1-0.7 pts) — consistent with elite
attackers' realised scoring rates running slightly ahead of their
underlying shot quality.

**Not done**: the Phase B0 model registry itself (`fpl/models/base.py`'s
`ProjectionModel` protocol, M0 formally wrapped as a registry entry) —
M2 exists as a callable variant of the existing pipeline, not yet a
registered bakeoff candidate the (not-yet-built) evaluation harness can
iterate over. M1 as a SEPARATE nested step was not built: shrinkage
already shipped into production as part of the same-day v2 work before
this plan was written, so there's no "M0 without shrinkage" left in the
codebase to nest M1 on top of — same pattern as handoff findings #2/#4
("dissolved," not fixed, because the bug's precondition no longer exists).
5 new tests (`tests/test_xg_blend.py`) — 49 total.

### Phase C1/C2 — top-40 rank correlation (the primary statistical metric)

Plan §C2's own argument: the optimiser never cares about rank quality
across the full ~600-player pool, only among players actually competing
for a squad slot — a 0.5pt MAE gain on every £4.0m bench defender changes
zero decisions; one £12m forward's wrong rank changes the captain. Added
`fpl.evaluate.backtest.compute_top40_rank_correlation` — Spearman rank
correlation restricted to the model's own top-40-by-predicted (not
top-40-by-actual, which would leak the answer into the test set), wired
into `run_backtest`'s summary as `top40_rank_correlation`, kept alongside
(not replacing) the existing full-pool `rank_correlation_by_position` per
§C2 ("global MAE is secondary, reported only for calibration purposes").

Real result, and a genuinely useful one: full-pool rank correlation was
already reported at 0.90-0.97 by position, but top-40 rank correlation is
**0.444** for M0 — range restriction (only the top-of-pool players, where
outcomes are noisiest and closest together) attenuates correlation
substantially even for a model that ranks the full pool well. This is
exactly the gap plan §C2 predicted existed and the old metric was hiding.

Also gave `run_backtest`/`predict_points` a `model` param mirroring
`project.py`'s (plan §B1): `"m0_rules"` (default, writes
`model_health.json` unchanged — verified byte-diff is exactly the two new
summary keys) or `"m2_xg"` (writes `model_health_m2_xg.json`, never the
production file). Ran both: **M2 beats M0 on both metrics in this
backtest** — top40_rank_correlation 0.471 vs 0.444, overall RMSE 20.663 vs
20.973. Reported honestly as a single-split, non-live signal, not a result
to act on (see `docs/DECISION_RULE.md` below) — per the plan's own
methodology note, minimising backtest loss alone has already been shown
(the shrinkage.k and xg_blend.k_xg sweeps) to reward degenerate solutions,
and this is the same retrospective split with the same documented scope
limits (single split, no fixture adjustment, DEFCON excluded).

**Caught and fixed a real leakage bug while wiring `model="m2_xg"` into
the backtest**: `xg_blend.apply_xg_blend` internally reloads player
history via `config["history"]["seasons"]`, and the plain `config`
`predict_points` receives already includes `TEST_SEASON` (2025-26 is in
production's own `history.seasons` list) — passing it unmodified would
train the xG rates partly on the season being predicted. Fixed by passing
`_train_only_config(config)` into `apply_xg_blend` specifically, same
pattern `_apply_shrinkage`'s caller already uses for the goal/assist
rates. Guarded by a new regression test
(`tests/test_backtest_xg_blend.py`) that monkeypatches
`apply_xg_blend` and asserts the config it actually receives excludes
`TEST_SEASON` — this is exactly the leakage class `identity.py`'s
docstring warns about, caught here before it reached a committed number.

4 new tests (3 `test_top40_rank_correlation.py`, 1
`test_backtest_xg_blend.py`) — 53 total.

### Phase C5 — pre-registered decision rule

Plan §C5 / decision D4: "write this into the repo before GW2 data
exists." GW1's own deadline hadn't even passed yet at write time, so this
is a genuine pre-registration, not a post-hoc rationalisation dressed up
as one. Wrote `docs/DECISION_RULE.md`: the plan's rule verbatim, dated
concretely (GW12 review = 2026-11-06, computed from GW1's real deadline
at the plan's weekly cadence — matches the plan's own "early November
2026" estimate), current champion recorded as M0, M2's backtest numbers
listed explicitly as "informational, not part of the rule" with the
reasoning for why a single retrospective split isn't allowed to move the
champion before GW12's gameweek-clustered, live-data conditions are met.

### Phase D — K-best with diversity + bench weighting refinement

Plan §D1/D2/D3: alternatives via no-good cuts (`Σ_{i∈S} x_i <= |S| - d`),
not pure K-best (`d=1` gives fifteen near-identical squads differing only
in the cheapest bench filler — the plan's own worked example). Built
`fpl/decide/kbest.py`:

- `find_k_best_squads` — full 15-man alternatives, `d=3` required (plan
  §D2). Iteratively calls `optimiser.optimise_squad` with an accumulating
  list of no-good cuts (`optimise_squad` gained an `extra_no_good_cuts`
  param for this). Stops early (returns fewer than k) rather than
  fabricating a result once the pool has no more sufficiently-different
  legal squads — verified with a zero-slack synthetic pool (exactly 15
  eligible players, only one legal squad exists at all).
- `find_k_best_xis` — alternative XIs from a FIXED 15, pure K-best `d=1`
  (plan §D2 — the pool is only 15 players, so more diversity would run out
  fast). Reuses `pick_xi_and_captain`'s exact LP structure with no-good
  cuts on `start[]` added between solves.
- `compute_cross_model_agreement` (plan §D3: "if M0's top squad ranks #2
  under M2 ... that convergence is worth more than any single model's
  confidence") — for each model's #1 squad, looks it up (as an exact id
  set) inside every other model's own K-best frontier, `None` if not
  found there (documented explicitly as NOT necessarily disagreement — a
  squad differing by only 1-2 players from another model's own #1 can
  still fail to appear as an EXACT match inside that model's `d=3`-spaced
  frontier).

**Real bug caught while building this, before it reached a committed
number**: the first `frontier_spread` implementation ranked squads by
`horizon_weighted_xpts` (the raw sum of all 15 squad members' xpts) —
non-monotonic with what the stage-1 MILP actually optimises (starting-XI
xpts + captain + a small bench term, NOT the full 15-man sum). Concretely:
squad #5 in a real run showed `frontier_spread=+5.16` — HIGHER than squad
#1, which is mathematically impossible for a more-constrained solve
UNLESS the ranking metric doesn't match the actual objective. Fixed by
having `optimise_squad` return `stage1_objective` (`pulp.value(prob.
objective)` — the real thing being maximised) and ranking on that
instead; frontier_spread is now verified monotonically non-increasing by
construction (`tests/test_kbest.py`) and confirmed on real GW1 data: M0's
top-5 diverse squads span only 223.80 down to a `stage1_objective` spread
of -0.13 pts — the model has essentially no strong preference among its
top 5 diverse squads this early in the season, exactly the "report the
gap, not just squad #2" case plan §D3 describes.

**Cross-model agreement, real result**: M0's and M2's respective top
squads share 12 of 15 players but neither appears as an exact match in
the other's own K-best frontier — a genuine, reportable disagreement
(the 3 differing players are exactly where M2's xG-blended attacking
rate diverges from M0's shrunk personal rate), not a bug.

**Phase D4 (bench weighting) refinement**: the flat
`bench_weight_epsilon` (v2, already shipped) broke bench ties but weighted
every position identically. Added `optimiser._position_blank_rate` — a
per-position `1 - mean(minutes_factor)` among that position's
above-median-price players (a "likely starters" proxy, avoiding the
endogeneity of using the LP's own `start[]` result, which doesn't exist
yet when this constant is computed) — so a bench GK (rare blank event) is
weighted differently from a bench DEF/MID/FWD (more common), per plan
§D4's "weight bench slots by P(a starter in that position blanks) ×
bench_player_xpts." Degrades to the old flat 1.0 multiplier when
`minutes_factor` isn't in the pool (keeps `tests/test_bench_weight.py`'s
synthetic-pool tests passing unmodified). Verified against real GW1 data:
squad/XI/captain output unchanged (the position-scaled weight still only
matters for genuine ties, same design constraint as before).

**Not done**: bench-SLOT ordering itself (which specific bench player
occupies "slot 1" vs "slot 3") — the objective now differentiates bench
value by POSITION, not yet by an explicit per-slot rank inside the
objective (would need real per-starter blank-probability estimates and a
slot-assignment sub-structure in the MILP; flagged as a genuine follow-up,
not attempted this session given the modelling gap).

8 new tests (`tests/test_kbest.py`) — 61 total.

### Phase A0 / A2 / A5 — source adapter contract, Understat adapter, identity mapping

**Real finding, surfaced immediately and put to the user before writing any
scraping code**: understat.com's `robots.txt` disallows ALL automated
access (`Disallow: /`). The v3 plan (§A2) classified Understat as
lower-risk "enrichment tier," distinct from Sofascore's explicit ToS-risk
quarantine (D1/D2), but doesn't appear to have checked robots.txt when
making that call. Stopped and asked before proceeding — the user reviewed
and explicitly authorized continuing for this private, local,
non-redistributed tool. Recorded as a standing note in
`fpl/collect/sources/understat.py`'s module docstring: this is not a
default future sessions should assume still stands, and the adapter is
held to at least Sofascore's A3 containment rules (rate limit, honest UA,
no evasion, stop rather than escalate) even though the plan only formally
required those for the quarantined tier.

**Also found**: Understat's page no longer embeds player data as an
inline `JSON.parse(...)` blob the way the plan's scraping approach (and
most public guides) describe — confirmed by inspecting real network
traffic via the browser tool. The actual mechanism: the league page sets
a session cookie, then client-side JS calls
`GET /getLeagueData/{league}/{season}` with a matching `Referer` header,
returning `{teams, players, dates}` as real JSON. Replicated with a plain
`requests.Session` (page load, then the API call with `Referer` — the
same two-step flow the site's own JS performs, not a bypass of anything).

Built:
- **`fpl/collect/sources/base.py`** (plan §A0) — `SourceAdapter` Protocol +
  `SourceHealth` dataclass, the three hard rules (degrade never crash,
  append-only raw file, health reported every run) as docstring
  requirements every real adapter restates.
- **`fpl/collect/sources/understat.py`** (plan §A2) — `UnderstatAdapter`.
  Caches to `data/raw/understat/{season}.csv`, never re-fetches once
  cached (a completed season never changes). Verified against REAL live
  data: `fetch("2025")` returns 537 real 2025-26 Premier League players
  with real xG/npxG/xA/xGChain/xGBuildup (Haaland: xG=28.80, npxG=25.75,
  matches his real season). Verified graceful degradation on a bad
  request (404 -> empty DataFrame + `health().error` set, no crash) and
  that a cache hit never touches the network (0.5s vs a real ~7s
  page+API round trip). **Scope note, documented not hidden**: the
  endpoint gives season-TO-DATE aggregates, not per-gameweek splits —
  the literal `SourceAdapter.fetch(gameweek)` signature doesn't fit
  Understat's actual data model; would need date-bucketing against
  `fpl/transform/build_fixtures.py`'s gameweek table, a real follow-up.
  4 tests network-independent (mocked `_fetch_live`), 1 real network test
  (`@pytest.mark.network`, excluded from the default suite via new
  `pytest.ini` — `addopts = -m "not network"` — run explicitly with
  `pytest tests/ -v -m network`).
- **`fpl/project/identity_multi.py`** (plan §A5) — cross-SOURCE identity
  bridge (FPL `code` <-> Understat `source_player_id`), distinct from
  `fpl/project/identity.py`'s cross-SEASON bridge. Exactly the plan's
  specified design: normalise (strip diacritics, lowercase, drop
  punctuation, **plus html.unescape — real bug found against live data,
  Understat's `player_name` carries raw HTML entities like
  `Matt O&#039;Riley`**), exact `(name, team)` match = high confidence,
  fuzzy fallback (>=0.82 SequenceMatcher ratio, team-restricted to avoid
  cross-team false positives) = medium confidence -> review queue, NEVER
  auto-accepted. Genuinely unmatched players get no row at all — not a
  null, not a zero (finding #11's bug, not reintroduced here).
  **Added a real third pass beyond the plan's literal spec**: exact
  name-only match (team ignored) when the normalised name is unique on
  BOTH sides among the still-unmatched pool — recovers a real, verified
  gap: some historical archive CSVs already reflect a LATER team
  transfer than the season being matched (confirmed directly — Eze's
  2025-26 `players_raw.csv` record already shows Arsenal, his 2026-27
  club, not Crystal Palace, his actual 2025-26 club), so team-qualified
  matching alone silently missed an otherwise-unambiguous exact name
  match.

**Real result, run against real 2025-26 data** (both FPL's own archive
and a live Understat fetch): of 537 FPL players with real 2025-26
minutes, **444 matched (82.68% coverage)** — 416 high confidence
(405 exact name+team, 11 exact name-only-unique), 28 medium confidence
in the review queue. Below the plan's own >=90% coverage target — the
remaining ~17% is genuinely unmatched (not silently dropped: `coverage_pct`
makes the gap a visible, reportable number, per plan §A5 "coverage is a
health metric"), a real, honest state for a first pass, not a passing
gate. Written to `data/reference/player_id_map_2025-26.csv` (444 rows) and
`..._review_queue.csv` (28 rows, for a human to work through). **Named
explicitly by season** (`_2025-26` suffix) rather than a plain
`player_id_map.csv` — this is the completed-season validation exercise
the real numbers above come from, NOT a live 2026-27 map (Understat has
no 2026-27 data yet — GW1 hasn't finished, same calendar blocker already
documented for `actuals.py`/`hindsight.py`). Producing the real 2026-27
map is a rerun of the same pipeline once real 2026-27 matches exist on
Understat.

**Not done**: wiring this map into `fpl/project/xg_blend.py` or a new M3
model (plan's B3, `understat` — npxG/xGChain/set-piece/shot-quality
features) — this session built and validated the identity bridge and the
adapter, not the model that consumes them. `SourceHealth` also isn't yet
written into `model_health.json` (A0 rule 3) — a real follow-up.

21 new tests (4 `test_understat_adapter.py` non-network + 1 network,
12 `test_identity_multi.py`) — 76 total (77 including the network test).

### Phase B3 — M3 (Understat blend), a real negative result

Plan's nesting table (§B1): M3 = M2 + "npxG, xGChain, set-piece/penalty
split, shot quality." Built `fpl/project/understat_blend.py`: for players
with a real cross-source identity match (via the 2025-26
`player_id_map`, whose `understat_id` is verified stable across Understat
seasons — same mechanism as FPL's own `code`), blends M2's already-
blended goal rate a second time toward a recency-weighted **npxG90**
(non-penalty xG, fetched and cached for all 3 training/test seasons —
2023, 2024, 2025 — via `UnderstatAdapter`), weight `v3 = k_npxg / (m +
k_npxg)` using the Understat-specific weighted-minutes sample (not FPL's),
same functional shape as M2's own `v`. Unmatched players pass through
with M2's rate unchanged. Wired into `project.py`/`backtest.py` as
`model="m3_understat"`, same non-invasive pattern as M2 (own projections
path, leakage-guarded train-only config for the backtest). 4 new tests
(`tests/test_understat_blend.py`).

**Real result, and an honest one**: swept `k_npxg` the same way as
`shrinkage.k` and `xg_blend.k_xg` — **M3 does NOT beat M2 at any tested
value.** Best case (k_npxg=50, nearly full npxG trust) reaches
top40_rank_correlation=0.459 / RMSE=20.773, still short of M2's 0.471 /
20.663; every other tested value (200-10000) is meaningfully worse
(down to 0.30-0.32). Diagnosed, not just reported: real npxG values
looked sane on inspection (301 real players bridged in the train-only
pass, rates like Saka npxG90=0.393 vs personal 0.406 — plausible), so
this isn't a wiring bug. Two candidate explanations, both consistent with
this project's own prior findings, neither confirmed: (1) blending an
ALREADY-blended M2 rate a second time may just dilute signal rather than
sharpen it — M3's blend has no way to "unmix" M2's own xG contribution
before adding npxG on top; (2) 2+ year-old Understat data (the TRAIN
seasons, 2023-24/2024-25) predicting a different season's actual goals
may be a genuinely noisier signal than FPL's own more-current xG — the
exact "stale personal rate" effect already documented in the
shrinkage.k sweep reasoning (2026-08-20, same file, §10).

**Kept k_npxg=1500** (matching k_xg/shrinkage.k) for architectural
consistency, NOT because it won the sweep — nothing did. **M3 is not
promoted as the lead challenger over M2** — recorded honestly in
`docs/DECISION_RULE.md`'s informational table, per the project's own
established culture (PROJECT_LOG §10: "if a challenger doesn't clearly
win, that's a valid, reportable outcome, not a failure of the
experiment"). This is exactly what plan §A3's recorded Sofascore
prediction anticipates for a composite/derived signal that doesn't add
information beyond what's already captured — plausibly the same
dynamic here, one layer earlier than the plan expected it.

4 new tests (`tests/test_understat_blend.py`) — 80 passing by default
(81 including the network test).

### Phase B5 — M5 (ensemble), another honest negative result

Plan §B5: "performance-weighted blend of M0-M4... weighting fitted
OUT-OF-SAMPLE only." Built `fpl/evaluate/ensemble.py`: fits inverse-
RMSE-squared weights (a model with half the RMSE gets 4x the weight, not
2x — reflects that RMSE is itself already a square root of what's being
minimised) on one random half of `TEST_SEASON`'s tested player pool (a
FIT half), then evaluates the blended prediction — and, for a fair
same-set comparison, every individual model — on the DISJOINT other half
(the EVAL half). This out-of-sample discipline matters: fitting weights
and evaluating the ensemble on the SAME data would be circular, the same
mistake as tuning a hyperparameter directly against a test set. 6 new
tests (`tests/test_ensemble.py`) for the weighting/split mechanics.

**Real result, run against M0+M2+M3 on real 2025-26 data**: the ensemble
does NOT beat M2 alone. On the eval half (269 players): M2 alone scores
top40_rank_correlation=0.5966; the full M0+M2+M3 ensemble scores only
0.5266; even dropping the already-weaker M3 (an M0+M2-only ensemble)
scores 0.5593 — both worse than simply using M2 by itself. Ensemble RMSE
(20.104-20.138) IS marginally better than any individual model's RMSE in
both configurations — but that's exactly plan §C2's own trap in action:
RMSE-based weighting optimises the metric the ensemble was FIT on, not
the primary top-40 rank-correlation metric it's actually judged on, and
blending in a model that's merely "not much worse" on RMSE but clearly
weaker on top-40 rank quality drags the blend's primary metric down even
as it nudges the secondary one up.

**Not attempted**: re-fitting weights directly against top40_rank_correlation
instead of RMSE — with only 269 eval players and an already-thin
fit/eval split, optimising ensemble weights against the exact metric
being reported risks a new, smaller-scale version of the same circularity
this module's out-of-sample split exists to avoid. Flagged as a real
follow-up requiring more data (live per-gameweek hindsight results, once
they exist) rather than squeezed further out of the one retrospective
season this backtest has.

**Where this leaves the bakeoff**: M2 (`xg_blend`) is the strongest
challenger found across this entire session's model-building work — it
beats M0 on every metric tested, and it beats every combination tried
against it (M3 alone, M3-blended-in, ensemble). `docs/DECISION_RULE.md`
updated with M5's row and an M4 (Sofascore) placeholder row, both
honestly marked. None of this changes the champion — per the
pre-registered rule (plan §C5), only real GW1-11 live data at the GW12
review can do that.

### Phase E — live Streamlit decision layer

Plan §E1's boundary rule: FROZEN (data pull -> xpts, GitHub Actions,
committed artefacts) vs LIVE (xpts vector + constraints -> MILP -> squad/
XI/captain/K-best, Streamlit, re-solved on demand, never persisted as
canonical). Built as two files: `dashboard/live_data.py` (all file I/O —
reads only already-committed parquet/JSON, never calls
`project_gameweeks`/`build_players`) and `dashboard/app.py` (the UI,
imports only `fpl.decide.kbest`/`fpl.decide.optimiser` — the pure,
offline MILP layer plan §E1 says may be re-solved freely).

**Prerequisite work, done first**: `optimise_squad` didn't have any of
plan §E2's what-if knobs (lock/ban/budget/formation/chip) — added them as
six new optional params (previous commit, `cc8aff7`), all defaulting to
None so the frozen weekly path stays byte-identical. **Caught a real bug
while wiring the UI to them**: `force_formation` only reached stage 1's
XI-shaping variables, which `optimise_squad`'s OWN docstring says are
discarded — the starting XI actually returned always comes from stage 2
(`pick_xi_and_captain`), which never saw the constraint, so a forced
3-5-2 request silently produced whatever formation stage 2 preferred on
its own. Fixed by threading `force_formation` into `pick_xi_and_captain`
too (and `kbest.find_k_best_xis`, for K-best-under-a-forced-formation).
Caught by a test that failed against the pre-fix code
(`test_force_formation_produces_the_exact_requested_starting_shape`).

**E2 controls implemented**: lock/ban players, ban clubs, budget
override, force formation, model selector (reads whichever of M0/M2/M3
has a committed projection for the selected gameweek —
`live_data.available_models`), Bench Boost chip (swaps the bench term's
objective weight from the tie-breaking epsilon to full value). Free Hit
is present in the UI but an honest no-op: plan §E2 describes it as "drop
the transfer-cost term," and `fpl/decide/transfers.py` (which would
compute that term) doesn't exist — the UI says so directly rather than
silently doing nothing under a name that implies otherwise.

**E3 reproducibility guard**: every live solve is banner-labelled
EXPLORATORY; the canonical `gw{n}_recommendations.json` is always shown
pinned above it, unmodifiable; every live solve appends to
`data/scratch/live_solves.jsonl` (constraints, model, top result,
timestamp — gitignored, ephemeral, never committed); nothing on this page
writes to `data/state/` — committing a squad stays a separate, explicit
action this app deliberately does not implement.

**E4 performance**: `st.cache_data` on the player-inputs load, keyed by a
real content hash of the projection parquet + config.yaml (not just
mtime, so an identical redeploy doesn't needlessly bust the cache but a
real change always does); an explicit Solve button (nothing re-solves on
slider drag).

**Verified end-to-end via the browser tool, not just unit tests**:
launched the real app (`streamlit run dashboard/app.py`), clicked Solve
with no constraints — got 3 K-best squads matching the CLI's own numbers
exactly (223.80 / 221.56 / 220.48 weighted xPts, same as
`kbest.find_k_best_squads` run directly), same captain/vice as the
canonical recommendation, frontier-spread note rendered correctly ("top 3
squads within 0.07 pts... no strong preference"), and confirmed
`data/scratch/live_solves.jsonl` was written with the exact constraint
set and top squad — matching `data/state/squad_gw1.json` exactly. No
server errors in the Streamlit process log.

8 new tests (`tests/test_live_data.py`, the pure data-loading logic — no
Streamlit runtime dependency) + the 9 optimiser what-if tests from the
prerequisite commit — 103 passing total.

**Not done**: `st.cache_data` on the K-best SOLVE itself (only the xpts
vector load is cached — re-solving on every Solve click is intentional
per §E4's "explicit Solve button," so this isn't a gap, just noting the
scope); streaming K-best results as each one lands rather than waiting
for all of them (plan §E4 mentions this — `find_k_best_squads` runs all
solves before returning, a real follow-up for a K as large as 8-10);
Free Hit's actual transfer-cost-term removal (blocked on
`fpl/decide/transfers.py` not existing, same as noted above).
