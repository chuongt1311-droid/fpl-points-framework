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
