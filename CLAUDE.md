# CLAUDE.md — FPL Points-Maximization Framework

Orientation for any Claude session picking up this repo. Read this first;
it points to the deeper docs rather than duplicating them. If something
here conflicts with a doc below, the doc is more likely current — but
also more likely to be about to change, so check `docs/PROJECT_LOG.md`'s
most recent entry for the actual latest state.

## What this is

A personal, real-money Fantasy Premier League decision tool: pulls live
FPL data, projects expected points per player per gameweek, and solves a
MILP for the optimal 15-man squad, starting XI, and captain. Not a
demo — it makes real transfer/captaincy decisions for the user's actual
team, so correctness bugs have real consequences and get taken seriously
(see "Project culture" below).

## Read these, in this order, before doing anything non-trivial

1. `docs/HANDOFF.md` — the living state-of-the-world doc. Module
   inventory, environment gotchas, the full bug history, what's built
   vs. not, what's open. **Always check its dated "Status as of" header
   first** — it's updated every session.
2. `docs/PROJECT_LOG.md` — the detailed changelog. Every fix has its
   root cause and the commit it landed in. Search this before assuming
   something is a new bug — it may be a known, already-investigated one.
3. `docs/DECISION_RULE.md` — the pre-registered model-selection rule
   (v3 plan §C5). **Do not edit the rule itself** except to fix a genuine
   drafting error, logged as a diff — the whole point is that it was
   locked before any live result existed to tempt a rewrite.
4. `docs/FPL_V3_PLAN.md` (if present locally) / the v2 execution plan —
   the design docs. Section numbers ("plan §4.2", "spec §3.5") referenced
   throughout the code and both docs above point back into these.
5. `config.yaml` — every tunable constant, with the *why* in comments.
   If a number in the model looks wrong, check here before assuming a
   code bug.

## Environment gotchas

- **Always use `.venv\Scripts\python.exe` explicitly**, never bare
  `python`. This machine's default `python` resolves to an MSYS2 build
  with no prebuilt wheels for pandas/numpy. Example:
  `& ".venv\Scripts\python.exe" -m fpl.decide.optimiser`
- Some scripts need `PYTHONPATH=.` set first (PowerShell:
  `$env:PYTHONPATH="."`) — e.g. `scripts/build_dashboard_data.py`.
  `dashboard/app.py` and `dashboard/live_server.py` insert the repo root
  onto `sys.path` themselves, so they don't need this.
- Tests: `.venv\Scripts\python.exe -m pytest tests/ -q` from repo root.

## The one fact that breaks everything if you get it wrong

**FPL's `id` field is NOT stable across seasons — `code` is.** Every join
between the current season and historical/cross-source data goes through
`fpl/project/identity.py` (`attach_current_player_id` /
`attach_current_team_id`). Never join raw `element`/`team` id columns
directly across seasons or sources — it silently mismatches ~99% of
players with plausible-looking, not obviously wrong, numbers. See
`fpl/project/identity.py`'s own docstring and `HANDOFF.md` §3.

## Architecture — the pipeline, in order

```
fpl/collect/          live FPL API + vaastav historical archive + source adapters (Understat; Sofascore abandoned permanently, see below)
fpl/transform/        raw -> one row per player / fixture table + DGW/BGW detection
fpl/project/          per-90 rates -> fixture multipliers -> availability -> xPts(p,g)   [PROJECT layer]
fpl/decide/           MILP squad + XI + captain (PuLP), K-best with diversity            [DECIDE layer]
fpl/evaluate/         backtest (one-off retrospective), hindsight (post-GW regret)
dashboard/            two static views + two live decision-layer apps (see below)
scripts/              one-off / regeneration scripts (never part of the model itself)
```

Every stage is a pure function of its inputs — the FROZEN/LIVE boundary
(v3 plan §E1) is enforced structurally: the projection pipeline
(`fpl/collect`, `fpl/transform`, `fpl/project`) is network-bound and only
ever runs via the committed pipeline / GitHub Actions; the decision layer
(`fpl/decide`) is a pure, offline, deterministic function of whatever
xPts vector and constraints it's handed, and may be re-solved freely,
including live from a dashboard, without ever corrupting a projection.

## Model registry

Multiple projection models exist behind one interface
(`fpl/decide/optimiser.py`/`fpl/decide/kbest.py` are model-agnostic):

| Model | What it is | Status |
|---|---|---|
| M0 `rules_v1` | shrinkage/calibration/bench-weight rules model | **Champion** — the live path, `project.py`'s default |
| M2 `xg_blend` | FPL's own Opta xG/xA blended in | Backtest-favourable, not live |
| M3 `understat_blend` | + Understat npxG/xGChain | Does not beat M2 |
| M5 `ensemble` | out-of-sample-weighted blend of M0+M2+M3 | Does not beat M2 alone either |
| M4 `sofascore` | + Sofascore rating | **Abandoned permanently** (2026-08-22) — see "Sofascore" below |

**M0 remains champion until `docs/DECISION_RULE.md`'s pre-registered
GW12 rule says otherwise — never promote a challenger informally, no
matter how good a backtest number looks.** Backtest results are a single
retrospective split against a *different* season with no
gameweek-clustered CI; the decision rule exists specifically to stop
"switch to whatever's hot this week" reasoning.

## Dashboard — four apps, two purposes

- `dashboard/index.html` (generated from `dashboard/template.html` +
  `dashboard/data.json` via `scripts/build_dashboard_data.py`) — static,
  self-contained, offline snapshot. **Edit `template.html`, never
  `index.html` by hand**, then regenerate. Reads only committed
  `data/output/*.json` + `data/processed/*.parquet` — never recomputes.
- `dashboard/live_server.py` + `dashboard/live/index.html` — **the
  recommended live decision layer.** Flask JSON API + a purpose-built
  frontend for what-if squad exploration (lock/ban/budget/formation/chip,
  K-best alternatives). Run: `.venv\Scripts\python.exe -m
  dashboard.live_server`, open http://127.0.0.1:5000/.
- `dashboard/app.py` — the original Streamlit live decision layer.
  Legacy, still works, kept for quick prototyping. Run:
  `.venv\Scripts\python.exe -m streamlit run dashboard/app.py`.
- `dashboard/live_data.py` — shared, framework-agnostic data-loading
  module both live apps import. Never call the projection pipeline from
  here; it only reads already-committed artefacts.

Every live solve is labelled EXPLORATORY, the committed
`gw{n}_recommendations.json` is always shown pinned alongside as the
canonical answer, and every live solve is logged to
`data/scratch/live_solves.jsonl` (gitignored, never a canonical
artefact) — never written to `data/state/`. Don't weaken any of these
three guarantees when touching either live app.



## Project culture (why bugs get root-caused, not patched)

This codebase has a documented history of confirmed bugs that were each
a *plausible-looking* result with a real error underneath (wrong-
direction fixture multiplier, a confidence gate with a boundary bug, a
GK backup that never got re-promoted, a K-best solve that silently
dropped its own constraints). The working norm, visible throughout
`docs/PROJECT_LOG.md`:

- **An unexplained win gets investigated, not celebrated** — this is
  literally condition 5 of the pre-registered decision rule.
- **Verify by running against real data, not just unit tests** — re-run
  `fpl.decide.optimiser` and eyeball the squad after touching
  `baseline.py`/`minutes.py`/`fixtures.py`/`defcon.py`/`project.py`/
  `optimiser.py`. Injured players, backup keepers, and thin-sample
  outliers ranking highly are the tell.
- **Report negative results honestly** — M3/M5 not beating M2, Sofascore
  being blocked, a dashboard limitation still being true, are all
  documented plainly rather than smoothed over.
- **A shared constant/helper only counts as centralized if call sites
  actually call it** — `fpl/status.py`'s `is_unavailable()` was written
  to close a duplication finding but isn't called by any of the three
  sites it targeted; the `UNAVAILABLE_STATUSES` constant alone was
  adopted. Worth finishing if you're in that file.

## Known open items (as of the last session — check HANDOFF.md's header for updates)

- **`scripts/build_dashboard_data.py` silently recomputes and overwrites
  `data/processed/*.parquet`** — contrary to its own docstring and to
  "never recomputes" above. `project.build_player_inputs()` cascades into
  three functions that each persist to `data/processed/*.parquet` as a
  side effect, using whatever's in the gitignored `data/raw/` at call
  time. Found 2026-08-22 (docs/PROJECT_LOG.md §13); not fixed — needs a
  genuinely pure, non-persisting path. A local run of this script can
  silently corrupt the committed snapshot's consistency with no warning.
- `fpl/decide/optimiser.py`: locking a player who's been filtered out by
  `apply_availability_filters` (e.g. injured) is silently ignored rather
  than failing loudly, despite the docstring's explicit claim otherwise —
  now reachable via the live dashboards' unfiltered lock-search UI.
- `dashboard/live_server.py`: an unrecognized `force_formation` value
  silently solves unconstrained instead of erroring; captain/vice are
  matched to squad rows by `web_name` string instead of `id`.
- `fpl/decide/transfers.py`, `fpl/decide/chips.py`,
  `fpl/evaluate/evaluate.py` — not built, explicitly out of scope for
  v2/v3 as written.
- Sofascore (M4) — **abandoned permanently, not just blocked.** Root-caused
  twice (2026-08-20/21, and reconfirmed 2026-08-22): an edge ACL returns
  an identical 403 from Sofascore's own origin on both `sofascore.com`
  and `api.sofascore.com`, most plausibly this network's geography. Any
  client library (ScraperFC included) hits the same block; ScraperFC's
  Sofascore module additionally now requires a headed anti-bot-evasion
  browser, which won't be built regardless of authorization. See
  `docs/DECISION_RULE.md`'s M4 row. Do not re-litigate this without new
  facts (e.g. a genuinely different network).
- GW12 review and the fixture-multiplier-collapse check (plan A4) are
  calendar-blocked, not effort-blocked — nothing to do until real
  gameweeks pass.

## Testing conventions

Pure unit tests, no network, no real files where avoidable — `tmp_path`
+ `monkeypatch` on module-level path constants (see any `tests/test_*.py`
for the pattern). Every real bug fix ships with a regression test that
fails against the pre-fix code. A module with production logic and no
test file (this has happened — `fpl/project/minutes.py` had none for
most of this project's life despite housing several of its bugs) is
worth flagging, not just working around.

