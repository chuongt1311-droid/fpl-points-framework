"""
Tests for fpl/decide/squad_state.py — spec §3.2.

Pure unit tests: synthetic optimiser result dict, tmp_path for the state
file (monkeypatched STATE_DIR), no network.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

from fpl.decide import squad_state


def _result() -> dict:
    return {
        "squad": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
        "starting_xi": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        "captain": 1,
        "vice_captain": 2,
    }


def test_write_squad_state_writes_recommended_with_played_null(tmp_path, monkeypatch):
    monkeypatch.setattr(squad_state, "STATE_DIR", tmp_path)
    path = squad_state.write_squad_state(1, _result(), bank=0.5, free_transfers=1)

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["gameweek"] == 1
    assert state["recommended"]["captain"] == 1
    assert state["played"] is None
    assert state["bank"] == 0.5
    assert state["free_transfers"] == 1
    assert state["transfers_made"] == []
    assert state["chips_used"] == []
    assert state["chip_active"] is None


def test_write_squad_state_never_overwrites_an_existing_played_value(tmp_path, monkeypatch):
    monkeypatch.setattr(squad_state, "STATE_DIR", tmp_path)
    squad_state.write_squad_state(1, _result(), bank=0.5)
    squad_state.set_played(1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12], captain=1, vice_captain=2)

    # A second write_squad_state call (e.g. re-running the recommender)
    # must preserve the already-recorded `played` value, not null it out.
    path = squad_state.write_squad_state(1, _result(), bank=0.4)
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["played"]["starting_xi"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12]
    assert state["bank"] == 0.4  # bookkeeping DOES update


def test_load_squad_state_raises_a_clear_error_for_a_missing_gameweek(tmp_path, monkeypatch):
    monkeypatch.setattr(squad_state, "STATE_DIR", tmp_path)
    try:
        squad_state.load_squad_state(99)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_set_played_can_diverge_from_recommended(tmp_path, monkeypatch):
    """Decision D6: recommended and played are independent — grading each
    answers a different question."""
    monkeypatch.setattr(squad_state, "STATE_DIR", tmp_path)
    squad_state.write_squad_state(1, _result(), bank=0.5)
    squad_state.set_played(1, _result()["squad"], _result()["starting_xi"], captain=2, vice_captain=1)

    state = squad_state.load_squad_state(1)
    assert state["recommended"]["captain"] == 1
    assert state["played"]["captain"] == 2  # overrode the model's captain pick
