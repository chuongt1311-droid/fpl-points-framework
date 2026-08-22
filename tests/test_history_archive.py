"""
Tests for fpl/history/archive.py — spec §3.5.

The two behaviours that matter most:
  1. Partitions are immutable — writing into an existing one RAISES.
  2. run.json is written LAST, as a completion marker. A run that dies
     mid-write leaves partitions with no run.json, which the query layer
     treats as incomplete rather than silently analysing as whole.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from fpl.history import archive, paths

NOW = datetime(2026, 8, 22, 12, 53, 14, tzinfo=timezone.utc)
ASOF = "20260822T125314Z"


def _seed(tmp_path, monkeypatch, *, with_challengers=True):
    """Build a fake pipeline output tree and point every module constant at it."""
    hist = tmp_path / "history"
    proj = tmp_path / "projections"
    out = tmp_path / "output"
    processed = tmp_path / "processed"
    raw = tmp_path / "raw"
    for d in (proj, out, processed, raw):
        d.mkdir(parents=True)

    pd.DataFrame({"id": [1, 2], "event": [1, 1], "xpts": [5.0, 6.0]}).to_parquet(
        proj / "gw1.parquet", index=False
    )
    if with_challengers:
        (proj / "m2_xg").mkdir()
        pd.DataFrame({"id": [1, 2], "event": [1, 1], "xpts": [5.5, 6.5]}).to_parquet(
            proj / "m2_xg" / "gw1.parquet", index=False
        )

    (out / "gw1_recommendations.json").write_text(
        json.dumps({"gameweek": 1, "next_gw_expected_points": 66.2}), encoding="utf-8"
    )
    (out / "model_health.json").write_text(json.dumps({"overall_rmse": 20.9}), encoding="utf-8")
    if with_challengers:
        (out / "model_health_m2_xg.json").write_text(
            json.dumps({"overall_rmse": 20.6}), encoding="utf-8"
        )

    pd.DataFrame({"id": [1, 2], "code": [154561, 109745], "web_name": ["Raya", "Ari"]}).to_parquet(
        processed / "players.parquet", index=False
    )
    (raw / "bootstrap_static.json").write_text(
        json.dumps({"events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z",
                                "finished": False, "is_next": True}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    monkeypatch.setattr(archive, "PROJECTIONS_DIR", proj)
    monkeypatch.setattr(archive, "OUTPUT_DIR", out)
    monkeypatch.setattr(archive, "PROCESSED_DIR", processed)
    monkeypatch.setattr(archive, "RAW_DIR", raw)
    return hist


def test_archive_writes_expected_partitions(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg" / "players.parquet").exists()
    assert (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").exists()
    assert (hist / "health" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "model_health.json").exists()
    assert (hist / "_runs" / f"asof={ASOF}" / "run.json").exists()


def test_archived_content_is_byte_identical(tmp_path, monkeypatch):
    """Spec §3.3: names are normalized, CONTENT is copied byte-for-byte."""
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    src = (tmp_path / "output" / "gw1_recommendations.json").read_bytes()
    dst = (hist / "decisions" / "gw=1" / f"asof={ASOF}" / "recommendation.json").read_bytes()
    assert src == dst


def test_id_code_map_sidecar_written(tmp_path, monkeypatch):
    """Spec §3.4 — projections carry id but not code, and code is the only
    season-stable key. The sidecar is what keeps the archive joinable
    across a season boundary."""
    hist = _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    m = pd.read_parquet(hist / "_runs" / f"asof={ASOF}" / "id_code_map.parquet")
    assert set(m.columns) >= {"id", "code"}
    assert m.loc[m["id"] == 1, "code"].iloc[0] == 154561


def test_refuses_to_overwrite_an_existing_partition(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    archive.archive_run(now=NOW)
    with pytest.raises(archive.PartitionExistsError):
        archive.archive_run(now=NOW)


def test_run_json_written_last_so_partial_runs_are_detectable(tmp_path, monkeypatch):
    """Simulate a crash after partitions are copied but before the marker."""
    hist = _seed(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("simulated crash before completion marker")

    monkeypatch.setattr(archive, "_write_run_json", boom)
    with pytest.raises(RuntimeError):
        archive.archive_run(now=NOW)

    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules" / "players.parquet").exists()
    assert not (hist / "_runs" / f"asof={ASOF}" / "run.json").exists()


def test_run_json_records_provenance_and_archived_list(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch)
    meta = archive.archive_run(now=NOW)
    on_disk = json.loads((hist / "_runs" / f"asof={ASOF}" / "run.json").read_text(encoding="utf-8"))
    assert on_disk == meta
    assert on_disk["provenance"] == "recorded"
    assert on_disk["target_gameweek"] == 1
    assert on_disk["hours_to_deadline"] < 0  # GW1 deadline already passed
    assert len(on_disk["archived"]) >= 4


def test_missing_challenger_models_are_skipped_not_faked(tmp_path, monkeypatch):
    hist = _seed(tmp_path, monkeypatch, with_challengers=False)
    archive.archive_run(now=NOW)
    assert (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m0_rules").exists()
    assert not (hist / "projections" / "gw=1" / f"asof={ASOF}" / "model=m2_xg").exists()
