"""
query.py — the archive READ model (spec §5).

READ-ONLY. This module never writes to data/history/. Reading history
must never be able to corrupt it — the same discipline as the
FROZEN/LIVE boundary.

No ingest step: DuckDB reads the partitioned Parquet directly, so there
is nothing to migrate and no second copy to fall out of sync.

Every public view is filtered to COMPLETE runs (a run.json exists —
spec §3.5) and joined to `code` (spec §3.4), so no caller can
accidentally analyse a half-written run or key on a season-unstable id.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from fpl.history import paths

_EMPTY_RUNS = pd.DataFrame(columns=[
    "asof", "target_gameweek", "trigger", "provenance", "hours_to_deadline",
    "git_sha", "config_sha256",
])
_EMPTY_COVERAGE = pd.DataFrame(columns=[
    "gw", "n_runs", "n_complete", "n_incomplete", "first_asof", "last_asof",
    "n_reconstructed",
])
_EMPTY_PROJECTIONS = pd.DataFrame(columns=[
    "gw", "asof", "model", "id", "code", "event", "xpts",
])


def _glob(root: Path, *parts: str) -> str:
    return str(root.joinpath(*parts)).replace("\\", "/")


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _connect(self):
        import duckdb  # lazy — the pipeline write path must not need it
        return duckdb.connect(database=":memory:")

    def _asofs(self, *, complete_only: bool) -> list[str]:
        runs_dir = self.root / "_runs"
        if not runs_dir.exists():
            return []
        out = []
        for d in runs_dir.iterdir():
            if not (d.is_dir() and d.name.startswith("asof=")):
                continue
            if complete_only and not (d / "run.json").exists():
                continue
            out.append(d.name.split("=", 1)[1])
        return sorted(out)

    def runs(self) -> pd.DataFrame:
        rows = []
        for asof in self._asofs(complete_only=True):
            p = self.root / "_runs" / f"asof={asof}" / "run.json"
            try:
                rows.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if not rows:
            return _EMPTY_RUNS.copy()
        df = pd.DataFrame(rows)
        if "archived" in df.columns:
            df = df.drop(columns=["archived"])
        return df.sort_values("asof").reset_index(drop=True)

    def _id_code_map(self) -> pd.DataFrame:
        frames = []
        for asof in self._asofs(complete_only=True):
            p = paths.id_code_map_path(asof)
            if p.exists():
                m = pd.read_parquet(p)
                m["asof"] = asof
                frames.append(m)
        if not frames:
            return pd.DataFrame(columns=["id", "code", "web_name", "asof"])
        return pd.concat(frames, ignore_index=True)

    def projections(self, gw: Optional[int] = None, model: Optional[str] = None,
                    asof: Optional[str] = None, event: Optional[int] = None) -> pd.DataFrame:
        complete = self._asofs(complete_only=True)
        if not complete:
            return _EMPTY_PROJECTIONS.copy()

        pattern = _glob(self.root, "projections", "**", "*.parquet")
        if not any(self.root.glob("projections/**/*.parquet")):
            # Genuinely nothing archived yet — an empty result, not an error.
            # Checked in Python rather than by catching DuckDB's IOException,
            # so a real read failure still surfaces instead of silently
            # looking like an empty archive.
            return _EMPTY_PROJECTIONS.copy()

        con = self._connect()
        try:
            # PARAMETER BINDING, never f-string interpolation: this repo's
            # own path contains an apostrophe ("D:\CT's Portfolio\..."),
            # which terminates a SQL string literal and made every real
            # query fail while tmp_path-based tests passed.
            df = con.execute(
                "SELECT * FROM read_parquet(?, hive_partitioning=true)", [pattern]
            ).fetch_df()
        finally:
            con.close()

        if df.empty:
            return _EMPTY_PROJECTIONS.copy()

        df["gw"] = df["gw"].astype(int)
        df = df[df["asof"].isin(complete)]

        cmap = self._id_code_map()
        if not cmap.empty:
            cols = ["id", "code", "asof"] + (["web_name"] if "web_name" in cmap.columns else [])
            df = df.merge(cmap[cols], on=["id", "asof"], how="left", suffixes=("", "_map"))

        if gw is not None:
            df = df[df["gw"] == gw]
        if model is not None:
            df = df[df["model"] == model]
        if asof is not None:
            df = df[df["asof"] == asof]
        if event is not None:
            df = df[df["event"] == event]
        return df.reset_index(drop=True)

    def revisions(self, event: int, model: str = "m0_rules",
                  player_code: Optional[int] = None) -> pd.DataFrame:
        """xPts for ONE future gameweek (`event`), as seen at each as-of time.

        Deliberately keyed on `event`, not `gw` (spec §3.3a): the same
        event is projected by every run whose 5-GW horizon covers it, so
        a revision series spans multiple `gw` partitions.
        """
        df = self.projections(model=model, event=event)
        if df.empty:
            return df
        if player_code is not None and "code" in df.columns:
            df = df[df["code"] == player_code]
        return df.sort_values("asof").reset_index(drop=True)

    def _infer_gw_from_partitions(self, asof: str) -> Optional[int]:
        proj = self.root / "projections"
        if not proj.exists():
            return None
        for gw_dir in sorted(proj.iterdir()):
            if gw_dir.is_dir() and (gw_dir / f"asof={asof}").exists():
                try:
                    return int(gw_dir.name.split("=", 1)[1])
                except (IndexError, ValueError):
                    continue
        return None

    def coverage(self) -> pd.DataFrame:
        all_asofs = self._asofs(complete_only=False)
        if not all_asofs:
            return _EMPTY_COVERAGE.copy()

        rows = []
        for asof in all_asofs:
            rj = self.root / "_runs" / f"asof={asof}" / "run.json"
            complete = rj.exists()
            gw, prov = None, None
            if complete:
                try:
                    meta = json.loads(rj.read_text(encoding="utf-8"))
                    gw, prov = meta.get("target_gameweek"), meta.get("provenance")
                except (OSError, ValueError):
                    complete = False
            if gw is None:
                gw = self._infer_gw_from_partitions(asof)
            rows.append({"asof": asof, "gw": gw, "complete": complete,
                         "reconstructed": prov == "reconstructed"})

        df = pd.DataFrame(rows)
        df = df[df["gw"].notna()]
        if df.empty:
            return _EMPTY_COVERAGE.copy()
        df["gw"] = df["gw"].astype(int)

        out = df.groupby("gw").agg(
            n_runs=("asof", "count"),
            n_complete=("complete", "sum"),
            first_asof=("asof", "min"),
            last_asof=("asof", "max"),
            n_reconstructed=("reconstructed", "sum"),
        ).reset_index()
        out["n_complete"] = out["n_complete"].astype(int)
        out["n_reconstructed"] = out["n_reconstructed"].astype(int)
        out["n_incomplete"] = out["n_runs"] - out["n_complete"]
        return out[["gw", "n_runs", "n_complete", "n_incomplete",
                    "first_asof", "last_asof", "n_reconstructed"]]

    def sql(self, q: str) -> pd.DataFrame:
        con = self._connect()
        try:
            return con.execute(q).fetch_df()
        finally:
            con.close()


def open_archive(root: Optional[Path] = None) -> Archive:
    return Archive(Path(root) if root is not None else paths.HISTORY_DIR)
