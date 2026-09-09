"""
news.py — PROJECT layer. Parses FPL's free-text `news` field into a
per-player start-probability signal (C2 / PROJECT_LOG §22).

Only the challenger model M6 (m6_news) consumes this — see
fpl/project/minutes.py. The live path never imports this module.

Cache: data/news/news_parsed.json, keyed by sha256(normalize(news_text)).
The LLM (fpl/collect/llm_client) is called ONLY on a cache miss; the
result is written straight back, so a re-run of the same bootstrap makes
zero API calls and produces byte-identical projections. Committed because
it is not reproducible without the LLM (unlike everything else in
data/raw/, which is gitignored).

Degrade-on-failure (fpl/collect/sources/base.py rule 1): a per-string LLM
failure leaves that string unparsed — its players fall through to
chance_of_playing in minutes.py. parse_news NEVER raises.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from fpl.collect import llm_client

NEWS_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "news" / "news_parsed.json"

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text.strip()).casefold()


def _key(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


@dataclass
class NewsHealth:
    distinct_strings: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    parse_failures: int = 0
    players_with_signal: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "distinct_strings": self.distinct_strings,
            "cache_hits": self.cache_hits,
            "api_calls": self.api_calls,
            "parse_failures": self.parse_failures,
            "players_with_signal": self.players_with_signal,
            "last_error": self.last_error,
        }


def _load_cache() -> dict:
    if not NEWS_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(NEWS_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    NEWS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEWS_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def parse_news(players_df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, NewsHealth]:
    news_cfg = config.get("news", {})
    prompt_version = news_cfg.get("prompt_version", llm_client.PROMPT_VERSION)
    min_conf = news_cfg.get("min_confidence", 0.5)

    health = NewsHealth()
    cache = _load_cache()
    dirty = False

    if "news" in players_df.columns:
        news_col = players_df["news"].fillna("").astype(str)
    else:
        news_col = pd.Series([""] * len(players_df), index=players_df.index)

    distinct = sorted({s for s in news_col if s.strip()})
    health.distinct_strings = len(distinct)

    parsed: dict[str, dict] = {}
    for text in distinct:
        key = _key(text)
        entry = cache.get(key)
        if entry is not None and entry.get("prompt_version") == prompt_version:
            health.cache_hits += 1
            parsed[text] = entry
            continue
        try:
            result = llm_client.parse_availability(text, config)
            health.api_calls += 1
        except Exception as exc:  # degrade — never raise
            health.parse_failures += 1
            health.last_error = f"{type(exc).__name__}: {exc}"
            continue
        entry = {
            "news_text": text,
            "start_prob": float(result["start_prob"]),
            "status": result["status"],
            "return_gw": result.get("return_gw"),
            "confidence": float(result["confidence"]),
            "reason": result.get("reason", ""),
            "model": news_cfg.get("model", ""),
            "prompt_version": prompt_version,
            "parsed_ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }
        cache[key] = entry
        parsed[text] = entry
        dirty = True

    if dirty:
        _save_cache(cache)

    def _signal(text: str) -> dict:
        blank = {"p": np.nan, "s": None, "g": pd.NA, "c": np.nan}
        if not text.strip():
            return blank
        e = parsed.get(text)
        if not e or e["confidence"] < min_conf:
            return blank
        return {
            "p": e["start_prob"],
            "s": e["status"],
            "g": pd.NA if e["return_gw"] is None else int(e["return_gw"]),
            "c": e["confidence"],
        }

    sig = [_signal(t) for t in news_col]
    out = pd.DataFrame({
        "id": players_df["id"].values,
        "news_start_prob": [x["p"] for x in sig],
        "news_status": [x["s"] for x in sig],
        "news_return_gw": pd.array([x["g"] for x in sig], dtype="Int64"),
        "news_confidence": [x["c"] for x in sig],
    })
    health.players_with_signal = int(out["news_start_prob"].notna().sum())
    return out, health


def _load_config() -> dict:
    import yaml

    p = Path(__file__).resolve().parents[2] / "config.yaml"
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    from fpl.transform import build_players

    cfg = _load_config()
    players = build_players.build_players()
    _, health = parse_news(players, cfg)
    print(f"[news] {health.to_dict()}")
