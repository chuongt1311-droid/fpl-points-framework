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


# ---------------------------------------------------------------------------
# H3a — real-team ingestion (spec §5)
#
# Two sources, deliberately:
#   * public entry/{id}/event/{gw}/picks/ -- squad composition, bank, chip.
#   * a user-pasted my-team JSON -- sell prices and free transfers, which
#     are NOT public.
#
# NO CREDENTIALS ANYWHERE. The pasted file is saved by the user from their
# own logged-in browser; this code only ever reads a local gitignored path.
# ---------------------------------------------------------------------------

import time

PRIVATE_DIR = Path(__file__).resolve().parents[2] / "data" / "private"
MY_TEAM_PATH = PRIVATE_DIR / "my_team.json"

FPL_API = "https://fantasy.premierleague.com/api"


class StaleMyTeamError(Exception):
    """The pasted my-team file is too old to be trusted for a live solve."""


class SquadMismatchError(Exception):
    """Pasted file and public endpoint disagree about the 15 - stop."""


def _tenths(v) -> float:
    return round(float(v) / 10.0, 1)


def parse_entry_picks(picks_json: dict) -> dict:
    """Public endpoint -> squad composition, armband, bank, chip."""
    picks = picks_json["picks"]
    hist = picks_json.get("entry_history", {})
    return {
        "squad": [int(p["element"]) for p in picks],
        "starting_xi": [int(p["element"]) for p in picks if int(p["position"]) <= 11],
        "captain": next(int(p["element"]) for p in picks if p.get("is_captain")),
        "vice_captain": next(int(p["element"]) for p in picks if p.get("is_vice_captain")),
        "bank": _tenths(hist.get("bank", 0)),
        "value": _tenths(hist.get("value", 0)),
        "active_chip": picks_json.get("active_chip"),
        "event": hist.get("event"),
    }


def parse_my_team(my_team_json: dict) -> dict:
    """
    Pasted authenticated payload -> sell prices + free transfers.

    selling_price is READ, not recomputed. FPL returns half the rise
    rounded down to 0.1; reimplementing that rule would add error surface
    to exactly the field the plan flags as this phase's highest risk, and
    the endpoint already gives the answer.
    """
    picks = my_team_json["picks"]
    transfers = my_team_json.get("transfers", {})
    return {
        "squad": [int(p["element"]) for p in picks],
        "sell_prices": {int(p["element"]): _tenths(p["selling_price"]) for p in picks},
        "purchase_prices": {
            int(p["element"]): _tenths(p.get("purchase_price", p["selling_price"]))
            for p in picks
        },
        "bank": _tenths(transfers.get("bank", 0)),
        "free_transfers": int(transfers.get("limit") or 0),
    }


def load_my_team_file(path: Optional[Path] = None,
                      max_age_hours: float = 24.0) -> tuple[dict, float]:
    """
    Read + parse the pasted file, returning (parsed, age_hours).

    A stale file is worse than a missing one -- it yields a confident,
    wrong, unactionable recommendation -- so age is enforced, not logged.
    """
    p = Path(path) if path is not None else MY_TEAM_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"No my-team file at {p}. Save the JSON from "
            f"{FPL_API}/my-team/<entry_id>/ while logged in to "
            f"fantasy.premierleague.com, then re-run. "
            f"Run scripts/my_team_instructions.py for the exact steps."
        )
    age_hours = (time.time() - p.stat().st_mtime) / 3600.0
    if age_hours > max_age_hours:
        raise StaleMyTeamError(
            f"{p} is {age_hours:.1f}h old (limit {max_age_hours:.0f}h). Sell prices "
            f"and free transfers drift; re-save it before solving."
        )
    return parse_my_team(json.loads(p.read_text(encoding="utf-8"))), age_hours


def reconcile(public: dict, pasted: dict) -> dict:
    """
    Cross-check the two sources and merge into one squad state.

    Squad disagreement is FATAL: it means the pasted file is stale or
    belongs to a different entry, and every downstream number would be
    wrong. Bank disagreement is NOT fatal -- the public value is the bank
    at the last deadline, so a difference legitimately means a transfer
    has already been made this week; the live (pasted) value wins and the
    difference is surfaced.
    """
    if sorted(public["squad"]) != sorted(pasted["squad"]):
        only_public = sorted(set(public["squad"]) - set(pasted["squad"]))
        only_pasted = sorted(set(pasted["squad"]) - set(public["squad"]))
        raise SquadMismatchError(
            "Pasted my-team file and the public picks endpoint disagree about the "
            f"squad - the file is stale or for a different entry. Only in public: "
            f"{only_public}; only in file: {only_pasted}. Re-save the file and retry."
        )
    return {
        "squad": public["squad"],
        "starting_xi": public["starting_xi"],
        "captain": public["captain"],
        "vice_captain": public["vice_captain"],
        "active_chip": public["active_chip"],
        "event": public["event"],
        "value": public["value"],
        "bank": pasted["bank"],
        "bank_mismatch": public["bank"] != pasted["bank"],
        "public_bank": public["bank"],
        "sell_prices": pasted["sell_prices"],
        "free_transfers": pasted["free_transfers"],
    }


def fetch_entry_picks(entry_id: int, gw: int, config: Optional[dict] = None) -> dict:
    """Public, unauthenticated. Not unit-tested (network)."""
    import urllib.request

    url = f"{FPL_API}/entry/{entry_id}/event/{gw}/picks/"
    req = urllib.request.Request(url, headers={
        "User-Agent": "fpl-points-framework/1.0 (personal research tool)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))
