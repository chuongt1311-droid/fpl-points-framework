"""
Tests for fpl/evaluate/hindsight.py — spec §3.3/§3.4/§3.7's exit gate.

Pure unit tests for simulate_autosubs/_formation_ok (no file I/O), plus one
integration-style test of compute_hindsight against a fully synthetic
squad/actuals setup (tmp_path + monkeypatch, no network) — proving the
regret decomposition sums to the total on real code, not just algebra.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

import pandas as pd

from fpl.decide import squad_state as squad_state_mod
from fpl.evaluate import hindsight

RULES = {"starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1}}


def _squad_of_15() -> tuple[list[int], dict[int, str]]:
    ids, position = [], {}
    for pos, n in [("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        for _ in range(n):
            pid = len(ids) + 1
            ids.append(pid)
            position[pid] = pos
    return ids, position


def test_gk_autosub_fires_only_when_the_starting_gk_gets_zero_minutes():
    ids, position = _squad_of_15()
    starting = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13]  # 1=GK1, rest a legal 4-4-2ish XI
    bench_order = [2, 12, 14, 15]  # 2 = GK2
    minutes = {i: 90 for i in ids}
    minutes[1] = 0  # starting GK blanked

    xi, captain = hindsight.simulate_autosubs(starting, bench_order, 3, 4, minutes, position, RULES)
    assert 2 in xi and 1 not in xi


def test_outfield_autosub_respects_formation_minimums():
    ids, position = _squad_of_15()
    # Starting XI: GK1, 3 DEF (3,4,5), 4 MID (8,9,10,11), 3 FWD (13,14,15)
    starting = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]
    bench_order = [2, 6, 7, 12]  # 6,7 = spare DEF; 12 = spare MID
    minutes = {i: 90 for i in ids}
    minutes[3] = 0  # a DEF blanks — at exactly the 3-DEF minimum

    xi, _ = hindsight.simulate_autosubs(starting, bench_order, 8, 9, minutes, position, RULES)
    # Replacing DEF 3 with bench DEF 6 keeps DEF count at 3 (legal) —
    # must actually happen, not be skipped.
    assert 3 not in xi
    assert 6 in xi
    assert sum(1 for i in xi if position[i] == "DEF") == 3


def test_a_bench_player_who_also_got_zero_minutes_cannot_come_on():
    ids, position = _squad_of_15()
    starting = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]
    bench_order = [2, 6, 7, 12]
    minutes = {i: 90 for i in ids}
    minutes[3] = 0
    minutes[6] = 0  # first bench DEF also didn't play

    xi, _ = hindsight.simulate_autosubs(starting, bench_order, 8, 9, minutes, position, RULES)
    assert 6 not in xi  # can't come on themselves
    assert 7 in xi      # next bench DEF in order does come on
    assert 3 not in xi


def test_captain_falls_back_to_vice_when_captain_blanks():
    ids, position = _squad_of_15()
    starting = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]
    bench_order = [2, 6, 7, 12]
    minutes = {i: 90 for i in ids}
    minutes[8] = 0  # captain blanks

    _, captain = hindsight.simulate_autosubs(starting, bench_order, 8, 9, minutes, position, RULES)
    assert captain == 9


def test_no_armband_bonus_when_captain_and_vice_both_blank():
    ids, position = _squad_of_15()
    starting = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]
    bench_order = [2, 6, 7, 12]
    minutes = {i: 90 for i in ids}
    minutes[8] = 0
    minutes[9] = 0

    _, captain = hindsight.simulate_autosubs(starting, bench_order, 8, 9, minutes, position, RULES)
    assert captain is None


def test_regret_decomposition_sums_to_total(tmp_path, monkeypatch):
    """Integration-style: a fully synthetic squad/actuals setup, proving
    captaincy + bench + squad == total on real compute_hindsight code, not
    just by algebraic construction."""
    ids, position = _squad_of_15()

    monkeypatch.setattr(squad_state_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hindsight, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(hindsight, "ACTUALS_DIR", tmp_path)
    monkeypatch.setattr(hindsight, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(hindsight, "OUTPUT_DIR", tmp_path)

    # Actual points: deliberately give the bench a big edge over a weak
    # starting choice, and give the squad a mediocre spread so a
    # differently-priced-but-unaffordable global XI isn't reachable —
    # budget is irrelevant here since every player is priced identically.
    actual_points = {i: (i % 7) + 1 for i in ids}
    actual_minutes = {i: 90 for i in ids}
    rows = [{"code": i, "id": i, "event": 1, "minutes": actual_minutes[i], "total_points": actual_points[i]}
            for i in ids]
    pd.DataFrame(rows).to_csv(tmp_path / "actuals_test.csv", index=False)
    monkeypatch.setattr(hindsight, "load_config", lambda: {
        "season": "test", "squad_rules": {
            "budget_tenths": 1000, "total": 15, "gk": 2, "def": 5, "mid": 5, "fwd": 3, "max_per_club": 3,
            "starting_xi": RULES["starting_xi"],
        },
        "optimiser": {"allow_low_confidence": True, "bench_weight_epsilon": 0.0},
    })

    result_stub = {
        "squad": ids,
        "starting_xi": [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15],
        "captain": 8, "vice_captain": 9,
    }
    squad_state_mod.write_squad_state(1, result_stub, bank=0.0)

    # players.parquet needs price/team/code for the global-XI fallback pool
    players_full = pd.DataFrame([
        {"id": i, "code": i, "position": position[i], "team": i, "price": 5.0} for i in ids
    ])
    players_full.to_parquet(tmp_path / "players.parquet", index=False)

    result = hindsight.compute_hindsight(1)
    r = result["regret"]
    assert r["captaincy"] + r["bench"] + r["squad"] == r["total"]

    # And the output file was actually written.
    assert (tmp_path / "hindsight_gw1.json").exists()
    on_disk = json.loads((tmp_path / "hindsight_gw1.json").read_text(encoding="utf-8"))
    assert on_disk["regret"]["total"] == r["total"]
