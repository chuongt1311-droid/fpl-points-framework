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
    meta = provenance.build_run_metadata(
        target_gameweek=_target_gameweek(), deadline_utc=_deadline_utc(), now=now
    )
    asof = meta["asof"]

    found = discover_artefacts()
    archived = []

    for gw, model, src in found["projections"]:
        dst = paths.projections_partition(gw, asof, model)
        _copy(src, dst)
        archived.append({"domain": "projections", "gw": gw, "model": model,
                         "path": str(dst.relative_to(paths.HISTORY_DIR))})

    for gw, src in found["decisions"]:
        dst = paths.decisions_partition(gw, asof)
        _copy(src, dst)
        archived.append({"domain": "decisions", "gw": gw, "model": None,
                         "path": str(dst.relative_to(paths.HISTORY_DIR))})

    for gw, model, src in found["health"]:
        dst = paths.health_partition(gw, asof, model)
        _copy(src, dst)
        archived.append({"domain": "health", "gw": gw, "model": model,
                         "path": str(dst.relative_to(paths.HISTORY_DIR))})

    _write_id_code_map(asof)

    meta["archived"] = archived
    _write_run_json(asof, meta)
    return meta


if __name__ == "__main__":
    m = archive_run()
    print(f"Archived run {m['asof']} — {len(m['archived'])} partition(s), "
          f"target GW{m['target_gameweek']}, trigger={m['trigger']}")
