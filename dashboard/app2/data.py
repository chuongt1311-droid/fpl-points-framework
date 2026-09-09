"""
dashboard/app2/data.py — data assembly for the rough demo.

Same FROZEN/LIVE split as dashboard/live_data.py: model output (xPts,
channel breakdown, fixture multipliers) is read from already-committed
artefacts only. The one live piece is a direct, cheap bootstrap-static +
entry/picks pull (prices, injury news, this GW's live score) — no
projection/decide code ever runs from this process. This is a rough demo,
not the final app2 module split described in the design doc; it will be
split into views/ once the shape is proven.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.project import project as project_mod

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yaml"
RAW_DIR = ROOT / "data" / "raw"
PROJECTIONS_DIR = ROOT / "data" / "projections"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "data" / "output"

API_BASE = "https://fantasy.premierleague.com/api"
_CACHE: dict = {}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_json(url: str) -> dict:
    if url in _CACHE:
        return _CACHE[url]
    req = urllib.request.Request(url, headers={"User-Agent": "fpl-dashboard-demo/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    _CACHE[url] = data
    return data


def live_bootstrap() -> dict:
    """Cheap direct pull — prices, status/news, live event points. NOT the
    committed data/raw snapshot (that only refreshes on the pipeline
    schedule); this is genuinely live, per the freshness split we agreed."""
    return _get_json(f"{API_BASE}/bootstrap-static/")


def live_entry_picks(entry_id: int, event: int) -> dict:
    return _get_json(f"{API_BASE}/entry/{entry_id}/event/{event}/picks/")


def live_entry_history(entry_id: int) -> dict:
    return _get_json(f"{API_BASE}/entry/{entry_id}/history/")


def _target_event(bootstrap: dict) -> int:
    events = bootstrap["events"]
    for e in events:
        if e.get("is_current"):
            return e["id"]
    for e in events:
        if not e.get("finished"):
            return e["id"]
    return 1


def current_squad_view(config: Optional[dict] = None) -> dict:
    """Squad table: live price/status/news overlaid on committed model
    output (weighted xPts from the last pipeline run's projections)."""
    config = config or load_config()
    bootstrap = live_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    entry_id = config["fpl"]["entry_id"]

    completed_event = None
    for e in bootstrap["events"]:
        if e.get("finished") or e.get("is_previous") or e.get("is_current"):
            completed_event = e["id"]
    picks_event = completed_event or 1
    picks = live_entry_picks(entry_id, picks_event)["picks"]

    # Model output: whichever projections file the pipeline last produced —
    # not re-derived here.
    proj_files = sorted(PROJECTIONS_DIR.glob("gw*.parquet"))
    proj_path = proj_files[-1] if proj_files else None
    totals = None
    if proj_path is not None:
        proj = pd.read_parquet(proj_path)
        totals = project_mod.weighted_horizon_total(proj, config).set_index("id")

    rows = []
    for p in picks:
        eid = p["element"]
        e = elements[eid]
        row = {
            "id": eid,
            "web_name": e["web_name"],
            "team": teams[e["team"]],
            "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}[e["element_type"]],
            "price": e["now_cost"] / 10,
            "status": e["status"],
            "news": e["news"],
            "chance": e.get("chance_of_playing_next_round"),
            "is_captain": p["is_captain"],
            "is_vice": p["is_vice_captain"],
            "multiplier": p["multiplier"],
            "event_points": e["event_points"],
        }
        if totals is not None and eid in totals.index:
            t = totals.loc[eid]
            row["weighted_xpts"] = round(float(t["weighted_xpts"]), 2)
            row["next_gw_xpts"] = round(float(t["next_gw_xpts"]), 2)
            row["confidence"] = t["confidence"]
        else:
            row["weighted_xpts"] = None
            row["next_gw_xpts"] = None
            row["confidence"] = None
        rows.append(row)

    rows.sort(key=lambda r: (r["multiplier"] == 0, {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}[r["position"]]))

    try:
        hist = live_entry_history(entry_id)["current"]
    except Exception:
        hist = []

    return {
        "rows": rows,
        "history": hist,
        "projections_asof": proj_path.name if proj_path else None,
        "picks_event": picks_event,
        "entry_id": entry_id,
    }


def _transfer_out_ids(target_gw: int) -> set[int]:
    """Player ids the committed gw{n}_transfers.json recommends selling —
    the source of the 'sell?' verdict on a card. Empty if no such file."""
    path = OUTPUT_DIR / f"gw{target_gw}_transfers.json"
    if not path.exists():
        return set()
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {int(i) for i in rec.get("transfers_out", [])}


def points_view(config: Optional[dict] = None) -> dict:
    """FPL 'Points' screen — last completed GW, ACTUAL points on a pitch.
    A reshape of squad_snapshot; no model output involved."""
    config = config or load_config()
    bootstrap = live_bootstrap()
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    elements = {e["id"]: e for e in bootstrap["elements"]}

    event = None
    for e in bootstrap["events"]:
        if e.get("finished"):
            event = e["id"]
    event = event or 1

    snap = squad_snapshot(event, config)
    xi, bench = [], []
    for r in snap["rows"]:
        card = {
            "id": r["id"], "web_name": r["web_name"], "position": r["position"],
            "team_short": teams.get(elements.get(r["id"], {}).get("team"), r.get("team", "?")),
            "pts": r["points"], "is_captain": r["is_captain"], "is_vice": r["is_vice"],
        }
        (bench if r["multiplier"] == 0 else xi).append(card)

    eh = snap.get("entry_history", {})
    return {
        "event": event, "xi": xi, "bench": bench,
        "total": eh.get("points"), "rank": eh.get("overall_rank"),
        "active_chip": snap.get("active_chip"),
    }


def pick_team_view(config: Optional[dict] = None) -> dict:
    """FPL 'Pick Team' screen — your current XI/bench on a pitch, with the
    model's numbers overlaid. A reshape of current_squad_view."""
    config = config or load_config()
    bootstrap = live_bootstrap()
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    elements = {e["id"]: e for e in bootstrap["elements"]}
    target_gw = _target_event(bootstrap)
    sell_ids = _transfer_out_ids(target_gw)

    view = current_squad_view(config)
    xi, bench = [], []
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    captain = None
    for r in view["rows"]:
        chance = r.get("chance")
        card = {
            "id": r["id"], "web_name": r["web_name"], "position": r["position"],
            "team_short": teams.get(elements.get(r["id"], {}).get("team"), r.get("team", "?")),
            "xpts": r.get("weighted_xpts"), "next_gw_xpts": r.get("next_gw_xpts"),
            "start_prob": (chance / 100.0) if chance is not None else None,
            "status": r.get("status"), "news": r.get("news"),
            "verdict": "down" if r["id"] in sell_ids else "hold",
            "is_captain": r["is_captain"], "is_vice": r["is_vice"],
        }
        if r["multiplier"] == 0:
            bench.append(card)
        else:
            xi.append(card)
            counts[r["position"]] += 1
            if r["is_captain"]:
                captain = r["web_name"]

    return {
        "event": target_gw, "xi": xi, "bench": bench,
        "formation": f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}",
        "captain": captain,
        "bank": (view["history"][-1]["bank"] / 10.0) if view.get("history") else None,
    }


def channel_breakdown(player_id: int, event: int, config: Optional[dict] = None) -> Optional[dict]:
    """Per-channel xPts for one player/fixture — model transparency view.
    Reuses compute_channel_pts_per_fixture, which project.py's own
    docstring names as built for exactly this (the dashboard channel-
    breakdown script)."""
    config = config or load_config()
    inputs = project_mod.build_player_inputs(config)
    fixture_mults = pd.read_parquet(PROCESSED_DIR / "fixture_multipliers.parquet")
    breakdown = project_mod.compute_channel_pts_per_fixture(inputs, fixture_mults, config)
    row = breakdown[(breakdown["id"] == player_id) & (breakdown["event"] == event)]
    if row.empty:
        return None
    r = row.iloc[0]
    channels = {
        "Appearance": r["appearance_pts"],
        "Goals": r.get("goal_pts_cal", r["goal_pts"]),
        "Assists": r.get("assist_pts_cal", r["assist_pts"]),
        "Clean sheet": r.get("cleansheet_pts_cal", r["cleansheet_pts"]),
        "DEFCON": r["defcon_pts"],
        "Saves": r.get("save_pts_cal", r["save_pts"]),
        "Bonus": r.get("bonus_pts_cal", r["bonus_pts"]),
        "Cards": r.get("card_pts", 0.0),
        "Goals conceded": r.get("conceded_pts_cal", r["conceded_pts"]),
    }
    channels = {k: round(float(v), 3) for k, v in channels.items() if abs(v) > 1e-9}
    return {
        "web_name": r["web_name"],
        "minutes_factor": round(float(r["minutes_factor"]), 2),
        "fixture_attack_mult": round(float(r["fixture_attack_mult"]), 2),
        "fixture_defence_mult": round(float(r["fixture_defence_mult"]), 2),
        "channels": channels,
        "total": round(sum(channels.values()), 2),
    }


def fixture_radar(config: Optional[dict] = None, n_gw: int = 5) -> dict:
    """Difficulty grid for the current squad's clubs, next N gameweeks —
    already-computed fixtures.parquet, no new modelling."""
    config = config or load_config()
    bootstrap = live_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    entry_id = config["fpl"]["entry_id"]
    completed_event = None
    for e in bootstrap["events"]:
        if e.get("finished") or e.get("is_previous") or e.get("is_current"):
            completed_event = e["id"]
    picks = live_entry_picks(entry_id, completed_event or 1)["picks"]
    my_teams = sorted({elements[p["element"]]["team"] for p in picks})

    fx = pd.read_parquet(PROCESSED_DIR / "fixtures.parquet")
    upcoming_events = project_mod.next_n_gameweeks(n_gw)

    grid = []
    for tm in my_teams:
        cells = []
        for gw in upcoming_events:
            r = fx[(fx["team"] == tm) & (fx["event"] == gw)]
            if r.empty:
                cells.append({"event": gw, "label": "-", "difficulty": None})
                continue
            rr = r.iloc[0]
            venue = "H" if rr["is_home"] else "A"
            cells.append({
                "event": gw,
                "label": f"{teams[rr['opponent']]}({venue})",
                "difficulty": int(rr["difficulty"]),
            })
        grid.append({"team": teams[tm], "cells": cells})
    return {"events": upcoming_events, "grid": grid}


def live_entry_transfers(entry_id: int) -> list[dict]:
    return _get_json(f"{API_BASE}/entry/{entry_id}/transfers/")


def live_event_live(event: int) -> dict:
    return _get_json(f"{API_BASE}/event/{event}/live/")


def history_view(config: Optional[dict] = None) -> dict:
    """GW-by-GW points/rank, chip log, and a transfer log with an honest
    'since transfer' regret proxy (actual points scored by each side since
    the swap — NOT the model's point-in-time projection, which would need
    the history archive extended to capture per-transfer projections; not
    built yet, see the design doc). All from the live entry endpoints —
    FPL keeps this itself, no local capture pipeline needed for it."""
    config = config or load_config()
    entry_id = config["fpl"]["entry_id"]
    bootstrap = live_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}

    hist = live_entry_history(entry_id)
    season_history = hist.get("current", [])
    chips = hist.get("chips", [])

    transfers_raw = live_entry_transfers(entry_id)
    transfers = []
    for t in transfers_raw:
        out_id, in_id = t["element_out"], t["element_in"]
        # "Since transfer": sum actual total_points from event-live for every
        # finished event from the transfer's event onward. Cheap because
        # season_history tells us which events exist; event-live for a
        # small number of events is fine for a demo, would need caching for
        # a real deploy.
        def _pts_since(pid: int, from_event: int) -> Optional[int]:
            total = 0
            for e in season_history:
                ev = e["event"]
                if ev < from_event:
                    continue
                try:
                    live = live_event_live(ev)
                except Exception:
                    continue
                row = next((x for x in live["elements"] if x["id"] == pid), None)
                if row:
                    total += row["stats"]["total_points"]
            return total

        transfers.append({
            "event": t["event"],
            "time": t["time"],
            "out_name": elements.get(out_id, {}).get("web_name", f"#{out_id}"),
            "in_name": elements.get(in_id, {}).get("web_name", f"#{in_id}"),
            "out_price": t.get("element_out_cost", 0) / 10,
            "in_price": t.get("element_in_cost", 0) / 10,
            "out_pts_since": _pts_since(out_id, t["event"]),
            "in_pts_since": _pts_since(in_id, t["event"]),
        })

    return {
        "entry_id": entry_id,
        "season_history": season_history,
        "chips": chips,
        "transfers": transfers,
    }


def squad_snapshot(event: int, config: Optional[dict] = None) -> dict:
    """The full XI/bench/captain exactly as picked for a past gameweek,
    with each player's ACTUAL points scored that GW."""
    config = config or load_config()
    entry_id = config["fpl"]["entry_id"]
    bootstrap = live_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    picks_resp = live_entry_picks(entry_id, event)
    picks = picks_resp["picks"]
    try:
        live = live_event_live(event)
        live_pts = {x["id"]: x["stats"]["total_points"] for x in live["elements"]}
    except Exception:
        live_pts = {}

    rows = []
    for p in picks:
        eid = p["element"]
        e = elements.get(eid, {})
        base_pts = live_pts.get(eid, 0)
        rows.append({
            "id": eid,
            "web_name": e.get("web_name", f"#{eid}"),
            "team": teams.get(e.get("team"), "?"),
            "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(e.get("element_type"), "?"),
            "is_captain": p["is_captain"],
            "is_vice": p["is_vice_captain"],
            "multiplier": p["multiplier"],
            "points": base_pts * p["multiplier"],
            "raw_points": base_pts,
        })
    rows.sort(key=lambda r: (r["multiplier"] == 0, {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}[r["position"]]))
    return {
        "event": event,
        "entry_history": picks_resp.get("entry_history", {}),
        "active_chip": picks_resp.get("active_chip"),
        "rows": rows,
    }


def insights_view(config: Optional[dict] = None, league_id: Optional[int] = None) -> dict:
    """Ownership/differential + captaincy view. Overall-ownership proxy
    (selected_by_percent, most_captained — both public, no league needed)
    always available; real mini-league EO only if a league_id is given
    (see live_league_eo)."""
    config = config or load_config()
    bootstrap = live_bootstrap()
    elements = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    entry_id = config["fpl"]["entry_id"]

    completed_event = None
    for e in bootstrap["events"]:
        if e.get("finished") or e.get("is_previous") or e.get("is_current"):
            completed_event = e["id"]
    event = completed_event or 1
    picks = live_entry_picks(entry_id, event)["picks"]
    my_ids = {p["element"] for p in picks}
    my_captain = next((p["element"] for p in picks if p["is_captain"]), None)

    rows = []
    for p in picks:
        e = elements[p["element"]]
        rows.append({
            "id": e["id"],
            "web_name": e["web_name"],
            "team": teams[e["team"]],
            "position": {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}[e["element_type"]],
            "selected_by_percent": float(e["selected_by_percent"]),
            "is_captain": p["is_captain"],
        })
    # Differentials: your players with low overall ownership — a genuine
    # edge signal (high ceiling if they haul, since few rivals have them).
    differentials = sorted(
        [r for r in rows if r["selected_by_percent"] < 10],
        key=lambda r: r["selected_by_percent"],
    )[:5]
    # Template (overowned): your players nearly everyone else also owns —
    # not wrong to hold, but they won't move your rank relative to the field.
    template = sorted(rows, key=lambda r: -r["selected_by_percent"])[:5]

    most_captained_id = bootstrap["events"][event - 1]["most_captained"] if event <= len(bootstrap["events"]) else None
    most_captained = elements.get(most_captained_id, {}).get("web_name") if most_captained_id else None
    my_captain_name = elements.get(my_captain, {}).get("web_name") if my_captain else None

    league_eo = None
    league_error = None
    if league_id:
        try:
            league_eo = mini_league_eo(league_id, event, elements)
        except Exception as exc:
            league_error = str(exc)

    return {
        "event": event,
        "rows": sorted(rows, key=lambda r: -r["selected_by_percent"]),
        "differentials": differentials,
        "template": template,
        "most_captained": most_captained,
        "my_captain": my_captain_name,
        "captain_is_template": my_captain == most_captained_id,
        "league_eo": league_eo,
        "league_error": league_error,
    }


def mini_league_eo(league_id: int, event: int, elements: dict) -> dict:
    """Real effective ownership within a mini-league: pull every entry's
    picks for this GW and count captaincy/ownership across the actual
    rivals you're competing with, not the whole game. Capped at the first
    page of standings (typically 50 entries) to keep this a demo-scale
    call, not an unbounded fan-out."""
    standings = _get_json(f"{API_BASE}/leagues-classic/{league_id}/standings/")
    league_name = standings["league"]["name"]
    entries = standings["standings"]["results"][:50]

    ownership: dict[int, int] = {}
    captaincy: dict[int, int] = {}
    n = 0
    for entry in entries:
        try:
            picks = live_entry_picks(entry["entry"], event)["picks"]
        except Exception:
            continue
        n += 1
        for p in picks:
            if p["multiplier"] == 0:
                continue
            ownership[p["element"]] = ownership.get(p["element"], 0) + 1
            if p["is_captain"]:
                captaincy[p["element"]] = captaincy.get(p["element"], 0) + 1

    top_owned = sorted(ownership.items(), key=lambda kv: -kv[1])[:8]
    top_captained = sorted(captaincy.items(), key=lambda kv: -kv[1])[:8]
    return {
        "league_name": league_name,
        "n_rivals": n,
        "top_owned": [
            {"web_name": elements.get(pid, {}).get("web_name", f"#{pid}"), "pct": round(100 * c / n, 1)}
            for pid, c in top_owned if n
        ],
        "top_captained": [
            {"web_name": elements.get(pid, {}).get("web_name", f"#{pid}"), "pct": round(100 * c / n, 1)}
            for pid, c in top_captained if n
        ],
    }
