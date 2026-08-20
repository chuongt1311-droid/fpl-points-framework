# Dashboard (Phase 4)

## What this is

A static, self-contained HTML dashboard (`index.html`) implementing the 5
views from `docs/FPL_EXECUTION_PLAN.md` §7 — This Week, Player Explorer,
Fixture Radar, Chip Planner, Model Health — using the exact token system,
typography, and Fixture Ticker signature element from plan §7.1.

**Deviation from the plan worth flagging:** §7.1/§11 describe a Streamlit
app (`dashboard/app.py`, `.streamlit/config.toml`). This is a static HTML
file instead — no server, opens directly in a browser, works offline. It
was built this way because it's a point-in-time snapshot dashboard (the
plan's own description of view 1) and a static file is the simplest thing
that satisfies that. If Phase 5 automation (plan §8, weekly GitHub Actions
regeneration + hosting) wants live interactivity beyond client-side
filtering — e.g. editable chip state persisted across sessions — that's
the point to reconsider Streamlit. Until then, this is strictly cheaper to
build, run, and deploy (e.g. as a GitHub Pages artifact).

## Files

- `template.html` — the dashboard shell: all CSS/JS, with a
  `/*__DATA__*/` placeholder for the data payload. **Edit this file**, not
  `index.html`.
- `data.json` — the data payload alone, for inspection/debugging.
- `index.html` — **generated.** `template.html` with `data.json` inlined
  into it, so the dashboard is one fully offline-capable file. Don't hand-edit.

## How to regenerate

Run after any pipeline re-run (new gameweek, model change, re-optimised squad):

```powershell
$env:PYTHONPATH="."; & ".venv\Scripts\python.exe" scripts\build_dashboard_data.py
```

This reads `data/output/gw1_recommendations.json`, `data/output/model_health.json`,
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
- **This Week**'s transfer line is hardcoded to "initial squad, no
  transfer to evaluate" because `fpl/decide/transfers.py` doesn't exist
  yet either. Once it does, wire its output through `data/output/` and
  this script the same way `gw1_recommendations.json` is wired now.
- **Model Health**'s known-limitations list embeds two items from the
  unresolved punch list in `HANDOFF.md` §5 (the `conceded_pts`
  wrong-direction fixture multiplier bug, and DEFCON confidence not being
  surfaced) *in addition to* the plan's own v1 limitations — because both
  currently affect the live GW1 projections this same dashboard is
  showing. If/when those get fixed, remove the two `KNOWN UNFIXED` entries
  from the `known_limitations` list in `scripts/build_dashboard_data.py`.
- Fixture Radar and the ticker both use `fixture_multipliers.parquet`,
  which is season-long and already has no missing-file gap (unlike
  `fixtures.parquet` itself — see `HANDOFF.md` §5 item 5).
