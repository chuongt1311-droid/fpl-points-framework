# Phase H (H3a + H3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tool answer "I own these 15, I have 1 free transfer and £X in the bank — what do I do this week?" with a named transfer or an explicit roll, hits endogenous.

**Architecture:** Extract `optimise_squad`'s inline MILP constraints into shared builders (`fpl/decide/constraints.py`), behind a byte-identical-output gate. Add real-team ingestion to `squad_state.py` (public API for composition/bank, a user-pasted `my-team` file for sell prices and free transfers). Add `fpl/decide/transfers.py`: a pure MILP over `pool ∪ current_squad` with `transfer_in`/`transfer_out` variables, solved on the 5-GW weighted horizon with the hit charged once, then XI re-picked on next-GW xPts via the existing `pick_xi_and_captain`.

**Tech Stack:** Python 3.12, pandas, PuLP/CBC, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-transfer-decision-layer-design.md`

## Global Constraints

- **Always use `.venv\Scripts\python.exe` explicitly**, never bare `python`.
- Tests: `.venv\Scripts\python.exe -m pytest tests/ -q` from repo root. All 162 existing tests must keep passing.
- FPL entry id is **6669718** (public, goes in `config.yaml` as `fpl.entry_id`).
- **Never handle FPL credentials.** The `my-team` JSON is saved by the user; the code only reads a local gitignored file.
- Sell price and free transfers are **read** from that file (`selling_price`, `transfers.limit`), never recomputed.
- Prices in `players.parquet` are real £m (already `/10` from FPL tenths). The `my-team` file gives tenths — convert on read.
- `hit_cost` comes from `config["transfers"]["hit_cost"]` (= 4). Do **not** read `free_per_gw`, `max_bank`, or `buffer`.
- Objective horizon is `weighted_xpts`; XI/captain are re-picked on `next_gw_xpts`. Both gains are always reported.
- Tests are pure: `tmp_path` + `monkeypatch` on module-level constants, no network.

---

### Task 1: Extract shared MILP constraint builders

**Files:**
- Create: `fpl/decide/constraints.py`
- Modify: `fpl/decide/optimiser.py:242-278` (the inline constraint block)
- Test: `tests/test_decide_constraints.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `add_squad_composition(prob, squad, ids, position, rules)` — size + per-position counts
  - `add_club_limits(prob, squad, ids, team, rules)`
  - `add_budget(prob, squad, ids, price, budget_limit)`
  - `add_xi_shape(prob, squad, start, ids, position, rules, force_formation=None)` — XI size, GK count, formation bounds, `start[i] <= squad[i]`
  - `add_captain_rules(prob, start, captain, ids)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_decide_constraints.py`:

```python
"""
Tests for fpl/decide/constraints.py — the shared MILP constraint builders
extracted from optimise_squad so fpl/decide/transfers.py can reuse them
rather than fork them (spec §4).

The v4 plan said "reuse optimise_squad's constraint builders"; those
builders did not exist — the constraints were inline. These are them.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pulp

from fpl.decide import constraints

RULES = {
    "total": 3, "gk": 1, "def": 1, "mid": 1, "fwd": 0,
    "max_per_club": 2, "budget_tenths": 200,
    "starting_xi": {"total": 2, "gk": 1, "min_def": 1, "min_mid": 0, "min_fwd": 0},
}
IDS = [1, 2, 3, 4]
POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "DEF"}
TEAM = {1: 10, 2: 10, 3: 11, 4: 10}
PRICE = {1: 4.0, 2: 4.0, 3: 4.0, 4: 9.0}


def _vars(name):
    return pulp.LpVariable.dicts(name, IDS, cat="Binary")


def test_squad_composition_enforces_size_and_positions():
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad = _vars("squad")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, RULES)
    prob += pulp.lpSum(squad[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    chosen = [i for i in IDS if squad[i].value() == 1]
    assert len(chosen) == 3
    assert sum(1 for i in chosen if POSITION[i] == "GK") == 1
    assert sum(1 for i in chosen if POSITION[i] == "DEF") == 1
    assert sum(1 for i in chosen if POSITION[i] == "MID") == 1


def test_club_limits_cap_players_per_team():
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad = _vars("squad")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, RULES)
    constraints.add_club_limits(prob, squad, IDS, TEAM, RULES)
    prob += pulp.lpSum(squad[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    chosen = [i for i in IDS if squad[i].value() == 1]
    assert sum(1 for i in chosen if TEAM[i] == 10) <= 2


def test_budget_constraint_blocks_the_expensive_player():
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad = _vars("squad")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, RULES)
    constraints.add_budget(prob, squad, IDS, PRICE, 12.0)
    prob += pulp.lpSum(squad[i] * PRICE[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    chosen = [i for i in IDS if squad[i].value() == 1]
    assert sum(PRICE[i] for i in chosen) <= 12.0
    assert 4 not in chosen  # the £9.0m DEF cannot fit


def test_xi_shape_respects_size_gk_and_subset_of_squad():
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad, start = _vars("squad"), _vars("start")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, RULES)
    constraints.add_xi_shape(prob, squad, start, IDS, POSITION, RULES)
    prob += pulp.lpSum(start[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    xi = [i for i in IDS if start[i].value() == 1]
    chosen = [i for i in IDS if squad[i].value() == 1]
    assert len(xi) == 2
    assert sum(1 for i in xi if POSITION[i] == "GK") == 1
    assert set(xi).issubset(set(chosen))


def test_xi_shape_force_formation_pins_exact_counts():
    rules = dict(RULES)
    rules["starting_xi"] = {"total": 2, "gk": 1, "min_def": 0, "min_mid": 0, "min_fwd": 0}
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad, start = _vars("squad"), _vars("start")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, rules)
    constraints.add_xi_shape(prob, squad, start, IDS, POSITION, rules,
                             force_formation={"def": 1})
    prob += pulp.lpSum(start[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    xi = [i for i in IDS if start[i].value() == 1]
    assert sum(1 for i in xi if POSITION[i] == "DEF") == 1


def test_captain_is_exactly_one_and_must_start():
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    squad, start, cap = _vars("squad"), _vars("start"), _vars("cap")
    constraints.add_squad_composition(prob, squad, IDS, POSITION, RULES)
    constraints.add_xi_shape(prob, squad, start, IDS, POSITION, RULES)
    constraints.add_captain_rules(prob, start, cap, IDS)
    prob += pulp.lpSum(cap[i] for i in IDS)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    caps = [i for i in IDS if cap[i].value() == 1]
    xi = [i for i in IDS if start[i].value() == 1]
    assert len(caps) == 1
    assert caps[0] in xi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decide_constraints.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fpl.decide.constraints'`

- [ ] **Step 3: Write minimal implementation**

Create `fpl/decide/constraints.py`:

```python
"""
constraints.py — shared MILP constraint builders for the DECIDE layer.

Extracted from optimise_squad so fpl/decide/transfers.py reuses the same
squad-legality rules instead of forking them (spec §4). The v4 plan told
us to "reuse optimise_squad's constraint builders" — they did not exist;
the constraints were written inline. These are that extraction, and the
extraction is behaviour-preserving by construction: same expressions,
same order, just parameterised.

Every function mutates `prob` in place and returns None.
"""
from __future__ import annotations

from typing import Optional

import pulp

_POS_KEYS = [("def", "min_def"), ("mid", "min_mid"), ("fwd", "min_fwd")]


def add_squad_composition(prob, squad, ids, position, rules) -> None:
    """Squad size and the per-position counts (2 GK / 5 DEF / 5 MID / 3 FWD)."""
    prob += pulp.lpSum(squad[i] for i in ids) == rules["total"]
    for pos, count in [("GK", rules["gk"]), ("DEF", rules["def"]),
                       ("MID", rules["mid"]), ("FWD", rules["fwd"])]:
        prob += pulp.lpSum(squad[i] for i in ids if position[i] == pos) == count


def add_club_limits(prob, squad, ids, team, rules) -> None:
    """At most `max_per_club` players from any one club."""
    for club in set(team[i] for i in ids):
        prob += pulp.lpSum(squad[i] for i in ids if team[i] == club) <= rules["max_per_club"]


def add_budget(prob, squad, ids, price, budget_limit) -> None:
    """Total squad cost within budget. `price` is real £m, not tenths."""
    prob += pulp.lpSum(price[i] * squad[i] for i in ids) <= budget_limit


def add_xi_shape(prob, squad, start, ids, position, rules,
                 force_formation: Optional[dict] = None) -> None:
    """
    Starting-XI size, GK count, formation bounds, and start ⊆ squad.

    force_formation tightens a position from ">= min" to "== exact";
    omitted keys keep the configured minimum (same semantics as
    optimise_squad's own parameter).
    """
    sx = rules["starting_xi"]
    force_formation = force_formation or {}
    prob += pulp.lpSum(start[i] for i in ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "GK") == sx["gk"]
    for pos_key, min_key in _POS_KEYS:
        pos = pos_key.upper()
        count_expr = pulp.lpSum(start[i] for i in ids if position[i] == pos)
        if pos_key in force_formation:
            prob += count_expr == force_formation[pos_key]
        else:
            prob += count_expr >= sx[min_key]
    for i in ids:
        prob += start[i] <= squad[i]


def add_captain_rules(prob, start, captain, ids) -> None:
    """Exactly one captain, who must be a starter."""
    prob += pulp.lpSum(captain[i] for i in ids) == 1
    for i in ids:
        prob += captain[i] <= start[i]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_decide_constraints.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Capture the pre-refactor baseline**

Before touching `optimiser.py`, record the current real-data output:

```bash
.venv\Scripts\python.exe -m fpl.decide.optimiser > baseline_before.txt 2>&1
```

Also save the artefact: `copy data\output\gw1_recommendations.json baseline_before.json`

- [ ] **Step 6: Refactor optimise_squad to call the builders**

In `fpl/decide/optimiser.py`, replace the inline block that currently reads (from the `# Squad composition` comment through the captain constraints, ~lines 242-278):

```python
    # Squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == rules["total"]
    for pos, count in [("GK", rules["gk"]), ("DEF", rules["def"]), ("MID", rules["mid"]), ("FWD", rules["fwd"])]:
        prob += pulp.lpSum(squad[i] for i in ids if position[i] == pos) == count
```

…through…

```python
    # Captain: exactly one, must be a starter
    prob += pulp.lpSum(captain[i] for i in ids) == 1
    for i in ids:
        prob += captain[i] <= start[i]
```

with:

```python
    # Squad composition, club limits, budget, XI shape and captain rules all
    # live in fpl/decide/constraints.py so fpl/decide/transfers.py reuses the
    # SAME legality rules rather than forking them (spec §4). Behaviour is
    # unchanged — same expressions, same order — and that is gated on a
    # byte-identical real-data re-run, not assumed.
    constraints.add_squad_composition(prob, squad, ids, position, rules)

    # budget_tenths is in tenths-of-a-million (matches raw now_cost units);
    # `price` here is already converted to real £m by build_players.py, so
    # the budget must be converted the same way: /10, not /100.
    # plan §E2: budget_override replaces the RHS wholesale (a live "what if
    # I had £X instead" question), not an addition to the configured budget.
    budget_limit = budget_override if budget_override is not None else rules["budget_tenths"] / 10.0
    constraints.add_budget(prob, squad, ids, price, budget_limit)
    constraints.add_club_limits(prob, squad, ids, team, rules)

    # Starting XI. plan §E2 force_formation tightens a position's ">= min"
    # to "== exact" when the caller specifies it (e.g. {"def": 3, "mid": 5,
    # "fwd": 2} for a 3-5-2) — any position not named keeps its configured
    # minimum, unchanged from the weekly path's behaviour.
    constraints.add_xi_shape(prob, squad, start, ids, position, rules, force_formation)
    constraints.add_captain_rules(prob, start, captain, ids)
```

Add the import near the top of `optimiser.py`, alongside the other `fpl.` imports:

```python
from fpl.decide import constraints
```

- [ ] **Step 7: THE GATE — verify byte-identical output on real data**

Run: `.venv\Scripts\python.exe -m fpl.decide.optimiser > baseline_after.txt 2>&1`

Then compare both the console output and the artefact:

```bash
diff baseline_before.txt baseline_after.txt
```

Expected: **no differences**. Then confirm the JSON artefact is unchanged:

```bash
git diff --stat data/output/gw1_recommendations.json
```

Expected: **empty** (no change).

**If either differs, STOP.** A pure refactor that changes a number is a bug. Do not proceed to Task 2; diagnose the discrepancy first.

- [ ] **Step 8: Run the full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 168 passed (162 + 6 new).

```bash
del baseline_before.txt baseline_after.txt baseline_before.json
git add fpl/decide/constraints.py fpl/decide/optimiser.py tests/test_decide_constraints.py
git commit -m "refactor(decide): extract shared MILP constraint builders"
```

---

### Task 2: Real squad-state ingestion

**Files:**
- Modify: `fpl/decide/squad_state.py`
- Modify: `config.yaml`
- Modify: `.gitignore`
- Test: `tests/test_squad_state_ingest.py`

**Interfaces:**
- Consumes: `config["fpl"]["entry_id"]`.
- Produces:
  - `MY_TEAM_PATH: Path` — module constant, monkeypatched in tests
  - `parse_entry_picks(picks_json) -> dict` — `{squad, starting_xi, captain, vice_captain, bank, value, active_chip, event}`
  - `parse_my_team(my_team_json) -> dict` — `{squad, sell_prices, bank, free_transfers}`
  - `load_my_team_file(path=None, max_age_hours=24.0) -> tuple[dict, float]` — returns `(parsed, age_hours)`; raises `StaleMyTeamError` past the hard limit
  - `StaleMyTeamError(Exception)`, `SquadMismatchError(Exception)`
  - `reconcile(public: dict, pasted: dict) -> dict` — cross-checks and merges; raises `SquadMismatchError` on squad disagreement
  - `fetch_entry_picks(entry_id, gw, config=None) -> dict` — network; not unit-tested

- [ ] **Step 1: Write the failing test**

Create `tests/test_squad_state_ingest.py`:

```python
"""
Tests for the H3a ingestion half of fpl/decide/squad_state.py (spec §5).

The dangerous failure here is a STALE pasted my-team file: it produces a
confident, wrong, unactionable recommendation. Hence two independent
guards — file age, and a cross-check of the pasted squad against the
public picks endpoint. Disagreement STOPS the solve.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import time

import pytest

from fpl.decide import squad_state

PICKS_JSON = {
    "active_chip": None,
    "entry_history": {"event": 1, "bank": 5, "value": 1000,
                      "event_transfers": 0, "event_transfers_cost": 0},
    "picks": [
        {"element": 10 + i, "position": i + 1, "multiplier": 1,
         "is_captain": i == 0, "is_vice_captain": i == 1,
         "element_type": 1 if i < 2 else 2}
        for i in range(15)
    ],
}

MY_TEAM_JSON = {
    "picks": [
        {"element": 10 + i, "position": i + 1, "selling_price": 50 + i,
         "purchase_price": 50 + i, "multiplier": 1,
         "is_captain": i == 0, "is_vice_captain": i == 1}
        for i in range(15)
    ],
    "transfers": {"bank": 5, "limit": 2, "made": 0, "value": 1000, "status": "cost"},
}


def test_parse_entry_picks_extracts_squad_bank_and_armband():
    out = squad_state.parse_entry_picks(PICKS_JSON)
    assert out["squad"] == list(range(10, 25))
    assert len(out["starting_xi"]) == 11
    assert out["captain"] == 10
    assert out["vice_captain"] == 11
    assert out["bank"] == 0.5          # 5 tenths -> £0.5m
    assert out["value"] == 100.0
    assert out["active_chip"] is None
    assert out["event"] == 1


def test_parse_my_team_reads_sell_prices_and_free_transfers():
    """Sell price is READ, never recomputed — spec §5.1."""
    out = squad_state.parse_my_team(MY_TEAM_JSON)
    assert out["free_transfers"] == 2
    assert out["bank"] == 0.5
    assert out["sell_prices"][10] == 5.0   # 50 tenths -> £5.0m
    assert out["sell_prices"][24] == 6.4
    assert sorted(out["squad"]) == list(range(10, 25))


def test_load_my_team_file_returns_age(tmp_path, monkeypatch):
    p = tmp_path / "my_team.json"
    p.write_text(json.dumps(MY_TEAM_JSON), encoding="utf-8")
    monkeypatch.setattr(squad_state, "MY_TEAM_PATH", p)
    parsed, age = squad_state.load_my_team_file()
    assert parsed["free_transfers"] == 2
    assert age < 1.0


def test_stale_file_past_hard_limit_raises(tmp_path, monkeypatch):
    p = tmp_path / "my_team.json"
    p.write_text(json.dumps(MY_TEAM_JSON), encoding="utf-8")
    old = time.time() - 60 * 60 * 100  # 100 hours ago
    os.utime(p, (old, old))
    monkeypatch.setattr(squad_state, "MY_TEAM_PATH", p)
    with pytest.raises(squad_state.StaleMyTeamError):
        squad_state.load_my_team_file(max_age_hours=24.0)


def test_missing_file_raises_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setattr(squad_state, "MY_TEAM_PATH", tmp_path / "absent.json")
    with pytest.raises(FileNotFoundError) as exc:
        squad_state.load_my_team_file()
    assert "my-team" in str(exc.value).lower()


def test_reconcile_merges_public_and_pasted():
    public = squad_state.parse_entry_picks(PICKS_JSON)
    pasted = squad_state.parse_my_team(MY_TEAM_JSON)
    out = squad_state.reconcile(public, pasted)
    assert out["squad"] == public["squad"]
    assert out["free_transfers"] == 2
    assert out["sell_prices"][10] == 5.0
    assert out["bank"] == 0.5


def test_reconcile_prefers_pasted_bank_when_they_differ():
    """Public bank is the value at the last deadline; the pasted file is
    live. A mismatch legitimately means 'already transferred this week'
    — reported, not fatal (spec §5.1)."""
    public = squad_state.parse_entry_picks(PICKS_JSON)
    pasted = dict(squad_state.parse_my_team(MY_TEAM_JSON))
    pasted["bank"] = 1.3
    out = squad_state.reconcile(public, pasted)
    assert out["bank"] == 1.3
    assert out["bank_mismatch"] is True


def test_reconcile_raises_when_squads_disagree():
    """A squad mismatch means the file is stale or for another entry.
    STOP — do not proceed with a warning (spec §5.3)."""
    public = squad_state.parse_entry_picks(PICKS_JSON)
    pasted = dict(squad_state.parse_my_team(MY_TEAM_JSON))
    pasted["squad"] = [999] + pasted["squad"][1:]
    with pytest.raises(squad_state.SquadMismatchError):
        squad_state.reconcile(public, pasted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_squad_state_ingest.py -q`
Expected: FAIL with `AttributeError: module 'fpl.decide.squad_state' has no attribute 'parse_entry_picks'`

- [ ] **Step 3: Write minimal implementation**

Append to `fpl/decide/squad_state.py` (keep the existing contents):

```python
# ---------------------------------------------------------------------------
# H3a — real-team ingestion (spec §5)
#
# Two sources, deliberately:
#   * public entry/{id}/event/{gw}/picks/ — squad composition, bank, chip.
#   * a user-pasted my-team JSON — sell prices and free transfers, which are
#     NOT public.
#
# NO CREDENTIALS ANYWHERE. The pasted file is saved by the user from their
# own logged-in browser; this code only ever reads a local gitignored path.
# ---------------------------------------------------------------------------

import time
from typing import Optional

PRIVATE_DIR = Path(__file__).resolve().parents[2] / "data" / "private"
MY_TEAM_PATH = PRIVATE_DIR / "my_team.json"

FPL_API = "https://fantasy.premierleague.com/api"


class StaleMyTeamError(Exception):
    """The pasted my-team file is too old to be trusted for a live solve."""


class SquadMismatchError(Exception):
    """Pasted file and public endpoint disagree about the 15 — stop."""


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
        "purchase_prices": {int(p["element"]): _tenths(p.get("purchase_price", p["selling_price"]))
                            for p in picks},
        "bank": _tenths(transfers.get("bank", 0)),
        "free_transfers": int(transfers.get("limit") or 0),
    }


def load_my_team_file(path: Optional[Path] = None,
                      max_age_hours: float = 24.0) -> tuple[dict, float]:
    """
    Read + parse the pasted file, returning (parsed, age_hours).

    A stale file is worse than a missing one — it yields a confident,
    wrong, unactionable recommendation — so age is enforced, not logged.
    """
    p = Path(path) if path is not None else MY_TEAM_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"No my-team file at {p}. Save the JSON from "
            f"{FPL_API}/my-team/<entry_id>/ while logged in to fantasy.premierleague.com, "
            f"then re-run. Run scripts/my_team_instructions.py for the exact steps."
        )
    age_hours = (time.time() - p.stat().st_mtime) / 3600.0
    if age_hours > max_age_hours:
        raise StaleMyTeamError(
            f"{p} is {age_hours:.1f}h old (limit {max_age_hours:.0f}h). Sell prices and "
            f"free transfers drift; re-save it before solving."
        )
    return parse_my_team(json.loads(p.read_text(encoding="utf-8"))), age_hours


def reconcile(public: dict, pasted: dict) -> dict:
    """
    Cross-check the two sources and merge into one squad state.

    Squad disagreement is FATAL: it means the pasted file is stale or
    belongs to a different entry, and every downstream number would be
    wrong. Bank disagreement is NOT fatal — the public value is the bank
    at the last deadline, so a difference legitimately means a transfer
    has already been made this week; the live (pasted) value wins and the
    difference is surfaced.
    """
    if sorted(public["squad"]) != sorted(pasted["squad"]):
        only_public = sorted(set(public["squad"]) - set(pasted["squad"]))
        only_pasted = sorted(set(pasted["squad"]) - set(public["squad"]))
        raise SquadMismatchError(
            "Pasted my-team file and the public picks endpoint disagree about the squad — "
            f"the file is stale or for a different entry. Only in public: {only_public}; "
            f"only in file: {only_pasted}. Re-save the file and retry."
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
```

Add `from pathlib import Path` is already imported at the top of the file; confirm `import json` is too (it is).

Add to `config.yaml`, as a new top-level block near the top (after `season:`):

```yaml
# The operator's own FPL entry. PUBLIC (it's in the Points page URL) —
# not a secret. Used by fpl/decide/squad_state.py to read the real squad.
fpl:
  entry_id: 6669718
```

Add to `.gitignore`:

```
# Phase H: the pasted my-team JSON. Personal squad/price data, and the
# file the transfer solver reads. Never committed.
data/private/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_squad_state_ingest.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Verify against the real entry**

Run:

```bash
.venv\Scripts\python.exe -c "from fpl.decide import squad_state as s; p=s.parse_entry_picks(s.fetch_entry_picks(6669718,1)); print(p['squad']); print('captain',p['captain'],'bank',p['bank'],'value',p['value'])"
```

Expected: the 15 ids `[1, 154, 165, 180, 212, 335, 367, 387, 388, 411, 428, 465, 497, 508, 533]`, captain `411`, bank `0.0`, value `100.0`.

- [ ] **Step 6: Commit**

```bash
git add fpl/decide/squad_state.py tests/test_squad_state_ingest.py config.yaml .gitignore
git commit -m "feat(decide): real squad-state ingestion with stale-file guards"
```

---

### Task 3: The transfer solver

**Files:**
- Create: `fpl/decide/transfers.py`
- Test: `tests/test_transfers.py`

**Interfaces:**
- Consumes: `constraints.*`, `optimiser.pick_xi_and_captain`, `fpl.status.UNAVAILABLE_STATUSES`.
- Produces:
  - `InfeasibleBudgetError(Exception)`
  - `solve_transfers(players, current_squad_ids, sell_prices, bank, free_transfers, config=None, apply_availability_filters=True, force_n_transfers=None) -> dict`
  - `recommend(players, current_squad_ids, sell_prices, bank, free_transfers, config=None) -> dict` — solves twice (forced-roll baseline + free optimum) and returns the comparison payload of spec §6

- [ ] **Step 1: Write the failing test**

Create `tests/test_transfers.py`:

```python
"""
Tests for fpl/decide/transfers.py — spec §3.

The subtle one is test_owned_but_filtered_player_can_still_be_sold: an
injured player you OWN is dropped from the candidate pool, but you can
still sell him. If he is absent from the model's id space the linking
constraint squad[p] = current[p] - out[p] + in[p] is unsatisfiable for
him and the solve fails or misprices the squad (spec §3.3).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.decide import transfers

CONFIG = {
    "squad_rules": {
        "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3,
        "max_per_club": 3,
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1},
    },
    "optimiser": {"allow_low_confidence": False, "bench_weight_epsilon": 0.02},
    "transfers": {"hit_cost": 4},
}


def _pool(n_extra=6, upgrade_xpts=30.0):
    """15 owned players + spare candidates, all £5.0m, 5 clubs x plenty."""
    rows = []
    # Owned 15: 2 GK, 5 DEF, 5 MID, 3 FWD
    plan = ["GK"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    for i, pos in enumerate(plan):
        rows.append({"id": 100 + i, "web_name": f"own{i}", "position": pos,
                     "price": 5.0, "team": i % 5, "confidence": "high", "status": "a",
                     "weighted_xpts": 10.0, "next_gw_xpts": 2.0})
    # Spare candidates, one per position, cheap, on clubs with room
    spare_plan = ["GK", "DEF", "MID", "FWD", "MID", "DEF"][:n_extra]
    for j, pos in enumerate(spare_plan):
        rows.append({"id": 200 + j, "web_name": f"cand{j}", "position": pos,
                     "price": 5.0, "team": 5 + (j % 3), "confidence": "high", "status": "a",
                     "weighted_xpts": upgrade_xpts if j == 2 else 9.0,
                     "next_gw_xpts": 3.0 if j == 2 else 1.0})
    return pd.DataFrame(rows)


OWNED = [100 + i for i in range(15)]
SELL = {i: 5.0 for i in OWNED}


def test_zero_transfers_is_feasible_and_is_the_roll_baseline():
    players = _pool(upgrade_xpts=9.0)  # nothing better available
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert out["n_transfers"] == 0
    assert sorted(out["squad"]) == sorted(OWNED)


def test_a_clear_upgrade_is_taken_with_a_free_transfer():
    players = _pool(upgrade_xpts=30.0)  # cand2 (MID) is far better
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert out["n_transfers"] == 1
    assert out["hits"] == 0
    assert 202 in out["squad"]


def test_cannot_buy_a_player_already_owned():
    players = _pool()
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert set(out["transfers_in"]).isdisjoint(set(OWNED))


def test_budget_blocks_an_unaffordable_transfer():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 202, "price"] = 20.0   # way over bank+proceeds
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG)
    assert 202 not in out["squad"]


def test_second_transfer_costs_exactly_one_hit_with_one_free():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 204, "weighted_xpts"] = 30.0  # a 2nd big MID upgrade
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG,
                                    force_n_transfers=2)
    assert out["n_transfers"] == 2
    assert out["hits"] == 1
    assert out["hit_points"] == 4


def test_two_free_transfers_means_no_hit():
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 204, "weighted_xpts"] = 30.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=2, config=CONFIG,
                                    force_n_transfers=2)
    assert out["hits"] == 0
    assert out["hit_points"] == 0


def test_owned_but_filtered_player_can_still_be_sold():
    """THE trap (spec §3.3). An injured owned player is dropped from the
    pool by availability filters, but must remain sellable — otherwise
    the linking constraint is unsatisfiable for him."""
    players = _pool(upgrade_xpts=30.0)
    players.loc[players["id"] == 110, "status"] = "i"        # owned MID, injured
    players.loc[players["id"] == 110, "weighted_xpts"] = 0.0
    players.loc[players["id"] == 110, "next_gw_xpts"] = 0.0

    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=1, config=CONFIG,
                                    apply_availability_filters=True)
    # Solve succeeded, squad still legal, and the injured player was sold
    # in favour of the available upgrade.
    assert len(out["squad"]) == 15
    assert 110 in out["transfers_out"]


def test_owned_but_filtered_player_can_be_kept_when_no_upgrade_exists():
    players = _pool(upgrade_xpts=9.0)
    players.loc[players["id"] == 110, "status"] = "i"
    players.loc[players["id"] == 110, "weighted_xpts"] = 0.0
    out = transfers.solve_transfers(players, OWNED, SELL, bank=0.0,
                                    free_transfers=0, config=CONFIG)
    assert 110 in out["squad"]      # representable, not forced out


def test_post_solve_assertion_rejects_an_unaffordable_result():
    """Belt and braces on top of the budget constraint (spec §5.4) —
    the constraint being right in theory is not the same as the input
    numbers being right."""
    players = _pool(upgrade_xpts=30.0)
    bad_sell = {i: 0.0 for i in OWNED}   # sells raise nothing
    with pytest.raises(transfers.InfeasibleBudgetError):
        transfers._assert_affordable(
            transfers_in=[202], transfers_out=[110],
            price={202: 9.0}, sell_prices=bad_sell, bank=0.0,
        )


def test_recommend_reports_both_horizons_and_names_the_move():
    players = _pool(upgrade_xpts=30.0)
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["recommendation"] == "transfer"
    assert rec["n_transfers"] == 1
    assert rec["transfers"][0]["in"]["id"] == 202
    assert rec["transfers"][0]["out"]["id"] in OWNED
    assert rec["weighted_gain"] > 0
    assert "next_gw_gain" in rec           # stated even when negative
    assert rec["baseline"]["weighted"] < rec["after"]["weighted"]


def test_recommend_says_roll_when_nothing_is_worth_it():
    players = _pool(upgrade_xpts=9.0)
    rec = transfers.recommend(players, OWNED, SELL, bank=0.0,
                              free_transfers=1, config=CONFIG)
    assert rec["recommendation"] == "roll"
    assert rec["n_transfers"] == 0
    assert rec["transfers"] == []
    assert rec["weighted_gain"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transfers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fpl.decide.transfers'`

- [ ] **Step 3: Write minimal implementation**

Create `fpl/decide/transfers.py`:

```python
"""
transfers.py — DECIDE layer, v4 plan §5 / spec
docs/superpowers/specs/2026-08-22-transfer-decision-layer-design.md.

Answers the question the squad optimiser cannot: "I own these 15, I have
N free transfers and £X in the bank — what do I do?"

PURE FUNCTION. No network, no file I/O, no writes to data/state/ —
ingestion is squad_state.py's job. Same discipline as optimise_squad, so
this is freely re-solvable offline and from the live dashboards (v3 §E1).

HORIZON (spec §3.2): the decision is made on weighted_xpts (the same
5-GW decay-weighted value squad selection uses), with the hit charged
ONCE. The v4 plan's literal single-gameweek objective would be
systematically hit-averse — a one-week gain would have to clear 4 points
to justify a hit, which almost never happens, making the -4 decorative.
The next-gameweek consequence is always reported alongside.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pulp

from fpl.decide import constraints
from fpl.decide.optimiser import pick_xi_and_captain
from fpl.status import UNAVAILABLE_STATUSES

_EPS = 1e-6


class InfeasibleBudgetError(Exception):
    """A recommended transfer set costs more than bank + sale proceeds."""


def _assert_affordable(transfers_in, transfers_out, price, sell_prices, bank) -> None:
    """
    Post-solve guard (spec §5.4), independent of the MILP constraint.

    The constraint being correct in theory is not the same as the INPUT
    numbers being correct, and this is the failure mode that produces a
    plausible, wrong, unactionable recommendation.
    """
    cost = sum(price[i] for i in transfers_in)
    proceeds = sum(sell_prices[i] for i in transfers_out)
    if cost > bank + proceeds + _EPS:
        raise InfeasibleBudgetError(
            f"Recommended transfers cost {cost:.1f} but only "
            f"{bank + proceeds:.1f} is available (bank {bank:.1f} + proceeds "
            f"{proceeds:.1f}). This recommendation is not executable."
        )


def _build_id_space(players: pd.DataFrame, current_squad_ids, apply_filters, allow_low_conf):
    """
    pool ∪ current_squad (spec §3.3).

    An owned player filtered out by availability MUST still be in the id
    space or `squad[p] = current[p] - out[p] + in[p]` is unsatisfiable for
    him. Note no explicit no-buy flag is needed: for an owned player
    current[p] = 1, so transfer_in[p] <= 1 - current[p] = 0 already
    forbids buying him. The union alone is the whole fix.
    """
    pool = players.copy()
    if apply_filters:
        if not allow_low_conf:
            pool = pool[pool["confidence"] != "low"]
        pool = pool[~pool["status"].isin(UNAVAILABLE_STATUSES)]
    owned = players[players["id"].isin(current_squad_ids)]
    space = pd.concat([pool, owned]).drop_duplicates(subset="id").reset_index(drop=True)
    return space


def solve_transfers(
    players: pd.DataFrame,
    current_squad_ids: list,
    sell_prices: dict,
    bank: float,
    free_transfers: int,
    config: Optional[dict] = None,
    apply_availability_filters: bool = True,
    force_n_transfers: Optional[int] = None,
    force_formation: Optional[dict] = None,
) -> dict:
    """
    One MILP: choose the transfer set maximising weighted squad value net
    of hits. `force_n_transfers` pins Σ transfer_in exactly (used to build
    the roll baseline, and by tests).
    """
    if config is None:
        from fpl.decide.optimiser import load_config
        config = load_config()
    rules = config["squad_rules"]
    hit_cost = config["transfers"]["hit_cost"]
    allow_low_conf = config["optimiser"]["allow_low_confidence"]
    bench_eps = config["optimiser"].get("bench_weight_epsilon", 0.0)

    space = _build_id_space(players, current_squad_ids, apply_availability_filters, allow_low_conf)
    ids = space["id"].tolist()
    missing = set(current_squad_ids) - set(ids)
    if missing:
        raise ValueError(
            f"Owned players missing from the input DataFrame entirely: {sorted(missing)}. "
            f"They cannot be represented, so no legal transfer set exists."
        )

    wx = dict(zip(space["id"], space["weighted_xpts"]))
    nx = dict(zip(space["id"], space["next_gw_xpts"]))
    price = dict(zip(space["id"], space["price"]))
    position = dict(zip(space["id"], space["position"]))
    team = dict(zip(space["id"], space["team"]))
    current = {i: (1 if i in set(current_squad_ids) else 0) for i in ids}

    prob = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", ids, cat="Binary")
    t_in = pulp.LpVariable.dicts("t_in", ids, cat="Binary")
    t_out = pulp.LpVariable.dicts("t_out", ids, cat="Binary")
    penalized = pulp.LpVariable("penalized", lowBound=0, cat="Integer")

    # Transfer linking. For an owned player current=1 so t_in <= 0; for an
    # unowned one current=0 so t_out <= 0. Both directions fall out of the
    # same pair of constraints.
    for i in ids:
        prob += squad[i] == current[i] - t_out[i] + t_in[i]
        prob += t_out[i] <= current[i]
        prob += t_in[i] <= 1 - current[i]

    n_transfers_expr = pulp.lpSum(t_in[i] for i in ids)
    if force_n_transfers is not None:
        prob += n_transfers_expr == force_n_transfers
    prob += penalized >= n_transfers_expr - free_transfers
    prob += penalized >= 0

    # Transfer budget: what you buy must be covered by bank + what you sell.
    prob += (pulp.lpSum(price[i] * t_in[i] for i in ids)
             <= bank + pulp.lpSum(sell_prices.get(i, price[i]) * t_out[i] for i in ids))

    constraints.add_squad_composition(prob, squad, ids, position, rules)
    constraints.add_club_limits(prob, squad, ids, team, rules)
    constraints.add_xi_shape(prob, squad, start, ids, position, rules, force_formation)
    constraints.add_captain_rules(prob, start, captain, ids)

    prob += (
        pulp.lpSum(start[i] * wx[i] for i in ids)
        + pulp.lpSum(captain[i] * wx[i] for i in ids)
        + bench_eps * pulp.lpSum((squad[i] - start[i]) * wx[i] for i in ids)
        - hit_cost * penalized
    ), "weighted_points_net_of_hits"

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Transfer solve failed: {pulp.LpStatus[prob.status]}")

    new_squad = [i for i in ids if squad[i].value() == 1]
    t_in_ids = [i for i in ids if t_in[i].value() == 1]
    t_out_ids = [i for i in ids if t_out[i].value() == 1]

    _assert_affordable(t_in_ids, t_out_ids, price, sell_prices, bank)

    n = len(t_in_ids)
    hits = max(0, n - free_transfers)

    # Stage 2: XI + armband are a THIS-WEEK decision, re-picked on
    # next_gw_xpts via the existing solver — same split optimise_squad uses.
    xi, cap_id, vice_id = pick_xi_and_captain(new_squad, nx, position, rules, force_formation)

    return {
        "squad": new_squad,
        "starting_xi": xi,
        "captain": cap_id,
        "vice_captain": vice_id,
        "transfers_in": t_in_ids,
        "transfers_out": t_out_ids,
        "n_transfers": n,
        "hits": hits,
        "hit_points": hits * hit_cost,
        "weighted_value": sum(wx[i] for i in new_squad),
        "weighted_objective": pulp.value(prob.objective),
        "next_gw_value": sum(nx[i] for i in xi) + nx[cap_id],
    }


def recommend(
    players: pd.DataFrame,
    current_squad_ids: list,
    sell_prices: dict,
    bank: float,
    free_transfers: int,
    config: Optional[dict] = None,
    apply_availability_filters: bool = True,
) -> dict:
    """
    Solve twice — a forced-roll baseline and the free optimum — so the
    reported gain is against an identical objective rather than an
    ad-hoc comparison, and "roll it" is a first-class answer.
    """
    baseline = solve_transfers(players, current_squad_ids, sell_prices, bank,
                               free_transfers, config, apply_availability_filters,
                               force_n_transfers=0)
    best = solve_transfers(players, current_squad_ids, sell_prices, bank,
                           free_transfers, config, apply_availability_filters)

    space = players.set_index("id")
    def _row(i):
        r = space.loc[i]
        return {"id": int(i), "web_name": r["web_name"], "position": r["position"],
                "price": round(float(r["price"]), 1)}

    moves = []
    for out_id, in_id in zip(sorted(best["transfers_out"]), sorted(best["transfers_in"])):
        o, n = _row(out_id), _row(in_id)
        o["sell_price"] = round(float(sell_prices.get(out_id, o["price"])), 1)
        moves.append({"out": o, "in": n})

    weighted_gain = round(best["weighted_objective"] - baseline["weighted_objective"], 2)
    next_gw_gain = round(best["next_gw_value"] - baseline["next_gw_value"], 2)

    return {
        "recommendation": "transfer" if best["n_transfers"] > 0 else "roll",
        "free_transfers": free_transfers,
        "bank": round(float(bank), 1),
        "n_transfers": best["n_transfers"],
        "hits": best["hits"],
        "hit_points": best["hit_points"],
        "transfers": moves,
        "weighted_gain": weighted_gain,
        "next_gw_gain": next_gw_gain,
        "baseline": {"weighted": round(baseline["weighted_objective"], 2),
                     "next_gw": round(baseline["next_gw_value"], 2)},
        "after": {"weighted": round(best["weighted_objective"], 2),
                  "next_gw": round(best["next_gw_value"], 2)},
        "squad": best["squad"],
        "starting_xi": best["starting_xi"],
        "captain": best["captain"],
        "vice_captain": best["vice_captain"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transfers.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 187 passed (168 + 11 + 8 from Task 2).

```bash
git add fpl/decide/transfers.py tests/test_transfers.py
git commit -m "feat(decide): transfer solver with hits endogenous"
```

---

### Task 4: Entry point, output artefact, archive hook

**Files:**
- Modify: `fpl/decide/transfers.py` (add `__main__` + `build_recommendation`)
- Create: `scripts/my_team_instructions.py`
- Modify: `fpl/history/archive.py`
- Test: `tests/test_transfers_output.py`

**Interfaces:**
- Consumes: `squad_state.*`, `transfers.recommend`, `fpl.project.project`.
- Produces:
  - `transfers.OUTPUT_DIR: Path` — module constant, monkeypatched in tests
  - `transfers.write_recommendation(rec: dict, gw: int, entry_id: int, extra: dict) -> Path`
  - `archive` picks up `gw*_transfers.json` as a decisions-domain artefact

- [ ] **Step 1: Write the failing test**

Create `tests/test_transfers_output.py`:

```python
"""
Tests for the transfer recommendation artefact (spec §6) and its capture
by the Phase G archive.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

from fpl.decide import transfers

REC = {
    "recommendation": "transfer", "free_transfers": 1, "bank": 0.0,
    "n_transfers": 1, "hits": 0, "hit_points": 0,
    "transfers": [{"out": {"id": 497, "web_name": "X", "position": "MID",
                           "price": 6.0, "sell_price": 6.0},
                   "in": {"id": 302, "web_name": "Y", "position": "MID", "price": 6.5}}],
    "weighted_gain": 3.21, "next_gw_gain": -0.14,
    "baseline": {"weighted": 223.8, "next_gw": 64.56},
    "after": {"weighted": 227.01, "next_gw": 64.42},
    "squad": list(range(15)), "starting_xi": list(range(11)),
    "captain": 0, "vice_captain": 1,
}


def test_write_recommendation_emits_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(transfers, "OUTPUT_DIR", tmp_path)
    p = transfers.write_recommendation(
        REC, gw=2, entry_id=6669718,
        extra={"sell_price_source": "my_team_file", "my_team_file_age_hours": 2.1},
    )
    assert p.name == "gw2_transfers.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["gameweek"] == 2
    assert d["entry_id"] == 6669718
    assert d["recommendation"] == "transfer"
    assert d["weighted_gain"] == 3.21
    assert d["next_gw_gain"] == -0.14      # negative is reported, not hidden
    assert d["sell_price_source"] == "my_team_file"
    assert d["my_team_file_age_hours"] == 2.1


def test_roll_recommendation_still_states_the_gain(tmp_path, monkeypatch):
    monkeypatch.setattr(transfers, "OUTPUT_DIR", tmp_path)
    roll = dict(REC, recommendation="roll", n_transfers=0, transfers=[], weighted_gain=0.0)
    d = json.loads(transfers.write_recommendation(
        roll, gw=2, entry_id=1, extra={}).read_text(encoding="utf-8"))
    assert d["recommendation"] == "roll"
    assert d["transfers"] == []
    assert d["weighted_gain"] == 0.0


def test_archive_discovers_transfer_artefacts(tmp_path, monkeypatch):
    """Phase G must capture transfer recommendations like any other
    decision artefact, or the bitemporal record has a hole in it."""
    import pandas as pd
    from fpl.history import archive, paths

    out = tmp_path / "output"
    proj = tmp_path / "projections"
    raw = tmp_path / "raw"
    for d in (out, proj, raw):
        d.mkdir(parents=True)
    (out / "gw2_transfers.json").write_text(json.dumps({"gameweek": 2}), encoding="utf-8")
    (raw / "bootstrap_static.json").write_text(json.dumps(
        {"events": [{"id": 2, "deadline_time": "2026-08-28T17:30:00Z",
                     "finished": False, "is_next": True}]}), encoding="utf-8")

    monkeypatch.setattr(archive, "OUTPUT_DIR", out)
    monkeypatch.setattr(archive, "PROJECTIONS_DIR", proj)
    monkeypatch.setattr(archive, "RAW_DIR", raw)

    found = archive.discover_artefacts()
    assert (2, out / "gw2_transfers.json") in found["transfers"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transfers_output.py -q`
Expected: FAIL with `AttributeError: module 'fpl.decide.transfers' has no attribute 'OUTPUT_DIR'`

- [ ] **Step 3: Add the writer and entry point**

Append to `fpl/decide/transfers.py`:

```python
# ---------------------------------------------------------------------------
# Output artefact + CLI entry point (spec §6/§7)
# ---------------------------------------------------------------------------
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"


def write_recommendation(rec: dict, gw: int, entry_id: int, extra: dict) -> Path:
    """
    Writes data/output/gw{n}_transfers.json.

    next_gw_gain is included even when negative: a correct long-game
    transfer that costs points this week is exactly the case a human
    needs to see, not have hidden.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"gameweek": int(gw), "entry_id": int(entry_id),
               "generated_utc": datetime.now(timezone.utc).isoformat()}
    payload.update(rec)
    payload.update(extra)
    path = OUTPUT_DIR / f"gw{gw}_transfers.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    """
    Wire ingestion -> solve -> artefact. Deliberately NOT in weekly.yml
    yet (spec §7.3): the recommendation must be hand-verified as
    executable in the real game for two gameweeks first.
    """
    from fpl.decide.optimiser import load_config
    from fpl.decide import squad_state
    from fpl.project import project as project_mod

    config = load_config()
    entry_id = config["fpl"]["entry_id"]

    players = project_mod.weighted_horizon_total(
        project_mod.project_all(config), config)

    public_raw = squad_state.fetch_entry_picks(entry_id, _latest_finished_or_current(config))
    public = squad_state.parse_entry_picks(public_raw)
    pasted, age_h = squad_state.load_my_team_file()
    state = squad_state.reconcile(public, pasted)

    if state["bank_mismatch"]:
        print(f"[transfers] NOTE: public bank {state['public_bank']} != live bank "
              f"{state['bank']} — you have already transferred this week.")

    target_gw = int(state["event"]) + 1
    rec = recommend(players, state["squad"], state["sell_prices"],
                    state["bank"], state["free_transfers"], config)

    path = write_recommendation(rec, target_gw, entry_id, {
        "sell_price_source": "my_team_file",
        "my_team_file_age_hours": round(age_h, 2),
    })

    if rec["recommendation"] == "roll":
        print(f"[transfers] GW{target_gw}: ROLL. No transfer clears the hit cost.")
    else:
        for m in rec["transfers"]:
            print(f"[transfers] GW{target_gw}: OUT {m['out']['web_name']} "
                  f"(£{m['out']['sell_price']}m) -> IN {m['in']['web_name']} "
                  f"(£{m['in']['price']}m)")
        print(f"[transfers] hits={rec['hits']} (-{rec['hit_points']} pts) | "
              f"weighted gain {rec['weighted_gain']:+.2f} | "
              f"next-GW {rec['next_gw_gain']:+.2f}")
    print(f"[transfers] wrote {path}")
    return 0


def _latest_finished_or_current(config) -> int:
    """The most recent gameweek with picks available (its deadline passed)."""
    raw = Path(__file__).resolve().parents[2] / "data" / "raw" / "bootstrap_static.json"
    events = json.loads(raw.read_text(encoding="utf-8"))["events"]
    started = [e["id"] for e in events if e.get("is_current") or e.get("finished")]
    return max(started) if started else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

- [ ] **Step 4: Add the archive hook**

In `fpl/history/archive.py`, inside `discover_artefacts`, after the decisions loop, add:

```python
    transfers_found = []
    for p in sorted(OUTPUT_DIR.glob("gw*_transfers.json")):
        try:
            gw = int(json.loads(p.read_text(encoding="utf-8"))["gameweek"])
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        transfers_found.append((gw, p))
```

and change the return to:

```python
    return {"projections": projections, "decisions": decisions,
            "health": health, "transfers": transfers_found}
```

Then in `archive_run`, after the decisions loop, add:

```python
    for gw, src in found["transfers"]:
        dst = paths.transfers_partition(gw, asof)
        _copy(src, dst)
        archived.append({"domain": "transfers", "gw": gw, "model": None,
                         "path": str(dst.relative_to(paths.HISTORY_DIR))})
```

And in `fpl/history/paths.py` add:

```python
def transfers_partition(gw: int, asof: str) -> Path:
    return HISTORY_DIR / "transfers" / f"gw={gw}" / f"asof={asof}" / "transfers.json"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transfers_output.py tests/test_history_archive.py -q`
Expected: PASS (3 new + 7 existing archive tests still green)

- [ ] **Step 6: Add the instructions helper**

Create `scripts/my_team_instructions.py`:

```python
"""
my_team_instructions.py — prints how to save the my-team JSON.

The transfer solver needs sell prices and the free-transfer count, which
are NOT public. This tool never sees, asks for, or stores an FPL
credential; you save the file yourself from your own logged-in browser.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fpl.decide.optimiser import load_config  # noqa: E402
from fpl.decide import squad_state  # noqa: E402


def main() -> int:
    entry_id = load_config()["fpl"]["entry_id"]
    url = f"{squad_state.FPL_API}/my-team/{entry_id}/"
    dest = squad_state.MY_TEAM_PATH
    print(f"""
To refresh sell prices and your free-transfer count:

  1. Log in at https://fantasy.premierleague.com in your normal browser.
  2. Open this URL in the same browser:
         {url}
  3. Save the JSON it returns to:
         {dest}
     (Ctrl+S, or copy the text into that file.)

The file is gitignored and never leaves your machine. No password, cookie
or token is read by this repo -- step 2 works because YOUR browser is
already authenticated, and nothing here has access to that session.

Re-save it whenever prices may have moved; the solver refuses a file
older than 24h.
""".strip())
    dest.parent.mkdir(parents=True, exist_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 190 passed.

```bash
git add fpl/decide/transfers.py fpl/history/archive.py fpl/history/paths.py scripts/my_team_instructions.py tests/test_transfers_output.py
git commit -m "feat(decide): transfer recommendation artefact + archive capture"
```

---

### Task 5: Backfill GW1 `played`, and documentation

**Files:**
- Modify: `data/state/squad_gw1.json`
- Modify: `docs/PROJECT_LOG.md`, `docs/HANDOFF.md`, `CLAUDE.md`

- [ ] **Step 1: Backfill the now-known `played` value**

The real GW1 team was verified byte-identical to the recommendation
(spec §2.2). Record it:

```bash
.venv\Scripts\python.exe -c "import json; p='data/state/squad_gw1.json'; d=json.load(open(p,encoding='utf-8')); assert d['played'] is None; d['played']=dict(d['recommended']); open(p,'w',encoding='utf-8').write(json.dumps(d,indent=2,ensure_ascii=False)); print('played backfilled')"
```

Verify:

```bash
.venv\Scripts\python.exe -c "import json; d=json.load(open('data/state/squad_gw1.json',encoding='utf-8')); print('played == recommended:', d['played']==d['recommended'])"
```

Expected: `True`

- [ ] **Step 2: Append a PROJECT_LOG section**

Add `## 16. Phase H (H3a+H3b) — the transfer decision layer` covering:
what was built module by module; the horizon deviation from the plan's
literal single-GW objective and why; that the constraint builders had to
be extracted before they could be reused and the byte-identical gate that
protected the champion path; the owned-but-filtered id-space union and
why no explicit no-buy flag was needed; the two ingestion guards and why
squad mismatch is fatal while bank mismatch is not; and the verified fact
that GW1 `played == recommended`.

- [ ] **Step 3: Update HANDOFF status header and open items**

Prepend a dated status block. Move to "Not yet done": H3c multi-period,
H3d chips, the two-gameweek hand-verification gate before `transfers.py`
joins `weekly.yml`, and the still-open `optimiser.py` lock-of-filtered-
player bug (now closely related to §3.3 and worth fixing together).

- [ ] **Step 4: Update CLAUDE.md**

Add `fpl/decide/transfers.py` and `fpl/decide/constraints.py` to the
architecture map; correct the "`transfers.py` not built" line in Known
open items; add a short note that the my-team file is gitignored and no
credential is ever handled.

- [ ] **Step 5: Commit**

```bash
git add data/state/squad_gw1.json docs/PROJECT_LOG.md docs/HANDOFF.md CLAUDE.md
git commit -m "docs: Phase H transfer decision layer"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 public vs pasted sources | 2 |
| §2.2 backfill `played` | 5 |
| §3.1 MILP formulation | 3 |
| §3.2 weighted horizon, hit once | 3 |
| §3.3 owned-but-filtered union | 3 (with its own two tests) |
| §3.4 FT as input, `hit_cost` only | 3 |
| §4 constraint extraction + gate | 1 |
| §5.1 sources table, bank preference | 2 |
| §5.2 pasted file location, gitignore | 2 |
| §5.3 age + cross-check guards | 2 |
| §5.4 post-solve assertion | 3 |
| §6 output artefact + archive | 4 |
| §7.1 purity | 3 (no I/O in `solve_transfers`) |
| §7.2 writes output, never state | 4 |
| §7.3 not in weekly.yml | 4 (documented in `main`'s docstring) |
| §7.4 backfill | 5 |
| §8 verification | 1–4 |

No spec section is unimplemented.

**Type consistency:** `solve_transfers` / `recommend` / `write_recommendation` / `_assert_affordable` / `parse_entry_picks` / `parse_my_team` / `load_my_team_file` / `reconcile` — names and signatures used in later tasks match their definitions. `MY_TEAM_PATH` and `OUTPUT_DIR` are the monkeypatch targets. `constraints.add_*` signatures are identical between Task 1's definition and Task 3's use.

**Known risk:** Task 1 modifies the champion path. Its Step 7 gate is a hard stop, not a warning — if real-data output differs at all, the task fails and Task 2 does not start.
