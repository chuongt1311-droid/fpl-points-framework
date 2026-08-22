"""
Tests for fpl/project/fixtures.py's load_fixture_table() — HANDOFF.md §5
finding #5: unlike every other input in this package (build_players/
baseline/defcon/minutes, all self-building from raw files),
load_fixture_table() used to just raise FileNotFoundError if
`fpl.transform.build_fixtures` had never been separately run, breaking a
fresh checkout that only ran the two COLLECT scripts.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

from fpl.project import fixtures as fixtures_mod
from fpl.transform import build_fixtures as build_fixtures_mod

RAW_FIXTURES = [
    {
        "id": 1, "event": 1, "kickoff_time": "2026-08-15T14:00:00Z", "finished": False,
        "team_h": 1, "team_a": 2, "team_h_difficulty": 3, "team_a_difficulty": 3,
    },
    {
        "id": 2, "event": 1, "kickoff_time": "2026-08-15T16:30:00Z", "finished": False,
        "team_h": 3, "team_a": 4, "team_h_difficulty": 2, "team_a_difficulty": 4,
    },
]


def test_load_fixture_table_self_builds_when_parquet_missing(tmp_path, monkeypatch):
    """No fixtures.parquet, but a raw fixtures.json exists — must build
    and return the table, not raise, exactly like build_players() does
    for players.parquet elsewhere in this package."""
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    (raw_dir / "fixtures.json").write_text(json.dumps(RAW_FIXTURES), encoding="utf-8")

    monkeypatch.setattr(fixtures_mod, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(build_fixtures_mod, "RAW_DIR", raw_dir)
    monkeypatch.setattr(build_fixtures_mod, "PROCESSED_DIR", processed_dir)

    assert not (processed_dir / "fixtures.parquet").exists()
    table = fixtures_mod.load_fixture_table()
    assert len(table) == 4  # 2 fixtures x 2 team-rows each
    assert (processed_dir / "fixtures.parquet").exists()  # self-build persists, same as build_players


def test_load_fixture_table_raises_only_when_raw_file_is_also_missing(tmp_path, monkeypatch):
    """The self-build path itself still raises — via build_fixtures — if
    even the raw fixtures.json doesn't exist, i.e. fpl.collect.fpl_client
    was never run at all. Never silently fabricates fixture data."""
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()

    monkeypatch.setattr(fixtures_mod, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(build_fixtures_mod, "RAW_DIR", raw_dir)
    monkeypatch.setattr(build_fixtures_mod, "PROCESSED_DIR", processed_dir)

    import pytest
    with pytest.raises(FileNotFoundError):
        fixtures_mod.load_fixture_table()


def test_load_fixture_table_reads_existing_parquet_without_rebuilding(tmp_path, monkeypatch):
    """If fixtures.parquet already exists, read it directly — never
    silently rebuild over a real committed artefact."""
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    raw_dir = tmp_path / "raw"  # deliberately has no fixtures.json

    monkeypatch.setattr(fixtures_mod, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(build_fixtures_mod, "RAW_DIR", raw_dir)
    monkeypatch.setattr(build_fixtures_mod, "PROCESSED_DIR", processed_dir)

    table, _ = build_fixtures_mod.build_fixture_table(RAW_FIXTURES), None
    table.to_parquet(processed_dir / "fixtures.parquet", index=False)

    result = fixtures_mod.load_fixture_table()
    assert len(result) == 4
