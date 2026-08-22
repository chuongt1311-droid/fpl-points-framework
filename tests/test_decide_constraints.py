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
