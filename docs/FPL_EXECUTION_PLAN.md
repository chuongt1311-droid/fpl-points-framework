# FPL Points-Maximization Framework — Execution Plan v1.0

**Owner:** Charlie Trinh (CT13)
**Status:** Locked before code. v1 scope frozen; v2 items explicitly deferred.
**Constraint:** GW1 deadline in ~48 hours. Phases 0–3 must ship before deadline. Phases 4–6 ship during GW1–GW3.

---

## 0. Principles (carried over from VAEP / xT / penalty work)

1. **Framework before code.** This document is the gate. No implementation until it's approved.
2. **Rules-based v1, ML v2.** v1 must be fully explainable — when a projection is wrong, you can trace exactly which term caused it. ML only after v1 has a measured baseline to beat.
3. **Separate concerns so bugs are diagnosable.** Data layer, projection layer, and decision layer never share state. Each is independently testable.
4. **Document limitations, don't paper over them.** The "new signings" problem and the "no xG" problem are named in the dashboard UI itself, not buried.
5. **Validate incrementally.** Every phase has an explicit exit gate. Don't proceed on vibes.
6. **Kill findings that don't hold.** If the profile research shows the hypothesis is wrong, the hypothesis dies — the model doesn't get bent to preserve it.

---

## 1. Scope

### In scope for v1
- Weekly projected points for every FPL player, for the next 1 and next 5 gameweeks
- Fixture difficulty adjustment (FDR-weighted)
- Minutes/availability weighting
- DEFCON-threshold modelling for defenders and defensive midfielders
- Double Gameweek and Blank Gameweek detection
- Chip timing recommendations (Wildcard / Bench Boost / Free Hit / Triple Captain / Assistant Manager)
- Squad optimiser (best XI + captain within £100.0m and squad rules)
- Transfer recommendations (in/out with net projected gain, accounting for the −4 hit)
- Streamlit dashboard as the review surface
- GitHub Actions weekly automation writing projections to the repo

### Explicitly NOT in v1 (deferred to v2)
- Event-stream data (StatsBomb / Understat / Wyscout)
- xG / xA regression flags ("is this player lucky or good?")
- Pressure-adjusted defensive metrics
- ML model (XGBoost) replacing the rules-based projection
- Price-change prediction
- Ownership / effective-ownership and rank-differential strategy
- Set-piece taker detection beyond what FPL's own data exposes
- Player role change detection (heat maps)

**Rationale for excluding event-stream data in v1:** you are forecasting a match that hasn't happened. Event data describes the past in high resolution; it cannot describe next Saturday. Its real value is *correcting* the baseline (flagging a striker on 12 goals from 6.1 xG as due a regression), which is a refinement of an input, not a new input. Build the thing that works with FPL's own aggregates first, measure its error, and only then decide whether the xG layer is worth the ID-mapping and infra cost. Adding it now would be building a v2 refinement before v1 has a baseline to refine.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  COLLECT                                                 │
│  fpl_client.py    → bootstrap-static, fixtures,          │
│                     element-summary, event/{gw}/live     │
│  history_loader.py→ vaastav archive (past seasons CSV)   │
└────────────────────────┬────────────────────────────────┘
                         ↓  raw JSON → data/raw/
┌─────────────────────────────────────────────────────────┐
│  TRANSFORM                                               │
│  build_players.py → one row per player, normalised       │
│  build_fixtures.py→ fixture table + DGW/BGW flags        │
└────────────────────────┬────────────────────────────────┘
                         ↓  data/processed/*.parquet
┌─────────────────────────────────────────────────────────┐
│  PROJECT  (the model — pure functions, no I/O)           │
│  baseline.py      → per-90 scoring rate by channel       │
│  fixtures.py      → FDR multiplier per player per GW     │
│  minutes.py       → availability / start probability     │
│  defcon.py        → P(hit threshold) for DEF and MID     │
│  project.py       → assembles xPts for GW+1 … GW+5       │
└────────────────────────┬────────────────────────────────┘
                         ↓  data/projections/gw{n}.parquet
┌─────────────────────────────────────────────────────────┐
│  DECIDE                                                  │
│  optimiser.py     → best XI + captain (budget + rules)   │
│  transfers.py     → in/out suggestions, hit-adjusted     │
│  chips.py         → chip timing recommendation           │
└────────────────────────┬────────────────────────────────┘
                         ↓  data/output/gw{n}_recommendations.json
┌─────────────────────────────────────────────────────────┐
│  SURFACE                                                 │
│  app.py (Streamlit) → reads saved output, never          │
│                       recomputes. Read-only dashboard.   │
└─────────────────────────────────────────────────────────┘
```

**Key architectural rule:** the Streamlit app never runs the model. GitHub Actions runs the model and writes artefacts; Streamlit reads artefacts. This means the dashboard loads instantly, the model is reproducible, and a dashboard bug can never corrupt a projection.

---

## 3. Data layer

### 3.1 Live source — official FPL API

No auth required. Base: `https://fantasy.premierleague.com/api/`

| Endpoint | Gives you | Pull frequency |
|---|---|---|
| `bootstrap-static/` | All players (IDs, prices, positions, season totals, form, status/injury flags), all teams (strength ratings), all gameweek deadlines | Weekly |
| `fixtures/` | Every fixture, `event` (GW number), `team_h_difficulty`, `team_a_difficulty`, kickoff times | Weekly |
| `fixtures/?event={gw}` | Single-GW fixtures | On demand |
| `element-summary/{id}/` | Per-player match-by-match history + upcoming fixtures with FDR | Weekly, only for shortlist (~150 players) — not all ~700 |
| `event/{gw}/live/` | Live per-player stats for a GW | Post-GW, to record actuals |

**Rate limiting:** one request per second, `User-Agent` header set. `element-summary` for 150 players = 150 requests = ~2.5 min. Acceptable inside a 20-min Actions job. Never loop all ~700 players — pre-filter to players above a minutes threshold first.

**Critical field notes:**
- `status` — `a` available, `d` doubtful, `i` injured, `s` suspended, `u` unavailable. This is the availability gate.
- `chance_of_playing_next_round` — integer 0–100 or null. Null means no news (treat as 100 if `status == 'a'`).
- `element_type` — 1 GK, 2 DEF, 3 MID, 4 FWD.
- `now_cost` is in tenths (105 = £10.5m).
- FDR from `fixtures` is FPL's own 1–5. We use it as an input but do **not** trust it blindly — see 4.2.

### 3.2 Historical source — vaastav/Fantasy-Premier-League

GitHub archive mirroring the FPL API into per-season CSVs, including `gws/merged_gw.csv` (every player, every gameweek). Used for:
- Establishing per-90 baselines for returning players before GW1
- Backtesting the projection model against known outcomes
- The profile research in Phase 2

Pull the last 3 seasons. Note the 2025/26 season is the only one with DEFCON scoring, so DEFCON baselines come from that season only.

### 3.3 The new-signings problem — v1 handling

**The problem:** a player arriving from another league has no FPL history, so no baseline. At GW1 this is a large fraction of interesting players.

**v1 rule (locked):**
- Players with `< 3` FPL appearances in the current season AND no prior-season FPL history are tagged `INSUFFICIENT_DATA`.
- They receive a projection derived from **team-level and position-level priors only**: their club's attacking/defensive strength from `bootstrap-static`, their position, and their price tier (price is FPL's own implicit prior on expected output — a £8.5m new midfielder is FPL signalling expected returns).
- Their projection carries a `confidence: low` flag.
- The dashboard renders them in a visually distinct state and the optimiser **will not** recommend transferring *in* a `low` confidence player unless explicitly unlocked by a toggle.
- After 3 appearances they graduate to normal baseline logic automatically.

**Why not do better:** mapping La Liga/Serie A output to Premier League FPL points requires a league-strength adjustment and a role-translation model. Both are guessable, neither is validated, and a confidently wrong number is worse than an honestly uncertain one. This is a documented limitation, not a gap to be quietly filled. v2 can add a comparable-archetype layer using Understat once there's a validated baseline to compare against.

---

## 4. The projection model (v1, rules-based)

### 4.1 Structure

For each player *p* and gameweek *g*:

```
xPts(p, g) = Σ over fixtures f in g of:

    appearance_pts(p)
  + goal_pts(p)      × fixture_attack_mult(p, f)
  + assist_pts(p)    × fixture_attack_mult(p, f)
  + cleansheet_pts(p)× fixture_defence_mult(p, f)
  + defcon_pts(p)    × fixture_defcon_mult(p, f)
  + save_pts(p)      × fixture_defence_mult(p, f)   [GK only]
  + bonus_pts(p)
  − card_pts(p)
  − conceded_pts(p)  × fixture_defence_mult(p, f)

  all multiplied by:  minutes_factor(p)
```

Note the summation over fixtures: a Double Gameweek is not a special case bolted on — it falls out naturally because the player simply has two fixtures in that gameweek. Same for a Blank Gameweek, which sums to zero. This is deliberate; special-casing DGWs is where bugs live.

### 4.2 Component definitions

**`appearance_pts`** — `2 × P(60+ mins) + 1 × P(1–59 mins)`.

**`goal_pts`, `assist_pts`** — per-90 rate from historical data × position multiplier (GK 10 / DEF 6 / MID 5 / FWD 4 for goals; 3 flat for assists) × expected minutes / 90.

**`cleansheet_pts`** — `P(clean sheet) × position value (GK/DEF 4, MID 1, FWD 0) × P(60+ mins)`. Base `P(clean sheet)` derived from the team's goals-conceded rate, then fixture-adjusted.

**`defcon_pts`** — `2 × P(hits threshold)`. Threshold is 10 CBIT for DEF, 12 CBIRT for MID/FWD. This is a **binary threshold, capped at 2** — so the correct estimator is the historical *rate at which the player crosses the line*, not their average action count. A player averaging 11 CBIT is not "1.1× a 10-threshold player"; what matters is what share of their matches cleared 10. Compute from per-match history: `matches_over_threshold / matches_played`. This is the single most commonly mis-modelled term in public FPL models and it is where an edge exists.

**`bonus_pts`** — per-90 historical bonus rate. v1 does not simulate BPS directly (that needs the full 32-stat Opta feed). Documented limitation. v2 candidate.

**`conceded_pts`** — `−0.5 × expected goals conceded` for GK/DEF (−1 per 2 conceded).

**`minutes_factor`** — the availability gate:
```
if status in ('i','s','u'):            0.0
elif chance_of_playing is not None:     chance/100
elif status == 'd':                     0.5
else:                                   rolling start rate over last 6 apps
```

**Fixture multipliers** — three separate multipliers, because fixture difficulty is not one thing:
- `fixture_attack_mult` — how easy is it to score against this opponent. Derived from opponent's defensive strength.
- `fixture_defence_mult` — how likely is a clean sheet. Derived from opponent's attacking strength.
- `fixture_defcon_mult` — **inverted**. A harder opponent means *more* defensive work, so more CBIT opportunities. A defender facing a top side is worse for clean sheets and *better* for DEFCON.

That third multiplier is the non-obvious one and it's why a single "FDR" number is insufficient. Two of the three multipliers move in opposite directions. FPL's own 1–5 FDR is used as a sanity check and a fallback, not as the primary signal — the primary signal is team strength ratings from `bootstrap-static` (`strength_attack_home`, `strength_defence_away`, etc.), which are finer-grained.

**Home/away split:** applied to all three multipliers separately. Home advantage is real and FPL's strength ratings already encode it.

### 4.3 Multi-gameweek horizon

Project GW+1 through GW+5. The 5-GW horizon is what drives transfer decisions — you never transfer for one week. Decay weight later gameweeks (uncertainty compounds): weights `[1.0, 0.85, 0.7, 0.55, 0.4]`.

---

## 5. Chip strategy layer

Chips are **timing multipliers on a finite budget** — five chips, 38 gameweeks. The model's job is to say "this is a top-decile week for chip X" rather than "use it now."

| Chip | Trigger condition | What the model computes |
|---|---|---|
| **Triple Captain** | Best in a DGW with a high-xPts captain in two favourable fixtures | `max(xPts) × 2` extra (3× vs 1×). Recommend when this exceeds the season's projected 90th percentile. |
| **Bench Boost** | DGW where all 15 players have fixtures | Sum of bench xPts. Recommend when bench sum > threshold (~15 pts). Requires deliberate squad prep 1–2 GWs prior — the model flags "prep now" ahead of the target week. |
| **Free Hit** | A BGW where you'd field fewer than 11, or a GW where your squad's fixtures are uniformly awful | `xPts(optimal unconstrained XI) − xPts(your actual XI)`. Recommend when the delta exceeds ~20 pts. |
| **Wildcard** | Squad has drifted far from optimal; two per season (autumn window, spring window) | `xPts(optimal squad over next 5 GW) − xPts(current squad over next 5 GW)`, minus the hit cost of achieving it via normal transfers. Recommend when normal transfers can't close the gap within 3 GWs. |
| **Assistant Manager** | Niche. A manager whose side has a favourable run. | Ranked by that club's aggregate fixture ease over the chip's active window. |

**DGW / BGW detection:** derived directly from the `fixtures` endpoint by counting fixtures per team per `event`. A team with 2 fixtures in one event = DGW; 0 fixtures = BGW. Rearranged fixtures appear in the API with `event: null` until scheduled, so the detector must re-run weekly — a DGW can materialise with only 2–3 weeks' notice. The dashboard shows a forward-looking DGW/BGW radar for this reason.

**v1 chip constraint:** the model recommends *timing*, it does not auto-play chips. You decide. Chip state (which chips remain) is a config value you update manually.

---

## 6. Decision layer

### 6.1 Squad optimiser

Constraints (FPL rules):
- 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
- £100.0m budget
- Max 3 players per club
- Starting XI: 1 GK, min 3 DEF, min 2 MID, min 1 FWD, 11 total
- Captain gets 2× (vice-captain fallback if captain doesn't play)

**Method:** linear programming (PuLP) maximising total 5-GW weighted xPts subject to the above. This is a clean MILP, solves in seconds, and is exactly the right tool — no heuristics needed.

For GW1 with no existing team, this produces the initial squad directly.

### 6.2 Transfer recommendations

From GW2 onward, given a current squad:
- 1 free transfer per GW, bankable up to 5
- Each additional transfer costs −4 points
- Recommend a transfer only when `Δ(5-GW weighted xPts) > 4 + buffer` for a hit, or `> 0 + buffer` for a free transfer

The buffer exists because model error is real. Set it to ~1.5 points initially; tune after backtesting tells you the model's actual RMSE.

### 6.3 Captain selection

`argmax(xPts)` for the coming GW among the starting XI, with a variance consideration surfaced but not auto-applied: the highest-xPts player is not always the right captain if his projection is driven by a single volatile channel. The dashboard shows the top 5 captain candidates with their xPts *and* the channel breakdown, so you can see whether a projection rests on one term or several.

---

## 7. The dashboard

### 7.1 Design direction

**Subject:** a weekly decision surface for one analyst making transfer and captaincy calls under a deadline. Not a public-facing product. Its single job: *let you see, in under 60 seconds, what changed and what to do about it.*

The visual world to draw from is the matchday teamsheet and the floodlit pitch at dusk — not generic dashboard chrome, and deliberately not the standard FPL green/purple.

**Tokens:**

```
--dusk:        #101826   /* page ground — deep blue, floodlit-evening */
--slate:       #1B2738   /* card surface */
--slate-lift:  #24334A   /* hover / raised */
--chalk:       #EDF1F5   /* primary text — pitch marking white */
--chalk-dim:   #8A9AAE   /* secondary text, labels */
--floodlight:  #F5B841   /* the single accent — captain, chip alerts, focus */
--grass:       #4FA96B   /* positive delta, easy fixture */
--flag:        #D9503F   /* negative delta, hard fixture */
```

Amber-on-deep-blue rather than the acid-green-on-black or cream-and-serif looks that every generated dashboard converges on. The accent is used *once per view* — captain marker and chip alerts only. Everything else is chalk and slate.

**Type:**
- Display / headers: **Archivo Narrow** — condensed, scoreboard lineage, holds up at large sizes without feeling decorative
- Body / UI: **Inter** — neutral, reads well at 14px
- All numerals: **JetBrains Mono**, tabular figures. Non-negotiable — this is a table-heavy interface and misaligned digits make columns unreadable

**Signature element — the Fixture Ticker.** Every player row carries a six-cell horizontal strip showing their next six gameweeks, each cell colour-mapped by fixture difficulty, opponent code inside, `(H)`/`(A)` marked, DGW cells split diagonally, BGW cells struck through. It is the one element that makes the model's central insight — *context multiplies talent* — visible at a glance, and it is the thing the dashboard should be remembered by. Everything else stays quiet so this reads.

Because the DEFCON multiplier inverts, defender rows show the ticker in a dual state: clean-sheet difficulty on the upper half of each cell, DEFCON opportunity on the lower. A red-over-green cell means "won't keep a clean sheet, will rack up clearances" — which is exactly the nuance a single FDR number destroys.

**Quality floor:** responsive to mobile width, visible keyboard focus rings, `prefers-reduced-motion` respected, no animation beyond a 120ms hover transition.

*Streamlit note:* Streamlit's default theme is overridden via `.streamlit/config.toml` for the base palette, plus a single injected `<style>` block for the ticker component and tabular numerals. The ticker itself is a small custom HTML component — it's the one place worth the extra effort.

### 7.2 Views

**1 — This Week (landing)**
The 60-second view. Deadline countdown. Recommended XI as a pitch graphic with the captain marked in floodlight amber. Projected total. Below it: the recommended transfer (in/out, net gain after any hit) stated as one sentence, and the chip verdict as one line — either "no chip this week" or a specific recommendation with its computed gain.

**2 — Player Explorer**
The full projections table. Sortable, filterable by position, price, club, availability, ownership. Every row carries the Fixture Ticker. Clicking a row expands the **channel breakdown** — how much of this player's xPts comes from goals vs assists vs clean sheets vs DEFCON vs bonus. This is the view that answers "why is the model recommending this guy," and it's what makes the whole thing a learning tool rather than a black box.

`INSUFFICIENT_DATA` players render with a dashed border and a `low confidence` label, filterable on/off.

**3 — Fixture Radar**
Forward-looking grid: all 20 clubs × next 8 gameweeks, colour-mapped. DGWs and BGWs flagged prominently. This is where you spot green runs 4 weeks out and plan wildcards. Toggle between attack-view, defence-view, and DEFCON-view — the three multipliers, shown separately, because they disagree.

**4 — Chip Planner**
Each of the five chips with its current computed best-window over the remaining season, the projected gain, and the reasoning. Chip state (used/unused) editable here.

**5 — Model Health**
The honesty view, and the one that makes this a portfolio piece rather than a toy. Last week's projections vs actuals. Running RMSE and mean absolute error by position. Calibration plot. A standing list of known limitations rendered in the UI itself — no BPS simulation, no xG regression layer, new-signing cold start. If the model is performing badly, this view says so plainly.

---

## 8. Automation

`.github/workflows/weekly.yml`

**Schedule:** two runs per week.
- **Tuesday 09:00 UTC** — main run. Previous GW is final (lockdown is 09:00 UK the day after the last match), so actuals are locked and baselines can update cleanly.
- **Friday 09:00 UTC** — refresh run. Catches late injury news, price changes, and newly-scheduled rearranged fixtures ahead of a typical Saturday deadline.
- `workflow_dispatch` for manual triggering.

**Job steps:**
1. Checkout, setup Python 3.11, install deps
2. `python -m fpl.collect` — pull API, write `data/raw/`
3. `python -m fpl.transform` — normalise, write `data/processed/`
4. `python -m fpl.project` — run model, write `data/projections/gw{n}.parquet`
5. `python -m fpl.decide` — optimiser + transfers + chips, write `data/output/gw{n}_recommendations.json`
6. `python -m fpl.evaluate` — score last GW's projections against actuals, append to `data/model_health.json`
7. Commit and push artefacts (`permissions: contents: write`)

Timeout 20 min. Rate-limited to 1 req/sec.

**Failure handling:** the job must not silently produce stale recommendations. If any step fails, the workflow fails loudly and the previous week's artefacts remain — the dashboard reads the artefact's timestamp and displays a warning banner if it's more than 4 days old. A dashboard confidently showing last week's numbers is the worst failure mode available, so it's designed out explicitly.

**Cost:** £0. Public repo, GitHub Actions free tier, no LLM calls in the pipeline, Streamlit Community Cloud for hosting.

---

## 9. Phased build with gates

| Phase | Deliverable | Exit gate | Timing |
|---|---|---|---|
| **0 — Scaffold** | Repo, structure, `config.yaml`, deps | `pip install -r requirements.txt` clean; repo pushed | Day 1, 1h |
| **1 — Data** | `fpl_client.py`, `history_loader.py`, transforms | One row per player with all needed fields; fixture table with DGW/BGW flags correct for a known past DGW | Day 1, 3h |
| **2 — Research** | Profile analysis notebook on 3 seasons of history | Written finding: which channel combinations actually produce high points-per-90 by position, with the DEFCON threshold-rate analysis. **The hypothesis may be falsified here — that's a valid outcome.** | Day 1, 3h |
| **3 — Projection v1** | `project.py` + optimiser | Backtest on 2025/26: RMSE reported by position. Top-20 projected players pass eye test — if someone obviously wrong ranks high, there's a bug (this is exactly how the Mbappé/xT shot-handling bug surfaced). **GW1 squad generated.** | Day 2, 5h |
| **4 — Dashboard** | Streamlit app, all 5 views | Loads from saved artefacts in <2s; Fixture Ticker renders correctly for a known DGW; mobile-readable | Day 2 → GW1, 4h |
| **5 — Automation** | GitHub Actions workflow | Two consecutive successful scheduled runs; stale-data banner verified by forcing a stale artefact | GW1 → GW2, 2h |
| **6 — Validation** | Model Health view populated | 3 gameweeks of projection-vs-actual data; RMSE stable; decision on whether v2 (ML / xG layer) is justified | GW2 → GW4 |

**Minimum before the GW1 deadline: phases 0–3.** That produces a data-driven starting squad, which is the actual deadline requirement. The dashboard and automation can land during GW1 without losing anything.

---

## 10. Known limitations (v1) — stated up front

1. **No BPS simulation.** Bonus points are projected from historical rate, not modelled from the underlying 32 Opta stats. Systematically under-projects bonus-magnet players.
2. **No xG/xA regression layer.** A player over- or under-performing their underlying numbers is projected on their raw output, so hot streaks are over-projected and cold streaks under-projected.
3. **New-signing cold start.** Players without 3 FPL appearances get a prior-only projection flagged `low confidence`.
4. **No price-change modelling.** Team value growth is not optimised for.
5. **No ownership/differential strategy.** The model maximises raw points, not rank. These diverge — the optimal play for winning a mini-league sometimes differs from the optimal play for points. v1 optimises points; you apply rank judgement yourself.
6. **Rotation risk is backward-looking.** Start probability comes from recent starts, so it lags a manager changing his mind, and it has no notion of European fixture congestion.
7. **Set-piece duties not explicitly modelled** beyond what's already implicit in historical output.
8. **FDR is derived from season-long team strength ratings**, which lag genuine form shifts by several gameweeks early in a season. Early-season projections are the least reliable — including, unavoidably, GW1.

Limitation 8 deserves emphasis given the timing: **the GW1 squad this produces is the weakest output the model will ever generate**, because it has the least data. That's not a reason to skip it — a systematic prior beats a vibes-based squad — but it's a reason to expect an early wildcard and not to over-commit to the initial 15.

---

## 11. Repo structure

```
fpl-framework/
├── config.yaml                 # budget, chip state, thresholds, horizon weights
├── requirements.txt
├── .streamlit/config.toml      # theme tokens
├── fpl/
│   ├── collect/
│   │   ├── fpl_client.py
│   │   └── history_loader.py
│   ├── transform/
│   │   ├── build_players.py
│   │   └── build_fixtures.py
│   ├── project/
│   │   ├── baseline.py
│   │   ├── fixtures.py
│   │   ├── minutes.py
│   │   ├── defcon.py
│   │   └── project.py
│   ├── decide/
│   │   ├── optimiser.py
│   │   ├── transfers.py
│   │   └── chips.py
│   └── evaluate/
│       └── backtest.py
├── dashboard/
│   ├── app.py
│   ├── components/ticker.py    # the Fixture Ticker
│   └── views/
├── notebooks/
│   └── 01_profile_research.ipynb
├── data/                       # artefacts, committed
│   ├── raw/ processed/ projections/ output/
│   └── model_health.json
├── docs/
│   └── FPL_EXECUTION_PLAN.md   # this file
└── .github/workflows/weekly.yml
```

---

## 12. Immediate next step

Phase 0 + Phase 1: scaffold the repo and get `bootstrap-static` and `fixtures` pulling into a clean player table with DGW/BGW detection working. Everything downstream depends on that table being right, and it's verifiable in isolation — which is the point.
