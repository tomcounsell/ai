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
- **Impact on plan**: Thread `turn_count` as an explicit parameter, captured
  **at schedule time** (inside the executor turn, before teardown) — NOT read off
  the stale in-memory `session.turn_count`, which is a different instance than the
  one `sdk_client` writes and is typically `0`. Capture via
  `sdk_client.get_turn_count(session.session_id)` at the schedule call site OR a
  fresh Popoto re-fetch of the newest `AgentSession`. Do NOT call
  `sdk_client.get_turn_count()` *inside* the background extraction task — it may
  be cleared by then. The gate keys on turn count (not length), per the issue's
  "Revised" recon item. Default the new parameter to `None` (gate is a no-op when
  unknown) so direct callers and tests stay backward-compatible.

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
  an optional `turn_count: int | None = None` parameter plus a session
  origin/type signal (backward-compatible defaults). No new LLM call pattern (the
  LLM-refusal complement is cut to a follow-up).
- **Coupling**: slightly increases coupling between the executor (turn count) and
  the extraction pipeline, but via an explicit parameter, not a global.
- **Data ownership**: unchanged. Memory records still owned by the memory system.
- **Reversibility**: high. Each fix is independently revertable; Fix 4 is
  dry-run-gated so it deletes nothing until explicitly enabled.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (all open questions resolved; LLM complement cut to follow-up)
- Review rounds: 1

Four small, independent fixes in one module plus one reflection. Coding time is
short; the overhead is careful test coverage for each new filter (narrowness +
origin-gating).

## Prerequisites

None — all four fixes are pure-Python edits to extraction filters and a GC
reflection. (The LLM-refusal complement that would have needed an Anthropic API
key is cut from this PR and filed as a follow-up.)

## Solution

### Key Elements

- **Fix 1 — Extend `_REFUSAL_PATTERNS`**: append the 7 confirmed phrasings as
  narrow, full-phrase substrings, each annotated with its originating memory ID
  per the existing convention. **SCOPE CUT (critique):** the optional LLM-based
  refusal complement is DROPPED from this PR — the 7 pattern appends satisfy every
  success criterion. The LLM complement (env flag, extra Haiku call, API-key
  prereq) is filed as separate follow-up issue #1829 to avoid gold-plating here.
- **Fix 2 — Trivial-session gate**: skip extraction when `turn_count <= 1`,
  sourced from `session.turn_count` and threaded explicitly to the extraction
  call. Keys on turn count, not length (per recon "Revised" item).
- **Fix 3 — Scoping-boilerplate filter**: drop any observation whose text
  contains a session-scoping marker (`"sdlc-local-"`, `"scoped to isolated
  session"`, `"scope boundary"`, `"cross-session"`) before it is saved.
- **Fix 4 — GC second tier**: in `memory_decay_prune`, add a non-overlapping tier
  (`WF_MIN_THRESHOLD <= importance <= 1.0`, `access_count == 0`, `confidence ≈
  0.5`, `age > 14 days`) behind its OWN dedicated default-off gate
  `MEMORY_NOISE_PRUNE_APPLY`, deduped against tier-1 before the shared cap.

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
  substring). **The LLM refusal-complement is OUT OF SCOPE for this PR** (filed as
  follow-up #1829) — pattern-extension is the complete Fix 1 deliverable here.
- **Fix 2:** Add `turn_count: int | None = None` to
  `_schedule_post_session_extraction`, `run_post_session_extraction`, and
  `extract_observations_async`. **CRITICAL (critique BLOCKER):** the executor's
  in-memory `session` object at line ~1920 is a *different* instance than the one
  `sdk_client` persists `turn_count` onto (`sdk_client.py:2573-2584`), so its
  in-memory `session.turn_count` is stale (often `0`). Reading it would make the
  gate (`<= 1`) fire for *every* session and silently kill all extraction.
  RESOLUTION — capture the real turn count **synchronously at schedule time,
  while still inside the executor turn (before teardown clears anything)** and
  pass the captured `int` by value into the background task. Source it via
  `agent.sdk_client.get_turn_count(session.session_id)` read at schedule time
  (line ~1920), OR by a fresh Popoto re-fetch of the newest `AgentSession` for
  that `session_id` (`sdk_client.py:2573-2576` pattern). Do NOT read
  `session.turn_count` off the stale in-scope object, and do NOT call
  `get_turn_count()` from *inside* the background task (teardown may have cleared
  it). **Do NOT blanket-skip every single-turn session** — a substantive
  single-turn conversational message (e.g. a one-shot Telegram correction) is
  high-value and must still extract. Pair the turn-count signal with a
  session-origin/type signal so the gate ONLY skips non-conversational / CLI-origin
  single-turn sessions. Thread the session origin/type alongside the captured
  `turn_count` (e.g. a captured `is_cli_origin: bool` / session_type, sourced at
  schedule time from the in-scope `session`). Gate at the top of
  `extract_observations_async` (alongside the 50-char guard): skip (`return []`)
  only when `turn_count is not None and turn_count <= 1 AND` the session is
  CLI-origin / non-conversational. `turn_count=None` (unknown) = no-op, preserving
  existing direct-caller/test behavior. Tests: (a) the gate sees the real count
  (not 0) for a multi-turn session; (b) a substantive single-turn **Telegram**
  correction STILL extracts; (c) a CLI `/update`-style single-turn session does
  NOT extract.
- **Fix 3:** Define a module constant `_SCOPING_MARKERS = ("sdlc-local-",
  "scoped to isolated session")` — **only substrings actually observed in real
  noise records.** The previously-listed `"scope boundary"` and `"cross-session"`
  are UNCONFIRMED (never seen in an evidenced noise record) and are dropped:
  adding an unevidenced marker risks silently dropping legitimate observations.
  Add a helper `_is_scoping_boilerplate(text) -> bool` (case-insensitive
  substring match) and filter in `_parse_categorized_observations` (both JSON and
  line-based paths) before tuples are emitted, so direct callers are covered too.
  Add a `_SCOPING_MARKERS` narrowness regression test (mirroring Fix 1's
  `TestRefusalPatternsNarrowness`) asserting legitimate observations that merely
  mention sessions/scope are NOT dropped.
- **Fix 4:** In `memory_decay_prune.py::run`, after the existing tier-1 candidate
  loop, add a tier-2 pass over the same `all_memories` with the new predicate
  (`importance <= 1.0`, `access_count == 0`, `abs((confidence or 0.5) - 0.5) <
  1e-6`, age > `NOISE_PRUNE_AGE_DAYS = 14`, not superseded, importance < 7.0).
  **Tier overlap (critique BLOCKER):** tier-1 (`importance < WF_MIN_THRESHOLD =
  0.15`) is a strict subset of tier-2 (`importance <= 1.0`), so a naive two-loop
  concat double-counts against `MAX_PRUNE_PER_RUN` and issues duplicate deletes.
  RESOLUTION — make the tiers non-overlapping by construction: give tier-2 a lower
  bound `importance >= WF_MIN_THRESHOLD` so it excludes tier-1 (AND dedupe the
  union by `memory_id` before slicing to the cap as a belt-and-suspenders).
  **Separate gate (critique):** tier-2 gets its OWN dedicated default-off env gate
  `MEMORY_NOISE_PRUNE_APPLY` (distinct from `MEMORY_DECAY_PRUNE_APPLY`) so the
  broader tier-2 predicate can be validated in dry-run independently before any
  deletion is enabled. Tier-1 keeps using `MEMORY_DECAY_PRUNE_APPLY`. Apply the
  shared `MAX_PRUNE_PER_RUN` cap across the deduped union. Make
  `NOISE_PRUNE_AGE_DAYS` a named module constant with a "provisional/tunable"
  comment.

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
  UPDATE: add a CLI-origin `turn_count=1` skip case, a substantive single-turn
  Telegram `turn_count=1` PROCEED case, and `turn_count=None`/`turn_count=3`
  proceed cases.
- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` —
  UPDATE: add scoping-boilerplate drop cases (JSON path + line-based path) AND a
  `_SCOPING_MARKERS` narrowness case (legitimate session/scope-mentioning
  observation is NOT dropped).
- [ ] `tests/unit/test_session_executor_extraction_decoupling.py` — UPDATE: the
  `_schedule_post_session_extraction` signature gains `turn_count`; verify the
  mock/patch call sites still match and that `session.turn_count` is threaded.
- [ ] `tests/integration/test_session_finalization_decoupled.py` — UPDATE: assert
  the extraction stub is called with the new `turn_count` argument (or that the
  call remains compatible).
- [ ] New test file `tests/unit/test_memory_decay_prune.py` (create if absent;
  otherwise extend the existing decay-prune test) — REPLACE/ADD: cover tier-2
  candidate selection, the non-overlap with tier-1, union dedup by `memory_id`,
  the dedicated `MEMORY_NOISE_PRUNE_APPLY` gate (dry-run vs apply), dry-run
  reporting, the 14-day boundary, the `confidence ≈ 0.5` epsilon, and the shared
  `MAX_PRUNE_PER_RUN` cap across the deduped union.

## Rabbit Holes

- **Building the LLM-refusal complement (Fix 1).** Cut from this PR entirely and
  filed as a follow-up issue. Ship the 7 pattern appends only — they satisfy every
  success criterion. Do not add an env flag, an extra Haiku call, or a classifier.
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
**Data prerequisite:** the real turn count must be captured at schedule time
(before teardown clears `sdk_client`'s tracker), not read off the stale in-scope
`session.turn_count`.
**State prerequisite:** the value is captured as a plain int argument at schedule
time (not re-read from a mutable global inside the task), so there is no shared
mutable state to race on.
**Mitigation:** Capture `turn_count` via `sdk_client.get_turn_count(session_id)`
(or a fresh Popoto re-fetch) at schedule time and pass it by value into the
asyncio task closure (same pattern as `response_text` today). Do NOT call
`sdk_client.get_turn_count()` inside the task — it may be cleared (spike-1
caveat). This makes the read race-free.

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
  filters: extended refusal vocabulary, the CLI-origin single-turn gate
  (`turn_count <= 1` paired with session origin), the scoping-boilerplate content
  filter, and the `memory_decay_prune` second tier (with its dedicated
  `MEMORY_NOISE_PRUNE_APPLY` dry-run gate and 14-day / confidence-baseline
  criteria). Note the LLM-refusal complement is tracked as a separate follow-up.
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
- [ ] A CLI-origin single-turn session (`turn_count == 1`, e.g. `/update`)
  produces zero Memory records via post-session extraction (and makes no Haiku
  call); a substantive single-turn **Telegram** correction STILL extracts.
- [ ] A session with `turn_count == None` or `>= 2` still extracts normally.
- [ ] No observation containing `"sdlc-local-"` or `"scoped to isolated session"`
  is persisted; legitimate observations mentioning sessions/scope are NOT dropped.
- [ ] `memory_decay_prune` reports (dry-run) tier-2 candidates matching
  `WF_MIN_THRESHOLD <= importance <= 1.0`, `access_count == 0`, `confidence ≈
  0.5`, `age > 14 days`, deletes them only when `MEMORY_NOISE_PRUNE_APPLY` is set,
  and the tier-1/tier-2 union is deduped before the `MAX_PRUNE_PER_RUN` cap.
- [ ] **Outcome check:** a recall query (or tier-2 dry-run candidate count against
  the known noise corpus) confirms a known noise record (e.g. `b0b24ef7`) no
  longer surfaces / is selected for pruning.
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
  - Role: Implement Fix 1 (7 pattern appends only — no LLM complement), Fix 2
    (origin-paired turn_count gate + signature threading), Fix 3 (scoping filter)
    in `agent/memory_extraction.py` and `agent/session_executor.py`.
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
- LLM refusal-complement is OUT OF SCOPE (follow-up #1829) — do NOT build it here.

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
- Add `NOISE_PRUNE_AGE_DAYS = 14` (provisional/tunable comment) and a non-overlapping tier-2 predicate (`WF_MIN_THRESHOLD <= importance <= 1.0`).
- Add a dedicated default-off `MEMORY_NOISE_PRUNE_APPLY` gate (distinct from `MEMORY_DECAY_PRUNE_APPLY`); dedupe the tier-1/tier-2 union by `memory_id` before the shared `MAX_PRUNE_PER_RUN` cap; update module docstring.

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
| Tier-2 dedicated gate present | `grep -c "MEMORY_NOISE_PRUNE_APPLY" reflections/memory/memory_decay_prune.py` | output > 0 |
| Decay apply-mode NOT defaulted on (anti-criterion) | `grep -n 'MEMORY_DECAY_PRUNE_APPLY.*=.*"true"' reflections/memory/memory_decay_prune.py` | match count == 0 |
| Noise apply-mode NOT defaulted on (anti-criterion) | `grep -n 'MEMORY_NOISE_PRUNE_APPLY.*=.*"true"' reflections/memory/memory_decay_prune.py` | match count == 0 |

## Critique Results

Three critique rounds (FULL, 3 lenses) converged. All findings resolved in-plan;
plan FROZEN for BUILD on 2026-06-30.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Correctness | Fix 2 read stale in-memory `session.turn_count` (different instance than sdk_client persists; stays 0) → gate would kill ALL extraction | Capture real turn count at schedule time via `sdk_client.get_turn_count(session_id)` / fresh Popoto re-fetch, pass by value | Technical Approach Fix 2, spike-1 impact, Race 1 |
| BLOCKER | Correctness | Fix 4 tier-1 ⊂ tier-2 → double-count vs cap + duplicate deletes | Non-overlapping tiers (`importance >= WF_MIN_THRESHOLD` lower bound) + dedupe union by `memory_id` | Technical Approach Fix 4 |
| CONCERN | Safety | Fix 4 shared `MEMORY_DECAY_PRUNE_APPLY` gate prevents validating tier-2 independently | Dedicated default-off `MEMORY_NOISE_PRUNE_APPLY` gate for tier-2 | Technical Approach Fix 4, Open Q3 |
| CONCERN | Correctness | Blanket `turn_count <= 1` skip discards high-value single-turn conversational corrections | Pair turn-count with session origin/type; only skip CLI-origin single-turn | Technical Approach Fix 2, Success Criteria |
| CONCERN | Safety | Fix 3 `_SCOPING_MARKERS` included unconfirmed substrings (`"cross-session"`, `"scope boundary"`) → silent false-positive drops | Dropped to only evidenced markers + narrowness regression test | Technical Approach Fix 3 |
| SCOPE | Simplicity | Fix 1 optional LLM refusal-complement is gold-plating (env flag, API key, extra Haiku call) | Cut from PR; filed as follow-up; ship 7 pattern appends only | Key Elements / Step 1 / Prerequisites |
| NIT | Coverage | No outcome-level assertion that known noise stops surfacing | Added outcome-check success criterion (recall / dry-run candidate count) | Success Criteria |
| NIT | History | Step 1 carried stale "(If PM approves)" hedge | Removed; Q1 resolved | Step 1 |

---

## Open Questions

**All three resolved 2026-06-30 with the recommended defaults — no human blocker remains.**

1. **Fix 1 LLM-refusal complement — build it or pattern-extension only?** The
   issue suggests it ("Consider also adding an LLM-based refusal detector"). It
   costs one extra Haiku call per non-empty extraction and adds latency to a
   background path. **RESOLVED:** ship pattern-extension now (zero-risk); add the
   LLM complement behind a **default-off** env flag as an opt-in.
2. **Fix 2 gate threshold — `turn_count <= 1` only, or also a length floor?**
   Recon says key on turn count, not length. **RESOLVED:** turn count only
   (length is already covered by the 50-char guard); no separate length
   threshold.
3. **Fix 4 — separate env gate or reuse `MEMORY_DECAY_PRUNE_APPLY`?** Reusing the
   existing gate means enabling apply-mode enables BOTH tiers at once.
   **RESOLVED (revised by critique):** give tier-2 its OWN dedicated default-off
   gate `MEMORY_NOISE_PRUNE_APPLY` (distinct from `MEMORY_DECAY_PRUNE_APPLY`) so
   the broader tier-2 predicate can be validated in dry-run independently before
   any deletion is enabled.
