"""
Regression tests for the 2026-08-20 hotfix. The first real pytest suite in
this repo — every bug in HANDOFF.md §4 was caught by eyeballing output, which
does not scale and does not protect against reintroduction.

Both tests are pure unit tests: no network, no API pull, no committed
artefacts, no history archive. They construct the minimum frame each function
needs so they run anywhere, including in CI before any data exists.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.decide import optimiser as opt
from fpl.project import project as proj

CONFIG = {
    "scoring_rules": {
        "appearance_60plus": 2, "appearance_1to59": 1, "yellow_card": -1,
        "red_card": -3, "saves_per_point": 3, "conceded_per_point": 2,
    },
    "position_multipliers": {
        "goals": {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4},
        "assists_flat": 3,
        "clean_sheet_value": {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
    },
    "defcon": {"points": 2},
    "minutes": {"bench_cameo_rate": 0.3},
    "squad_rules": {
        "starting_xi": {"total": 11, "gk": 1, "min_def": 3, "min_mid": 2, "min_fwd": 1}
    },
}


def _one_defender() -> pd.DataFrame:
    """A single DEF whose team ships 1.5 goals/90 — every other channel zeroed
    so the conceded term is the only thing moving."""
    return pd.DataFrame([{
        "id": 1, "web_name": "Test DEF", "position": "DEF", "price": 5.0, "team": 1,
        "confidence": "high", "status": "a", "minutes_factor": 1.0,
        "goals_scored_per90": 0.0, "assists_per90": 0.0, "clean_sheets_per90": 0.0,
        "bonus_per90": 0.0, "saves_per90": 0.0, "goals_conceded_per90": 0.0,
        "yellow_cards_per90": 0.0, "red_cards_per90": 0.0, "defcon_rate": 0.0,
        "team_cs_rate": 0.0, "team_goals_conceded_per90": 1.5,
    }])


def _fixture(defence_mult: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "team": 1, "event": 1, "fixture_id": 100,
        "fixture_attack_mult": 1.0,
        "fixture_defence_mult": defence_mult,
        "fixture_defcon_mult": 1.0 / defence_mult,
        "fixture_concede_mult": 1.0 / defence_mult,
    }])


def test_conceded_penalty_shrinks_on_an_easy_fixture():
    """
    THE BUG: conceded_pts used fixture_defence_mult, which is HIGH when a
    clean sheet is likely. Multiplying a negative penalty by it made an EASY
    fixture produce a BIGGER goals-conceded punishment — the term pointed
    backwards relative to fixture difficulty for every GK and DEF.

    A penalty must move OPPOSITE to clean-sheet ease.
    """
    easy = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(2.0), CONFIG, calibration={})
    hard = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(0.5), CONFIG, calibration={})

    easy_pen = easy["conceded_pts"].iloc[0]
    hard_pen = hard["conceded_pts"].iloc[0]

    assert easy_pen < 0 and hard_pen < 0, "conceded_pts must be a penalty"
    assert easy_pen > hard_pen, (
        f"easy fixture must be punished LESS than a hard one, got "
        f"easy={easy_pen:.3f} hard={hard_pen:.3f} — the multiplier is inverted"
    )


def test_conceded_penalty_is_symmetric_about_a_neutral_fixture():
    """A 2.0x-easy and a 0.5x-hard fixture are reciprocal, so their penalties
    must be reciprocal about the neutral case too."""
    neutral = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(1.0), CONFIG, calibration={})
    easy = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(2.0), CONFIG, calibration={})
    hard = proj.compute_channel_pts_per_fixture(_one_defender(), _fixture(0.5), CONFIG, calibration={})

    n = neutral["conceded_pts"].iloc[0]
    assert easy["conceded_pts"].iloc[0] == pytest.approx(n / 2.0)
    assert hard["conceded_pts"].iloc[0] == pytest.approx(n * 2.0)


def _squad_of_15() -> tuple[list, dict]:
    ids, position = [], {}
    for pos, n in [("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        for k in range(n):
            pid = len(ids) + 1
            ids.append(pid)
            position[pid] = pos
    return ids, position


def test_xi_and_captain_follow_this_week_not_the_horizon():
    """
    THE BUG: v1 chose the XI AND the captain by maximising 5-GW decay-weighted
    xPts. Both are re-decided every single gameweek. A player can be the
    correct captain this week and the wrong one across five fixtures.

    Here, player 13 (FWD) is the best player over the horizon but is blanking
    this week; player 14 (FWD) is the best THIS week. The armband must follow
    this week.
    """
    ids, position = _squad_of_15()
    horizon = {i: 1.0 for i in ids}
    this_week = {i: 1.0 for i in ids}
    horizon[13], this_week[13] = 50.0, 0.1     # great over 5 GWs, blanking now
    horizon[14], this_week[14] = 2.0, 12.0     # only good this week

    xi, captain, vice = opt.pick_xi_and_captain(
        ids, this_week, position, CONFIG["squad_rules"]
    )

    assert captain == 14, "captain must maximise THIS gameweek, not the horizon"
    assert captain in xi and vice in xi and vice != captain


def test_xi_respects_formation_constraints():
    ids, position = _squad_of_15()
    pts = {i: float(i) for i in ids}  # FWDs highest -> tempts an illegal shape
    xi, captain, _ = opt.pick_xi_and_captain(ids, pts, position, CONFIG["squad_rules"])

    counts = pd.Series([position[i] for i in xi]).value_counts()
    assert len(xi) == 11
    assert counts.get("GK", 0) == 1
    assert counts.get("DEF", 0) >= 3
    assert counts.get("MID", 0) >= 2
    assert counts.get("FWD", 0) >= 1


def test_optimiser_refuses_to_run_without_a_next_gameweek_column():
    """Silently falling back to weighted_xpts would reintroduce the bug as a
    plausible-looking wrong answer rather than a crash."""
    players = pd.DataFrame([{
        "id": 1, "web_name": "X", "position": "MID", "price": 5.0, "team": 1,
        "confidence": "high", "status": "a", "weighted_xpts": 10.0,
    }])
    with pytest.raises(KeyError, match="next_gw_xpts"):
        opt.optimise_squad(players, {**CONFIG, "optimiser": {"allow_low_confidence": False},
                                     "squad_rules": {**CONFIG["squad_rules"], "total": 15,
                                                     "gk": 2, "def": 5, "mid": 5, "fwd": 3,
                                                     "max_per_club": 3, "budget_tenths": 1000}})
