"""
Tests for fpl/project/minutes.py — HANDOFF.md §5 findings #3 (GK backup
re-promotion) and #6 (hard column selection breaking build_players.py's
"keep if present" contract). No test file existed for this module before
this session, despite it having caught and fixed 3 of the session-4
bugs listed in HANDOFF.md §4 — both fixes below are real, previously
untested gaps.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from fpl.project import minutes as minutes_mod

CONFIG = {
    "history": {"seasons": []},  # no history CSVs to read -> pure no-history fallback path
    "minutes": {"backup_gk_factor": 0.02},
}


def _players(rows: list[dict]) -> pd.DataFrame:
    defaults = {"web_name": "x", "status": "a", "chance_of_playing_next_round": None, "position": "MID", "team": 1, "price": 5.0}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_compute_minutes_factor_degrades_gracefully_when_status_column_missing(tmp_path, monkeypatch):
    """Real bug regression (finding #6): build_players.py drops `status`
    entirely if FPL ever omits it from bootstrap-static (its own "keep if
    present" contract). A hard players_df[[...]] select used to KeyError
    instead of degrading — this must not raise, and must treat every
    player as available (no unavailability signal present)."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    players = _players([{"id": 1}, {"id": 2}]).drop(columns=["status", "chance_of_playing_next_round"])
    assert "status" not in players.columns

    result = minutes_mod.compute_minutes_factor(players, CONFIG)
    assert (result["status"] == "a").all()
    assert (result["minutes_factor"] == minutes_mod.DEFAULT_START_RATE_NO_HISTORY).all()


def test_compute_minutes_factor_degrades_gracefully_when_chance_column_missing(tmp_path, monkeypatch):
    """Same finding #6, the other column: no chance_of_playing data at all
    must fall through to the rolling-start-rate fallback branch, not raise."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    players = _players([{"id": 1, "status": "a"}]).drop(columns=["chance_of_playing_next_round"])
    assert "chance_of_playing_next_round" not in players.columns

    result = minutes_mod.compute_minutes_factor(players, CONFIG)
    assert result.loc[0, "minutes_factor"] == minutes_mod.DEFAULT_START_RATE_NO_HISTORY


def test_compute_minutes_factor_still_zeroes_unavailable_players_when_status_present(tmp_path, monkeypatch):
    """Sanity check that the column-presence fix (finding #6) didn't
    weaken the normal (status present) path — an injured player must
    still get factor 0."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    players = _players([{"id": 1, "status": "i"}, {"id": 2, "status": "a"}])
    result = minutes_mod.compute_minutes_factor(players, CONFIG)
    assert result.loc[result["id"] == 1, "minutes_factor"].iloc[0] == 0.0
    assert result.loc[result["id"] == 2, "minutes_factor"].iloc[0] > 0.0


def _write_merged_gw(dir_path, rows: list[dict]) -> None:
    """rows: dicts with element, minutes, starts, GW."""
    gws = dir_path / "gws"
    gws.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(gws / "merged_gw.csv", index=False, encoding="utf-8")


def _write_players_raw(dir_path, id_to_code: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"id": i, "code": c} for i, c in id_to_code.items()]).to_csv(
        dir_path / "players_raw.csv", index=False, encoding="utf-8"
    )


def test_rolling_start_rate_blends_current_season_over_stale_history(tmp_path, monkeypatch):
    """Real bug regression: compute_rolling_start_rate read ONLY the archived
    `history.seasons` and never the current season, so a player whose last 6
    archived appearances were an injury/benched spell (starts=0) was floored
    to rolling_start_rate=0.0 -> minutes_factor=0.0 -> xPts~0.3 flat, even
    after starting every game of the new season. Wissa (Brentford injury tail
    2025-26 -> nailed at Newcastle 2026-27) was the live case.

    The current season must enter the rolling window like any other games:
    last 6 of [4 old cameos (starts 0), 2 new starts] -> 2/6."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    _write_merged_gw(tmp_path / "2025-26", [
        {"element": 50, "minutes": m, "starts": 0, "GW": gw}
        for gw, m in zip(range(33, 39), [15, 1, 1, 24, 22, 19])
    ])
    _write_players_raw(tmp_path / "2025-26", {50: 999})
    _write_merged_gw(tmp_path / "2026-27", [
        {"element": 7, "minutes": 90, "starts": 1, "GW": 1},
        {"element": 7, "minutes": 88, "starts": 1, "GW": 2},
    ])
    _write_players_raw(tmp_path / "2026-27", {7: 999})

    config = {"history": {"seasons": ["2025-26"]}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02}}
    players = _players([{"id": 7, "code": 999}])

    out = minutes_mod.compute_rolling_start_rate(players, config)
    rate = out.loc[out["id"] == 7, "rolling_start_rate"].iloc[0]
    assert rate == pytest.approx(2 / 6)


def test_rolling_start_rate_pre_season_is_pure_historical(tmp_path, monkeypatch):
    """Guard: with no current-season CSV yet (pre-season, or config has no
    `season`), behaviour is unchanged — pure archived history, no crash."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    _write_merged_gw(tmp_path / "2025-26", [
        {"element": 50, "minutes": 90, "starts": 1, "GW": gw} for gw in range(1, 7)
    ])
    _write_players_raw(tmp_path / "2025-26", {50: 999})

    config = {"history": {"seasons": ["2025-26"]}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02}}
    players = _players([{"id": 7, "code": 999}])

    out = minutes_mod.compute_rolling_start_rate(players, config)
    assert out.loc[out["id"] == 7, "rolling_start_rate"].iloc[0] == pytest.approx(1.0)


def test_apply_gk_backup_override_re_promotes_backup_when_number_one_is_injured():
    """Real bug regression (finding #3): the price-designated #1 used to
    be picked from a STATIC price ranking with no re-check against
    current availability. An injured #1 correctly zeroes their OWN
    factor (status-driven, upstream of this function) but the function
    itself never re-checked who's actually next in line — the real
    starter (the backup, now playing) stayed clipped at backup_factor
    even though they'd become the incumbent."""
    players = _players([
        {"id": 1, "team": 10, "position": "GK", "price": 5.5},  # price-designated #1
        {"id": 2, "team": 10, "position": "GK", "price": 4.0},  # backup, now the real starter
    ])
    minutes_df = pd.DataFrame({
        "id": [1, 2],
        "rolling_start_rate": [0.9, 0.1],
        "minutes_factor": [0.0, 0.85],  # #1 already status-zeroed (injured); backup is playing
    })

    out = minutes_mod.apply_gk_backup_override(minutes_df, players, CONFIG)

    injured_number_one = out.loc[out["id"] == 1, "minutes_factor"].iloc[0]
    real_starter = out.loc[out["id"] == 2, "minutes_factor"].iloc[0]
    assert injured_number_one == 0.0  # never raised back up
    assert real_starter == 0.85  # NOT clipped to backup_factor — re-promoted


def test_apply_gk_backup_override_still_caps_a_genuine_backup_behind_a_healthy_number_one():
    """Sanity check the fix didn't remove the cap entirely — a genuine
    backup behind a healthy, available #1 must still be capped."""
    players = _players([
        {"id": 1, "team": 10, "position": "GK", "price": 5.5},  # healthy #1
        {"id": 2, "team": 10, "position": "GK", "price": 4.0},  # genuine backup
    ])
    minutes_df = pd.DataFrame({
        "id": [1, 2],
        "rolling_start_rate": [0.9, 0.3],
        "minutes_factor": [0.9, 0.3],
    })

    out = minutes_mod.apply_gk_backup_override(minutes_df, players, CONFIG)
    backup_factor = out.loc[out["id"] == 2, "minutes_factor"].iloc[0]
    assert backup_factor == CONFIG["minutes"]["backup_gk_factor"]
