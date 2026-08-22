"""
Tests for scripts/migrate_crude_archive.py — spec §6.

The Tier 0.2 crude step wrote data/history/{TIMESTAMP}/{output,projections}/…
with no gw partitioning and no provenance. This migrates it into the real
layout, reconstructing ONLY what is genuinely derivable and writing null
for everything else — a guessed config hash would be worse than no hash,
because it would make an unreproducible run look reproducible.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpl.history import paths
import scripts.migrate_crude_archive as mig

ASOF = "20260822T125314Z"


def _seed_crude(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    crude = hist / ASOF
    (crude / "projections" / "m2_xg").mkdir(parents=True)
    (crude / "output").mkdir(parents=True)

    pd.DataFrame({"id": [1], "event": [1], "xpts": [5.0]}).to_parquet(
        crude / "projections" / "gw1.parquet", index=False
    )
    pd.DataFrame({"id": [1], "event": [1], "xpts": [5.5]}).to_parquet(
        crude / "projections" / "m2_xg" / "gw1.parquet", index=False
    )
    (crude / "output" / "gw1_recommendations.json").write_text(
        json.dumps({"gameweek": 1}), encoding="utf-8"
    )
    (crude / "output" / "model_health.json").write_text(
        json.dumps({"overall_rmse": 20.9}), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    monkeypatch.setattr(mig, "HISTORY_DIR", hist)
    return hist, crude


def test_finds_crude_timestamp_dirs_only(tmp_path, monkeypatch):
    hist, _ = _seed_crude(tmp_path, monkeypatch)
    (hist / "projections").mkdir(exist_ok=True)
    (hist / "_runs").mkdir(exist_ok=True)
    assert [p.name for p in mig.find_crude_dirs()] == [ASOF]


def test_migrates_into_hive_layout(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc="2026-08-21T17:30:00Z", trigger="workflow_dispatch")

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg" / "players.parquet").exists()
    assert (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").exists()
    assert (hist / "health" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "model_health.json").exists()


def test_marks_provenance_reconstructed_and_nulls_the_unknowable(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc="2026-08-21T17:30:00Z", trigger="workflow_dispatch")
    meta = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))

    assert meta["provenance"] == "reconstructed"
    assert meta["trigger"] == "workflow_dispatch"
    assert meta["asof"] == ASOF
    assert meta["target_gameweek"] == 1
    assert round(meta["hours_to_deadline"], 1) == -19.4
    # Not recoverable after the fact — must be null, never guessed.
    assert meta["config_sha256"] is None
    assert meta["git_dirty"] is None


def test_unknown_trigger_is_null_not_guessed(tmp_path, monkeypatch):
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc=None, trigger=None)
    meta = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))
    assert meta["trigger"] is None
    assert meta["hours_to_deadline"] is None


def test_writes_id_code_sidecar_when_players_parquet_available(tmp_path, monkeypatch):
    """Without this, migrated partitions have null `code` and cannot be
    joined across a season boundary — the identity-mapping failure mode."""
    hist, crude = _seed_crude(tmp_path, monkeypatch)
    players = tmp_path / "players.parquet"
    pd.DataFrame({"id": [1], "code": [154561], "web_name": ["Raya"]}).to_parquet(players, index=False)

    meta = mig.migrate_one(crude, deadline_utc=None, trigger=None, players_parquet=players)

    assert meta["id_code_map"] == "reconstructed_from_current_season"
    m = pd.read_parquet(hist / "_runs" / f"asof={ASOF}" / "id_code_map.parquet")
    assert m.loc[m["id"] == 1, "code"].iloc[0] == 154561


def test_missing_players_parquet_is_flagged_not_silently_skipped(tmp_path, monkeypatch):
    _, crude = _seed_crude(tmp_path, monkeypatch)
    meta = mig.migrate_one(crude, deadline_utc=None, trigger=None,
                           players_parquet=tmp_path / "absent.parquet")
    assert meta["id_code_map"] is None


def test_refuses_to_migrate_twice(tmp_path, monkeypatch):
    _, crude = _seed_crude(tmp_path, monkeypatch)
    mig.migrate_one(crude, deadline_utc=None, trigger=None)
    with pytest.raises(mig.PartitionExistsError):
        mig.migrate_one(crude, deadline_utc=None, trigger=None)
