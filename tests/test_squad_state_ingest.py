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
