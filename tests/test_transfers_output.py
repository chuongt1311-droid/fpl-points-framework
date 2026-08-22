"""
Tests for the transfer recommendation artefact (spec §6) and its capture
by the Phase G archive.

Run: .venv\\Scripts\\python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json

from fpl.decide import transfers

REC = {
    "recommendation": "transfer", "caveats": [], "free_transfers": 1, "bank": 0.0,
    "n_transfers": 1, "hits": 0, "hit_points": 0,
    "transfers": [{"out": {"id": 497, "web_name": "X", "position": "MID",
                           "price": 6.0, "sell_price": 6.0},
                   "in": {"id": 302, "web_name": "Y", "position": "MID", "price": 6.5}}],
    "weighted_gain": 3.21, "next_gw_gain": -0.14,
    "baseline": {"weighted": 223.8, "next_gw": 64.56},
    "after": {"weighted": 227.01, "next_gw": 64.42},
    "squad": list(range(15)), "starting_xi": list(range(11)),
    "captain": 0, "vice_captain": 1,
}


def test_write_recommendation_emits_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(transfers, "OUTPUT_DIR", tmp_path)
    p = transfers.write_recommendation(
        REC, gw=2, entry_id=6669718,
        extra={"sell_price_source": "my_team_file", "my_team_file_age_hours": 2.1},
    )
    assert p.name == "gw2_transfers.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["gameweek"] == 2
    assert d["entry_id"] == 6669718
    assert d["recommendation"] == "transfer"
    assert d["weighted_gain"] == 3.21
    assert d["next_gw_gain"] == -0.14      # negative is reported, not hidden
    assert d["sell_price_source"] == "my_team_file"
    assert d["my_team_file_age_hours"] == 2.1


def test_roll_recommendation_still_states_the_gain(tmp_path, monkeypatch):
    monkeypatch.setattr(transfers, "OUTPUT_DIR", tmp_path)
    roll = dict(REC, recommendation="roll", n_transfers=0, transfers=[], weighted_gain=0.0)
    d = json.loads(transfers.write_recommendation(
        roll, gw=2, entry_id=1, extra={}).read_text(encoding="utf-8"))
    assert d["recommendation"] == "roll"
    assert d["transfers"] == []
    assert d["weighted_gain"] == 0.0


def test_archive_discovers_transfer_artefacts(tmp_path, monkeypatch):
    """Phase G must capture transfer recommendations like any other
    decision artefact, or the bitemporal record has a hole in it."""
    from fpl.history import archive

    out = tmp_path / "output"
    proj = tmp_path / "projections"
    raw = tmp_path / "raw"
    for d in (out, proj, raw):
        d.mkdir(parents=True)
    (out / "gw2_transfers.json").write_text(json.dumps({"gameweek": 2}), encoding="utf-8")
    (raw / "bootstrap_static.json").write_text(json.dumps(
        {"events": [{"id": 2, "deadline_time": "2026-08-28T17:30:00Z",
                     "finished": False, "is_next": True}]}), encoding="utf-8")

    monkeypatch.setattr(archive, "OUTPUT_DIR", out)
    monkeypatch.setattr(archive, "PROJECTIONS_DIR", proj)
    monkeypatch.setattr(archive, "RAW_DIR", raw)

    found = archive.discover_artefacts()
    assert (2, out / "gw2_transfers.json") in found["transfers"]


def test_archive_writes_transfer_partition(tmp_path, monkeypatch):
    import pandas as pd
    from fpl.history import archive, paths

    hist = tmp_path / "history"
    out = tmp_path / "output"
    proj = tmp_path / "projections"
    processed = tmp_path / "processed"
    raw = tmp_path / "raw"
    for d in (out, proj, processed, raw):
        d.mkdir(parents=True)

    pd.DataFrame({"id": [1], "event": [2], "xpts": [5.0]}).to_parquet(
        proj / "gw2.parquet", index=False)
    (out / "gw2_transfers.json").write_text(json.dumps({"gameweek": 2}), encoding="utf-8")
    pd.DataFrame({"id": [1], "code": [111], "web_name": ["A"]}).to_parquet(
        processed / "players.parquet", index=False)
    (raw / "bootstrap_static.json").write_text(json.dumps(
        {"events": [{"id": 2, "deadline_time": "2026-08-28T17:30:00Z",
                     "finished": False, "is_next": True}]}), encoding="utf-8")

    monkeypatch.setattr(paths, "HISTORY_DIR", hist)
    monkeypatch.setattr(archive, "OUTPUT_DIR", out)
    monkeypatch.setattr(archive, "PROJECTIONS_DIR", proj)
    monkeypatch.setattr(archive, "PROCESSED_DIR", processed)
    monkeypatch.setattr(archive, "RAW_DIR", raw)

    meta = archive.archive_run()
    asof = meta["asof"]
    assert (hist / "transfers" / "gw=2" / f"asof={asof}" / "transfers.json").exists()
    assert any(a["domain"] == "transfers" for a in meta["archived"])
