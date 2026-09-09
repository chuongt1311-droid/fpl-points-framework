"""
_pitch - the shared formation-pitch renderer (C1 / PROJECT_LOG §23).

Used by dashboard/app2 (Jinja calls render_pitch) and the static
dashboard/template.html (PITCH_CSS inlined at build time, markup rendered
client-side by a JS port of the same chip shape). Pure string building -
no I/O beyond reading its own CSS file at import.
"""
from __future__ import annotations

import html
from pathlib import Path

PITCH_CSS = (Path(__file__).parent / "pitch.css").read_text(encoding="utf-8")

_ROW_ORDER = ["GK", "DEF", "MID", "FWD"]


def _chip(p: dict, mode: str) -> str:
    name = html.escape(str(p.get("web_name", "?")))
    club = html.escape(str(p.get("team_short", "")))
    cls = "chip is-cap" if p.get("is_captain") else "chip"
    data_id = f' data-id="{int(p["id"])}"' if mode == "transfers" and "id" in p else ""

    if mode == "points":
        stat = f'<div class="cs">{p.get("pts", 0)}</div>'
        extra = ""
    else:
        stat = f'<div class="cs">{p.get("xpts", "—")}</div>'
        verdict = p.get("verdict")
        extra = f'<div class="cv {verdict}">{html.escape(str(verdict))}</div>' if verdict else ""
        sp = p.get("start_prob")
        if sp is not None:
            pct = max(0, min(100, round(float(sp) * 100)))
            extra += f'<div class="startbar"><i style="width:{pct}%"></i></div>'

    return (
        f'<div class="{cls}"{data_id}>'
        f'<div class="kit"></div>'
        f'<div class="cn">{name}</div><div class="cl">{club}</div>'
        f'{stat}{extra}</div>'
    )


def render_pitch(xi: list[dict], bench: list[dict], *, mode: str = "pick") -> str:
    """Formation-row HTML for a squad. Each player dict needs at least
    `web_name`, `position` ("GK"|"DEF"|"MID"|"FWD"), `team_short`. `mode`:
    "points" -> `pts`; "pick" -> `xpts` (+ optional `verdict`, `start_prob`);
    "transfers" -> `xpts` + a `data-id` for selection."""
    rows = []
    for pos in _ROW_ORDER:
        players = [p for p in xi if p.get("position") == pos]
        if players:
            rows.append('<div class="pitch-row">' + "".join(_chip(p, mode) for p in players) + "</div>")
    bench_html = ""
    if bench:
        bench_html = '<div class="pitch-bench">' + "".join(_chip(p, mode) for p in bench) + "</div>"
    return '<div class="pitch">' + "".join(rows) + bench_html + "</div>"
