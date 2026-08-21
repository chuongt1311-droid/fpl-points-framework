"""
Tests for fpl/project/identity_multi.py (v3 plan §A5 — cross-source
identity bridge, distinct from fpl/project/identity.py's cross-SEASON
bridge).

Pure unit tests: synthetic FPL/Understat pools, no network — same bar as
tests/test_shrinkage.py. The real 2025-26 exercise (real Understat fetch,
real 82.7% coverage, 33->28 review-queue reduction from the html.unescape
fix) is documented in docs/PROJECT_LOG.md, not re-run here (that's the
network-marked test's job — see test_understat_adapter.py).

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd

from fpl.project import identity_multi as im


def test_normalise_name_strips_diacritics_lowercases_and_drops_punctuation():
    assert im.normalise_name("João Pedro") == "joao pedro"
    assert im.normalise_name("N'Golo Kanté") == "ngolo kante"


def test_normalise_name_unescapes_html_entities_first():
    """Real bug found against live Understat data: player_name carries raw
    HTML entities for apostrophes."""
    assert im.normalise_name("Matt O&#039;Riley") == "matt oriley"


def test_normalise_name_handles_non_string_input_without_raising():
    assert im.normalise_name(None) == ""
    assert im.normalise_name(float("nan")) == ""


def _fpl_row(code, name, team, web_name=None):
    return {"code": code, "web_name": web_name or name.split()[-1], "name": name, "team_name": team}


def _understat_row(source_id, player_name, team_title):
    return {"source_player_id": source_id, "player_name": player_name, "team_title": team_title}


def test_exact_normalised_name_and_team_match_is_high_confidence():
    fpl = pd.DataFrame([_fpl_row(1, "Erling Haaland", "Manchester City")])
    understat = pd.DataFrame([_understat_row(100, "Erling Haaland", "Manchester City")])
    player_map, review_queue = im.build_player_id_map(fpl, understat)

    assert len(player_map) == 1
    assert player_map.iloc[0]["confidence"] == "high"
    assert player_map.iloc[0]["match_method"] == "exact_normalised_name_team"
    assert review_queue.empty


def test_team_alias_table_bridges_a_naming_difference():
    fpl = pd.DataFrame([_fpl_row(1, "Someone Player", "Nottingham Forest")])
    understat = pd.DataFrame([_understat_row(100, "Someone Player", "Nott'm Forest")])
    player_map, _ = im.build_player_id_map(fpl, understat)
    assert len(player_map) == 1
    assert player_map.iloc[0]["confidence"] == "high"


def test_near_match_below_exact_goes_to_review_queue_not_auto_accepted():
    fpl = pd.DataFrame([_fpl_row(1, "Martin Odegaard", "Arsenal")])
    understat = pd.DataFrame([_understat_row(100, "Martín Ødegaard", "Arsenal")])
    player_map, review_queue = im.build_player_id_map(fpl, understat)

    assert len(player_map) == 1
    assert player_map.iloc[0]["confidence"] == "medium"
    assert player_map.iloc[0]["match_method"].startswith("fuzzy_")
    assert len(review_queue) == 1  # NOT auto-accepted as high


def test_genuinely_different_players_are_left_unmatched_not_forced():
    fpl = pd.DataFrame([_fpl_row(1, "Zzzargle Qwoxfield", "Arsenal")])
    understat = pd.DataFrame([_understat_row(100, "Erling Haaland", "Manchester City")])
    player_map, _ = im.build_player_id_map(fpl, understat)
    assert player_map.empty  # no row at all, not a null row, not a 0


def test_cross_team_fuzzy_match_is_not_attempted():
    """Two different players sharing a similar surname on DIFFERENT teams
    must not be fuzzy-matched to each other — plan §A5's own stated
    failure mode (a wrong join looks plausible and never trips an
    eye-test alarm)."""
    fpl = pd.DataFrame([_fpl_row(1, "James Smith", "Arsenal")])
    understat = pd.DataFrame([_understat_row(100, "James Smyth", "Chelsea")])
    player_map, _ = im.build_player_id_map(fpl, understat)
    assert player_map.empty


def test_unique_name_only_pass_recovers_a_real_team_mismatch():
    """Real gap found against live data: an archive CSV can show a LATER
    team transfer than the season being matched. An exact, name-unique
    match must still be recovered as high confidence even when team
    disagrees, as long as no other candidate shares that name."""
    fpl = pd.DataFrame([_fpl_row(1, "Eberechi Eze", "Arsenal")])       # archive shows the NEW club
    understat = pd.DataFrame([_understat_row(100, "Eberechi Eze", "Crystal Palace")])  # actual season club
    player_map, review_queue = im.build_player_id_map(fpl, understat)

    assert len(player_map) == 1
    assert player_map.iloc[0]["confidence"] == "high"
    assert player_map.iloc[0]["match_method"] == "exact_normalised_name_only_unique"
    assert review_queue.empty


def test_ambiguous_name_only_match_is_not_used_when_not_unique():
    """If TWO different-team candidates share the exact same normalised
    name, the name-only pass must not guess — falls through to the
    team-qualified fuzzy pass (or unmatched), never an arbitrary pick."""
    fpl = pd.DataFrame([
        _fpl_row(1, "Ambiguous Name", "Arsenal"),
    ])
    understat = pd.DataFrame([
        _understat_row(100, "Ambiguous Name", "Chelsea"),
        _understat_row(101, "Ambiguous Name", "Liverpool"),
    ])
    player_map, _ = im.build_player_id_map(fpl, understat)
    assert player_map.empty  # team mismatch on both, name not unique -> no guess


def test_coverage_pct_computes_percentage_of_fpl_pool_matched():
    fpl = pd.DataFrame([
        _fpl_row(1, "Player One", "Arsenal"),
        _fpl_row(2, "Player Two", "Chelsea"),
    ])
    player_map = pd.DataFrame([{"fpl_code": 1, "understat_id": 100, "confidence": "high"}])
    assert im.coverage_pct(fpl, player_map) == 50.0


def test_coverage_pct_handles_an_empty_map_without_raising():
    fpl = pd.DataFrame([_fpl_row(1, "Player One", "Arsenal")])
    empty_map = pd.DataFrame(columns=["fpl_code", "understat_id", "confidence"])
    assert im.coverage_pct(fpl, empty_map) == 0.0
