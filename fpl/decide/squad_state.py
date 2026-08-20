"""
squad_state.py — DECIDE layer. FPL_V2_DESIGN.md spec §3.2: the missing
prerequisite for regret (spec §3.4) — "the 15 you owned that week."

For GW1 that's just the recommendations file. From GW2 it isn't, because
transfers change the squad and fpl/decide/transfers.py doesn't exist yet
(plan §9 / spec §6 — out of scope here too). There is no persistent team
state anywhere else in this repo, so this has to start being written from
GW1 even though nothing downstream can use it yet, or every regret metric
from GW2 onward is uncomputable retroactively (spec §3.2's own framing).

`recommended` and `played` are kept separate (decision D6): they will
diverge (a manager overrides the model, and should) — grading `recommended`
answers "is the model good", grading `played` answers "am I good". `played`
starts null; fpl.evaluate.hindsight falls back to `recommended` when it is.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "state"


def _squad_from_result(result: dict) -> dict:
    return {
        "squad": [int(i) for i in result["squad"]],
        "starting_xi": [int(i) for i in result["starting_xi"]],
        "captain": int(result["captain"]),
        "vice_captain": int(result["vice_captain"]),
    }


def write_squad_state(
    gw: int,
    result: dict,
    bank: float,
    free_transfers: int = 1,
    transfers_made: Optional[list[dict]] = None,
    chips_used: Optional[list[str]] = None,
    chip_active: Optional[str] = None,
) -> Path:
    """
    Writes data/state/squad_gw{gw}.json. Called from optimiser.build_gw1_squad
    for GW1 (bank = leftover budget, free_transfers = 1 — the amount you
    start GW2 with, since there's no transfer to make INTO the initial
    squad). transfers.py, once built, calls this for every subsequent GW
    with the real bank/FT/transfers_made state.

    Never overwrites a `played` value that's already been set for this GW —
    only the `recommended` half and the bookkeeping fields (bank, FT,
    transfers, chips) are ever written here; setting `played` is a manual/
    separate step (spec §3.2: what you actually did, which may diverge from
    what was recommended).
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"squad_gw{gw}.json"

    played = None
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        played = existing.get("played")

    state = {
        "gameweek": int(gw),
        "recommended": _squad_from_result(result),
        "played": played,
        "bank": round(float(bank), 1),
        "free_transfers": int(free_transfers),
        "transfers_made": transfers_made or [],
        "chips_used": chips_used or [],
        "chip_active": chip_active,
    }
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_squad_state(gw: int) -> dict:
    path = STATE_DIR / f"squad_gw{gw}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — no squad state recorded for GW{gw}.")
    return json.loads(path.read_text(encoding="utf-8"))


def set_played(gw: int, squad: list[int], starting_xi: list[int], captain: int, vice_captain: int) -> Path:
    """Records what was ACTUALLY played for a gameweek — a manual step
    (the manager overrode the model, or simply hasn't diverged and this is
    identical to `recommended`). Never called automatically."""
    state = load_squad_state(gw)
    state["played"] = {
        "squad": [int(i) for i in squad],
        "starting_xi": [int(i) for i in starting_xi],
        "captain": int(captain),
        "vice_captain": int(vice_captain),
    }
    path = STATE_DIR / f"squad_gw{gw}.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
