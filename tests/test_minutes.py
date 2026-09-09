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


def _write_actuals(actuals_dir, season: str, rows: list[dict]) -> None:
    """rows: dicts with id, event, minutes, starts."""
    actuals_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(actuals_dir / f"actuals_{season}.csv", index=False, encoding="utf-8")


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
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
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
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
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


def test_rolling_start_rate_prefers_fresh_actuals_over_lagging_vaastav(tmp_path, monkeypatch):
    """Real bug regression (PROJECT_LOG §20): vaastav's current-season
    merged_gw.csv lags the FPL API by ~1 GW. Our own data/actuals/ has the
    graded GW the moment it settles. When actuals covers more current-season
    gameweeks than vaastav, it is used instead.

    3 stale injury cameos (starts 0). vaastav shows only GW1, actuals shows
    GW1-2 (kept below the current-season-only threshold so the blend is
    visible): with actuals the window is [3 cameos(0), 2 starts(1)] = 2/5;
    on vaastav alone it would be [3 cameos(0), 1 start(1)] = 1/4."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    _write_merged_gw(tmp_path / "2025-26", [
        {"element": 50, "minutes": m, "starts": 0, "GW": gw}
        for gw, m in zip(range(36, 39), [12, 8, 20])
    ])
    _write_players_raw(tmp_path / "2025-26", {50: 999})
    _write_merged_gw(tmp_path / "2026-27", [{"element": 7, "minutes": 90, "starts": 1, "GW": 1}])
    _write_players_raw(tmp_path / "2026-27", {7: 999})
    _write_actuals(tmp_path / "actuals", "2026-27", [
        {"id": 7, "event": gw, "minutes": 90, "starts": 1} for gw in (1, 2)
    ])

    config = {"history": {"seasons": ["2025-26"]}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02}}
    players = _players([{"id": 7, "code": 999}])

    out = minutes_mod.compute_rolling_start_rate(players, config)
    assert out.loc[out["id"] == 7, "rolling_start_rate"].iloc[0] == pytest.approx(2 / 5)


def test_rolling_start_rate_window_is_current_season_only_past_the_threshold(tmp_path, monkeypatch):
    """Once a player has CURRENT_SEASON_ONLY_AFTER current-season appearances,
    the window is drawn from the current season alone — a stale cross-club
    history tail no longer counts. 6 old benched apps (starts 0) + 3 fresh
    starts -> 1.0, not the 3/6 a blended last-6 would give."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    _write_merged_gw(tmp_path / "2025-26", [
        {"element": 50, "minutes": 15, "starts": 0, "GW": gw} for gw in range(1, 7)
    ])
    _write_players_raw(tmp_path / "2025-26", {50: 999})
    _write_actuals(tmp_path / "actuals", "2026-27", [
        {"id": 7, "event": gw, "minutes": 90, "starts": 1} for gw in (1, 2, 3)
    ])

    config = {"history": {"seasons": ["2025-26"]}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02}}
    players = _players([{"id": 7, "code": 999}])

    out = minutes_mod.compute_rolling_start_rate(players, config)
    assert out.loc[out["id"] == 7, "rolling_start_rate"].iloc[0] == pytest.approx(1.0)


def test_rolling_start_rate_below_threshold_still_blends_history(tmp_path, monkeypatch):
    """Guard: with fewer than CURRENT_SEASON_ONLY_AFTER current-season apps,
    the blended last-6 behaviour is unchanged (2 fresh starts don't yet
    override a benched history)."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    _write_merged_gw(tmp_path / "2025-26", [
        {"element": 50, "minutes": 15, "starts": 0, "GW": gw} for gw in range(1, 7)
    ])
    _write_players_raw(tmp_path / "2025-26", {50: 999})
    _write_actuals(tmp_path / "actuals", "2026-27", [
        {"id": 7, "event": gw, "minutes": 90, "starts": 1} for gw in (1, 2)
    ])

    config = {"history": {"seasons": ["2025-26"]}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02}}
    players = _players([{"id": 7, "code": 999}])

    out = minutes_mod.compute_rolling_start_rate(players, config)
    assert out.loc[out["id"] == 7, "rolling_start_rate"].iloc[0] == pytest.approx(2 / 6)


def test_minutes_m6_uses_news_prob_over_chance_of_playing(tmp_path, monkeypatch):
    """model='m6_news': a parsed news signal supersedes chance_of_playing.
    Player 1 has chance_of_playing=25 but the news parse says 0.8 → 0.8."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", tmp_path / "news.json")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: {
        "start_prob": 0.8, "status": "doubt", "return_gw": None,
        "confidence": 0.9, "reason": "back in training",
    })

    players = _players([
        {"id": 1, "status": "a", "chance_of_playing_next_round": 25, "news": "Knock - back in training"},
        {"id": 2, "status": "a", "chance_of_playing_next_round": None, "news": ""},
    ])
    config = {"history": {"seasons": []}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02},
              "news": {"model": "m", "prompt_version": 1, "min_confidence": 0.5}}

    out = minutes_mod.compute_minutes_factor(players, config, model="m6_news")
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == pytest.approx(0.8)


def test_minutes_m0_is_byte_identical_with_news_present(tmp_path, monkeypatch):
    """model='m0_rules' (the default): news is NEVER consulted, even with a
    populated cache. Same input → same output as before this change."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod.llm_client, "parse_availability",
                        lambda t, c: (_ for _ in ()).throw(AssertionError("m0 must not touch news")))

    players = _players([
        {"id": 1, "status": "a", "chance_of_playing_next_round": 25, "news": "Knock"},
    ])
    config = {"history": {"seasons": []}, "minutes": {"backup_gk_factor": 0.02}}

    out = minutes_mod.compute_minutes_factor(players, config)  # default model
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == pytest.approx(0.25)


def test_minutes_m6_news_never_overrides_hard_unavailable(tmp_path, monkeypatch):
    """FPL status 'i' + a news parse of 0.9 → minutes_factor is still 0.0.
    The parse only moves a player WITHIN the available-but-doubtful space."""
    monkeypatch.setattr(minutes_mod, "HIST_DIR", tmp_path)
    monkeypatch.setattr(minutes_mod, "ACTUALS_DIR", tmp_path / "actuals")
    monkeypatch.setattr("fpl.project.identity.HIST_DIR", tmp_path)

    from fpl.project import news as news_mod
    monkeypatch.setattr(news_mod, "NEWS_CACHE_PATH", tmp_path / "news.json")
    monkeypatch.setattr(news_mod.llm_client, "parse_availability", lambda t, c: {
        "start_prob": 0.9, "status": "available", "return_gw": None,
        "confidence": 0.9, "reason": "rumour says fit",
    })

    players = _players([{"id": 1, "status": "i", "news": "Contradictory rumour"}])
    config = {"history": {"seasons": []}, "season": "2026-27",
              "minutes": {"backup_gk_factor": 0.02},
              "news": {"model": "m", "prompt_version": 1, "min_confidence": 0.5}}

    out = minutes_mod.compute_minutes_factor(players, config, model="m6_news")
    assert out.loc[out["id"] == 1, "minutes_factor"].iloc[0] == 0.0


def test_build_player_inputs_m6_news_threads_model_to_minutes(monkeypatch):
    """build_player_inputs(model='m6_news') must pass model through to
    compute_minutes_factor — otherwise M6 == M0 and the whole feature is inert."""
    from fpl.project import project as project_mod

    seen = {}
    real = project_mod.minutes_mod.compute_minutes_factor

    def _spy(players, config, model="m0_rules"):
        seen["model"] = model
        return real(players, config, model=model)

    monkeypatch.setattr(project_mod.minutes_mod, "compute_minutes_factor", _spy)

    cfg = project_mod.load_config()
    try:
        project_mod.build_player_inputs(cfg, model="m6_news")
    except Exception:
        pass  # we only care that the model string reached minutes
    assert seen.get("model") == "m6_news"


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
