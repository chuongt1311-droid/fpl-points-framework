# Phase G — History Layer (bitemporal projection archive)

**Status:** design approved 2026-08-22, not yet implemented.
**Implements:** `docs/FPL_V4_PLAN.md` §4 (Phase G), with two documented
deviations from that document's text (§3.2 and §5.1 below), both agreed
with the user before writing.
**Scope decision (user, 2026-08-22):** storage layer + query layer + two
dashboard views. Three further v4-plan views are deliberately deferred —
see §7.

---

## 1. Problem

Every pipeline run overwrites `data/projections/` and `data/output/` in
place. Four runs per gameweek collapse into one surviving file, so the
answer to *"did Friday's press conference change my recommendation, and
was that change right?"* is destroyed within days of being created. This
is the only irreversible gap left after Tier 0 — `data/snapshots/` already
solves the equivalent problem for availability beliefs, and does it well.

The data is **bitemporal**: every projection has an *event time* (which
gameweek it is about) and an *as-of time* (when it was computed, and
therefore what was known). The availability snapshot already records both
(`snapshot_ts` + `hours_to_deadline` per row). Projections record neither.

A crude stopgap landed in Tier 0.2 (`weekly.yml` copies each run into
`data/history/{timestamp}/`) and has captured one partition
(`20260822T125314Z`, verified live in run #3 → commit `c8ec4fa`). It has
no gameweek partitioning, no provenance, and no append-only enforcement.
This spec replaces it.

## 2. Goals and non-goals

**Goals**
- Append-only, immutable, provenance-stamped archive of every run's
  projections, decisions, and model-health artefacts.
- Queryable without an ingest step, by gameweek / as-of / model.
- Detect and exclude partially-written runs rather than silently serving
  them as if complete.
- Survive a season boundary (see §3.4 — the `code`-not-`id` rule).
- Make archive coverage *visible*, so silent capture failure is
  impossible to miss.

**Non-goals**
- Analysis of revisions (that is what the archive *enables*; it is not
  this phase).
- Any change to the projection or decision layers. This phase reads their
  outputs and writes copies. It does not touch `fpl/project/` or
  `fpl/decide/`.
- Actuals archiving beyond defining the path — GW1 is not final, so there
  is nothing to archive and nothing to test against.

## 3. Storage design

### 3.1 Layout

```
data/history/
  projections/gw={n}/asof={ts}/model={m}/players.parquet
  decisions/gw={n}/asof={ts}/recommendation.json
  health/gw={n}/asof={ts}/model={m}/model_health.json
  actuals/gw={n}/players.parquet            # written once, when a GW finalises
  _runs/asof={ts}/run.json                  # provenance + completion marker
  _runs/asof={ts}/id_code_map.parquet       # see §3.4
  manifest.json                             # DERIVED, gitignored
```

Hive-style `key=value` directories so DuckDB's `hive_partitioning=true`
infers `gw`, `asof` and `model` as real columns with no path parsing in
application code.

### 3.2 The `asof` key format — deviation from the v4 plan

The plan specifies `asof={utc_iso}`. **This is unimplementable on the
operator's platform.** ISO-8601 UTC contains colons (`2026-08-22T12:53:14Z`),
which Windows forbids in path names; verified empirically before writing
this spec (`OSError: The filename, directory name, or volume label syntax
is incorrect`).

`asof` therefore uses **ISO 8601 basic format**: `YYYYMMDDTHHMMSSZ`, e.g.
`20260822T125314Z`. Windows-safe, lexicographically sortable (so
`ORDER BY asof` is chronological without parsing), unambiguous, and
already the format the Tier 0.2 crude step happened to use — so the one
existing partition needs no timestamp rewriting.

`run.json` additionally carries the full ISO form in `asof_iso` for
display and for `CAST(... AS TIMESTAMP)`.

### 3.3 Native formats, not normalized

Projections are archived as Parquet, decisions and health as JSON —
DuckDB reads both under hive partitioning, so normalizing everything to
Parquet would buy uniform SQL at the cost of fidelity, and would mean a
future schema change makes the transform code and old partitions
disagree. For an append-only archive of record, fidelity wins.

**Precisely what "faithful" means here:** file *contents* are copied
byte-for-byte and never rewritten. File *names* are normalized, because
the pipeline encodes metadata in filenames that the partition path now
carries properly:

| Source artefact | Archived as |
|---|---|
| `data/projections/gw1.parquet` | `projections/gw=1/asof=…/model=m0_rules/players.parquet` |
| `data/projections/m2_xg/gw1.parquet` | `projections/gw=1/asof=…/model=m2_xg/players.parquet` |
| `data/output/gw1_recommendations.json` | `decisions/gw=1/asof=…/recommendation.json` |
| `data/output/model_health.json` | `health/gw=1/asof=…/model=m0_rules/model_health.json` |
| `data/output/model_health_m2_xg.json` | `health/gw=1/asof=…/model=m2_xg/model_health.json` |

The rename is lossless — every fact the old filename encoded (`gw`,
`model`) becomes a queryable partition column instead of a string to be
parsed. Content bytes are untouched, so a partition can always be diffed
against the artefact it came from.

### 3.3a `gw` partition key vs. the `event` column — do not confuse these

A projections artefact spans the 5-gameweek horizon: the archived GW1
parquet is 3000 rows = 600 players × 5 `event` values. Therefore:

- **`gw={n}` (partition key) = the gameweek the run was *targeting*** —
  the next upcoming gameweek at run time, matching the source filename.
  It is an attribute of the *run*, and equals `run.json`'s
  `target_gameweek`.
- **`event` (column inside the parquet) = which gameweek a given row's
  xPts is for**, spanning `n … n+4`.

So a single player has five rows per partition. Any revision analysis
must fix **both** coordinates — "how did the GW3 projection move across
as-of times" means `event = 3`, not `gw = 3`, and the same `event = 3`
projection appears in the `gw=1`, `gw=2` and `gw=3` partitions computed
at different times. That is the whole point of the archive, and it is
also the easiest thing in this design to get backwards.

### 3.4 Cross-season identity — `code`, not `id`

`data/projections/gw{n}.parquet` carries `id` but **not `code`**. Per
`CLAUDE.md`'s single most important rule and `fpl/project/identity.py`,
FPL's `id` is **not** stable across seasons; `code` is. An archive whose
entire purpose is longitudinal comparison cannot be keyed on `id` alone —
this is precisely the failure mode `identity.py` exists to prevent, and
it would surface as ~99% silent mismatches with plausible-looking numbers.

Resolution that preserves §3.3's fidelity rule: **do not mutate the
archived artefact.** Instead write a per-run sidecar
`_runs/asof={ts}/id_code_map.parquet` (`id`, `code`, `web_name`; ~600
rows, sourced from `data/processed/players.parquet`, confirmed to carry
both columns). The mapping is captured per run because mid-season
additions change it. The query layer (§5) joins it automatically so
callers get `code` without touching the raw partition.

### 3.5 Append-only, and honest handling of partial runs

- Writing into an existing partition directory **raises**
  (`PartitionExistsError`). Never overwrites, never deletes. Same rule as
  the availability snapshot, extended to everything.
- **`run.json` is written LAST, as a commit marker.** A run whose
  partitions exist but which has no `run.json` is *incomplete*: the query
  layer excludes it and the manifest flags it.

The commit-marker approach is chosen over the obvious alternative
(idempotency keyed on run id, so a retry no-ops). A retried run genuinely
observed the data at a *different* time; recording it as a duplicate of
the first attempt would fabricate a revision that never happened, and
suppressing it would discard a real observation. Writing `run.json` last
gives atomic-ish semantics with no transactions and no lying about time.

Timestamp collisions are not a practical concern (two runs would have to
start within the same second), and a collision raises rather than
corrupting.

### 3.6 Run provenance (`run.json`)

| Field | Why |
|---|---|
| `run_id` | uuid4 per process |
| `asof`, `asof_iso` | the as-of coordinate, both formats |
| `git_sha` | which code produced this |
| `git_dirty` | a local run with uncommitted changes is NOT reproducible from its SHA alone — recording the SHA without this would be misleading |
| `config_sha256` | without it you cannot distinguish a real projection revision from a `config.yaml` parameter changed on a Wednesday (v4 plan §G2's own point) |
| `trigger` | `schedule` / `workflow_dispatch` / `local`, from `GITHUB_EVENT_NAME` |
| `target_gameweek` | event-time coordinate |
| `deadline_utc`, `hours_to_deadline` | the load-bearing field. A projection 4 days out and one 2 hours out are different events; computable ONLY at capture time, exactly as `snapshot.py` documents |
| `provenance` | `recorded` \| `reconstructed` (§6) |
| `archived` | list of partitions this run wrote |

### 3.7 `manifest.json` — deviation from the v4 plan

The plan wants `manifest.json` committed as *the* index of every
partition. **Made derived and gitignored instead**, rebuilt by scanning
via `python -m fpl.history.manifest`.

Reason: a single file that every scheduled run rewrites, committed by a
bot on four crons a week, is a merge-conflict generator — which is the
exact objection the plan itself raises against SQLite. Provenance lives
in the immutable per-run `run.json` files (the actual source of truth);
the manifest is a convenience index and follows the same disposability
rule as the DuckDB file. Nothing is lost: the manifest is a pure function
of the partitions.

## 4. Module layout

```
fpl/history/
  __init__.py
  paths.py       partition path construction + parsing
  provenance.py  run metadata capture
  archive.py     write path; `python -m fpl.history.archive` entry point
  manifest.py    derived index build/read
  query.py       DuckDB read model (read-only)
scripts/
  migrate_crude_archive.py
  build_history_data.py
```

`paths.py` is separate because the layout is referenced by the write
path, the read path, the manifest builder and the migration script — four
call sites that will silently drift if each constructs its own paths.
This repo has already been bitten by the inverse of this
(`fpl/status.py`'s helper written but never called by the sites it
targeted); the test suite must assert the call sites actually use it.

## 5. Query layer

### 5.1 Design

In-memory DuckDB, views over globs, no ingest step, **read-only — the
query layer never writes anything**. This mirrors the FROZEN/LIVE
discipline: reading history must never be able to corrupt it.

```sql
CREATE VIEW runs        AS SELECT * FROM read_json_auto('…/_runs/**/run.json');
CREATE VIEW projections AS SELECT * FROM read_parquet('…/projections/**/*.parquet', hive_partitioning=true);
CREATE VIEW decisions   AS SELECT * FROM read_json_auto('…/decisions/**/*.json', hive_partitioning=true);
```

Every public view is filtered to **complete runs only** (inner join on
`runs`, per §3.5) and enriched with `code` (per §3.4), so no caller can
accidentally analyse a half-written run or a season-unstable key.

`duckdb` is imported **lazily** inside `query.py` so the pipeline path
(`archive.py`) never needs it; it is added to `requirements.txt` with a
comment marking it analysis-only.

### 5.2 Python API

- `open_archive(root=None) -> Archive`
- `Archive.runs() -> DataFrame`
- `Archive.coverage() -> DataFrame` — runs per gameweek, touchpoints seen,
  gaps, incomplete runs. Backs the dashboard view in §7.
- `Archive.projections(gw=None, model=None, asof=None) -> DataFrame`
- `Archive.revisions(event, model=None, player_code=None) -> DataFrame` —
  xPts by as-of, for a fixed **`event`** (the gameweek being projected),
  across every run that projected it. The actual Phase G payload. Keyed
  on `event` rather than `gw` deliberately, per §3.3a — a revision series
  is "the same future gameweek, seen at different times", which spans
  multiple `gw` partitions.
- `Archive.sql(query) -> DataFrame` — escape hatch.

A typed API rather than raw SQL at each call site, so "which runs are
complete", "which model is champion" and the `code` join are defined once.

## 6. Migration of the crude archive

`scripts/migrate_crude_archive.py`, one-off, re-partitions
`data/history/{TIMESTAMP}/` into the §3.1 layout.

Reconstructed honestly, and only where genuinely derivable:
- `asof` — from the directory name (already the right format).
- `gw` — from artefact filenames (`gw1.parquet`, `recommendation.json`'s
  own `gameweek` field).
- `model` — from subdirectory (`m2_xg`, `m3_understat`); root-level
  artefacts are `m0_rules`, matching the pipeline's own convention.
- `hours_to_deadline` — computable from the known GW1 deadline and `asof`.
- `trigger` — known to be `workflow_dispatch` for `20260822T125314Z`
  (run #3 was manually dispatched; recorded in `docs/PROJECT_LOG.md` §12).

`run.json` is marked `provenance: "reconstructed"`. **Fields that are not
genuinely knowable are written as `null`, never guessed** — notably
`config_sha256` and `git_dirty` are not recoverable for a run whose exact
working tree is not identifiable after the fact.

The crude directory is removed in the same commit once migration is
verified. It remains recoverable from git history, so nothing is
destroyed.

## 7. Dashboard

Contract unchanged: **reads committed artefacts, never recomputes.**

### 7.1 Built now — "Archive Coverage"

What the archive actually holds: runs per gameweek, which of the four
weekly touchpoints were captured, gaps, `recorded` vs `reconstructed`
provenance, and any incomplete runs.

This is the highest-value panel available today. Tier 0's entire premise
is that silent capture failure is the real risk (a failed Actions run is
an email you learn to ignore); this makes the archive's health visible
rather than assumed. It also works with the one partition that exists.

### 7.2 Scaffolded now, fills in automatically — "Revision"

xPts by as-of timestamp for a chosen gameweek, wired to
`Archive.revisions()` for real. Until there are ≥2 complete runs within a
gameweek it renders an honest state naming exactly what is missing and
the current count — no invented chart. No code change is needed when the
data arrives.

### 7.3 Deferred, with stated reasons — Timeline, Decision trail, Model drift

All three require finished actuals and several gameweeks. Building them
now means placeholder charts of invented shape. This repo already has the
better convention (Chip Planner and Week in Review both state plainly
what is missing and why), and `docs/PROJECT_LOG.md` records a prior
decision not to build a static "Alternatives" view for essentially this
reason. Revisit at GW3+.

### 7.4 Wiring

`scripts/build_history_data.py` reads via `fpl.history.query` and writes
`dashboard/history.json`; the template gains a `/*__HISTORY__*/`
substitution hook alongside the existing `/*__DATA__*/`.

**Known conflict risk, stated up front:** this requires ~3 lines in
`scripts/build_dashboard_data.py`, which a concurrently-running background
task is refactoring (to remove its silent `data/processed/*.parquet`
overwrite — `docs/HANDOFF.md` §9). The overlap is small and in a
different region of the file (template substitution vs. the write
side-effect), but the merge must be checked rather than assumed.

## 8. `weekly.yml` changes

Replace the Tier 0.2 crude shell step with `python -m fpl.history.archive`,
and **move it earlier** — to immediately after the Decide step, before
the dashboard build. The archive then unambiguously captures "what the
pipeline decided", and stops being downstream of
`build_dashboard_data.py`'s known overwrite bug.

`scripts/build_history_data.py` runs after the dashboard build.
`data/history` is already in the commit step's `git add` list.

## 9. Testing

Per repo convention: pure unit tests, `tmp_path` + `monkeypatch` on
module-level path constants, no network, no real files where avoidable.

| Test file | Covers |
|---|---|
| `test_history_paths.py` | build/parse round-trip; rejects colon-bearing timestamps; hive-compatible output; call sites use these helpers |
| `test_history_archive.py` | correct layout written; refuses to overwrite an existing partition; `run.json` written last; a simulated mid-run failure leaves the run detectably incomplete; provenance fields captured; `id_code_map` sidecar written |
| `test_history_manifest.py` | derived index correct; incomplete runs flagged not silently included |
| `test_history_query.py` | synthetic multi-run archive → `revisions()` returns the right xPts-by-as-of series; **a fixed `event` correctly spans multiple `gw` partitions** (§3.3a — the easiest thing here to get backwards, so it gets an explicit test); incomplete runs excluded; `code` present on every public view |
| `test_migrate_crude_archive.py` | crude → new layout; `provenance: reconstructed`; unknown fields `null`, not guessed |

The synthetic multi-run fixture is what makes `revisions()` testable
**now**, without waiting for real gameweeks to accumulate.

**Beyond unit tests** (per this project's "verify against real data"
norm): run the migration against the real crude partition and confirm it
reads back through `query.py`; regenerate the dashboard and confirm
Archive Coverage reports exactly the partitions that exist on disk.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Partition schema locked in wrong, needs migration | Partitions immutable and self-describing; migration = write a new partition set, never rewrite |
| Archive silently stops (the thing it exists to prevent) | Archive Coverage view (§7.1) + the Tier 0.3 staleness assert already in `weekly.yml` |
| `id`-keyed analysis silently breaks at the season boundary | `code` sidecar (§3.4), joined by default in every public view |
| Half-written run analysed as complete | `run.json`-last commit marker (§3.5), enforced in the query layer, not left to callers |
| Merge conflict with the in-flight `build_dashboard_data.py` refactor | §7.4 — small, known, checked rather than assumed |
| Archive size growth | ~250KB/run × 4 runs × 38 GW ≈ 38MB/season. Measured, not estimated. Acceptable in git |

## 11. Implementation order

Four increments, each independently verifiable, so this does not have to
land as one large unreviewable change. Each is a gate for the next.

1. **`paths.py` + `provenance.py`** — the layout and metadata primitives.
   Gate: path round-trip tests pass, including colon rejection.
2. **`archive.py` + `weekly.yml` rewire + migration** — the write path,
   which is the irreversible half and therefore lands first. Gate: the
   real crude partition migrates cleanly and a live run writes a
   correctly-partitioned, provenance-stamped, complete-marked partition.
3. **`manifest.py` + `query.py`** — the read half. Gate: the synthetic
   multi-run fixture proves `revisions()`, including the `event`-spans-
   multiple-`gw` case.
4. **Dashboard (`build_history_data.py` + the two views)** — gate:
   Archive Coverage reports exactly the partitions on disk; Revision
   renders its honest insufficient-data state with the correct count.

Increment 2 is the one with real consequences (it changes what the
scheduled pipeline writes); 1, 3 and 4 are pure additions.

## 12. Open questions deliberately NOT resolved here

- The three deferred dashboard views (§7.3) — revisit at GW3+.
- Whether `actuals/` archiving needs its own collector step, once GW1
  finalises and there is something to archive.
- v4 plan Appendix B decisions 3 (FPL auth for real squad ingestion) and
  4 (phase ordering) — both belong to Phase H, not here.
