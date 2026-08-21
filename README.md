# FPL Points-Maximization Framework

A rules-based, fully explainable projection and decision system for Fantasy
Premier League — weekly xPts per player, DEFCON-aware, DGW/BGW-aware, chip
timing, squad optimisation, and a live what-if decision layer. Built in three
stages, each with its own locked spec:

- [docs/FPL_EXECUTION_PLAN.md](docs/FPL_EXECUTION_PLAN.md) — v1: the core
  rules-based projection + MILP optimiser.
- [docs/FPL_V2_DESIGN.md](docs/FPL_V2_DESIGN.md) — v2: the measurement layer
  (availability snapshots, regret decomposition, shrinkage, calibration).
- [docs/FPL_V3_PLAN.md](docs/FPL_V3_PLAN.md) — v3: multi-source data, a model
  bakeoff, and a live Streamlit decision layer.

For exactly what's built vs. not, read [docs/HANDOFF.md](docs/HANDOFF.md)
(forward-looking — what to know before extending this) and
[docs/PROJECT_LOG.md](docs/PROJECT_LOG.md) (backward-looking — the full
chronological record, commit by commit, including real bugs found and
honest negative results). Both are kept current every session; this file
is the short version.

## Status (as of 2026-08-21, GW1)

**v1** (Phases 0–4) and **v2** (measurement layer + statistical core) are
done. **v3** is in progress: Phase 0 verified, and Phases A0/A1/A2/A5
(source adapters, xG columns, Understat integration, cross-source identity
mapping), B2/B3/B5 (model bakeoff — M2 xG blend, M3 Understat blend, M5
ensemble), C1/C2/C5 (evaluation metrics + pre-registered decision rule),
D (K-best alternatives with diversity), and E (the live Streamlit decision
layer) are all done. Not yet built: A3/B4 (Sofascore, quarantined tier —
needs its own explicit ToS review before starting), Phase F (dashboard
health-view wiring for the new models).

**Current champion model: M0 (`rules_v1`)** — see
[docs/DECISION_RULE.md](docs/DECISION_RULE.md) for the pre-registered rule
governing when (if ever) that changes, and why M2 is the strongest
challenger found so far but hasn't been promoted.

103 tests, all passing (`pytest tests/`).

## Architecture

```
COLLECT (fpl/collect)  → TRANSFORM (fpl/transform) → PROJECT (fpl/project)
→ DECIDE (fpl/decide)  → EVALUATE (fpl/evaluate)   → SURFACE (dashboard/)
```

Two surfaces, two different jobs:
- `dashboard/index.html` — a static, point-in-time snapshot. No server.
- `dashboard/app.py` — the **live decision layer** (v3 §E): what-if squad
  exploration (lock/ban players, budget override, force a formation, chip
  scenarios, K-best alternatives) via live MILP re-solves against an
  already-committed projection. It never recomputes the model — GitHub
  Actions runs the projection pipeline weekly and commits the artefacts;
  the live layer only ever reads them and re-solves the (pure, offline,
  deterministic) optimiser. See `dashboard/app.py`'s module docstring for
  the full frozen/live boundary rule.

## Setup

```bash
pip install -r requirements.txt
```

**Windows note**: this repo's default `python` may resolve to a build with
no prebuilt pandas/numpy wheels. If `pip install` tries to compile from
source and fails, use a native Windows Python venv instead — see
`docs/HANDOFF.md` §1 for the exact gotcha and workaround.

## Running the pipeline end to end

```bash
python -m fpl.collect.fpl_client        # -> data/raw/bootstrap_static.json, fixtures.json
python -m fpl.collect.history_loader    # -> data/raw/history/{season}/...
python -m fpl.collect.snapshot          # -> data/snapshots/ (irreversible — run this every week)
python -m fpl.transform.build_players   # -> data/processed/players.parquet
python -m fpl.transform.build_fixtures  # -> data/processed/fixtures.parquet
python -m fpl.decide.optimiser          # runs project+decide, writes gw{n}_recommendations.json
python -m fpl.evaluate.backtest         # -> data/output/model_health.json
```

## Opening the dashboards

```bash
# Live decision layer (v3 §E) — what-if squad exploration
python -m streamlit run dashboard/app.py

# Static snapshot — just open the file directly
dashboard/index.html
```

## Running the tests

```bash
pytest tests/                # 103 tests, network calls excluded by default
pytest tests/ -v -m network   # + 1 real-network test against understat.com
                              # (see fpl/collect/sources/understat.py's
                              # module docstring before running this —
                              # real robots.txt/consent considerations)
```

## Known limitations

The full, current list lives in `docs/HANDOFF.md` and is surfaced
honestly in the dashboard's Model Health view, not hidden — includes v1's
original limitations (§10 of the v1 plan) plus everything found since via
structured code review and real-data testing. Headlines: DEFCON has no
leak-free backtest season; the fixture-multiplier edge is still inert
pre-season; M3 (Understat) and M5 (ensemble) are built but don't currently
beat M2 in backtest; Sofascore (M4) isn't built at all.
