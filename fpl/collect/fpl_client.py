"""
fpl_client.py — COLLECT layer, official FPL API client.

No auth required. Base: https://fantasy.premierleague.com/api/

  bootstrap-static/         all players, teams, gameweek deadlines   (weekly)
  fixtures/                 every fixture, event/FDR/kickoff         (weekly)
  fixtures/?event={gw}      single-GW fixtures                       (on demand)
  element-summary/{id}/     per-player match history + upcoming FDR  (weekly, shortlist only)
  event/{gw}/live/          live per-player stats for a GW           (post-GW)

Rate limit: 1 request/second (config: data.rate_limit_seconds), User-Agent set.
Never loop all ~700 players for element-summary — pre-filter to a shortlist
first (get_shortlist_ids). 150 players x 1 req/sec ~= 2.5 min, comfortable
inside a 20-minute Actions job. See plan §3.1.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

_last_request_time = 0.0


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _rate_limit(seconds: float) -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < seconds:
        time.sleep(seconds - elapsed)
    _last_request_time = time.monotonic()


def _get(url: str, config: dict) -> Any:
    _rate_limit(config["data"]["rate_limit_seconds"])
    headers = {"User-Agent": config["api"]["user_agent"]}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _save_raw(name: str, payload: Any) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{name}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def get_bootstrap_static(config: Optional[dict] = None, save: bool = True) -> dict:
    config = config or load_config()
    url = f"{config['api']['base_url']}/bootstrap-static/"
    data = _get(url, config)
    if save:
        _save_raw("bootstrap_static", data)
    return data


def get_fixtures(config: Optional[dict] = None, event: Optional[int] = None, save: bool = True) -> list[dict]:
    config = config or load_config()
    url = f"{config['api']['base_url']}/fixtures/"
    if event is not None:
        url += f"?event={event}"
    data = _get(url, config)
    if save:
        _save_raw(f"fixtures_gw{event}" if event else "fixtures", data)
    return data


def get_element_summary(player_id: int, config: Optional[dict] = None, save: bool = True) -> dict:
    config = config or load_config()
    url = f"{config['api']['base_url']}/element-summary/{player_id}/"
    data = _get(url, config)
    if save:
        _save_raw(f"element_summary_{player_id}", data)
    return data


def get_event_live(gw: int, config: Optional[dict] = None, save: bool = True) -> dict:
    config = config or load_config()
    url = f"{config['api']['base_url']}/event/{gw}/live/"
    data = _get(url, config)
    if save:
        _save_raw(f"event_{gw}_live", data)
    return data


def get_shortlist_ids(bootstrap: dict, top_n: int = 150, min_minutes: int = 1) -> list[int]:
    """
    Pre-filter before calling element-summary per player. Ranks by minutes
    played (proxy for relevance) then total_points, falling back to the full
    pool pre-season / GW1 when nobody has minutes yet.
    """
    elements = bootstrap["elements"]
    with_minutes = [e for e in elements if e.get("minutes", 0) >= min_minutes]
    pool = with_minutes if with_minutes else elements
    ranked = sorted(
        pool,
        key=lambda e: (e.get("minutes", 0), e.get("total_points", 0)),
        reverse=True,
    )
    return [e["id"] for e in ranked[:top_n]]


def pull_shortlist_element_summaries(bootstrap: dict, config: Optional[dict] = None) -> dict[int, dict]:
    config = config or load_config()
    top_n = config["data"]["element_summary_shortlist_size"]
    ids = get_shortlist_ids(bootstrap, top_n=top_n)
    out: dict[int, dict] = {}
    for pid in ids:
        out[pid] = get_element_summary(pid, config=config)
    return out


if __name__ == "__main__":
    cfg = load_config()
    bs = get_bootstrap_static(cfg)
    fx = get_fixtures(cfg)
    print(f"Pulled bootstrap-static: {len(bs['elements'])} players, {len(bs['teams'])} teams")
    print(f"Pulled fixtures: {len(fx)} fixtures")
