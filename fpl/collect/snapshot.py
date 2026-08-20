"""
snapshot.py — COLLECT layer. Appends one row per player per pipeline run to
data/snapshots/availability_{season}.csv — spec §2 (FPL_V2_DESIGN.md).

WHY THIS EXISTS: `players.parquet` is overwritten on every run, so what the
model believed about a player's availability at any past point in time is
otherwise lost forever the moment the next run happens. This is the only
genuinely IRREVERSIBLE piece of the v2 spec — every gameweek that passes
without a snapshot is a training row (spec §5, learned availability) that
can never be recovered later, no matter how much other work gets done first.

Hooks in immediately after get_bootstrap_static() and BEFORE any transform
step, so a row reflects the raw API state, not anything derived downstream.

TWO-PHASE WRITE (spec §2.3): `minutes_factor` depends on the transform
layer (compute_minutes_factor), which runs after this module's raw capture.
  1. build_snapshot_rows() — raw bootstrap fields only, minutes_factor null.
  2. attach_minutes_factor() — fills minutes_factor in once the projection
     step has actually succeeded.
If the projection step raises before phase 2, run_snapshot()'s caller
(the __main__ block below, and the weekly job) still appends the phase-1
rows with minutes_factor null. A failed projection must NEVER cost a
snapshot row.

`hours_to_deadline` is the load-bearing column (spec §2.2): a 75% flag four
days out and the same 75% flag two hours out are different events with
different resolution rates, and this is computable ONLY at capture time —
it cannot be reconstructed later from any other source.

`code`, not `id`, per identity.py's rule (docs/HANDOFF.md §3) — this table
is meant to outlive the season and be joined across a season boundary,
which is precisely where `id` breaks.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshots"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

POSITION_MAP = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# Order matches spec §2.2 exactly.
SNAPSHOT_COLUMNS = [
    "run_id", "snapshot_ts", "next_event", "deadline_time", "hours_to_deadline",
    "code", "id", "position", "team", "status", "news", "news_added",
    "chance_of_playing_this_round", "chance_of_playing_next_round",
    "now_cost", "selected_by_percent", "minutes_factor",
]

# Raw bootstrap-static element fields this module needs, kept-if-present —
# same "degrade gracefully rather than KeyError" contract as
# build_players.ELEMENT_COLUMNS (see docs/HANDOFF.md §5 finding 6: a hard
# select here would be the same bug class in a new file).
_RAW_ELEMENT_COLUMNS = [
    "id", "code", "element_type", "team", "status", "news", "news_added",
    "chance_of_playing_this_round", "chance_of_playing_next_round",
    "now_cost", "selected_by_percent",
]


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_cached_bootstrap() -> dict:
    """Reads the bootstrap-static JSON already saved by fpl.collect.fpl_client
    — same read-not-repull pattern as build_players._load_bootstrap(). Only
    used as a fallback when run_snapshot()/__main__ isn't handed a fresh
    `bootstrap` dict directly."""
    path = RAW_DIR / "bootstrap_static.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m fpl.collect.fpl_client` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_path(config: dict) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SNAPSHOT_DIR / f"availability_{config['season']}.csv"


def _next_event(bootstrap: dict) -> Optional[dict]:
    """First unfinished GW from bootstrap-static `events` — None only if
    every event is finished (end of season); don't crash on that."""
    events = sorted(bootstrap.get("events", []), key=lambda e: e["id"])
    for e in events:
        if not e.get("finished"):
            return e
    return None


def build_snapshot_rows(bootstrap: dict, run_id: str, snapshot_ts: datetime) -> pd.DataFrame:
    """
    Phase 1 of the two-phase write: everything available straight from
    bootstrap-static, before compute_minutes_factor has run. minutes_factor
    is left null (filled in later by attach_minutes_factor).
    """
    next_event = _next_event(bootstrap)
    if next_event is not None:
        next_event_id = next_event["id"]
        deadline_time = next_event["deadline_time"]
        deadline = datetime.fromisoformat(deadline_time.replace("Z", "+00:00"))
        hours_to_deadline = (deadline - snapshot_ts).total_seconds() / 3600.0
    else:
        next_event_id, deadline_time, hours_to_deadline = None, None, None

    elements = pd.DataFrame(bootstrap["elements"])
    present = [c for c in _RAW_ELEMENT_COLUMNS if c in elements.columns]
    df = elements[present].copy()
    for c in _RAW_ELEMENT_COLUMNS:
        if c not in df.columns:
            df[c] = None  # column still emitted (schema stays stable), just empty

    df["position"] = df["element_type"].map(POSITION_MAP)
    df = df.drop(columns=["element_type"])

    df["run_id"] = run_id
    df["snapshot_ts"] = snapshot_ts.isoformat()
    df["next_event"] = next_event_id
    df["deadline_time"] = deadline_time
    df["hours_to_deadline"] = hours_to_deadline
    df["minutes_factor"] = pd.NA

    return df[SNAPSHOT_COLUMNS]


def attach_minutes_factor(rows: pd.DataFrame, minutes_df: pd.DataFrame) -> pd.DataFrame:
    """Phase 2: fills minutes_factor into already-built raw rows, once
    fpl.project.minutes.compute_minutes_factor has actually succeeded."""
    out = rows.drop(columns=["minutes_factor"]).merge(
        minutes_df[["id", "minutes_factor"]], on="id", how="left"
    )
    return out[SNAPSHOT_COLUMNS]


def append_snapshot(rows: pd.DataFrame, config: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    """
    Append-only write. Guards, per spec §2.4's exit gate:
      - never overwrites existing rows (mode="a", no header on append)
      - a deliberately re-run job with the same run_id is rejected outright,
        not merged/deduped after the fact — row count must strictly increase
        on every *new* run and stay unchanged on a retried one.
    """
    config = config or load_config()
    out_path = path or _snapshot_path(config)

    if out_path.exists():
        existing = pd.read_csv(out_path, usecols=["run_id"])
        run_id = rows["run_id"].iloc[0]
        if run_id in set(existing["run_id"]):
            return out_path
        rows.to_csv(out_path, mode="a", header=False, index=False)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(out_path, mode="w", header=True, index=False)
    return out_path


def run_snapshot(
    config: Optional[dict] = None,
    bootstrap: Optional[dict] = None,
    minutes_df: Optional[pd.DataFrame] = None,
    path: Optional[Path] = None,
) -> Path:
    """
    Entry point for the weekly job. Conceptually runs immediately after
    get_bootstrap_static(), before any transform step. In this codebase's
    one-module-per-process pattern (each pipeline stage is its own
    `python -m fpl.x.y` invocation — see docs/HANDOFF.md §2), "immediately
    after" means the ORDERING of steps in the job, not a shared in-process
    dict — so if `bootstrap` isn't passed in directly, this reads the raw
    JSON already saved to disk by fpl.collect.fpl_client's own run (same
    pattern as build_players._load_bootstrap()) rather than live-pulling a
    second time, which would risk a different bootstrap snapshot than the
    rest of that same pipeline run used. Pass `minutes_df` once
    compute_minutes_factor() has run downstream; the caller is responsible
    for still calling this (with minutes_df=None) on the raw fields alone
    if the projection step fails, per the two-phase-write contract above.
    """
    config = config or load_config()
    bootstrap = bootstrap if bootstrap is not None else _load_cached_bootstrap()
    run_id = str(uuid.uuid4())
    snapshot_ts = datetime.now(timezone.utc)

    rows = build_snapshot_rows(bootstrap, run_id, snapshot_ts)
    if minutes_df is not None:
        rows = attach_minutes_factor(rows, minutes_df)

    return append_snapshot(rows, config, path)


if __name__ == "__main__":
    from fpl.project import minutes as minutes_mod
    from fpl.transform import build_players

    cfg = load_config()
    bs = _load_cached_bootstrap()
    run_id = str(uuid.uuid4())
    snapshot_ts = datetime.now(timezone.utc)
    rows = build_snapshot_rows(bs, run_id, snapshot_ts)

    try:
        players = build_players.build_players(bs)
        minutes_df = minutes_mod.compute_minutes_factor(players, cfg)
        rows = attach_minutes_factor(rows, minutes_df)
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any failure
        # downstream of the raw capture must not cost the snapshot row itself.
        print(f"[snapshot] WARNING: projection step failed ({exc!r}) — writing "
              f"raw snapshot rows with minutes_factor null. A failed projection "
              f"must never cost a snapshot row.")

    out_path = append_snapshot(rows, cfg)
    print(f"Snapshot written: {out_path}")
    print(f"Rows this run: {len(rows)} | next_event: {rows['next_event'].iloc[0]} | "
          f"hours_to_deadline: {rows['hours_to_deadline'].iloc[0]:.2f}")
    flagged = rows[(rows["status"] != "a") | (rows["news"].fillna("") != "")]
    print(f"Flagged players (status != 'a' or non-empty news): {len(flagged)}")
    if len(flagged):
        print(flagged[["id", "status", "news", "chance_of_playing_next_round"]].head(10).to_string(index=False))
