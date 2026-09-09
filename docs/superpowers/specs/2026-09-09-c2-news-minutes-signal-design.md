# C2 — Real-time team-news → minutes signal (model M6)

**Status:** design, pre-implementation. Brainstormed 2026-09-09.
**Owner:** Charlie Trinh.
**Relates to:** FPL_EXECUTION_PLAN.md §10 limitation 6 ("Rotation risk is
backward-looking … it lags a manager changing his mind"); `CLAUDE.md`
model-registry rules; `docs/DECISION_RULE.md`.

---

## 1. Problem

`fpl/project/minutes.py::compute_minutes_factor` gates start probability
on, in order: `status ∈ {i,s,u}` → 0; else `chance_of_playing_next_round`
(the FPL numeric, `/100`); else `status == 'd'` → 0.5; else the historical
`rolling_start_rate`.

`chance_of_playing_next_round` is blunt and often absent:

- It is `null` for the large majority of players — FPL only populates it
  for a flagged injury/suspension doubt.
- When present it is one of `0 / 25 / 50 / 75 / 100` — no finer.
- It carries no return date, so a player "out until GW7" and a player
  "50/50 this week" both read as a single number with no horizon.

FPL's own free-text `news` string per player is richer — e.g.
*"Knock - 75% chance of playing"*, *"Suspended until 12 Sep"*,
*"Expected back for the Arsenal game"*, *"Illness - Late test"*. Nothing
in the pipeline reads it. Parsing it into a structured availability
signal is the smallest change that closes the plan's limitation 6 for
the injury/suspension half (squad rotation of a *healthy* player is
explicitly out of scope here — see §9).

## 2. Decisions already locked (brainstorm 2026-09-09)

| # | Question | Decision |
|---|---|---|
| 1 | AI produces the recommendation, or better inputs? | **Better inputs only.** LLM emits a structured per-player availability signal. The MILP still makes every squad / XI / captain / transfer decision. |
| 2 | Source of the news text? | **FPL's own `news` field first.** No new network surface, no geo-block risk (cf. Sofascore, abandoned). External sources considered only if this proves thin. |
| 3 | How does the signal feed `minutes.py`? | **Replace the `chance_of_playing` branch.** Same branch position, better number. Falls back to today's behaviour when `news` is empty/unparseable. |
| 4 | LLM call mechanics? | **Deterministic cache keyed by the news text.** Parse each distinct string once; store in a committed `data/news/news_parsed.json` keyed by `sha256(normalized)`. Re-runs and local runs make zero API calls. |
| 5 | Model-registry entry, given it can't be backtested? | **Challenger model M6 (`m6_news`), parallel.** M0 stays the untouched live champion. M6 = M0 + news-parsed minutes, archived every run like M2/M3. A **separate** pre-registration doc (not a `DECISION_RULE.md` edit) states what would promote it. |

## 3. Architecture

```
data/raw/bootstrap_static.json
        │  elements[].news  (free text, per player)
        ▼
fpl/project/news.py :: parse_news(players_df, config)
        │  for each DISTINCT non-empty news string:
        │    key = sha256(normalize(text))
        │    hit  → data/news/news_parsed.json[key]
        │    miss → fpl/collect/llm_client.parse_availability(text)  → store
        ▼
   DataFrame[id, news_start_prob, news_status, news_return_gw,
             news_confidence]  +  NewsHealth(misses, errors, api_calls)
        │
        ▼
fpl/project/minutes.py :: compute_minutes_factor(players_df, config, model)
        │  model == "m6_news":  news_start_prob supersedes the
        │                       chance_of_playing branch (when non-null)
        │  model == anything else:  byte-identical to today
        ▼
fpl/project/project.py :: build_player_inputs(config, model="m6_news")
        ▼
   M6 projections  ──►  fpl/history/archive  (model=m6_news partition,
                        alongside m0_rules / m2_xg / m3_understat)
        │
        ▼  (once actuals settle)
fpl/evaluate/news_scorecard.py :: brier(m6, m0) vs actual `starts`
        ▼
   data/output/news_scorecard.json  ──►  dashboard Model-Health view
```

**The live path (`model="m0_rules"`) never touches `news.py`, never
calls the LLM, and produces provably identical output.** Enforced the
same way M2/M3 are: the new code is only reachable through an explicit
`model` argument.

## 4. Components

### 4.1 `fpl/collect/llm_client.py` (new)

Thin wrapper over the Anthropic API. Single public function:

```python
def parse_availability(news_text: str, config: dict) -> dict:
    """Structured availability from one FPL `news` string.

    Returns:
      {
        "start_prob":   float 0..1,      # P(starts the next GW)
        "status":       "available" | "doubt" | "injured" | "suspended" | "unknown",
        "return_gw":    int | None,      # absolute GW the player is expected back, if stated
        "confidence":   float 0..1,      # the model's own confidence in this parse
        "reason":       str,             # one-line human-readable justification
      }

    Raises on network / auth / malformed-response failure AFTER one retry
    — the caller (news.py) catches and degrades.
    """
```

- Model: `config["news"]["model"]` (default `"claude-haiku-4-5-20251001"` — this
  is short-text extraction, the cheapest capable model).
- Structured output via a single tool definition (`record_availability`)
  so the response is schema-validated, not free-text-parsed.
- No system-wide Anthropic dependency beyond this file. `ANTHROPIC_API_KEY`
  from the environment; absent → `parse_availability` raises immediately
  (caught, degrades). **The key is never logged, never written to any
  artefact, never committed** — `scripts/check_secrets.py` already guards
  the tree.
- Prompt is a module constant, versioned: the cache stores which
  `prompt_version` produced each entry, so a prompt change re-parses
  rather than silently mixing schemas (same idea as graphify's
  prompt-hash cache keys).

### 4.2 `data/news/news_parsed.json` (new, committed)

```json
{
  "<sha256 of normalized news text>": {
    "news_text": "Knock - 75% chance of playing",
    "start_prob": 0.75,
    "status": "doubt",
    "return_gw": null,
    "confidence": 0.9,
    "reason": "Explicit 75% chance quoted by FPL.",
    "model": "claude-haiku-4-5-20251001",
    "prompt_version": 1,
    "parsed_ts": "20260909T041500Z"
  }
}
```

- **Append-only in practice**: entries are only added. A news string that
  reappears (same knock, same wording) hits the cache forever.
- Committed because it is **not reproducible** without the LLM — unlike
  everything else under `data/raw/` (which is gitignored precisely
  because it *is* reproducible from the API). New top-level `data/news/`
  dir, mirroring how `data/actuals/` was added in PROJECT_LOG §20.
- Normalization before hashing: strip, collapse whitespace, casefold.
  So *"Knock - 75% chance"* and *"knock -  75% chance "* share one entry.
- Size: a full season has on the order of a few hundred distinct news
  strings. Trivial.

### 4.3 `fpl/project/news.py` (new)

```python
def parse_news(players_df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, NewsHealth]:
    """One row per player with a parsed signal (NaN where no usable news).

    - reads players_df["news"] (kept by build_players — see §4.4)
    - for each DISTINCT non-empty string: cache lookup, else llm_client
    - a per-string LLM failure is swallowed: that string is left unparsed,
      its players fall through to chance_of_playing. NewsHealth records it.
    - NEVER raises. Source-adapter rule 1 (fpl/collect/sources/base.py).
    """
```

`NewsHealth` (mirrors `SourceHealth`): `distinct_strings`, `cache_hits`,
`api_calls`, `parse_failures`, `players_with_signal`, `last_error`.
Written into the M6 projection's metadata and surfaced on the dashboard.

### 4.4 `fpl/transform/build_players.py`

`news` (and `news_added` timestamp if present) added to `ELEMENT_COLUMNS`
under the existing "keep if present" contract — a missing column must
degrade, not `KeyError` (HANDOFF.md §5 finding #6 pattern).

### 4.5 `fpl/project/minutes.py`

`compute_minutes_factor(players_df, config, model="m0_rules")` — new
optional `model` param. Only for `model == "m6_news"`:

```
if status in (i,s,u):                              0.0
elif model == "m6_news" and news_start_prob != NaN: news_start_prob     ← NEW, M6 ONLY
elif chance_of_playing_next_round is not None:      chance/100
elif status == 'd':                                0.5
else:                                              rolling_start_rate
```

`news.py` is imported lazily inside the `m6_news` branch so `m0_rules`
has no new import at all. `apply_gk_backup_override` runs unchanged after.

**Interaction with `status`:** the LLM is told to return
`status="suspended"` / `"injured"` for a player FPL still lists as `a`
(rare, but happens on breaking news between FPL updates). Even so, the
first branch (`status ∈ {i,s,u}`) uses FPL's `status`, not the parse —
the parse only ever lowers a player *within* the "available but doubtful"
space via `start_prob`. It never overrides FPL's hard unavailable flag in
either direction. Documented so a future reader doesn't "fix" it.

### 4.6 `fpl/project/project.py`

- `build_player_inputs(config, model)` threads `model` into
  `compute_minutes_factor(players, config, model)`.
- `m6_news` added to the model branch: it is `m0_rules` rates +
  news-aware minutes — **no** xG/Understat blend. Nested the M-series way
  (M6 = M0 + one new component).
- `project_gameweeks(..., model="m6_news")` already generic — no change
  beyond passing the string through.
- The `ValueError` allow-list in `build_player_inputs` gains `"m6_news"`.

### 4.7 `fpl/history/archive.py` + `weekly.yml`

The archive step already loops a model list (`m0_rules`, `m2_xg`,
`m3_understat` — seen in the GW4 run's `data/history/projections/gw=*/…/model=*`
partitions). Add `m6_news`. M6 projections are archived every run,
provenance-stamped, immutable — the raw material the scorecard reads.

### 4.8 `fpl/evaluate/news_scorecard.py` (new)

```python
def compute_news_scorecard(bootstrap: dict, config=None) -> dict:
    """For every settled GW with actuals: Brier score of M6 vs M0
    minutes_factor predictions against the actual `starts` flag, plus the
    subset restricted to players who HAD a news signal that week (where
    M6 and M0 actually differ). Written to data/output/news_scorecard.json.
    Skips a GW with no archived M6 projection or no actuals — never fails."""
```

Brier: `mean((minutes_factor − actual_start)²)` over players with
`minutes > 0` eligibility that GW. The **news-subset** number is the one
that matters — on the full population M6 ≈ M0 by construction.

### 4.9 `config.yaml`

```yaml
news:
  enabled: true
  model: "claude-haiku-4-5-20251001"
  cache_path: "data/news/news_parsed.json"
  prompt_version: 1
  # LLM is hit only on cache miss; set false to force cache-only
  # (offline / no key) — unknown strings then just fall back to
  # chance_of_playing, same as a parse failure.
```

No change to `config.horizon`, `config.minutes`, or any M0 constant.

### 4.10 `.github/workflows/weekly.yml`

New step **"Parse — team news (model M6 input)"** after
`fpl.collect.fpl_client`, before Transform:

```yaml
- name: Parse — team news (model M6 input)
  run: python -m fpl.project.news
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

- `python -m fpl.project.news` with no args: parse the current bootstrap,
  update the cache, print the `NewsHealth` summary.
- If the secret is unset the step still succeeds (cache-only, misses
  fall back) — it must not fail the job (rule 1).
- New "Evaluate — news scorecard" step after the hindsight step:
  `python -m fpl.evaluate.news_scorecard all`.
- `data/news` added to the commit `git add` list.
- The M6 projection run: the Decide step still runs `m0_rules` only (M6
  is **not** a decision input yet). M6 enters only via the archive step's
  model loop and the scorecard. So `weekly.yml`'s Decide line is
  unchanged; the archive step's model list gains `m6_news`.

## 5. Pre-registration (NOT a `DECISION_RULE.md` edit)

`CLAUDE.md`: *"Do NOT edit `docs/DECISION_RULE.md` except to fix a genuine
drafting error, logged as a diff."* Adding a challenger's promotion
criterion is neither a drafting fix nor covered by the existing GW12
rule. So it lives in a **new** file, `docs/M6_PREREGISTRATION.md`,
written and committed **before** M6 produces a single projection:

> **M6 (`m6_news`) promotes to a decision input over M0 iff, evaluated
> once at GW W+6** (W = the first full gameweek after this spec's
> implementation merges):
>
> 1. On the **news-signal subset** (players with a parsed signal that
>    week, where M6 and M0 differ), M6's `minutes_factor` has a **lower
>    Brier score** vs the actual `starts` flag than M0, summed over
>    GW W … W+5; **and**
> 2. Captaincy + squad hindsight regret (`fpl.evaluate.hindsight`) over
>    the same window is **no worse** under M6 than M0; **and**
> 3. `NewsHealth.parse_failures / distinct_strings < 0.2` across the
>    window (the parser is actually working).
>
> If all three hold, M6's minutes path is folded into M0 as a
> data-quality improvement (like the §17/§20 rolling-start-rate fixes),
> `m6_news` is retired, and the change is logged in `PROJECT_LOG.md`.
> If not, M6 is kept archived-only or dropped. **No informal promotion
> before GW W+6 regardless of how good an interim number looks** —
> condition 5 of the existing decision rule (an unexplained win gets
> investigated, not celebrated) applies.

Whether this graduates into `DECISION_RULE.md` proper is the owner's
call, made after reading it — not done here.

## 6. Testing (TDD, no network)

`fpl/collect/llm_client.py` is injected/monkeypatched everywhere below —
**no test makes a real API call.**

| Test | Asserts |
|---|---|
| `test_llm_client_builds_the_tool_call` | request payload shape (stubbed transport), schema of the parsed dict |
| `test_parse_news_cache_hit_makes_no_api_call` | warm cache → `api_calls == 0`, correct signal returned |
| `test_parse_news_cache_miss_calls_llm_and_persists` | miss → one call, entry written with `prompt_version` + `parsed_ts` |
| `test_parse_news_llm_failure_degrades_to_no_signal` | stub raises → `NaN` signal for that player, `NewsHealth.parse_failures == 1`, **no exception** |
| `test_parse_news_normalizes_before_hashing` | two whitespace/case variants share one cache entry |
| `test_prompt_version_bump_forces_reparse` | entry with old `prompt_version` is re-parsed, not reused |
| `test_minutes_m6_uses_news_prob_over_chance_of_playing` | `model="m6_news"` + signal present → `minutes_factor == news_start_prob` |
| `test_minutes_m0_is_byte_identical_with_news_cache_present` | same `players_df`, `model="m0_rules"` → identical to pre-change output even when `data/news/` is populated |
| `test_minutes_news_never_overrides_hard_unavailable` | FPL `status='i'` + parse says `start_prob=0.9` → `minutes_factor == 0.0` |
| `test_news_scorecard_brier_math` | known predictions/actuals → hand-computed Brier, full vs news-subset |
| `test_news_scorecard_skips_gw_without_m6_projection` | missing archive partition → skipped, no raise |

Plus the regression bar from `CLAUDE.md`: a full `m0_rules` optimiser run
before/after this change must produce an identical squad.

## 7. Rollout

1. Land the code with `news.enabled: true`, M6 archived-only. No
   decision-path change. `docs/M6_PREREGISTRATION.md` committed in the
   same PR.
2. Set the `ANTHROPIC_API_KEY` repo secret.
3. Let the weekly pipeline accumulate M6 projections + scorecard rows for
   6 gameweeks.
4. At GW W+6: evaluate against §5. Fold in or drop. Log in `PROJECT_LOG.md`.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM misparses a news string (e.g. reads "ruled out" as available) | `confidence` field; a low-confidence parse below `config.news.min_confidence` (default 0.5) is discarded → fall back. Scorecard's news-subset Brier catches systematic error within 2–3 GWs. |
| FPL `news` is itself stale/wrong | Same failure mode M0 already has via `chance_of_playing`; not made worse. |
| API key leak | Handled only in `llm_client.py`, never persisted; `check_secrets.py` guards commits. |
| Non-determinism creeps in | Cache is the only path in CI; a cache miss during a run is logged in `NewsHealth`, and the miss's result is committed so the next run is deterministic. Two runs on the same bootstrap with a warm cache are identical. |
| Anthropic API outage on a Friday | Step degrades (cache-only), M6 falls back to `chance_of_playing` for new strings, job still green. M0 (live) entirely unaffected. |
| Scope creep into "squad rotation" | Explicitly deferred — §9. |

## 9. Explicitly out of scope

- **Healthy-player rotation risk** (a nailed starter rested for a cup
  game / with a Thursday–Sunday turnaround). FPL's `news` almost never
  states this; it needs fixture-congestion modelling and/or predicted-XI
  scraping. A separate `rotation_factor` term was considered in the
  brainstorm and rejected for v1 (bigger blast radius, own calibration).
  Revisit as C2.1 after M6 is evaluated.
- **External news sources** (predicted-lineup sites, news APIs, LLM web
  search). Only FPL's own `news` for v1.
- **Any change to the MILP, the captain logic, or the transfer solver.**
- **Editing `docs/DECISION_RULE.md`.**

## 10. Files touched

**New:** `fpl/collect/llm_client.py`, `fpl/project/news.py`,
`fpl/evaluate/news_scorecard.py`, `data/news/news_parsed.json`,
`docs/M6_PREREGISTRATION.md`, `tests/test_llm_client.py`,
`tests/test_news.py`, `tests/test_news_scorecard.py`.

**Modified:** `fpl/transform/build_players.py` (keep `news`),
`fpl/project/minutes.py` (M6 branch + `model` param),
`fpl/project/project.py` (`m6_news` registry),
`fpl/history/archive.py` (model list),
`.github/workflows/weekly.yml` (2 steps + commit path),
`config.yaml` (`news:` block), `docs/PROJECT_LOG.md` (§22),
`docs/HANDOFF.md` (header), `CLAUDE.md` (model registry table row),
`requirements.txt` (`anthropic`).
