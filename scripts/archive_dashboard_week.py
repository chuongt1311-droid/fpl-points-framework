"""
archive_dashboard_week.py — snapshot each gameweek's dashboard.

dashboard/index.html is a single, fully self-contained file (data + history
inlined into the template by build_dashboard_data.py). So a weekly snapshot
is a byte copy — no re-render, no dependency on data/history/.

  dashboard/weeks/gw{N}.html   — one frozen snapshot per gameweek
  dashboard/weeks/index.html   — generated list, newest first

Runs right after build_dashboard_data.py (locally or in weekly.yml). During
a gameweek the pipeline runs ~4x, each overwriting gw{N}.html with the
freshest pre-deadline view; once the deadline passes and the dashboard
advances to N+1, gw{N}.html is never touched again and stays frozen.

Run: .venv\\Scripts\\python.exe scripts/archive_dashboard_week.py
"""
from __future__ import annotations

import html as html_mod
import json
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "dashboard"

_DATA_RE = re.compile(r"^const DATA = (\{.*\});$", re.MULTILINE)
_GW_FILE_RE = re.compile(r"^gw(\d+)\.html$")


def _extract_meta(snapshot_html: str) -> dict:
    """Pull the summary fields out of an archived snapshot's inlined DATA.
    build_dashboard_data.py writes DATA as a single line, so one regex is
    enough; a snapshot we can't parse still gets listed, just bare."""
    m = _DATA_RE.search(snapshot_html)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    meta = data.get("meta", {})
    squad = data.get("squad", {})
    captain = (squad.get("captain") or {}).get("web_name")
    return {
        "gameweek": meta.get("gameweek"),
        "season": meta.get("season"),
        "deadline_utc": meta.get("deadline_utc"),
        "xi_total": squad.get("next_gw_expected_points"),
        "captain": captain,
    }


def archive_week(dashboard_dir: Path, gameweek: int) -> Path:
    """Copy the current index.html to weeks/gw{gameweek}.html (overwrite)."""
    src = dashboard_dir / "index.html"
    if not src.exists():
        raise FileNotFoundError(f"{src} not found — run build_dashboard_data.py first.")
    weeks = dashboard_dir / "weeks"
    weeks.mkdir(parents=True, exist_ok=True)
    dst = weeks / f"gw{gameweek}.html"
    # The snapshot sits inside weeks/, so the template's "weeks/index.html"
    # nav link would resolve to weeks/weeks/index.html. Make it a sibling.
    body = src.read_text(encoding="utf-8").replace('href="weeks/index.html"', 'href="index.html"')
    dst.write_text(body, encoding="utf-8")
    return dst


def _row(meta: dict, filename: str) -> str:
    gw = meta.get("gameweek", "?")
    cap = html_mod.escape(str(meta.get("captain") or "—"))
    total = meta.get("xi_total")
    total_txt = f"{total:.1f}" if isinstance(total, (int, float)) else "—"
    deadline = html_mod.escape(str(meta.get("deadline_utc") or ""))[:10]
    return (
        f'<li><a href="{filename}"><span class="gw">GW{gw}</span>'
        f'<span class="cap">C: {cap}</span>'
        f'<span class="tot">{total_txt} xPts</span>'
        f'<span class="date">{deadline}</span></a></li>'
    )


def rebuild_index(dashboard_dir: Path) -> Path:
    weeks = dashboard_dir / "weeks"
    weeks.mkdir(parents=True, exist_ok=True)

    snaps: list[tuple[int, str, dict]] = []
    for p in weeks.glob("gw*.html"):
        fm = _GW_FILE_RE.match(p.name)
        if not fm:
            continue
        snaps.append((int(fm.group(1)), p.name, _extract_meta(p.read_text(encoding="utf-8"))))
    snaps.sort(key=lambda t: t[0], reverse=True)

    season = next((m.get("season") for _, _, m in snaps if m.get("season")), "")
    body = (
        "<ul class=\"weeks\">" + "".join(_row(m, name) for _, name, m in snaps) + "</ul>"
        if snaps
        else '<p class="empty">No weekly snapshots yet — they appear here after the first pipeline run.</p>'
    )

    index = weeks / "index.html"
    index.write_text(_PAGE.replace("{{SEASON}}", html_mod.escape(season)).replace("{{BODY}}", body),
                     encoding="utf-8")
    return index


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPL dashboard — weekly archive</title>
<style>
  :root{color-scheme:dark;--bg:#0f1216;--card:#171c22;--border:#2a323c;--ink:#edf1f5;--dim:#93a0ad;--accent:#5cc8ff}
  body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:640px;margin:0 auto;padding:32px 20px}
  h1{font-size:19px;margin:0 0 4px} .sub{color:var(--dim);margin:0 0 24px}
  a.back{color:var(--accent);text-decoration:none;font-size:13px}
  ul.weeks{list-style:none;margin:0;padding:0}
  ul.weeks li a{display:grid;grid-template-columns:64px 1fr auto auto;gap:14px;align-items:center;
    padding:13px 14px;margin-bottom:8px;background:var(--card);border:1px solid var(--border);
    border-radius:10px;color:var(--ink);text-decoration:none}
  ul.weeks li a:hover{border-color:var(--accent)}
  .gw{font-weight:700} .cap{color:var(--dim)} .tot{font-variant-numeric:tabular-nums}
  .date{color:var(--dim);font-size:12px} .empty{color:var(--dim)}
</style></head><body><div class="wrap">
<p><a class="back" href="../index.html">&larr; Current week</a></p>
<h1>Weekly archive</h1>
<p class="sub">{{SEASON}} &middot; one frozen snapshot per gameweek</p>
{{BODY}}
</div></body></html>
"""


def main() -> None:
    data_path = DASHBOARD_DIR / "data.json"
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} not found — run build_dashboard_data.py first.")
    gameweek = json.loads(data_path.read_text(encoding="utf-8"))["meta"]["gameweek"]
    snap = archive_week(DASHBOARD_DIR, gameweek)
    index = rebuild_index(DASHBOARD_DIR)
    print(f"Archived {snap.name}; rebuilt {index.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
