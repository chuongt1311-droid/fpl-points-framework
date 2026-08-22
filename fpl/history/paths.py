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
empirically, the v4 plan's literal `asof={utc_iso}` is unimplementable
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
