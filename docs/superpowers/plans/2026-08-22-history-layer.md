# Phase G History Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an append-only, provenance-stamped, bitemporal archive of every pipeline run's projections/decisions/health, queryable via DuckDB over partitioned Parquet, plus two honest dashboard views.

**Architecture:** A new `fpl/history/` package with a strict write/read split. `paths.py` owns the partition layout (one source of truth, used by all four call sites). `archive.py` copies pipeline artefacts into hive-partitioned directories and stamps each run with provenance, writing `run.json` LAST as a completion marker. `query.py` exposes a read-only DuckDB view layer that excludes incomplete runs and joins the cross-season `code` key automatically. Nothing in this package touches `fpl/project/` or `fpl/decide/`.

**Tech Stack:** Python 3.12, pandas, pyarrow, duckdb (new, analysis-only, lazily imported), pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-history-layer-design.md`

## Global Constraints

- **Always use `.venv\Scripts\python.exe` explicitly**, never bare `python`. The machine default resolves to an MSYS2 build with no pandas/numpy wheels.
- Tests: `.venv\Scripts\python.exe -m pytest tests/ -q` from repo root. All 116 existing tests must keep passing.
- `asof` timestamp format is **ISO 8601 basic**: `YYYYMMDDTHHMMSSZ` (e.g. `20260822T125314Z`). Never ISO extended — colons are illegal in Windows path names.
- Partition directories are **immutable**: writing into an existing one raises. Never overwrite, never delete.
- `run.json` is written **last**, after all of a run's partitions succeed. Its presence means "complete".
- Every public query view joins `code` (cross-season-stable) and filters to complete runs only.
- Tests are pure: `tmp_path` + `monkeypatch` on module-level path constants, no network, no real repo files.
- Model keys are exactly: `m0_rules`, `m2_xg`, `m3_understat`.
- Repo root is `Path(__file__).resolve().parents[2]` from inside `fpl/history/*.py`.

---

### Task 1: Partition path construction and parsing

**Files:**
- Create: `fpl/history/__init__.py`
- Create: `fpl/history/paths.py`
- Test: `tests/test_history_paths.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `HISTORY_DIR: Path` — module-level constant, monkeypatched in tests.
  - `ASOF_FORMAT: str` = `"%Y%m%dT%H%M%SZ"`
  - `MODELS: tuple[str, ...]` = `("m0_rules", "m2_xg", "m3_understat")`
  - `format_asof(dt: datetime) -> str`
  - `parse_asof(asof: str) -> datetime` (raises `ValueError` on bad input)
  - `projections_partition(gw: int, asof: str, model: str) -> Path`
  - `decisions_partition(gw: int, asof: str) -> Path`
  - `health_partition(gw: int, asof: str, model: str) -> Path`
  - `actuals_partition(gw: int) -> Path`
  - `run_partition(asof: str) -> Path`
  - `run_json_path(asof: str) -> Path`
  - `id_code_map_path(asof: str) -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_paths.py`:

```python
"""
Tests for fpl/history/paths.py — the single source of truth for the
Phase G archive layout (spec §3.1/§3.2). Four call sites build paths
(archive, query, manifest, migration); if any of them constructs paths
by hand instead of calling these helpers, the layout silently drifts.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fpl.history import paths


def test_format_asof_uses_iso_basic_not_extended():
    """Colons are illegal in Windows path names, so the spec's asof key
    is ISO 8601 BASIC. This is the single most load-bearing formatting
    decision in the archive — partitions are immutable once written."""
    dt = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    assert paths.format_asof(dt) == "20260822T125314Z"
    assert ":" not in paths.format_asof(dt)


def test_parse_asof_round_trips():
    dt = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    assert paths.parse_asof(paths.format_asof(dt)) == dt


def test_parse_asof_rejects_iso_extended():
    with pytest.raises(ValueError):
        paths.parse_asof("2026-08-22T12:53:14Z")


def test_projections_partition_is_hive_partitioned(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    p = paths.projections_partition(1, "20260822T125314Z", "m0_rules")
    assert p == tmp_path / "projections" / "gw=1" / "asof=20260822T125314Z" / "model=m0_rules" / "players.parquet"


def test_decisions_and_health_and_run_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    asof = "20260822T125314Z"
    assert paths.decisions_partition(1, asof) == tmp_path / "decisions" / "gw=1" / f"asof={asof}" / "recommendation.json"
    assert paths.health_partition(1, asof, "m2_xg") == tmp_path / "health" / "gw=1" / f"asof={asof}" / "model=m2_xg" / "model_health.json"
    assert paths.actuals_partition(3) == tmp_path / "actuals" / "gw=3" / "players.parquet"
    assert paths.run_json_path(asof) == tmp_path / "_runs" / f"asof={asof}" / "run.json"
    assert paths.id_code_map_path(asof) == tmp_path / "_runs" / f"asof={asof}" / "id_code_map.parquet"


def test_runs_dir_is_underscore_prefixed_so_hive_globs_skip_it(tmp_path, monkeypatch):
    """_runs must not be picked up by projections/**/*.parquet globs."""
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path)
    assert paths.run_partition("20260822T125314Z").parent.name == "_runs"


def test_models_constant_matches_the_registry():
    assert paths.MODELS == ("m0_rules", "m2_xg", "m3_understat")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fpl.history'`

- [ ] **Step 3: Write minimal implementation**

Create `fpl/history/__init__.py`:

```python
"""
fpl/history/ — Phase G bitemporal archive (docs/superpowers/specs/
2026-08-22-history-layer-design.md).

Strict write/read split: archive.py writes, query.py only reads. Nothing
in this package imports from fpl/project/ or fpl/decide/ — it archives
their already-written outputs and never recomputes them.
"""
```

Create `fpl/history/paths.py`:

```python
"""
paths.py — the ONE source of truth for the archive's partition layout
(spec §3.1/§3.2).

Four call sites build these paths: archive.py (write), query.py (read),
manifest.py (index), migrate_crude_archive.py (migration). If any of
them constructs paths by hand the layout drifts silently — this repo has
already been bitten by a shared helper that call sites didn't actually
call (fpl/status.py, HANDOFF.md §5 finding #10).

WHY ISO 8601 BASIC for `asof`: ISO extended (2026-08-22T12:53:14Z)
contains colons, which Windows forbids in path names — verified
empirically, the plan's literal `asof={utc_iso}` is unimplementable
here. Basic format is also lexicographically sortable, so `ORDER BY
asof` is chronological with no parsing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path(__file__).resolve().parents[2] / "data" / "history"

ASOF_FORMAT = "%Y%m%dT%H%M%SZ"

# Matches the model registry in CLAUDE.md. m0_rules is the champion and
# the pipeline's default (written to data/projections/gw{n}.parquet with
# no model subdirectory); the other two live in named subdirectories.
MODELS = ("m0_rules", "m2_xg", "m3_understat")


def format_asof(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ASOF_FORMAT)


def parse_asof(asof: str) -> datetime:
    """Raises ValueError on anything that isn't ISO basic — including
    ISO extended, which would mean a colon reached a path."""
    return datetime.strptime(asof, ASOF_FORMAT).replace(tzinfo=timezone.utc)


def projections_partition(gw: int, asof: str, model: str) -> Path:
    return HISTORY_DIR / "projections" / f"gw={gw}" / f"asof={asof}" / f"model={model}" / "players.parquet"


def decisions_partition(gw: int, asof: str) -> Path:
    return HISTORY_DIR / "decisions" / f"gw={gw}" / f"asof={asof}" / "recommendation.json"


def health_partition(gw: int, asof: str, model: str) -> Path:
    return HISTORY_DIR / "health" / f"gw={gw}" / f"asof={asof}" / f"model={model}" / "model_health.json"


def actuals_partition(gw: int) -> Path:
    return HISTORY_DIR / "actuals" / f"gw={gw}" / "players.parquet"


def run_partition(asof: str) -> Path:
    return HISTORY_DIR / "_runs" / f"asof={asof}"


def run_json_path(asof: str) -> Path:
    return run_partition(asof) / "run.json"


def id_code_map_path(asof: str) -> Path:
    return run_partition(asof) / "id_code_map.parquet"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_paths.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add fpl/history/__init__.py fpl/history/paths.py tests/test_history_paths.py
git commit -m "feat(history): partition path layout, ISO-basic asof key"
```

---

### Task 2: Run provenance capture

**Files:**
- Create: `fpl/history/provenance.py`
- Test: `tests/test_history_provenance.py`

**Interfaces:**
- Consumes: `fpl.history.paths` (for `format_asof`).
- Produces:
  - `CONFIG_PATH: Path` — module constant, monkeypatched in tests.
  - `build_run_metadata(target_gameweek: int, deadline_utc: str | None, now: datetime | None = None) -> dict` — returns the `run.json` payload minus `archived` (which `archive.py` fills in).
  - `config_sha256() -> str | None`
  - `git_state() -> tuple[str | None, bool | None]` → `(sha, dirty)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_provenance.py`:

```python
"""
Tests for fpl/history/provenance.py — spec §3.6.

hours_to_deadline is the load-bearing field: a projection 4 days out and
one 2 hours out are different events, and it is computable ONLY at
capture time (same reasoning as fpl/collect/snapshot.py's own column).
git_dirty matters because a local run with uncommitted changes is NOT
reproducible from its SHA alone.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone

from fpl.history import provenance


def test_hours_to_deadline_is_negative_after_the_deadline_passed():
    now = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(
        target_gameweek=1, deadline_utc="2026-08-21T17:30:00Z", now=now
    )
    assert meta["hours_to_deadline"] < 0
    assert round(meta["hours_to_deadline"], 1) == -18.5


def test_hours_to_deadline_is_positive_before_the_deadline():
    now = datetime(2026, 8, 20, 17, 30, 0, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(
        target_gameweek=2, deadline_utc="2026-08-21T17:30:00Z", now=now
    )
    assert round(meta["hours_to_deadline"], 1) == 24.0


def test_hours_to_deadline_is_none_when_deadline_unknown():
    """Never guess. A missing deadline is null, not 0."""
    meta = provenance.build_run_metadata(
        target_gameweek=1, deadline_utc=None,
        now=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )
    assert meta["hours_to_deadline"] is None


def test_asof_is_iso_basic_and_asof_iso_is_extended():
    now = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
    meta = provenance.build_run_metadata(1, "2026-08-21T17:30:00Z", now=now)
    assert meta["asof"] == "20260822T125314Z"
    assert meta["asof_iso"] == "2026-08-22T12:53:14+00:00"


def test_trigger_reads_github_event_name_else_local(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["trigger"] == "local"

    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["trigger"] == "workflow_dispatch"


def test_provenance_defaults_to_recorded():
    meta = provenance.build_run_metadata(1, None, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert meta["provenance"] == "recorded"


def test_config_sha256_is_stable_and_content_dependent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(provenance, "CONFIG_PATH", cfg)
    first = provenance.config_sha256()
    assert provenance.config_sha256() == first

    cfg.write_text("a: 2\n", encoding="utf-8")
    assert provenance.config_sha256() != first


def test_config_sha256_is_none_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "CONFIG_PATH", tmp_path / "nope.yaml")
    assert provenance.config_sha256() is None


def test_run_id_is_unique_per_call():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    a = provenance.build_run_metadata(1, None, now=now)["run_id"]
    b = provenance.build_run_metadata(1, None, now=now)["run_id"]
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fpl.history.provenance'`

- [ ] **Step 3: Write minimal implementation**

Create `fpl/history/provenance.py`:

```python
"""
provenance.py — run metadata for the archive (spec §3.6).

Without config_sha256 you cannot tell a real projection revision from a
config.yaml parameter you changed on a Wednesday — which is the whole
question the archive exists to answer.

Nothing here guesses. A field that isn't genuinely knowable is None.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fpl.history import paths

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


def config_sha256() -> Optional[str]:
    try:
        return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def git_state() -> tuple[Optional[str], Optional[bool]]:
    """(sha, dirty). Both None if git isn't available or this isn't a repo —
    an honest unknown beats a fabricated SHA."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        return sha, bool(status.strip())
    except (subprocess.SubprocessError, OSError):
        return None, None


def _hours_to_deadline(now: datetime, deadline_utc: Optional[str]) -> Optional[float]:
    if not deadline_utc:
        return None
    try:
        deadline = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (deadline - now).total_seconds() / 3600.0


def build_run_metadata(
    target_gameweek: int,
    deadline_utc: Optional[str],
    now: Optional[datetime] = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sha, dirty = git_state()
    return {
        "run_id": str(uuid.uuid4()),
        "asof": paths.format_asof(now),
        "asof_iso": now.isoformat(),
        "git_sha": sha,
        "git_dirty": dirty,
        "config_sha256": config_sha256(),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "target_gameweek": target_gameweek,
        "deadline_utc": deadline_utc,
        "hours_to_deadline": _hours_to_deadline(now, deadline_utc),
        "provenance": "recorded",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_provenance.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add fpl/history/provenance.py tests/test_history_provenance.py
git commit -m "feat(history): run provenance capture with honest nulls"
```

---

### Task 3: Archive write path

**Files:**
- Create: `fpl/history/archive.py`
- Test: `tests/test_history_archive.py`

**Interfaces:**
- Consumes: `paths.*`, `provenance.build_run_metadata`.
- Produces:
  - `PROJECTIONS_DIR`, `OUTPUT_DIR`, `PROCESSED_DIR`, `RAW_DIR: Path` — module constants, monkeypatched in tests.
  - `PartitionExistsError(Exception)`
  - `archive_run(now: datetime | None = None) -> dict` — returns the written `run.json` payload. Raises `PartitionExistsError` if any target partition already exists.
  - `discover_artefacts() -> dict` — `{"projections": [(gw, model, Path)], "decisions": [(gw, Path)], "health": [(gw, model, Path)]}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_archive.py`:

```python
"""
Tests for fpl/history/archive.py — spec §3.5.

The two behaviours that matter most:
  1. Partitions are immutable — writing into an existing one RAISES.
  2. run.json is written LAST, as a completion marker. A run that dies
     mid-write leaves partitions with no run.json, which the query layer
     treats as incomplete rather than silently analysing as whole.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from fpl.history import archive, paths

NOW = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
ASOF = "20260822T125314Z"


def _seed(tmp_path, monkeypatch, *, with_challengers=True):
    """Build a fake pipeline output tree and point every module constant at it."""
    hist = tmp_path / "history"
    proj = tmp_path / "projections"
    out = tmp_path / "output"
    processed = tmp_path / "processed"
    raw = tmp_path / "raw"
    for d in (proj, out, processed, raw):
        d.mkdir(parents=True)

    pd.DataFrame({"id": [1, 2], "event": [1, 1], "xpts": [5.0, 6.0]}).to_parquet(
        proj / "gw1.parquet", index=False
    )
    if with_challengers:
        (proj / "m2_xg").mkdir()
        pd.DataFrame({"id": [1, 2], "event": [1, 1], "xpts": [5.5, 6.5]}).to_parquet(
            proj / "m2_xg" / "gw1.parquet", index=False
        )

    (out / "gw1_recommendations.json").write_text(
        json.dumps({"gameweek": 1, "next_gw_expected_points": 66.2}), encoding="utf-8"
    )
    (out / "model_health.json").write_text(json.dumps({"overall_rmse": 20.9}), encoding="utf-8")
    if with_challengers:
        (out / "model_health_m2_xg.json").write_text(
            json.dumps({"overall_rmse": 20.6}), encoding="utf-8"
        )

    pd.DataFrame({"id": [1, 2], "code": [154561, 109745], "web_name": ["Raya", "Ari"]}).to_parquet(
        processed / "players.parquet", index=False
    )
    (raw / "bootstrap_static.json").write_text(
        json.dumps({"events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z",
                                "finished": False, "is_next": True}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    monkeypatch.setattr(archive, "PROJECTIONS_DIR", proj)
    monkeypatch.setattr(archive, "OUTPUT_DIR", out)
    monkeypatch.setattr(archive, "PROCESSED_DIR", processed)
    monkeypatch.setattr(archive, "RAW_DIR", raw)
    return hist


def test_archive_writes_expected_partitions(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg" / "players.parquet").exists()
    assert (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").exists()
    assert (hist / "health" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "model_health.json").exists()
    assert (hist / "_runs" / f"asof={ASOF}" / "run.json").exists()


def test_archived_content_is_byte_identical(tmp_path, monkeypatch):
    """Spec §3.3: names are normalized, CONTENT is copied byte-for-byte."""
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    src = (tmp_path / "output" / "gw1_recommendations.json").read_bytes()
    dst = (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").read_bytes()
    assert src == dst


def test_id_code_map_sidecar_written(tmp_path, monkeypatch):
    """Spec §3.4 — projections carry id but not code, and code is the only
    season-stable key. The sidecar is what keeps the archive joinable
    across a season boundary."""
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    m = pd.read_parquet(hist / "_runs" / f"asof={ASOF}" / "id_code_map.parquet")
    assert set(m.columns) >= {"id", "code"}
    assert m.loc[m["id"] == 1, "code"].iloc[0] == 154561


def test_refuses_to_overwrite_an_existing_partition(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    with pytest.raises(archive.PartitionExistsError):
        archive.archive_run(now=NOW)


def test_run_json_written_last_so_partial_runs_are_detectable(tmp_path, monkeypatch):
    """Simulate a crash after partitions are copied but before the marker."""
    hist = _seed(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("simulated crash before completion marker")

    monkeypatch.setattr(archive, "_write_run_json", boom)
    with pytest.raises(RuntimeError):
        archive.archive_run(now=NOW)

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert not (hist / "_runs" / f"asof={ASOF}" / "run.json").exists()


def test_run_json_records_provenance_and_archived_list(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch)
    meta = archive.archive_run(now=NOW)
    on_disk = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))
    assert on_disk == meta
    assert on_disk["provenance"] == "recorded"
    assert on_disk["target_gameweek"] == 1
    assert on_disk["hours_to_deadline"] < 0  # GW1 deadline already passed
    assert len(on_disk["archived"]) >= 4


def test_missing_challenger_models_are_skipped_not_faked(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch, with_challengers=False)
    archive.archive_run(now=NOW)
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules").exists()
    assert not (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_archive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fpl.history.archive'`

- [ ] **Step 3: Write minimal implementation**

Create `fpl/history/archive.py`:

```python
"""
archive.py — the archive WRITE path (spec §3.5).

Copies the pipeline's already-written artefacts into immutable,
hive-partitioned directories. Never recomputes anything, never imports
from fpl/project/ or fpl/decide/.

TWO RULES THIS MODULE ENFORCES:
  1. A partition, once written, is never modified or deleted. Writing
     into an existing one raises PartitionExistsError.
  2. run.json is written LAST. Its presence is the completion marker —
     partitions without it are an incomplete run, and query.py excludes
     them. This gives atomic-ish semantics with no transactions.

Why not idempotency-on-retry instead: a retried run genuinely observed
the data at a DIFFERENT time. Recording it as a duplicate of the first
attempt would fabricate a revision that never happened.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from fpl.history import paths, provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTIONS_DIR = REPO_ROOT / "data" / "projections"
OUTPUT_DIR = REPO_ROOT / "data" / "output"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
RAW_DIR = REPO_ROOT / "data" / "raw"

# m0_rules lives at the root of data/projections/ (it is project.py's
# default path); challengers live in named subdirectories.
_CHALLENGER_SUBDIRS = {"m2_xg": "m2_xg", "m3_understat": "m3_understat"}
_HEALTH_FILES = {
    "m0_rules": "model_health.json",
    "m2_xg": "model_health_m2_xg.json",
    "m3_understat": "model_health_m3_understat.json",
}


class PartitionExistsError(Exception):
    """Raised rather than overwriting an immutable partition."""


def _gw_from_projection_filename(p: Path) -> Optional[int]:
    stem = p.stem  # "gw1"
    if not stem.startswith("gw"):
        return None
    try:
        return int(stem[2:])
    except ValueError:
        return None


def discover_artefacts() -> dict:
    """What the pipeline has written that is worth archiving."""
    projections, decisions, health = [], [], []

    for p in sorted(PROJECTIONS_DIR.glob("gw*.parquet")):
        gw = _gw_from_projection_filename(p)
        if gw is not None:
            projections.append((gw, "m0_rules", p))
    for model, sub in _CHALLENGER_SUBDIRS.items():
        for p in sorted((PROJECTIONS_DIR / sub).glob("gw*.parquet")):
            gw = _gw_from_projection_filename(p)
            if gw is not None:
                projections.append((gw, model, p))

    for p in sorted(OUTPUT_DIR.glob("gw*_recommendations.json")):
        try:
            gw = int(json.loads(p.read_text(encoding="utf-8"))["gameweek"])
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        decisions.append((gw, p))

    target = _target_gameweek()
    if target is not None:
        for model, fname in _HEALTH_FILES.items():
            p = OUTPUT_DIR / fname
            if p.exists():
                health.append((target, model, p))

    return {"projections": projections, "decisions": decisions, "health": health}


def _next_event() -> Optional[dict]:
    bs = RAW_DIR / "bootstrap_static.json"
    if not bs.exists():
        return None
    try:
        events = json.loads(bs.read_text(encoding="utf-8")).get("events", [])
    except json.JSONDecodeError:
        return None
    for e in events:
        if e.get("is_next"):
            return e
    for e in events:
        if not e.get("finished"):
            return e
    return None


def _target_gameweek() -> Optional[int]:
    e = _next_event()
    return int(e["id"]) if e else None


def _deadline_utc() -> Optional[str]:
    e = _next_event()
    return e.get("deadline_time") if e else None


def _copy(src: Path, dst: Path) -> None:
    if dst.exists():
        raise PartitionExistsError(
            f"{dst} already exists — partitions are immutable and are never "
            f"overwritten (spec §3.5). A genuinely new observation belongs "
            f"under a new asof."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _write_id_code_map(asof: str) -> None:
    players = PROCESSED_DIR / "players.parquet"
    if not players.exists():
        return
    df = pd.read_parquet(players)
    cols = [c for c in ("id", "code", "web_name") if c in df.columns]
    if "code" not in cols:
        return
    dst = paths.id_code_map_path(asof)
    dst.parent.mkdir(parents=True, exist_ok=True)
    df[cols].to_parquet(dst, index=False)


def _write_run_json(asof: str, meta: dict) -> None:
    """Written LAST — this is the completion marker (spec §3.5)."""
    dst = paths.run_json_path(asof)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def archive_run(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    target = _target_gameweek()
    meta = provenance.build_run_metadata(
        target_gameweek=target, deadline_utc=_deadline_utc(), now=now
    )
    asof = meta["asof"]

    found = discover_artefacts()
    archived = []

    for gw, model, src in found["projections"]:
        dst = paths.projections_partition(gw, asof, model)
        _copy(src, dst)
        archived.append({"domain": "projections", "gw": gw, "model": model, "path": str(dst.relative_to(paths.HISTORY_DIR))})

    for gw, src in found["decisions"]:
        dst = paths.decisions_partition(gw, asof)
        _copy(src, dst)
        archived.append({"domain": "decisions", "gw": gw, "model": None, "path": str(dst.relative_to(paths.HISTORY_DIR))})

    for gw, model, src in found["health"]:
        dst = paths.health_partition(gw, asof, model)
        _copy(src, dst)
        archived.append({"domain": "health", "gw": gw, "model": model, "path": str(dst.relative_to(paths.HISTORY_DIR))})

    _write_id_code_map(asof)

    meta["archived"] = archived
    _write_run_json(asof, meta)
    return meta


if __name__ == "__main__":
    m = archive_run()
    print(f"Archived run {m['asof']} — {len(m['archived'])} partition(s), "
          f"target GW{m['target_gameweek']}, trigger={m['trigger']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_archive.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add fpl/history/archive.py tests/test_history_archive.py
git commit -m "feat(history): append-only archive write path with completion marker"
```

---

### Task 4: Migrate the crude Tier 0.2 archive

**Files:**
- Create: `scripts/migrate_crude_archive.py`
- Test: `tests/test_migrate_crude_archive.py`

**Interfaces:**
- Consumes: `paths.*`.
- Produces: `find_crude_dirs() -> list[Path]`, `migrate_one(crude_dir: Path, *, deadline_utc: str | None, trigger: str | None) -> dict`, `main() -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate_crude_archive.py`:

```python
"""
Tests for scripts/migrate_crude_archive.py — spec §6.

The Tier 0.2 crude step wrote data/history/{TIMESTAMP}/{output,projections}/…
with no gw partitioning and no provenance. This migrates it into the real
layout, reconstructing ONLY what is genuinely derivable and writing null
for everything else — a guessed config hash would be worse than no hash.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpl.history import paths
import scripts.migrate_crude_archive as mig

ASOF = "20260822T125314Z"


def _seed_crude(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    crude = hist / ASOF
    (crude / "projections" / "m2_xg").mkdir(parents=True)
    (crude / "output").mkdir(parents=True)

    pd.DataFrame({"id": [1], "event": [1], "xpts": [5.0]}).to_parquet(
        crude / "projections" / "gw1.parquet", index=False
    )
    pd.DataFrame({"id": [1], "event": [1], "xpts": [5.5]}).to_parquet(
        crude / "projections" / "m2_xg" / "gw1.parquet", index=False
    )
    (crude / "output" / "gw1_recommendations.json").write_text(
        json.dumps({"gameweek": 1}), encoding="utf-8"
    )
    (crude / "output" / "model_health.json").write_text(
        json.dumps({"overall_rmse": 20.9}), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    monkeypatch.setattr(mig, "HISTORY_DIR", hist)
    return hist, crude


def test_finds_crude_timestamp_dirs_only(tmp_path, monkeypatch):
    hist, _ = _seed_crude(tmp_path, monkeypatch)
    (hist / "projections").mkdir(exist_ok=True)
    (hist / "_runs").mkdir(exist_ok=True)
    assert [p.name for p in mig.find_crude_dirs()] == [ASOF]


def test_migrates_into_hive_layout(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc="2026-08-21T17:30:00Z", trigger="workflow_dispatch")

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg" / "players.parquet").exists()
    assert (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").exists()
    assert (hist / "health" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "model_health.json").exists()


def test_marks_provenance_reconstructed_and_nulls_the_unknowable(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc="2026-08-21T17:30:00Z", trigger="workflow_dispatch")
    meta = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))

    assert meta["provenance"] == "reconstructed"
    assert meta["trigger"] == "workflow_dispatch"
    assert meta["asof"] == ASOF
    assert meta["target_gameweek"] == 1
    assert round(meta["hours_to_deadline"], 1) == -19.4
    # Not recoverable after the fact — must be null, never guessed.
    assert meta["config_sha256"] is None
    assert meta["git_dirty"] is None


def test_unknown_trigger_is_null_not_guessed(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc=None, trigger=None)
    meta = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))
    assert meta["trigger"] is None
    assert meta["hours_to_deadline"] is None


def test_refuses_to_migrate_twice(tmp_path, monkeypatch):
    _, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc=None, trigger=None)
    with pytest.raises(mig.PartitionExistsError):
        mig.migrate_one(crude, deadline_utc=None, trigger=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_migrate_crude_archive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_crude_archive'`

- [ ] **Step 3: Write minimal implementation**

First ensure `scripts/` is importable — create `scripts/__init__.py` if absent (empty file).

Create `scripts/migrate_crude_archive.py`:

```python
"""
migrate_crude_archive.py — one-off, spec §6.

Re-partitions the Tier 0.2 crude archive (data/history/{TIMESTAMP}/) into
the real hive layout. Reconstructs provenance ONLY where genuinely
derivable; everything else is null. A guessed config hash would be worse
than an absent one — it would make an unreproducible run look reproducible.

Usage:
  .venv\\Scripts\\python.exe scripts/migrate_crude_archive.py [--delete-crude]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fpl.history import paths  # noqa: E402
from fpl.history.archive import PartitionExistsError  # noqa: E402

HISTORY_DIR = REPO_ROOT / "data" / "history"

CRUDE_DIR_RE = re.compile(r"^\d{8}T\d{6}Z$")
_CHALLENGERS = {"m2_xg": "m2_xg", "m3_understat": "m3_understat"}
_HEALTH_FILES = {
    "m0_rules": "model_health.json",
    "m2_xg": "model_health_m2_xg.json",
    "m3_understat": "model_health_m3_understat.json",
}


def find_crude_dirs() -> list[Path]:
    if not HISTORY_DIR.exists():
        return []
    return sorted(
        p for p in HISTORY_DIR.iterdir()
        if p.is_dir() and CRUDE_DIR_RE.match(p.name)
    )


def _copy(src: Path, dst: Path) -> None:
    if dst.exists():
        raise PartitionExistsError(f"{dst} already exists — refusing to overwrite.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _gw_from_name(p: Path) -> Optional[int]:
    if not p.stem.startswith("gw"):
        return None
    try:
        return int(p.stem[2:])
    except ValueError:
        return None


def migrate_one(crude_dir: Path, *, deadline_utc: Optional[str],
                trigger: Optional[str]) -> dict:
    asof = crude_dir.name
    asof_dt = paths.parse_asof(asof)
    archived = []

    proj = crude_dir / "projections"
    if proj.exists():
        for p in sorted(proj.glob("gw*.parquet")):
            gw = _gw_from_name(p)
            if gw is None:
                continue
            dst = paths.projections_partition(gw, asof, "m0_rules")
            _copy(p, dst)
            archived.append({"domain": "projections", "gw": gw, "model": "m0_rules",
                             "path": str(dst.relative_to(paths.HISTORY_DIR))})
        for model, sub in _CHALLENGERS.items():
            for p in sorted((proj / sub).glob("gw*.parquet")):
                gw = _gw_from_name(p)
                if gw is None:
                    continue
                dst = paths.projections_partition(gw, asof, model)
                _copy(p, dst)
                archived.append({"domain": "projections", "gw": gw, "model": model,
                                 "path": str(dst.relative_to(paths.HISTORY_DIR))})

    out = crude_dir / "output"
    target_gw = None
    if out.exists():
        for p in sorted(out.glob("gw*_recommendations.json")):
            try:
                gw = int(json.loads(p.read_text(encoding="utf-8"))["gameweek"])
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
            target_gw = gw
            dst = paths.decisions_partition(gw, asof)
            _copy(p, dst)
            archived.append({"domain": "decisions", "gw": gw, "model": None,
                             "path": str(dst.relative_to(paths.HISTORY_DIR))})
        if target_gw is not None:
            for model, fname in _HEALTH_FILES.items():
                p = out / fname
                if p.exists():
                    dst = paths.health_partition(target_gw, asof, model)
                    _copy(p, dst)
                    archived.append({"domain": "health", "gw": target_gw, "model": model,
                                     "path": str(dst.relative_to(paths.HISTORY_DIR))})

    hours = None
    if deadline_utc:
        deadline = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
        hours = (deadline - asof_dt.astimezone(timezone.utc)).total_seconds() / 3600.0

    meta = {
        "run_id": None,
        "asof": asof,
        "asof_iso": asof_dt.isoformat(),
        "git_sha": None,
        "git_dirty": None,
        "config_sha256": None,
        "trigger": trigger,
        "target_gameweek": target_gw,
        "deadline_utc": deadline_utc,
        "hours_to_deadline": hours,
        "provenance": "reconstructed",
        "archived": archived,
    }
    dst = paths.run_json_path(asof)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline-utc", default="2026-08-21T17:30:00Z",
                    help="GW1 deadline; used to reconstruct hours_to_deadline.")
    ap.add_argument("--trigger", default="workflow_dispatch",
                    help="Known trigger for the crude run(s); pass empty for unknown.")
    ap.add_argument("--delete-crude", action="store_true",
                    help="Remove the crude directory after a verified migration.")
    args = ap.parse_args()

    crude = find_crude_dirs()
    if not crude:
        print("No crude archive directories found — nothing to migrate.")
        return 0

    for d in crude:
        meta = migrate_one(d, deadline_utc=args.deadline_utc or None,
                           trigger=args.trigger or None)
        print(f"Migrated {d.name}: {len(meta['archived'])} partition(s), "
              f"target GW{meta['target_gameweek']}, provenance={meta['provenance']}")
        if args.delete_crude:
            shutil.rmtree(d)
            print(f"  removed crude dir {d} (recoverable from git history)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_migrate_crude_archive.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/migrate_crude_archive.py tests/test_migrate_crude_archive.py
git commit -m "feat(history): migrate crude Tier 0.2 archive into hive layout"
```

---

### Task 5: DuckDB read model and manifest

**Files:**
- Create: `fpl/history/query.py`
- Create: `fpl/history/manifest.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Test: `tests/test_history_query.py`

**Interfaces:**
- Consumes: `paths.*`.
- Produces:
  - `query.open_archive(root: Path | None = None) -> Archive`
  - `Archive.runs() -> pd.DataFrame`
  - `Archive.coverage() -> pd.DataFrame` — columns `gw`, `n_runs`, `n_complete`, `n_incomplete`, `first_asof`, `last_asof`, `n_reconstructed`
  - `Archive.projections(gw=None, model=None, asof=None, event=None) -> pd.DataFrame`
  - `Archive.revisions(event: int, model: str = "m0_rules", player_code: int | None = None) -> pd.DataFrame`
  - `Archive.sql(q: str) -> pd.DataFrame`
  - `manifest.build_manifest() -> dict`, `manifest.write_manifest() -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history_query.py`:

```python
"""
Tests for fpl/history/query.py — spec §5.

The subtlety that gets people (spec §3.3a): `gw` is the gameweek a run
was TARGETING; `event` is the gameweek a row's xPts is FOR. A revision
series fixes `event` and therefore spans MULTIPLE `gw` partitions. Get
this backwards and the whole archive answers the wrong question.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fpl.history import paths, query

duckdb = pytest.importorskip("duckdb")


def _write_run(hist, asof, gw, rows, *, complete=True, provenance="recorded"):
    """rows: list of (id, event, xpts)"""
    df = pd.DataFrame(rows, columns=["id", "event", "xpts"])
    p = paths.projections_partition(gw, asof, "m0_rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)

    m = paths.id_code_map_path(asof)
    m.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"id": [1, 2], "code": [111, 222], "web_name": ["A", "B"]}).to_parquet(m, index=False)

    if complete:
        paths.run_json_path(asof).write_text(json.dumps({
            "run_id": "r", "asof": asof, "asof_iso": None, "git_sha": None,
            "git_dirty": None, "config_sha256": None, "trigger": "schedule",
            "target_gameweek": gw, "deadline_utc": None, "hours_to_deadline": -1.0,
            "provenance": provenance, "archived": [],
        }), encoding="utf-8")


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    # Two runs targeting GW1, both projecting events 1 and 2.
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0), (1, 2, 4.0)])
    _write_run(hist, "20260821T090000Z", 1, [(1, 1, 7.0), (1, 2, 4.5)])
    # A later run targeting GW2, still projecting event 2.
    _write_run(hist, "20260825T090000Z", 2, [(1, 2, 6.0)])
    return hist


def test_runs_lists_complete_runs(archive_root):
    a = query.open_archive(archive_root)
    assert len(a.runs()) == 3


def test_incomplete_runs_are_excluded(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0)], complete=True)
    _write_run(hist, "20260819T090000Z", 1, [(1, 1, 9.9)], complete=False)

    a = query.open_archive(hist)
    assert len(a.runs()) == 1
    assert "20260819T090000Z" not in set(a.projections()["asof"])


def test_projections_carry_code_not_just_id(archive_root):
    """Spec §3.4 — id is not season-stable, code is."""
    a = query.open_archive(archive_root)
    df = a.projections(gw=1)
    assert "code" in df.columns
    assert set(df["code"]) == {111}


def test_revisions_fix_event_and_span_multiple_gw_partitions(archive_root):
    """THE load-bearing semantic (spec §3.3a). Event 2 was projected by
    all three runs — two under gw=1, one under gw=2. A revision series
    must include all three, ordered by asof."""
    a = query.open_archive(archive_root)
    rev = a.revisions(event=2, player_code=111)

    assert list(rev["asof"]) == ["20260818T090000Z", "20260821T090000Z", "20260825T090000Z"]
    assert list(rev["xpts"]) == [4.0, 4.5, 6.0]
    assert set(rev["gw"]) == {1, 2}


def test_revisions_for_event_1_only_span_gw1(archive_root):
    a = query.open_archive(archive_root)
    rev = a.revisions(event=1, player_code=111)
    assert list(rev["xpts"]) == [5.0, 7.0]


def test_coverage_reports_runs_per_gameweek(archive_root):
    a = query.open_archive(archive_root)
    cov = a.coverage().set_index("gw")
    assert int(cov.loc[1, "n_complete"]) == 2
    assert int(cov.loc[2, "n_complete"]) == 1


def test_coverage_flags_incomplete_runs(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    _write_run(hist, "20260818T090000Z", 1, [(1, 1, 5.0)], complete=True)
    _write_run(hist, "20260819T090000Z", 1, [(1, 1, 9.9)], complete=False)

    cov = query.open_archive(hist).coverage().set_index("gw")
    assert int(cov.loc[1, "n_complete"]) == 1
    assert int(cov.loc[1, "n_incomplete"]) == 1


def test_empty_archive_returns_empty_frames_not_errors(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.mkdir()
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    a = query.open_archive(hist)
    assert a.runs().empty
    assert a.coverage().empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_query.py -v`
Expected: FAIL — `duckdb` not installed, so `importorskip` skips; install it first (next step) then it fails with `ModuleNotFoundError: No module named 'fpl.history.query'`

- [ ] **Step 3: Write minimal implementation**

Install duckdb:

```bash
.venv\Scripts\python.exe -m pip install duckdb==1.1.3
```

Add to `requirements.txt` (after `pyarrow`):

```
duckdb==1.1.3     # Phase G history read model only — NOT needed by the pipeline path
```

Add to `.gitignore`:

```
# Phase G: DuckDB read model + derived manifest are rebuildable from the
# immutable Parquet/JSON partitions in data/history/ — keeping the
# immutable thing immutable and the queryable thing disposable.
data/history/manifest.json
*.duckdb
```

Create `fpl/history/query.py`:

```python
"""
query.py — the archive READ model (spec §5).

READ-ONLY. This module never writes to data/history/. Reading history
must never be able to corrupt it — the same discipline as the
FROZEN/LIVE boundary.

No ingest step: DuckDB reads the partitioned Parquet/JSON directly, so
there is nothing to migrate and no second copy to fall out of sync.

Every public view is filtered to COMPLETE runs (a run.json exists —
spec §3.5) and joined to `code` (spec §3.4), so no caller can
accidentally analyse a half-written run or key on a season-unstable id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from fpl.history import paths

_EMPTY_RUNS = pd.DataFrame(columns=[
    "asof", "target_gameweek", "trigger", "provenance", "hours_to_deadline",
    "git_sha", "config_sha256",
])
_EMPTY_COVERAGE = pd.DataFrame(columns=[
    "gw", "n_runs", "n_complete", "n_incomplete", "first_asof", "last_asof",
    "n_reconstructed",
])


def _glob(root: Path, *parts: str) -> str:
    return str(root.joinpath(*parts)).replace("\\", "/")


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _connect(self):
        import duckdb  # lazy — the pipeline write path must not need it
        return duckdb.connect(database=":memory:")

    def _complete_asofs(self) -> list[str]:
        runs_dir = self.root / "_runs"
        if not runs_dir.exists():
            return []
        return sorted(
            d.name.split("=", 1)[1]
            for d in runs_dir.iterdir()
            if d.is_dir() and d.name.startswith("asof=") and (d / "run.json").exists()
        )

    def _all_asofs(self) -> list[str]:
        runs_dir = self.root / "_runs"
        if not runs_dir.exists():
            return []
        return sorted(
            d.name.split("=", 1)[1]
            for d in runs_dir.iterdir()
            if d.is_dir() and d.name.startswith("asof=")
        )

    def runs(self) -> pd.DataFrame:
        rows = []
        for asof in self._complete_asofs():
            import json
            p = self.root / "_runs" / f"asof={asof}" / "run.json"
            try:
                rows.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if not rows:
            return _EMPTY_RUNS.copy()
        df = pd.DataFrame(rows)
        if "archived" in df.columns:
            df = df.drop(columns=["archived"])
        return df.sort_values("asof").reset_index(drop=True)

    def _id_code_map(self) -> pd.DataFrame:
        frames = []
        for asof in self._complete_asofs():
            p = paths.id_code_map_path(asof)
            if p.exists():
                m = pd.read_parquet(p)
                m["asof"] = asof
                frames.append(m)
        if not frames:
            return pd.DataFrame(columns=["id", "code", "web_name", "asof"])
        return pd.concat(frames, ignore_index=True)

    def projections(self, gw: Optional[int] = None, model: Optional[str] = None,
                    asof: Optional[str] = None, event: Optional[int] = None) -> pd.DataFrame:
        complete = self._complete_asofs()
        if not complete:
            return pd.DataFrame(columns=["gw", "asof", "model", "id", "code", "event", "xpts"])

        con = self._connect()
        try:
            pattern = _glob(self.root, "projections", "**", "*.parquet")
            df = con.execute(
                f"SELECT * FROM read_parquet('{pattern}', hive_partitioning=true)"
            ).fetch_df()
        except Exception:
            return pd.DataFrame(columns=["gw", "asof", "model", "id", "code", "event", "xpts"])
        finally:
            con.close()

        if df.empty:
            return df
        df["gw"] = df["gw"].astype(int)
        df = df[df["asof"].isin(complete)]

        cmap = self._id_code_map()
        if not cmap.empty:
            cols = ["id", "code", "asof"] + (["web_name"] if "web_name" in cmap.columns else [])
            df = df.merge(cmap[cols], on=["id", "asof"], how="left", suffixes=("", "_map"))

        if gw is not None:
            df = df[df["gw"] == gw]
        if model is not None:
            df = df[df["model"] == model]
        if asof is not None:
            df = df[df["asof"] == asof]
        if event is not None:
            df = df[df["event"] == event]
        return df.reset_index(drop=True)

    def revisions(self, event: int, model: str = "m0_rules",
                  player_code: Optional[int] = None) -> pd.DataFrame:
        """xPts for ONE future gameweek (`event`), as seen at each as-of time.

        Deliberately keyed on `event`, not `gw` (spec §3.3a): the same
        event is projected by every run whose 5-GW horizon covers it, so
        a revision series spans multiple `gw` partitions.
        """
        df = self.projections(model=model, event=event)
        if df.empty:
            return df
        if player_code is not None:
            df = df[df["code"] == player_code]
        return df.sort_values("asof").reset_index(drop=True)

    def coverage(self) -> pd.DataFrame:
        import json
        all_asofs = self._all_asofs()
        if not all_asofs:
            return _EMPTY_COVERAGE.copy()

        rows = []
        for asof in all_asofs:
            rj = self.root / "_runs" / f"asof={asof}" / "run.json"
            complete = rj.exists()
            gw, prov = None, None
            if complete:
                try:
                    meta = json.loads(rj.read_text(encoding="utf-8"))
                    gw, prov = meta.get("target_gameweek"), meta.get("provenance")
                except (OSError, ValueError):
                    complete = False
            if gw is None:
                gw = self._infer_gw_from_partitions(asof)
            rows.append({"asof": asof, "gw": gw, "complete": complete,
                         "reconstructed": prov == "reconstructed"})

        df = pd.DataFrame(rows)
        df = df[df["gw"].notna()]
        if df.empty:
            return _EMPTY_COVERAGE.copy()
        df["gw"] = df["gw"].astype(int)

        out = df.groupby("gw").agg(
            n_runs=("asof", "count"),
            n_complete=("complete", "sum"),
            first_asof=("asof", "min"),
            last_asof=("asof", "max"),
            n_reconstructed=("reconstructed", "sum"),
        ).reset_index()
        out["n_incomplete"] = out["n_runs"] - out["n_complete"]
        return out[["gw", "n_runs", "n_complete", "n_incomplete",
                    "first_asof", "last_asof", "n_reconstructed"]]

    def _infer_gw_from_partitions(self, asof: str) -> Optional[int]:
        proj = self.root / "projections"
        if not proj.exists():
            return None
        for gw_dir in proj.iterdir():
            if gw_dir.is_dir() and (gw_dir / f"asof={asof}").exists():
                try:
                    return int(gw_dir.name.split("=", 1)[1])
                except (IndexError, ValueError):
                    continue
        return None

    def sql(self, q: str) -> pd.DataFrame:
        con = self._connect()
        try:
            return con.execute(q).fetch_df()
        finally:
            con.close()


def open_archive(root: Optional[Path] = None) -> Archive:
    return Archive(Path(root) if root is not None else paths.HISTORY_DIR)
```

Create `fpl/history/manifest.py`:

```python
"""
manifest.py — DERIVED index of the archive (spec §3.7).

Deliberately NOT committed. A single file every scheduled run rewrites,
committed by a bot on four crons a week, is exactly the merge-conflict
generator the v4 plan rejects SQLite for. The immutable per-run run.json
files are the source of truth; this is a convenience index and is a pure
function of them, so it can be deleted and rebuilt at any time.

Rebuild: .venv\\Scripts\\python.exe -m fpl.history.manifest
"""
from __future__ import annotations

import json
from pathlib import Path

from fpl.history import paths, query


def build_manifest() -> dict:
    a = query.open_archive()
    runs = a.runs()
    coverage = a.coverage()
    return {
        "generated_note": "DERIVED from data/history/_runs/**/run.json — rebuildable, not a source of truth.",
        "n_complete_runs": int(len(runs)),
        "n_incomplete_runs": int(coverage["n_incomplete"].sum()) if not coverage.empty else 0,
        "gameweeks": coverage.to_dict(orient="records"),
        "runs": runs.to_dict(orient="records"),
    }


def write_manifest() -> Path:
    dst = paths.HISTORY_DIR / "manifest.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(build_manifest(), indent=2, default=str), encoding="utf-8")
    return dst


if __name__ == "__main__":
    p = write_manifest()
    print(f"Manifest written: {p}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_history_query.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add fpl/history/query.py fpl/history/manifest.py tests/test_history_query.py requirements.txt .gitignore
git commit -m "feat(history): read-only DuckDB query layer + derived manifest"
```

---

### Task 6: Wire the archive into the pipeline, and migrate for real

**Files:**
- Modify: `.github/workflows/weekly.yml`
- Run: `scripts/migrate_crude_archive.py`

**Interfaces:**
- Consumes: `fpl.history.archive` (`python -m` entry point), `scripts/migrate_crude_archive.py`.
- Produces: no new code interfaces; changes what the scheduled pipeline writes.

- [ ] **Step 1: Replace the crude archive step**

In `.github/workflows/weekly.yml`, DELETE this whole step:

```yaml
      - name: Archive this run's projections + decisions (crude, pre-schema)
        run: |
          ts="$(date -u +%Y%m%dT%H%M%SZ)"
          dest="data/history/${ts}"
          mkdir -p "$dest"
          [ -d data/projections ] && cp -r data/projections "$dest/projections"
          [ -d data/output ] && cp -r data/output "$dest/output"
          echo "Archived run to $dest"
```

and INSERT this immediately after the `Decide — squad/XI/captain recommendations` step (before the dashboard step), so the archive captures what the pipeline decided and is not downstream of `build_dashboard_data.py`'s known overwrite bug (docs/HANDOFF.md §9):

```yaml
      # Phase G (docs/superpowers/specs/2026-08-22-history-layer-design.md):
      # append-only, provenance-stamped, hive-partitioned archive. Replaces
      # the Tier 0.2 crude copy step. Placed immediately after Decide so the
      # archive unambiguously captures what the pipeline decided.
      - name: Archive this run (append-only, provenance-stamped)
        run: python -m fpl.history.archive
```

- [ ] **Step 2: Verify the YAML still parses**

Run: `.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/weekly.yml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 3: Run the real migration**

Run: `.venv\Scripts\python.exe scripts/migrate_crude_archive.py --delete-crude`
Expected output naming the migrated partition count, target GW1, `provenance=reconstructed`, and removal of the crude dir.

- [ ] **Step 4: Verify the migrated archive reads back**

Run:

```bash
.venv\Scripts\python.exe -c "from fpl.history import query; a=query.open_archive(); print(a.coverage().to_string(index=False)); print(a.runs()[['asof','target_gameweek','provenance','trigger']].to_string(index=False))"
```

Expected: one row in coverage for `gw=1` with `n_complete=1`, `n_incomplete=0`, `n_reconstructed=1`; one run with `provenance=reconstructed`.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all tests pass (116 existing + 36 new).

```bash
git add .github/workflows/weekly.yml data/history
git commit -m "feat(history): wire archive into pipeline, migrate crude partition"
```

---

### Task 7: Dashboard — Archive Coverage and Revision views

**Files:**
- Create: `scripts/build_history_data.py`
- Modify: `dashboard/template.html`
- Modify: `scripts/build_dashboard_data.py`
- Modify: `.github/workflows/weekly.yml`
- Test: `tests/test_build_history_data.py`

**Interfaces:**
- Consumes: `fpl.history.query.open_archive`.
- Produces: `build_history_payload() -> dict` with keys `generated_note`, `coverage`, `runs`, `revision`; writes `dashboard/history.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_history_data.py`:

```python
"""
Tests for scripts/build_history_data.py — spec §7.4.

The dashboard contract is unchanged: reads committed artefacts, never
recomputes. This script reads the archive through fpl.history.query and
emits one JSON blob; it must never call the projection pipeline.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpl.history import paths
import scripts.build_history_data as bhd

pytest.importorskip("duckdb")


def _write_run(asof, gw, rows):
    df = pd.DataFrame(rows, columns=["id", "event", "xpts"])
    p = paths.projections_partition(gw, asof, "m0_rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    m = paths.id_code_map_path(asof)
    m.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"id": [1], "code": [111], "web_name": ["A"]}).to_parquet(m, index=False)
    paths.run_json_path(asof).write_text(json.dumps({
        "run_id": "r", "asof": asof, "asof_iso": None, "git_sha": None,
        "git_dirty": None, "config_sha256": None, "trigger": "schedule",
        "target_gameweek": gw, "deadline_utc": None, "hours_to_deadline": -1.0,
        "provenance": "recorded", "archived": [],
    }), encoding="utf-8")


def test_payload_reports_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    payload = bhd.build_history_payload()
    assert payload["coverage"][0]["gw"] == 1
    assert payload["coverage"][0]["n_complete"] == 1


def test_revision_is_marked_insufficient_with_one_run(tmp_path, monkeypatch):
    """Honest empty state, not an invented chart (spec §7.2)."""
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    payload = bhd.build_history_payload()
    assert payload["revision"]["sufficient"] is False
    assert payload["revision"]["n_runs_max_in_a_gameweek"] == 1


def test_revision_becomes_sufficient_with_two_runs_in_one_gameweek(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    _write_run("20260818T090000Z", 1, [(1, 1, 5.0)])
    _write_run("20260821T090000Z", 1, [(1, 1, 7.0)])
    payload = bhd.build_history_payload()
    assert payload["revision"]["sufficient"] is True
    assert payload["revision"]["n_runs_max_in_a_gameweek"] == 2
    series = payload["revision"]["series"]
    assert series and series[0]["xpts"] == [5.0, 7.0]


def test_empty_archive_yields_honest_empty_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    (tmp_path / "history").mkdir()
    payload = bhd.build_history_payload()
    assert payload["coverage"] == []
    assert payload["revision"]["sufficient"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_history_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.build_history_data'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/build_history_data.py`:

```python
"""
build_history_data.py — emits dashboard/history.json (spec §7.4).

Reads the Phase G archive through fpl.history.query ONLY. Never calls
the projection pipeline, never writes to data/ — the dashboard contract
is "reads committed artefacts, never recomputes", and unlike
build_dashboard_data.py (see docs/HANDOFF.md §9) this script actually
honours it.

Usage: .venv\\Scripts\\python.exe scripts/build_history_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fpl.history import query  # noqa: E402

DASHBOARD_DIR = REPO_ROOT / "dashboard"

# A revision series needs at least two runs WITHIN one gameweek — one
# point is not a revision. Spec §7.2.
MIN_RUNS_FOR_REVISION = 2
TOP_N_PLAYERS = 12


def build_history_payload() -> dict:
    a = query.open_archive()
    cov = a.coverage()
    runs = a.runs()

    coverage = [] if cov.empty else json.loads(cov.to_json(orient="records"))
    runs_out = [] if runs.empty else json.loads(
        runs[[c for c in ("asof", "asof_iso", "target_gameweek", "trigger",
                          "provenance", "hours_to_deadline", "git_sha")
              if c in runs.columns]].to_json(orient="records")
    )

    max_runs = 0 if cov.empty else int(cov["n_complete"].max())
    revision = {
        "sufficient": max_runs >= MIN_RUNS_FOR_REVISION,
        "n_runs_max_in_a_gameweek": max_runs,
        "min_required": MIN_RUNS_FOR_REVISION,
        "event": None,
        "series": [],
    }

    if revision["sufficient"]:
        busiest_gw = int(cov.sort_values("n_complete", ascending=False)["gw"].iloc[0])
        revision["event"] = busiest_gw
        rev = a.revisions(event=busiest_gw)
        if not rev.empty and "code" in rev.columns:
            latest = rev.sort_values("asof").groupby("code")["xpts"].last()
            top = latest.sort_values(ascending=False).head(TOP_N_PLAYERS).index
            for code in top:
                sub = rev[rev["code"] == code].sort_values("asof")
                name = sub["web_name"].iloc[0] if "web_name" in sub.columns else str(code)
                revision["series"].append({
                    "code": int(code),
                    "web_name": name,
                    "asof": list(sub["asof"]),
                    "xpts": [round(float(v), 2) for v in sub["xpts"]],
                })

    return {
        "generated_note": "Read from data/history/ via fpl.history.query — never recomputed.",
        "coverage": coverage,
        "runs": runs_out,
        "revision": revision,
    }


def main() -> int:
    payload = build_history_payload()
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    out = DASHBOARD_DIR / "history.json"
    out.write_text(json.dumps(payload, indent=None, default=str), encoding="utf-8")
    print(f"Wrote {out} — {len(payload['coverage'])} gameweek(s), "
          f"{len(payload['runs'])} complete run(s), "
          f"revision sufficient={payload['revision']['sufficient']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_history_data.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the dashboard tab, views, and data hook**

In `dashboard/template.html`:

(a) Add a tab button after the `bakeoff` tab:

```html
    <div class="tab" role="tab" tabindex="-1" aria-selected="false" aria-controls="view-history" id="tab-history" data-view="history">History</div>
```

(b) Add the view section immediately before `<!-- ===================== VIEW 6: WEEK IN REVIEW =====================` :

```html
  <!-- ===================== VIEW: HISTORY (Phase G) ===================== -->
  <section class="view" id="view-history" role="tabpanel" aria-labelledby="tab-history">
    <div class="card">
      <h2>Archive coverage</h2>
      <div class="hint" style="font-size:12.5px;color:var(--chalk-dim);margin-bottom:14px;">
        What <span class="mono">data/history/</span> actually holds. The pipeline runs four times a gameweek;
        an incomplete run is one whose partitions exist but whose <span class="mono">run.json</span> completion
        marker does not, and it is excluded from every query rather than silently analysed as whole.
      </div>
      <div class="table-wrap">
        <table id="history-coverage-table">
          <thead><tr><th>GW</th><th>Complete runs</th><th>Incomplete</th><th>First as-of</th><th>Last as-of</th><th>Reconstructed</th></tr></thead>
          <tbody id="history-coverage-tbody"></tbody>
        </table>
      </div>
      <div class="note-banner" id="history-empty-note" style="display:none;">
        The archive is empty. Either no run has completed since the Phase G archive step landed, or archiving is failing silently — check the most recent Actions run.
      </div>
    </div>

    <div class="card">
      <h2>Projection revisions</h2>
      <div class="hint" style="font-size:12.5px;color:var(--chalk-dim);margin-bottom:14px;">
        How each player's projection for one gameweek moved as new information arrived. Fixes the
        <em>event</em> (the gameweek being projected) and tracks it across as-of times — which spans
        multiple run-target partitions.
      </div>
      <div class="note-banner" id="history-revision-insufficient" style="display:none;"></div>
      <div class="chart-box" id="history-revision-box" style="display:none;"><canvas id="chart-revision"></canvas></div>
    </div>
  </section>
```

(c) Add the history data hook immediately after the existing `const DATA = /*__DATA__*/;` line:

```html
const HISTORY = /*__HISTORY__*/null;
```

(d) Add this render block just before the final `})();` that closes the main IIFE:

```javascript
/* ===================== VIEW: HISTORY (Phase G) ===================== */
(function(){
  if(!HISTORY) return;
  const cov = HISTORY.coverage || [];
  const tbody = document.getElementById('history-coverage-tbody');
  if(cov.length === 0){
    document.getElementById('history-empty-note').style.display = '';
  } else {
    cov.forEach(r=>{
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="mono-cell">${r.gw}</td>
        <td class="mono-cell">${r.n_complete}</td>
        <td class="mono-cell">${r.n_incomplete > 0 ? '<span class="conf-low">'+r.n_incomplete+'</span>' : '0'}</td>
        <td class="mono-cell">${r.first_asof}</td>
        <td class="mono-cell">${r.last_asof}</td>
        <td class="mono-cell">${r.n_reconstructed}</td>`;
      tbody.appendChild(tr);
    });
  }

  const rev = HISTORY.revision || {sufficient:false};
  if(!rev.sufficient){
    const el = document.getElementById('history-revision-insufficient');
    el.style.display = '';
    el.textContent = `Not enough history yet. A revision series needs at least ${rev.min_required} completed runs within one gameweek; the archive currently has at most ${rev.n_runs_max_in_a_gameweek}. This view fills in automatically as the pipeline's four weekly runs accumulate — no code change needed.`;
    return;
  }

  document.getElementById('history-revision-box').style.display = '';
  const labels = (rev.series[0] && rev.series[0].asof) || [];
  new Chart(document.getElementById('chart-revision'), {
    type:'line',
    data:{labels, datasets: rev.series.map((s,i)=>({
      label:s.web_name, data:s.xpts, borderColor:SERIES[i % SERIES.length],
      backgroundColor:SERIES[i % SERIES.length], tension:0.25, pointRadius:3,
    }))},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:CHALK,usePointStyle:true,pointStyle:'line'}},
        title:{display:true,text:`xPts for GW${rev.event}, by when it was computed`,color:CHALK_DIM,font:{size:11}}},
      scales:{x:{ticks:{color:CHALK_DIM},grid:{display:false}},
              y:{ticks:{color:CHALK_DIM},grid:{color:BORDER}}}}
  });
})();
```

- [ ] **Step 6: Hook history.json into the template substitution**

In `scripts/build_dashboard_data.py`, find the template substitution block (currently around line 247-252) and change it so the history blob is substituted too. Replace:

```python
        html = html.replace("/*__DATA__*/", data_json)
```

with:

```python
        html = html.replace("/*__DATA__*/", data_json)
        # Phase G (spec §7.4): history.json is produced separately by
        # scripts/build_history_data.py. Substituted here so index.html
        # stays a single self-contained offline file. Absent history.json
        # is fine — the placeholder falls back to `null` and the History
        # view renders its own honest empty state.
        history_path = DASHBOARD_DIR / "history.json"
        if history_path.exists():
            html = html.replace("/*__HISTORY__*/", history_path.read_text(encoding="utf-8"))
```

- [ ] **Step 7: Add the pipeline step**

In `.github/workflows/weekly.yml`, add immediately AFTER the existing `Dashboard data (reads pipeline outputs, never recomputes)` step:

```yaml
      # Phase G: reads data/history/ via fpl.history.query only. Must run
      # BEFORE the dashboard data step on the next run to be embedded, so
      # ordering here means history.json lags by one run on first
      # introduction — acceptable and self-correcting.
      - name: Dashboard history data (reads the archive, never recomputes)
        run: python scripts/build_history_data.py
```

Then in the same file, add `dashboard/history.json` to the `git add` list in the commit step.

- [ ] **Step 8: Generate and verify in the browser**

Run:

```bash
.venv\Scripts\python.exe scripts/build_history_data.py
```

Then regenerate `index.html` WITHOUT invoking `build_dashboard_data.py` (which has the known `data/processed` overwrite bug — docs/HANDOFF.md §9):

```bash
.venv\Scripts\python.exe -c "from pathlib import Path; t=Path('dashboard/template.html').read_text(encoding='utf-8'); d=Path('dashboard/data.json').read_text(encoding='utf-8'); h=Path('dashboard/history.json').read_text(encoding='utf-8'); Path('dashboard/index.html').write_text(t.replace('/*__DATA__*/',d).replace('/*__HISTORY__*/',h), encoding='utf-8'); print('regenerated')"
```

Verify with the browser preview (`fpl-static-dashboard` in `.claude/launch.json`, port 8000):
- The History tab appears and is keyboard-reachable (arrow keys from adjacent tabs).
- Archive Coverage shows exactly one row: `gw=1`, `n_complete=1`, `n_reconstructed=1`.
- Revision shows the insufficient-data message naming "at most 1".
- No console errors.

- [ ] **Step 9: Run the full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all pass.

```bash
git add scripts/build_history_data.py tests/test_build_history_data.py dashboard/template.html dashboard/index.html dashboard/history.json scripts/build_dashboard_data.py .github/workflows/weekly.yml
git commit -m "feat(history): Archive Coverage + Revision dashboard views"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/PROJECT_LOG.md`
- Modify: `docs/HANDOFF.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Append a PROJECT_LOG section**

Add `## 14. Phase G — history layer (2026-08-22)` at the end of `docs/PROJECT_LOG.md`, covering: what was built (module by module), the three spec deviations and why (ISO-basic `asof` because Windows forbids colons — verified empirically; derived-not-committed manifest because a bot-rewritten file on four crons is a merge-conflict generator; `run.json`-last as completion marker), the `code` sidecar and why `id` alone would have been an identity-mapping-class bug, the `gw`-vs-`event` distinction, what the dashboard does and does NOT show, and the migration result.

- [ ] **Step 2: Update the HANDOFF status header**

Prepend a new dated status block to `docs/HANDOFF.md` summarising Phase G, and update the `## 9. Not yet done` list: the three deferred dashboard views (Timeline, Decision trail, Model drift) become a new bullet with their GW3+ gate.

- [ ] **Step 3: Update CLAUDE.md's architecture map**

Add `fpl/history/` to the pipeline diagram with a one-line description, and note in the dashboard section that `index.html` now embeds both `data.json` and `history.json`.

- [ ] **Step 4: Commit**

```bash
git add docs/PROJECT_LOG.md docs/HANDOFF.md CLAUDE.md
git commit -m "docs: Phase G history layer"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3.1 layout | 1 |
| §3.2 asof ISO-basic | 1 |
| §3.3 native formats / byte-faithful | 3 |
| §3.3a gw vs event | 5 (`revisions`), tested explicitly |
| §3.4 code sidecar | 3 (write), 5 (join) |
| §3.5 append-only + completion marker | 3 |
| §3.6 provenance | 2 |
| §3.7 derived manifest | 5 |
| §4 module layout | 1–5 |
| §5 query layer | 5 |
| §6 migration | 4, 6 |
| §7.1 Archive Coverage | 7 |
| §7.2 Revision scaffold | 7 |
| §7.3 deferred views | 8 (documented as deferred) |
| §7.4 wiring + conflict risk | 7 |
| §8 weekly.yml | 6, 7 |
| §9 testing | 1–5, 7 |
| §11 implementation order | Tasks ordered 1→8 accordingly |

No spec section is unimplemented.

**Type consistency:** `format_asof`/`parse_asof`, `PartitionExistsError` (defined in `archive.py`, imported by the migration script), `open_archive`/`Archive.{runs,coverage,projections,revisions,sql}`, `build_history_payload` — all names used in later tasks match their definitions in earlier ones. `paths.HISTORY_DIR` is the monkeypatch target throughout.

**Known risk carried from the spec (§7.4):** Task 7 Step 6 edits `scripts/build_dashboard_data.py`, which a concurrent background task is refactoring. The edit is additive and in the template-substitution block, not the write-side-effect path being changed — but the merge must be checked, not assumed.
