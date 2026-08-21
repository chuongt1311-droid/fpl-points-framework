"""
live_data.py — v3 plan §E: the data-loading half of the live Streamlit
decision layer, kept separate from app.py's UI code so the FROZEN/LIVE
boundary (plan §E1) is enforced in one obvious place.

**Everything in this file reads already-committed artefacts. Nothing
here calls fpl.project.project.project_gameweeks(), fpl.transform.
build_players.build_players(), or any other function that re-runs the
projection pipeline.** That pipeline is network-bound, rate-limited, and
must stay reproducible at deadline time — running it from a dashboard
would destroy the audit trail permanently (plan §E1's own words). The
only computation this file does is RESHAPING already-computed numbers
(fpl.project.project.weighted_horizon_total, a pure aggregation over a
frozen parquet file) — never re-deriving them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
import yaml

from fpl.project import project as project_mod

ROOT = Path(__file__).resolve().parents[1]
PROJECTIONS_DIR = ROOT / "data" / "projections"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "data" / "output"
STATE_DIR = ROOT / "data" / "state"
SCRATCH_DIR = ROOT / "data" / "scratch"
CONFIG_PATH = ROOT / "config.yaml"

MODEL_LABELS = {
    "m0_rules": "M0 — rules_v1 (champion)",
    "m2_xg": "M2 — xG blend",
    "m3_understat": "M3 — Understat npxG blend (does not beat M2 in backtest)",
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _projection_path(model: str, gameweek: int) -> Path:
    if model == "m0_rules":
        return PROJECTIONS_DIR / f"gw{gameweek}.parquet"
    return PROJECTIONS_DIR / model / f"gw{gameweek}.parquet"


def available_models(gameweek: int) -> list[str]:
    """Which models actually have a committed projection for this
    gameweek — never all of MODEL_LABELS blindly, so the UI can't offer a
    model whose frozen artefact doesn't exist yet."""
    return [m for m in MODEL_LABELS if _projection_path(m, gameweek).exists()]


def available_gameweeks() -> list[int]:
    gws = set()
    for p in PROJECTIONS_DIR.glob("gw*.parquet"):
        gws.add(int(p.stem.replace("gw", "")))
    for sub in PROJECTIONS_DIR.iterdir():
        if sub.is_dir():
            for p in sub.glob("gw*.parquet"):
                gws.add(int(p.stem.replace("gw", "")))
    return sorted(gws)


def _artefact_hash(path: Path) -> str:
    """Cache key component (plan §E4: 'keyed by (model, gameweek,
    artefact_hash)') — content hash, not just mtime, so a redeploy that
    rewrites the file with identical bytes doesn't needlessly bust the
    cache, but a real content change always does."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@st.cache_data(show_spinner=False)
def _load_player_inputs_cached(model: str, gameweek: int, artefact_hash: str, config_hash: str) -> pd.DataFrame:
    """The actual cached loader — `artefact_hash`/`config_hash` are cache
    keys only (plan §E4), not used inside; Streamlit's cache_data hashes
    every argument, and a DataFrame/dict argument would be slow to hash
    directly, so the caller pre-hashes the file/config instead."""
    config = load_config()
    path = _projection_path(model, gameweek)
    projections = pd.read_parquet(path)
    totals = project_mod.weighted_horizon_total(projections, config)

    players = pd.read_parquet(PROCESSED_DIR / "players.parquet")
    totals = totals.merge(players[["id", "code", "status"]], on="id", how="left")
    return totals


def load_player_inputs(model: str, gameweek: int) -> pd.DataFrame:
    path = _projection_path(model, gameweek)
    artefact_hash = _artefact_hash(path)
    config_hash = _artefact_hash(CONFIG_PATH)
    return _load_player_inputs_cached(model, gameweek, artefact_hash, config_hash)


def load_canonical_recommendation(gameweek: int) -> Optional[dict]:
    """The PINNED, unmodifiable committed artefact (plan §E3) — always
    the frozen weekly pipeline's own output, never touched by a live
    solve. None if it doesn't exist yet (no GW recommendation committed)."""
    path = OUTPUT_DIR / f"gw{gameweek}_recommendations.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def log_live_solve(entry: dict) -> None:
    """Plan §E3: 'Live solves append to data/scratch/live_solves.jsonl —
    constraints, model, result, timestamp. If you deviate from the model,
    that deviation is recorded and can be graded later.' Append-only,
    same pattern as fpl/collect/snapshot.py's own irreversible-history
    reasoning — never overwritten, never merged in place.

    NEVER writes to data/state/ — that's the separate, explicit
    'commit a squad' action this file deliberately does not implement
    (plan §E3's last mitigation: 'a live solve never writes to
    data/state/. Committing a squad is a separate, explicit action.')
    """
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SCRATCH_DIR / "live_solves.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
