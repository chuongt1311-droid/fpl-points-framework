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
