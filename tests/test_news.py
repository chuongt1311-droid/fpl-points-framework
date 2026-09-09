"""
Tests for fpl/project/news.py — parses FPL's `news` field into a per-player
start-probability signal, cached by a hash of the news text so re-runs make
zero API calls (C2 / PROJECT_LOG §22).

No network: llm_client.parse_availability is monkeypatched everywhere.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fpl.project import news as news_mod

CONFIG = {"news": {"model": "claude-haiku-4-5", "prompt_version": 1, "min_confidence": 0.5}}


def _players(rows):
    defaults = {"news": "", "status": "a"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _write_cache(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_parse_news_cache_hit_makes_no_api_call(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    key = news_mod._key("Knock - 75% chance of playing")
    _write_cache(cache, {key: {
        "news_text": "Knock - 75% chance of playing", "start_prob": 0.75,
        "status": "doubt", "return_gw": None, "confidence": 0.9,
        "reason": "x", "model": "claude-haiku-4-5", "prompt_version": 1,
        "parsed_ts": "20260909T000000Z",
    }})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)

    def _boom(*a, **k):
        raise AssertionError("LLM must not be called on a cache hit")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", _boom)

    players = _players([{"id": 1, "news": "Knock - 75% chance of playing"}])
    out, health = news_mod.parse_news(players, CONFIG)

    assert out.loc[out["id"] == 1, "news_start_prob"].iloc[0] == pytest.approx(0.75)
    assert health.api_calls == 0
    assert health.cache_hits == 1


def test_parse_news_cache_miss_calls_llm_and_persists(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    _write_cache(cache, {})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda text, cfg: {
        "start_prob": 0.0, "status": "injured", "return_gw": 7,
        "confidence": 0.9, "reason": "ACL",
    })

    players = _players([{"id": 2, "news": "Serious knee injury - Expected back GW7"}])
    out, health = news_mod.parse_news(players, CONFIG)

    assert out.loc[out["id"] == 2, "news_start_prob"].iloc[0] == pytest.approx(0.0)
    assert health.api_calls == 1
    stored = json.loads(cache.read_text(encoding="utf-8"))
    entry = next(iter(stored.values()))
    assert entry["start_prob"] == 0.0
    assert entry["prompt_version"] == 1
    assert "parsed_ts" in entry


def test_parse_news_llm_failure_degrades_to_no_signal(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    _write_cache(cache, {})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)

    def _raise(text, cfg):
        raise RuntimeError("api down")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", _raise)

    players = _players([{"id": 3, "news": "Late fitness test"}])
    out, health = news_mod.parse_news(players, CONFIG)  # must NOT raise

    assert np.isnan(out.loc[out["id"] == 3, "news_start_prob"].iloc[0])
    assert health.parse_failures == 1
    assert health.last_error is not None


def test_parse_news_empty_news_is_not_parsed(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    _write_cache(cache, {})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)
    monkeypatch.setattr(news_mod.llm_client, "parse_availability",
                        lambda t, c: (_ for _ in ()).throw(AssertionError("no news → no call")))

    players = _players([{"id": 4, "news": ""}, {"id": 5, "news": None}])
    out, health = news_mod.parse_news(players, CONFIG)

    assert out["news_start_prob"].isna().all()
    assert health.distinct_strings == 0


def test_parse_news_normalizes_before_hashing(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    key = news_mod._key("knock - 75% chance")
    _write_cache(cache, {key: {
        "news_text": "knock - 75% chance", "start_prob": 0.75, "status": "doubt",
        "return_gw": None, "confidence": 0.9, "reason": "x",
        "model": "m", "prompt_version": 1, "parsed_ts": "t",
    }})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)
    monkeypatch.setattr(news_mod.llm_client, "parse_availability",
                        lambda t, c: (_ for _ in ()).throw(AssertionError("variant must hit cache")))

    players = _players([{"id": 6, "news": "  Knock -  75% chance "}])
    out, health = news_mod.parse_news(players, CONFIG)
    assert out.loc[out["id"] == 6, "news_start_prob"].iloc[0] == pytest.approx(0.75)
    assert health.cache_hits == 1


def test_parse_news_low_confidence_is_discarded(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    _write_cache(cache, {})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: {
        "start_prob": 0.2, "status": "doubt", "return_gw": None,
        "confidence": 0.3, "reason": "very unsure",
    })
    players = _players([{"id": 7, "news": "Some ambiguous note"}])
    out, health = news_mod.parse_news(players, CONFIG)
    assert np.isnan(out.loc[out["id"] == 7, "news_start_prob"].iloc[0])


def test_parse_news_prompt_version_bump_forces_reparse(tmp_path, monkeypatch):
    cache = tmp_path / "news_parsed.json"
    key = news_mod._key("knock")
    _write_cache(cache, {key: {
        "news_text": "knock", "start_prob": 0.9, "status": "doubt",
        "return_gw": None, "confidence": 0.9, "reason": "old",
        "model": "m", "prompt_version": 1, "parsed_ts": "t",
    }})
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", cache)
    calls = []
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: calls.append(t) or {
        "start_prob": 0.4, "status": "doubt", "return_gw": None,
        "confidence": 0.9, "reason": "new",
    })

    cfg2 = {"news": {"model": "m", "prompt_version": 2, "min_confidence": 0.5}}
    players = _players([{"id": 8, "news": "knock"}])
    out, _ = news_mod.parse_news(players, cfg2)
    assert calls == ["knock"]
    assert out.loc[out["id"] == 8, "news_start_prob"].iloc[0] == pytest.approx(0.4)
