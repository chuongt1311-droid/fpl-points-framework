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
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from fpl.project import project as project_mod

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "output"
PROCESSED_DIR = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw"
DASHBOARD_DIR = ROOT / "dashboard"

N_HORIZON = 5
N_TICKER = 6
N_RADAR = 8

_REC_RE = re.compile(r"^gw(\d+)_recommendations\.json$")


def r2(x):
    return round(float(x), 2) if pd.notna(x) else None


def select_target_gameweek(output_dir: Path, gameweeks: list[int]) -> int:
    """The gameweek the dashboard should show: the upcoming one if the
    pipeline has already solved it, else the most recent one it has (the
    Decide step may not have run since the last GW finished). Was hardcoded
    to 1 — PROJECT_LOG §18."""
    solved = sorted(
        int(m.group(1))
        for p in output_dir.glob("gw*_recommendations.json")
        if (m := _REC_RE.match(p.name))
    )
    if not solved:
        raise FileNotFoundError(
            f"No gw*_recommendations.json under {output_dir} — run the Decide step first."
        )
    if gameweeks and gameweeks[0] in solved:
        return gameweeks[0]
    return solved[-1]


def parse_deadline(bootstrap: dict, gameweek: int) -> Optional[str]:
    """The ISO deadline for `gameweek` from a bootstrap-static payload, or
    None if that event isn't present (bootstrap not refreshed yet)."""
    for event in bootstrap.get("events", []):
        if event.get("id") == gameweek:
            return event.get("deadline_time")
    return None


def load_transfer_recommendation(output_dir: Path, gameweek: int) -> Optional[dict]:
    """gw{n}_transfers.json if a manual `fpl.decide.transfers` run has
    committed one for this gameweek; None otherwise (it isn't in weekly.yml
    yet — HANDOFF §9)."""
    path = output_dir / f"gw{gameweek}_transfers.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    config = project_mod.load_config()

    # ---- which gameweek are we showing? (was hardcoded to 1) ----
    gameweeks = project_mod.next_n_gameweeks(N_RADAR)
    target_gw = select_target_gameweek(OUTPUT_DIR, gameweeks)
    if target_gw not in gameweeks:  # pipeline is behind — anchor everything on what we have
        gameweeks = list(range(target_gw, target_gw + N_RADAR))

    # ---- recommendations + model health: read as-is, never recompute ----
    squad = json.loads(
        (OUTPUT_DIR / f"gw{target_gw}_recommendations.json").read_text(encoding="utf-8")
    )
    health = json.loads((OUTPUT_DIR / "model_health.json").read_text(encoding="utf-8"))
    transfer = load_transfer_recommendation(OUTPUT_DIR, target_gw)

    bootstrap_path = RAW_DIR / "bootstrap_static.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8")) if bootstrap_path.exists() else {}
    deadline_utc = parse_deadline(bootstrap, target_gw)

    # ---- Bakeoff tab (v3 plan §9 Phase F, previously unbuilt) — every
    # committed model_health*.json, read as-is. M0's own file has no
    # suffix (the production/champion path); every challenger writes to
    # model_health_{model}.json (fpl.evaluate.backtest's own convention).
    # M5 (ensemble) has no committed artefact — its held-out-eval numbers
    # were a one-off script run (see docs/DECISION_RULE.md), never
    # written to a file this script could read — so it's surfaced as
    # static informational text below, same "not yet computed live"
    # honesty pattern as the Chip Planner tab, not fabricated from here.
    champion_model = health["model"]
    bakeoff_models = [{
        "model": health["model"], "label": "M0 — rules_v1", "is_champion": True,
        "status_note": "Current champion (docs/DECISION_RULE.md).",
        "top40_rank_correlation": r2(health["top40_rank_correlation"]),
        "overall_rmse": r2(health["overall_rmse"]),
        "n_players_tested": health["n_players_tested"],
    }]
    challenger_labels = {
        "m2_xg": ("M2 — xG blend", "Backtest-favourable, not yet live-evaluated."),
        "m3_understat": ("M3 — Understat npxG blend", "Does not currently beat M2 at any tested k_npxg."),
    }
    for path in sorted(OUTPUT_DIR.glob("model_health_*.json")):
        challenger = json.loads(path.read_text(encoding="utf-8"))
        label, note = challenger_labels.get(challenger["model"], (challenger["model"], ""))
        bakeoff_models.append({
            "model": challenger["model"], "label": label, "is_champion": False,
            "status_note": note,
            "top40_rank_correlation": r2(challenger["top40_rank_correlation"]),
            "overall_rmse": r2(challenger["overall_rmse"]),
            "n_players_tested": challenger["n_players_tested"],
        })

    # ---- hindsight / Week in Review (spec §3.6) — null until a gameweek
    # has actually finished; fpl.evaluate.hindsight writes this file, this
    # script only reads it, same committed-artefact contract as everything
    # else here. Dashboard shows an honest placeholder when null.
    hindsight_path = OUTPUT_DIR / f"hindsight_gw{squad['gameweek']}.json"
    hindsight = json.loads(hindsight_path.read_text(encoding="utf-8")) if hindsight_path.exists() else None

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
    proj_path = PROCESSED_DIR.parent / "projections" / f"gw{target_gw}.parquet"
    if not proj_path.exists():
        proj_path = PROCESSED_DIR.parent / "projections" / f"gw{horizon_gws[0]}.parquet"
    proj = pd.read_parquet(proj_path)
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
        "No xG/xA regression layer in the LIVE champion path — M0 rules_v1 (what this dashboard shows) projects on raw realised output, so hot streaks are over-projected and cold streaks under-projected. An xG-blended model (M2) exists and is backtest-favourable, but is not live — see the Bakeoff tab and docs/DECISION_RULE.md.",
        "New-signing cold start — players without 3 FPL appearances (or under 450 historical minutes) get a prior-only projection flagged low confidence.",
        "No price-change modelling — team value growth is not optimised for.",
        "No ownership/differential strategy — the model maximises raw points, not rank.",
        "Rotation risk is backward-looking — start probability lags a manager's actual current thinking, with no notion of fixture congestion.",
        "Set-piece duties are not explicitly modelled beyond what's implicit in historical output.",
        "FDR is derived from season-long team strength ratings, which lag genuine early-season form — GW1-5 projections are the least reliable the model will ever produce.",
    ]

    payload = {
        "meta": {
            "season": config["season"],
            "gameweek": squad["gameweek"],
            "deadline_utc": deadline_utc,
            "generated_note": "Static snapshot — regenerate via scripts/build_dashboard_data.py after any pipeline re-run.",
        },
        "squad": squad,
        "transfer": transfer,
        "model_health": health,
        "bakeoff": {
            "champion_model": champion_model,
            "models": bakeoff_models,
            "ensemble_note": (
                "M5 (ensemble, M0+M2+M3 weighted out-of-sample) has no committed "
                "artefact to read here — its held-out-eval numbers were a one-off "
                "script run, not a pipeline output. Reported for context, not "
                "recomputed: on a held-out eval half, M2 alone scores 0.5966 top-40 "
                "rank correlation; the full M0+M2+M3 ensemble scores only 0.5266, "
                "and even an M0+M2-only ensemble scores 0.5593 — both worse than "
                "M2 alone. See docs/DECISION_RULE.md."
            ),
        },
        "hindsight": hindsight,
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
        # Phase G (spec §7.4): history.json is produced separately by
        # scripts/build_history_data.py. Substituted here so index.html
        # stays a single self-contained offline file. ALWAYS substitutes —
        # the literal "null" when history.json is absent — because leaving
        # the placeholder in place would emit `const HISTORY = ;`, a syntax
        # error that takes down the whole page.
        history_path = DASHBOARD_DIR / "history.json"
        history_json = (
            history_path.read_text(encoding="utf-8") if history_path.exists() else "null"
        )
        html = html.replace("/*__HISTORY__*/", history_json)
        # my_team.json — produced separately by scripts/build_my_team_data.py
        # (needs a live entry pull + optional private my_team.json). Same
        # always-substitute rule: "null" if absent, never a bare placeholder.
        my_team_path = DASHBOARD_DIR / "my_team.json"
        my_team_json = (
            my_team_path.read_text(encoding="utf-8") if my_team_path.exists() else "null"
        )
        html = html.replace("/*__MYTEAM__*/", my_team_json)
        index_path = DASHBOARD_DIR / "index.html"
        index_path.write_text(html, encoding="utf-8")
        print(f"Wrote {index_path} ({index_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
