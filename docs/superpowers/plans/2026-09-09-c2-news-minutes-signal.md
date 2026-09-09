# C2 — News → Minutes Signal (model M6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse FPL's own free-text `news` field into a structured start-probability via an LLM (deterministic hash-keyed cache), and feed it into `minutes.py` gated to a new challenger model **M6 (`m6_news`)** — M0 stays the untouched live champion.

**Architecture:** A thin Anthropic wrapper (`llm_client.py`) parses one news string at a time into `{start_prob, status, return_gw, confidence, reason}`. `news.py` walks the current bootstrap, hits `data/news/news_parsed.json` for cache, calls the LLM only on a miss, and returns a per-player signal — never raising (degrade-on-failure). `minutes.py` gains a `model` param; for `model == "m6_news"` a new branch uses the parsed `start_prob` in place of the `chance_of_playing` branch. `project.py` registers `m6_news`; the history archive gains an `m6_news` challenger partition; `news_scorecard.py` scores M6 vs M0 minutes predictions against actual `starts` once GWs settle. Promotion criteria are pre-registered in a new `docs/M6_PREREGISTRATION.md`.

**Tech Stack:** Python 3.12, `pandas==2.2.3`, `anthropic` (new dependency), PuLP (unaffected), pytest. `.venv\Scripts\python.exe` always — never bare `python`.

**Spec:** `docs/superpowers/specs/2026-09-09-c2-news-minutes-signal-design.md`

## Global Constraints

- **Always invoke Python as `.venv\Scripts\python.exe`** (bare `python` is an MSYS2 build with no pandas/numpy wheels). Tests: `.venv\Scripts\python.exe -m pytest tests/ -q` from repo root.
- **Some scripts need `PYTHONPATH=.`** — set `export PYTHONPATH=.` in the Bash tool (it runs bash, not PowerShell) before running any `scripts/` or `-m` module.
- **The live path (`model="m0_rules"`) must be provably unaffected.** A full `m0_rules` optimiser run before/after this change produces an identical squad. Enforced by a regression test and a manual eyeball.
- **`fpl/project/minutes.py` is a production-logic module with a history of subtle bugs** — every change ships a regression test that fails against the pre-fix code.
- **FPL `id` is NOT stable across seasons — `code` is.** The current-season `news`/`id` join is within one season, so no `identity.py` bridge is needed here; do not add cross-season joins.
- **Never commit a secret.** `ANTHROPIC_API_KEY` lives only in the process environment / a CI secret. It is never logged, never written to any artefact, never committed. `scripts/check_secrets.py` guards the tree.
- **`data/news/news_parsed.json` IS committed** (it is not reproducible without the LLM) — unlike everything else under `data/raw/` which is gitignored.
- **Do NOT edit `docs/DECISION_RULE.md`.** M6's promotion criteria go in a new `docs/M6_PREREGISTRATION.md`.
- **M6 is archived-only.** The Decide step still runs `m0_rules` exclusively; M6 never feeds a recommendation in this plan.
- **Model ID:** `claude-haiku-4-5` (exact string, no date suffix). Configurable via `config.news.model`.
- **Git:** work on a branch, never commit to `main`. Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- **Tests never hit the network.** `llm_client.parse_availability` is monkeypatched in every `news.py` / `minutes.py` / `scorecard` test.

---

## File Structure

**New files:**

| File | Responsibility |
|---|---|
| `fpl/collect/llm_client.py` | One function: `parse_availability(news_text, config) -> dict`. Anthropic call + strict-tool structured output + one prompt constant. Raises on failure. |
| `fpl/project/news.py` | `parse_news(players_df, config) -> (DataFrame, NewsHealth)`. Cache read/write, LLM-on-miss, degrade-on-failure. `NewsHealth` dataclass. `__main__` prints the health summary. |
| `fpl/evaluate/news_scorecard.py` | `compute_news_scorecard(bootstrap, config) -> dict`. Brier score M6 vs M0 `minutes_factor` vs actual `starts`, full + news-subset. `__main__ all`. |
| `data/news/news_parsed.json` | Committed cache: `{sha256(normalized_text): {parsed fields + provenance}}`. Created empty `{}` in Task 2. |
| `docs/M6_PREREGISTRATION.md` | Locked-before-results promotion criteria. |
| `tests/test_llm_client.py` | Request-payload shape, parse of a stubbed response. No network. |
| `tests/test_news.py` | Cache hit/miss, degrade-on-failure, normalization, prompt-version bump. |
| `tests/test_news_scorecard.py` | Brier math, skip-on-missing-partition. |

**Modified files:**

| File | Change |
|---|---|
| `fpl/project/minutes.py` | `compute_minutes_factor(players_df, config, model="m0_rules")` — new param; M6-only news branch above `chance_of_playing`. |
| `fpl/project/project.py` | `build_player_inputs` threads `model` into `compute_minutes_factor`; `"m6_news"` added to the model branch + `ValueError` allow-list. |
| `fpl/history/paths.py` | `MODELS` tuple gains `"m6_news"`. |
| `fpl/history/archive.py` | `_CHALLENGER_SUBDIRS` gains `"m6_news": "m6_news"`. |
| `config.yaml` | new `news:` block. |
| `.github/workflows/weekly.yml` | "Parse — team news" step; "Project — M6 challenger" step; "Evaluate — news scorecard" step; `data/news` in the commit `git add`. |
| `requirements.txt` | `anthropic` (pinned). |
| `tests/test_minutes.py` | M6 branch tests + M0-unchanged regression. |
| `tests/test_project.py` | `m6_news` registry test (if the file exists; else fold into `test_minutes.py`). |
| `docs/PROJECT_LOG.md` | §22. |
| `docs/HANDOFF.md` | header. |
| `CLAUDE.md` | model-registry table row for M6. |

**Already done (verify, don't re-add):** `fpl/transform/build_players.py::ELEMENT_COLUMNS` already contains `"news"` and `"news_added"` (line 43). Task 2 only needs to confirm `news` reaches `players_df` in the projection layer.

---

## Task 1: `llm_client.py` — the Anthropic wrapper

**Files:**
- Create: `fpl/collect/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `config` dict with `config["news"]["model"]` and `config["news"]["prompt_version"]`.
- Produces:
  - `PROMPT_VERSION: int` — module constant, currently `1`.
  - `AVAILABILITY_TOOL: dict` — the strict tool schema (exported for the test).
  - `parse_availability(news_text: str, config: dict) -> dict` — returns
    `{"start_prob": float, "status": str, "return_gw": int | None, "confidence": float, "reason": str}`.
    `status` is one of `"available" | "doubt" | "injured" | "suspended" | "unknown"`.
    Raises `anthropic.APIError` subclasses / `RuntimeError` on malformed output — the caller catches.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
"""
Tests for fpl/collect/llm_client.py — the Anthropic wrapper that parses one
FPL `news` string into a structured availability dict (C2 / PROJECT_LOG §22).

No network: the anthropic client is monkeypatched with a fake that records
the request and returns a canned tool-use response.
"""
from __future__ import annotations

import pytest

from fpl.collect import llm_client

CONFIG = {"news": {"model": "claude-haiku-4-5", "prompt_version": 1}}


class _FakeMessages:
    def __init__(self, tool_input):
        self._tool_input = tool_input
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs

        class _Block:
            type = "tool_use"
            name = "record_availability"
            input = self._tool_input

        class _Resp:
            content = [_Block()]
            stop_reason = "tool_use"

        return _Resp()


class _FakeClient:
    def __init__(self, tool_input):
        self.messages = _FakeMessages(tool_input)


def test_parse_availability_returns_the_tool_input_dict(monkeypatch):
    fake = _FakeClient({
        "start_prob": 0.75, "status": "doubt", "return_gw": None,
        "confidence": 0.9, "reason": "Explicit 75% chance quoted.",
    })
    monkeypatch.setattr(llm_client, "_client", lambda: fake)

    out = llm_client.parse_availability("Knock - 75% chance of playing", CONFIG)

    assert out["start_prob"] == 0.75
    assert out["status"] == "doubt"
    assert out["return_gw"] is None
    assert out["confidence"] == 0.9


def test_parse_availability_sends_a_strict_forced_tool_call(monkeypatch):
    fake = _FakeClient({
        "start_prob": 0.0, "status": "suspended", "return_gw": 5,
        "confidence": 0.95, "reason": "Suspended until GW5.",
    })
    monkeypatch.setattr(llm_client, "_client", lambda: fake)

    llm_client.parse_availability("Suspended until 12 Sep", CONFIG)

    kw = fake.messages.last_kwargs
    assert kw["model"] == "claude-haiku-4-5"
    assert kw["tool_choice"] == {"type": "tool", "name": "record_availability"}
    assert kw["tools"][0]["name"] == "record_availability"
    assert kw["tools"][0]["strict"] is True
    assert kw["tools"][0]["input_schema"]["additionalProperties"] is False
    # the news string is in the user message
    assert "Suspended until 12 Sep" in str(kw["messages"])


def test_parse_availability_raises_when_no_tool_block_returned(monkeypatch):
    class _NoToolClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                class _Resp:
                    content = []
                    stop_reason = "end_turn"
                return _Resp()

    monkeypatch.setattr(llm_client, "_client", lambda: _NoToolClient())

    with pytest.raises(RuntimeError, match="no record_availability"):
        llm_client.parse_availability("Knock", CONFIG)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_llm_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fpl.collect.llm_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# fpl/collect/llm_client.py
"""
llm_client.py — COLLECT layer. A thin wrapper over the Anthropic API that
turns ONE FPL `news` string into a structured availability dict, for
fpl/project/news.py (C2 / PROJECT_LOG §22).

Deliberately tiny: one function, one prompt, one strict tool. The whole
point is that `news.py` owns the cache and the degrade-on-failure logic,
and this module is the only place an API key is ever touched. The key is
read from the environment (ANTHROPIC_API_KEY) by the SDK — never passed
in, never logged, never written anywhere.

Model: config["news"]["model"] (default claude-haiku-4-5 — short-text
extraction, the cheapest capable model). No thinking (omitted → Haiku
runs without it). max_tokens is tiny; this is a classification.
"""
from __future__ import annotations

from functools import lru_cache

PROMPT_VERSION = 1

_SYSTEM = (
    "You convert a single Fantasy Premier League player-news string into a "
    "structured availability estimate for the NEXT gameweek. Be conservative: "
    "'knock', 'late test', 'illness' with no percentage means genuine doubt, "
    "not 'available'. A stated percentage is authoritative. 'Expected back "
    "for <opponent>' means unavailable until then. Suspensions are zero. "
    "If the string is empty or purely transfer/loan news with no fitness "
    "signal, return status 'unknown' with start_prob 0.5 and low confidence."
)

AVAILABILITY_TOOL = {
    "name": "record_availability",
    "description": "Record the structured availability estimate for this player.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["start_prob", "status", "return_gw", "confidence", "reason"],
        "properties": {
            "start_prob": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
                "description": "P(player is in the starting XI next gameweek).",
            },
            "status": {
                "type": "string",
                "enum": ["available", "doubt", "injured", "suspended", "unknown"],
            },
            "return_gw": {
                "type": ["integer", "null"],
                "description": "Absolute gameweek number the player is expected back, if the news states one; else null.",
            },
            "confidence": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
                "description": "Your confidence in this parse.",
            },
            "reason": {"type": "string", "description": "One short sentence."},
        },
    },
}


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def parse_availability(news_text: str, config: dict) -> dict:
    """Structured availability from one FPL `news` string. Raises on a
    network / auth / malformed-response failure — fpl/project/news.py
    catches and degrades. The SDK auto-retries 429/5xx (max_retries=2)."""
    model = config["news"]["model"]
    resp = _client().messages.create(
        model=model,
        max_tokens=400,
        system=_SYSTEM,
        tools=[AVAILABILITY_TOOL],
        tool_choice={"type": "tool", "name": "record_availability"},
        messages=[{"role": "user", "content": f"News: {news_text!r}"}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_availability":
            data = dict(block.input)
            data["start_prob"] = float(data["start_prob"])
            data["confidence"] = float(data["confidence"])
            data["return_gw"] = None if data.get("return_gw") is None else int(data["return_gw"])
            return data
    raise RuntimeError(f"llm_client: no record_availability tool block in response for {news_text!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_llm_client.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Add `anthropic` to requirements.txt**

Add a line to `requirements.txt` (keep it alphabetically near the top, pinned):

```
anthropic==0.69.0
```

Then install into the venv:

Run: `.venv/Scripts/python.exe -m pip install "anthropic==0.69.0"`
Expected: installs cleanly. (If a newer 0.x/1.x is current, pin that instead — check `.venv/Scripts/python.exe -m pip index versions anthropic` and use the latest stable; update the line to match.)

- [ ] **Step 6: Run the full suite to confirm nothing regressed**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — previous count + 3.

- [ ] **Step 7: Commit**

```bash
git add fpl/collect/llm_client.py tests/test_llm_client.py requirements.txt
git commit -m "feat(collect): llm_client — parse one FPL news string to structured availability

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `news.py` — `parse_news` + cache + `NewsHealth`

**Files:**
- Create: `fpl/project/news.py`, `data/news/news_parsed.json`
- Test: `tests/test_news.py`

**Interfaces:**
- Consumes: `llm_client.parse_availability(news_text, config)`, `llm_client.PROMPT_VERSION`.
- Produces:
  - `NEWS_CACHE_PATH: Path` — module constant, monkeypatched in tests.
  - `NewsHealth` dataclass: `distinct_strings: int`, `cache_hits: int`, `api_calls: int`, `parse_failures: int`, `players_with_signal: int`, `last_error: str | None`; `.to_dict()`.
  - `parse_news(players_df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, NewsHealth]` — the DataFrame has columns `id`, `news_start_prob` (float, NaN where no usable signal), `news_status` (str | None), `news_return_gw` (Int64, nullable), `news_confidence` (float, NaN).
  - `_normalize(text: str) -> str` and `_key(text: str) -> str` (sha256 hex of normalized).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_news.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fpl.project.news'`

- [ ] **Step 3: Write minimal implementation**

```python
# fpl/project/news.py
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
from dataclasses import dataclass, field
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

    news_col = players_df["news"] if "news" in players_df.columns else pd.Series("", index=players_df.index)
    distinct = sorted({s for s in news_col.fillna("").astype(str) if s.strip()})
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
        e = parsed.get((text or "").strip() and text)
        if not e or e["confidence"] < min_conf:
            return {"p": np.nan, "s": None, "g": pd.NA, "c": np.nan}
        return {"p": e["start_prob"], "s": e["status"],
                "g": pd.NA if e["return_gw"] is None else int(e["return_gw"]),
                "c": e["confidence"]}

    sig = news_col.fillna("").astype(str).map(_signal)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_news.py -q`
Expected: PASS (7 tests). Fix the `_signal` lookup if the empty-string case misbehaves — it must return the NaN dict for `""`/`None`.

- [ ] **Step 5: Create the committed empty cache file**

```bash
mkdir -p "data/news"
printf '{}\n' > "data/news/news_parsed.json"
```

- [ ] **Step 6: Run the full suite**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — previous + 7.

- [ ] **Step 7: Commit**

```bash
git add fpl/project/news.py tests/test_news.py data/news/news_parsed.json
git commit -m "feat(project): news.py — parse FPL news into a cached per-player start-prob signal

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `minutes.py` — M6 news branch + `model` param

**Files:**
- Modify: `fpl/project/minutes.py` — `compute_minutes_factor` signature + a new branch
- Test: `tests/test_minutes.py` — add 3 tests

**Interfaces:**
- Consumes: `news.parse_news(players_df, config)` (lazy import, M6 only).
- Produces: `compute_minutes_factor(players_df, config=None, model="m0_rules") -> DataFrame` — unchanged columns; behaviour identical to today for every `model` except `"m6_news"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_minutes.py`:

```python
def test_minutes_m6_uses_news_prob_over_chance_of_playing(tmp_path, monkeypatch):
    """model='m6_news': a parsed news signal supersedes chance_of_playing.
    Player 1 has chance_of_playing=25 but the news parse says 0.8 → 0.8."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", tmp_path / "news.json")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: {
        "start_prob": 0.8, "status": "doubt", "return_gw": None,
        "confidence": 0.9, "reason": "back in training",
    })

    players = _players([
        {"id": 1, "status": "a", "chance_of_playing_next_round": 25, "news": "Knock - back in training"},
        {"id": 2, "status": "a", "chance_of_playing_next_round": None, "news": ""},
    ])
    config = {"history": {"seasons": []}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02},
              "news": {"model": "m", "prompt_version": 1, "min_confidence": 0.5}}

    out = minutes_mod.compute_minutes_factor(players, config, model="m6_news")
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == pytest.approx(0.8)


def test_minutes_m0_is_byte_identical_with_news_present(tmp_path, monkeypatch):
    """model='m0_rules' (the default): news is NEVER consulted, even with a
    populated cache. Same input → same output as before this change."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod.llm_client, "parse_availability",
                        lambda t, c: (_ for _ in ()).throw(AssertionError("m0 must not touch news")))

    players = _players([
        {"id": 1, "status": "a", "chance_of_playing_next_round": 25, "news": "Knock"},
    ])
    config = {"history": {"seasons": []}, "minutes": {"backup_gk_factor": 0.02}}

    out = minutes_mod.compute_minutes_factor(players, config)  # default model
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == pytest.approx(0.25)


def test_minutes_m6_news_never_overrides_hard_unavailable(tmp_path, monkeypatch):
    """FPL status 'i' + a news parse of 0.9 → minutes_factor is still 0.0.
    The parse only moves a player WITHIN the available-but-doubtful space."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", tmp_path / "news.json")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: {
        "start_prob": 0.9, "status": "available", "return_gw": None,
        "confidence": 0.9, "reason": "rumour says fit",
    })

    players = _players([{"id": 1, "status": "i", "news": "Contradictory rumour"}])
    config = {"history": {"seasons": []}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02},
              "news": {"model": "m", "prompt_version": 1, "min_confidence": 0.5}}

    out = minutes_mod.compute_minutes_factor(players, config, model="m6_news")
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == 0.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_minutes.py -k "m6 or byte_identical" -q`
Expected: FAIL — `compute_minutes_factor() got an unexpected keyword argument 'model'`

- [ ] **Step 3: Implement**

In `fpl/project/minutes.py`, change the signature (line ~158) and add the branch. Current:

```python
def compute_minutes_factor(players_df: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    config = config or load_config()
    start_rates = compute_rolling_start_rate(players_df, config)
```

New:

```python
def compute_minutes_factor(
    players_df: pd.DataFrame, config: Optional[dict] = None, model: str = "m0_rules"
) -> pd.DataFrame:
    config = config or load_config()
    start_rates = compute_rolling_start_rate(players_df, config)

    # M6 (m6_news) ONLY: a parsed-news start probability. Lazily imported so
    # the live path (m0_rules) never even loads news.py. C2 / PROJECT_LOG §22.
    news_prob = None
    if model == "m6_news":
        from fpl.project import news as news_mod

        news_signal, _health = news_mod.parse_news(players_df, config)
        news_prob = players_df[["id"]].merge(
            news_signal[["id", "news_start_prob"]], on="id", how="left"
        )["news_start_prob"]
```

Then find the factor-assembly block (the `.where(...)` chain, ~lines 200-210):

```python
    factor = pd.Series(float("nan"), index=out.index)
    factor = factor.where(~unavailable, 0.0)
    factor = factor.where(unavailable | ~has_chance, chance / 100.0)
    factor = factor.where(~doubtful_no_chance, 0.5)
    fallback = ~unavailable & ~has_chance & ~doubtful_no_chance
    factor = factor.where(~fallback, out["rolling_start_rate"])
```

Insert the news branch **immediately after the `unavailable` line** so it sits above `chance`, and gate it on `news_prob`:

```python
    factor = pd.Series(float("nan"), index=out.index)
    factor = factor.where(~unavailable, 0.0)

    if news_prob is not None:
        news_prob = news_prob.reset_index(drop=True)
        has_news = news_prob.notna().to_numpy()
        # only where FPL hasn't already zeroed the player (hard unavailable)
        apply_news = has_news & (~unavailable).to_numpy()
        factor = factor.mask(pd.Series(apply_news, index=out.index), news_prob)

    factor = factor.where(unavailable | ~has_chance | factor.notna(), chance / 100.0)
    factor = factor.where(~doubtful_no_chance | factor.notna(), 0.5)
    fallback = ~unavailable & ~has_chance & ~doubtful_no_chance
    factor = factor.where(~fallback | factor.notna(), out["rolling_start_rate"])
```

> The `| factor.notna()` guard added to the three following `.where` calls stops them overwriting a news value that's already been set. For `model != "m6_news"`, `news_prob is None` → `factor` is all-NaN at that point → every `factor.notna()` is False → the chain is **identical** to before.

- [ ] **Step 4: Run to verify pass**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_minutes.py -q`
Expected: PASS (all — new 3 + existing unchanged)

- [ ] **Step 5: Manual regression eyeball — M0 squad unchanged**

```bash
export PYTHONPATH=.
.venv/Scripts/python.exe -m fpl.decide.optimiser 2>&1 | tail -25
```
Expected: identical XI / captain / cost to `data/output/gw4_recommendations.json` on `main`. If it differs, the branch leaked into `m0_rules` — stop and fix.

- [ ] **Step 6: Commit**

```bash
git add fpl/project/minutes.py tests/test_minutes.py
git commit -m "feat(project): minutes.py M6 news branch + model param — m0_rules unchanged

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `project.py` — register `m6_news`

**Files:**
- Modify: `fpl/project/project.py` — `build_player_inputs` (line ~143-191)
- Test: `tests/test_minutes.py` (add one) or `tests/test_project.py` if present

**Interfaces:**
- Consumes: `compute_minutes_factor(players, config, model)` from Task 3.
- Produces: `build_player_inputs(config, model="m6_news")` works; `project_gameweeks(n, config, model="m6_news")` writes `data/projections/m6_news/gw{n}.parquet`.

- [ ] **Step 1: Write the failing test**

```python
# in tests/test_minutes.py (or tests/test_project.py)
def test_build_player_inputs_m6_news_threads_model_to_minutes(monkeypatch, tmp_path):
    """build_player_inputs(model='m6_news') must pass model through to
    compute_minutes_factor — otherwise M6 == M0 and the whole feature is inert."""
    from fpl.project import project as project_mod

    seen = {}
    real = project_mod.minutes_mod.compute_minutes_factor
    def _spy(players, config, model="m0_rules"):
        seen["model"] = model
        return real(players, config, model=model)
    monkeypatch.setattr(project_mod.minutes_mod, "compute_minutes_factor", _spy)

    cfg = project_mod.load_config()
    try:
        project_mod.build_player_inputs(cfg, model="m6_news")
    except Exception:
        pass  # we only care that the model string reached minutes
    assert seen.get("model") == "m6_news"
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_minutes.py::test_build_player_inputs_m6_news_threads_model_to_minutes -q`
Expected: FAIL — `assert None == 'm6_news'` (model not threaded) or a `ValueError: Unknown model 'm6_news'`.

- [ ] **Step 3: Implement**

In `build_player_inputs` (project.py ~line 162), change:

```python
    mins = minutes_mod.compute_minutes_factor(players, config)
```
to:
```python
    mins = minutes_mod.compute_minutes_factor(players, config, model=model)
```

And the model branch (~line 183-189), add `m6_news` — it is M0 rates + news minutes, no xG/Understat blend:

```python
    if model == "m2_xg":
        out = xg_blend_mod.apply_xg_blend(out, config)
    elif model == "m3_understat":
        out = xg_blend_mod.apply_xg_blend(out, config)
        out = understat_blend_mod.apply_understat_blend(out, config)
    elif model == "m6_news":
        pass  # M0 rates + news-aware minutes_factor (already applied above)
    elif model != "m0_rules":
        raise ValueError(
            f"Unknown model {model!r} — expected 'm0_rules', 'm2_xg', 'm3_understat' or 'm6_news'"
        )
```

Update the `build_player_inputs` docstring's model list to mention `m6_news`.

- [ ] **Step 4: Run to verify pass**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_minutes.py -q`
Expected: PASS

- [ ] **Step 5: Manual — generate an M6 projection end to end**

```bash
export PYTHONPATH=.
# ANTHROPIC_API_KEY may be unset here — news.py degrades, M6 falls back to
# chance_of_playing for every string, still produces a valid parquet.
.venv/Scripts/python.exe -c "from fpl.project import project as p; p.project_gameweeks(5, p.load_config(), model='m6_news')"
ls data/projections/m6_news/
```
Expected: `data/projections/m6_news/gw4.parquet` written. (Delete it after — it's regenerated in CI; do not commit it here.)

```bash
rm -rf data/projections/m6_news
```

- [ ] **Step 6: Commit**

```bash
git add fpl/project/project.py tests/test_minutes.py
git commit -m "feat(project): register m6_news in the model registry

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: History archive — `m6_news` challenger partition

**Files:**
- Modify: `fpl/history/paths.py` (line 29), `fpl/history/archive.py` (line 39)
- Test: `tests/test_history_archive.py` if it exists; else `tests/test_history_paths.py`; else add a minimal one.

**Interfaces:**
- Consumes: `data/projections/m6_news/gw{n}.parquet` from Task 4.
- Produces: archive `discover_artefacts()` returns `(gw, "m6_news", path)` tuples; `paths.MODELS` includes `"m6_news"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history_archive.py  (append; check the file's existing helpers first)
def test_discover_artefacts_picks_up_m6_news_projections(tmp_path, monkeypatch):
    import fpl.history.archive as arch
    monkeypatch.setattr(arch, "PROJECTIONS_DIR", tmp_path / "projections")
    monkeypatch.setattr(arch, "OUTPUT_DIR", tmp_path / "output")
    (tmp_path / "output").mkdir(parents=True)
    m6 = tmp_path / "projections" / "m6_news"
    m6.mkdir(parents=True)
    import pandas as pd
    pd.DataFrame({"id": [1], "event": [4], "xpts": [3.0]}).to_parquet(m6 / "gw4.parquet")

    got = arch.discover_artefacts()
    assert (4, "m6_news", m6 / "gw4.parquet") in got["projections"]
```

Check `archive.py` for the real constant names (`PROJECTIONS_DIR`, `OUTPUT_DIR`) and the return-dict key (`"projections"`) before finalizing — adjust the test to match.

- [ ] **Step 2: Run to verify it fails**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_history_archive.py -k m6_news -q`
Expected: FAIL — `m6_news` not discovered.

- [ ] **Step 3: Implement**

`fpl/history/paths.py` line 29:
```python
MODELS = ("m0_rules", "m2_xg", "m3_understat", "m6_news")
```

`fpl/history/archive.py` line 39:
```python
_CHALLENGER_SUBDIRS = {"m2_xg": "m2_xg", "m3_understat": "m3_understat", "m6_news": "m6_news"}
```
Do **not** add `m6_news` to `_HEALTH_FILES` — M6 produces no `model_health.json` (that is `fpl.evaluate.backtest`, deliberately unscheduled). Confirm the health loop (archive.py ~line 111) iterates `_HEALTH_FILES` independently so a challenger absent from it is fine.

- [ ] **Step 4: Run to verify pass**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/ -q -k "history or archive"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fpl/history/paths.py fpl/history/archive.py tests/test_history_archive.py
git commit -m "feat(history): archive m6_news projections as a challenger partition

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: `news_scorecard.py` — M6 vs M0 Brier

**Files:**
- Create: `fpl/evaluate/news_scorecard.py`
- Test: `tests/test_news_scorecard.py`

**Interfaces:**
- Consumes: `data/actuals/actuals_<season>.csv` (`id`, `event`, `minutes`, `starts`); archived M0 + M6 `minutes_factor` — from `fpl.history.query.open_archive()` projections, OR recomputed. **Simplest correct source:** the archived projections carry `xpts` not `minutes_factor`; instead read `minutes_factor` from the per-model `data/projections/<model>/gw{n}.parquet` is also wrong (that's xpts too). → The scorecard recomputes `minutes_factor` for M0 and M6 for the target GW via `minutes.compute_minutes_factor(players, config, model=...)` on the **current** `players.parquet` snapshot, and scores against that GW's actual `starts`. Document this: it is a same-inputs comparison, not a point-in-time one; good enough for a relative Brier.
- Produces: `compute_news_scorecard(bootstrap, config=None) -> dict` written to `data/output/news_scorecard.json`:
  ```json
  {"per_gw": [{"event": 3, "brier_m0_full": 0.09, "brier_m6_full": 0.09,
               "brier_m0_newsonly": 0.21, "brier_m6_newsonly": 0.14,
               "n_news": 12}],
   "window": {"brier_m0_newsonly": ..., "brier_m6_newsonly": ..., "n_news": ...}}
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news_scorecard.py
"""
Tests for fpl/evaluate/news_scorecard.py — relative Brier score of M6 vs M0
minutes_factor predictions against the actual `starts` flag (C2 / §22).
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.evaluate import news_scorecard as sc


def test_brier_score_is_mean_squared_error_vs_actual_start():
    pred = pd.Series([0.9, 0.1, 0.5])
    actual = pd.Series([1, 0, 1])
    # (0.1^2 + 0.1^2 + 0.5^2) / 3 = (0.01 + 0.01 + 0.25)/3
    assert sc.brier(pred, actual) == pytest.approx((0.01 + 0.01 + 0.25) / 3)


def test_compute_news_scorecard_skips_a_gw_without_actuals(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "ACTUALS_DIR", tmp_path)
    monkeypatch.setattr(sc, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sc, "load_config", lambda: {"season": "test"})
    # no actuals file at all
    bootstrap = {"events": [{"id": 3, "finished": True, "data_checked": True}]}
    out = sc.compute_news_scorecard(bootstrap)
    assert out["per_gw"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_news_scorecard.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# fpl/evaluate/news_scorecard.py
"""
news_scorecard.py — EVALUATE layer. Relative Brier score of M6 (m6_news)
vs M0 (m0_rules) minutes_factor predictions against the actual `starts`
flag, for every settled gameweek (C2 / PROJECT_LOG §22).

The number that matters is `brier_*_newsonly` — the subset of players who
had a parsed news signal that week, where M6 and M0 actually differ. On
the full population M6 ≈ M0 by construction.

This recomputes minutes_factor for both models on the CURRENT
players.parquet snapshot and scores it against that GW's actuals — a
same-inputs comparison, not a point-in-time one. Good enough for a
relative signal; feeds docs/M6_PREREGISTRATION.md's promotion check.

`python -m fpl.evaluate.news_scorecard all` drives it from the pipeline.
Never raises on a missing input — skips the GW.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTUALS_DIR = ROOT / "data" / "actuals"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "data" / "output"


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def brier(pred: pd.Series, actual: pd.Series) -> float:
    p = pd.to_numeric(pred, errors="coerce").to_numpy(dtype=float)
    a = pd.to_numeric(actual, errors="coerce").to_numpy(dtype=float)
    mask = ~np.isnan(p) & ~np.isnan(a)
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean((p[mask] - a[mask]) ** 2))


def _settled_gws(bootstrap: dict) -> list[int]:
    return [e["id"] for e in bootstrap.get("events", [])
            if e.get("finished") and e.get("data_checked")]


def compute_news_scorecard(bootstrap: dict, config: Optional[dict] = None) -> dict:
    config = config or load_config()
    season = config["season"]
    actuals_path = ACTUALS_DIR / f"actuals_{season}.csv"
    players_path = PROCESSED_DIR / "players.parquet"

    per_gw: list[dict] = []
    if not actuals_path.exists() or not players_path.exists():
        result = {"per_gw": [], "window": {}}
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "news_scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    from fpl.project import minutes as minutes_mod

    actuals = pd.read_csv(actuals_path)
    players = pd.read_parquet(players_path)

    if "starts" not in actuals.columns:
        result = {"per_gw": [], "window": {}}
        (OUTPUT_DIR / "news_scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    m0 = minutes_mod.compute_minutes_factor(players, config, model="m0_rules")[["id", "minutes_factor"]]
    try:
        m6 = minutes_mod.compute_minutes_factor(players, config, model="m6_news")[["id", "minutes_factor"]]
    except Exception:
        m6 = m0.copy()
    from fpl.project import news as news_mod
    news_sig, _ = news_mod.parse_news(players, config)
    news_ids = set(news_sig.loc[news_sig["news_start_prob"].notna(), "id"])

    for gw in _settled_gws(bootstrap):
        a = actuals[actuals["event"] == gw]
        if a.empty:
            continue
        a = a[a["minutes"].fillna(0) >= 0]  # every graded player
        starts = a.set_index("id")["starts"]
        j0 = m0.set_index("id")["minutes_factor"].reindex(starts.index)
        j6 = m6.set_index("id")["minutes_factor"].reindex(starts.index)
        news_mask = starts.index.isin(news_ids)
        per_gw.append({
            "event": int(gw),
            "brier_m0_full": brier(j0, starts),
            "brier_m6_full": brier(j6, starts),
            "brier_m0_newsonly": brier(j0[news_mask], starts[news_mask]),
            "brier_m6_newsonly": brier(j6[news_mask], starts[news_mask]),
            "n_news": int(news_mask.sum()),
        })

    def _agg(key):
        vals = [(g[key], g["n_news"]) for g in per_gw if not np.isnan(g[key])]
        n = sum(w for _, w in vals)
        return float(sum(v * w for v, w in vals) / n) if n else float("nan")

    window = {
        "brier_m0_newsonly": _agg("brier_m0_newsonly"),
        "brier_m6_newsonly": _agg("brier_m6_newsonly"),
        "n_news": sum(g["n_news"] for g in per_gw),
    }
    result = {"per_gw": per_gw, "window": window}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "news_scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "all":
        raw = ROOT / "data" / "raw" / "bootstrap_static.json"
        r = compute_news_scorecard(json.loads(raw.read_text(encoding="utf-8")))
        print(f"[news_scorecard] {r['window']}")
    else:
        print("usage: python -m fpl.evaluate.news_scorecard all")
```

- [ ] **Step 4: Run to verify pass**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/test_news_scorecard.py -q`
Expected: PASS

- [ ] **Step 5: Full suite**

Run: `export PYTHONPATH=. && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add fpl/evaluate/news_scorecard.py tests/test_news_scorecard.py
git commit -m "feat(evaluate): news_scorecard — relative Brier of M6 vs M0 minutes vs actual starts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: config, workflow, pre-registration

**Files:**
- Modify: `config.yaml` (after the `minutes:` block, ~line 190)
- Modify: `.github/workflows/weekly.yml`
- Create: `docs/M6_PREREGISTRATION.md`

**Interfaces:** none new — wiring only.

- [ ] **Step 1: Add the `news:` block to `config.yaml`**

After the `minutes:` block (before `data:`), add:

```yaml
news:
  # C2 / PROJECT_LOG §22 — LLM parse of FPL's free-text `news` field into a
  # per-player start probability, consumed ONLY by challenger model m6_news.
  enabled: true
  model: "claude-haiku-4-5"   # exact string, no date suffix — short-text extraction
  prompt_version: 1           # bump when llm_client._SYSTEM / AVAILABILITY_TOOL changes
  min_confidence: 0.5         # parses below this are discarded → fall back to chance_of_playing
  # The LLM is hit only on a cache miss (data/news/news_parsed.json). With
  # no ANTHROPIC_API_KEY set, misses just fall back — deterministic, offline.
```

- [ ] **Step 2: Add the pipeline steps to `weekly.yml`**

After the "Collect — availability snapshot" / staleness steps and **before** "Transform", add:

```yaml
      # PROJECT_LOG §22: parse FPL's free-text `news` field into a structured
      # start-probability, cached by a hash of the text (data/news/). Consumed
      # only by challenger model m6_news. Degrades to cache-only if the secret
      # is unset — must not fail the job.
      - name: Parse — team news (model M6 input)
        run: python -m fpl.project.news
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

After the "Decide" step and **before** "Archive this run", add:

```yaml
      # PROJECT_LOG §22: the M6 challenger projection (M0 rules + news-aware
      # minutes). Archived-only — never a decision input. Parallels how M2/M3
      # would be produced; only M6 is wired for now.
      - name: Project — M6 challenger (m6_news)
        run: python -c "from fpl.project import project as p; p.project_gameweeks(p.load_config()['horizon']['gameweeks'], p.load_config(), model='m6_news')"
        env:
          PYTHONPATH: "."
```

After the hindsight step (added in PROJECT_LOG §21) and before the dashboard steps, add:

```yaml
      - name: Evaluate — M6 vs M0 news scorecard
        run: python -m fpl.evaluate.news_scorecard all
        env:
          PYTHONPATH: "."
```

In the "Commit + push regenerated artefacts" step, add `data/news` and `data/projections/m6_news` coverage — the existing `git add` already lists `data/projections` (which now includes the `m6_news/` subdir) and `data/output`; **add `data/news`**:

```
          git add data/processed data/projections data/output data/snapshots data/history data/actuals data/state data/news dashboard/data.json dashboard/history.json dashboard/index.html dashboard/weeks
```

- [ ] **Step 3: Write `docs/M6_PREREGISTRATION.md`**

```markdown
# M6 (`m6_news`) — pre-registered promotion criteria

**Locked:** 2026-09-09, before M6 produced a single projection.
**Spec:** `docs/superpowers/specs/2026-09-09-c2-news-minutes-signal-design.md`
**Why this is not in `docs/DECISION_RULE.md`:** `CLAUDE.md` forbids editing
that file outside drafting fixes. This is a separate, additive
pre-registration for one challenger. Whether it graduates into
`DECISION_RULE.md` is the owner's call, made after reading this.

## The rule

Let **W** = the first full gameweek whose deadline falls after the C2
implementation merges to `main`.

**M6's news-aware minutes path is folded into M0** (as a data-quality
improvement, the way the §17 / §20 rolling-start-rate fixes were) **iff,
evaluated once at GW W+6, ALL THREE hold:**

1. **Minutes accuracy.** Over GW W … W+5, on the *news-signal subset*
   (players with a parsed signal that week — where M6 and M0 differ),
   M6's `minutes_factor` has a **strictly lower** Brier score against the
   actual `starts` flag than M0's. Source: `data/output/news_scorecard.json`
   `window.brier_m6_newsonly < window.brier_m0_newsonly`.

2. **No decision-quality regression.** Captaincy + squad hindsight regret
   (`fpl.evaluate.hindsight`, summed GW W … W+5) under an M6-fed
   optimiser is **no worse** than under M0. (Requires a one-off M6
   optimiser run at evaluation time — not scheduled before then.)

3. **The parser works.** `NewsHealth.parse_failures / distinct_strings
   < 0.2` averaged over the window (from the per-run logs).

If any of the three fails, M6 stays **archived-only** or is dropped. The
outcome — either way — is logged in `docs/PROJECT_LOG.md`.

## What is NOT allowed

- No informal promotion before GW W+6, however good an interim number
  looks. An unexplained interim win gets investigated, not shipped
  (condition 5 of the existing decision rule).
- No change to M0's live path, the MILP, the captain logic, or the
  transfer solver before GW W+6.
- No re-tuning of this rule after results start arriving. If it is
  genuinely mis-drafted, the fix is logged as a diff here with its
  rationale, same discipline as `DECISION_RULE.md`.
```

- [ ] **Step 4: Validate the workflow YAML**

Run: `.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/weekly.yml', encoding='utf-8')); print('weekly.yml OK')"`
Expected: `weekly.yml OK`

- [ ] **Step 5: Commit**

```bash
git add config.yaml .github/workflows/weekly.yml docs/M6_PREREGISTRATION.md
git commit -m "feat(pipeline): wire M6 news parse + challenger projection + scorecard into weekly.yml

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Docs + backfill the cache

**Files:**
- Modify: `docs/PROJECT_LOG.md` (§22), `docs/HANDOFF.md` (header), `CLAUDE.md` (model registry table)
- Modify: `data/news/news_parsed.json` (backfill — local, with a key present)

**Interfaces:** none.

- [ ] **Step 1: `docs/PROJECT_LOG.md` — append §22**

Add after §21:

```markdown
## 22. News → minutes signal, challenger model M6 (2026-09-09)

### The gap

`minutes.py` gated start probability on FPL's numeric
`chance_of_playing_next_round` — null for most players, and one of
0/25/50/75/100 when present, with no return date. FPL's own free-text
`news` string ("Knock - 75% chance", "Suspended until 12 Sep", "Expected
back for the Arsenal game") is richer and unread.

### What changed (C2 — brainstormed + spec'd 2026-09-09)

- **`fpl/collect/llm_client.py`** (new) — one function, `parse_availability`,
  turns one `news` string into `{start_prob, status, return_gw,
  confidence, reason}` via a strict forced tool call (model
  `claude-haiku-4-5`). The only place `ANTHROPIC_API_KEY` is touched.
- **`fpl/project/news.py`** (new) — `parse_news` walks the bootstrap,
  hits `data/news/news_parsed.json` (keyed by `sha256(normalize(text))`),
  calls the LLM only on a miss, writes the result back. Committed cache →
  re-runs make zero API calls, projections are byte-identical. A per-string
  failure degrades (that player falls through to `chance_of_playing`);
  `parse_news` never raises. `min_confidence` (0.5) discards weak parses.
- **`fpl/project/minutes.py`** — `compute_minutes_factor` gains a `model`
  param; for `model == "m6_news"` a parsed `start_prob` supersedes the
  `chance_of_playing` branch. It never overrides FPL's hard `i/s/u` zero.
  **`m0_rules` is byte-identical** — `news.py` isn't even imported.
- **`fpl/project/project.py`** — `m6_news` in the model registry (M0 rates
  + news minutes, no xG/Understat blend).
- **`fpl/history/`** — `m6_news` is a challenger partition (`paths.MODELS`,
  `archive._CHALLENGER_SUBDIRS`). No `model_health` — that's the backtest.
- **`fpl/evaluate/news_scorecard.py`** (new) — relative Brier of M6 vs M0
  `minutes_factor` vs actual `starts`, full + news-subset, to
  `data/output/news_scorecard.json`.
- **`weekly.yml`** — parse step (before Transform), M6 projection step
  (before Archive), scorecard step (after hindsight). `data/news` committed.
- **`config.yaml`** — `news:` block.
- **`docs/M6_PREREGISTRATION.md`** (new) — promotion criteria, locked
  before any M6 result. NOT a `DECISION_RULE.md` edit.
- Tests: `test_llm_client` (3), `test_news` (7), `test_news_scorecard` (2),
  `test_minutes` (+4). No test hits the network.

### Rollout

M6 is **archived-only** — the Decide step still runs `m0_rules`. After 6
gameweeks, evaluate against `M6_PREREGISTRATION.md`. Fold in or drop; log
the outcome here.
```

- [ ] **Step 2: `docs/HANDOFF.md` — new header entry**

Insert at the top (above the current latest "Status as of" block):

```markdown
**Status as of 2026-09-09, latest (C2 — news → minutes, challenger M6):**
FPL's free-text `news` field is now parsed by an LLM
(`fpl/collect/llm_client.py`) into a per-player start probability,
hash-cached in `data/news/news_parsed.json` (committed; zero API calls on
re-run). Consumed only by new challenger model **`m6_news`** —
`compute_minutes_factor(..., model="m6_news")` uses it in place of the
`chance_of_playing` branch. **M0 is byte-identical** (news.py not
imported). M6 archived every run alongside M2/M3; `news_scorecard.json`
tracks M6-vs-M0 Brier vs actual `starts`. Promotion criteria locked in
`docs/M6_PREREGISTRATION.md` (evaluate at GW W+6). Needs the
`ANTHROPIC_API_KEY` repo secret; degrades to cache-only without it.
**N tests passing.** Detail: `docs/PROJECT_LOG.md` §22. Branch
`feat/c2-news-minutes-signal` (PR pending).
```

(Replace `N` with the real count after the suite runs.)

- [ ] **Step 3: `CLAUDE.md` — model registry table**

Add a row to the model table (after M4 or in M-number order):

```markdown
| M6 `m6_news` | M0 rules + LLM-parsed FPL `news` → start probability (replaces the `chance_of_playing` branch) | **Challenger, archived-only** — pre-registered promotion at GW W+6 (`docs/M6_PREREGISTRATION.md`); M0's live path is byte-identical |
```

- [ ] **Step 4: Backfill the cache (local, needs the key)**

If `ANTHROPIC_API_KEY` is available locally:

```bash
export PYTHONPATH=.
.venv/Scripts/python.exe -m fpl.project.news
```
This parses the current bootstrap's news strings and populates
`data/news/news_parsed.json`. Review the file — spot-check a few entries
read sensibly. If the key is **not** available locally, leave the cache
as `{}`; the first CI run with the secret set will populate it.

- [ ] **Step 5: Full suite + M0 regression eyeball**

```bash
export PYTHONPATH=.
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m fpl.decide.optimiser 2>&1 | tail -20
```
Expected: all green; optimiser squad identical to `main`.

- [ ] **Step 6: Commit**

```bash
git add docs/PROJECT_LOG.md docs/HANDOFF.md CLAUDE.md data/news/news_parsed.json
git commit -m "docs: PROJECT_LOG §22 + HANDOFF + CLAUDE model registry for M6

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Push + PR**

```bash
git push -u origin feat/c2-news-minutes-signal
```
Open a PR titled `feat: C2 — news → minutes signal (challenger model M6)` with the spec + PROJECT_LOG §22 summary, and the note: **M0 live path byte-identical; M6 archived-only; needs the `ANTHROPIC_API_KEY` repo secret.** Body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| §4.1 `llm_client.py` | Task 1 |
| §4.2 `data/news/news_parsed.json` | Task 2 (created), Task 8 (backfill) |
| §4.3 `news.py` + `NewsHealth` | Task 2 |
| §4.4 `build_players` keeps `news` | Already done — verified in Task 2 |
| §4.5 `minutes.py` M6 branch | Task 3 |
| §4.6 `project.py` registry | Task 4 |
| §4.7 archive + `weekly.yml` model list | Task 5 (archive), Task 7 (weekly.yml projection step) |
| §4.8 `news_scorecard.py` | Task 6 |
| §4.9 `config.yaml` | Task 7 |
| §4.10 `weekly.yml` steps | Task 7 |
| §5 pre-registration | Task 7 (`M6_PREREGISTRATION.md`) |
| §6 testing | every task's TDD steps |
| §7 rollout | Task 8 §1 (documented), `M6_PREREGISTRATION.md` |
| §8 risks (min_confidence, key handling, determinism) | Task 2 (min_confidence, degrade), Task 1 (key isolation) |
| §10 files touched | all tasks; `requirements.txt` in Task 1 |

Gap check: the spec's `news_return_gw` / `news_status` columns are produced by `parse_news` (Task 2) but not yet *consumed* anywhere — that's intentional (spec §9 defers rotation logic; return_gw is provenance for now). No task needed.

**2. Placeholder scan:** No "TBD"/"handle errors appropriately" — every code step has real code. The `news_scorecard` point-in-time caveat is stated explicitly in the module docstring and Task 6 interfaces, not hand-waved.

**3. Type consistency:** `parse_availability` returns `{start_prob, status, return_gw, confidence, reason}` — same keys in Task 1 (definition), Task 2 (`news.py` consumes), Task 3 + Task 6 (test stubs). `parse_news` returns `(DataFrame[id, news_start_prob, news_status, news_return_gw, news_confidence], NewsHealth)` — consistent in Tasks 2, 3, 6. `compute_minutes_factor(players_df, config, model)` — Task 3 defines, Task 4 calls with `model=model`, Task 6 calls with `model="m0_rules"`/`"m6_news"`. `_CHALLENGER_SUBDIRS` / `MODELS` gain `"m6_news"` consistently (Task 5).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-09-c2-news-minutes-signal.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
