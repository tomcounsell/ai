---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2040
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-13T07:13:08Z
---

# Memory Extraction Per-Session Cumulative Cap

## Problem

The daily memory-quality audit (`reflections/memory/memory_quality_audit.py`) has auto-filed the **exact same anomaly five times** for the same session: #1497 (24 junk), #1786 (21), #1931 (21), #2016 (22), and now #2040 (21), all for `agent_id=extraction-local-ebe79d3b-633c-4f32-b1e6-f64f340d769d`. Each run, Layer 1 supersedes 20+ refusal/shrapnel records from that one session_id, tripping the `agent-id-cluster` signal (threshold > 10).

**Current behavior:**
- A single Haiku extraction response is capped at 10 saved records (`extract_observations_async`, `parsed[:10]`). But **nothing bounds the cumulative number of extraction records a single `session_id` can produce across repeated extraction calls** (resumes, long multi-turn local sessions, or a stuck/looping session). Because `agent_id = f"extraction-{session_id}"`, every re-run of a session accretes more records under the same agent_id.
- When a batch of that junk is superseded in one audit run, the retrospective `agent-id-cluster` alert fires and files a GitHub issue — again and again for the same cluster.

**Desired outcome:**
- A single session_id cannot accumulate more than the audit's own cluster threshold (`AGENT_ID_CLUSTER_THRESHOLD = 10`) worth of *non-superseded* extraction records at any instant. A content-agnostic per-session cumulative cap acts as a structural circuit-breaker that complements the existing (finite-vocabulary) refusal filters. This is deliberately a **signal-suppression** goal, not merely volume-bounding: because the cap is set **≤ the audit threshold**, the number of records that can be superseded from one agent_id in any single audit run is ≤ 10, so `count > 10` is never satisfied and the retrospective `agent-id-cluster` GitHub issue can never re-fire for a cap-bounded session (looping, resumed, or long).

**Why the cap must be ≤ the audit threshold (the load-bearing invariant):**
- The audit's `_layer2_signals` fires when **more than `AGENT_ID_CLUSTER_THRESHOLD` (10)** records from one `agent_id` are superseded *in a single run* (`reflections/memory/memory_quality_audit.py:352`, `count > AGENT_ID_CLUSTER_THRESHOLD`).
- The cap bounds *non-superseded* records for one agent_id to `≤ cap` at every instant. Layer 1 can only supersede records that are currently non-superseded, so at most `cap` records from that agent_id can be superseded in any one run.
- Therefore `cap ≤ 10` ⇒ superseded-this-run ≤ 10 ⇒ `count > 10` is always False ⇒ **the signal cannot fire**. A cap of 15 (the prior draft's value) would allow 15 > 10 and re-fire the identical alert — and since only non-superseded records count toward the cap, the quota resets after each daily audit supersede, so the alert could recur *daily*. The default is therefore **10**, equal to the threshold, with a test asserting the invariant `default ≤ AGENT_ID_CLUSTER_THRESHOLD` so a future bump can't silently re-open the signal.

## Freshness Check

**Baseline commit:** `42c4dfe90c1e2796332b8d55e0c2cb7102df4dc2`
**Issue filed at:** 2026-07-12T04:03:06Z
**Disposition:** Unchanged (claims re-verified against current main; the underlying gap is still present)

**File:line references re-verified:**
- `agent/memory_extraction.py:666` — save loop `for obs_content, importance, metadata in parsed[:10]:` — confirmed present; the `[:10]` per-call cap is the *only* quantitative bound and it is per-response, not per-session.
- `agent/memory_extraction.py:667-674` — `Memory.safe_save(agent_id=f"extraction-{session_id}", ...)` — confirmed; agent_id is derived from session_id, so all records from one session share one agent_id.
- `reflections/memory/memory_quality_audit.py:340-369` — `_layer2_signals` agent-id-cluster block — confirmed; fires on `count > AGENT_ID_CLUSTER_THRESHOLD (10)` records superseded *this run* from one agent_id.
- `reflections/memory/memory_quality_audit.py:196-249` — `_layer1_supersede` uses `_looks_like_refusal(m.content)` — confirmed; the same predicate the write path uses, so caught records would have been blocked at write time had the current code been live.
- `models/memory.py:133` — `agent_id = KeyField()` — confirmed indexed, so a per-session count query is O(index lookup), not a full scan.

**Cited sibling issues/PRs re-checked:**
- #2016 — CLOSED 2026-07-11T09:27Z; merged as `a214847f` (Fix A per-record refusal filter + Fix B 14-day dup-suppression). Root cause it targeted (write-path per-record junk + open-only dup-check) is genuinely addressed in current code.
- #1931 / #1786 / #1497 — all CLOSED; all the identical cluster. Confirms chronic recurrence.
- #1829 — merged `f707f7e6` (2026-07-12 00:08+0700); added a default-OFF LLM refusal complement. Not enabled, so not a live defense.

**Commits on main since issue was filed (touching referenced files):**
- `443b5642` Standardize non-harness LLM calls on a PydanticAI wrapper (#1925) — refactored the call mechanism (`_llm_call` → `run_typed`); did not change the save cap or agent_id derivation. Irrelevant to root cause.
- `e1ec8695` Centralize magic timeout/retry/TTL literals (#1968) — moved timeout constants to settings; irrelevant to root cause.

**Active plans in `docs/plans/` overlapping this area:** none (no open plan touches `memory_extraction.py` or the audit).

**Notes:** All 21 sample records are absent from the live corpus (superseded then pruned/absent), so recon leaned on code + the audit mechanism + the five-filing history rather than record inspection. This does not change the premise: the structural gap (no per-session cap) is present in current code.

## Prior Art

- **Issue/PR #2016 (`a214847f`)**: *Memory extraction: fix recurring junk-cluster re-filing.* Added Fix A (per-record `_looks_like_refusal` filter inside the JSON-parse branch of `_parse_categorized_observations`) and Fix B (14-day `CLUSTER_REFILE_SUPPRESSION_DAYS` so a closed cluster issue suppresses re-filing). Directly targets this exact cluster. **Outcome: correct but insufficient** — it closes the *known-shape* write hole and the *open-only* dup-check, but does not bound *cumulative per-session* output, and was re-filed 1 day later (deploy lag).
- **Issue/PR #1822 (`0f68f09e`)**: closed three systematic noise sources (scoping-boilerplate filter, trivial-session gate, GC tier). Reduced noise volume but is content-pattern based.
- **Issue/PR #1829 (`f707f7e6`)**: LLM-based refusal-detector complement, default-OFF. An attempt to escape the finite-vocabulary trap; not enabled in production.
- **Issue/PR #1212 (`951fea79`)**: original JSON-shrapnel + refusal-prose hardening. Established `_REFUSAL_PATTERNS` and the tolerant JSON parser.
- **Issue #1231 (`31f5c477`)**: introduced the 3-layer memory audit that files these cluster issues.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1212 | `_REFUSAL_PATTERNS` closed-vocab + tolerant JSON parse | Finite vocabulary — any novel refusal phrasing escapes. |
| #1822 | Scoping/trivial-session filters | Still content-pattern based; does not bound per-session volume. |
| #1829 | LLM refusal complement | Default-OFF; not a live defense. |
| #2016 (Fix A) | Per-record `_looks_like_refusal` in JSON branch | Closes *known-shape* junk at write time, but a novel shape from a looping session still accretes. |
| #2016 (Fix B) | 14-day dup-suppression | Correct, but re-file happened inside the window → **deploy lag**: audit ran pre-#2016 code ~18.5h after merge. |

**Root cause pattern:** Every prior fix attacks the *textual shape* of junk (a finite, ever-growing vocabulary) or the *symptom* (dup-filing). None bound the *structural* quantity: a single looping/resumed `session_id` can call extraction arbitrarily many times, each adding up to 10 records under one agent_id. The audit threshold (10) is exactly the per-call cap, so it takes only 2+ calls to trip. A content-agnostic per-session cap is the missing structural backstop.

## Data Flow

1. **Entry point**: A session completes → `agent/session_executor.py:333` schedules `run_post_session_extraction(session_id, response_text, ...)` (with an in-flight guard against *concurrent* duplicate extraction for the same session_id).
2. **`extract_observations_async`** (`agent/memory_extraction.py:502`): pre-LLM guards → Haiku call → post-LLM refusal filter → `_parse_categorized_observations` → save loop `parsed[:10]` → `Memory.safe_save(agent_id=f"extraction-{session_id}", ...)`.
3. **Storage**: each observation persisted as a `Memory` with `agent_id="extraction-{session_id}"`. Repeated calls for the same session_id (resume, next turn, or loop) each add up to 10 → cumulative growth under one agent_id.
4. **Audit (daily)**: `_layer1_supersede` marks refusal-matching records `superseded_by="cleanup-junk-extraction"`; `_layer2_signals` counts per-agent_id supersedes this run; >10 → files `[memory-audit] agent-id-cluster-...` issue.

The in-flight guard in step 1 stops *concurrent* duplicate runs but not *sequential* re-runs over the session's life — the gap this plan closes at step 2.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (internal extractor + Redis/Popoto only).

## Solution

**Decision (resolves prior Open Question #2 — ship vs. close):** **Ship the durable structural backstop.** The recurrence is partly deploy lag against an already-correct #2016, but the issue explicitly asked for a backstop *independent of deploy state*, and the finite refusal vocabulary means the content-pattern filters will always have escape hatches. A content-agnostic per-session cap is the one structural bound none of the prior fixes provide. It is small, and it closes the recurrence *class*, not just this instance. This is no longer an open question.

### Key Elements

- **Per-session cumulative cap**: Before extraction saves, count existing non-superseded `Memory` records for `agent_id="extraction-{session_id}"`. If already at/above the cap, skip the save (and skip the Haiku call to save cost).
- **Env-overridable, provisional constant**: `MEMORY_EXTRACTION_SESSION_CAP` (**default 10 — equal to and never above `AGENT_ID_CLUSTER_THRESHOLD`**, so the audit signal can never fire for a bounded session; see the load-bearing invariant in Problem) sourced from `config/settings.py` per repo convention, marked as a tunable grain-of-salt value. `0` disables the cap (matching the audit's `MEMORY_AUDIT_LAYER1_CAP` "0 → no cap" convention).
- **Observability**: emit a low-cardinality `memory.extraction.session_cap_hit` counter metric AND a `logger.info` line that **carries `session_id`, the current `count`, and the `cap`**, so one runaway session is distinguishable from many and the condition is visible instead of silent. (`session_id` is deliberately kept out of the metric labels — high-cardinality label — and lives only in the log line.)

### Flow

Session completes → `run_post_session_extraction` → `extract_observations_async` → **count existing non-superseded `extraction-{session_id}` records** → if `cap > 0` and `count >= cap`: log (`session_id`/`count`/`cap`) + metric + return `[]` (no Haiku call, no save) → else: proceed with existing extract/parse/save path (still `parsed[:10]` per call).

### Technical Approach

- Add the cap check inside `extract_observations_async`, placed **after** the cheap pre-LLM guards (50-char, pre-refusal, whitespace) and **before** the existing outer `try:` (currently `agent/memory_extraction.py:566`), so a saturated session short-circuits without incurring Haiku cost. **The cap block gets its own dedicated `try/except` and its own `from models.memory import Memory` import** (the existing `Memory` import lives *inside* the outer try at line ~651). Placing the block and its try/except *before* the outer `try:` means the fail-open contract does not depend on the broad exception handler — a bug in the cap logic cannot be silently swallowed by, or entangled with, the LLM-path handler.
- **Non-superseded count with a cheap fast-path (efficiency — resolves NIT 5a):** `superseded_by` is a `StringField`, not an indexed `KeyField` (`models/memory.py:143`), so it cannot be a `.filter()` kwarg; a non-superseded count requires either object materialization or a two-step read. To avoid materializing full `Memory` objects on every turn:
  1. `total = Memory.query.count(agent_id=f"extraction-{session_id}")` — Popoto `count()` avoids object instantiation (`popoto/models/query.py:2513`). Since non-superseded ≤ total, **if `total < cap` we proceed immediately without materializing anything** (the common case — most sessions are far below the cap).
  2. Only when `total >= cap` do we materialize `Memory.query.filter(agent_id=...).all()` and count the non-superseded subset (`not (m.superseded_by or "")`) to make the final decision. Only non-superseded records count so a session whose refusal junk was already cleaned by Layer 1 isn't permanently locked out.
  3. **Bounded in-process memo:** maintain a small module-level dict `{session_id: capped_until_ts}` (bounded size, oldest-evicted) with a short TTL. A session confirmed at/over the cap short-circuits subsequent same-process calls without re-querying — important for a looping session that fires extraction many times in quick succession. The TTL (provisional, env-tunable) is short enough that the daily Layer 1 supersede eventually un-sticks a session whose junk was cleaned (the memo entry expires and the next call re-queries).
  Wrap the entire block in one `try/except` → on any failure, **fail-open** (proceed with extraction) to preserve the module's "never crash the agent" invariant.
- Add `MEMORY_EXTRACTION_SESSION_CAP: int` (default **10**) to the appropriate settings group in `config/settings.py` with a comment marking it provisional/tunable AND stating the load-bearing invariant "must stay ≤ `AGENT_ID_CLUSTER_THRESHOLD` or the audit signal re-opens". Overridable via env, read at call time (not module-capture) so tests can monkeypatch.
- Emit the counter metric through the existing `analytics.collector.record_metric` best-effort pattern already used in this module (`memory.extraction.error`, `memory.extraction`). The `logger.info` line carries `session_id`, `count`, and `cap` (NIT/CONCERN 4).
- Do **not** touch the audit thresholds, the refusal vocabulary, or Fix A/Fix B — those are orthogonal and already correct. This plan adds one content-agnostic structural bound.

### Self-healing scope (correcting the prior draft's overclaim — resolves CONCERN 3)

The "self-healing" property is **real but partial, and only for refusal-shaped records.** Layer 1 supersedes records matching `_looks_like_refusal`; once superseded, they drop out of the non-superseded count, freeing the session to extract again. This covers the actual failure mode (a looping/stuck session emitting refusal/shrapnel junk), which is exactly what the five re-filings were.

It does **not** heal a genuinely verbose *non-refusal* session: real observations are never superseded by the audit, so a single session producing more than `cap` legitimate, unique observations across its life is permanently capped at `cap` lifetime non-superseded extraction records. **We accept this limitation deliberately:**
- **A time-window escape hatch is rejected because it reintroduces Blocker #1.** If the cap counted only records within a recent window, aged-out refusal records would stop counting toward the cap while remaining non-superseded until the next daily audit — letting a looping session accumulate well past 10 non-superseded records between audit runs, all of which then get superseded in one run → `count > 10` → the signal fires again. The signal-suppression guarantee requires counting *all* non-superseded records, with no time window.
- The limitation is benign in practice: a single `session_id` emitting >10 genuinely unique high-value observations is rare; `memory-dedup` already collapses the near-duplicate re-extractions that resumed sessions produce; and a cumulative non-superseded count that high from one session is itself the anomaly shape the audit exists to flag. The cap is env-tunable (raise it, accepting a proportionally higher audit threshold, if production ever shows real loss). Appetite is Small — a per-record provenance/time-window scheme would be over-engineering for a case we have never observed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new cap-count block wraps the count/fast-path/materialize logic in its **own dedicated try/except placed before the existing outer `try:`** (not nested inside it), and fails open (proceeds with extraction). Add a test asserting that a raising `Memory.query` does NOT block extraction (fail-open) and logs at debug/info.
- [ ] Existing `except Exception` blocks in `extract_observations_async` are unchanged; no new silent swallow is introduced, and the cap block's fail-open does not rely on the broad LLM-path handler.

### Empty/Invalid Input Handling
- [ ] `session_id` empty/None: the cap query builds `agent_id="extraction-"` (or `extraction-None`); assert the cap logic does not crash and behaves as no-op (count 0 → proceed). Covered by an explicit test.
- [ ] Cap value of 0 or negative via env: define behavior (0 = disabled / no cap, matching the audit's `MEMORY_AUDIT_LAYER1_CAP` convention) and test both boundaries.

### Error State Rendering
- [ ] No user-visible surface — this is a bridge-internal extractor path. When the cap trips, the observable outputs are a `logger.info` line (carrying `session_id`, `count`, and `cap`) and a `memory.extraction.session_cap_hit` metric; assert both fire, and assert the log line contains the session_id, the count, and the cap value (so one runaway session is distinguishable from many — CONCERN 4).

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction` — UPDATE: add cases:
  - `test_session_cap_blocks_after_threshold` (session at cap → returns `[]`, no Haiku call)
  - `test_session_cap_allows_below_threshold` (below cap → normal extract)
  - `test_session_cap_ignores_superseded` (superseded records don't count toward cap)
  - `test_session_cap_fail_open_on_query_error` (query raises → extraction proceeds)
  - `test_session_cap_disabled_when_zero`
  - `test_session_cap_hit_log_carries_session_id_count_cap` (CONCERN 4 — assert the `logger.info` line, via `caplog`, contains the session_id, the count, and the cap value; and that the `memory.extraction.session_cap_hit` metric fires)
  - `test_session_cap_default_le_audit_threshold` (Blocker 1 invariant — assert the settings default `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD` so a future bump can't silently re-open the audit signal)
- [ ] **Audit-level signal-suppression test (Blocker 1 — the missing coverage the critique flagged).** Add `tests/unit/test_memory_quality_audit.py::test_capped_session_does_not_fire_agent_id_cluster` (or a new module if cleaner): seed exactly `cap` (10) non-superseded refusal-shaped `extraction-{sid}` records for one agent_id, run `_layer1_supersede` then feed its `just_superseded_ids`/`agent_ids` into `_layer2_signals`, and assert **no `agent-id-cluster` candidate is produced** (10 is not `> 10`). Add the paired control `test_uncapped_session_would_fire_agent_id_cluster`: seed 11 records, run the same path, assert the candidate **does** appear — proving it is the cap value (≤ threshold), not the test harness, that suppresses the signal. This is the end-to-end proof that the audit signal actually stops, closing the gap that all 9 prior unit/grep criteria left open.
- [ ] No existing test asserts an absence of a per-session cap, so no existing case needs DELETE/REPLACE — the change is additive. Existing extraction tests that don't pre-seed `extraction-{session_id}` records stay green because an empty corpus yields count 0 (below any positive cap). Verify this assumption holds for tests that call `extract_observations_async` with a live/mocked `Memory` (the mocked-save tests must not accidentally report a high count).

## Rabbit Holes

- **Rewriting the refusal vocabulary or enabling the #1829 LLM complement.** Out of scope — this plan is deliberately content-agnostic; the finite-vocabulary problem is a separate, already-tracked concern.
- **Changing the audit's cluster threshold or dedup logic.** Fix A/Fix B are correct; touching them re-opens litigated ground.
- **Cross-session global rate limiting or a new Popoto model to track per-session counts.** The indexed `agent_id` query is sufficient; do not build new state.
- **Chasing the exact deploy-lag timeline of the audit machine.** The logical proof (re-file inside the suppression window ⇒ stale code) is enough; forensic log-diving is disproportionate.

## Risks

### Risk 1: Cap drops legitimate observations from a genuinely productive long session
**Impact:** A high-value, many-turn session that legitimately produces >cap real (non-refusal) observations would have later observations skipped, and — because non-refusal records are never superseded by the audit — would stay locked at `cap` lifetime non-superseded extraction records (see "Self-healing scope" above; this is the accepted limitation of CONCERN 3, not a self-healing case).
**Mitigation:** The default is 10, tied to the audit threshold rather than set "generously" — a lower cap is the price of the signal-suppression guarantee (a cap >10 re-opens the audit alert, Blocker #1). Repeated extraction on a resumed session largely re-extracts overlapping content (near-dupes that memory-dedup already collapses), so real unique-observation loss is minimal. The value is env-tunable and marked provisional; raise it (accepting a proportionally higher audit `AGENT_ID_CLUSTER_THRESHOLD`, which must move together) if production ever shows real loss. A cumulative non-superseded count this high from one session is itself the anomaly shape the audit exists to flag.

### Risk 2: Per-extraction count query adds latency/load
**Impact:** One extra indexed Popoto query per extraction call.
**Mitigation:** `agent_id` is a `KeyField` (indexed), so the lookup is cheap; extraction is already an async background task off the hot path. Fail-open on query error means it can never block or crash extraction.

## Race Conditions

### Race 1: Concurrent extraction for the same session_id undercounts the cap
**Location:** `agent/memory_extraction.py` (new cap block) vs. `agent/session_executor.py:325` in-flight guard.
**Trigger:** Two extraction runs for the same session_id race; both read count < cap before either saves.
**Data prerequisite:** The count query must reflect prior saves before a dependent run reads it.
**State prerequisite:** At most one extraction per session_id in flight.
**Mitigation:** `session_executor` already holds an in-flight guard that skips duplicate concurrent extraction for a session_id (observed at `agent/session_executor.py:325`), so concurrent double-count is already prevented upstream. The cap is a coarse structural backstop (tolerance of ±10 is acceptable), not an exact quota, so a rare transient over-count by one batch is harmless and self-heals on the next audit supersede. No new lock needed.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1829] Enabling or improving the LLM-based refusal-detector complement — already tracked; this plan stays content-agnostic and does not depend on it.
- Nothing else deferred — the per-session cap, its config knob, its metric, and its tests are all in scope for this plan. The deploy-lag recurrence self-resolves once current main is deployed (the 14-day suppression from #2016 is already in the deployed-forward code); no code action remains for it.

## Update System

No update system changes required — this is a purely internal extractor change. `config/settings.py` gains one optional field with a default, propagated by the existing settings/`.env.example` convention (add a placeholder line to `.env.example` with the required comment). No new dependency, no migration, no `scripts/update/run.py` change. No Popoto schema change (no new/changed model field), so no `migrations.py` entry.

## Agent Integration

No agent integration required — this is a bridge-internal change to the post-session extraction path. No new CLI entry point, no MCP surface, no `.mcp.json` change, and the bridge already invokes the extractor via `agent/session_executor.py` → `run_post_session_extraction`. The behavior change (cap) is transparent to all callers.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a short subsection under the extraction description documenting the per-session cumulative cap, its env knob `MEMORY_EXTRACTION_SESSION_CAP`, and how it complements the refusal filters and audit.
- [ ] If a config-field catalog exists (`docs/features/config-timeout-catalog.md` is timeouts-only), note the new knob wherever memory-extraction tunables are documented; otherwise the feature doc above is the canonical reference.

### Inline Documentation
- [ ] Comment on the cap block explaining: the retrospective-audit rationale, the `cap ≤ AGENT_ID_CLUSTER_THRESHOLD` invariant (why the default is 10), the fail-open contract and why the block precedes the outer `try:`, the "non-superseded only" count and its *partial* self-healing (refusal-shape only; non-refusal sessions are permanently capped — accepted limitation), why a time-window escape hatch is rejected (reintroduces the audit signal), and the provisional/tunable nature of the default.
- [ ] Docstring update on `extract_observations_async` noting the per-session cap gate.

## Success Criteria

- [ ] **Audit signal actually stops (Blocker 1, the load-bearing criterion):** with the cap at its default (10, ≤ `AGENT_ID_CLUSTER_THRESHOLD`), a session seeded to exactly `cap` non-superseded refusal records, run through `_layer1_supersede` → `_layer2_signals`, produces **no `agent-id-cluster` candidate**; the paired 11-record control **does** produce one. Verified by the audit-level tests in Test Impact.
- [ ] **Invariant guard:** the settings default satisfies `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD`, asserted by a test so a future bump can't silently re-open the signal.
- [ ] `extract_observations_async` skips the Haiku call and returns `[]` when a session_id already has >= cap non-superseded `extraction-{session_id}` records.
- [ ] Below the cap, extraction behaves exactly as before (existing tests green); the `total < cap` fast-path does not materialize `Memory` objects.
- [ ] Superseded records do not count toward the cap (refusal-shape self-healing verified by test); the accepted non-refusal limitation is documented, not "healed".
- [ ] Query failure in the cap block fails open (extraction proceeds) — verified by test; the cap block's try/except is separate from and precedes the outer LLM-path `try:`.
- [ ] `MEMORY_EXTRACTION_SESSION_CAP` is env-overridable; `0` disables the cap; both boundaries tested.
- [ ] A `memory.extraction.session_cap_hit` metric fires and a `logger.info` line carrying `session_id`, `count`, and `cap` is emitted when the cap trips — verified by test (CONCERN 4).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep` confirms the cap constant is read from `config/settings.py` (not a bare literal in `memory_extraction.py`).

## Team Orchestration

### Team Members

- **Builder (extractor-cap)**
  - Name: extractor-cap-builder
  - Role: Implement the per-session cap in `extract_observations_async`, the `config/settings.py` field, the metric, and unit tests.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Validator (extractor-cap)**
  - Name: extractor-cap-validator
  - Role: Verify success criteria — cap blocks above threshold, allows below, ignores superseded, fails open, `0` disables; metric + log fire; constant sourced from settings.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add config knob
- **Task ID**: build-config
- **Depends On**: none
- Add `MEMORY_EXTRACTION_SESSION_CAP: int` (**default 10**) to the appropriate group in `config/settings.py`, env-overridable, with a grain-of-salt tunable comment that **states the invariant "must stay ≤ `AGENT_ID_CLUSTER_THRESHOLD` or the audit signal re-opens (Blocker 1)"**. Add a placeholder + comment line to `.env.example`.

### 2. Implement the per-session cap
- **Task ID**: build-cap
- **Depends On**: build-config
- In `agent/memory_extraction.py::extract_observations_async`, insert a **dedicated `try/except` block with its own `from models.memory import Memory` import, placed after the pre-LLM guards and BEFORE the existing outer `try:` (line ~566)** so fail-open does not depend on the broad handler. Logic: `total = Memory.query.count(agent_id=f"extraction-{session_id}")`; if `total < cap` proceed (no materialization); else materialize `.all()` and count non-superseded (`not (m.superseded_by or "")`); consult/update the bounded in-process capped-session memo (short TTL). If `cap > 0` and non-superseded `count >= cap`: emit `logger.info` **carrying `session_id`, `count`, `cap`**, emit `memory.extraction.session_cap_hit` counter metric, and `return []`. Any exception → fail open (proceed). Read the cap at call time. Update the docstring to note the per-session cap gate.

### 3. Unit tests
- **Task ID**: build-tests
- **Depends On**: build-cap
- Add the extractor-level `TestRunPostSessionExtraction` cases from Test Impact: blocks-above, allows-below, ignores-superseded, fail-open-on-query-error, disabled-when-zero, empty-session-id no-op, log-carries-session_id/count/cap, and default-≤-threshold invariant. **Add the audit-level signal-suppression tests** (`test_capped_session_does_not_fire_agent_id_cluster` at `cap`=10 and the 11-record control) proving the `agent-id-cluster` signal actually stops.

### 4. Validate
- **Task ID**: validate
- **Depends On**: build-tests
- Run the new tests + existing `tests/unit/test_memory_extraction.py`; confirm all Success Criteria; confirm constant is sourced from settings via grep.

### 5. Documentation
- **Task ID**: build-docs
- **Depends On**: validate
- Update `docs/features/subconscious-memory.md` and inline docs per the Documentation section.

## Resolved Decisions (formerly Open Questions)

1. **Default cap value — DECIDED: 10, tied to (never above) `AGENT_ID_CLUSTER_THRESHOLD`.** The prior draft's 15 defeated the plan's own goal (15 > 10 re-fires the audit alert, recurring daily as the non-superseded quota resets). The default is the audit threshold itself, with an invariant test asserting `default ≤ AGENT_ID_CLUSTER_THRESHOLD`. See the load-bearing invariant in Problem and the audit-level test in Test Impact. Env-tunable; raising it requires raising the audit threshold in lockstep.
2. **Disposition — DECIDED: ship the durable structural backstop.** Not closing as "known/deploy-lag". The issue explicitly asked for a backstop independent of deploy state, and the finite refusal vocabulary guarantees the content-pattern filters will always leak; only a content-agnostic per-session cap closes the recurrence *class*. See the Decision note at the top of Solution.
