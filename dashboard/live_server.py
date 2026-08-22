"""
live_server.py — v3 plan §E: the live decision layer, Flask edition.

This replaces dashboard/app.py's Streamlit UI with a purpose-built
frontend (dashboard/live/index.html) while keeping the exact same
FROZEN/LIVE boundary (plan §E1) and reproducibility guard (plan §E3):

    FROZEN (GitHub Actions, committed artefacts, reproducible):
      data pull -> identity join -> baseline -> per-channel rates -> xpts
      Lives in fpl/collect, fpl/transform, fpl/project — this file never
      imports the projection pipeline itself, only dashboard/live_data.py's
      pure loaders (same contract app.py already used).

    LIVE (this server, re-solved on demand, never persisted as canonical):
      xpts vector + constraints -> MILP -> squad / XI / captain / K-best
      fpl/decide/optimiser.py and fpl/decide/kbest.py, called exactly the
      way app.py called them — this file adds an HTTP/JSON skin, not a
      new decision path.

Why Flask instead of Streamlit: Streamlit's widget chrome is a fixed
visual vocabulary — every Streamlit app looks like a Streamlit app. The
plan's own D3 boundary rule ("the optimiser is a pure function... may be
re-solved freely") only constrains WHERE the solve happens, not what
renders it, so a thin JSON API plus a hand-built frontend can keep every
guardrail (EXPLORATORY labelling, pinned canonical, solve logging, an
explicit Solve action) while looking nothing like generic widget chrome.

dashboard/app.py (Streamlit) is left in place and still works — this is
an addition, not a replacement in the codebase, in case Streamlit's
faster-to-prototype iteration loop is ever wanted again.

Run: .venv\\Scripts\\python.exe -m dashboard.live_server
Then open http://127.0.0.1:5000/
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

# Runnable as `python -m dashboard.live_server` OR directly — make sure
# the repo root is on sys.path either way (same reasoning as app.py's
# own sys.path.insert, see that file's comment).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from fpl.decide import kbest as kbest_mod

from dashboard import live_data

app = Flask(__name__, static_folder=None)

FORMATIONS = {
    "auto": None,
    "3-4-3": {"def": 3, "mid": 4, "fwd": 3},
    "3-5-2": {"def": 3, "mid": 5, "fwd": 2},
    "4-3-3": {"def": 4, "mid": 3, "fwd": 3},
    "4-4-2": {"def": 4, "mid": 4, "fwd": 2},
    "4-5-1": {"def": 4, "mid": 5, "fwd": 1},
    "5-3-2": {"def": 5, "mid": 3, "fwd": 2},
    "5-4-1": {"def": 5, "mid": 4, "fwd": 1},
}

LIVE_DIR = Path(__file__).resolve().parent / "live"


def _team_lookup() -> pd.DataFrame:
    """id -> short team name, read from the same committed players.parquet
    live_data.py already reads for `status` — never a second pipeline call."""
    players = pd.read_parquet(live_data.PROCESSED_DIR / "players.parquet")
    return players[["team", "team_short_name"]].drop_duplicates().rename(
        columns={"team_short_name": "team_short"}
    )


def _detail_records(detail: pd.DataFrame, team_lookup: pd.DataFrame) -> dict:
    d = detail.merge(team_lookup, on="team", how="left")
    d = d.sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
    cols = ["id", "web_name", "position", "team_short", "price", "weighted_xpts", "is_starting"]
    starters = d[d["is_starting"]][cols].to_dict("records")
    bench = d[~d["is_starting"]][cols].to_dict("records")
    return {"starters": starters, "bench": bench}


@app.get("/")
def index():
    return send_from_directory(LIVE_DIR, "index.html")


@app.get("/api/state")
def api_state():
    gameweeks = live_data.available_gameweeks()
    if not gameweeks:
        return jsonify({"gameweeks": [], "models_by_gw": {}, "default_budget": None})
    models_by_gw = {
        gw: [{"value": m, "label": live_data.MODEL_LABELS.get(m, m)} for m in live_data.available_models(gw)]
        for gw in gameweeks
    }
    config = live_data.load_config()
    default_budget = config["squad_rules"]["budget_tenths"] / 10.0
    return jsonify({
        "gameweeks": gameweeks,
        "models_by_gw": models_by_gw,
        "default_budget": default_budget,
        "formations": list(FORMATIONS.keys()),
    })


@app.get("/api/players")
def api_players():
    model = request.args.get("model", "m0_rules")
    gameweek = int(request.args["gameweek"])
    df = live_data.load_player_inputs(model, gameweek)
    df = df.merge(_team_lookup(), on="team", how="left")
    cols = ["id", "web_name", "position", "team", "team_short", "price", "confidence", "status", "weighted_xpts", "next_gw_xpts"]
    return jsonify(df[cols].to_dict("records"))


@app.get("/api/canonical")
def api_canonical():
    gameweek = int(request.args["gameweek"])
    result = live_data.load_canonical_recommendation(gameweek)
    return jsonify(result)


@app.post("/api/solve")
def api_solve():
    body = request.get_json(force=True) or {}
    gameweek = int(body["gameweek"])
    model = body.get("model", "m0_rules")
    k_squads = int(body.get("k_squads", 3))
    diversity_d = int(body.get("diversity_d", 3))
    locked_ids = [int(i) for i in body.get("locked_ids") or []] or None
    banned_ids = [int(i) for i in body.get("banned_ids") or []] or None
    banned_clubs = [int(i) for i in body.get("banned_clubs") or []] or None
    budget_override = body.get("budget_override")
    formation_key = body.get("force_formation") or "auto"
    force_formation = FORMATIONS.get(formation_key)
    chip = body.get("chip") or None

    player_inputs = live_data.load_player_inputs(model, gameweek)
    config = live_data.load_config()
    team_lookup = _team_lookup()

    constraints_summary = {
        "gameweek": gameweek, "model": model, "locked_ids": locked_ids, "banned_ids": banned_ids,
        "banned_clubs": banned_clubs, "budget_override": budget_override,
        "force_formation": force_formation, "chip": chip,
        "k_squads": k_squads, "diversity_d": diversity_d,
    }

    try:
        results = kbest_mod.find_k_best_squads(
            player_inputs, config, k=k_squads, diversity_d=diversity_d,
            apply_availability_filters=True,
            locked_ids=locked_ids, banned_ids=banned_ids, banned_clubs=banned_clubs,
            budget_override=budget_override, force_formation=force_formation, chip=chip,
        )
    except RuntimeError as exc:
        live_data.log_live_solve({
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "constraints": constraints_summary, "n_results": 0, "error": str(exc),
        })
        return jsonify({"error": str(exc)})

    live_data.log_live_solve({
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "constraints": constraints_summary,
        "n_results": len(results),
        "top_squad_ids": results[0]["squad"] if results else None,
        "top_stage1_objective": results[0]["stage1_objective"] if results else None,
    })

    payload = []
    for r in results:
        cap = player_inputs.loc[player_inputs["id"] == r["captain"], "web_name"]
        vc = player_inputs.loc[player_inputs["id"] == r["vice_captain"], "web_name"]
        payload.append({
            "rank": r["rank"],
            "frontier_spread": r["frontier_spread"],
            "total_cost": round(r["total_cost"], 1),
            "horizon_weighted_xpts": round(r["horizon_weighted_xpts"], 2),
            "next_gw_expected_points": round(r["next_gw_expected_points"], 2),
            "captain": cap.iloc[0] if len(cap) else None,
            "vice_captain": vc.iloc[0] if len(vc) else None,
            **_detail_records(r["detail"], team_lookup),
        })

    return jsonify({
        "results": payload,
        "no_constraints": not any([locked_ids, banned_ids, banned_clubs, budget_override, chip]) and formation_key == "auto",
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
