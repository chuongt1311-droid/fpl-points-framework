"""
understat_blend.py — v3 plan §B3, model M3: nests on top of M2 (plan §B1's
nesting table — M3 = M2 + "npxG, xGChain, set-piece/penalty split, shot
quality"). Adds exactly one component over M2: for players with a real
Understat match, blend M2's already-blended attacking rate with a
recency-weighted **npxG90** rate (Understat's non-penalty xG) — the
signal FPL's own `expected_goals` (used by M2) cannot give you, since FPL
doesn't separate penalty income from open-play finishing. Plan's own
reasoning: "npxG separates penalty income from open-play finishing. A
penalty taker's xG is not a repeatable open-play skill."

Season labelling: this repo's history.seasons ("2023-24", "2024-25",
"2025-26") vs Understat's URL year (the season's START year — "2023",
"2024", "2025") are the same three seasons, one year-format apart —
UNDERSTAT_SEASON_MAP bridges them. All three were fetched and cached by
fpl/collect/sources/understat.py (see docs/PROJECT_LOG.md for the real
robots.txt/consent note this data source carries — read understat.py's
module docstring before extending this).

Identity bridge: fpl/project/identity_multi.py's player_id_map (built
once against 2025-26, `data/reference/player_id_map_2025-26.csv`) maps
FPL `code` -> Understat `source_player_id`. Verified directly that
Understat's own id is STABLE across seasons for the same player (same
mechanism as FPL's own `code` — fpl/project/identity.py), so this ONE map
is enough to bridge all three fetched Understat seasons, not one map per
season. **Real limitation, not hidden**: this map was built against the
2025-26 roster, so a 2026-27 new signing or promoted-club player who
wasn't in that roster has no bridge — falls through to M2's rate
unchanged (never a null, never a zero — same principle every prior model
in this project follows).

Unmatched players (no Understat bridge at all) pass through with M2's
rate UNCHANGED — this module only ever narrows M2's rate toward npxG for
players it can actually verify a real Understat identity for.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from fpl.collect.sources.understat import UnderstatAdapter

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"
DEFAULT_PLAYER_ID_MAP_PATH = REFERENCE_DIR / "player_id_map_2025-26.csv"

UNDERSTAT_SEASON_MAP = {"2023-24": "2023", "2024-25": "2024", "2025-26": "2025"}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_player_id_map(path: Optional[Path] = None) -> pd.DataFrame:
    path = path or DEFAULT_PLAYER_ID_MAP_PATH
    if not path.exists():
        return pd.DataFrame(columns=["fpl_code", "understat_id", "confidence"])
    return pd.read_csv(path, encoding="utf-8")


def compute_recency_weighted_npxg(seasons: list[str], decay: float) -> pd.DataFrame:
    """
    Per Understat source_player_id, recency-weighted npxG90/xA90 across the
    given seasons (repo's "2023-24"-style labels), reusing cached data via
    UnderstatAdapter (network-free if already cached — see that module's
    caching contract). Same recency-decay shape as
    baseline._season_weight: weight = decay ** seasons_ago, most recent
    season = 0 seasons ago. Understat's per-season aggregate already IS a
    per-season total (games/time/npxG/xA), so the per-90 rate is computed
    directly per season, then combined weighted by (weight * minutes) —
    the same "weighted_minutes-denominator" shape compute_player_rates
    uses, just at season-aggregate granularity instead of per-match.
    """
    adapter = UnderstatAdapter()
    frames = []
    for i, season in enumerate(seasons):
        understat_year = UNDERSTAT_SEASON_MAP.get(season)
        if understat_year is None:
            continue
        df = adapter.fetch(understat_year)
        if df.empty:
            continue
        df = df[df["time"] > 0].copy()
        weight = decay ** (len(seasons) - 1 - i)
        df["weight"] = weight
        df["weighted_minutes"] = weight * df["time"]
        frames.append(df[["source_player_id", "time", "npxG", "xA", "weight", "weighted_minutes"]])

    if not frames:
        return pd.DataFrame(columns=["source_player_id", "npxG90", "xA90_understat", "understat_weighted_minutes"])

    all_rows = pd.concat(frames, ignore_index=True)
    grouped = all_rows.groupby("source_player_id")
    out = pd.DataFrame({
        "source_player_id": grouped.size().index,
        "understat_weighted_minutes": grouped["weighted_minutes"].sum().values,
        "_npxg_weighted_sum": grouped.apply(lambda g: (g["weight"] * g["npxG"]).sum(), include_groups=False).values,
        "_xa_weighted_sum": grouped.apply(lambda g: (g["weight"] * g["xA"]).sum(), include_groups=False).values,
    })
    out["npxG90"] = out["_npxg_weighted_sum"] * 90 / out["understat_weighted_minutes"]
    out["xA90_understat"] = out["_xa_weighted_sum"] * 90 / out["understat_weighted_minutes"]
    return out[["source_player_id", "npxG90", "xA90_understat", "understat_weighted_minutes"]]


def apply_understat_blend(
    player_inputs: pd.DataFrame, config: Optional[dict] = None, player_id_map: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Takes M2's output (fpl.project.project.build_player_inputs(model="m2_xg")
    — already has the M2-blended goals_scored_per90/assists_per90) and
    further blends the GOAL rate toward npxG90 for players with a real
    Understat identity bridge, weight v3 = k_npxg / (m + k_npxg), same
    functional shape as xg_blend.blend_weight (declining with minutes —
    trust the purer non-penalty signal more when personal history is
    thin, same reasoning as M2's own xG blend). Assists are NOT
    re-blended here — Understat's xA is a similar signal to FPL's own xA
    already used by M2, with no penalty-separation angle, so there is no
    new information to add for that channel (unlike npxG vs xG for
    goals) — kept as M2 left it.

    Players with no Understat bridge pass through with M2's
    goals_scored_per90 UNCHANGED (not defaulted, not zeroed).
    """
    config = config or load_config()
    k_npxg = config["understat_blend"]["k_npxg"]
    seasons = config["history"]["seasons"]
    decay = config["history"]["recency_decay"]

    player_id_map = player_id_map if player_id_map is not None else load_player_id_map()
    npxg_rates = compute_recency_weighted_npxg(seasons, decay)

    bridge = player_id_map[["fpl_code", "understat_id"]].merge(
        npxg_rates, left_on="understat_id", right_on="source_player_id", how="inner",
    )

    out = player_inputs.merge(
        bridge[["fpl_code", "npxG90", "understat_weighted_minutes"]],
        left_on="code", right_on="fpl_code", how="left",
    )
    has_bridge = out["npxG90"].notna()

    v3 = pd.Series(0.0, index=out.index, dtype="float64")  # unmatched: v3=0 -> rate unchanged
    v3.loc[has_bridge] = (k_npxg / (out.loc[has_bridge, "understat_weighted_minutes"] + k_npxg)).astype("float64")

    out["goals_scored_per90"] = (
        v3 * out["npxG90"].fillna(0.0).astype("float64") + (1 - v3) * out["goals_scored_per90"]
    )
    return out.drop(columns=["fpl_code", "npxG90", "understat_weighted_minutes"])
