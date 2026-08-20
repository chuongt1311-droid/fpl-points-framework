"""
optimiser.py — DECIDE layer. Best 15-player squad + starting XI + captain,
within budget and squad rules — plan §6.1.

Constraints (real FPL rules):
  - 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
  - £100.0m budget
  - Max 3 players per club
  - Starting XI: 1 GK, min 3 DEF, min 2 MID, min 1 FWD, 11 total
  - Captain gets 2x (vice-captain = 2nd-highest xPts starter, as a fallback
    if captain doesn't play)

Method: MILP via PuLP, maximising 5-GW weighted xPts of the starting XI
plus the captain's doubled contribution — a clean MILP that solves in
seconds, exactly matching plan §6.1's "no heuristics needed." For GW1 with
no existing team, this produces the initial squad directly.

plan §3.3: won't recommend a confidence='low' player unless
config.optimiser.allow_low_confidence is set — squad selection is
"transferring in" 15 players from nothing, so the same rule applies here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import pulp
import yaml

from fpl.project import project as project_mod

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def optimise_squad(players: pd.DataFrame, config: Optional[dict] = None) -> dict:
    """
    players: one row per player with id, web_name, position, price, team,
    weighted_xpts, confidence, status (as produced by
    fpl.project.project.weighted_horizon_total, merged with status).

    Returns {"squad": [...15 ids...], "starting_xi": [...11...],
    "captain": id, "vice_captain": id, "total_cost": float,
    "expected_points": float} plus the full DataFrame with is_squad/
    is_starting/is_captain flags for inspection.
    """
    config = config or load_config()
    rules = config["squad_rules"]
    allow_low_confidence = config["optimiser"]["allow_low_confidence"]

    pool = players.copy()
    if not allow_low_confidence:
        pool = pool[pool["confidence"] != "low"]
    pool = pool[~pool["status"].isin(["i", "s", "u"])]  # never select a definitely-unavailable player
    pool = pool.reset_index(drop=True)

    ids = pool["id"].tolist()
    xpts = dict(zip(pool["id"], pool["weighted_xpts"]))
    price = dict(zip(pool["id"], pool["price"]))
    position = dict(zip(pool["id"], pool["position"]))
    team = dict(zip(pool["id"], pool["team"]))

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    captain = pulp.LpVariable.dicts("captain", ids, cat="Binary")

    prob += (
        pulp.lpSum(start[i] * xpts[i] for i in ids) + pulp.lpSum(captain[i] * xpts[i] for i in ids)
    ), "total_expected_points"

    # Squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == rules["total"]
    for pos, count in [("GK", rules["gk"]), ("DEF", rules["def"]), ("MID", rules["mid"]), ("FWD", rules["fwd"])]:
        prob += pulp.lpSum(squad[i] for i in ids if position[i] == pos) == count

    # budget_tenths is in tenths-of-a-million (matches raw now_cost units);
    # `price` here is already converted to real £m by build_players.py, so
    # the budget must be converted the same way: /10, not /100.
    prob += pulp.lpSum(price[i] * squad[i] for i in ids) <= rules["budget_tenths"] / 10.0

    for club in set(team.values()):
        prob += pulp.lpSum(squad[i] for i in ids if team[i] == club) <= rules["max_per_club"]

    # Starting XI
    sx = rules["starting_xi"]
    prob += pulp.lpSum(start[i] for i in ids) == sx["total"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "GK") == sx["gk"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "DEF") >= sx["min_def"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "MID") >= sx["min_mid"]
    prob += pulp.lpSum(start[i] for i in ids if position[i] == "FWD") >= sx["min_fwd"]
    for i in ids:
        prob += start[i] <= squad[i]

    # Captain: exactly one, must be a starter
    prob += pulp.lpSum(captain[i] for i in ids) == 1
    for i in ids:
        prob += captain[i] <= start[i]

    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)

    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Optimiser did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    squad_ids = [i for i in ids if squad[i].value() == 1]
    starting_ids = [i for i in ids if start[i].value() == 1]
    captain_id = next(i for i in ids if captain[i].value() == 1)

    starters_by_xpts = sorted(starting_ids, key=lambda i: xpts[i], reverse=True)
    vice_captain_id = next(i for i in starters_by_xpts if i != captain_id)

    result_df = pool[pool["id"].isin(squad_ids)].copy()
    result_df["is_squad"] = True
    result_df["is_starting"] = result_df["id"].isin(starting_ids)
    result_df["is_captain"] = result_df["id"] == captain_id
    result_df["is_vice_captain"] = result_df["id"] == vice_captain_id

    total_cost = sum(price[i] for i in squad_ids)
    expected_points = sum(xpts[i] for i in starting_ids) + xpts[captain_id]

    return {
        "squad": squad_ids,
        "starting_xi": starting_ids,
        "captain": captain_id,
        "vice_captain": vice_captain_id,
        "total_cost": total_cost,
        "expected_points": expected_points,
        "detail": result_df,
    }


def build_gw1_squad(config: Optional[dict] = None) -> dict:
    config = config or load_config()
    horizon = config["horizon"]["gameweeks"]
    projections = project_mod.project_gameweeks(horizon, config)
    totals = project_mod.weighted_horizon_total(projections, config)

    players = pd.read_parquet(Path(__file__).resolve().parents[2] / "data" / "processed" / "players.parquet")
    totals = totals.merge(players[["id", "status"]], on="id", how="left")

    result = optimise_squad(totals, config)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gw = projections["event"].min()
    out_path = OUTPUT_DIR / f"gw{gw}_recommendations.json"

    detail = result["detail"].sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
    squad_json = {
        "gameweek": int(gw),
        "total_cost": round(result["total_cost"], 1),
        "budget": config["squad_rules"]["budget_tenths"] / 10.0,
        "expected_points": round(result["expected_points"], 2),
        "captain": {
            "id": int(result["captain"]),
            "web_name": detail.loc[detail["id"] == result["captain"], "web_name"].iloc[0],
        },
        "vice_captain": {
            "id": int(result["vice_captain"]),
            "web_name": detail.loc[detail["id"] == result["vice_captain"], "web_name"].iloc[0],
        },
        "squad": [
            {
                "id": int(r["id"]), "web_name": r["web_name"], "position": r["position"],
                "team": int(r["team"]), "price": r["price"], "weighted_xpts": round(r["weighted_xpts"], 2),
                "is_starting": bool(r["is_starting"]), "is_captain": bool(r["is_captain"]),
                "is_vice_captain": bool(r["is_vice_captain"]),
            }
            for _, r in detail.iterrows()
        ],
    }
    import json
    out_path.write_text(json.dumps(squad_json, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


if __name__ == "__main__":
    result = build_gw1_squad()
    detail = result["detail"].sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
    print(f"Total cost: £{result['total_cost']:.1f}m / expected points: {result['expected_points']:.2f}\n")
    print("STARTING XI:")
    starters = detail[detail["is_starting"]]
    print(starters[["web_name", "position", "team", "price", "weighted_xpts"]].to_string(index=False))
    print("\nBENCH:")
    bench = detail[~detail["is_starting"]]
    print(bench[["web_name", "position", "team", "price", "weighted_xpts"]].to_string(index=False))
    cap_name = detail.loc[detail["id"] == result["captain"], "web_name"].iloc[0]
    vc_name = detail.loc[detail["id"] == result["vice_captain"], "web_name"].iloc[0]
    print(f"\nCaptain: {cap_name} | Vice-captain: {vc_name}")
