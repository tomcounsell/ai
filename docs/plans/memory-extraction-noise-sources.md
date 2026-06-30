---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/tomcounsell/ai/issues/1822
last_comment_id:
---

# Memory Extraction: Three Systematic Noise Sources

## Problem

The subconscious memory system runs Haiku-based post-session extraction on every
completed session (`agent/memory_extraction.py`) and persists the resulting
observations as `Memory` records. Three distinct noise sources slip past every
existing filter and accumulate at scale (the `valor` partition holds ~1,512
records). The noise competes with genuine signal in BM25 ranking and, in at least
one confirmed case (`b0b24ef7`, access_count=6), caused the agent to recall and
act on Haiku's own refusal reasoning instead of a real observation.

**Current behavior:**

1. **Incomplete refusal-pattern list.** `_looks_like_refusal()` filters Haiku's
   refusals against a closed-vocabulary `_REFUSAL_PATTERNS` tuple
   (`agent/memory_extraction.py:59`). When Haiku rephrases a refusal, the new
   phrasing is saved. 7 distinct surviving phrasings were confirmed in production
   on 2026-06-29 (memory IDs `0208f60d`, `b0b24ef7`, `517ccf5`, `9fd6006a`,
   `1a572475`, `868869`, `8f2c9d5c`).

2. **No trivial-session gate.** `run_post_session_extraction()` runs on every
   completed session, including 1-turn CLI interactions (e.g., the user runs
   `/update`). The only pre-LLM length guard rejects responses under 50 chars,
   but a `/update` response is ~2000 chars of skill docs — it passes the guard,
   Haiku extracts, and produces records like `"In a 1-event session, human ran
   /update."` (`010456c1`, `891174521`, both access_count=0).

3. **Session-scoping boilerplate saved as observations.** SDLC sub-sessions
   inject a scope-boundary preamble ("this session is scoped to sdlc-local-N; do
   not include work from other sessions"). Haiku reads it as session context and
   extracts it, producing records like `"Valor AI agentic system scoped to
   isolated session contexts (sdlc-local-96) with strict boundary enforcement"`
   (`1911b062`, confidence=0.90 — high-confidence noise) on every SDLC cycle.

Plus a GC gap: `memory_decay_prune` only deletes at `importance < 0.15`, but all
`pattern`/`surprise` extractions are saved at `importance = 1.0` and are never
pruned.

**Desired outcome:**
- Refusal-echo records do not enter the corpus, including phrasings Haiku hasn't
  used before.
- Trivial/short sessions (≤1 turn, CLI-only) are skipped for extraction.
- Session-infrastructure boilerplate (scope boundaries, session slugs) is never
  saved as an observation.
- `memory_decay_prune` gains a second tier that catches `importance = 1.0`
  never-recalled baseline noise — behind a dry-run gate.

## Freshness Check

**Baseline commit:** `4a66f506d245e4892440bec0973c65d527e413b4`
**Issue filed at:** 2026-06-29T11:15:42Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/memory_extraction.py:59` (`_REFUSAL_PATTERNS`) — still holds. Tuple has
  8 entries; none match the 7 confirmed noise phrasings.
- `agent/memory_extraction.py:163` (`_looks_like_refusal`) — still holds; exact
  line match.
- `reflections/memory/memory_decay_prune.py` — still holds. Floor is
  `WF_MIN_THRESHOLD = 0.15` (line 28); `MEMORY_DECAY_PRUNE_APPLY` dry-run gate at
  line 56.
- `agent/session_executor.py` — extraction is scheduled at line 1920 via
  `_schedule_post_session_extraction(session.session_id, task._result or "")`,
  which wraps `run_post_session_extraction(session_id, response_text)` at line
  207. The `session` object (carrying `session.turn_count`) is in scope at the
  call site. (Issue cited `session_executor.py` generically; corrected precise
  lines noted here.)

**Cited sibling issues/PRs re-checked:**
- #1212 (closed) — added `_REFUSAL_PATTERNS` / `_looks_like_refusal`; this issue
  extends that work. Still the relevant baseline.
- #1786 (open) — session-specific junk (21 records in one run); orthogonal, this
  issue is systematic.
- #1231 (closed) — `memory-quality-audit` reflection; downstream safety net, not
  a substitute for upstream filters.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=<createdAt>` over `agent/memory_extraction.py`,
`reflections/memory/memory_decay_prune.py`, `agent/session_executor.py`, and
`tests/unit/test_memory_extraction.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Bug confirmed still present by code reading (all four gaps live on
`main`). Reproduction is corpus-state-dependent (requires Haiku rephrasing /
trivial sessions); the code paths are verified present rather than re-run live.

## Prior Art

- **PR #1217 / Issue #1212** (closed/merged): "harden memory extraction parser
  against JSON shrapnel and refusal prose" — introduced `_REFUSAL_PATTERNS`,
  `_looks_like_refusal()`, `extract_json_payload()`, and the dual pre/post-LLM
  refusal filter. This plan extends that machinery (more patterns + an optional
  LLM complement) rather than re-architecting it. The existing convention of
  annotating each pattern with originating memory IDs must be preserved.
- **PR #1252 / Issue #1231** (merged/closed): "3-layer memory health audit
  reflection" (`memory-quality-audit`) — supersedes junk records post-hoc and
  imports `_looks_like_refusal` directly. It is the recurring safety net but
  cannot fix upstream extraction rules; this plan fixes them upstream.
- **Issue #1213** (closed): memory recall score threshold — tangential (retrieval
  side), confirms noise-in-corpus is a known cost center.

No prior attempt addressed the trivial-session gate, the scoping-boilerplate
filter, or the GC second tier — those are greenfield within this module.

## Research

No relevant external findings — proceeding with codebase context and training
data. This is a purely internal change to extraction filters and a GC reflection;
no external libraries, APIs, or ecosystem patterns are involved.

## Spike Results

### spike-1: How is turn_count available at the extraction call site? (Fix 2)
- **Assumption**: "A `turn_count` signal is reachable from
  `run_post_session_extraction()` so extraction can be gated on ≤1-turn sessions."
- **Method**: code-read
- **Finding**: Two sources exist.
  1. `AgentSession.turn_count` — a persisted `IntField(default=0)`
     (`models/agent_session.py:175`). The full `session` object is in scope at
     the scheduler call site (`agent/session_executor.py:1920`), so
     `session.turn_count` can be threaded as a new parameter through
     `_schedule_post_session_extraction()` → `run_post_session_extraction()` →
     `extract_observations_async()`.
  2. `agent.sdk_client.get_turn_count(session_id)` — an in-memory tracker of the
     SDK's `ResultMessage.num_turns` (`agent/sdk_client.py:160`). **Caveat:**
     `clear_turn_count(session_id)` is called on session teardown, so this source
     may be cleared by the time extraction runs in the background task. It is
     therefore unreliable at extraction time.
- **Confidence**: high
- **Impact on plan**: Thread `turn_count` as an explicit parameter sourced from
  `session.turn_count` at the scheduler. Do NOT depend on
  `sdk_client.get_turn_count()` inside the background extraction task — it may be
  cleared. The gate keys on turn count (not length), per the issue's "Revised"
  recon item. Default the new parameter to `None` (gate is a no-op when unknown)
  so direct callers and tests stay backward-compatible.

### spike-2: Does the Memory model expose a baseline `confidence` for Fix 4?
- **Assumption**: "Records carry a `confidence` field whose untouched baseline is
  0.5, distinguishing never-reinforced noise from acted-upon memories."
- **Method**: code-read
- **Finding**: Yes — `confidence = ConfidenceField(initial_confidence=0.5)`
  (`models/memory.py:152`). ObservationProtocol updates it away from 0.5 when a
  memory is acted on or dismissed. **Caveat:** `confidence` is a float; exact
  `== 0.5` comparison is fragile. The GC tier should use an epsilon tolerance
  (`abs(confidence - 0.5) < 1e-6`) rather than strict equality.
- **Confidence**: high
- **Impact on plan**: Fix 4's new tier filters on
  `access_count == 0 AND importance <= 1.0 AND abs(confidence - 0.5) < 1e-6 AND
  age > 14 days`, reusing the existing `IMPORTANCE_EXEMPT_THRESHOLD = 7.0`
  guard and the `superseded_by` skip.

## Data Flow

1. **Entry point**: A session completes. `agent/session_executor.py:1920` calls
   `_schedule_post_session_extraction(session.session_id, task._result or "")`
   (fire-and-forget asyncio task — must stay non-blocking, #987/#1055).
2. **Scheduler** (`_schedule_post_session_extraction`, line 171): creates a
   background task that calls `run_post_session_extraction(session_id,
   response_text)` (line 207). **Fix 2 wires `turn_count` through here.**
3. **Pipeline** (`run_post_session_extraction`, line 1061): calls
   `extract_observations_async()`. **Fix 2's trivial-session gate lands here or
   at the top of `extract_observations_async`.**
4. **Extraction** (`extract_observations_async`, line 327): pre-LLM guards (50
   chars, `_looks_like_refusal` — **Fix 1**, whitespace ratio) → Haiku call →
   post-LLM `_looks_like_refusal` (**Fix 1**) → `_parse_categorized_observations`
   → save loop. **Fix 3's scoping filter lands in the save path / parser.**
5. **Parser** (`_parse_categorized_observations`, line 495): JSON path then
   line-based fallback. **Fix 3** drops any observation containing scoping
   markers before it becomes a tuple.
6. **Storage**: `Memory.safe_save(...)` at `importance` per category (1.0 for
   pattern/surprise). Records with no recall sit at `confidence=0.5`,
   `access_count=0` forever.
7. **GC** (`reflections/memory/memory_decay_prune.py::run`, daily): **Fix 4**
   adds a second pruning tier downstream of all the above as the catch-all.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #1217 (#1212) | Added closed-vocabulary `_REFUSAL_PATTERNS` + dual refusal filter | Closed vocabulary by design; any Haiku rephrasing escapes until manually appended. Addressed JSON shrapnel and the then-known phrasings, not the open-ended rephrasing problem or the two non-refusal noise sources (trivial sessions, scoping boilerplate). |
| PR #1252 (#1231) | `memory-quality-audit` reflection supersedes junk clusters | Operates post-hoc on already-persisted records; cannot prevent upstream extraction. A cleanup net, not a source fix. |

**Root cause pattern:** all prior work treated symptoms at the parse/cleanup
layer. The noise originates earlier — Haiku is invoked on inputs that should
never reach it (trivial sessions, infrastructure preambles) and its refusal
vocabulary is open-ended. The fix must move filtering upstream (gate before the
LLM call; filter structural content) and add a backstop GC tier for whatever
still slips through.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `run_post_session_extraction()`,
  `_schedule_post_session_extraction()`, and `extract_observations_async()` gain
  an optional `turn_count: int | None = None` parameter (backward-compatible
  default). Optional LLM-refusal complement (Fix 1) reuses the existing
  `_llm_call` helper — no new call pattern.
- **Coupling**: slightly increases coupling between the executor (turn count) and
  the extraction pipeline, but via an explicit parameter, not a global.
- **Data ownership**: unchanged. Memory records still owned by the memory system.
- **Reversibility**: high. Each fix is independently revertable; Fix 4 is
  dry-run-gated so it deletes nothing until explicitly enabled.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the optional LLM-refusal complement is wanted vs.
  pattern-extension-only; confirm GC tier thresholds)
- Review rounds: 1

Four small, independent fixes in one module plus one reflection. Coding time is
short; the overhead is the design decision on Fix 1's optional LLM complement and
careful test coverage for each new filter.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key (only if the optional LLM-refusal complement in Fix 1 is built) | `python -c "from utils.api_keys import get_anthropic_api_key as g; assert g()"` | The optional LLM refusal detector calls Haiku via the existing `_llm_call` path. Pattern-extension (the core of Fix 1) needs no key. |

Run via `python scripts/check_prerequisites.py docs/plans/memory-extraction-noise-sources.md`.

## Solution

### Key Elements

- **Fix 1 — Extend `_REFUSAL_PATTERNS`**: append the 7 confirmed phrasings as
  narrow, full-phrase substrings, each annotated with its originating memory ID
  per the existing convention. Optionally add an LLM-based refusal complement
  (Haiku yes/no) so future rephrasing self-heals without a manual cycle.
- **Fix 2 — Trivial-session gate**: skip extraction when `turn_count <= 1`,
  sourced from `session.turn_count` and threaded explicitly to the extraction
  call. Keys on turn count, not length (per recon "Revised" item).
- **Fix 3 — Scoping-boilerplate filter**: drop any observation whose text
  contains a session-scoping marker (`"sdlc-local-"`, `"scoped to isolated
  session"`, `"scope boundary"`, `"cross-session"`) before it is saved.
- **Fix 4 — GC second tier**: in `memory_decay_prune`, add a tier matching
  `importance <= 1.0`, `access_count == 0`, `confidence ≈ 0.5`, `age > 14 days`,
  reusing the existing `MEMORY_DECAY_PRUNE_APPLY` dry-run gate.

### Flow

Session completes → executor reads `session.turn_count` → schedules extraction
with turn_count → **Fix 2** gate (skip if ≤1) → pre-LLM `_looks_like_refusal`
(**Fix 1** patterns) → Haiku → post-LLM refusal + optional LLM refusal complement
(**Fix 1**) → parse → **Fix 3** scoping filter → save → (nightly) **Fix 4** GC
tier prunes residual baseline noise.

### Technical Approach

- **Fix 1 (independent, ship first):** Append 7 entries to `_REFUSAL_PATTERNS`
  (`agent/memory_extraction.py:59`). Patterns must be full phrases (not bare
  keywords) to preserve the narrowness invariant guarded by
  `TestRefusalPatternsNarrowness`. Derive each phrase from the actual stored
  content of the 7 cited memory IDs (read them via
  `python -m tools.memory_search inspect --id <ID>` before choosing the
  substring). The optional LLM complement, if approved, is a new helper that
  reuses `_llm_call(MODEL_FAST, ...)` and is called only on the post-LLM path
  (cost: one extra Haiku call per non-empty extraction) — gate it behind a small
  env flag so it can be disabled.
- **Fix 2:** Add `turn_count: int | None = None` to
  `_schedule_post_session_extraction`, `run_post_session_extraction`, and
  `extract_observations_async`. Source it from `session.turn_count` at line 1920.
  Gate at the top of `extract_observations_async` (alongside the 50-char guard):
  `if turn_count is not None and turn_count <= 1: return []`. `None` = unknown =
  no-op, preserving existing direct-caller/test behavior.
- **Fix 3:** Define a module constant `_SCOPING_MARKERS = ("sdlc-local-",
  "scoped to isolated session", "scope boundary", "cross-session")`. Add a
  helper `_is_scoping_boilerplate(text) -> bool` (case-insensitive substring
  match) and filter in `_parse_categorized_observations` (both JSON and
  line-based paths) before tuples are emitted, so direct callers are covered too.
- **Fix 4:** In `memory_decay_prune.py::run`, after the existing tier-1 candidate
  loop, add a tier-2 pass over the same `all_memories` with the new predicate
  (`importance <= 1.0`, `access_count == 0`, `abs((confidence or 0.5) - 0.5) <
  1e-6`, age > `NOISE_PRUNE_AGE_DAYS = 14`, not superseded, importance < 7.0).
  Reuse the existing `MEMORY_DECAY_PRUNE_APPLY` env gate and `MAX_PRUNE_PER_RUN`
  cap (apply the cap across the union of both tiers). Make `NOISE_PRUNE_AGE_DAYS`
  a named module constant with a "provisional/tunable" comment.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `extract_observations_async` wraps everything in `try/except Exception` →
  `logger.warning` + `_record_extraction_error`. New turn_count gate sits BEFORE
  the try block (a pure early-return), so it cannot be swallowed. Assert the gate
  returns `[]` and that NO Haiku call is made (mock `_llm_call`, assert not
  called).
- [ ] `memory_decay_prune` per-record `delete()` is wrapped in try/except and
  logged — existing behavior; add a test that a tier-2 delete failure is logged
  and the run continues.

### Empty/Invalid Input Handling
- [ ] `turn_count=None` (unknown) must NOT skip extraction — assert the gate is a
  no-op and the pipeline proceeds.
- [ ] `_is_scoping_boilerplate("")` returns `False`; empty/whitespace
  observations already filtered by the existing `len < 10` check.
- [ ] Fix 4 tier-2 with `confidence=None` must be treated as baseline 0.5 (or
  skipped safely) — assert no crash.

### Error State Rendering
- [ ] No user-visible output surface — extraction and GC are background/internal.
  The observable contract is "record not persisted" / "candidate counted in
  dry-run". Assert via Memory store inspection and the reflection's returned
  `findings`/`summary` dict.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestLooksLikeRefusal` — UPDATE: add
  a parametrized case per new phrasing (7 cases) asserting `True`.
- [ ] `tests/unit/test_memory_extraction.py::TestRefusalPatternsNarrowness` —
  UPDATE: verify the 7 new patterns are full-phrase and do NOT false-positive on
  legitimate observations that share keywords (add narrowness cases).
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction` —
  UPDATE: add a `turn_count=1` skip case and a `turn_count=None`/`turn_count=3`
  proceed case.
- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` —
  UPDATE: add scoping-boilerplate drop cases (JSON path + line-based path).
- [ ] `tests/unit/test_session_executor_extraction_decoupling.py` — UPDATE: the
  `_schedule_post_session_extraction` signature gains `turn_count`; verify the
  mock/patch call sites still match and that `session.turn_count` is threaded.
- [ ] `tests/integration/test_session_finalization_decoupled.py` — UPDATE: assert
  the extraction stub is called with the new `turn_count` argument (or that the
  call remains compatible).
- [ ] New test file `tests/unit/test_memory_decay_prune.py` (create if absent;
  otherwise extend the existing decay-prune test) — REPLACE/ADD: cover tier-2
  candidate selection, dry-run reporting, the 14-day boundary, the `confidence ≈
  0.5` epsilon, and the shared `MAX_PRUNE_PER_RUN` cap across both tiers.

## Rabbit Holes

- **Over-engineering the LLM-refusal complement (Fix 1).** It is optional and
  cost-bearing (extra Haiku call). Do not build a classifier, training set, or
  caching layer. If approved, it is a single yes/no Haiku call behind an env
  flag, nothing more. The pattern-extension is the load-bearing fix.
- **Hunting down the exact source of the scoping preamble.** Fix 3 filters on
  observation *content*; the preamble's origin (skill/prime file vs. dynamic
  prompt) is irrelevant to the filter. Do not refactor the SDLC sub-session
  prompt construction here.
- **Broadening `_REFUSAL_PATTERNS` to bare keywords.** Tempting for "future
  proofing" but it silently drops legitimate observations. Stay full-phrase;
  narrowness is guarded by an existing test.
- **Retroactively purging the 1,512-record corpus by hand.** Fix 4's GC tier (in
  apply mode, after the dry-run window) plus the existing `memory-quality-audit`
  reflection handle cleanup. No manual mass deletion.

## Risks

### Risk 1: New refusal patterns false-positive on legitimate observations
**Impact:** A real observation containing a new substring is silently dropped —
the worst failure mode for this module (silent data loss).
**Mitigation:** Full-phrase patterns only; extend `TestRefusalPatternsNarrowness`
with the legitimate-but-keyword-overlapping cases; derive each phrase from the
actual cited memory body, not a guess.

### Risk 2: turn_count is unpopulated/zero at finalization, over-skipping
**Impact:** If `session.turn_count` is 0 for real multi-turn sessions, the gate
(`<= 1`) would wrongly skip extraction, suppressing genuine memories.
**Mitigation:** Verify `session.turn_count` is populated before finalization
(spike-1 confirmed the field exists; the builder must confirm it is written for
real sessions). Gate only on `turn_count is not None and turn_count <= 1`;
default `None` = no-op. Add a test for a known multi-turn session asserting
extraction proceeds.

### Risk 3: Fix 4 deletes records that were about to be recalled
**Impact:** Premature deletion of low-but-real memories.
**Mitigation:** `access_count == 0` AND `confidence ≈ 0.5` (never reinforced)
AND `age > 14 days` are conjunctive — a record must be untouched for two weeks.
Dry-run by default via `MEMORY_DECAY_PRUNE_APPLY`; ship the tier in dry-run,
review the candidate report, then enable. `MAX_PRUNE_PER_RUN` caps blast radius.

## Race Conditions

### Race 1: turn_count read at schedule time vs. background extraction
**Location:** `agent/session_executor.py:1920` → background task.
**Trigger:** `turn_count` is captured when the task is *scheduled* but consumed
later when it *runs*.
**Data prerequisite:** `session.turn_count` must be final before line 1920.
**State prerequisite:** the value is captured as a plain int argument at schedule
time (not re-read from a mutable global inside the task), so there is no shared
mutable state to race on.
**Mitigation:** Pass `turn_count` by value into the asyncio task closure (same
pattern as `response_text` today). Do NOT call `sdk_client.get_turn_count()`
inside the task — it may be cleared (spike-1 caveat). This makes the read
race-free.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1786] Session-specific junk-burst investigation (one stuck
  session producing 21 records) — tracked separately; this plan is systematic,
  not session-specific.
- [DESTRUCTIVE] Enabling Fix 4 apply-mode deletion in production
  (`MEMORY_DECAY_PRUNE_APPLY=true`) — the tier ships dry-run; flipping to apply is
  a reviewed, post-validation operator action, not part of this build.
- [DESTRUCTIVE] Mass retroactive purge of the existing ~1,512-record corpus —
  cleanup is handled incrementally by the GC tier (once enabled) and the existing
  `memory-quality-audit` reflection.

## Update System

No update-system changes required for the code fixes — they are internal to
`agent/memory_extraction.py` and `reflections/memory/memory_decay_prune.py`,
both already deployed by the standard `/update` git pull.

One reflection-config note: `memory-decay-prune` is already declared in
`config/reflections.yaml` (line 328, callable
`reflections.memory_management.run_memory_decay_prune`, a re-export of
`reflections.memory.memory_decay_prune.run`). Fix 4 modifies the existing
callable in place, so no new reflection registration is needed. No Popoto schema
change (no new/changed model fields), so no `scripts/update/migrations.py` entry
is required.

## Agent Integration

No agent integration required — this is entirely a worker/reflection-internal
change. The extraction pipeline runs in the background worker after every session
(`agent/session_executor.py`), and the GC runs as a scheduled reflection. There
is no new CLI entry point, no MCP surface, and no bridge import. The agent already
benefits automatically via cleaner memory recall on subsequent turns.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — document the four noise
  filters: extended refusal vocabulary (+ optional LLM complement), the
  trivial-session (`turn_count <= 1`) gate, the scoping-boilerplate content
  filter, and the `memory_decay_prune` second tier (with its dry-run gate and
  14-day / confidence-baseline criteria).
- [ ] No new `docs/features/README.md` index entry needed — the feature page
  already exists in the index.

### Inline Documentation
- [ ] Annotate each new `_REFUSAL_PATTERNS` entry with its originating memory ID
  (existing convention).
- [ ] Comment `_SCOPING_MARKERS` and `NOISE_PRUNE_AGE_DAYS` as
  provisional/tunable named constants.
- [ ] Update the `memory_decay_prune.py` module docstring to describe both
  pruning tiers.

## Success Criteria

- [ ] All 7 confirmed noise phrasings (`0208f60d`, `b0b24ef7`, `517ccf5`,
  `9fd6006a`, `1a572475`, `868869`, `8f2c9d5c`) return `True` from
  `_looks_like_refusal()`.
- [ ] A session with `turn_count == 1` produces zero Memory records via
  post-session extraction (and makes no Haiku call).
- [ ] A session with `turn_count == None` or `>= 2` still extracts normally.
- [ ] No observation containing `"sdlc-local-"` or `"scoped to isolated session"`
  is persisted.
- [ ] `memory_decay_prune` reports (dry-run) candidates matching `importance <=
  1.0`, `access_count == 0`, `confidence ≈ 0.5`, `age > 14 days`, and deletes
  them only when `MEMORY_DECAY_PRUNE_APPLY` is set.
- [ ] Existing `tests/unit/test_memory_extraction.py` passes with no narrowness
  regressions.
- [ ] A test exists for each new refusal pattern.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `session_executor.py` threads `session.turn_count` into the
  extraction schedule call.

## Team Orchestration

The lead orchestrates; it does not build directly.

### Team Members

- **Builder (extraction-filters)**
  - Name: `extraction-builder`
  - Role: Implement Fix 1 (patterns + optional LLM complement), Fix 2
    (turn_count gate + signature threading), Fix 3 (scoping filter) in
    `agent/memory_extraction.py` and `agent/session_executor.py`.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (gc-tier)**
  - Name: `gc-builder`
  - Role: Implement Fix 4 (second pruning tier) in
    `reflections/memory/memory_decay_prune.py` with dry-run gate.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Test Engineer (extraction-tests)**
  - Name: `extraction-tester`
  - Role: Add/extend unit + integration tests for all four fixes.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (memory-noise)**
  - Name: `memory-validator`
  - Role: Verify all success criteria, run narrowness + gate tests, confirm no
    Haiku call on the trivial-session path.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `memory-doc`
  - Role: Update `docs/features/subconscious-memory.md` and inline docs.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types
See template legend. Domain framing (async, Redis/Popoto) pasted into builder
task assignments per `DOMAIN_FRAMING.md`.

## Step by Step Tasks

### 1. Extend refusal patterns (Fix 1)
- **Task ID**: build-refusal-patterns
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py::TestLooksLikeRefusal, ::TestRefusalPatternsNarrowness
- **Informed By**: prior-art #1212 convention (annotate each pattern with memory ID)
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Parallel**: true
- Read the 7 cited memory bodies (`python -m tools.memory_search inspect --id <ID>`); choose a narrow full-phrase substring for each.
- Append the 7 annotated entries to `_REFUSAL_PATTERNS`.
- (If PM approves) add an optional Haiku refusal-complement helper behind an env flag, reusing `_llm_call`.

### 2. Trivial-session gate (Fix 2)
- **Task ID**: build-turn-gate
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction, tests/unit/test_session_executor_extraction_decoupling.py
- **Informed By**: spike-1 (use `session.turn_count`, NOT `sdk_client.get_turn_count`; thread by value)
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: true
- Add `turn_count: int | None = None` to `_schedule_post_session_extraction`, `run_post_session_extraction`, `extract_observations_async`.
- Source it from `session.turn_count` at `session_executor.py:1920`; capture by value into the task closure.
- Gate: `if turn_count is not None and turn_count <= 1: return []` early in `extract_observations_async`.

### 3. Scoping-boilerplate filter (Fix 3)
- **Task ID**: build-scoping-filter
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py::TestParseCategorizedObservations
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_SCOPING_MARKERS` constant and `_is_scoping_boilerplate()` helper.
- Filter in both JSON and line-based paths of `_parse_categorized_observations`.

### 4. GC second tier (Fix 4)
- **Task ID**: build-gc-tier
- **Depends On**: none
- **Validates**: tests/unit/test_memory_decay_prune.py (create)
- **Informed By**: spike-2 (confidence epsilon; reuse MEMORY_DECAY_PRUNE_APPLY + MAX_PRUNE_PER_RUN)
- **Assigned To**: gc-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Add `NOISE_PRUNE_AGE_DAYS = 14` (provisional/tunable comment) and tier-2 predicate.
- Apply shared cap across both tiers; reuse the existing dry-run gate; update module docstring.

### 5. Tests for all fixes
- **Task ID**: build-tests
- **Depends On**: build-refusal-patterns, build-turn-gate, build-scoping-filter, build-gc-tier
- **Assigned To**: extraction-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add the cases enumerated in ## Test Impact and ## Failure Path Test Strategy.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: memory-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` and inline docs/docstrings.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every Success Criterion; report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Memory-extraction tests pass | `pytest tests/unit/test_memory_extraction.py -q` | exit code 0 |
| Decay-prune tests pass | `pytest tests/unit/test_memory_decay_prune.py -q` | exit code 0 |
| Decoupling tests pass | `pytest tests/unit/test_session_executor_extraction_decoupling.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/memory_extraction.py agent/session_executor.py reflections/memory/memory_decay_prune.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/memory_extraction.py agent/session_executor.py reflections/memory/memory_decay_prune.py` | exit code 0 |
| 7 new patterns present | `grep -c "0208f60d\|b0b24ef7\|517ccf5\|9fd6006a\|1a572475\|868869\|8f2c9d5c" agent/memory_extraction.py` | output > 0 |
| turn_count threaded into schedule | `grep -c "turn_count" agent/session_executor.py` | output > 0 |
| Scoping markers filter present | `grep -c "sdlc-local-" agent/memory_extraction.py` | output > 0 |
| GC tier age constant present | `grep -c "NOISE_PRUNE_AGE_DAYS" reflections/memory/memory_decay_prune.py` | output > 0 |
| Apply-mode NOT defaulted on (anti-criterion) | `grep -n 'MEMORY_DECAY_PRUNE_APPLY.*=.*"true"' reflections/memory/memory_decay_prune.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Fix 1 LLM-refusal complement — build it or pattern-extension only?** The
   issue suggests it ("Consider also adding an LLM-based refusal detector"). It
   costs one extra Haiku call per non-empty extraction and adds latency to a
   background path. Recommendation: ship pattern-extension now (zero-risk), add
   the LLM complement behind a default-off env flag as an opt-in. Confirm.
2. **Fix 2 gate threshold — `turn_count <= 1` only, or also a length floor?**
   Recon says key on turn count, not length. Recommendation: turn count only
   (length is already covered by the 50-char guard). Confirm no separate length
   threshold is wanted.
3. **Fix 4 — separate env gate or reuse `MEMORY_DECAY_PRUNE_APPLY`?** Reusing the
   existing gate means enabling apply-mode enables BOTH tiers at once.
   Recommendation: reuse the one gate for simplicity; the conjunctive predicate
   already makes tier-2 conservative. Confirm, or request a distinct
   `MEMORY_NOISE_PRUNE_APPLY` flag for staged rollout.
