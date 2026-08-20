"""
project.py — PROJECT layer. Assembles xPts(p, g) for GW+1 ... GW+5, per
plan §4.1:

    xPts(p, g) = Σ over fixtures f in g of:
        appearance_pts(p)
      + goal_pts(p)       × fixture_attack_mult(p, f)
      + assist_pts(p)     × fixture_attack_mult(p, f)
      + cleansheet_pts(p) × fixture_defence_mult(p, f)
      + defcon_pts(p)     × fixture_defcon_mult(p, f)
      + save_pts(p)       × fixture_defence_mult(p, f)   [GK only]
      + bonus_pts(p)
      − card_pts(p)
      − conceded_pts(p)   × fixture_concede_mult(p, f)   [NOT defence_mult —
                                                          a penalty moves the
                                                          opposite way; see
                                                          compute_channel_pts_
                                                          per_fixture]
      all multiplied by: minutes_factor(p)

Summing over fixtures (not a special DGW/BGW case) is why a Double
Gameweek naturally produces ~2x an average week's xPts here, and a Blank
Gameweek naturally sums to 0 — see plan §4.1.

DELIBERATE v1 INTERPRETATION (documented, not hidden — the plan's formula
layout is ambiguous on one point): appearance_pts and cleansheet_pts each
already embed their own P(60+ mins) term in their definition (§4.2), because
real FPL scoring requires 60+ minutes for both. Re-applying the outer
minutes_factor(p) to those two terms as well would double-discount the same
probability. So: minutes_factor(p) is treated as the P(60+ mins) estimate
used INSIDE appearance_pts/cleansheet_pts, and is applied as a separate
scaling factor to every OTHER term (goal/assist/defcon/save/bonus/card/
conceded), none of which has its own embedded playing-time term. One
factor, applied once per term — never twice.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.project import baseline as baseline_mod
from fpl.project import defcon as defcon_mod
from fpl.project import fixtures as fixtures_mod
from fpl.project import minutes as minutes_mod
from fpl.transform import build_players

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
PROJECTIONS_DIR = Path(__file__).resolve().parents[2] / "data" / "projections"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"

# Spec §4.2: which raw *_pts column each calibration_factors[position][channel]
# multiplier applies to. defcon_pts, card_pts, appearance_pts are
# deliberately NOT calibrated — see backtest.py's CALIBRATION_CHANNELS
# docstring for why (defcon has no leak-free backtest season; cards are
# small/noisy; appearance follows deterministically from minutes_factor).
CHANNEL_TO_CALIBRATION_KEY = {
    "goal_pts": "goal", "assist_pts": "assist", "cleansheet_pts": "cleansheet",
    "bonus_pts": "bonus", "save_pts": "save", "conceded_pts": "conceded",
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def next_n_gameweeks(n: int) -> list[int]:
    path = RAW_DIR / "bootstrap_static.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    events = sorted(data["events"], key=lambda e: e["id"])
    unfinished = [e["id"] for e in events if not e.get("finished")]
    return unfinished[:n]


def build_player_inputs(config: dict) -> pd.DataFrame:
    """One row per player with every input the formula needs."""
    players = build_players.build_players()
    base = baseline_mod.build_baseline(players, config)
    dc = defcon_mod.build_defcon(players, config)
    mins = minutes_mod.compute_minutes_factor(players, config)

    keep_base_cols = [
        "id", "code", "web_name", "position", "price", "team", "confidence", "confidence_weight",
        "goals_scored_per90", "assists_per90", "clean_sheets_per90",
        "bonus_per90", "saves_per90", "goals_conceded_per90",
        "yellow_cards_per90", "red_cards_per90",
        "team_cs_rate", "team_goals_conceded_per90",
        "historical_minutes", "weighted_minutes",
    ]
    out = base[keep_base_cols].merge(dc[["id", "defcon_rate"]], on="id", how="left")
    out = out.merge(mins[["id", "minutes_factor", "status"]], on="id", how="left")

    # League-average fallback for teams with no bridged history (newly
    # promoted — see baseline.py) — a neutral "unknown, assume average"
    # prior rather than fabricating an optimistic or pessimistic bias.
    league_avg_cs = out["team_cs_rate"].mean()
    league_avg_conceded = out["team_goals_conceded_per90"].mean()
    out["team_cs_rate"] = out["team_cs_rate"].fillna(league_avg_cs)
    out["team_goals_conceded_per90"] = out["team_goals_conceded_per90"].fillna(league_avg_conceded)

    return out


def load_calibration_factors() -> dict:
    """
    Spec §4.2: per-(position, channel) multiplier correcting measured
    under-prediction, fit on the repaired backtest (fpl.evaluate.backtest,
    spec §3.5) — 'backtest for the initial value' per spec's recommendation,
    live rolling window taking over once ~8 gameweeks exist (not built yet).

    Returns {} (every factor defaults to a 1.0 no-op) if model_health.json
    doesn't exist — calibration must degrade gracefully on a fresh checkout
    that hasn't run the backtest, not become a hard dependency.
    """
    path = OUTPUT_DIR / "model_health.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("calibration_factors", {})


def apply_calibration(df: pd.DataFrame, calibration: dict) -> pd.DataFrame:
    """
    Explicit, inspectable final step (decision D11 — FPL_V2_DESIGN.md):
    applied AFTER the raw channel columns are computed, never folded into
    the channel rates themselves, so the uncorrected number stays visible
    beside the corrected one (raw `goal_pts` next to calibrated
    `goal_pts_cal`, etc.) — the size of the correction is always legible,
    and 'a calibration factor that starts growing' (a warning sign per D11)
    is something you can actually go look at.
    """
    for pts_col, channel_key in CHANNEL_TO_CALIBRATION_KEY.items():
        if pts_col not in df.columns:
            continue  # defensive: production always computes all six first, but don't assume it
        factor_by_position = {
            pos: calibration.get(pos, {}).get(channel_key, 1.0) for pos in ["GK", "DEF", "MID", "FWD"]
        }
        df[f"{pts_col}_cal"] = df[pts_col] * df["position"].map(factor_by_position).fillna(1.0)
    return df


def compute_channel_pts_per_fixture(
    players_inputs: pd.DataFrame, fixture_mults: pd.DataFrame, config: dict, calibration: Optional[dict] = None,
) -> pd.DataFrame:
    """
    One row per (player, fixture) — players_inputs joined to every fixture
    their team plays, with every channel's raw (pre-minutes-factor) points
    and the appropriate fixture multiplier already applied where relevant.
    `calibration` defaults to load_calibration_factors() if not passed —
    accepting it as a parameter lets callers (tests, the dashboard's
    channel-breakdown script) pin a specific value instead of reading disk.
    """
    calibration = calibration if calibration is not None else load_calibration_factors()
    rules = config["scoring_rules"]
    goal_mult = config["position_multipliers"]["goals"]
    assist_mult = config["position_multipliers"]["assists_flat"]
    cs_value = config["position_multipliers"]["clean_sheet_value"]
    defcon_pts_value = config["defcon"]["points"]
    bench_cameo_rate = config["minutes"]["bench_cameo_rate"]

    df = players_inputs.merge(fixture_mults, on="team", how="inner")

    df["goal_mult"] = df["position"].map(goal_mult)
    df["cs_value"] = df["position"].map(cs_value)

    # appearance_pts: embeds P(60+) via minutes_factor directly (see module
    # docstring) — NOT scaled again by the outer minutes_factor multiply.
    # p1to59 must be gated on actual availability separately from the
    # minutes_factor VALUE: a healthy fringe player with minutes_factor=0.1
    # genuinely has residual sub-appearance probability, but a player with
    # minutes_factor=0 because they're injured/suspended/unavailable has
    # ZERO chance of any minutes at all — (1-0)*bench_cameo_rate would
    # otherwise hand every injured player a phantom ~0.3 appearance_pts.
    unavailable = df["status"].isin(["i", "s", "u"])
    p60 = df["minutes_factor"]
    p1to59 = ((1 - p60) * bench_cameo_rate).where(~unavailable, 0.0)
    df["appearance_pts"] = rules["appearance_60plus"] * p60 + rules["appearance_1to59"] * p1to59

    # cleansheet_pts: team-level P(CS), fixture-adjusted, embeds P(60+) too.
    df["cleansheet_pts"] = df["team_cs_rate"] * df["fixture_defence_mult"] * df["cs_value"] * p60

    # Terms scaled by the outer minutes_factor (no embedded P(60+) of their own):
    df["goal_pts"] = df["goals_scored_per90"] * df["goal_mult"] * df["fixture_attack_mult"]
    df["assist_pts"] = df["assists_per90"] * assist_mult * df["fixture_attack_mult"]
    df["defcon_pts"] = df["defcon_rate"] * defcon_pts_value * df["fixture_defcon_mult"]
    df["save_pts"] = (df["saves_per90"] / rules["saves_per_point"]) * df["fixture_defence_mult"]
    df["save_pts"] = df["save_pts"].where(df["position"] == "GK", 0.0)
    df["bonus_pts"] = df["bonus_per90"]
    df["card_pts"] = df["yellow_cards_per90"] * rules["yellow_card"] + df["red_cards_per90"] * rules["red_card"]
    # conceded penalty only applies to GK/DEF, per plan §4.2.
    #
    # DIRECTION (fixed 2026-08-20): this term is a PENALTY, so it must move
    # OPPOSITE to cleansheet_pts, not with it. fixture_defence_mult is high
    # when a clean sheet is LIKELY (weak attacking opponent) — multiplying a
    # negative penalty by it made an easy fixture produce a BIGGER goals-
    # conceded punishment. Every GK/DEF projection's conceded term was
    # pointing backwards relative to fixture difficulty.
    # Magnitude, measured on real GW1-5 data at league-average 1.38 conceded
    # /90: up to 1.03 pts per fixture per GK/DEF at the multiplier bounds,
    # ~0.5 at the values actually present pre-season.
    df["conceded_pts"] = (
        -1.0 / rules["conceded_per_point"] * df["team_goals_conceded_per90"] * df["fixture_concede_mult"]
    )
    df["conceded_pts"] = df["conceded_pts"].where(df["position"].isin(["GK", "DEF"]), 0.0)

    # Spec §4.2: calibration is the FINAL step, on top of the raw channels
    # above — every *_pts column computed so far is left untouched and
    # stays readable; *_pts_cal is what xpts_fixture actually sums.
    df = apply_calibration(df, calibration)

    minutes_scaled = (
        df["goal_pts_cal"] + df["assist_pts_cal"] + df["defcon_pts"] + df["save_pts_cal"]
        + df["bonus_pts_cal"] + df["card_pts"] + df["conceded_pts_cal"]
    ) * df["minutes_factor"]

    df["xpts_fixture"] = df["appearance_pts"] + df["cleansheet_pts_cal"] + minutes_scaled
    return df


def project_gameweeks(n_gameweeks: int = 5, config: Optional[dict] = None) -> pd.DataFrame:
    """
    Returns one row per (player, gameweek) for the next n_gameweeks, with
    xpts (summed over that GW's fixtures — 0 for a blank, ~2x for a double)
    and, once all n gameweeks are present, a decay-weighted total.
    """
    config = config or load_config()
    gameweeks = next_n_gameweeks(n_gameweeks)
    if not gameweeks:
        raise RuntimeError("No upcoming gameweeks found in bootstrap-static events.")

    players_inputs = build_player_inputs(config)
    fixture_mults = fixtures_mod.compute_fixture_multipliers()
    fixture_mults = fixture_mults[fixture_mults["event"].isin(gameweeks)]

    per_fixture = compute_channel_pts_per_fixture(players_inputs, fixture_mults, config)

    per_gw = per_fixture.groupby(["id", "event"], as_index=False).agg(
        xpts=("xpts_fixture", "sum"),
        n_fixtures=("fixture_id", "nunique"),
    )

    # Players whose team has no fixture at all in a given GW (blank) don't
    # appear in the groupby above — add them back in explicitly at xpts=0,
    # so every player has a row for every one of the n gameweeks.
    all_ids = players_inputs[["id", "web_name", "position", "team", "price", "confidence"]]
    full_index = all_ids.merge(pd.DataFrame({"event": gameweeks}), how="cross")
    result = full_index.merge(per_gw, on=["id", "event"], how="left")
    result["xpts"] = result["xpts"].fillna(0.0)
    result["n_fixtures"] = result["n_fixtures"].fillna(0).astype(int)

    PROJECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    gw1 = gameweeks[0]
    result.to_parquet(PROJECTIONS_DIR / f"gw{gw1}.parquet", index=False)

    return result


def weighted_horizon_total(projections: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """Collapses the (player, gameweek) table to one row per player, with a
    decay-weighted total across whichever gameweeks are present, per plan
    §4.3's horizon weights [1.0, 0.85, 0.7, 0.55, 0.4] for GW+1...GW+5.

    ALSO returns `next_gw_xpts` — the FIRST gameweek's xpts on its own,
    undecayed. These two numbers answer two different questions and must not
    be used interchangeably (see fpl/decide/optimiser.py):

      weighted_xpts — "is this player worth OWNING for the next 5 GWs?"
                      -> squad-of-15 selection, a multi-week asset decision.
      next_gw_xpts  — "should this player START and/or be CAPTAIN this week?"
                      -> XI and captaincy, both re-decided every single GW.

    Measured on real GW1-5 projections: 100 of 595 players move more than 20
    rank places between these two orderings, 14 move more than 50. Picking an
    XI on the horizon number benches players who are the correct start this
    week, and captains on a blend that describes no actual gameweek.
    """
    config = config or load_config()
    weights = config["horizon"]["decay_weights"]
    events = sorted(projections["event"].unique())
    weight_map = {event: weights[i] for i, event in enumerate(events) if i < len(weights)}
    next_event = events[0]

    df = projections.copy()
    df["weight"] = df["event"].map(weight_map)
    weighted = df.groupby("id").apply(
        lambda g: pd.Series({"weighted_xpts": (g["xpts"] * g["weight"]).sum(), "total_xpts": g["xpts"].sum()}),
        include_groups=False,
    ).reset_index()

    next_gw = (
        df.loc[df["event"] == next_event, ["id", "xpts"]]
        .rename(columns={"xpts": "next_gw_xpts"})
        .drop_duplicates("id")
    )

    meta = projections[["id", "web_name", "position", "team", "price", "confidence"]].drop_duplicates("id")
    out = meta.merge(weighted, on="id", how="left").merge(next_gw, on="id", how="left")
    out["next_gw_xpts"] = out["next_gw_xpts"].fillna(0.0)
    out.attrs["next_event"] = int(next_event)
    return out


if __name__ == "__main__":
    cfg = load_config()
    horizon = cfg["horizon"]["gameweeks"]
    projections = project_gameweeks(horizon, cfg)
    print(f"Projected {projections['event'].nunique()} gameweeks for {projections['id'].nunique()} players")
    print(f"Gameweeks: {sorted(projections['event'].unique())}")

    totals = weighted_horizon_total(projections, cfg)
    print(f"\nTop 20 by 5-GW weighted xPts:")
    top20 = totals.nlargest(20, "weighted_xpts")
    print(top20[["web_name", "position", "team", "price", "confidence", "weighted_xpts"]].to_string(index=False))
