"""
actuals.py — COLLECT layer. FPL_V2_DESIGN.md spec §3.1: real per-player,
per-gameweek results, appended to data/actuals/actuals_{season}.csv.

WHY THIS GATES ON data_checked, NOT JUST finished: bonus points and BPS are
PROVISIONAL until FPL flips `data_checked` true for that event (typically a
day or two after the final whistle, once their own review is done). Pulling
and grading against provisional bonus produces a regret number (spec §3.4)
that then changes retroactively when the real bonus lands — worse than
useless, actively misleading. `finished=True, data_checked=False` is a
real, common in-between state this module must not treat as ready.

Unlike fpl/collect/snapshot.py (a POINT-IN-TIME BELIEF table — every run
is a new, valid row, even if beliefs didn't change), a finished, data-
checked gameweek's results are a SETTLED FACT — they don't change on a
second pull. So the append guard here is keyed on `event` already being
present, not run_id-based dedup: there is no legitimate reason to want two
rows for the same (player, event) in this table.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
ACTUALS_DIR = Path(__file__).resolve().parents[2] / "data" / "actuals"

# Per plan §4.1 / spec §3.1 — every scoring channel the live model itself
# uses, plus identity/context columns. `total_points` and `minutes` are the
# two the hindsight engine (spec §3.3) actually needs; the rest is here so
# spec §3.4's "calibrate per channel, not just per position" regret work
# doesn't need a second collector later.
STAT_COLUMNS = [
    "minutes", "total_points", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "saves", "bonus", "bps", "yellow_cards", "red_cards",
    "defensive_contribution",
]

ACTUALS_COLUMNS = ["code", "id", "event"] + STAT_COLUMNS


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _actuals_path(config: dict) -> Path:
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    return ACTUALS_DIR / f"actuals_{config['season']}.csv"


def gameweek_is_ready(gw: int, bootstrap: dict) -> bool:
    """finished AND data_checked — see module docstring for why both."""
    events = {e["id"]: e for e in bootstrap.get("events", [])}
    event = events.get(gw)
    if event is None:
        return False
    return bool(event.get("finished")) and bool(event.get("data_checked"))


def _id_to_code(bootstrap: dict) -> dict[int, int]:
    return {e["id"]: e["code"] for e in bootstrap["elements"]}


def build_actuals_rows(gw: int, event_live: dict, bootstrap: dict) -> pd.DataFrame:
    """
    event_live: the raw dict from fpl.collect.fpl_client.get_event_live(gw)
    — {"elements": [{"id": ..., "stats": {...}}, ...]}.
    """
    id_to_code = _id_to_code(bootstrap)
    rows = []
    for element in event_live["elements"]:
        stats = element.get("stats", {})
        row = {"code": id_to_code.get(element["id"]), "id": element["id"], "event": gw}
        for col in STAT_COLUMNS:
            row[col] = stats.get(col, 0)
        rows.append(row)
    return pd.DataFrame(rows)[ACTUALS_COLUMNS]


def append_actuals(rows: pd.DataFrame, config: Optional[dict] = None, path: Optional[Path] = None) -> Path:
    """
    Append-only, guarded by `event` already present (see module docstring
    for why this differs from snapshot.py's run_id-based dedup) — a settled
    gameweek's results don't need a second row from a re-run.
    """
    config = config or load_config()
    out_path = path or _actuals_path(config)

    if out_path.exists():
        existing = pd.read_csv(out_path, usecols=["event"])
        gw = rows["event"].iloc[0]
        if gw in set(existing["event"]):
            return out_path
        rows.to_csv(out_path, mode="a", header=False, index=False)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(out_path, mode="w", header=True, index=False)
    return out_path


def collect_gameweek_actuals(gw: int, config: Optional[dict] = None) -> Optional[Path]:
    """
    Entry point for the weekly job, called once a gameweek is plausibly
    finished. Returns None (and writes nothing) if the gameweek isn't
    finished+data_checked yet — a no-op is the correct, quiet outcome for
    a job that runs on a schedule regardless of whether a GW just ended.
    """
    from fpl.collect import fpl_client

    config = config or load_config()
    bootstrap_path = RAW_DIR / "bootstrap_static.json"
    if not bootstrap_path.exists():
        raise FileNotFoundError(f"{bootstrap_path} not found — run fpl.collect.fpl_client first.")
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))

    if not gameweek_is_ready(gw, bootstrap):
        print(f"[actuals] GW{gw} not finished+data_checked yet — nothing to collect.")
        return None

    event_live = fpl_client.get_event_live(gw, config)
    rows = build_actuals_rows(gw, event_live, bootstrap)
    return append_actuals(rows, config)


if __name__ == "__main__":
    import sys

    gw = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    path = collect_gameweek_actuals(gw)
    if path:
        print(f"Actuals written: {path}")
