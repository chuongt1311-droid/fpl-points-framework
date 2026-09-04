# Dashboard (Phase 4) + Live Decision Layer (v3 plan §E)

## Three apps in this directory, three different jobs

- `index.html` (below) — the static, point-in-time "This Week" snapshot.
  Read-only, offline, no server.
- `live_server.py` + `live/index.html` — **the live decision layer,
  Flask edition.** What-if squad exploration (lock/ban/budget/formation/
  chip scenarios, K-best alternatives) against an already-committed,
  FROZEN projection — never recomputes the model, never persists a live
  solve as your official squad. This is the recommended way to run the
  live layer now — same guardrails as `app.py` below, a purpose-built UI
  instead of Streamlit's generic widget chrome. Run it:

  ```powershell
  & ".venv\Scripts\python.exe" -m dashboard.live_server
  ```

  Then open http://127.0.0.1:5000/. (Or use the `.claude/launch.json`
  entry `fpl-live-decision-layer-flask` if you're driving this through
  the Claude Code preview tool.)

- `app.py` — the **legacy** Streamlit live decision layer (v3 plan §E's
  original implementation). Left in place, still works, still useful if
  Streamlit's faster prototyping loop is wanted for a quick one-off
  experiment. Run it:

  ```powershell
  & ".venv\Scripts\python.exe" -m streamlit run dashboard/app.py
  ```

Both live apps share the exact same data-loading module
(`dashboard/live_data.py`, framework-agnostic — no Streamlit import) and
the exact same decision logic (`fpl/decide/optimiser.py`,
`fpl/decide/kbest.py`). Neither is the source of a projection; see
`live_data.py`'s module docstring for the full FROZEN/LIVE boundary rule
(plan §E1) and the reproducibility guard (plan §E3 — EXPLORATORY
labelling, the canonical recommendation always pinned alongside, every
live solve logged to `data/scratch/live_solves.jsonl`, never written to
`data/state/`).

**Real bug fixed while building the Flask edition**: `kbest.
find_k_best_squads` never accepted `locked_ids`/`banned_ids`/
`banned_clubs`/`budget_override`/`force_formation`/`chip` — every
what-if control in `app.py`'s sidebar was silently inert for K-best
squad search; every "constrained" solve quietly returned the
unconstrained frontier. Fixed in `fpl/decide/kbest.py`, regression-
tested (`tests/test_kbest.py`), and `app.py`'s own call site updated to
actually pass its sidebar state through. See `docs/PROJECT_LOG.md` for
the full story of how the previous session's own verification missed it
(it only exercised the no-constraints path).

## What the static dashboard (`index.html`) is

A static, self-contained HTML dashboard (`index.html`) implementing the 5
views from `docs/FPL_EXECUTION_PLAN.md` §7 — This Week, Player Explorer,
Fixture Radar, Chip Planner, Model Health — using the exact token system,
typography, and Fixture Ticker signature element from plan §7.1. Plus a
sixth, added later: **Bakeoff** (`docs/FPL_V3_PLAN.md` §9 Phase F) — M0 vs
M2 vs M3's statistical scorecard side by side, reading only committed
`data/output/model_health*.json` files, with the pre-registered GW12
decision rule and an honest "not a promotion signal" banner. (Plan §9's
"Alternatives" view — K-best frontier + cross-model agreement — was
deliberately NOT added here: K-best is architecturally live-only per plan
§E1, and already exists properly in the Flask live decision layer below.)

**Deviation from the plan worth flagging:** §7.1/§11 describe a Streamlit
app (`dashboard/app.py`, `.streamlit/config.toml`). This is a static HTML
file instead — no server, opens directly in a browser, works offline. It
was built this way because it's a point-in-time snapshot dashboard (the
plan's own description of view 1) and a static file is the simplest thing
that satisfies that.

## Files

- `template.html` — the dashboard shell: all CSS/JS, with a
  `/*__DATA__*/` placeholder for the data payload. **Edit this file**, not
  `index.html`.
- `data.json` — the data payload alone, for inspection/debugging.
- `index.html` — **generated.** `template.html` with `data.json` inlined
  into it, so the dashboard is one fully offline-capable file. Don't hand-edit.
  Shows the upcoming gameweek, not a fixed one (PROJECT_LOG §18).
- `weeks/` — **generated.** One frozen snapshot per gameweek
  (`gw{N}.html`, a byte copy of that week's `index.html`) plus a
  `weeks/index.html` list. Produced by `scripts/archive_dashboard_week.py`,
  run right after `build_dashboard_data.py`. Don't hand-edit.
- `my_team.json` — **generated, gitignored.** Your actual squad + a
  roll/1-transfer/2-transfer/wildcard comparison, from
  `scripts/build_my_team_data.py` (run it *before* `build_dashboard_data.py`,
  which inlines it into `index.html`'s "My Team" tab). Uses your
  `data/private/my_team.json` sell prices when fresh, else market price as
  a proxy. In CI there's no private file so the committed dashboard shows
  the market-price version.
- `live_server.py` / `live/index.html` — the Flask live decision layer.
- `app.py` / `live_data.py` — the legacy Streamlit live decision layer
  and its shared, framework-agnostic data-loading module.

## How to regenerate the static dashboard

Run after any pipeline re-run (new gameweek, model change, re-optimised squad):

```powershell
$env:PYTHONPATH="."; & ".venv\Scripts\python.exe" scripts\build_dashboard_data.py
```

This reads `data/output/gw{target}_recommendations.json` (target = the
upcoming GW, or the latest solved), `data/output/model_health.json`,
`data/output/gw{target}_transfers.json` if present,
`data/processed/*.parquet`, and `data/projections/gw{n}.parquet` — the
dashboard's data contract per plan §6: **read pipeline outputs, never
recompute the model.** The one exception, and it's a read not a
recompute: the per-channel points breakdown (goals vs assists vs clean
sheets vs DEFCON vs bonus — the Player Explorer's "why is the model
recommending this guy" view) isn't saved as a pipeline artifact anywhere,
so `scripts/build_dashboard_data.py` calls `fpl.project.project`'s own
`build_player_inputs` / `compute_channel_pts_per_fixture` functions
directly to get it — same code path production uses, not reimplemented.

## Known gaps in this v1

- **Chip Planner** has no live recommendations — `fpl/decide/chips.py`
  doesn't exist yet (see `HANDOFF.md` §6). The tab shows the config
  thresholds it will use, honestly labelled as not-yet-computed.
- **This Week**'s transfer line renders `gw{n}_transfers.json` when a
  manual `fpl.decide.transfers` run has committed one (it's not in
  `weekly.yml` yet — HANDOFF §9). GW1 shows initial-build text; a later
  GW with no artefact shows a "runs manually" note.
- Fixture Radar and the ticker both use `fixture_multipliers.parquet`,
  which is season-long and already has no missing-file gap (unlike
  `fixtures.parquet` itself — see `HANDOFF.md` §5 item 5).
- The Flask live layer's K-best solve runs synchronously and blocks the
  request until all `k` squads are found (same as `app.py` before it —
  streaming results as each one lands is a real follow-up, see plan §E4).
