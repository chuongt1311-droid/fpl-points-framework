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

**Not done this session** (deliberately out of scope for a single
sitting, flagged in `docs/HANDOFF.md` §9): rotating the leaked Bzzoiro
token (requires the token-issuing service, not something doable from the
repo — do this first, independent of everything else here), and the rest
of `FPL_V2_DESIGN.md` (measurement layer, statistical core, learned
availability) — substantially larger, and in the last case blocked on
10-12 gameweeks of calendar time regardless of effort spent now.
