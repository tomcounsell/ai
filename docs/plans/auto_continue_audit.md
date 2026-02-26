---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-02-14
revised: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/99
---

# Auto-Continue Audit: Remaining Fixes

## Problem

The original issue (#99) identified 7 reliability gaps in the auto-continue system. After a burst of related PRs (Feb 24-26), **5 of 7 items are resolved**. A deep audit on Feb 27 surfaced 3 additional validation gaps. This revised plan covers **7 remaining items**.

### What's Already Fixed

| Item | Fixed by | Status |
|------|----------|--------|
| Orphaned index entries inflated queue depth | `_recover_orphaned_jobs()` at startup | ✅ Shipped |
| Completed-session guard | PR #194 — guard in `send_to_chat()` | ✅ Shipped |
| Duplicate delivery from catchup scanner | PR #194 — Redis dedup in catchup | ✅ Shipped |
| Stage-aware auto-continue (SDLC cap=10) | PR #185 — `effective_max` routing | ✅ Shipped |
| Error bypass guard (crash loop prevention) | PR #132 — `OutputType.ERROR` skips auto-continue | ✅ Shipped |
| Classifier path dedup (`_enqueue_continuation()`) | PR #195 (pending merge) | ✅ In PR |
| `effective_max` in log guards | PR #195 (pending merge) | ✅ In PR |

### What Remains

1. **Heuristic fallback defaults to STATUS_UPDATE** — When the LLM classifier fails, `_classify_with_heuristics` defaults to `STATUS_UPDATE` at 0.60 confidence. This silently auto-continues messages that may need human attention. The LLM path conservatively defaults to QUESTION; the heuristic path should match.

2. **No "approval gate" patterns in classifier** — Messages like "Ready to build when approved" and "Waiting for your go-ahead" are classified as STATUS_UPDATE or COMPLETION. They should be QUESTION — the agent is asking for permission.

3. **`_defer_reaction` is a fragile closure nonlocal** — Set in `send_to_chat()`, read in the outer `_execute_job()` scope. Multiple code paths set it. If an exception occurs between set and return, state becomes inconsistent. Should be explicit return value or state object.

4. **No classification audit log** — No structured observability for classification decisions. When misclassification occurs, there's no way to retroactively analyze accuracy or identify patterns.

5. **Heuristic confidence bypasses threshold** — The LLM path applies `CLASSIFICATION_CONFIDENCE_THRESHOLD` (0.80) and falls back to QUESTION when confidence is low. The heuristic path completely bypasses this threshold — a heuristic result at 0.60 confidence is returned as-is (STATUS_UPDATE), while an LLM result at 0.60 would become QUESTION. This creates an asymmetry where the fallback path is *less* conservative than the primary path.

6. **Stage-aware path bypasses classification entirely** — When `_sdlc_has_remaining=True` and under the cap, outputs auto-continue without any prose validation (job_queue.py:1107-1143). If the output is actually an error message ("Error: test suite timeout"), it gets silently re-enqueued instead of reaching the user. The `_sdlc_has_failed` guard only catches stage-level failures recorded in the session model, not errors in the agent's prose output.

7. **Zero test coverage for `_enqueue_continuation`** — The refactored function (job_queue.py:894) consolidates coaching+enqueue logic for both stage-aware and classifier paths. No unit tests exercise it directly. Coaching message generation, enqueue parameters, and source labeling are all untested. (Also tracked in issue #196.)

## Appetite

**Size:** Small (7 items: 4 original remaining + 3 new from deep audit)

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #195 merged | `gh pr view 195 --json state -q .state` → MERGED | Tech debt cleanup |

## Solution

### Key Elements

- **Heuristic safety net**: Default to QUESTION when no signal detected — "show the user" is always safer than "silently continue"
- **Heuristic confidence threshold**: Apply the same 0.80 threshold to heuristic results that the LLM path uses
- **Approval gate patterns**: Add heuristic + prompt patterns for permission-seeking language
- **Stage-aware error guard**: Classify prose before auto-continuing stage-aware jobs to catch error outputs
- **Explicit auto-continue state**: Replace `_defer_reaction` nonlocal with a dataclass that tracks state explicitly
- **Classification audit JSONL**: Lightweight structured log for every classify_output() call
- **`_enqueue_continuation` test coverage**: Unit tests for the shared coaching+enqueue function

### Technical Approach

1. **Fix heuristic default** (`bridge/summarizer.py`)

   In `_classify_with_heuristics()`, change the final default return from:
   ```python
   OutputType.STATUS_UPDATE, confidence=0.60, reason="No strong signal..."
   ```
   To:
   ```python
   OutputType.QUESTION, confidence=0.50, reason="No strong signal — defaulting to show user"
   ```
   This makes heuristic fallback conservative (show user) instead of permissive (auto-continue).

2. **Add approval gate patterns** (`bridge/summarizer.py`)

   Add to `_classify_with_heuristics()`:
   - Pattern list: "when approved", "ready to build", "waiting for.*go-ahead", "let me know when", "shall I proceed", "awaiting.*approval"
   - If any match → return QUESTION with confidence=0.85

   Add to `CLASSIFIER_SYSTEM_PROMPT`:
   - Explicit examples: "Ready to build when approved" → QUESTION, "Waiting for your go-ahead" → QUESTION
   - Rule: "Messages seeking permission or approval are QUESTION, not STATUS_UPDATE"

3. **Replace `_defer_reaction` with explicit state** (`agent/job_queue.py`)

   Create a small dataclass:
   ```python
   @dataclass
   class SendToChatResult:
       completion_sent: bool = False
       defer_reaction: bool = False
   ```

   `send_to_chat()` returns `SendToChatResult` instead of setting nonlocal variables. The outer `_execute_job()` reads the result explicitly. This eliminates the closure fragility — state is passed as a value, not mutated through a shared reference.

   Note: `_completion_sent` is also a nonlocal with the same fragility pattern. Consolidate both into the result dataclass.

4. **Add classification audit log** (`bridge/summarizer.py`)

   At the end of `classify_output()`, append a JSONL line to `logs/classification_audit.jsonl`:
   ```json
   {"ts": "2026-02-26T14:00:00Z", "session_id": "abc", "text_preview": "first 200 chars...", "result": "STATUS_UPDATE", "confidence": 0.92, "reason": "...", "source": "llm|heuristic"}
   ```
   Use append mode, no locking needed (single writer). Rotate by checking file size (>10MB → rename to `.1`).

5. **Apply confidence threshold to heuristics** (`bridge/summarizer.py`)

   After `_classify_with_heuristics()` returns, apply the same confidence gate used for LLM results. In `classify_output()`, after the heuristic fallback call:
   ```python
   result = _classify_with_heuristics(text)
   if result.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD:
       return ClassificationResult(
           output_type=OutputType.QUESTION,
           confidence=result.confidence,
           reason=f"Low heuristic confidence ({result.confidence:.2f}): {result.reason}",
       )
   return result
   ```
   This closes the asymmetry where heuristic at 0.60 becomes STATUS_UPDATE while LLM at 0.60 becomes QUESTION.

6. **Add error guard to stage-aware path** (`agent/job_queue.py`)

   Before auto-continuing in the stage-aware path (line 1107), run a lightweight check for error signals in the prose output:
   ```python
   # Quick error check — don't auto-continue if the output looks like an error
   from bridge.summarizer import _classify_with_heuristics
   quick_check = _classify_with_heuristics(msg[:500])
   if quick_check.output_type in (OutputType.ERROR, OutputType.BLOCKER):
       logger.warning(f"[{job.project_key}] Stage-aware path detected error in prose, routing to classifier")
       # Fall through to classifier-based routing below
   ```
   This catches error/blocker prose that would otherwise be silently auto-continued because stage history says "still in progress."

7. **Add `_enqueue_continuation` tests** (`tests/test_enqueue_continuation.py`)

   Create focused unit tests covering:
   - Coaching message built with correct `coaching_source` label
   - `enqueue_job` called with correct `auto_continue_count`, `session_id`, `work_item_slug`
   - Stage-aware vs classifier source labeling
   - Plan file resolution from WorkflowState
   - Error handling when `build_coaching_message` raises

## Rabbit Holes

- Don't redesign auto-continue as in-process looping — the re-enqueue pattern works, it just needs hardening
- Don't build a dashboard for classification accuracy — the JSONL log is sufficient
- Don't touch the stale-job-cleanup threshold — existing recovery functions handle it well enough
- Don't refactor the entire `_execute_job()` function — just the state management pattern
- Don't run full classification on every stage-aware output — a heuristic error check is sufficient (cheap, synchronous, no API call)
- Don't add dynamic confidence threshold tuning — a uniform 0.80 is fine for now

## Risks

### Risk 1: Heuristic default change causes unnecessary pauses
**Impact:** Agent pauses for human input when LLM is unavailable
**Mitigation:** Correct behavior — pausing unnecessarily is far less harmful than silently swallowing a question. LLM handles 95%+ of classifications; heuristic is rare fallback.

### Risk 2: SendToChatResult refactor breaks existing behavior
**Impact:** Reaction or completion state gets lost during refactor
**Mitigation:** Existing test suite (48 auto-continue tests) covers the state transitions. Run full suite before and after.

## No-Gos (Out of Scope)

- Full architecture replacement (re-enqueue → in-process looping)
- Real-time classification accuracy dashboard
- Classifier model upgrade (Haiku is fine)
- Changes to MAX_AUTO_CONTINUES values (done in PR #185)
- Redis startup cleanup for stale auto-continue jobs (existing recovery is sufficient)

## Update System

No update system changes required — this is bridge-internal behavior.

## Agent Integration

No agent integration required — this is a bridge-internal change.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` with classification safety net and audit log
- [ ] Add classification audit log format documentation

## Success Criteria

- [ ] Heuristic fallback defaults to QUESTION instead of STATUS_UPDATE
- [ ] Heuristic confidence threshold applied (0.80), matching LLM path
- [ ] "Ready to build when approved" correctly classified as QUESTION (heuristic + LLM)
- [ ] Stage-aware path checks for error/blocker prose before auto-continuing
- [ ] `_defer_reaction` and `_completion_sent` replaced with `SendToChatResult` dataclass
- [ ] Classification audit JSONL written for every `classify_output()` call
- [ ] `_enqueue_continuation` has dedicated unit tests
- [ ] All existing tests pass (48 auto-continue + 7 duplicate delivery)
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (safety-net)**
  - Name: safety-builder
  - Role: Fix heuristic default + threshold, add approval gates, stage-aware error guard, audit log
  - Agent Type: builder
  - Resume: true

- **Builder (state-refactor)**
  - Name: state-builder
  - Role: Replace nonlocal closure variables with explicit SendToChatResult, add _enqueue_continuation tests
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: audit-validator
  - Role: Verify all fixes, run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix heuristic safety net (default + threshold + approval gates)
- **Task ID**: fix-heuristic-safety
- **Depends On**: none
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `_classify_with_heuristics` default from STATUS_UPDATE(0.60) to QUESTION(0.50)
- Apply `CLASSIFICATION_CONFIDENCE_THRESHOLD` to heuristic results in `classify_output()` (same gate as LLM path)
- Add approval gate pattern matching to heuristics
- Add approval gate examples to CLASSIFIER_SYSTEM_PROMPT
- Add tests for new default, threshold application, and approval gate classification

### 2. Replace closure state with SendToChatResult
- **Task ID**: refactor-state
- **Depends On**: none
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `SendToChatResult` dataclass
- Refactor `send_to_chat()` to return it instead of setting nonlocals
- Update all read sites in `_execute_job()` to use the result object
- Verify all 48 auto-continue tests still pass

### 3. Add stage-aware error guard
- **Task ID**: stage-error-guard
- **Depends On**: none
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: true
- Add heuristic error/blocker check before stage-aware auto-continue (job_queue.py:1107)
- If error detected, fall through to classifier path instead of auto-continuing
- Add test: stage-aware path with error prose routes to classifier

### 4. Add classification audit log
- **Task ID**: audit-log
- **Depends On**: fix-heuristic-safety
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: false
- Add JSONL append at end of `classify_output()`
- Include: timestamp, session_id, text preview, result, confidence, source
- Simple size-based rotation (>10MB)

### 5. Add `_enqueue_continuation` tests
- **Task ID**: test-enqueue-continuation
- **Depends On**: refactor-state
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/test_enqueue_continuation.py`
- Test coaching message generation with correct source labels
- Test enqueue_job parameters (session_id, auto_continue_count, work_item_slug)
- Test plan file resolution from WorkflowState
- Test error handling when build_coaching_message raises

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: fix-heuristic-safety, refactor-state, stage-error-guard, audit-log, test-enqueue-continuation
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all auto-continue and classification tests
- Verify heuristic default returns QUESTION
- Verify heuristic confidence threshold applied
- Verify "Ready to build when approved" classified as QUESTION
- Verify stage-aware error guard catches error prose
- Verify audit log written
- Verify _enqueue_continuation tests pass
- Run ruff + black checks

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: safety-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/coaching-loop.md` with safety net details
- Document audit log format
- Document stage-aware error guard behavior

### 8. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Full test suite run
- Verify all success criteria

## Validation Commands

- `pytest tests/test_auto_continue.py tests/test_stage_aware_auto_continue.py -v` — Auto-continue tests
- `pytest tests/test_summarizer.py -v` — Summarizer/classifier tests
- `pytest tests/test_duplicate_delivery.py -v` — Dedup tests
- `black --check bridge/summarizer.py agent/job_queue.py` — Format
- `ruff check bridge/summarizer.py agent/job_queue.py` — Lint
