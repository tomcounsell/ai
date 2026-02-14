---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-02-14
tracking: https://github.com/tomcounsell/ai/issues/99
---

# Auto-Continue Audit: Stale Jobs, Ghost Queuing, and Reliability

## Problem

The auto-continue system (job re-enqueue on status updates) has reliability gaps that cause stale jobs, phantom queue depth, and potential silent message loss.

**Current behavior:**
- Auto-continue jobs can get stuck in `pending` indefinitely — one was found alongside 38 phantom jobs inflating queue depth
- "Queued (position 38)" shown to users when the real queue was empty, due to orphaned Redis index entries
- Classification defaults to `STATUS_UPDATE` at confidence=0.60 when LLM fails — silently swallowing messages that may need human attention
- "Ready to build when approved" was classified as status (0.92 confidence) instead of question, auto-continuing past a genuine approval gate
- `_defer_reaction` is a fragile closure variable that can get into inconsistent state
- No observability into classification accuracy — no way to tell if the system is misclassifying in production

**Desired outcome:**
- Stale auto-continue jobs are cleaned up on startup
- Classification accuracy is measurable and improvable
- Silent message loss is impossible — fallback is always "show the user"
- Auto-continue path is auditable via logs and metrics

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (alignment on which issues are real bugs vs. acceptable behavior)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Redis cleanup on startup**: Prune orphaned auto-continue jobs and stale index entries
- **Classification safety net**: Never silently swallow messages — if confidence is low, show the user
- **Heuristic fallback fix**: Default to QUESTION (not STATUS_UPDATE) when no signal is detected
- **Observability**: Log classification decisions with enough context to audit accuracy
- **Deferred reaction hardening**: Replace fragile closure flag with explicit state

### Flow

**Agent produces output** → classify_output() → High confidence STATUS_UPDATE → auto-continue → Re-enqueue job

**Agent produces output** → classify_output() → Low confidence anything → Send to chat (conservative) → Wait for human

**Bridge startup** → Scan Redis for orphaned auto-continue jobs → Prune stale entries → Log cleanup actions

### Technical Approach

1. **Fix heuristic default** (`bridge/summarizer.py:266-271`): Change `_classify_with_heuristics` default from `STATUS_UPDATE` (confidence=0.60) to `QUESTION` (confidence=0.50). This makes the conservative path "show the user" instead of "silently continue." The LLM path already does this via the confidence threshold — the heuristic path should match.

2. **Redis startup audit** (`agent/job_queue.py`): On worker startup, scan for jobs with `sender_name="System (auto-continue)"` that are in `pending` state for >5 minutes. Delete them and log the cleanup. Also prune orphaned KeyField index entries left by pre-1.0.0b2 deletes.

3. **Improve "approval gate" classification**: Add explicit patterns to the classifier system prompt for messages like "Ready to build when approved", "Waiting for your go-ahead", "Let me know when to proceed" — these are QUESTION, not STATUS_UPDATE.

4. **Harden `_defer_reaction`** (`agent/job_queue.py:827`): Move from a closure nonlocal to an explicit field on the job or a return value from `send_to_chat`, so it can't get into inconsistent state from multiple outputs or errors.

5. **Add classification audit log**: Write a JSONL file (`logs/classification_audit.jsonl`) with every classification decision including the input text preview, result, confidence, and whether auto-continue was triggered. This enables offline accuracy analysis.

6. **Handle "job outlives parent" edge case**: When a continuation job runs and the session has no active conversation context, detect this and send a summary to chat instead of running blind.

## Rabbit Holes

- Don't redesign auto-continue as in-process looping — the re-enqueue pattern works, it just needs hardening. A full architecture swap would be a separate project.
- Don't build a real-time classification accuracy dashboard — the JSONL audit log is sufficient for now.
- Don't try to retroactively fix all 38 phantom jobs in Redis — just add the cleanup-on-startup mechanism.

## Risks

### Risk 1: Changing heuristic default causes unnecessary pauses
**Impact:** Agent pauses for human input on status updates when LLM classification is unavailable
**Mitigation:** This is the correct conservative behavior — pausing unnecessarily is far less harmful than silently swallowing a question. The LLM path handles 95%+ of classifications; heuristic fallback is rare.

### Risk 2: Startup cleanup deletes a legitimate pending job
**Impact:** An auto-continue that was about to be picked up gets pruned
**Mitigation:** Only prune jobs older than 5 minutes. Auto-continue jobs should be picked up within seconds. Log every deletion for audit.

## No-Gos (Out of Scope)

- Full architecture replacement of re-enqueue pattern with in-process looping
- Real-time classification accuracy monitoring dashboard
- Changes to MAX_AUTO_CONTINUES value (separate decision)
- Classifier model upgrade (Haiku is fine, the prompts need work)

## Update System

No update system changes required — this is bridge-internal behavior. The startup cleanup runs automatically.

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent doesn't know about auto-continue; it just sees "continue" messages.

## Documentation

- [ ] Update `docs/features/bridge-workflow-gaps.md` with the audit findings and fixes
- [ ] Add entry to `docs/features/README.md` if a new feature doc is warranted
- [ ] Document the classification audit log format in `docs/features/bridge-workflow-gaps.md`

## Success Criteria

- [ ] Stale auto-continue jobs pruned on bridge startup (verified in logs)
- [ ] Heuristic fallback defaults to QUESTION instead of STATUS_UPDATE
- [ ] "Ready to build when approved" correctly classified as QUESTION
- [ ] `_defer_reaction` replaced with explicit state tracking
- [ ] Classification audit JSONL log written for every decision
- [ ] Orphaned Redis index entries cleaned up on startup
- [ ] All existing tests pass + new tests for edge cases
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (auto-continue-hardening)**
  - Name: hardener
  - Role: Fix heuristic default, harden defer_reaction, add startup cleanup
  - Agent Type: builder
  - Resume: true

- **Builder (classification-improvement)**
  - Name: classifier
  - Role: Improve classifier prompts, add audit logging
  - Agent Type: builder
  - Resume: true

- **Validator (auto-continue-validation)**
  - Name: validator
  - Role: Verify all fixes work correctly, run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix heuristic fallback default
- **Task ID**: fix-heuristic-default
- **Depends On**: none
- **Assigned To**: hardener
- **Agent Type**: builder
- **Parallel**: true
- Change `_classify_with_heuristics` default (line 266-271 of `bridge/summarizer.py`) from `STATUS_UPDATE` to `QUESTION` with confidence=0.50
- Update the docstring to reflect the new conservative default
- Add test case verifying the new default

### 2. Improve classifier prompts
- **Task ID**: improve-classifier
- **Depends On**: none
- **Assigned To**: classifier
- **Agent Type**: builder
- **Parallel**: true
- Add "approval gate" patterns to `CLASSIFIER_SYSTEM_PROMPT` in `bridge/summarizer.py`
- Add heuristic patterns for approval-seeking language
- Add test cases for "Ready to build when approved" and similar

### 3. Add Redis startup cleanup
- **Task ID**: redis-cleanup
- **Depends On**: none
- **Assigned To**: hardener
- **Agent Type**: builder
- **Parallel**: true
- Add `prune_stale_auto_continues()` function to `agent/job_queue.py`
- Call it during worker startup
- Prune jobs with `sender_name="System (auto-continue)"` older than 5 minutes
- Log every deletion

### 4. Harden _defer_reaction
- **Task ID**: harden-defer-reaction
- **Depends On**: none
- **Assigned To**: hardener
- **Agent Type**: builder
- **Parallel**: true
- Replace nonlocal `_defer_reaction` with explicit return value or state on the job
- Ensure consistent state even with multiple outputs or errors

### 5. Add classification audit log
- **Task ID**: audit-log
- **Depends On**: none
- **Assigned To**: classifier
- **Agent Type**: builder
- **Parallel**: true
- Write JSONL to `logs/classification_audit.jsonl` on every `classify_output()` call
- Include: timestamp, session_id, text preview (first 200 chars), classification result, confidence, auto-continue triggered (bool)

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: fix-heuristic-default, improve-classifier, redis-cleanup, harden-defer-reaction, audit-log
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_auto_continue.py tests/test_reply_delivery.py tests/test_job_queue_race.py -v`
- Verify heuristic fallback returns QUESTION
- Verify "Ready to build when approved" classified as QUESTION
- Verify classification audit log is written
- Check startup cleanup logic

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: classifier
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-workflow-gaps.md` with audit findings
- Document classification audit log format
- Update `docs/features/README.md` index if needed

### 8. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite `pytest tests/ -v`
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/test_auto_continue.py -v` - Auto-continue routing tests
- `pytest tests/test_reply_delivery.py -v` - Reply delivery and messenger tracking
- `pytest tests/test_job_queue_race.py -v` - Job queue race condition tests
- `black --check bridge/summarizer.py agent/job_queue.py` - Code formatting
- `ruff check bridge/summarizer.py agent/job_queue.py` - Linting

---

## Open Questions

1. **Should we raise MAX_AUTO_CONTINUES from 3?** The current limit seems reasonable, but some long-running tasks (like `/build`) legitimately produce many status updates. Should the limit be configurable per-task or per-session type?

2. **Should the "job outlives parent" case be a hard error or graceful degradation?** When a continuation job finds no active conversation, should it (a) send a summary to chat and stop, (b) log and discard, or (c) attempt to resume anyway?

3. **Is the 5-minute threshold for stale job cleanup correct?** Auto-continue jobs should be picked up in seconds, but what if Redis is temporarily overloaded? Should we use a longer threshold (15 min) to be safe?
