---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/232
---

# Fix DM/Group Chat Cross-Wire

## Problem

Messages from one Telegram chat get their responses delivered to a different chat, in reply to a completely different conversation.

**Current behavior:**
Tom asked "We are getting duplicate daydream reports each day" in Dev: Valor group and "How does the summarizer feature work?" in DM. The DM received a response about daydream bugs instead of the summarizer explanation.

**Desired outcome:**
Each conversation gets its own response. No cross-contamination between concurrent sessions across different chats.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on classifier approach)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Root Cause Analysis

Two bugs acted in tandem:

1. **Classifier false positive**: The output classifier (`CLASSIFIER_SYSTEM_PROMPT` in `bridge/summarizer.py`) classified a Q&A answer about the summarizer as `status_update` (0.88 confidence) because it lacked "evidence" (test results, URLs, numbers). The prompt defines COMPLETION as requiring evidence — but informational/conversational answers have no evidence to show. This triggered an unwanted auto-continue.

2. **Shared Claude Code session**: The auto-continued DM job resumed the same Claude Code session (`0d825ffe`) that was being used by the concurrent Dev: Valor group investigation. The `resume=session_id` parameter in `sdk_client.py` passes our Telegram session_id, but `continue_conversation=True` is set whenever session_id is not None — even for brand-new sessions. Claude Code may reuse the most recent conversation on disk when a session_id doesn't match any existing session file.

### Key Elements

- **Classifier Q&A awareness**: Add a CONVERSATIONAL classification path for non-SDLC informational answers
- **Session isolation**: Prevent concurrent jobs from sharing Claude Code session state
- **Non-SDLC bypass**: Skip auto-continue entirely for non-SDLC (DM/conversational) sessions

### Technical Approach

#### Fix 1: Classifier — Add CONVERSATIONAL handling

The classifier prompt currently has no path for informational Q&A answers. These get caught by STATUS_UPDATE's "no question directed at human, intermediate progress" pattern.

**Approach**: Modify `CLASSIFIER_SYSTEM_PROMPT` to handle conversational/informational responses:

- Add explicit guidance that explanatory answers to user questions are COMPLETION, not STATUS_UPDATE
- Add few-shot examples showing Q&A answers classified as completion
- Key signal: if the user asked a question and the agent answered it without hedging, that's a completion — even without test output or URLs

The existing `was_rejected_completion` field already handles downgraded completions. The fix is upstream in the prompt itself.

#### Fix 2: Session isolation — Check AgentSession before setting `continue_conversation`

In `sdk_client.py`, line 526: `continue_conversation=session_id is not None`. This is always `True` because we always pass a session_id. For fresh (non-reply) messages, there's no previous Claude Code session to continue.

**Approach**: Query `AgentSession` in Redis to check if a session with this session_id has had prior activity (i.e., a previous completed or running job exists). Only set `continue_conversation=True` when the AgentSession DB confirms a prior session exists:

```python
# Only continue conversation when AgentSession confirms prior activity
continue_conversation = session_id is not None and _has_prior_session(session_id)
```

Using AgentSession (our existing Redis model) is robust — survives restarts, doesn't couple to Claude Code internal file structure, and leverages data we already track.

#### Fix 3: Non-SDLC auto-continue with different criteria

The DM message was a simple Q&A — not an SDLC job. The auto-continue logic (`MAX_AUTO_CONTINUES = 3`) still ran because the classifier said "status". Non-SDLC sessions need different auto-continue criteria than SDLC sessions.

**Approach**: In `send_to_chat()` in `job_queue.py`, apply different auto-continue rules for non-SDLC jobs:

- **Auto-continue**: When the agent shares its plan/approach before executing (e.g., "I'm going to check X, Y, Z...") — these are genuine status updates that should continue
- **Stop (deliver)**: When the agent asks a follow-up question, delivers an answer, or requests input — these need human attention
- **Stop (deliver)**: When the classifier says COMPLETION, QUESTION, BLOCKER, or ERROR

The key distinction: non-SDLC status updates that are "here's my plan" should auto-continue, but informational answers to user questions should not. The classifier needs to distinguish these — or the non-SDLC path should use a simpler heuristic (e.g., only auto-continue if the output is short AND contains planning language like "I'll", "Let me", "First I need to").

### Flow

**Message arrives** → Classifier evaluates output →
  - If SDLC job: existing auto-continue logic (max 10)
  - If non-SDLC + completion/question/blocker/error: deliver immediately
  - If non-SDLC + status_update + planning language: auto-continue (agent sharing its approach)
  - If non-SDLC + status_update + substantive content: deliver immediately (informational answer)

## Rabbit Holes

- **Rewriting the entire classifier**: The classifier prompt is complex and well-tuned for SDLC work. Don't rewrite — add targeted Q&A guidance only
- **Full session file management**: Don't build a session file tracking system. The simplest fix is to not set `continue_conversation=True` for fresh sessions
- **Per-chat Claude Code instances**: Overkill. The fix is preventing session reuse, not running separate processes

## Risks

### Risk 1: Classifier changes affect SDLC classification accuracy
**Impact:** SDLC status updates might get classified as completion prematurely, breaking auto-continue
**Mitigation:** Add Q&A-specific guidance ONLY — don't modify SDLC classification rules. Test with existing SDLC examples

### Risk 2: Disabling auto-continue for non-SDLC breaks legitimate use cases
**Impact:** Status updates from non-SDLC work (e.g., research tasks) might flood Telegram
**Mitigation:** Non-SDLC sessions rarely generate multiple outputs. If they do, the raw output is still valuable. Monitor after deployment

## No-Gos (Out of Scope)

- Rewriting the classifier from scratch — targeted prompt changes only
- Building a new session registry — use existing AgentSession Redis model
- Changing the Telegram session_id format
- Modifying how reply-thread continuation works (that's working correctly)

## Update System

No update system changes required — all changes are to bridge-internal Python code that deploys automatically.

## Agent Integration

No agent integration required — this is a bridge-internal change affecting message routing and classification. No new MCP tools or tool exposure needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/coaching-loop.md` — add Q&A classification guidance
- [ ] Update `docs/features/summarizer-format.md` — document non-SDLC auto-continue bypass

### Inline Documentation
- [ ] Code comments on classifier Q&A examples
- [ ] Docstring updates for `_create_options()` explaining session isolation logic

## Success Criteria

- [ ] Q&A answers in DM are classified as COMPLETION (not STATUS_UPDATE)
- [ ] Non-SDLC sessions never trigger auto-continue
- [ ] Fresh (non-reply) sessions don't set `continue_conversation=True`
- [ ] Concurrent DM and group sessions produce independent responses
- [ ] Existing SDLC auto-continue still works correctly (regression test)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classifier-fix)**
  - Name: classifier-builder
  - Role: Update classifier prompt and add non-SDLC guard
  - Agent Type: builder
  - Resume: true

- **Builder (session-isolation)**
  - Name: session-builder
  - Role: Fix session isolation in sdk_client.py
  - Agent Type: builder
  - Resume: true

- **Validator (cross-wire)**
  - Name: cross-wire-validator
  - Role: Verify fixes prevent cross-contamination
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update coaching-loop and summarizer docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix classifier prompt for Q&A responses
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Q&A/informational completion guidance to `CLASSIFIER_SYSTEM_PROMPT` in `bridge/summarizer.py`
- Add few-shot examples: "The summarizer works by..." → completion, "Here's how X works..." → completion
- Add explicit rule: "If the user asked a question and the agent answered it with factual content (not hedging), classify as COMPLETION"
- Write tests verifying Q&A outputs classify as COMPLETION

### 2. Fix session isolation in sdk_client.py
- **Task ID**: build-session
- **Depends On**: none
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `continue_conversation` logic in `_create_options()` to query AgentSession Redis model
- Only set `continue_conversation=True` when AgentSession confirms a prior session exists for this session_id
- Add `_has_prior_session(session_id)` helper that queries AgentSession
- Write tests verifying fresh sessions don't set `continue_conversation=True`

### 3. Add non-SDLC auto-continue with different criteria
- **Task ID**: build-guard
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- In `send_to_chat()` in `job_queue.py`, add non-SDLC branch with different auto-continue criteria
- Auto-continue when: output is short + contains planning language ("I'll", "Let me", "First I need to") — agent sharing its approach before executing
- Deliver when: output is substantive answer, contains follow-up questions, or is a completion
- Write tests verifying: (a) non-SDLC planning outputs auto-continue, (b) non-SDLC informational answers are delivered

### 4. Validate all fixes
- **Task ID**: validate-fixes
- **Depends On**: build-classifier, build-session, build-guard
- **Assigned To**: cross-wire-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify Q&A classification
- Verify session isolation
- Verify non-SDLC delivery

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fixes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/coaching-loop.md` with Q&A handling
- Update `docs/features/summarizer-format.md` with non-SDLC bypass

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cross-wire-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `cd /Users/valorengels/src/ai && python -m pytest tests/test_summarizer.py -v` - classifier tests
- `cd /Users/valorengels/src/ai && python -m pytest tests/ -v` - full test suite
- `cd /Users/valorengels/src/ai && ruff check .` - lint check

---

## Resolved Questions

1. **Session isolation approach**: Use AgentSession Redis model (our existing DB). Query it to check if a prior session exists before setting `continue_conversation=True`. Robust, survives restarts, no coupling to Claude Code internals.
2. **Non-SDLC auto-continue**: Use different criteria, not a simple cap. Auto-continue when agent shares its plan/approach (planning language). Stop when agent delivers an answer or asks a follow-up question.
