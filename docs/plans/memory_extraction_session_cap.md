---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2040
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-13T08:07:00Z
---

# Memory Extraction Per-Session Cumulative Cap

## Problem

The daily memory-quality audit (`reflections/memory/memory_quality_audit.py`) has auto-filed the **exact same anomaly five times** for the same session: #1497 (24 junk), #1786 (21), #1931 (21), #2016 (22), and now #2040 (21), all for `agent_id=extraction-local-ebe79d3b-633c-4f32-b1e6-f64f340d769d`. Each run, Layer 1 supersedes 20+ refusal/shrapnel records from that one session_id, tripping the `agent-id-cluster` signal (threshold > 10).

**Current behavior:**
- A single Haiku extraction response is capped at 10 saved records (`extract_observations_async`, `parsed[:10]`). But **nothing bounds the cumulative number of extraction records a single `session_id` can produce across repeated extraction calls** (resumes, long multi-turn local sessions, or a stuck/looping session). Because `agent_id = f"extraction-{session_id}"`, every re-run of a session accretes more records under the same agent_id.
- When a batch of that junk is superseded in one audit run, the retrospective `agent-id-cluster` alert fires and files a GitHub issue — again and again for the same cluster.

**Desired outcome:**
- A single session_id cannot accumulate 20+ extraction records regardless of the junk's textual shape. A content-agnostic per-session cumulative cap acts as a structural circuit-breaker that complements the existing (finite-vocabulary) refusal filters, so the `agent-id-cluster` signal stops recurring for looping/resumed sessions. The cap holds non-superseded records for one agent_id at **≤ cap at every instant** — an exact bound, not an approximate one (see the invariant proof under Solution).

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

### Key Elements

- **Per-session cumulative cap (two coordinated bounds).** The cap is enforced at *two* points that share one `current_count` reading, so the invariant "non-superseded `extraction-{session_id}` records ≤ cap **at every instant**" holds literally — a single pre-LLM count check is NOT enough because of check-then-batch arithmetic (see the invariant proof below):
  1. **Pre-LLM short-circuit** — before extraction, count existing non-superseded `Memory` records for `agent_id="extraction-{session_id}"`. If already at/above the cap, skip the Haiku call *and* the save (cost + volume bound).
  2. **Per-batch clamp** — when a call *does* proceed, clamp the save slice to `min(per_call_cap, cap − current_count)` records (never fewer than 0). This is the load-bearing part: without it, a call that passes the pre-LLM check at `count = cap−1` would still save up to `per_call_cap` (10) records, pushing non-superseded to `cap−1 + 10` — a true ceiling of `cap + (per_call_cap − 1)`, NOT `cap`. That overshoot is exactly what trips the audit's `agent-id-cluster` signal (threshold 10) the cap exists to prevent.
- **Env-overridable, provisional constant**: `MEMORY_EXTRACTION_SESSION_CAP` (default **10**, equal to and never above `AGENT_ID_CLUSTER_THRESHOLD` (10)) sourced from `config/settings.py` per repo convention, marked as a tunable grain-of-salt value. **Load-bearing invariant:** the cap must stay `<= AGENT_ID_CLUSTER_THRESHOLD` or the audit signal re-opens (Blocker 1) — a cap above the threshold lets a single session's non-superseded records exceed the count the audit flags, re-arming the exact `agent-id-cluster` recurrence this plan closes.
- **Observability**: emit a `memory.extraction.session_cap_hit` metric + `logger.info` when the cap trips — at the pre-LLM short-circuit *or* whenever the per-batch clamp reduces the slice below the number that would otherwise have been saved — so the condition is visible instead of silent.

### Why check-then-batch is insufficient (the invariant proof)

The existing save loop (`agent/memory_extraction.py:666`, `for ... in parsed[:10]:`) writes up to `per_call_cap = 10` records per call. A pre-LLM check of the form `if count >= cap: skip` runs **once, before** the batch. A call that passes at `count = cap−1` then saves up to 10 → non-superseded reaches `cap−1 + 10`. With `cap = 10` that is up to **19** non-superseded records for one agent_id; a single Layer-1 audit run supersedes all 19 → `count = 19 > AGENT_ID_CLUSTER_THRESHOLD (10)` → the agent-id-cluster signal **fires**, the precise outcome the cap was meant to prevent. Clamping the batch to `min(per_call_cap, cap − current_count)` closes this: after the clamp, `current_count + saved ≤ cap` always, so non-superseded never exceeds `cap` and the never-fire guarantee is **exact**, not approximate.

### Flow

Session completes → `run_post_session_extraction` → `extract_observations_async` → **count existing non-superseded `extraction-{session_id}` records** → if `count >= cap`: log + metric + return `[]` (no Haiku call, no save) → else: proceed with extract/parse, then **clamp the save slice to `parsed[:min(per_call_cap, cap − count)]`** before the save loop (if the clamp yields 0, log + metric + save nothing) → save the clamped slice.

### Technical Approach

- Add the cap enforcement inside `extract_observations_async` at **two** points that share one `current_count` reading:
  1. The pre-LLM short-circuit, placed **after** the cheap pre-LLM guards (50-char, pre-refusal, whitespace) and **before** the `_llm_call`, so a saturated session short-circuits without incurring Haiku cost.
  2. The per-batch clamp at the save loop (`parsed[:10]` at `agent/memory_extraction.py:666`): replace the hard `parsed[:10]` with `parsed[:save_limit]` where `save_limit = max(0, min(per_call_cap, cap − current_count))` (and when `cap <= 0` = disabled → keep the plain `parsed[:per_call_cap]`). This makes the "≤ cap at every instant" invariant literally true: `current_count + saved ≤ cap` after every call, so the never-fire guarantee is exact. `per_call_cap` is the existing `10`, kept as a named local (not re-litigated). When the clamp reduces the slice below what would otherwise be saved, emit the same `session_cap_hit` metric + `logger.info` so partial-clamp events are visible too.
- Count via `Memory.query.filter(agent_id=f"extraction-{session_id}")` (KeyField-indexed) then filter out `superseded_by`-set records in Python. Only non-superseded records count toward the cap so that a session whose junk was already cleaned isn't permanently locked out (self-healing after Layer 1 runs). Wrap the count in try/except → on any query failure, **fail-open** (proceed with extraction, un-clamped) to preserve the module's "never crash the agent" invariant.
- Add `MEMORY_EXTRACTION_SESSION_CAP: int` to the **`FeatureSettings`** group in `config/settings.py` (there is no memory-extraction-specific group today; `FeatureSettings` is the established home for optional-behaviour knobs and already hosts sibling feature flags such as `anthropic_concurrency`). Env-overridable via the `FEATURES__MEMORY_EXTRACTION_SESSION_CAP` nested-delimiter key. Add a comment marking it provisional/tunable. Read at call time (not module-capture) so tests can monkeypatch.
- Emit the metric through the existing `analytics.collector.record_metric` best-effort pattern already used in this module (`memory.extraction.error`, `memory.extraction`).
- Do **not** touch the audit thresholds, the refusal vocabulary, or Fix A/Fix B — those are orthogonal and already correct. This plan adds one content-agnostic structural bound.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new cap-count block wraps the Popoto query in try/except and fails open (proceeds with extraction). Add a test asserting that a raising `Memory.query` does NOT block extraction (fail-open) and logs at debug/info.
- [ ] Existing `except Exception` blocks in `extract_observations_async` are unchanged; no new silent swallow is introduced.

### Empty/Invalid Input Handling
- [ ] `session_id` empty/None: the cap query builds `agent_id="extraction-"` (or `extraction-None`); assert the cap logic does not crash and behaves as no-op (count 0 → proceed). Covered by an explicit test.
- [ ] Cap value of 0 or negative via env: define behavior (0 = disabled / no cap, matching the audit's `MEMORY_AUDIT_LAYER1_CAP` convention) and test both boundaries.

### Error State Rendering
- [ ] No user-visible surface — this is a bridge-internal extractor path. When the cap trips, the observable outputs are a `logger.info` line and a `memory.extraction.session_cap_hit` metric; assert both fire.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction` — UPDATE: add cases `test_session_cap_blocks_after_threshold` (session at cap → returns `[]`, no Haiku call), `test_session_cap_allows_below_threshold` (below cap → normal extract), `test_session_cap_ignores_superseded` (superseded records don't count toward cap), `test_session_cap_fail_open_on_query_error` (query raises → extraction proceeds), and `test_session_cap_disabled_when_zero`.
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction::test_session_cap_overshoot_batch_clamp` — ADD (the invariant regression test): seed `current_count = cap − 1` (i.e. 9 with default cap 10) non-superseded `extraction-{session_id}` records, run ONE extraction call whose parse yields ≥ `per_call_cap` (10) observations, and assert (a) non-superseded records for that agent_id end at **≤ cap** (the batch was clamped to a single save, not 10), and (b) feeding the resulting record set through the audit's `_layer2_signals` produces **no** agent-id-cluster candidate (post-supersede count ≤ AGENT_ID_CLUSTER_THRESHOLD). With `cap = 10` both assertions are co-satisfiable (clamp caps non-superseded at 10, and 10 is not `> AGENT_ID_CLUSTER_THRESHOLD`); with a cap above the threshold they are not — which is why the cap must stay `<= AGENT_ID_CLUSTER_THRESHOLD`. This is the case that a pre-LLM-check-only implementation fails.
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction::test_session_cap_control_signal_fires_at_11` — ADD (paired positive control, kept): a *genuine* 11-record accumulation for one agent_id (bypassing the clamp, e.g. via direct saves as a stuck legacy session would have produced) still trips `_layer2_signals` — proving the audit signal itself is intact and the clamp is what prevents the plan's own mechanism from over-producing. This test asserts the signal DOES fire for a real >10 cluster, bounding the clamp test above.
- [ ] `tests/unit/test_memory_extraction.py::test_session_cap_default_within_audit_threshold` — ADD (the invariant guard): assert the shipped settings default `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD` (both = 10). This is a static settings-vs-constant assertion that fails loudly if a future bump raises the cap above the audit threshold and silently re-arms the recurrence. Import `AGENT_ID_CLUSTER_THRESHOLD` from `reflections/memory/memory_quality_audit.py` and the cap default from `config/settings.py`.
- [ ] `tests/unit/test_memory_extraction.py::test_audit_signal_suppressed_at_cap` — ADD (audit-level signal-suppression, end-to-end through the real audit): seed exactly `cap` (10) non-superseded refusal-shaped `extraction-{session_id}` records, run the audit's real `_layer1_supersede` → `_layer2_signals`, and assert **no** agent-id-cluster candidate is produced (10 superseded is not `> AGENT_ID_CLUSTER_THRESHOLD`). Paired with `test_session_cap_control_signal_fires_at_11` (seed 11 → candidate IS produced) this brackets the threshold exactly: at the cap the signal is suppressed, one above it the signal fires — proving the `cap <= AGENT_ID_CLUSTER_THRESHOLD` invariant is what closes the recurrence.
- [ ] No existing test asserts an absence of a per-session cap, so no existing case needs DELETE/REPLACE — the change is additive. Existing extraction tests that don't pre-seed `extraction-{session_id}` records stay green because an empty corpus yields count 0 (below any positive cap). Verify this assumption holds for tests that call `extract_observations_async` with a live/mocked `Memory` (the mocked-save tests must not accidentally report a high count).

## Rabbit Holes

- **Rewriting the refusal vocabulary or enabling the #1829 LLM complement.** Out of scope — this plan is deliberately content-agnostic; the finite-vocabulary problem is a separate, already-tracked concern.
- **Changing the audit's cluster threshold or dedup logic.** Fix A/Fix B are correct; touching them re-opens litigated ground.
- **Cross-session global rate limiting or a new Popoto model to track per-session counts.** The indexed `agent_id` query is sufficient; do not build new state.
- **Chasing the exact deploy-lag timeline of the audit machine.** The logical proof (re-file inside the suppression window ⇒ stale code) is enough; forensic log-diving is disproportionate.

## Risks

### Risk 1: Cap drops legitimate observations from a genuinely productive long session
**Impact:** A high-value, many-turn session that legitimately produces >cap real observations would have later observations skipped.
**Mitigation:** The default is 10 (= `AGENT_ID_CLUSTER_THRESHOLD`; the cap cannot be set higher without re-arming the audit signal). Only *non-superseded* records count, and repeated extraction on a resumed session largely re-extracts overlapping content (near-dupes that memory-dedup already collapses), so real unique-observation loss is minimal even at 10. The value is env-tunable within the `<= threshold` invariant; a session accreting 10+ *distinct* non-superseded observations under one agent_id is itself the anomaly the audit already flags, so clamping there is the intended behavior, not a false positive.

### Risk 2: Per-extraction count query adds latency/load
**Impact:** One extra indexed Popoto query per extraction call.
**Mitigation:** `agent_id` is a `KeyField` (indexed), so the lookup is cheap; extraction is already an async background task off the hot path. Fail-open on query error means it can never block or crash extraction.

## Race Conditions

### Race 1: Concurrent extraction for the same session_id undercounts the cap
**Location:** `agent/memory_extraction.py` (new cap block) vs. `agent/session_executor.py:325` in-flight guard.
**Trigger:** Two extraction runs for the same session_id race; both read `current_count < cap` before either saves.
**Data prerequisite:** The count query must reflect prior saves before a dependent run reads it.
**State prerequisite:** At most one extraction per session_id in flight.
**Mitigation:** `session_executor` already holds an in-flight guard that skips duplicate *concurrent* extraction for a session_id (observed at `agent/session_executor.py:325`), so two extraction runs for the same session_id are already serialized upstream — the count each reads reflects the other's completed saves. Because extraction is serialized, the per-batch clamp (which enforces `current_count + saved ≤ cap` on every call) keeps non-superseded records at ≤ cap on every call, exactly. There is no accepted overshoot tolerance and no "transient over-count harmless" allowance: the clamp is the exact quota, and the upstream serialization guard is what makes a single `current_count` reading valid for the clamp arithmetic. No new lock needed. (Were the upstream guard ever removed, two truly-parallel calls could each clamp against a stale `current_count` and jointly overshoot by up to one batch — a separate hardening tracked by that guard's own invariant, not relaxed here.)

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1829] Enabling or improving the LLM-based refusal-detector complement — already tracked; this plan stays content-agnostic and does not depend on it.
- Nothing else deferred — the per-session cap, its config knob, its metric, and its tests are all in scope for this plan. The deploy-lag recurrence self-resolves once current main is deployed (the 14-day suppression from #2016 is already in the deployed-forward code); no code action remains for it.

## Update System

No update system changes required — this is a purely internal extractor change. `config/settings.py` gains one optional field with a default, propagated by the existing settings/`.env.example` convention (add a placeholder line to `.env.example` with the required comment). No new dependency, no migration, no `scripts/update/run.py` change. No Popoto schema change (no new/changed model field), so no `migrations.py` entry.

## Agent Integration

No agent integration required — this is a bridge-internal change to the post-session extraction path. No new CLI entry point, no MCP surface, no `.mcp.json` change, and the bridge already invokes the extractor via `agent/session_executor.py` → `run_post_session_extraction`. The behavior change (cap) is transparent to all callers.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a short subsection under the extraction description documenting the per-session cumulative cap, the two-point enforcement (pre-LLM short-circuit + per-batch clamp), its env knob `MEMORY_EXTRACTION_SESSION_CAP`, and how it complements the refusal filters and audit.
- [ ] If a config-field catalog exists (`docs/features/config-timeout-catalog.md` is timeouts-only), note the new knob wherever memory-extraction tunables are documented; otherwise the feature doc above is the canonical reference.

### Inline Documentation
- [ ] Comment on the cap block explaining the retrospective-audit rationale, the check-then-batch overshoot the clamp closes, the fail-open contract, the "non-superseded only" self-healing choice, and the provisional/tunable nature of the default.
- [ ] Docstring update on `extract_observations_async` noting the per-session cap gate and the per-batch clamp.

## Success Criteria

- [ ] `extract_observations_async` skips the Haiku call and returns `[]` when a session_id already has >= cap non-superseded `extraction-{session_id}` records.
- [ ] **Overshoot bound (the invariant):** starting from `current_count = cap − 1`, a single extraction call that parses ≥ `per_call_cap` observations saves only enough to reach `cap` (batch clamped), so non-superseded records for that agent_id end at **≤ cap** and the audit's `_layer2_signals` produces no agent-id-cluster candidate for it — verified by `test_session_cap_overshoot_batch_clamp`.
- [ ] Below the cap, extraction behaves exactly as before (existing tests green).
- [ ] Superseded records do not count toward the cap (self-healing verified by test).
- [ ] Query failure in the cap block fails open (extraction proceeds) — verified by test.
- [ ] `MEMORY_EXTRACTION_SESSION_CAP` is env-overridable; `0` disables the cap; both boundaries tested.
- [ ] A `memory.extraction.session_cap_hit` metric + `logger.info` fire when the cap trips (pre-LLM short-circuit or per-batch clamp).
- [ ] The audit's agent-id-cluster signal still fires for a genuine >10 cluster (positive control `test_session_cap_control_signal_fires_at_11` green) — the clamp bounds the plan's own output, it does not weaken the audit.
- [ ] **Cap-vs-threshold invariant:** the shipped settings default `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD` (both 10) — verified by `test_session_cap_default_within_audit_threshold`. Seeding exactly `cap` (10) non-superseded refusal records and running the real `_layer1_supersede` → `_layer2_signals` yields **no** agent-id-cluster candidate (`test_audit_signal_suppressed_at_cap`), bracketing the 11-record control above.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep` confirms the cap constant is read from `config/settings.py` (not a bare literal in `memory_extraction.py`).

## Team Orchestration

### Team Members

- **Builder (extractor-cap)**
  - Name: extractor-cap-builder
  - Role: Implement the per-session cap (pre-LLM short-circuit + per-batch clamp) in `extract_observations_async`, the `config/settings.py` `FeatureSettings` field, the metric, and unit tests.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Validator (extractor-cap)**
  - Name: extractor-cap-validator
  - Role: Verify success criteria — cap blocks above threshold, allows below, ignores superseded, fails open, `0` disables; overshoot bound holds (non-superseded ≤ cap after a cap−1 + 10 call, no cluster candidate); positive control still fires at 11; metric + log fire; constant sourced from settings.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add config knob
- **Task ID**: build-config
- **Depends On**: none
- Add `MEMORY_EXTRACTION_SESSION_CAP: int` (default 10) to the **`FeatureSettings`** group in `config/settings.py`, env-overridable via `FEATURES__MEMORY_EXTRACTION_SESSION_CAP`, with a grain-of-salt tunable comment. The default **must stay `<= AGENT_ID_CLUSTER_THRESHOLD`** (10) — the comment records this invariant so a future bump doesn't silently re-arm the audit signal. Add a placeholder + comment line to `.env.example`. Add an invariant-guard unit test asserting the shipped settings default `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD`.

### 2. Implement the per-session cap (two-point enforcement)
- **Task ID**: build-cap
- **Depends On**: build-config
- In `agent/memory_extraction.py::extract_observations_async`, read the cap at call time and compute `current_count` = non-superseded `extraction-{session_id}` records via the indexed `agent_id` query (try/except → fail open, un-clamped). **(a) Pre-LLM short-circuit:** after the pre-LLM guards and before `_llm_call`, if `cap > 0` and `current_count >= cap`, log `logger.info`, emit `memory.extraction.session_cap_hit`, and `return []`. **(b) Per-batch clamp:** replace the `parsed[:10]` save slice (`agent/memory_extraction.py:666`) with `parsed[:save_limit]` where `save_limit = max(0, min(per_call_cap, cap − current_count))` when `cap > 0` (else `parsed[:per_call_cap]`); when the clamp reduces the slice below the un-clamped count, emit the same metric + log. Update the docstring.

### 3. Unit tests
- **Task ID**: build-tests
- **Depends On**: build-cap
- Add the `TestRunPostSessionExtraction` cases from Test Impact: blocks-above, allows-below, ignores-superseded, fail-open-on-query-error, disabled-when-zero, empty-session-id no-op, **`test_session_cap_overshoot_batch_clamp`** (cap−1 seed + 10-parse → non-superseded ≤ cap AND no `_layer2_signals` cluster candidate), the **`test_session_cap_control_signal_fires_at_11`** positive control (genuine 11-record cluster still trips the audit), the **`test_session_cap_default_within_audit_threshold`** invariant guard (settings default `MEMORY_EXTRACTION_SESSION_CAP <= AGENT_ID_CLUSTER_THRESHOLD`), and the **`test_audit_signal_suppressed_at_cap`** audit-suppression test (seed exactly `cap` (10) non-superseded refusal records → real `_layer1_supersede` → `_layer2_signals` → no cluster candidate; the 11-record control above proves the signal WOULD fire one above the cap).

### 4. Validate
- **Task ID**: validate
- **Depends On**: build-tests
- Run the new tests + existing `tests/unit/test_memory_extraction.py`; confirm all Success Criteria including the overshoot bound and the positive control; confirm constant is sourced from settings via grep.

### 5. Documentation
- **Task ID**: build-docs
- **Depends On**: validate
- Update `docs/features/subconscious-memory.md` and inline docs per the Documentation section.

## Resolved Decisions

1. **Default cap value — RESOLVED: the default is 10, tied to `AGENT_ID_CLUSTER_THRESHOLD` (10).** The cap must stay `<= AGENT_ID_CLUSTER_THRESHOLD`, not above it. The earlier "`AGENT_ID_CLUSTER_THRESHOLD + N`" idea is wrong-signed: any cap above the threshold lets a single session accrue more non-superseded records than the audit flags, so a Layer-1 sweep supersedes `> AGENT_ID_CLUSTER_THRESHOLD` records in one run and the `agent-id-cluster` signal re-fires — the precise recurrence this plan exists to close. If the relationship is ever expressed as `AGENT_ID_CLUSTER_THRESHOLD + N`, then `N <= 0`; the shipped value is `N = 0` (cap == threshold == 10). Env-tunable regardless, but the `<= threshold` invariant is load-bearing and guarded by a unit test.

## Open Questions

1. **Disposition confirmation.** Recon shows the recurrence itself is deploy lag against an already-correct #2016 (Fix A + Fix B). This plan adds the *structural* per-session backstop the issue asked for. Do you want to ship the backstop, or would you rather close #2040 as "known/expected — resolved by deploying #2016" and file the per-session cap as a separate hardening? (Recommendation: ship the backstop — it's small and closes the recurrence class independent of deploy state and refusal-vocabulary gaps.)
