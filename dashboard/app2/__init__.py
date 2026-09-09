"""
dashboard/app2 — rough demo of the "replace live_server entirely" dashboard
(brainstorm approved 2026-08-25, approach A: Flask + htmx/Alpine + inline
SVG charts, no build step). Scope deliberately small: squad view with a
live overlay, one player's xPts channel breakdown (model transparency),
and a fixture-difficulty radar for the squad's clubs. History/EO/transfer
log come after this proves the shape out.

Run: .venv\\Scripts\\python.exe -m dashboard.app2
"""
from __future__ import annotations

import os

from flask import Flask, render_template, request

from . import data as data_mod


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        squad = data_mod.current_squad_view()
        radar = data_mod.fixture_radar()
        return render_template("index.html", squad=squad, radar=radar, active="squad")

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
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, port=port)
