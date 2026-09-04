# Handoff — FPL Points-Maximization Framework

**Status as of 2026-09-04, latest (dashboard "My Team" view):**
The static dashboard showed the model's *optimal* squad, never the user's
actual 15. New "My Team" tab: your real squad (from a cheap public entry
pull) + a roll / 1-transfer / 2-transfer / wildcard comparison via
`transfers.solve_transfers`. New `scripts/build_my_team_data.py` writes
`dashboard/my_team.json` (gitignored); `build_dashboard_data.py` inlines
it. Sell prices from `data/private/my_team.json` when fresh, else market
proxy. Wired into `weekly.yml`. **228 tests passing.** Detail:
`docs/PROJECT_LOG.md` §19. GW3: wildcard shows +25.29/5-GW but only
+2.62 next-GW — verdict is hold the chip (matches the Council review).

**Status as of 2026-09-04, later (per-gameweek dashboard):**
`scripts/build_dashboard_data.py` was pinned to GW1 (recommendation
filename + hardcoded deadline); the static dashboard never advanced. Now
picks the upcoming GW (`select_target_gameweek`), reads the deadline from
`bootstrap_static.json`, and renders the transfer line from
`gw{n}_transfers.json`. New `scripts/archive_dashboard_week.py` freezes
each week's self-contained `index.html` to `dashboard/weeks/gw{N}.html`
with a generated `weeks/index.html` list; wired into `weekly.yml`. **221
tests passing.** Detail: `docs/PROJECT_LOG.md` §18. The §13
recompute-overwrite bug in that script is still open and untouched.

**Status as of 2026-09-04 (rolling start-rate lag fix — GW3 prep):**
`fpl/project/minutes.py::compute_rolling_start_rate` was computing every
player's start probability from the 3 *completed* archived seasons only,
never the current one — so a player whose archived tail was an injury /
benched spell (Wissa: 0.3 xPts flat, a nailed Newcastle starter) got
`minutes_factor = 0` and was flagged for sale. Fixed by folding the
current season into the last-6 window (`history_loader.seasons_to_load()`
now also pulls `config.season`; `compute_rolling_start_rate` reads it if
present). Minimal by design — "natural last-6", no recency weighting.
**212 tests passing**, +4 regression tests for this fix. Full detail:
`docs/PROJECT_LOG.md` §17.
Residual: vaastav's current-season CSV lags the API ~1 GW, so the
correction is ~1 GW slower than ideal and self-heals by ~GW7. GW1 & GW2
`fpl.collect.actuals` + `fpl.evaluate.hindsight` now run (they were not
wired into `weekly.yml` and still aren't — run manually).

**Status as of 2026-08-22, latest (Phase H H3a+H3b — the transfer
decision layer):** The tool now answers the weekly question it always
claimed to: *"I own these 15, I have N free transfers and £X in the bank
— what do I do?"* New: `fpl/decide/constraints.py` (shared MILP builders,
extracted from `optimise_squad` behind a byte-identical gate),
`fpl/decide/transfers.py` (the solve + artefact + CLI), real-team
ingestion in `squad_state.py`, and `scripts/my_team_instructions.py`.
Archive extended to capture transfer recommendations. **193 tests
passing** (was 162). Full detail in `docs/PROJECT_LOG.md` §16; spec and
plan under `docs/superpowers/`.

**Four things to know before touching this:**
- **`config.yaml` now carries `fpl.entry_id: 6669718`** — public (it's in
  the Points page URL), not a secret.
- **Sell prices and free transfers come from a pasted, gitignored
  `data/private/my_team.json`.** No FPL credential is stored, requested
  or handled anywhere. Run `scripts/my_team_instructions.py` for the
  exact steps. The solver refuses a file older than 24h.
- **A squad mismatch between that file and the public endpoint STOPS the
  solve**; a bank mismatch does not (it legitimately means "already
  transferred this week"). That asymmetry is deliberate.
- **The transfer objective is the 5-GW weighted horizon with the hit
  charged once**, not the v4 plan's literal single-gameweek version,
  which would have been systematically hit-averse. Both gains are always
  reported.

**Known limitation, surfaced in the artefact itself:** a single-deadline
solve values an unused free transfer at zero, so it will spend one for
any positive gain — the real-data run recommended Guéhi → Matheus N. for
just **+0.18** weighted points. Low-gain recommendations now carry an
explicit `caveats` entry saying rolling is likely at least as good.
Pricing an unspent FT is what H3c adds.

**Status as of 2026-08-22, earlier (Phase G history layer built — branch
`phase-g-history-layer`):** Tier 0.2's crude timestamped copy is now a
real bitemporal, append-only, provenance-stamped archive:
`fpl/history/` (`paths` / `provenance` / `archive` / `manifest` /
`query`), hive-partitioned Parquet + JSON under `data/history/`, a
read-only DuckDB query layer, a migration script, and a **History** tab
on the static dashboard. `weekly.yml` now runs
`python -m fpl.history.archive` immediately after Decide (moved earlier
deliberately, so the archive is not downstream of
`build_dashboard_data.py`'s known overwrite bug). **162 tests passing**
(was 116 this morning). Full detail — including three deliberate
deviations from `FPL_V4_PLAN.md` §4 and three real bugs — in
`docs/PROJECT_LOG.md` §14. Spec: `docs/superpowers/specs/
2026-08-22-history-layer-design.md`; plan: `docs/superpowers/plans/
2026-08-22-history-layer.md`.

**Three things worth knowing before touching this package:**
- **`asof` is ISO 8601 BASIC (`20260822T125314Z`), not extended.** ISO
  extended has colons, which Windows forbids in paths. Partitions are
  immutable, so this cannot be changed later.
- **`gw` is the gameweek a run was TARGETING; `event` is the gameweek a
  row's xPts is FOR.** A projections artefact spans the 5-GW horizon, so
  a revision series fixes `event` and spans multiple `gw` partitions.
  `Archive.revisions()` is keyed on `event`. This is the easiest thing
  here to get backwards.
- **Never interpolate a path into DuckDB SQL — bind it.** This repo lives
  at `D:\CT's Portfolio\...`; the apostrophe terminated a SQL string
  literal and made every real query silently return zero rows while the
  tests passed. Regression-tested.

**Dashboard, honestly scoped:** Archive Coverage is real and useful now
(it is what makes silent capture failure visible). Revision is wired to
real queries but shows "needs 2 runs in a gameweek, currently 1" until
data accumulates — it fills in by itself. Timeline, Decision trail and
Model drift are **deliberately not built**: they need finished actuals
and several gameweeks, and would otherwise be invented-shape
placeholders. Revisit at GW3+.

**Status as of 2026-08-22, earlier (dashboard polish pass — keyboard
accessibility + a real Player Explorer row-count bug):** Reviewed both
dashboards (static `dashboard/template.html` + the live Flask app's
`dashboard/live/index.html`) — already visually cohesive from last
session's redesign, so no reskin. Fixed: tabs/sortable headers/
expandable rows were all click-only with no keyboard path (added the
ARIA tabs pattern + `tabindex`/`aria-sort`/`aria-expanded` +
Enter/Space handling throughout, plus the live app's player-search
dropdown options); Player Explorer's count label read the unfiltered
row count while `renderRows` silently capped the table at 300 —
dropped the cap (599 rows renders fine) so the label is honest again;
added a "no players match" empty state. Verified live in the browser
tool via dispatched keyboard events against the regenerated
`index.html`, not just static markup review — all transitions
(`aria-selected`, `aria-sort`, `aria-expanded`, focus movement) behave
correctly, no console errors, both files' JS passes `node --check`.
`.claude/launch.json` gained a `fpl-static-dashboard` preview entry.
116/116 tests still passing. See `docs/PROJECT_LOG.md` §13. Not yet
committed — see the entry below for the prior commit.

**Real bug found in passing, NOT fixed (flagged as a background task):
`scripts/build_dashboard_data.py` silently recomputes and overwrites
`data/processed/*.parquet`** despite its own docstring claiming
read-only. Caught because a routine `data/raw/` refresh (for Tier 0.4's
GW1 check) plus a dashboard-verification re-run of this script changed
42 real values in `dashboard/data.json`'s per-player channel breakdown —
restored via `git restore --source=HEAD` before anything got committed.
Root cause: `project.build_player_inputs()` → `build_players.
build_players()` / `baseline.build_baseline()` / `defcon.build_defcon()`
all persist to `data/processed/*.parquet` as a side effect of being
called, using whatever's currently in the gitignored `data/raw/` — there
is no pure, non-persisting path to the channel breakdown this script
actually needs. Full root-cause writeup in `docs/PROJECT_LOG.md` §13.
**Add to known open items below** — this is a real FROZEN/LIVE-boundary
violation, not just a docstring inaccuracy: it means any local run of
this script can silently corrupt the committed snapshot's internal
consistency with no warning and no test catching it.

**Status as of 2026-08-22, earlier (v4 plan proposed; Tier 0 executed;
Sofascore/M4 closed permanently):** Committed as `827adc8` (bundled
with the previously-uncommitted v3 close-out work below — not pushed
yet). A new proposal, `docs/FPL_V4_PLAN.md`
(external/user-supplied, not committed here, **not locked** — five open
`[DECISION]` points in its Appendix B), picks up where the entry below
stops. Its Tier 0 ("do these before any modelling work") is done this
session — see `docs/PROJECT_LOG.md` §12 for full detail:

- **M4/Sofascore: closed, permanently, not just for today.** User asked to
  resume it via `ScraperFC` and, once told the block was the same one
  already root-caused on 2026-08-20/21, explicitly asked to abandon
  containment rule A3.3 and keep trying. Declined — re-confirmed the same
  edge-ACL block with a fresh evasion-free probe
  (`scripts/probe_sofascore.py`), and separately, `ScraperFC`'s Sofascore
  module now requires driving a headed anti-bot-evasion browser, which
  this assistant won't build regardless of authorization. Logged in
  `docs/DECISION_RULE.md`'s M4 row as **abandoned**. ClubELO (via
  `ScraperFC`, unblocked) is the named replacement in the team-strength
  hierarchy — not yet built (v4 plan Phase I3).
- Added `.env.example`, `scripts/check_secrets.py` (pre-commit secret
  scan, clean against the current repo), `scripts/install_hooks.py`
  (one-time local hook installer — installed and verified this session).
  **The actual Bzzoiro token rotation is still not done** — it's on the
  issuing service directly, outside any tool access available here;
  flagged a fourth time.
- `.github/workflows/weekly.yml`: added a crude, unschema'd projection/
  decision archive step (`data/history/{utc_timestamp}/`, copies
  `data/projections` + `data/output`, never overwrites a prior run) so
  the overwrite-every-run data loss stops accumulating before the real
  bitemporal schema (v4 plan §G2) gets designed; added
  `scripts/check_staleness.py` (fails the job if the newest availability
  snapshot row is >4 days old) and a failure-notification step that
  opens/reuses a single tracking GitHub issue. **All verified live.** The
  schedule was already proven alive by two real scheduled runs
  (`85d9312`, `b40eb0e`); after pushing this session's work a manual
  `workflow_dispatch` (run #3) went **green in 1m20s** and produced
  `c8ec4fa`, which confirms the archive step wrote
  `data/history/20260822T125314Z/` (recommendations + all three
  `model_health*.json` + all three model `projections/*.parquet`), the
  staleness assert passed, and the bot's `index.html` regeneration
  correctly picked up this session's `template.html` accessibility
  changes. The `if: failure()` notification step was left unproven by
  this run (it succeeded, so that branch never executed) — later tested
  deliberately and found **broken**; fixed and re-proven. See §9 and
  `docs/PROJECT_LOG.md` §15.
- **GW1 actuals + hindsight hand-check (Tier 0.4): genuinely
  calendar-blocked, not started.** `bootstrap-static`'s GW1
  `finished`/`data_checked` are both still `false` as of this session —
  confirmed directly, and `fpl.collect.actuals`'s not-ready gate behaves
  correctly (no-ops cleanly). Revisit once GW1 actually finalizes.
- 116/116 tests still passing — no pipeline logic touched this session,
  only new standalone scripts plus workflow/doc changes. **Nothing
  committed or pushed yet** — all of the above is local, pending review.
- **Next, per the decomposition agreed with the user**: dashboard polish,
  then (once the history-archive design lands) the v4 Phase G dashboard
  views, then the decision layer (Phase H — the plan's #1-ranked gap:
  `fpl/decide/transfers.py` still doesn't exist). Four of the v4 plan's
  five Appendix B decisions remain genuinely open — storage engine
  (Parquet+DuckDB vs SQLite), the FPL-auth approach for real squad
  ingestion, phase ordering, and public-vs-private dashboard — ask the
  user before committing to any of them.

**Status as of 2026-08-22 (dashboard visual overhaul + Flask live decision
layer + a real kbest what-if bug fix):** `dashboard/template.html` (and
regenerated `index.html`) got a visual redesign — validated chart
palette (dataviz skill; the old one FAILED against the real dark
surface), true SVG pitch markings, hero KPI treatment, hover tooltips,
and a corrected `known_limitations` list (three stale entries referenced
already-dissolved v2/v3 findings). The live decision layer gained a
Flask edition (`dashboard/live_server.py` + `dashboard/live/index.html`)
alongside the existing Streamlit one (`app.py`, kept as legacy) — same
FROZEN/LIVE boundary and EXPLORATORY/pinned-canonical/solve-logging
guardrails, a purpose-built control-panel UI instead of Streamlit's
widget chrome, sharing one now-framework-agnostic `live_data.py`. Run the
new one: `.venv\Scripts\python.exe -m dashboard.live_server`, then open
http://127.0.0.1:5000/. **Real bug caught and fixed**:
`kbest.find_k_best_squads` never accepted the six what-if params
(`locked_ids`/`banned_ids`/`banned_clubs`/`budget_override`/
`force_formation`/`chip`) that `optimise_squad` supports — the entire
Streamlit sidebar was silently inert for K-best squad search, every
constrained solve quietly returning the unconstrained frontier. Fixed,
regression-tested, verified end-to-end (locked player + forced formation
both genuinely enforced in the live Flask app). See PROJECT_LOG §11's
newest subsection for the full story. 105 tests passing (was 103).

**Status as of 2026-08-22, continued (closed out §5's remaining open
bugs + added the Bakeoff dashboard view + a blocked Sofascore attempt):**
Fixed the four still-open findings from §5 below — GK backup
re-promotion (#3), `fixtures.parquet`'s missing auto-build path (#5),
`minutes.py`'s hard column select (#6), and the hardcoded status set now
centralised in a new `fpl/status.py` (#10) — all regression-tested,
`minutes.py` in particular had NO test file before this (now
`tests/test_minutes.py`). Re-ran `fpl.decide.optimiser` against real
data afterward: byte-identical GW1 output, confirming zero regression
(none of these bugs are currently live — no GK is injured pre-season,
both columns are present today — they were latent). Added the
`docs/FPL_V3_PLAN.md` §9 Phase F "Bakeoff" view to the static dashboard
(`dashboard/template.html`, `scripts/build_dashboard_data.py`) — M0 vs
M2 vs M3's top-40 rank correlation and RMSE side by side, reading only
committed `model_health*.json` files, with the pre-registered GW12 rule
and an honest "not a promotion signal" banner. **Sofascore (plan A3/A4,
M4): user gave fresh authorization, but the adapter was never written**
— `sofascore.com` was unreachable from this execution environment at
every access path tried (direct request, proxied fetch, browser
navigation), blocked at `robots.txt` itself, the most minimal possible
request. Per A3's own "no evasion, stops at 403, does not escalate"
rule, no workaround was attempted. Genuinely unknown whether this is
Sofascore's edge or this environment's own egress policy — see
`docs/DECISION_RULE.md`'s M4 row and PROJECT_LOG §11 for the full
finding. 116 tests passing (was 105).

**Status as of 2026-08-21 (v3 plan started — Phase 0 verified, A0/A1/A2/A5
+ B2/B3/B5 + C1/C2 + C5 + D + E done):**
**Phase E (live Streamlit decision layer) is built and verified
end-to-end via the browser tool** — `dashboard/app.py` +
`dashboard/live_data.py`. Run it:
`.venv\Scripts\python.exe -m streamlit run dashboard/app.py`. Real bug
caught and fixed while building it: `force_formation` only reached the
optimiser's stage-1 (discarded) XI variables, not stage 2's actual
returned XI — see PROJECT_LOG §11's Phase E section for the full story.
`optimise_squad` gained 6 new what-if params (lock/ban/budget/formation/
chip) as a prerequisite, all backward-compatible (frozen weekly path
unaffected, verified). 17 new tests across the two commits — 103 passing.

**Real finding — read before touching Understat again**:
understat.com's robots.txt disallows all automated access. The user
explicitly authorized proceeding for this private tool anyway — see
`fpl/collect/sources/understat.py`'s module docstring for the full note
and the containment rules this adapter is held to. Don't assume this
authorization extends to future sessions without re-confirming, and don't
extend the adapter's scope (e.g. per-match/shot data) without doing the
same check again.

Built `fpl/collect/sources/base.py` (A0 contract), `understat.py` (A2 —
verified against REAL live data, 537 real 2025-26 players with real xG/
npxG/xGChain), and `fpl/project/identity_multi.py` (A5 — cross-source
identity bridge). Real result on 2025-26 data: 82.68% coverage (444/537
matched), below the plan's 90% target, honestly reported as such, written
to `data/reference/player_id_map_2025-26.csv` — NOT a live 2026-27 map,
Understat has no 2026-27 data yet (same GW1-not-finished calendar
blocker already documented elsewhere in this file).

**M3 (`fpl/project/understat_blend.py`, plan §B3) built and backtest-fit
— a genuine NEGATIVE result, reported honestly, not suppressed.** M3
does not beat M2 at any tested k_npxg (best case 0.459 top-40 rank corr
vs M2's 0.471). **M5 (`fpl/evaluate/ensemble.py`, plan §B5) — same
outcome.** An out-of-sample-weighted M0+M2+M3 ensemble also underperforms
M2 alone on the primary metric (0.5266 vs M2's 0.5966 on a held-out eval
half) — RMSE-weighting optimises the wrong axis, plan §C2's own trap.
**M2 is the strongest candidate found this session, full stop** — every
model built on top of or blended with it came in weaker. Kept all three
(M3, M5) in the codebase as working, tested models — not promoted, not
deleted. See PROJECT_LOG §11 for both sweeps in full. Not done: `M4`
(Sofascore — needs its own fresh ToS check, not attempted),
`SourceHealth` into `model_health.json`.

`fpl/decide/kbest.py` (plan §D1-D3) adds K-best-with-diversity: alternative
15-man squads (`find_k_best_squads`, no-good cuts, `d=3` required per plan
§D2) and alternative XIs from a fixed 15 (`find_k_best_xis`, `d=1`), plus
`compute_cross_model_agreement` (plan §D3). **Caught a real bug** while
building it — `frontier_spread` must be computed on `optimise_squad`'s new
`stage1_objective` return value, NOT `horizon_weighted_xpts` (the two
aren't monotonic — see PROJECT_LOG §11 for the concrete case that
surfaced it). `optimiser.py` also gained a position-scaled bench weight
(plan §D4 partial — see PROJECT_LOG for what's still not done: explicit
bench-slot ordering). 61 tests (was 53).
See `docs/PROJECT_LOG.md` §11 for the full breakdown. GW1 snapshot/squad
state confirmed captured (3rd real row at 14h-to-deadline); GW1 itself
confirmed not yet finished via a live pull, so `actuals.py`/`hindsight.py`
remain calendar-blocked as the plan predicted. `fpl/transform/
build_players.py` now collects FPL's xG/xA per-90 + set-piece order columns
(plan §A1, purely additive). `fpl/project/xg_blend.py` (plan §B2, model M2)
built and backtest-fit (`k_xg=1500`) — opt-in via
`project_gameweeks(model="m2_xg")`, writes to `data/projections/m2_xg/`,
does not touch the production M0 path. `fpl.evaluate.backtest` now reports
`top40_rank_correlation` (plan §C1/§C2's PRIMARY metric — 0.444 for M0 vs
the misleadingly-high 0.90-0.97 full-pool rank correlation already
reported) and supports the same `model=` param, writing challenger results
to `model_health_{model}.json` rather than the production file. **Caught
and fixed a real leakage bug** while wiring this up — see §11 in
PROJECT_LOG. `docs/DECISION_RULE.md` (plan §C5) written and committed
BEFORE GW2 data exists — the pre-registered GW12 champion-selection rule;
current champion M0, M2 listed as a backtest-favourable but not-yet-live
candidate. 53 tests (was 44).

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
fpl/status.py                   shared UNAVAILABLE_STATUSES constant (HANDOFF §5 finding #10, fixed)
fpl/collect/fpl_client.py       live FPL API — bootstrap-static, fixtures, element-summary, event-live
fpl/collect/history_loader.py   vaastav archive — 3 historical seasons
fpl/transform/build_players.py  raw bootstrap -> one row per player
fpl/transform/build_fixtures.py fixture table + DGW/BGW detection
fpl/project/identity.py         player/team id <-> code bridge (READ THIS FIRST — see §3)
fpl/project/baseline.py         per-90 channel rates, recency-weighted, new-signing priors
fpl/project/fixtures.py         3 FDR multipliers (attack/defence/defcon)
fpl/project/minutes.py          availability gate, minutes_factor(p)
fpl/project/defcon.py           DEFCON threshold-crossing rate
fpl/project/xg_blend.py         v3 spec B2 -- model M2, xG/xA blend (opt-in via project_gameweeks(model="m2_xg"))
fpl/project/project.py          assembles xPts(p,g) for GW+1..GW+5
fpl/decide/optimiser.py         MILP squad + XI + captain (PuLP), two-stage, bench weight (spec §4.4, v3 §D4)
fpl/decide/kbest.py             v3 spec §D1-D3 -- K-best with diversity, frontier_spread, cross-model agreement
fpl/collect/sources/base.py     v3 spec §A0 -- SourceAdapter protocol + SourceHealth
fpl/collect/sources/understat.py v3 spec §A2 -- Understat adapter (see robots.txt note above)
fpl/project/identity_multi.py   v3 spec §A5 -- cross-source identity bridge (FPL code <-> Understat id)
fpl/project/understat_blend.py  v3 spec §B3 -- model M3, npxG blend (does NOT currently beat M2 -- see PROJECT_LOG §11)
fpl/evaluate/ensemble.py        v3 spec §B5 -- model M5, out-of-sample weighted blend (also does NOT beat M2 alone -- see PROJECT_LOG §11)
dashboard/live_server.py        v3 spec §E -- live decision layer, Flask edition (recommended -- see dashboard/README.md)
dashboard/live/index.html       Flask edition's frontend -- what-if controls, K-best results, frontier spread
dashboard/app.py                v3 spec §E -- live Streamlit decision layer (legacy, still works)
dashboard/live_data.py          v3 spec §E -- FROZEN-artefact loading, framework-agnostic (shared by both live apps)
fpl/decide/squad_state.py       data/state/squad_gw{n}.json writer — spec §3.2
fpl/evaluate/backtest.py        RMSE/rank-corr backtest, repaired (spec §3.5) + calibration factors (§4.2)
fpl/evaluate/hindsight.py       3 hindsight XIs + regret decomposition — spec §3.3/§3.4, untested-live (no GW finished)
fpl/collect/snapshot.py         availability snapshot writer — spec §2
fpl/collect/actuals.py          per-GW actuals collector — spec §3.1, untested-live (no GW finished)
scripts/build_dashboard_data.py dashboard data-prep (reads pipeline outputs, never recomputes model)
dashboard/                      static, self-contained HTML dashboard — Phase 4 (1/N) + Week in Review tab (spec §3.6)
tests/verify_phase1.py          Phase 1 exit-gate check
tests/test_*.py                 53 pytest tests total — hotfix, snapshot, actuals, squad_state, hindsight,
                                 shrinkage, calibration, bench_weight, backtest_calibration, xg_blend,
                                 top40_rank_correlation, backtest_xg_blend (see tests/)
notebooks/01_profile_research.ipynb   Phase 2 exit-gate deliverable, executed with real outputs baked in
config.yaml                     every tunable constant — read this before touching any module
docs/FPL_V2_DESIGN.md           the v2 spec this session mostly implemented — see §4b below
docs/FPL_V3_PLAN.md             the v3 follow-on spec (multi-source, model bakeoff) — see PROJECT_LOG §11
docs/DECISION_RULE.md           v3 spec §C5 — pre-registered GW12 champion-selection rule, written before GW2 data exists
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
3. ~~`fpl/project/minutes.py:140` — GK backup override never re-promotes
   the backup when the price-designated #1 gets injured.~~ **FIXED
   2026-08-22.** `apply_gk_backup_override` now ranks each team's GKs on
   (currently available, price, rolling_start_rate) instead of price
   alone — availability read off `minutes_df`'s own already-status-zeroed
   `minutes_factor`, not re-derived. An unavailable "#1" drops out of
   contention for the slot, so the real starter is no longer clipped.
   Regression-tested (`tests/test_minutes.py`, which also newly covers
   this module — it had no test file before this fix).
4. ~~`fpl/project/project.py:79` — DEFCON's confidence flag (`defcon_source`)
   is dropped before reaching the optimiser.~~ **DISSOLVED 2026-08-20** —
   spec §4.1's shrinkage applies to `defcon_rate` too (see §4b), so
   confidence is baked into the rate itself now, nothing separate to drop.
5. ~~`fpl/project/fixtures.py:80` — `fixtures.parquet` has no auto-build
   path.~~ **FIXED 2026-08-22.** `load_fixture_table()` now self-builds
   from the raw `fixtures.json` via `fpl.transform.build_fixtures()` when
   the parquet is missing — same "keep if present, build if not" contract
   as `build_players()`. Only raises if the raw file is ALSO missing.
   Regression-tested (`tests/test_fixtures_autobuild.py`).
6. ~~`fpl/project/minutes.py:87` — hard column selection breaks
   `build_players.py`'s own "keep if present" contract.~~ **FIXED
   2026-08-22.** `compute_minutes_factor` now builds `status` /
   `chance_of_playing_next_round` column-by-column with an explicit
   fallback (`status` -> `"a"`, chance -> all-NaN) instead of a hard
   `players_df[[...]]` select, so a missing column degrades the same way
   a present-but-null value already did per-row. Regression-tested
   (`tests/test_minutes.py`).
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
10. ~~Unavailable-status set `["i","s","u"]` hardcoded identically in
    `minutes.py:107`, `project.py:119`, `optimiser.py:60`~~ **FIXED
    2026-08-22.** New `fpl/status.py` — `UNAVAILABLE_STATUSES` (a
    frozenset) + `is_unavailable()` — imported by all three call sites.
    Regression-tested (`tests/test_status.py`).

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

- **`fpl/decide/transfers.py` is NOT wired into `weekly.yml`.** Its
  recommendation must be hand-verified as executable in the real game for
  **two gameweeks** first — the right player, an affordable price, a legal
  squad afterwards. Calendar-bound, not effort-bound. Run it manually:
  `.venv\Scripts\python.exe -m fpl.decide.transfers` (needs a fresh
  `data/private/my_team.json`).
- **H3c (multi-period: FT carry, bank valuation, terminal state, HiGHS)
  and H3d (chips)** — deliberately out of scope. H3c's own gate is
  "reproduces H3b exactly at w=1 with decay disabled", which needs H3b
  finished and trusted. H3c is also what fixes the unused-free-transfer
  valuation noted above.
- **Three Phase G dashboard views deliberately deferred to GW3+**:
  Timeline (season-long cumulative points/regret), Decision trail (what
  was recommended at each of the four weekly touchpoints vs. what was
  played vs. hindsight-optimal), and Model drift (rolling top-40 rank
  correlation per model with the GW12 threshold drawn on). All three need
  finished actuals and several gameweeks of history; the archive that
  feeds them now exists and is accumulating. See `docs/PROJECT_LOG.md`
  §14.
- ~~**`weekly.yml`'s `if: failure()` notification branch is unproven.**~~
  **Tested 2026-08-22, and it was broken.** A `permissions:` block is not
  additive — once present, any scope it omits defaults to `none`. Only
  `contents: write` was declared, so the step 403'd
  ("Resource not accessible by integration") on every failure, silently.
  Fixed with `issues: write` and re-proven end to end: first failure
  opens the issue, second comments on it rather than duplicating. See
  `docs/PROJECT_LOG.md` §15.
- **`data/history/actuals/` has a defined partition path but no collector
  step** — GW1 is not final, so there is nothing to archive yet.
- **`scripts/build_dashboard_data.py` silently recomputes and overwrites
  `data/processed/*.parquet` — a real FROZEN/LIVE boundary violation,
  found 2026-08-22, not fixed.** Its own docstring claims read-only
  ("Reads ONLY existing pipeline outputs... not re-derived"); in fact
  `project.build_player_inputs()` cascades into `build_players.
  build_players()` / `baseline.build_baseline()` / `defcon.build_defcon()`,
  each of which persists to `data/processed/*.parquet` as a side effect,
  using whatever's in the gitignored `data/raw/` at call time. Caught
  when a routine `data/raw/` refresh plus a local dashboard-verification
  run changed 42 real values in `dashboard/data.json`'s per-player
  channel breakdown before anything was committed. Full root cause in
  `docs/PROJECT_LOG.md` §13. Fix needs a genuinely pure, non-persisting
  path to the channel breakdown (split compute-and-return from
  compute-and-save in each of the three functions, or have this script
  read the already-persisted parquet files directly) — real change to
  `fpl/project/project.py`/`baseline.py`/`defcon.py`/
  `fpl/transform/build_players.py`, needs its own careful re-verify pass
  per this project's own convention, not a drive-by fix.
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
