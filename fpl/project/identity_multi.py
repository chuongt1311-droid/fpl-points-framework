"""
identity_multi.py — v3 plan §A5: the cross-SOURCE identity bridge (FPL
`code` <-> Understat `source_player_id` <-> eventually Sofascore). Distinct
from fpl/project/identity.py, which bridges FPL's OWN id across SEASONS —
this bridges DIFFERENT SOURCES within the same season. Both matter, both
get it wrong the same way if done carelessly: a silent bad join produces a
plausible-looking wrong number, not a crash (identity.py's own docstring;
the same failure class, a different join).

Plan §A5's explicit design, followed here:
  - Automated pass: normalise (strip diacritics, lowercase, drop
    punctuation), match on (normalised_name, team). Exact match only is
    auto-accepted as "high" confidence.
  - A second exact pass drops the team requirement, but ONLY for names
    unique on both sides among the still-unmatched pool (see
    build_player_id_map's pass 2) — a real gap this uncovered: some
    historical seasons' archive CSV already reflects a LATER team
    transfer than the season being matched, so team-qualified matching
    alone silently misses an otherwise-exact name match.
  - Fuzzy fallback (edit-distance ratio) ABOVE a high threshold is
    "medium" confidence and goes to the manual review queue — NOT
    auto-accepted. Plan's own words: "do not auto-accept fuzzy matches."
  - Everything below the fuzzy threshold is UNMATCHED, not forced into a
    wrong row. Unmatched players are excluded from the map entirely — any
    caller joining through this map must LEFT JOIN and treat a missing
    row as "no Understat data for this player," never as a 0 (that's
    finding #11 / backtest.py's original zero-fill bug, reintroduced in a
    new place if this got it wrong the same way).
  - `match_method` and `confidence` are stored per row so a bad join stays
    auditable after the fact (plan's own accountability requirement).

Team names differ across sources ("Man Utd" vs "Manchester United") — a
small manual alias table handles the ones normalisation alone can't, since
there's no shared team-id space to join on the way fpl/project/identity.py
does for FPL-across-seasons.
"""
from __future__ import annotations

import difflib
import html
import re
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"

# Understat's team_title -> FPL's team `name`. Only entries where simple
# normalisation (see normalise_name) doesn't already produce a match need
# to be listed here — e.g. "Nott'm Forest" (FPL) vs "Nottingham Forest"
# (Understat) normalise to different strings, so need an explicit alias.
TEAM_ALIASES = {
    "nottingham forest": "nottm forest",
    "manchester united": "man utd",
    "manchester city": "man city",
    "newcastle united": "newcastle",
    "tottenham": "spurs",
    "wolverhampton wanderers": "wolves",
    "brighton": "brighton",
    "west ham": "west ham",
    "leicester": "leicester",
    "leeds": "leeds",
}

# Above this ratio (difflib.SequenceMatcher), a non-exact name match goes
# to the review queue as "medium" confidence. Below it, unmatched. High
# per plan §A5's "do not auto-accept fuzzy matches" — this threshold only
# controls what's worth a HUMAN'S time to review, never an auto-accept.
FUZZY_REVIEW_THRESHOLD = 0.82


def normalise_name(name: str) -> str:
    """Strip diacritics, lowercase, drop punctuation — plan §A5's exact
    recipe. 'João Pedro' -> 'joao pedro', 'N'Golo Kanté' -> 'ngolo kante'.
    html.unescape first: Understat's player_name field carries raw HTML
    entities for apostrophes ('Matt O&#039;Riley'), verified against real
    2025-26 data — without this, a name that should match EXACTLY only
    fuzzy-matches at ~0.88, needlessly inflating the review queue with
    matches that are actually unambiguous."""
    if not isinstance(name, str):
        return ""
    name = html.unescape(name)
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    return re.sub(r"[^a-z0-9\s]", "", lowered).strip()


def normalise_team(team_name: str) -> str:
    normalised = normalise_name(team_name)
    return TEAM_ALIASES.get(normalised, normalised)


def build_player_id_map(
    fpl_players: pd.DataFrame, understat_players: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    fpl_players: needs `code`, `web_name` or `name` (full name preferred —
    see below), `team_name`.
    understat_players: needs `source_player_id`, `player_name`, `team_title`.

    Returns (player_id_map, review_queue) — see module docstring for the
    columns/semantics of each. player_id_map contains ONLY matched rows
    (high or medium confidence); review_queue contains the medium-
    confidence rows again (a subset, not a separate population) so a human
    can act on exactly what needs checking without re-deriving it.
    """
    fpl = fpl_players.copy()
    name_col = "name" if "name" in fpl.columns else "web_name"
    fpl["_norm_name"] = fpl[name_col].apply(normalise_name)
    fpl["_norm_team"] = fpl["team_name"].apply(normalise_team)

    understat = understat_players.copy()
    understat["_norm_name"] = understat["player_name"].apply(normalise_name)
    understat["_norm_team"] = understat["team_title"].apply(normalise_team)

    rows = []
    matched_understat_ids: set = set()

    # Pass 1: exact (normalised_name, normalised_team) match — high confidence.
    exact = fpl.merge(
        understat, on=["_norm_name", "_norm_team"], how="inner", suffixes=("_fpl", "_understat"),
    )
    for _, r in exact.iterrows():
        rows.append({
            "fpl_code": r["code"], "understat_id": r["source_player_id"],
            "match_method": "exact_normalised_name_team", "confidence": "high",
        })
        matched_understat_ids.add(r["source_player_id"])

    matched_fpl_codes = set(exact["code"])
    unmatched_fpl = fpl[~fpl["code"].isin(matched_fpl_codes)]

    # Pass 2: exact name match, team IGNORED, but ONLY when the normalised
    # name is UNIQUE on both sides among still-unmatched rows — recovers a
    # real, verified data-quality gap: some historical seasons' players_raw
    # csv reflects a LATER team assignment than the season being matched
    # (verified directly — e.g. 2025-26's archive already shows a player
    # transferred in 2026-27 under their NEW club), so team-qualified
    # matching alone misses them even though the name match is exact and
    # unambiguous. Still high confidence: an EXACT full-name match with no
    # other candidate sharing that name carries the same reliability as
    # the team-qualified exact pass, just without the (in this case wrong)
    # team as a second signal.
    remaining_understat = understat[~understat["source_player_id"].isin(matched_understat_ids)]
    name_counts_fpl = unmatched_fpl["_norm_name"].value_counts()
    name_counts_understat = remaining_understat["_norm_name"].value_counts()
    unique_names = set(name_counts_fpl[name_counts_fpl == 1].index) & set(name_counts_understat[name_counts_understat == 1].index)
    if unique_names:
        name_only = unmatched_fpl[unmatched_fpl["_norm_name"].isin(unique_names)].merge(
            remaining_understat[remaining_understat["_norm_name"].isin(unique_names)],
            on="_norm_name", how="inner", suffixes=("_fpl", "_understat"),
        )
        for _, r in name_only.iterrows():
            rows.append({
                "fpl_code": r["code"], "understat_id": r["source_player_id"],
                "match_method": "exact_normalised_name_only_unique", "confidence": "high",
            })
            matched_understat_ids.add(r["source_player_id"])
        matched_fpl_codes |= set(name_only["code"])
        unmatched_fpl = fpl[~fpl["code"].isin(matched_fpl_codes)]

    # Pass 3: fuzzy fallback, restricted to the SAME normalised team —
    # cross-team fuzzy name matching is how you get a false positive (two
    # different players who happen to share a surname), not a real
    # recovered match.
    for _, fpl_row in unmatched_fpl.iterrows():
        candidates = understat[
            (understat["_norm_team"] == fpl_row["_norm_team"])
            & (~understat["source_player_id"].isin(matched_understat_ids))
        ]
        if candidates.empty:
            continue
        best_ratio, best_row = 0.0, None
        for _, cand in candidates.iterrows():
            ratio = difflib.SequenceMatcher(None, fpl_row["_norm_name"], cand["_norm_name"]).ratio()
            if ratio > best_ratio:
                best_ratio, best_row = ratio, cand
        if best_row is not None and best_ratio >= FUZZY_REVIEW_THRESHOLD:
            rows.append({
                "fpl_code": fpl_row["code"], "understat_id": best_row["source_player_id"],
                "match_method": f"fuzzy_{best_ratio:.3f}", "confidence": "medium",
            })
            matched_understat_ids.add(best_row["source_player_id"])
        # else: genuinely unmatched — NOT added. No row, not a null row,
        # not a zero. Caller's LEFT JOIN handles the absence.

    player_id_map = pd.DataFrame(rows)
    if not player_id_map.empty:
        player_id_map["verified_by"] = None
        player_id_map["verified_ts"] = None
    review_queue = player_id_map[player_id_map["confidence"] == "medium"].copy() if not player_id_map.empty else player_id_map

    return player_id_map, review_queue


def coverage_pct(fpl_players: pd.DataFrame, player_id_map: pd.DataFrame) -> float:
    """Plan §A5: 'coverage is a health metric.' % of the FPL pool that has
    ANY row in the map (high or medium confidence — a medium-confidence
    row is still visible in the review queue, just not yet human-verified;
    it's covered, not silently absent)."""
    if fpl_players.empty:
        return 0.0
    matched = fpl_players["code"].isin(player_id_map["fpl_code"]) if not player_id_map.empty else pd.Series(False, index=fpl_players.index)
    return round(100.0 * matched.sum() / len(fpl_players), 2)


def write_player_id_map(player_id_map: pd.DataFrame, path: Optional[Path] = None) -> Path:
    path = path or (REFERENCE_DIR / "player_id_map.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    player_id_map.to_csv(path, index=False)
    return path
