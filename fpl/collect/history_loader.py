"""
history_loader.py — COLLECT layer, historical archive.

Pulls the vaastav/Fantasy-Premier-League GitHub archive (per-season CSVs,
mirroring the FPL API) for the last N seasons (config: history.seasons).
Used to establish per-90 baselines for returning players before GW1, and for
backtesting the projection model in Phase 2/3. See plan §3.2.

Note: 2025/26 is the only season with DEFCON scoring — DEFCON baselines must
come from that season alone (config: defcon.baseline_season_only). Earlier
seasons feed goals/assists/clean-sheet/bonus baselines only.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "history"

# relative path within a season's folder in the archive -> local filename
FILES = {
    "merged_gw": "gws/merged_gw.csv",
    "players_raw": "players_raw.csv",
    "teams": "teams.csv",
    "fixtures": "fixtures.csv",
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fetch_csv(season: str, key: str, base_url: str, rate_limit: float) -> Optional[pd.DataFrame]:
    rel_path = FILES[key]
    url = f"{base_url}/{season}/{rel_path}"
    time.sleep(rate_limit)
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        print(f"  [history] {season}/{rel_path} -> HTTP {resp.status_code}, skipped")
        return None

    out_dir = RAW_DIR / season / Path(rel_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / Path(rel_path).name
    out_path.write_bytes(resp.content)

    try:
        return pd.read_csv(out_path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(out_path, encoding="latin-1")


def load_season(season: str, config: Optional[dict] = None) -> dict[str, pd.DataFrame]:
    config = config or load_config()
    base_url = config["history"]["repo_raw_base"]
    rate_limit = config["data"]["rate_limit_seconds"]
    out: dict[str, pd.DataFrame] = {}
    for key in FILES:
        df = _fetch_csv(season, key, base_url, rate_limit)
        if df is not None:
            out[key] = df
    return out


def seasons_to_load(config: dict) -> list[str]:
    """Completed seasons (config.history.seasons) PLUS the current season.

    The current season is needed by fpl/project/minutes.py's rolling
    start-rate window (an in-season role change must not stay invisible for
    weeks). config.history.seasons itself is left completed-seasons-only —
    baseline.py depends on that. `_fetch_csv` no-ops on a 404, so listing
    the current season here is harmless before vaastav publishes it.
    """
    seasons = list(config["history"]["seasons"])
    current = config.get("season")
    if current and current not in seasons:
        seasons.append(current)
    return seasons


def load_seasons(seasons: Optional[list[str]] = None, config: Optional[dict] = None) -> dict[str, dict[str, pd.DataFrame]]:
    config = config or load_config()
    seasons = seasons or seasons_to_load(config)
    return {season: load_season(season, config) for season in seasons}


if __name__ == "__main__":
    cfg = load_config()
    seasons_data = load_seasons(config=cfg)
    for season, tables in seasons_data.items():
        summary = ", ".join(f"{k}={len(v)} rows" for k, v in tables.items()) or "no files fetched"
        print(f"{season}: {summary}")
