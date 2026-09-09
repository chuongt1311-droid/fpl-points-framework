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
