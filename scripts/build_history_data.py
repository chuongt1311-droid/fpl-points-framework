"""
build_history_data.py — emits dashboard/history.json (spec §7.4).

Reads the Phase G archive through fpl.history.query ONLY. Never calls
the projection pipeline, never writes to data/ — the dashboard contract
is "reads committed artefacts, never recomputes", and unlike
build_dashboard_data.py (see docs/HANDOFF.md §9) this script actually
honours it.

Usage: .venv\\Scripts\\python.exe scripts/build_history_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fpl.history import query  # noqa: E402

DASHBOARD_DIR = REPO_ROOT / "dashboard"

# A revision series needs at least two runs WITHIN one gameweek — one
# point is not a revision. Spec §7.2.
MIN_RUNS_FOR_REVISION = 2
TOP_N_PLAYERS = 12


def build_history_payload() -> dict:
    a = query.open_archive()
    cov = a.coverage()
    runs = a.runs()

    coverage = [] if cov.empty else json.loads(cov.to_json(orient="records"))
    runs_out = [] if runs.empty else json.loads(
        runs[[c for c in ("asof", "asof_iso", "target_gameweek", "trigger",
                          "provenance", "hours_to_deadline", "git_sha")
              if c in runs.columns]].to_json(orient="records")
    )

    max_runs = 0 if cov.empty else int(cov["n_complete"].max())
    revision = {
        "sufficient": max_runs >= MIN_RUNS_FOR_REVISION,
        "n_runs_max_in_a_gameweek": max_runs,
        "min_required": MIN_RUNS_FOR_REVISION,
        "event": None,
        "series": [],
    }

    if revision["sufficient"]:
        busiest_gw = int(cov.sort_values("n_complete", ascending=False)["gw"].iloc[0])
        revision["event"] = busiest_gw
        rev = a.revisions(event=busiest_gw)
        if not rev.empty and "code" in rev.columns:
            latest = rev.sort_values("asof").groupby("code")["xpts"].last()
            top = latest.sort_values(ascending=False).head(TOP_N_PLAYERS).index
            for code in top:
                sub = rev[rev["code"] == code].sort_values("asof")
                name = sub["web_name"].iloc[0] if "web_name" in sub.columns else str(code)
                revision["series"].append({
                    "code": int(code),
                    "web_name": name,
                    "asof": list(sub["asof"]),
                    "xpts": [round(float(v), 2) for v in sub["xpts"]],
                })

    return {
        "generated_note": "Read from data/history/ via fpl.history.query — never recomputed.",
        "coverage": coverage,
        "runs": runs_out,
        "revision": revision,
    }


def main() -> int:
    payload = build_history_payload()
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    out = DASHBOARD_DIR / "history.json"
    out.write_text(json.dumps(payload, indent=None, default=str), encoding="utf-8")
    print(f"Wrote {out} — {len(payload['coverage'])} gameweek(s), "
          f"{len(payload['runs'])} complete run(s), "
          f"revision sufficient={payload['revision']['sufficient']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
