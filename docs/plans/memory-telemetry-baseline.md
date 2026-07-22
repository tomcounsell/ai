---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2200
last_comment_id:
---

# Memory Telemetry: Corpus-Level Metrics JSON Export + Pre-Intervention Baseline

## Problem

The subconscious memory system records rich per-record outcome telemetry — each
injected memory accumulates `acted`/`dismissed` outcomes in
`metadata.outcome_history` — but the only surface for it is an HTML dashboard
(`GET /memories`, port 8500). There is no machine-readable corpus-level export,
no aggregate numbers, and no committed baseline. We cannot state today's
aggregate act rate or junk rate as a number, attribute a future improvement to a
specific intervention, or alert on regressions.

This is **Phase 1 (Layer A: Measure)** of a multi-phase memory-optimization
effort. Every later phase (write-gate unification #2201, ingest distillation
#2202, outcome-loop + pruning #2203) is scored against the baseline this issue
establishes. Get the measurement wrong and every downstream "we improved X"
claim is unfalsifiable.

**Current behavior:**
- `/memories` is HTML-only (`ui/app.py:237`, `response_class=HTMLResponse`);
  `GET /dashboard.json` (`ui/app.py:749`) omits memory metrics entirely.
- Per-record stats (`acted_count`, `dismissed_count`, `act_rate`,
  `decay_imminent`, `confidence`) are computed in `ui/data/memories.py:72`
  (`_decorate_record`) but never aggregated or exported.
- Analytics counters (`memory.extraction`, `memory.extraction.error`,
  `memory.extraction.session_cap_hit`) are emitted to
  `analytics.collector.record_metric` but surfaced nowhere.
- A live snapshot (2026-07-22, ~305 records) shows visible junk ("Yup", "Ahhh",
  "includes:") at flat importance 6.0 — but the junk *rate* is unquantified.

**Desired outcome:**
A JSON metrics endpoint (`GET /memories/metrics.json`) plus a committed
pre-intervention baseline artifact (JSON + short markdown summary), produced
with zero interactive steps, so that every subsequent memory-pipeline change can
report "metric X moved from A to B." The whole phase is **read-only** with
respect to Memory records — measurement must not mutate, gate, or prune anything
or the baseline is contaminated.

## Freshness Check

**Baseline commit:** c366bdb8 (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-22T04:30:11Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `ui/app.py:237` — `/memories` HTML-only route — still holds (`@app.get("/memories", response_class=HTMLResponse)`).
- `ui/app.py:749` — `/dashboard.json` route, memory-less — still holds (issue said 746; the `@app.get("/dashboard.json")` decorator is at 749, body import at 749 — minor line drift, no semantic change).
- `ui/data/memories.py:72` — `_decorate_record` computing per-record stats — still holds exactly.
- `agent/memory_extraction.py:1356` — `compute_act_rate(outcome_history) -> float | None` — still holds; returns `None` on empty history, `acted/len` otherwise (NO minimum-evidence floor — confirms the issue's requirement to add one at the aggregate layer).
- `agent/memory_extraction.py:1254` — `_persist_outcome_metadata` — still holds.
- `models/memory.py:119-124` — `outcome_history` docstring (`{outcome, reasoning, ts}`, capped) — still holds.

**Cited sibling issues/PRs re-checked:** none cited in the issue body beyond the downstream Phase 2-4 issues (#2201-#2203), which are consumers, not blockers.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since` over `ui/app.py ui/data/memories.py tools/memory_eval/ agent/memory_extraction.py models/memory.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** none — no existing plan mentions "memory" or "telemetry".

**Notes:** Only drift is the `/dashboard.json` decorator line (746 → 749), cosmetic. All claims hold against current main.

## Prior Art

Searched closed issues (`memory metrics telemetry baseline`) and merged PRs
(`memory eval metrics`).

- **Issue #1542** (closed 2026-06-02): "Production cutover: granite-agent-loop…"
  — unrelated (session-runner cutover), no bearing on memory telemetry.
- No prior issue or PR attempted a corpus-level memory metrics export or a
  committed baseline. This is greenfield with respect to the *export*, but it
  sits on top of existing, well-tested infrastructure (`tools/memory_eval/`
  metric functions, `ui/data/memories.py` decoration layer). No prior failed
  fix exists — the "Why Previous Fixes Failed" section is omitted.

## Research

No relevant external findings — proceeding with codebase context and training
data. This work is purely internal: it aggregates already-computed per-record
stats and exposes them via an existing FastAPI app and a `python -m` CLI. No new
external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

1. **Entry point (endpoint):** `GET /memories/metrics.json` (new route in
   `ui/app.py`) or `python -m tools.memory_eval.snapshot` (new CLI).
2. **Loader:** new `get_corpus_metrics(project_key=None, min_evidence=2)` in
   `ui/data/memories.py` — queries **all** `Memory` records for the resolved
   project keys (no `limit` truncation; superseded records loaded and counted
   separately from the durable-corpus denominator), reusing `_decorate_record`
   to get per-record `act_rate`, `outcome_history`, `source`, `importance`,
   `confidence`, `access_count`, `decay_imminent`.
3. **Classification:** each decorated record is classified by the shared
   heuristic module `agent/memory_quality.py` into `durable | ack_only |
   fragment`. This is the SAME module Phase 2's write gate (#2201) will import,
   guaranteeing identical junk definitions across measure and gate.
4. **Aggregation:** `tools/memory_eval/ingest_quality.py` (new, pure functions)
   folds the classified/decorated list into a corpus-metrics dict:
   aggregate act rate (min-evidence-filtered), act-rate distribution, dismissal
   rate, junk rate, ingest volume by `source`, importance/confidence
   distributions (histogram buckets), decay-imminent count, never-injected
   count (`access_count == 0`), fragment-suspect count. Counter metrics
   (`memory.extraction*`) are attached best-effort from `analytics.collector`.
5. **Output:** endpoint returns `JSONResponse(metrics)`; CLI writes the metrics
   JSON to `docs/baselines/memory-telemetry-baseline.json` and renders a short
   markdown summary to `docs/baselines/memory-telemetry-baseline.md`, both
   committed to the repo.

## Architectural Impact

- **New dependencies:** none (pure-Python; no new pip packages).
- **New modules:** `agent/memory_quality.py` (pure leaf, no popoto/redis
  imports), `tools/memory_eval/ingest_quality.py` (pure aggregation),
  `tools/memory_eval/snapshot.py` (CLI wrapper). One new function in
  `ui/data/memories.py`, one new route in `ui/app.py`.
- **Interface changes:** additive only — one new endpoint, one new CLI entry,
  one new data-layer function. No existing signature changes.
- **Coupling:** `agent/memory_quality.py` is deliberately a dependency-light
  leaf so BOTH `models/memory.py` (Phase 2 write gate) and `tools/memory_eval/`
  can import it without circular-import or heavy-dependency risk. `models/`
  already lazily imports `agent.memory_extraction` inside functions, so the
  import direction (models → agent leaf, lazy) is established and safe.
- **Data ownership:** unchanged. This phase READS Memory records only.
- **Reversibility:** trivial — delete the new modules/route/CLI and the baseline
  artifacts; nothing else touches them.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (junk-heuristic definition sign-off; baseline artifact location)
- Review rounds: 1

The coding is modest (pure functions + one route + one CLI). The care goes into
(a) defining junk/fragment heuristics that Phase 2 will inherit, and (b) proving
the read-only invariant so the baseline is trustworthy.

## Prerequisites

No prerequisites — this work has no external dependencies. It reads existing
Redis-backed Memory records via the already-configured Popoto ORM. A local
`redis` with a populated corpus is needed only to *generate* the committed
baseline artifact (the maintainer runs the CLI once on a machine with the live
corpus); all unit tests run on synthetic fixtures with no Redis.

## Solution

### Key Elements

- **`agent/memory_quality.py` (shared heuristics)**: pure string classifier —
  `classify_content(content) -> "durable" | "ack_only" | "fragment"`, plus
  predicate helpers `is_ack_only(content)` and `is_fragment(content)`. No
  imports of popoto, redis, or heavy modules. This is the single source of truth
  for "what counts as junk," reused verbatim by Phase 2's write gate.
- **`tools/memory_eval/ingest_quality.py` (corpus aggregation)**: pure functions
  over a list of decorated record dicts → a corpus-metrics dict. No Redis, no
  network — unit-tested on synthetic fixtures, matching the existing
  `tools/memory_eval/metrics.py` style.
- **`ui/data/memories.py::get_corpus_metrics`**: the one function that touches
  Redis; loads all records, decorates, classifies, aggregates. Wrapped in
  try/except like the existing loaders so the dashboard never crashes.
- **`GET /memories/metrics.json` (endpoint)**: thin `JSONResponse` wrapper over
  `get_corpus_metrics`. Optional `?project_key=` and `?min_evidence=` query
  params.
- **`python -m tools.memory_eval.snapshot` (baseline CLI)**: non-interactive;
  computes metrics and writes `docs/baselines/memory-telemetry-baseline.json` +
  `.md`. Idempotent (re-running overwrites the same paths).

### Flow

Maintainer runs `python -m tools.memory_eval.snapshot` → CLI calls
`get_corpus_metrics()` → writes JSON + markdown baseline → maintainer commits the
two artifacts. Separately, any consumer hits `GET /memories/metrics.json` →
live corpus metrics as JSON, anytime, no interaction.

### Technical Approach

- **Aggregate act rate with a minimum-evidence filter.** `compute_act_rate`
  returns a per-record ratio with no evidence floor — a single "acted" outcome
  yields `act_rate = 1.0`, which is noise. The aggregate layer counts a record's
  act rate only when `acted + dismissed >= min_evidence` (default 2). Report
  BOTH the evidence-filtered aggregate and the count of records excluded for
  thin evidence, so the denominator is transparent.
- **Junk rate = share of records classified `ack_only` or `fragment`** by
  `agent/memory_quality.py`, over the full corpus (durable denominator excludes
  superseded records; report superseded count separately). Heuristics (initial):
  `ack_only` = content stripped to <= ~3 tokens and matching an acknowledgement
  lexicon / no verb-noun content ("Yup", "Ahhh", "ok", "thanks"); `fragment` =
  dangling syntax (ends with `:` and no body, unbalanced brackets, leading
  list-marker with no content, single trailing colon like "includes:"). These
  are conservative and documented as the Phase-1 definition; Phase 2 refines them
  in the SAME module.
- **Ingest volume by writer path = counts grouped by `Memory.source`**
  (`human | agent | system | knowledge` — the enum at `models/memory.py:46`).
- **Distributions as fixed histogram buckets** for `importance` and
  `confidence` (e.g. 0.0-0.2, 0.2-0.4, …) so the JSON is stable and diffable
  across snapshots.
- **Never-injected count** = records with `access_count == 0`
  (`AccessTrackerMixin`). **Decay-imminent count** reuses the existing
  `decay_imminent` decoration.
- **No new Popoto model, no schema change** → no migration required
  (`scripts/update/migrations.py` untouched). Confirmed: this phase adds no
  fields to `Memory`.
- **Read-only invariant enforced in code review + Verification**: new modules
  must contain zero `.save(` / `.delete(` / `transition_status(` calls on
  Memory. An anti-criterion grep asserts this.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `get_corpus_metrics` wraps the Redis query in try/except (mirroring
  `get_memories`); a test asserts it returns a well-formed empty-corpus metrics
  dict (not a crash) and logs a warning when the Memory query raises.
- [ ] `snapshot.py` CLI: test that a metrics-computation failure exits non-zero
  with a logged error rather than writing a truncated/partial artifact.

### Empty/Invalid Input Handling
- [ ] `agent/memory_quality.classify_content`: tests for `""`, `None`,
  whitespace-only → classified as `fragment`/`ack_only` deterministically (never
  raises). Document the chosen disposition.
- [ ] `ingest_quality` aggregation over an empty record list → returns a
  zero-filled metrics dict with all keys present (no ZeroDivisionError on rates:
  guard denominators, return `0.0` or `null` for undefined rates).
- [ ] Records with malformed `metadata` (non-dict, missing `outcome_history`) —
  `_decorate_record` already defends against these; a test feeds a malformed
  record through the aggregation path.

### Error State Rendering
- [ ] `GET /memories/metrics.json` on an empty/unavailable corpus returns HTTP
  200 with a zero-filled metrics body (never a 500), matching the dashboard's
  never-crash contract; integration test asserts status + key presence.

## Test Impact

No existing tests are broken — all changes are additive (new modules, one new
route, one new data-layer function). `tests/unit/test_memory_eval.py` is
extended with new test classes; no existing case in it changes behavior.

- [ ] `tests/unit/test_memory_eval.py` — UPDATE (additive): add
  `TestIngestQuality` class covering aggregation on synthetic fixtures.
- [ ] `tests/unit/test_memory_quality.py` — CREATE: heuristic classification
  cases (ack-only, fragment, durable, edge cases).
- [ ] `tests/integration/test_dashboard.py` (or the existing UI-app test module)
  — UPDATE (additive): add a case hitting `GET /memories/metrics.json` and
  asserting the response schema.

## Rabbit Holes

- **Building a real time-series database.** Explicitly dropped in recon.
  Periodic JSON snapshots at reflection cadence (if ever) are enough; do not
  stand up InfluxDB/Prometheus/etc.
- **Perfecting the junk heuristic.** The Phase-1 goal is a *documented,
  reused-once* definition, not a perfect classifier. An LLM-based junk judge is
  out of scope — keep it to cheap deterministic string rules so Phase 2's write
  gate can call it on the hot write path.
- **Retrofitting `dashboard.json`.** Tempting to fold memory metrics into the
  existing `/dashboard.json`, but a dedicated `/memories/metrics.json` keeps the
  corpus aggregation (which loads ALL records) off the frequently-polled
  dashboard path. Keep them separate.
- **Wiring a periodic reflection to persist snapshots.** Nice-to-have, but a
  full trend store is out of scope; the committed baseline + live endpoint
  satisfy the acceptance criteria.

## Risks

### Risk 1: Loading the full corpus is slow on large partitions
**Impact:** `get_corpus_metrics` scans every record (no `limit`), unlike the
dashboard's capped 200. On a big multi-project corpus the endpoint could be
slow.
**Mitigation:** Aggregation is O(n) pure-Python over already-in-memory decorated
dicts; at current scale (~305 records) this is milliseconds. Document the
endpoint as an on-demand analytics surface, not a hot path, and keep it off
`/dashboard.json`. If it ever matters, add a short in-process TTL cache — noted,
not built.

### Risk 2: Junk heuristic disagrees with Phase 2's needs
**Impact:** If Phase 2's write gate wants a different definition, the "single
source of truth" promise breaks.
**Mitigation:** Put the heuristic in `agent/memory_quality.py` NOW and have both
this phase and #2201 import it. Any refinement lands in that one module and both
consumers move together. Flag the heuristic thresholds for PM sign-off before
the baseline is committed.

### Risk 3: Baseline captured from a non-representative corpus
**Impact:** If the baseline is generated on a stale or tiny local corpus, later
comparisons are misleading.
**Mitigation:** Generate the committed baseline on the machine holding the live
production corpus; record the record count, timestamp, and git SHA inside the
artifact so its provenance is auditable.

## Race Conditions

### Race 1: Corpus mutates mid-scan while a session writes a memory
**Location:** `ui/data/memories.py::get_corpus_metrics` iteration over
`Memory.query.filter(...)`.
**Trigger:** A worker session persists/decays a memory while the aggregation
loop is reading.
**Data prerequisite:** none — the read does not depend on any write ordering.
**State prerequisite:** none.
**Mitigation:** The scan is a point-in-time snapshot and is READ-ONLY; a record
appearing/disappearing mid-scan shifts an aggregate count by at most one and
never corrupts state. This is acceptable and expected for a corpus snapshot —
the artifact records its own timestamp. No lock needed.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2201] Unifying/activating the memory write gate to *act* on
  the junk classification (this phase only measures; #2201 gates).
- [SEPARATE-SLUG #2202] Ingest distillation / rewriting low-quality memories.
- [SEPARATE-SLUG #2203] Outcome-loop tuning and pruning activation.
- Real time-series datastore for trend lines — dropped in recon; periodic JSON
  snapshots suffice and are themselves out of scope for this phase beyond the
  single committed baseline. This is a scope-control decision, not a deferred
  code outcome, so it carries no anti-criterion.

## Update System

No update system changes required — this feature is purely internal. The new CLI
(`python -m tools.memory_eval.snapshot`) and endpoint live inside the existing
app/venv and need no propagation, no new config file, and no new dependency. No
`scripts/update/` or `/update`-skill changes.

## Agent Integration

No new MCP tool or bridge import is required — this is a dashboard/CLI-internal
change. The metrics are reachable two ways the agent already has: the web app
(`GET /memories/metrics.json` via the running `python -m ui.app` server) and the
Bash tool (`python -m tools.memory_eval.snapshot`, and a plain
`curl -s localhost:8500/memories/metrics.json`). No `pyproject.toml [project.scripts]`
entry is needed because `python -m tools.memory_eval.snapshot` is invokable
as-is; an integration test asserts the endpoint responds and the CLI produces a
schema-valid artifact.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/memory-telemetry.md` describing the metrics schema,
  the junk/fragment heuristic definitions, the `/memories/metrics.json` endpoint,
  and the `snapshot` CLI + baseline artifact location.
- [ ] Add entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstrings on `classify_content`, `get_corpus_metrics`, and every public
  function in `ingest_quality.py` (matching the existing `metrics.py` docstring
  style).
- [ ] A comment in `agent/memory_quality.py` stating it is the shared Phase-1/2
  junk-definition module and must stay dependency-light.

### CLAUDE.md
- [ ] Add a Quick Command row for `python -m tools.memory_eval.snapshot` and note
  the `/memories/metrics.json` endpoint alongside the existing dashboard rows.

## Success Criteria

- [ ] `GET /memories/metrics.json` returns corpus-level metrics including
  aggregate act rate (with minimum-evidence filter), junk rate, ingest volume by
  writer path (`source`), and confidence/importance distributions.
- [ ] Junk/fragment heuristics live in one shared module (`agent/memory_quality.py`)
  with unit tests, importable by both `models/` and `tools/memory_eval/`.
- [ ] A pre-intervention baseline snapshot (JSON artifact + markdown summary) is
  committed under `docs/baselines/`.
- [ ] `tools/memory_eval/` gains an ingest-quality metrics module without
  duplicating existing metric functions in `metrics.py`.
- [ ] No manual/interactive step is required to produce metrics (endpoint + `python -m` CLI).
- [ ] New code performs zero writes to Memory records (read-only invariant).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (heuristics + aggregation)**
  - Name: metrics-builder
  - Role: implement `agent/memory_quality.py`, `tools/memory_eval/ingest_quality.py`, and their unit tests
  - Agent Type: builder
  - Domain: redis-popoto (pure-read data shapes)
  - Resume: true

- **Builder (endpoint + CLI + loader)**
  - Name: surface-builder
  - Role: implement `get_corpus_metrics`, the `/memories/metrics.json` route, and the `snapshot` CLI + baseline artifacts
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: telemetry-docs
  - Role: feature doc, README index, CLAUDE.md row, docstrings
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: telemetry-validator
  - Role: verify success criteria, read-only invariant, and schema of the emitted metrics
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Shared junk heuristic module + tests
- **Task ID**: build-heuristics
- **Depends On**: none
- **Validates**: tests/unit/test_memory_quality.py (create)
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/memory_quality.py` with `classify_content`, `is_ack_only`,
  `is_fragment` — pure functions, no popoto/redis imports.
- Handle empty/None/whitespace inputs deterministically without raising.
- Create `tests/unit/test_memory_quality.py` covering ack-only ("Yup", "Ahhh",
  "ok"), fragment ("includes:", dangling colon, empty), and durable full facts.

### 2. Corpus aggregation module + tests
- **Task ID**: build-aggregation
- **Depends On**: build-heuristics
- **Validates**: tests/unit/test_memory_eval.py (extend with TestIngestQuality)
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_eval/ingest_quality.py`: pure functions consuming a list
  of decorated record dicts → corpus metrics dict (min-evidence-filtered
  aggregate act rate + excluded count, act-rate distribution, dismissal rate,
  junk rate via `agent.memory_quality`, ingest volume by `source`,
  importance/confidence histograms, decay-imminent count, never-injected count,
  fragment-suspect count). Guard all rate denominators.
- Reuse — do NOT duplicate — anything already in `metrics.py`.
- Extend `tests/unit/test_memory_eval.py` with synthetic-fixture aggregation
  tests including the empty-corpus and malformed-metadata cases.

### 3. Data loader + endpoint + CLI
- **Task ID**: build-surfaces
- **Depends On**: build-aggregation
- **Validates**: tests/integration/test_dashboard.py (or UI-app test module; extend)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `get_corpus_metrics(project_key=None, min_evidence=2)` to
  `ui/data/memories.py` (load all records, no truncation; try/except never-crash;
  superseded counted separately).
- Add `GET /memories/metrics.json` route to `ui/app.py` returning
  `JSONResponse(get_corpus_metrics(...))` with optional query params.
- Create `tools/memory_eval/snapshot.py` (`python -m tools.memory_eval.snapshot`):
  non-interactive; writes `docs/baselines/memory-telemetry-baseline.json` and
  `.md`, embedding record count, timestamp, and git SHA as provenance.
- Add an integration test hitting the endpoint (200 + schema) and a CLI test
  asserting artifact schema.

### 4. Generate and commit the baseline artifact
- **Task ID**: build-baseline
- **Depends On**: build-surfaces
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `python -m tools.memory_eval.snapshot` against the live corpus.
- Commit `docs/baselines/memory-telemetry-baseline.json` + `.md`.
- (If run on a machine without the production corpus, capture on the corpus-owning
  machine — see Risk 3.)

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-surfaces
- **Assigned To**: telemetry-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/memory-telemetry.md`; add README index entry; add
  CLAUDE.md Quick Command row; verify docstrings.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-baseline, document-feature
- **Assigned To**: telemetry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands.
- Confirm the read-only invariant (no writes in new modules).
- Confirm the emitted metrics JSON contains every required key.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_quality.py tests/unit/test_memory_eval.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/memory_quality.py tools/memory_eval/ingest_quality.py tools/memory_eval/snapshot.py ui/data/memories.py ui/app.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/memory_quality.py tools/memory_eval/ingest_quality.py tools/memory_eval/snapshot.py` | exit code 0 |
| Heuristic module is a dependency-light leaf | `grep -cE '^(import|from) (redis\|popoto\|models)' agent/memory_quality.py` | match count == 0 |
| Read-only invariant (no Memory writes in new code) | `grep -rnE '\.(save\|delete)\(\|transition_status\(' agent/memory_quality.py tools/memory_eval/ingest_quality.py tools/memory_eval/snapshot.py` | match count == 0 |
| Endpoint registered | `grep -c 'memories/metrics.json' ui/app.py` | output > 0 |
| Aggregation does not re-implement metrics.py | `grep -cE 'def (recall_at_k\|mrr\|ndcg_at_k\|bootstrap_ci)' tools/memory_eval/ingest_quality.py` | match count == 0 |
| Baseline artifact committed | `test -f docs/baselines/memory-telemetry-baseline.json && echo ok` | output contains ok |
| Min-evidence filter present | `grep -c 'min_evidence' tools/memory_eval/ingest_quality.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Junk heuristic thresholds** — Phase 1 proposes deterministic string rules
   (ack-only ≤ ~3 tokens matching an acknowledgement lexicon; fragment =
   dangling syntax). Is this cheap-deterministic definition acceptable as the
   shared Phase-1/2 contract, or do you want a specific token/lexicon list
   pinned before the baseline is committed?
2. **Baseline artifact location** — plan proposes `docs/baselines/`. Acceptable,
   or prefer `data/baselines/` (gitignored data dir) / somewhere else? The
   artifact must be committed, so a tracked path is required.
3. **Min-evidence default** — proposed `min_evidence=2` for the aggregate act
   rate (excludes single-sample records). Confirm 2 is the right floor, or
   specify a different threshold.
