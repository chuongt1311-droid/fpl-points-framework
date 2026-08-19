# FPL Points-Maximization Framework

A rules-based, fully explainable projection and decision system for Fantasy
Premier League — weekly xPts per player, DEFCON-aware, DGW/BGW-aware, chip
timing, squad optimisation, and transfer recommendations. Full spec in
[docs/FPL_EXECUTION_PLAN.md](docs/FPL_EXECUTION_PLAN.md).

## Status

Phase 0 (scaffold) + Phase 1 (data layer) + Phase 2 (profile research) done.
See the plan's §9 phase table for the full build sequence and exit gates.

## Architecture

```
COLLECT (fpl/collect)  → TRANSFORM (fpl/transform) → PROJECT (fpl/project)
→ DECIDE (fpl/decide)  → SURFACE (dashboard/)
```

The dashboard never runs the model — GitHub Actions runs the pipeline weekly
and commits artefacts to `data/`; Streamlit only reads them. See plan §2.

## Setup

```bash
pip install -r requirements.txt
```

## Running the data layer (Phase 1)

```bash
python -m fpl.collect.fpl_client       # pulls bootstrap-static + fixtures -> data/raw/
python -m fpl.collect.history_loader   # pulls last 3 seasons from vaastav archive -> data/raw/history/
python -m fpl.transform.build_players  # -> data/processed/players.parquet
python -m fpl.transform.build_fixtures # -> data/processed/fixtures.parquet, dgw_bgw_grid.parquet
```

## Verifying the Phase 1 exit gate

```bash
python tests/verify_phase1.py
```

## Profile research (Phase 2)

See [notebooks/01_profile_research.ipynb](notebooks/01_profile_research.ipynb)
for the channel-driver and DEFCON threshold-rate findings, run against the
real 3-season historical pull.

## Known limitations (v1)

See plan §10 — no BPS simulation, no xG/xA regression layer, new-signing cold
start, no price-change modelling, no ownership/differential strategy,
backward-looking rotation risk, FDR lags early-season form. These are
surfaced in the dashboard's Model Health view (Phase 4+), not hidden.
