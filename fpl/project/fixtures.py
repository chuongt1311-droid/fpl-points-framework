"""
fixtures.py — PROJECT layer. Three separate fixture-difficulty multipliers
per plan §4.2 — deliberately three, not one FDR number, because they don't
move together:

  fixture_attack_mult  — how easy to score against this opponent, from the
                          opponent's DEFENSIVE strength (relevant venue).
  fixture_defence_mult — how likely a clean sheet, from the opponent's
                          ATTACKING strength (relevant venue).
  fixture_defcon_mult  — INVERTED: a stronger attacking opponent means more
                          defensive work, so more CBIT/CBIRT opportunity.
                          fixture_defcon_mult = 1 / fixture_defence_mult by
                          construction (same inputs, opposite direction).

Primary signal is bootstrap-static's `strength_*` ratings (finer-grained
than FPL's own 1-5 FDR, which is used only as a sanity-check fallback — not
implemented as a separate path in v1 since strength_* is always present).
Home/away is handled by picking the opponent's home or away strength value
for their actual side in this fixture — no separate home-bonus term, since
plan §4.2 says the strength ratings already encode it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.transform import build_fixtures as build_fixtures_mod

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
PROCESSED_DIR = Path(__file__).resolve().parents[2] / "data" / "processed"

# Clip multipliers to sane bounds — guards against a divide-by-near-zero
# blowing up a projection if a strength rating is ever missing/degenerate.
MULT_MIN, MULT_MAX = 0.5, 2.0

# VERIFIED: pre-season, bootstrap-static's strength_* ratings are all 0 for
# every team (not published yet — presumably set once there's current-season
# form to base them on). Plan §4.2 names FPL's own 1-5 fixture `difficulty`
# as "a sanity check and a fallback" for exactly this situation. Log-
# symmetric around difficulty=3 (neutral, mult=1.0) so a 2x easy-fixture
# boost and a 0.5x hard-fixture cut are equal and opposite in log-space,
# hitting exactly MULT_MAX at difficulty=1 and MULT_MIN at difficulty=5.
_DIFFICULTY_K = math.log(MULT_MAX) / 2  # solved so difficulty=1 -> MULT_MAX


def _difficulty_fallback_mult(difficulty: pd.Series) -> pd.Series:
    return (((3 - difficulty) * _DIFFICULTY_K).apply(math.exp)).clip(MULT_MIN, MULT_MAX)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_team_strengths() -> pd.DataFrame:
    path = RAW_DIR / "bootstrap_static.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run fpl.collect.fpl_client first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    cols = ["id", "name", "strength_attack_home", "strength_attack_away",
            "strength_defence_home", "strength_defence_away"]
    return pd.DataFrame(data["teams"])[cols]


def league_averages(team_strengths: pd.DataFrame) -> dict[str, float]:
    """One league-average attack rating and one defence rating, each pooled
    across every team's home AND away values."""
    attack_avg = pd.concat([team_strengths["strength_attack_home"], team_strengths["strength_attack_away"]]).mean()
    defence_avg = pd.concat([team_strengths["strength_defence_home"], team_strengths["strength_defence_away"]]).mean()
    return {"attack": attack_avg, "defence": defence_avg}


def load_fixture_table() -> pd.DataFrame:
    """HANDOFF.md §5 finding #5: unlike every other input in this package
    (build_players/baseline/defcon/minutes), this used to just raise if
    `fpl.transform.build_fixtures` was never separately run — breaking on
    a fresh checkout that only ran the two COLLECT scripts. Now
    self-builds from the raw fixtures.json, same "keep if present, build
    if not" contract as build_players.build_players() elsewhere in this
    package. Only raises (via build_fixtures itself) if the raw file is
    ALSO missing, i.e. `fpl.collect.fpl_client` was never run at all."""
    path = PROCESSED_DIR / "fixtures.parquet"
    if not path.exists():
        fixture_table, _ = build_fixtures_mod.build_fixtures()
        return fixture_table
    return pd.read_parquet(path)


def compute_fixture_multipliers(
    fixture_table: Optional[pd.DataFrame] = None,
    team_strengths: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    One row per (team, event, fixture) — matches fixture_table's long
    format (build_fixtures.py: one row per team per fixture) — with the
    three multipliers attached, computed from the OPPONENT's strength at
    their actual venue for this fixture.
    """
    fixture_table = fixture_table if fixture_table is not None else load_fixture_table()
    team_strengths = team_strengths if team_strengths is not None else load_team_strengths()
    avgs = league_averages(team_strengths)

    strengths_by_id = team_strengths.set_index("id")

    df = fixture_table.copy()
    opp_defence_away = df["opponent"].map(strengths_by_id["strength_defence_away"])
    opp_defence_home = df["opponent"].map(strengths_by_id["strength_defence_home"])
    opp_attack_away = df["opponent"].map(strengths_by_id["strength_attack_away"])
    opp_attack_home = df["opponent"].map(strengths_by_id["strength_attack_home"])

    # If `team` is home, the opponent is away (and vice versa) — use the
    # opponent's rating for the venue THEY are actually playing at.
    opp_relevant_defence = opp_defence_away.where(df["is_home"], opp_defence_home)
    opp_relevant_attack = opp_attack_away.where(df["is_home"], opp_attack_home)

    # Divide-by-zero here produces inf (0 rating, e.g. unpublished
    # pre-season) rather than NaN — the `.where(... > 0, fallback)` below
    # overrides those rows unconditionally regardless, so no separate inf
    # handling is needed.
    attack_mult = (avgs["defence"] / opp_relevant_defence).clip(MULT_MIN, MULT_MAX)
    defence_mult = (avgs["attack"] / opp_relevant_attack).clip(MULT_MIN, MULT_MAX)

    # Per-row fallback to the difficulty-derived multiplier wherever the
    # strength-rating signal is degenerate (0 or missing) for this specific
    # opponent/venue — not an all-or-nothing global switch, so a mix of
    # published and unpublished ratings is handled correctly too.
    fallback = _difficulty_fallback_mult(df["difficulty"])
    df["fixture_attack_mult"] = attack_mult.where(opp_relevant_defence > 0, fallback)
    df["fixture_defence_mult"] = defence_mult.where(opp_relevant_attack > 0, fallback)
    df["fixture_defcon_mult"] = (1.0 / df["fixture_defence_mult"]).clip(MULT_MIN, MULT_MAX)
    # fixture_concede_mult — HOW MANY GOALS this team is expected to ship in
    # this fixture, relative to their own baseline. Numerically identical to
    # fixture_defcon_mult today (both are 1/fixture_defence_mult), but kept as
    # a SEPARATE column on purpose: "more defensive work to do" (DEFCON
    # opportunity) and "more goals expected against us" (the conceded penalty)
    # are distinct quantities that only happen to share an inverse right now.
    # Coupling them to one column is how the conceded-direction bug survived
    # review — the wrong multiplier read as plausible because its NAME didn't
    # say what it meant. If either gets its own model in v2, the other must
    # not silently move with it.
    df["fixture_concede_mult"] = df["fixture_defcon_mult"]
    df["used_strength_ratings"] = (opp_relevant_defence > 0) & (opp_relevant_attack > 0)

    # Guard: the three multipliers are supposed to be three INDEPENDENT
    # signals (plan §4.2). Pre-season, strength_* is unpublished and every row
    # falls back to FPL's single 1-5 difficulty number, which collapses
    # attack and defence onto the same value and makes that edge inert.
    # That's tolerable but must never be silent — it is the difference between
    # "my fixture model" and "FPL's FDR with extra steps."
    n_collapsed = int((df["fixture_attack_mult"].round(6) == df["fixture_defence_mult"].round(6)).sum())
    if n_collapsed:
        pct = 100.0 * n_collapsed / len(df)
        print(
            f"[fixtures] WARNING: attack and defence multipliers are identical on "
            f"{n_collapsed}/{len(df)} rows ({pct:.0f}%) — falling back to FPL's 1-5 "
            f"difficulty because strength_* ratings are unpublished/degenerate. "
            f"The three-multiplier edge is INERT on those rows."
        )

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PROCESSED_DIR / "fixture_multipliers.parquet", index=False)
    return df


if __name__ == "__main__":
    result = compute_fixture_multipliers()
    print(f"Computed multipliers for {len(result)} team-fixture rows")
    print(f"Used strength ratings: {result['used_strength_ratings'].sum()}, "
          f"used difficulty fallback: {(~result['used_strength_ratings']).sum()}")
    print(result[["team", "opponent", "is_home", "event", "difficulty", "fixture_attack_mult", "fixture_defence_mult", "fixture_defcon_mult"]].head(10).to_string(index=False))
    print(f"\nfixture_defence_mult x fixture_defcon_mult should == 1.0 (inverted by construction):")
    check = (result["fixture_defence_mult"] * result["fixture_defcon_mult"]).describe()
    print(check[["min", "max", "mean"]])
