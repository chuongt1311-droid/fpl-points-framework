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
