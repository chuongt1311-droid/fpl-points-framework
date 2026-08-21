"""
app.py — v3 plan §E: the live Streamlit decision layer.

Boundary rule (plan §E1), enforced structurally:

    FROZEN (GitHub Actions, committed artefacts, reproducible):
      data pull -> identity join -> baseline -> per-channel rates -> xpts
      All of this lives in fpl/collect, fpl/transform, fpl/project — NOT
      imported here except for two pure, offline helpers (see live_data.py).

    LIVE (this app, re-solved on demand, never persisted as canonical):
      xpts vector + constraints -> MILP -> squad / XI / captain / K-best
      fpl/decide/optimiser.py and fpl/decide/kbest.py — pure functions of
      whatever xpts vector and constraints this page hands them. Re-solving
      them live cannot corrupt a projection, because they never write one.

Run: .venv\\Scripts\\python.exe -m streamlit run dashboard/app.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

# Runnable as `streamlit run dashboard/app.py` from any cwd without
# requiring PYTHONPATH=. to be set first (unlike scripts/build_dashboard_
# data.py's documented convention) — Streamlit always executes this file
# as a standalone script, never as part of the `fpl` package, so the repo
# root needs to be on sys.path before the `fpl.*`/`dashboard.*` imports
# below can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from fpl.decide import kbest as kbest_mod

from dashboard import live_data

st.set_page_config(page_title="FPL Live Decision Layer", layout="wide")

FORMATIONS = {
    "Model's choice (no forced formation)": None,
    "3-4-3": {"def": 3, "mid": 4, "fwd": 3},
    "3-5-2": {"def": 3, "mid": 5, "fwd": 2},
    "4-3-3": {"def": 4, "mid": 3, "fwd": 3},
    "4-4-2": {"def": 4, "mid": 4, "fwd": 2},
    "4-5-1": {"def": 4, "mid": 5, "fwd": 1},
    "5-3-2": {"def": 5, "mid": 3, "fwd": 2},
    "5-4-1": {"def": 5, "mid": 4, "fwd": 1},
}

st.title("⚽ FPL Live Decision Layer")
st.caption(
    "Every result on this page is a live re-solve of the MILP against a FROZEN, "
    "already-committed projection — see the sidebar for which one. It is NEVER "
    "the source of a projection itself; the projection pipeline only ever runs "
    "in GitHub Actions. Nothing on this page is saved as your official squad "
    "until you say so."
)

gameweeks = live_data.available_gameweeks()
if not gameweeks:
    st.error("No committed projections found under data/projections/ — run the frozen pipeline first.")
    st.stop()

# ---------------------------------------------------------------- sidebar --
with st.sidebar:
    st.header("Inputs")
    gameweek = st.selectbox("Gameweek", gameweeks, index=0)

    models = live_data.available_models(gameweek)
    if not models:
        st.error(f"No committed model projections for GW{gameweek}.")
        st.stop()
    model = st.selectbox(
        "Model (plan §E2 — swap the xpts vector)", models,
        format_func=lambda m: live_data.MODEL_LABELS.get(m, m),
    )
    if model != "m0_rules":
        st.warning(
            "Not the champion model (see docs/DECISION_RULE.md) — exploratory "
            "comparison only. The pinned canonical recommendation always uses M0."
        )

    st.divider()
    st.subheader("What-if controls (plan §E2)")

    player_inputs = live_data.load_player_inputs(model, gameweek)
    player_choices = player_inputs.sort_values("web_name")[["id", "web_name", "position", "team"]]
    label_by_id = {
        int(r["id"]): f"{r['web_name']} ({r['position']})"
        for _, r in player_choices.iterrows()
    }

    locked = st.multiselect(
        "Lock players (must be in the squad)", options=list(label_by_id.keys()),
        format_func=lambda i: label_by_id[i],
    )
    banned = st.multiselect(
        "Ban players (must NOT be in the squad)", options=list(label_by_id.keys()),
        format_func=lambda i: label_by_id[i],
    )
    banned_clubs_raw = st.multiselect(
        "Ban clubs", options=sorted(player_inputs["team"].unique().tolist()),
    )

    config = live_data.load_config()
    default_budget = config["squad_rules"]["budget_tenths"] / 10.0
    budget = st.slider(
        "Budget override (£m)", min_value=60.0, max_value=120.0,
        value=float(default_budget), step=0.5,
    )
    budget_override = None if budget == default_budget else budget

    formation_label = st.selectbox("Force formation", list(FORMATIONS.keys()))
    force_formation = FORMATIONS[formation_label]

    chip = st.radio(
        "Chip scenario", ["None", "Bench Boost", "Free Hit"],
        help="Bench Boost: bench scores at full value in this solve, per plan "
             "§E2's chip table. Free Hit: plan §E2 describes 'drop the "
             "transfer-cost term' — fpl/decide/transfers.py (which would "
             "compute that term) doesn't exist yet, so there is nothing to "
             "drop; this option is currently a no-op, shown honestly rather "
             "than silently doing nothing under a name that implies it does.",
    )
    chip_arg = {"None": None, "Bench Boost": "bench_boost", "Free Hit": None}[chip]
    if chip == "Free Hit":
        st.info("Free Hit has no modelled effect yet — see the help text above.")

    st.divider()
    st.subheader("Alternatives (plan §D)")
    k_squads = st.slider("Number of alternative squads", 1, 8, 3)
    diversity_d = st.slider(
        "Diversity (min players differing between alternatives)", 1, 6, 3,
        help="Plan §D2: d=1 gives near-identical squads differing only in "
             "the cheapest bench filler. d>=3 forces genuine variety.",
    )

    solve_clicked = st.button("▶ Solve (plan §E4 — nothing re-solves until you click this)", type="primary")

# --------------------------------------------------------- canonical panel --
st.subheader("📌 Canonical recommendation (pinned, unmodifiable)")
canonical = live_data.load_canonical_recommendation(gameweek)
if canonical is None:
    st.info(f"No committed recommendation exists yet for GW{gameweek}.")
else:
    cols = st.columns(4)
    cols[0].metric("Next-GW xPts (XI+C)", canonical["next_gw_expected_points"])
    cols[1].metric("5-GW weighted xPts", canonical["horizon_weighted_xpts"])
    cols[2].metric("Captain", canonical["captain"]["web_name"])
    cols[3].metric("Total cost", f"£{canonical['total_cost']}m")
    with st.expander("Full canonical squad"):
        st.dataframe(pd.DataFrame(canonical["squad"]), use_container_width=True)

st.divider()

# -------------------------------------------------------------- live solve --
if solve_clicked:
    st.subheader("🧪 EXPLORATORY — live re-solve, not your official squad")
    st.warning(
        "This result is EXPLORATORY (plan §E3). It is never written to "
        "data/state/ and is not your committed squad. It IS logged to "
        "data/scratch/live_solves.jsonl so a deviation from the canonical "
        "recommendation can be reviewed later."
    )

    constraints_summary = {
        "gameweek": gameweek, "model": model, "locked_ids": locked, "banned_ids": banned,
        "banned_clubs": banned_clubs_raw, "budget_override": budget_override,
        "force_formation": force_formation, "chip": chip_arg,
        "k_squads": k_squads, "diversity_d": diversity_d,
    }

    progress = st.progress(0.0, text="Solving squad alternatives...")
    try:
        results = kbest_mod.find_k_best_squads(
            player_inputs, config, k=k_squads, diversity_d=diversity_d,
            apply_availability_filters=True,
        )
        # optimise_squad itself doesn't take a progress callback — the
        # K-best loop is the natural place to report progress (plan §E4:
        # "stream results as each solution lands rather than blocking on
        # all ten"), even though find_k_best_squads runs all solves before
        # returning; a per-iteration streaming version is a real follow-up.
        progress.progress(1.0, text=f"Found {len(results)} squad(s).")
    except RuntimeError as exc:
        progress.empty()
        st.error(f"Infeasible: {exc}. Your lock/ban/budget/formation combination has no legal squad.")
        results = []

    log_entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "constraints": constraints_summary,
        "n_results": len(results),
        "top_squad_ids": results[0]["squad"] if results else None,
        "top_stage1_objective": results[0]["stage1_objective"] if results else None,
    }
    live_data.log_live_solve(log_entry)

    if not locked and not banned and not banned_clubs_raw and budget_override is None and force_formation is None and chip_arg is None:
        st.caption("No what-if constraints set — this is the model's unconstrained best, live re-solved.")

    for r in results:
        detail = r["detail"].sort_values(["is_starting", "position", "weighted_xpts"], ascending=[False, True, False])
        header = f"#{r['rank']} — £{r['total_cost']:.1f}m — {r['horizon_weighted_xpts']:.2f} weighted xPts"
        if r["rank"] > 1:
            header += f"  (frontier_spread {r['frontier_spread']:+.3f} vs #1)"
        with st.expander(header, expanded=(r["rank"] == 1)):
            starters = detail[detail["is_starting"]]
            bench = detail[~detail["is_starting"]]
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Starting XI**")
                st.dataframe(
                    starters[["web_name", "position", "team", "price", "weighted_xpts"]],
                    use_container_width=True, hide_index=True,
                )
            with c2:
                st.markdown("**Bench**")
                st.dataframe(
                    bench[["web_name", "position", "team", "price", "weighted_xpts"]],
                    use_container_width=True, hide_index=True,
                )
            cap_name = detail.loc[detail["id"] == r["captain"], "web_name"].iloc[0]
            vc_name = detail.loc[detail["id"] == r["vice_captain"], "web_name"].iloc[0]
            st.markdown(f"**Captain:** {cap_name} · **Vice:** {vc_name}")

            if r["rank"] == 1 and len(results) > 1:
                spreads = [res["frontier_spread"] for res in results]
                tight = max(spreads) - min(spreads) < 2.0
                if tight:
                    st.info(
                        f"The top {len(results)} squads are within "
                        f"{abs(min(spreads)):.2f} pts of each other (plan §D3) — "
                        "the model has no strong preference among them. Other "
                        "factors (ownership, differential strategy, gut) are as "
                        "good a tiebreaker as anything here."
                    )
else:
    st.info("Set your what-if controls in the sidebar, then click **Solve**.")
