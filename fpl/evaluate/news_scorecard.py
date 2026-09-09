"""
news_scorecard.py — EVALUATE layer. Relative Brier score of M6 (m6_news)
vs M0 (m0_rules) minutes_factor predictions against the actual `starts`
flag, for every settled gameweek (C2 / PROJECT_LOG §22).

The number that matters is `brier_*_newsonly` — the subset of players who
had a parsed news signal that week, where M6 and M0 actually differ. On
the full population M6 ≈ M0 by construction.

This recomputes minutes_factor for both models on the CURRENT
players.parquet snapshot and scores it against that GW's actuals — a
same-inputs comparison, not a point-in-time one. Good enough for a
relative signal; feeds docs/M6_PREREGISTRATION.md's promotion check.

`python -m fpl.evaluate.news_scorecard all` drives it from the pipeline.
Never raises on a missing input — skips the GW.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTUALS_DIR = ROOT / "data" / "actuals"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "data" / "output"


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def brier(pred: pd.Series, actual: pd.Series) -> float:
    p = pd.to_numeric(pred, errors="coerce").to_numpy(dtype=float)
    a = pd.to_numeric(actual, errors="coerce").to_numpy(dtype=float)
    mask = ~np.isnan(p) & ~np.isnan(a)
    if mask.sum() == 0:
        return float("nan")
    return float(np.mean((p[mask] - a[mask]) ** 2))


def _settled_gws(bootstrap: dict) -> list[int]:
    return [e["id"] for e in bootstrap.get("events", [])
            if e.get("finished") and e.get("data_checked")]


def _write(result: dict) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "news_scorecard.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def compute_news_scorecard(bootstrap: dict, config: Optional[dict] = None) -> dict:
    config = config or load_config()
    season = config["season"]
    actuals_path = ACTUALS_DIR / f"actuals_{season}.csv"
    players_path = PROCESSED_DIR / "players.parquet"

    if not actuals_path.exists() or not players_path.exists():
        return _write({"per_gw": [], "window": {}})

    actuals = pd.read_csv(actuals_path)
    if "starts" not in actuals.columns:
        return _write({"per_gw": [], "window": {}})
    players = pd.read_parquet(players_path)

    from fpl.project import minutes as minutes_mod
    from fpl.project import news as news_mod

    m0 = minutes_mod.compute_minutes_factor(players, config, model="m0_rules").set_index("id")["minutes_factor"]
    try:
        m6 = minutes_mod.compute_minutes_factor(players, config, model="m6_news").set_index("id")["minutes_factor"]
    except Exception:
        m6 = m0.copy()

    news_sig, _ = news_mod.parse_news(players, config)
    news_ids = set(news_sig.loc[news_sig["news_start_prob"].notna(), "id"])

    per_gw: list[dict] = []
    for gw in _settled_gws(bootstrap):
        a = actuals[actuals["event"] == gw]
        if a.empty:
            continue
        starts = a.set_index("id")["starts"]
        j0 = m0.reindex(starts.index)
        j6 = m6.reindex(starts.index)
        news_mask = starts.index.isin(news_ids)
        per_gw.append({
            "event": int(gw),
            "brier_m0_full": brier(j0, starts),
            "brier_m6_full": brier(j6, starts),
            "brier_m0_newsonly": brier(j0[news_mask], starts[news_mask]),
            "brier_m6_newsonly": brier(j6[news_mask], starts[news_mask]),
            "n_news": int(news_mask.sum()),
        })

    def _agg(key: str) -> float:
        vals = [(g[key], g["n_news"]) for g in per_gw if not np.isnan(g[key])]
        n = sum(w for _, w in vals)
        return float(sum(v * w for v, w in vals) / n) if n else float("nan")

    window = {
        "brier_m0_newsonly": _agg("brier_m0_newsonly"),
        "brier_m6_newsonly": _agg("brier_m6_newsonly"),
        "n_news": sum(g["n_news"] for g in per_gw),
    }
    return _write({"per_gw": per_gw, "window": window})


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "all":
        raw = ROOT / "data" / "raw" / "bootstrap_static.json"
        r = compute_news_scorecard(json.loads(raw.read_text(encoding="utf-8")))
        print(f"[news_scorecard] {r['window']}")
    else:
        print("usage: python -m fpl.evaluate.news_scorecard all")
