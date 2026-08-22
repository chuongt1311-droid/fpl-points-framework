# Phase H (H3a + H3b) — the transfer decision layer

**Status:** design approved 2026-08-22, not yet implemented.
**Implements:** `docs/FPL_V4_PLAN.md` §5, increments H3a (real squad
ingestion) and H3b (single-deadline transfer solve, hits endogenous).
H3c (multi-period) and H3d (chips) are explicitly out of scope — see §10.
**Decisions taken with the user before writing** (v4 plan Appendix B #3
and #4, plus two this spec added): §2.

---

## 1. Problem

`CLAUDE.md` opens by saying this tool "makes real transfer/captaincy
decisions for the user's actual team." It does not. It builds an optimal
15 from a hypothetical £100m blank slate, and the weekly diff between
that and the real squad — which is the actual optimisation problem — is
done by hand.

The consequence is not just inconvenience. **The regret measurement is
grading the wrong counterfactual.** `fpl/evaluate/hindsight.py` compares
the chosen XI against the best XI from the owned 15 and the best £100m
XI. Neither is the decision actually faced, which is *the best reachable
squad given this week's transfer budget*. Until transfers exist there is
no term for "should have taken the hit", which is where a large share of
real FPL points are won and lost.

`fpl/decide/transfers.py` has been an empty slot in the plan since v1.

## 2. Decisions taken before designing

| # | Decision | Chosen |
|---|---|---|
| 1 | Does a real team exist? | **Yes** — entry `6669718`, "Chuong's Team", verified live against the public API |
| 2 | Auth approach (Appendix B #3) | **Paste-a-file.** No FPL credentials in the repo, in Actions secrets, or handled by the assistant — a hard boundary, not a preference |
| 3 | Transfer horizon | **5-GW decay-weighted, hit charged once** (see §3.2 for why the plan's literal single-GW objective is wrong) |
| 4 | Scope | **H3a + H3b.** H3c/H3d deferred |

### 2.1 What the live API actually provides — verified, not assumed

Probed on 2026-08-22 against entry `6669718`:

**Public, unauthenticated** — `entry/{id}/` and `entry/{id}/event/{gw}/picks/`:
- `picks[]`: `element`, `position`, `multiplier`, `is_captain`,
  `is_vice_captain`, `element_type` — **squad composition, 15 rows**
- `entry_history`: `bank`, `value`, `event_transfers`,
  `event_transfers_cost`, `points`, `points_on_bench`
- `active_chip`

**Not public, requires the pasted `my-team` file:**
- `selling_price` and `purchase_price` per pick
- `transfers.limit` — the free-transfer count

This corrects the design assumption going in: **bank is public** (from
`entry_history.bank`, which only changes on transfers, so the latest
gameweek's value is the current one), so the pasted file is needed for
*sell prices and free transfers only*.

### 2.2 Baseline fact, verified

The real GW1 team is **byte-identical** to the model's recommendation:
same 15, same XI, same captain (Haaland) and vice (Cunha), 0 transfers,
no chip. So `played == recommended` for GW1, and
`data/state/squad_gw1.json`'s `played: null` should be populated with
that now-known fact as part of this work (§7.4).

## 3. The model

### 3.1 Formulation

Extends the existing squad MILP with transfer decision variables rather
than forking it:

```
squad[p]  = current[p] − transfer_out[p] + transfer_in[p]
transfer_out[p] ≤ current[p]                    # can only sell what you own
transfer_in[p]  ≤ 1 − current[p]                # can only buy what you don't
Σ price[p]·in[p] ≤ bank + Σ sell_price[p]·out[p]
n_transfers = Σ in[p]
penalized  ≥ n_transfers − free_transfers,  penalized ≥ 0
```

Objective (stage 1, on **weighted** xPts):

```
maximise  Σ start[p]·wxpts[p] + Σ captain[p]·wxpts[p]
        + bench_weight·Σ (squad[p] − start[p])·bench_value[p]
        − hit_cost·penalized
```

`n_transfers = 0` is inside the feasible set, so **"roll it" is a
solution the model can choose**, not a special case bolted on afterwards.

Stage 2 re-picks XI and captain on **next-gameweek** xPts via the
existing `pick_xi_and_captain`, exactly mirroring `optimise_squad`'s own
two-stage split. Direct reuse, no fork.

**Alternatives considered and rejected:** *enumerate-and-evaluate* (one
transfer is ~9,000 candidate swaps, two is ~19M — intractable, and the
plan requires hits to be endogenous anyway); *penalty folded into xPts*
(cannot represent the free-transfer/hit structure at all).

### 3.2 Why the horizon deviates from the plan's literal text

The v4 plan §H3b writes the objective as
`Σ xPts(XI) + captain − 4·penalized_transfers` — a **single gameweek**.
Taken literally that is systematically wrong: a one-week gain would have
to exceed 4 points to justify a hit, which almost never happens, so the
solver would recommend rolling nearly always and the `-4` machinery would
be decorative.

Real transfers pay back over weeks. Squad selection in this repo already
uses the 5-GW decay-weighted value (`weighted_xpts`), so the transfer
decision uses the same, with the hit charged **once** (it is a one-off
cost, not a per-week one). The plan's own sanity gate — "it should almost
never recommend a −4 for a sub-2-point gain" — is preserved and in fact
becomes meaningful rather than vacuous.

**Both numbers are always reported** (§6): the weighted gain the decision
was made on, and the next-GW gain, which is the honest short-term
consequence and may be negative for a correct long-game transfer.

### 3.3 The owned-but-filtered-out trap

`apply_availability_filters` removes injured/suspended players from the
candidate pool. But **you can still sell an injured player you own** — and
if that player is absent from the model's id space, the linking constraint
`squad[p] = current[p] − out[p] + in[p]` is unsatisfiable for them and the
solve either fails outright or silently misprices the squad.

Resolution: the id space is `pool_ids ∪ current_squad_ids`. Owned players
missing from the filtered pool are admitted with a **no-buy** flag
(`transfer_in[p] = 0` forced) and their real xPts, which after
`minutes_factor` is at or near zero. Sell-or-bench then falls out of the
objective naturally with no special-casing.

This is the same bug class as the known `optimiser.py` finding in
`docs/HANDOFF.md` §9 (locking a filtered-out player is silently ignored),
and it is the reason that finding is worth fixing rather than working
around — see §10.

### 3.4 Free transfer semantics

For a single deadline, free transfers are an **input**, read from the
pasted file's `transfers.limit` — not computed. Carry-over between
gameweeks (and the current season's cap, which changed) is H3c's problem,
deliberately not modelled here.

Of `config.yaml`'s existing `transfers:` block, this increment uses
**`hit_cost: 4` only**. `free_per_gw: 1` and `max_bank: 5` describe
carry-over and are left untouched and unread, so nothing silently depends
on values H3c will need to revisit. `buffer: 1.5` — a placeholder
heuristic standing in for the decision variable this spec introduces — is
now superseded and is **not** read by `transfers.py`; removing it is left
to H3c so this change does not alter any existing caller's behaviour.

## 4. Refactor required first: extract the constraint builders

The plan says "reuse `optimise_squad`'s constraint builders — don't fork
them." **Those builders do not exist.** `optimise_squad` declares its
constraints inline (`optimiser.py` ~lines 242-276): squad size, per-
position counts, budget, max-per-club, XI size, formation bounds, one
captain.

So they are extracted into shared, tested helpers that both
`optimise_squad` and `transfers.py` call:

- `add_squad_composition_constraints(prob, squad_vars, pool, rules)`
- `add_club_limit_constraints(prob, squad_vars, pool, rules)`
- `add_xi_constraints(prob, squad_vars, start_vars, pool, rules)`
- `add_captain_constraints(prob, start_vars, captain_vars)`

**This touches the champion path**, so per this repo's own convention
(`CLAUDE.md`, "verify by running against real data") the refactor ships
with a hard gate: `fpl.decide.optimiser` re-run against real data must
produce a **byte-identical** GW1 squad, XI, captain and both point totals
before any transfer code lands. A pure refactor that changes a number is
a bug, and this project has caught that class four times already.

## 5. Ingestion (H3a)

`fpl/decide/squad_state.py` gains real-team readers; `config.yaml` gains
`fpl.entry_id: 6669718` (public, not a secret).

### 5.1 Sources

| Field | Source | Notes |
|---|---|---|
| squad, XI, captain, vice | public `picks` | authoritative for composition |
| bank | **pasted file preferred**, public `entry_history.bank` as fallback | see below |
| squad value | public `entry_history.value` | informational |
| active chip | public `active_chip` | |
| **sell price per pick** | **pasted `my-team` file** | read `selling_price` directly |
| **free transfers** | **pasted `my-team` file** | read `transfers.limit` directly |

**Why bank prefers the pasted file despite being public.** The public
`entry_history.bank` is the bank *as of that gameweek's deadline*. If a
transfer has already been made in the current window, the public value is
stale until the next gameweek rolls over, while the pasted file's
`transfers.bank` is live. Since bank drives the budget constraint, the
live value wins where available. When both are present they are
cross-checked; a mismatch is reported (it legitimately means "you have
already transferred this week"), not treated as an error — unlike the
squad mismatch in §5.3, which genuinely indicates a bad file.

**Sell price is read, never recomputed.** FPL returns half the rise
rounded down to 0.1, and reimplementing that rule would add error surface
to precisely the field the plan flags as this phase's identity-mapping-
class risk. The endpoint already gives the answer.

### 5.2 The pasted file

Saved by the user to `data/private/my_team.json` (**gitignored**), from
the authenticated `my-team/{entry_id}/` endpoint in their own logged-in
browser. No credential ever reaches the repo, Actions, or the assistant.

### 5.3 Two staleness guards, because a stale paste is the realistic failure

1. **Age check** — file mtime older than a configurable threshold
   (default 24h) warns; older than the next deadline fails.
2. **Cross-check against the public endpoint** — the 15 element ids in
   the pasted file must equal the 15 from the public `picks` endpoint. Two
   independent sources of the same fact disagreeing means the file is
   stale or belongs to a different entry. **Disagreement stops the solve;
   it does not proceed with a warning.**

A stale file is worse than a missing one: it produces a confident,
wrong, unactionable recommendation, which is the failure mode this whole
spec is most concerned with.

### 5.4 Hard feasibility assertion

Independently of the MILP's budget constraint, a **post-solve assertion**:
`Σ price(in) ≤ bank + Σ sell_price(out)`, with a small epsilon for float
noise. The constraint being correct in theory is not the same as the
input numbers being correct, and the plan's own risk register rates this
High/High. Violation raises; it does not warn.

## 6. Output

`data/output/gw{n}_transfers.json`:

```json
{
  "gameweek": 2,
  "entry_id": 6669718,
  "recommendation": "transfer" | "roll",
  "free_transfers": 1,
  "bank": 0.0,
  "n_transfers": 1,
  "hits": 0,
  "hit_cost": 0,
  "transfers": [
    {"out": {"id": 497, "web_name": "...", "sell_price": 6.0},
     "in":  {"id": 302, "web_name": "...", "price": 6.5}}
  ],
  "weighted_gain": 3.21,
  "next_gw_gain": -0.14,
  "baseline": {"weighted": 223.80, "next_gw": 64.56},
  "after":    {"weighted": 227.01, "next_gw": 64.42},
  "sell_price_source": "my_team_file",
  "my_team_file_age_hours": 2.1
}
```

`weighted_gain` is the decision basis; `next_gw_gain` is stated even when
negative, because a correct long-game transfer that loses points this
week is exactly the case a human needs to see rather than have hidden.

Phase G's archive gains this as a captured artefact (one entry in
`archive.discover_artefacts`), so transfer recommendations become part of
the bitemporal record like everything else.

## 7. Module layout

```
fpl/decide/
  constraints.py   NEW — extracted shared MILP constraint builders (§4)
  transfers.py     NEW — the transfer solve (§3)
  squad_state.py   EXTENDED — real-team readers + pasted-file reader (§5)
  optimiser.py     MODIFIED — calls constraints.py instead of inline
scripts/
  fetch_my_team_instructions.py  NEW — prints exactly how to save the file
```

### 7.1 Boundary discipline

`transfers.py` is a **pure function** of (current squad, sell prices,
bank, free transfers, xPts vector, constraints). It performs no network
I/O — ingestion is `squad_state.py`'s job — which keeps the DECIDE layer
re-solvable offline and freely callable from the live dashboards, exactly
as `optimise_squad` already is (v3 §E1).

### 7.2 Where it does and does not write

Writes `data/output/gw{n}_transfers.json` when run as a pipeline step.
Never writes `data/state/` — the same rule the live dashboards follow.

### 7.3 Not automated in `weekly.yml` yet

Deliberately. The recommendation must be hand-verified as executable in
the real game for two gameweeks (§8) before it runs unattended. Adding
the pipeline step is a follow-up, gated on that.

### 7.4 Backfill `played` for GW1

Now a known fact (§2.2): `played == recommended`. Recording it makes
hindsight's "am I good" vs "is the model good" split real from GW1 rather
than starting at GW2.

## 8. Verification

Unit tests follow repo convention (`tmp_path` + `monkeypatch`, no
network, synthetic fixtures):

| Area | Cases |
|---|---|
| Constraint extraction | `optimise_squad` output byte-identical pre/post refactor, against real data (§4) |
| Transfer linking | selling an owned player is representable; buying an owned player is infeasible; `n_transfers=0` is feasible |
| **Owned-but-filtered** | an injured owned player can be sold and can be benched, and can never be bought (§3.3) |
| Budget | a transfer costing more than bank+proceeds is infeasible; the post-solve assertion fires on a hand-crafted violation |
| Hits | with FT=1, two transfers charge exactly one hit; with FT=2 they charge none |
| Roll | when no transfer clears the hit cost, the recommendation is an explicit roll with the gain stated |
| Ingestion | public picks parsed correctly; stale file rejected; squad mismatch between pasted file and public picks **stops** the solve |

**Beyond unit tests** (this repo's stated norm): run against the real
GW2 data and check by hand that the named transfer is actually executable
in the live game — the right player, an affordable price, a legal squad
afterwards. The plan's gate is **two gameweeks** of this before the output
is trusted, and that gate is calendar-bound, not effort-bound.

## 9. Risks

| Risk | Mitigation |
|---|---|
| **Sell price wrong → infeasible recommendation** | Read `selling_price`, never recompute (§5.1); post-solve assertion (§5.4); two GWs of hand-verification (§8) |
| **Stale pasted file** | Age check + cross-check against public picks; disagreement stops the solve (§5.3) |
| Owned injured player unrepresentable | Explicit id-space union + no-buy flag, with its own test (§3.3) |
| Constraint refactor changes champion output | Byte-identical gate before anything else lands (§4) |
| Horizon choice makes it hit-happy | Both gains always reported (§6); the plan's sub-2-point sanity check applies |
| Credentials leak | Never handled. Paste-a-file only, gitignored, and `scripts/check_secrets.py` already guards commits |

## 10. Out of scope, deliberately

- **H3c multi-period** (FT carry, bank valuation, terminal state, HiGHS).
  Its own gate is "reproduces H3b exactly at w=1 with decay disabled",
  which requires H3b finished and trusted first.
- **H3d chips.** Four decisions a season, usually obvious.
- **Fixing `optimiser.py`'s silent lock-of-filtered-player bug**
  (`HANDOFF.md` §9). Closely related to §3.3 and worth doing, but it is a
  separate defect in a different function; folding it in would blur what
  this change is responsible for.
- **Effective ownership / rank strategy.** v3 §12's standing exclusion,
  unchanged.
- **Automating the pipeline step** (§7.3), until hand-verification passes.
