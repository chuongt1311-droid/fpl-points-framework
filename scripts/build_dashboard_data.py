"""
build_dashboard_data.py — one-off data-prep script for the Phase 4 dashboard.

Reads ONLY existing pipeline outputs (data/output/*.json,
data/projections/*.parquet, data/processed/*.parquet) plus the pipeline's
own already-written functions (fpl.project.project) to add the one derived
view those outputs don't already contain — the per-channel points
breakdown ("why is the model recommending this guy") — which is computed
the exact same way project.py computes it internally, not re-derived.

This script does NOT change the model. It writes one static JSON file
(dashboard/data.json) that the dashboard HTML embeds and reads. Re-run
this after any pipeline re-run to refresh the dashboard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fpl.project import project as project_mod

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "output"
PROCESSED_DIR = ROOT / "data" / "processed"
DASHBOARD_DIR = ROOT / "dashboard"

N_HORIZON = 5
N_TICKER = 6
N_RADAR = 8


def r2(x):
    return round(float(x), 2) if pd.notna(x) else None


def main():
    config = project_mod.load_config()

    # ---- recommendations + model health: read as-is, never recompute ----
    squad = json.loads((OUTPUT_DIR / "gw1_recommendations.json").read_text(encoding="utf-8"))
    health = json.loads((OUTPUT_DIR / "model_health.json").read_text(encoding="utf-8"))

    # ---- teams ----
    players_raw = pd.read_parquet(PROCESSED_DIR / "players.parquet")
    teams_df = players_raw[["team", "team_name", "team_short_name"]].drop_duplicates().sort_values("team")
    team_short = dict(zip(teams_df["team"], teams_df["team_short_name"]))
    teams = [
        {"id": int(r.team), "name": r.team_name, "short": r.team_short_name}
        for r in teams_df.itertuples()
    ]

    # ---- channel breakdown, via the pipeline's own function (not reimplemented) ----
    players_inputs = project_mod.build_player_inputs(config)
    fixture_mults_full = project_mod.fixtures_mod.compute_fixture_multipliers()

    gameweeks = project_mod.next_n_gameweeks(N_RADAR)
    horizon_gws = gameweeks[:N_HORIZON]
    ticker_gws = gameweeks[:N_TICKER]
    radar_gws = gameweeks[:N_RADAR]

    fixture_mults_h = fixture_mults_full[fixture_mults_full["event"].isin(horizon_gws)]
    per_fixture = project_mod.compute_channel_pts_per_fixture(players_inputs, fixture_mults_h, config)

    channel_cols = [
        "appearance_pts", "goal_pts", "assist_pts", "cleansheet_pts",
        "defcon_pts", "save_pts", "bonus_pts", "card_pts", "conceded_pts",
    ]
    per_gw_channels = per_fixture.groupby(["id", "event"], as_index=False)[channel_cols].sum()

    weights = config["horizon"]["decay_weights"]
    weight_map = {gw: weights[i] for i, gw in enumerate(horizon_gws) if i < len(weights)}
    per_gw_channels["weight"] = per_gw_channels["event"].map(weight_map).fillna(0.0)
    for c in channel_cols:
        per_gw_channels[c + "_w"] = per_gw_channels[c] * per_gw_channels["weight"]
    channel_totals = per_gw_channels.groupby("id", as_index=False)[[c + "_w" for c in channel_cols]].sum()
    channel_totals.columns = ["id"] + channel_cols

    # ---- per-GW projections (already-written artifact, read not recomputed) ----
    gw1_path = PROCESSED_DIR.parent / "projections" / f"gw{horizon_gws[0]}.parquet"
    proj = pd.read_parquet(gw1_path)
    weighted = project_mod.weighted_horizon_total(proj, config)

    baseline_extra = players_raw[[
        "id", "status", "selected_by_percent", "new_signing_flag", "form", "total_points",
    ]]

    pool = weighted.merge(baseline_extra, on="id", how="left")
    pool = pool.merge(channel_totals, on="id", how="left")

    proj_by_player = {pid: g.sort_values("event")[["event", "xpts", "n_fixtures"]].to_dict("records")
                       for pid, g in proj.groupby("id")}

    # ---- fixture ticker source (next N_TICKER gameweeks per team) ----
    ticker_mults = fixture_mults_full[fixture_mults_full["event"].isin(ticker_gws)].copy()
    ticker_mults["opponent_short"] = ticker_mults["opponent"].map(team_short)
    ticker_by_team = {}
    for team_id, g in ticker_mults.groupby("team"):
        g = g.sort_values("event")
        ticker_by_team[int(team_id)] = [
            {
                "event": int(row.event),
                "opp": row.opponent_short,
                "home": bool(row.is_home),
                "difficulty": int(row.difficulty),
                "attack_mult": r2(row.fixture_attack_mult),
                "defence_mult": r2(row.fixture_defence_mult),
                "defcon_mult": r2(row.fixture_defcon_mult),
            }
            for row in g.itertuples()
        ]

    # ---- players payload ----
    players_out = []
    for row in pool.itertuples():
        pid = row.id
        players_out.append({
            "id": int(pid),
            "web_name": row.web_name,
            "position": row.position,
            "team": int(row.team),
            "team_short": team_short.get(row.team, "?"),
            "price": r2(row.price),
            "confidence": row.confidence,
            "status": row.status,
            "selected_by_percent": r2(row.selected_by_percent) if pd.notna(row.selected_by_percent) else None,
            "new_signing": bool(row.new_signing_flag) if pd.notna(row.new_signing_flag) else False,
            "form": r2(row.form) if pd.notna(row.form) else None,
            "total_points_prev": int(row.total_points) if pd.notna(row.total_points) else None,
            "weighted_xpts": r2(row.weighted_xpts),
            "total_xpts": r2(row.total_xpts),
            "gw": proj_by_player.get(pid, []),
            "channels": {c: r2(getattr(row, c)) for c in channel_cols},
            "fixtures": ticker_by_team.get(int(row.team), []),
        })

    # ---- fixture radar (team x next N_RADAR gameweeks) ----
    radar_mults = fixture_mults_full[fixture_mults_full["event"].isin(radar_gws)].copy()
    radar_mults["opponent_short"] = radar_mults["opponent"].map(team_short)
    radar_teams = []
    for team_id, g in radar_mults.groupby("team"):
        g = g.sort_values("event")
        radar_teams.append({
            "team": int(team_id),
            "short": team_short.get(team_id, "?"),
            "cells": [
                {
                    "event": int(row.event),
                    "opp": row.opponent_short,
                    "home": bool(row.is_home),
                    "difficulty": int(row.difficulty),
                    "attack_mult": r2(row.fixture_attack_mult),
                    "defence_mult": r2(row.fixture_defence_mult),
                    "defcon_mult": r2(row.fixture_defcon_mult),
                }
                for row in g.itertuples()
            ],
        })

    known_limitations = [
        "No BPS simulation — bonus points come from historical rate, not the 32 underlying Opta stats. Systematically under-projects bonus-magnet players.",
        "No xG/xA regression layer — players are projected on raw output, so hot streaks are over-projected and cold streaks under-projected.",
        "New-signing cold start — players without 3 FPL appearances (or under 450 historical minutes) get a prior-only projection flagged low confidence.",
        "No price-change modelling — team value growth is not optimised for.",
        "No ownership/differential strategy — the model maximises raw points, not rank.",
        "Rotation risk is backward-looking — start probability lags a manager's actual current thinking, with no notion of fixture congestion.",
        "Set-piece duties are not explicitly modelled beyond what's implicit in historical output.",
        "FDR is derived from season-long team strength ratings, which lag genuine early-season form — GW1-5 projections are the least reliable the model will ever produce.",
        "KNOWN UNFIXED BUG: conceded_pts currently uses the wrong-direction fixture multiplier (fixture_defence_mult instead of fixture_defcon_mult) — every GK/DEF goals-conceded penalty is currently pointing backwards relative to fixture difficulty. See HANDOFF.md §5 item 1.",
        "KNOWN UNFIXED GAP: DEFCON confidence is not surfaced separately — a player can show confidence=high overall while their DEFCON rate specifically is a tier-prior guess.",
    ]

    payload = {
        "meta": {
            "season": config["season"],
            "gameweek": squad["gameweek"],
            "deadline_utc": "2026-08-21T17:30:00Z",
            "generated_note": "Static snapshot — regenerate via scripts/build_dashboard_data.py after any pipeline re-run.",
        },
        "squad": squad,
        "model_health": health,
        "known_limitations": known_limitations,
        "teams": teams,
        "players": players_out,
        "fixture_radar": {"events": radar_gws, "teams": radar_teams},
    }

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DASHBOARD_DIR / "data.json"
    data_json = json.dumps(payload, separators=(",", ":"))
    out_path.write_text(data_json, encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB), {len(players_out)} players")

    # ---- stitch the static, fully self-contained dashboard HTML ----
    template_path = DASHBOARD_DIR / "template.html"
    if template_path.exists():
        html = template_path.read_text(encoding="utf-8")
        html = html.replace("/*__DATA__*/", data_json)
        index_path = DASHBOARD_DIR / "index.html"
        index_path.write_text(html, encoding="utf-8")
        print(f"Wrote {index_path} ({index_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
