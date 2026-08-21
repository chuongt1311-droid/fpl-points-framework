"""
Tests for dashboard/live_data.py (v3 plan §E — the live Streamlit decision
layer's data-loading half). Covers the pure, non-UI functions: which
models/gameweeks are actually available, artefact hashing, and the
append-only live-solve log (plan §E3).

Pure unit tests: temp directories, no real Streamlit runtime, no network.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

from dashboard import live_data


def test_available_gameweeks_finds_both_root_and_model_subdir_parquets(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "PROJECTIONS_DIR", tmp_path)
    (tmp_path / "gw1.parquet").write_bytes(b"x")
    (tmp_path / "gw3.parquet").write_bytes(b"x")
    sub = tmp_path / "m2_xg"
    sub.mkdir()
    (sub / "gw2.parquet").write_bytes(b"x")

    assert live_data.available_gameweeks() == [1, 2, 3]


def test_available_models_only_lists_models_with_a_committed_file_for_that_gw(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "PROJECTIONS_DIR", tmp_path)
    (tmp_path / "gw1.parquet").write_bytes(b"x")  # m0_rules only
    sub = tmp_path / "m2_xg"
    sub.mkdir()
    (sub / "gw1.parquet").write_bytes(b"x")
    # m3_understat has NO gw1 file at all

    models = live_data.available_models(1)
    assert "m0_rules" in models
    assert "m2_xg" in models
    assert "m3_understat" not in models


def test_available_models_is_gameweek_specific():
    """A model with a file for GW1 but not GW2 shouldn't be offered for GW2
    — same reasoning as the real repo's own state (m2_xg only has gw1
    right now, no gw2 projection exists yet)."""
    # against the REAL repo tree — no monkeypatch — a nonexistent far-future
    # gameweek must return an empty list, not raise or fabricate a model.
    assert live_data.available_models(9999) == []


def test_artefact_hash_changes_when_file_content_changes(tmp_path):
    path = tmp_path / "f.parquet"
    path.write_bytes(b"version one")
    hash_a = live_data._artefact_hash(path)
    path.write_bytes(b"version two")
    hash_b = live_data._artefact_hash(path)
    assert hash_a != hash_b


def test_artefact_hash_is_stable_for_identical_content(tmp_path):
    path = tmp_path / "f.parquet"
    path.write_bytes(b"same content")
    assert live_data._artefact_hash(path) == live_data._artefact_hash(path)


def test_log_live_solve_appends_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "SCRATCH_DIR", tmp_path)
    live_data.log_live_solve({"n_results": 1})
    live_data.log_live_solve({"n_results": 2})

    log_path = tmp_path / "live_solves.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["n_results"] == 1
    assert json.loads(lines[1])["n_results"] == 2


def test_load_canonical_recommendation_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "OUTPUT_DIR", tmp_path)
    assert live_data.load_canonical_recommendation(999) is None


def test_load_canonical_recommendation_reads_the_real_committed_gw1_file():
    """Against the real repo tree — no monkeypatch — GW1's recommendation
    genuinely exists (this session produced it) and must be readable."""
    result = live_data.load_canonical_recommendation(1)
    assert result is not None
    assert "captain" in result
    assert "squad" in result
