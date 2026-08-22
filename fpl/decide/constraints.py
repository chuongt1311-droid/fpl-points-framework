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
