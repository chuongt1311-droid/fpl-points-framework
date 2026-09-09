# C1 — Dashboard revamp: FPL-page clone (`app2`) + static pitch view

**Status:** design, pre-implementation. Brainstormed 2026-09-09.
**Owner:** Charlie Trinh.
**Supersedes:** the never-written "app2 design doc" that
`dashboard/app2/__init__.py` references. This IS that doc.
**Relates to:** `dashboard/README.md` (four-apps inventory),
`CLAUDE.md` "Dashboard — four apps, two purposes", the FROZEN/LIVE
boundary (v3 plan §E1), the three live-solve guarantees.

---

## 1. Problem

The project has **four** dashboards:

| App | Purpose | State |
|---|---|---|
| `dashboard/index.html` (static, from `template.html`) | offline snapshot of the upcoming GW | live, 9 tabs, committed |
| `dashboard/live_server.py` + `live/index.html` | live what-if squad explorer (Flask) | recommended live tool |
| `dashboard/app.py` | original live explorer (Streamlit) | legacy, kept for prototyping |
| `dashboard/app2/` | rough demo of "replace `live_server` entirely" (Flask + htmx) | untracked, ~3 views |

Three problems:

1. **None of them looks like the FPL site.** A user who lives in the
   official app has to re-learn a bespoke tab layout to read model
   numbers. The ask: clone the official FPL page layout (pitch view,
   player cards, familiar nav) and let the **only** difference be the
   model's probabilities and xPts overlaid on it.
2. **`app2` is a demo with no spec** and no interactive solver — the one
   piece of real decision value in `live_server`.
3. **Four apps is three too many.** Two live Flask apps + one Streamlit
   app + one static generator is unmaintained surface.

## 2. Decisions already locked (brainstorm 2026-09-09)

| # | Question | Decision |
|---|---|---|
| 1 | Finish `app2` (live) or restyle the static dashboard? | **Both.** `app2` becomes the live FPL-clone app; the static snapshot borrows its pitch-view layout for the *This Week* + *My Team* tabs. Shared CSS. |
| 2 | Does `app2` keep `live_server`'s what-if solver? | **Yes — as a "Transfers" page.** FPL transfers-screen shell, every move scored live by `transfers.solve_transfers`, lock/ban + K-best. `live_server.py` + `app.py` retire once it lands. |
| 3 | (implicit) New CI surface? | **No.** `app2` is a local tool (`python -m dashboard.app2`), exactly as `live_server` is today. Only the static `index.html` stays CI-built and committed. |

## 3. What "clone the FPL page" means

The official FPL web app's primary screens, and how each maps here:

| FPL screen | `app2` route | Content |
|---|---|---|
| **Points** | `/` | Last completed GW: your XI + bench on a pitch, **actual** points per player, autosubs shown, captain doubled, GW total + rank movement. |
| **Pick Team** | `/pick` | Your current XI/bench on a pitch. Set captain/vice, swap bench order, change formation. **Model overlay** per card (see §4). No transfers here — organisation only, same as FPL. |
| **Transfers** | `/transfers` | The decision tool. FPL transfers shell — pitch + selectable player list + bank/budget bar + free-transfer count. Powered by `transfers.solve_transfers`: shows the model's recommended move(s) pre-filled, lock/ban any player, force *N* transfers, wildcard/free-hit toggle, K-best alternatives listed. Every scenario re-scored live. **Absorbs `live_server`.** |
| **Fixtures** | `/fixtures` | Fixture-difficulty radar for your clubs, next 5 GWs (already in the demo). |
| **Leagues** | `/insights` | Ownership, your differentials, template overlap, mini-league effective ownership (already in the demo). |
| **Status / history** | `/history` | GW-by-GW points & rank, chip log, transfer log with the "points since transfer" proxy (already in the demo). |

Visual north star is the **real FPL layout** — pitch with formation
rows, kit-coloured player chips, the green field, the bottom bench strip,
the top summary bar. No invented navigation metaphors. The one addition
is the model data on each card.

## 4. The player card — the only "twist"

An FPL card shows: shirt, name, club, price, next opponent (with the 1–5
difficulty colour), and the last-GW points. This card adds, below that:

- **Next-GW xPts** (model) — the headline number.
- **5-GW weighted xPts** — the horizon figure the optimiser actually uses.
- **Start probability** — `minutes_factor`, shown as a percent with a
  small bar. Once C2 (model M6) lands and is promoted, this reflects the
  parsed-news signal; until then it's the historical rate / FPL
  `chance_of_playing`. The card never invents a number the model didn't
  produce.
- **Model verdict chip** — computed against *your* squad from the
  committed transfer solve: `▲ upgrade target` / `▼ sell candidate` /
  `hold`. Advisory; the Transfers page is where you act.
- **Confidence** — the existing `confidence` (low/medium/high) as a dot.

All of these already exist in committed artefacts (`data/projections/`,
`data/output/gw*_transfers.json`, `data/processed/`). The card is a
rendering of them, not a recomputation.

## 5. Architecture

```
                         data/  (committed model artefacts)          FPL API (cheap, live)
                         projections/ · output/ · processed/         bootstrap-static · entry/picks ·
                                    │                                 entry/history · entry/transfers · event/live
                                    ▼                                          │
                    dashboard/app2/data.py  ◄──────────────────────────────────┘
                       (FROZEN model output + LIVE overlay — the split from
                        dashboard/live_data.py, unchanged)
                                    │
             ┌──────────────────────┼───────────────────────┐
             ▼                      ▼                       ▼
     /  ·  /pick             /transfers               /fixtures · /insights · /history
   (read-only pitch      (transfers.solve_transfers   (read-only, already in the demo)
    + model overlay)       live — lock/ban/force/K-best;
                           EXPLORATORY label; pinned committed
                           recommendation; logged to
                           data/scratch/live_solves.jsonl)

  dashboard/_pitch  (shared: pitch grid CSS + a render_pitch() helper)
             │
             ├── used by app2 templates
             └── used by dashboard/template.html  →  This Week + My Team tabs
                 (static, still generated by build_dashboard_data.py —
                  a rendering change only, same data.json)
```

### 5.1 FROZEN/LIVE — unchanged, restated

- **Model output** (xPts, channel breakdown, fixture multipliers,
  transfer solves) — read from committed `data/` artefacts, or a **pure
  re-solve** of `transfers.solve_transfers` / `optimise_squad` on already
  -committed inputs. The projection pipeline (`fpl/collect|transform|
  project`) is **never** invoked from `app2`.
- **Live overlay** — prices, `status`/`news`, your current picks, live GW
  scores, mini-league standings — cheap direct FPL API pulls, cached
  per-process. Never written to `data/`.
- **The three live-solve guarantees carry over verbatim** (`CLAUDE.md`):
  every interactive solve is labelled **EXPLORATORY**; the committed
  `gw{n}_recommendations.json` / `gw{n}_transfers.json` is shown **pinned**
  alongside as canonical; every solve is appended to
  `data/scratch/live_solves.jsonl` (gitignored, never canonical). The
  Transfers page must not weaken any of these.

### 5.2 `dashboard/_pitch`

A tiny shared module — one CSS block + `render_pitch(xi, bench, *, mode)`
that emits the formation-row markup. `mode="points"` (actual pts),
`mode="pick"` (model xPts + verdict), `mode="transfers"` (selectable).
Imported by `app2` templates; inlined into `template.html` at build time
by `build_dashboard_data.py` at a new `/*__PITCH_CSS__*/` placeholder so
the static file stays self-contained.

## 6. Phasing — four PRs

Each phase is independently shippable and independently useful.

### Phase 1 — Shared pitch component + static pitch view

- `dashboard/_pitch` (CSS + `render_pitch`).
- `dashboard/template.html`: *This Week* and *My Team* tabs re-rendered as
  a pitch (formation rows, chips, bench strip). Same `data.json` /
  `my_team.json` — no new data. Other tabs untouched.
- `build_dashboard_data.py`: inline the pitch CSS.
- **No server, no new CI, offline-safe.** Smallest, highest-reach change
  (the committed dashboard is what people actually open).
- Tests: `render_pitch` shaping; `build_dashboard_data` output diff is
  the pitch CSS + markup only; the archived weekly snapshot still
  self-contained.

### Phase 2 — `app2` read-only FPL-clone shell

- Restyle `app2/templates/base.html` + `index.html` to the FPL pitch
  layout using `_pitch`. Add `/pick` (Pick Team) and rework `/` into
  **Points** (last-GW actuals — extends `squad_snapshot`).
- Player card per §4 (read-only; verdict from the committed transfer
  solve).
- Keep `/fixtures`, `/insights`, `/history` — restyle to match.
- `app2/data.py`: fold in whatever `live_data.py` has that `data.py`
  lacks; add `points_view()` and `pick_team_view()`.
- Tests: each `data.py` assembly fn (stub the FPL API); Flask routes
  smoke-tested with a stubbed `data` module.

### Phase 3 — `app2` Transfers page (absorbs `live_server`)

- `/transfers`: FPL transfers shell + `transfers.solve_transfers` live.
  Pre-fill the committed recommendation; lock/ban via the player list;
  force-`N` / wildcard / free-hit toggles; K-best alternatives panel.
- Port `live_server.py`'s solve endpoint + its guarantees (EXPLORATORY
  label, pinned committed answer, `live_solves.jsonl` logging).
- Fix, on the way through, `live_server`'s two known bugs so they don't
  get carried over: unrecognised `force_formation` silently solving
  unconstrained; captain/vice matched by `web_name` instead of `id`
  (`CLAUDE.md` known-open).
- Tests: solve endpoint returns the same squad as `-m fpl.decide.transfers`
  for a fixed input; lock/ban respected; the three guarantees asserted.

### Phase 4 — Retirement + docs

- Delete `dashboard/app.py` (Streamlit) and its `requirements` entries,
  `dashboard/live_server.py`, `dashboard/live/`. Keep `live_data.py`
  only if still imported; otherwise fold into `app2/data.py`.
- `dashboard/README.md` + `CLAUDE.md`: "four apps" → **two** (static
  snapshot + `app2`). Update `docs/HANDOFF.md`.
- `PROJECT_LOG.md` §23.
- Verify nothing in `weekly.yml` or `scripts/` imported the deleted
  modules.

## 7. Non-goals

- **No new visual invention.** The design brief is "copy the FPL page."
  Implementation Phase 1 & 2 each open with a short mockup pass
  (frontend-design skill / visual companion) whose only job is matching
  the real FPL layout, not designing something new.
- **No CI/pipeline changes.** `app2` never runs in Actions.
- **No change to the model, the optimiser, or the projection pipeline.**
- **No auth, no write-back to FPL.** `app2` reads the public API; it
  never logs into an FPL account or submits a team. (Matches every
  existing dashboard.)
- **Mobile-perfect parity** with the FPL app is out of scope for v1 —
  responsive-and-usable on a phone is enough.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cloning FPL's visual design → trademark/passing-off concern | Personal, unpublished, single-user tool that reads the public API — same posture as every existing dashboard. It is not distributed or presented as FPL's. If that ever changes, revisit. Not published as an Artifact. |
| `app2` live pulls are slow / rate-limited (mini-league EO fans out) | Per-process cache already in `data.py`; the EO call is capped at the first standings page. Add a short on-disk TTL cache under `data/scratch/` if needed. |
| Porting `live_server` drops one of the three solve guarantees | Phase 3 tests assert each explicitly before `live_server.py` is deleted in Phase 4. |
| Retiring `app.py`/`live_server.py` breaks a muscle-memory workflow | Phase 4 is a separate PR; the two old apps keep working through Phases 1–3. |
| Static pitch view bloats `index.html` past a sensible size | Pitch is CSS + ~15 chips of markup; negligible next to the 872 KB `data.json` already inlined. |

## 9. Files touched

**New:** `dashboard/_pitch/` (`__init__.py` + `pitch.css`),
`dashboard/app2/templates/pick.html`, `points.html`, `transfers.html`,
`_player_card.html`, `_kbest.html`; `tests/test_app2_data.py`,
`tests/test_app2_routes.py`, `tests/test_pitch.py`.

**Modified:** `dashboard/app2/__init__.py` (routes), `dashboard/app2/data.py`
(`points_view`, `pick_team_view`, transfer-solve wiring, fold in
`live_data`), `dashboard/app2/templates/base.html` + `index.html` +
`history.html` + `insights.html` (FPL restyle),
`dashboard/template.html` (pitch view for This Week + My Team),
`scripts/build_dashboard_data.py` (inline pitch CSS),
`dashboard/README.md`, `CLAUDE.md`, `docs/HANDOFF.md`,
`docs/PROJECT_LOG.md`.

**Deleted (Phase 4):** `dashboard/app.py`, `dashboard/live_server.py`,
`dashboard/live/`, possibly `dashboard/live_data.py`.

## 10. Resolved defaults (override at review)

1. **Points page — included** in Phase 2. `squad_snapshot` already has the
   data and it is a core FPL screen; the *History* tab stays as the
   season-long view.
2. **Chips — neutral, position-coloured for v1.** A static ~20-entry
   club→kit-colour map is a Phase 2 stretch item, taken only if Phase 2
   lands with time to spare — it is what makes the pitch *read* as FPL,
   but nothing depends on it.
3. **Surviving `app2` port — 5000**, inheriting `live_server`'s. The
   `$PORT` override (merged in #8) stays.
