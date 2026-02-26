---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-02-14
revised: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/99
---

# Auto-Continue Audit: Remaining Fixes

## Problem

The original issue (#99) identified 7 reliability gaps in the auto-continue system. After a burst of related PRs (Feb 24-26), **5 of 7 items are resolved**. This revised plan covers the 4 remaining items.

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

## Appetite

**Size:** Small (revised down from Medium — 5/7 items already shipped)

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
- **Approval gate patterns**: Add heuristic + prompt patterns for permission-seeking language
- **Explicit auto-continue state**: Replace `_defer_reaction` nonlocal with a dataclass that tracks state explicitly
- **Classification audit JSONL**: Lightweight structured log for every classify_output() call

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

## Rabbit Holes

- Don't redesign auto-continue as in-process looping — the re-enqueue pattern works, it just needs hardening
- Don't build a dashboard for classification accuracy — the JSONL log is sufficient
- Don't touch the stale-job-cleanup threshold — existing recovery functions handle it well enough
- Don't refactor the entire `_execute_job()` function — just the state management pattern

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
- [ ] "Ready to build when approved" correctly classified as QUESTION (heuristic + LLM)
- [ ] `_defer_reaction` and `_completion_sent` replaced with `SendToChatResult` dataclass
- [ ] Classification audit JSONL written for every `classify_output()` call
- [ ] All existing tests pass (48 auto-continue + 7 duplicate delivery)
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (safety-net)**
  - Name: safety-builder
  - Role: Fix heuristic default, add approval gate patterns, add audit log
  - Agent Type: builder
  - Resume: true

- **Builder (state-refactor)**
  - Name: state-builder
  - Role: Replace nonlocal closure variables with explicit SendToChatResult
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: audit-validator
  - Role: Verify all fixes, run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix heuristic default and add approval gate patterns
- **Task ID**: fix-heuristic-and-gates
- **Depends On**: none
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `_classify_with_heuristics` default from STATUS_UPDATE(0.60) to QUESTION(0.50)
- Add approval gate pattern matching to heuristics
- Add approval gate examples to CLASSIFIER_SYSTEM_PROMPT
- Add tests for new default and approval gate classification

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

### 3. Add classification audit log
- **Task ID**: audit-log
- **Depends On**: fix-heuristic-and-gates
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: false
- Add JSONL append at end of `classify_output()`
- Include: timestamp, session_id, text preview, result, confidence, source
- Simple size-based rotation (>10MB)

### 4. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: fix-heuristic-and-gates, refactor-state, audit-log
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all auto-continue and classification tests
- Verify heuristic default returns QUESTION
- Verify "Ready to build when approved" classified as QUESTION
- Verify audit log written
- Run ruff + black checks

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: safety-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/coaching-loop.md` with safety net details
- Document audit log format

### 6. Final Validation
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
