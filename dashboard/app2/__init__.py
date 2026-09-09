"""
dashboard/app2 — the live FPL-page-clone dashboard (C1 / PROJECT_LOG §23;
Flask + htmx, no build step). FPL's own screens — Points, Pick Team,
Transfers, Fixtures, Leagues, History — with the model's xPts / start
probability / verdict overlaid. FROZEN/LIVE split: model output from
committed artefacts + pure re-solves, live overlay from cheap FPL API
GETs (dashboard/app2/data.py). Never calls the projection pipeline.

Run: .venv\\Scripts\\python.exe -m dashboard.app2
"""
from __future__ import annotations

import os

from flask import Flask, render_template, request

from dashboard import _pitch

from . import data as data_mod


def create_app() -> Flask:
    app = Flask(__name__)

    @app.context_processor
    def _inject_pitch_css():
        return {"pitch_css": _pitch.PITCH_CSS}

    @app.route("/")
    def points():
        v = data_mod.points_view()
        pitch = _pitch.render_pitch(v["xi"], v["bench"], mode="points")
        return render_template("points.html", v=v, pitch=pitch, active="points")

    @app.route("/pick")
    def pick():
        v = data_mod.pick_team_view()
        pitch = _pitch.render_pitch(v["xi"], v["bench"], mode="pick")
        return render_template("pick.html", v=v, pitch=pitch, active="pick")

    @app.route("/transfers")
    def transfers():
        # Phase 3 builds the interactive solver here. Shell for now.
        return render_template("transfers.html", active="transfers")

    @app.route("/fixtures")
    def fixtures():
        radar = data_mod.fixture_radar()
        return render_template("fixtures.html", radar=radar, active="fixtures")

    @app.route("/api/breakdown/<int:player_id>")
    def breakdown(player_id: int):
        radar = data_mod.fixture_radar()
        event = radar["events"][0] if radar["events"] else 1
        result = data_mod.channel_breakdown(player_id, event)
        if result is None:
            return render_template("_breakdown_empty.html")
        return render_template("_breakdown.html", b=result)

    @app.route("/history")
    def history():
        h = data_mod.history_view()
        return render_template("history.html", h=h, active="history")

    @app.route("/history/gw/<int:event>")
    def history_gw(event: int):
        snap = data_mod.squad_snapshot(event)
        return render_template("_squad_snapshot.html", s=snap)

    @app.route("/insights")
    def insights():
        league_id = request.args.get("league_id", type=int)
        i = data_mod.insights_view(league_id=league_id)
        return render_template("insights.html", i=i, active="insights")

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
