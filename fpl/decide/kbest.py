"""
kbest.py — DECIDE layer. v3 plan §D: alternatives with diversity, not pure
K-best. Two distinct problems (plan §D2), both via no-good cuts on the MILP
(plan §D1): Σ_{i in S} x_i <= |S| - d, forbidding a new solution from
matching a prior one in d-or-more of the cut variable's members.

  - Alternative SQUADS of 15 (find_k_best_squads): d>=2 required, or the
    K-best squads differ only in the cheapest bench filler and tell you
    nothing (plan's own example — d=1 gives fifteen near-identical squads).
  - Alternative XIs from a FIXED 15 (find_k_best_xis): pure K-best, d=1 —
    nearly free, `pick_xi_and_captain` already takes an arbitrary points
    vector (extracted as its own function in the 2026-08-20 hotfix
    specifically so this kind of reuse works).

Both report frontier_spread (plan §D3): the gap between squad/XI #1 and
#2 is the actual interesting number, not squad #2 itself. A tight spread
means the model has no real preference — non-model factors (ownership,
differentials, gut) should decide. A wide spread means a genuine
conviction pick.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import pulp

from fpl.decide import optimiser as opt_mod


def find_k_best_squads(
    players: pd.DataFrame, config: dict, k: int = 5, diversity_d: int = 3,
    apply_availability_filters: bool = True,
) -> list[dict]:
    """
    Repeatedly calls optimiser.optimise_squad, adding a no-good cut after
    each solution so the next solve is forced to differ from every prior
    one by at least diversity_d players (plan §D1/§D2). Stops early (fewer
    than k results) if the pool is exhausted of sufficiently-different
    legal squads — a real, reportable outcome (a thin pool near the
    frontier), not an error.

    Returns a list of optimise_squad's normal result dicts, each with two
    extra keys:
      "rank" — 1-indexed position on the frontier (1 = the single best squad
               optimise_squad alone would return).
      "frontier_spread" — stage1_objective gap from squad #1, always <= 0
               (0.0 for squad #1 itself). MUST be computed on
               stage1_objective, NOT horizon_weighted_xpts (the raw 15-man
               xpts sum) — the two are not monotonic with each other, since
               the objective only rewards starting-XI + captain + a small
               bench term, not the full squad sum (see optimise_squad's
               stage1_objective docstring; caught as a real bug here: a
               later, more-constrained squad had a HIGHER raw sum than
               squad #1 while being a genuinely worse pick by the actual
               objective). Plan §D3: this spread is the number that
               actually matters, not squad #2 in isolation — a small spread
               across several squads means the model has no strong
               preference among them.
    """
    results = []
    cuts: list[tuple[list, int]] = []
    for _ in range(k):
        try:
            result = opt_mod.optimise_squad(
                players, config, apply_availability_filters=apply_availability_filters,
                extra_no_good_cuts=cuts,
            )
        except RuntimeError:
            # Infeasible — the pool has no more squads differing from every
            # prior one by diversity_d+ players. Stop, don't fabricate one.
            break
        results.append(result)
        cuts.append((result["squad"], diversity_d))

    if not results:
        return results

    best_objective = results[0]["stage1_objective"]
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
        result["frontier_spread"] = round(result["stage1_objective"] - best_objective, 4)

    return results


def find_k_best_xis(
    squad_ids: list, next_gw_xpts: dict, position: dict, rules: dict, k: int = 5,
) -> list[dict]:
    """
    Alternative starting XIs from a FIXED 15 (plan §D2 — pure K-best,
    diversity_d=1, since the pool is only 15 players and forcing more
    diversity there would quickly run out of legal alternatives). Reuses
    the exact LP structure of optimiser.pick_xi_and_captain, with no-good
    cuts on the `start` variable added between solves.

    Returns a list of {"starting_xi": [...11...], "captain": id,
    "vice_captain": id, "next_gw_expected_points": float, "rank": int,
    "frontier_spread": float} — same frontier_spread convention as
    find_k_best_squads (gap from XI #1, computed on next_gw_expected_points
    since XI/captain are per-gameweek decisions, not the horizon number).
    """
    sx = rules["starting_xi"]
    results = []
    no_good_cuts: list[list] = []

    for _ in range(k):
        prob = pulp.LpProblem("fpl_xi_kbest", pulp.LpMaximize)
        start = pulp.LpVariable.dicts("xi_start", squad_ids, cat="Binary")
        cap = pulp.LpVariable.dicts("xi_captain", squad_ids, cat="Binary")

        prob += pulp.lpSum((start[i] + cap[i]) * next_gw_xpts[i] for i in squad_ids)

        prob += pulp.lpSum(start[i] for i in squad_ids) == sx["total"]
        prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "GK") == sx["gk"]
        prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "DEF") >= sx["min_def"]
        prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "MID") >= sx["min_mid"]
        prob += pulp.lpSum(start[i] for i in squad_ids if position[i] == "FWD") >= sx["min_fwd"]
        prob += pulp.lpSum(cap[i] for i in squad_ids) == 1
        for i in squad_ids:
            prob += cap[i] <= start[i]

        for prior_xi in no_good_cuts:
            prob += pulp.lpSum(start[i] for i in prior_xi) <= len(prior_xi) - 1  # diversity_d=1

        prob.solve(pulp.PULP_CBC_CMD(msg=False))
        if pulp.LpStatus[prob.status] != "Optimal":
            break

        starting_ids = [i for i in squad_ids if start[i].value() == 1]
        captain_id = next(i for i in squad_ids if cap[i].value() == 1)
        by_pts = sorted(starting_ids, key=lambda i: next_gw_xpts[i], reverse=True)
        vice_captain_id = next(i for i in by_pts if i != captain_id)

        results.append({
            "starting_xi": starting_ids,
            "captain": captain_id,
            "vice_captain": vice_captain_id,
            "next_gw_expected_points": sum(next_gw_xpts[i] for i in starting_ids) + next_gw_xpts[captain_id],
        })
        no_good_cuts.append(starting_ids)

    if not results:
        return results

    best_pts = results[0]["next_gw_expected_points"]
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
        result["frontier_spread"] = round(result["next_gw_expected_points"] - best_pts, 4)

    return results


def compute_cross_model_agreement(
    squads_by_model: dict[str, list[dict]],
) -> dict:
    """
    Plan §D3: "report cross-model agreement too: if M0's top squad ranks
    #2 under M2 and #3 under M3, that convergence is worth more than any
    single model's confidence."

    `squads_by_model` — {model_name: find_k_best_squads(...) result} for
    each model already run over the SAME player pool (different model =
    different weighted_xpts column values, same player universe/ids).

    For every model's #1 (rank==1) squad, finds where that exact squad
    (as a frozenset of ids) appears in every OTHER model's own K-best list
    — "rank" if found within that other model's computed frontier, else
    None (not necessarily disagreement — it may just be outside that
    model's own top-k, not literally ranked worse by every measure).

    Returns {model_name: {"squad_ids": [...], other_model: rank_or_None, ...}}.
    """
    top_squad_by_model = {
        model: frozenset(results[0]["squad"])
        for model, results in squads_by_model.items() if results
    }

    agreement = {}
    for model, squad_ids_set in top_squad_by_model.items():
        row = {"squad_ids": sorted(squad_ids_set)}
        for other_model, other_results in squads_by_model.items():
            if other_model == model:
                continue
            rank_in_other = next(
                (r["rank"] for r in other_results if frozenset(r["squad"]) == squad_ids_set), None
            )
            row[other_model] = rank_in_other
        agreement[model] = row
    return agreement
