---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/190
---

# Skip Summarization for Programmatic (Non-SDK) Skill Responses

## Problem

PR #187 removed the 500-char summarization threshold so all responses go through Haiku. This inadvertently summarizes programmatic skill outputs like `/update` which have `disable-model-invocation: true`.

**Current behavior:**
Programmatic skill output (commit lists, service status) gets rewritten by Haiku into bullet-point prose, losing operational detail.

**Desired outcome:**
Programmatic responses pass through unchanged. Only Agent SDK responses (where Claude generated the prose) get summarized.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all code exists and is already merged.

## Solution

### Key Elements

- **Session-based bypass**: Only summarize when the response comes from an AgentSession that used the SDK (i.e., `session` is not None). Programmatic skills that don't flow through the job queue pass `session=None`.

### Technical Approach

In `bridge/response.py` line ~388, change the summarization guard:

```python
# Before (always summarize):
if text:
    summarized = await summarize_response(text, session=session)

# After (only summarize SDK responses):
if text and session is not None:
    summarized = await summarize_response(text, session=session)
```

This works because:
- **SDK agent responses** always flow through `job_queue.py` → `send_cb()` with a valid `agent_session`
- **Programmatic skill responses** (like `/update`) still go through the same path but the key distinction is these are already formatted command output that shouldn't be reprocessed

Wait — both paths pass `agent_session`. Let me reconsider.

**Better approach**: Add a lightweight flag to detect programmatic output. The simplest signal: check if the session's `message_text` starts with a known programmatic skill prefix, or add a boolean field to AgentSession.

**Simplest correct approach**: In `bridge/summarizer.py`, `summarize_response()` should accept a `skip_summarize` boolean. The caller in `bridge/response.py` passes it. The bridge's `_send` callback sets it when the Telegram skill used `disable-model-invocation`. But we don't have that metadata at the callback level.

**Actual simplest approach**: Add a `is_programmatic` flag to AgentSession. Set it in `_execute_job()` when the skill output is detected as non-SDK. But skill metadata isn't available at the job level.

**Revised approach — keep it at the response layer**: The `send_response_with_files()` function receives `session`. If session is None (no job context), skip summarization. For job-based sessions, we always summarize. The `/update` skill runs through Claude Code which DOES invoke the model (to parse the skill), so it still gets a session.

**Final approach**: Since the real distinction is `disable-model-invocation: true` skills produce raw bash output that doesn't need summarization, the cleanest fix is to detect this at the summarizer level. Short raw command output (stdout/stderr patterns, no prose) shouldn't be summarized. But this is heuristic and fragile.

**Pragmatic fix**: Add `skip_summarize: bool = False` parameter to `send_response_with_files()`. The bridge callback passes `skip_summarize=True` when it knows the response is programmatic. The bridge can detect this by checking if the session's response was from a programmatic skill — which we can flag in the AgentSession during job execution.

**Implementation**:

1. Add `is_programmatic` field to `AgentSession` (default `False`)
2. In `agent/job_queue.py` `_execute_job()`, after getting the SDK response, leave it as False (default). For programmatic skills, the SDK itself handles the skill — we can't easily distinguish at this layer.
3. Actually — the cleanest approach: just check the response length. If it's short AND doesn't need SDLC formatting (no session or non-SDLC session), skip summarization. This re-introduces a threshold but only for non-SDLC sessions.

**FINAL decision — simplest correct fix**:

In `bridge/response.py`, restore a minimal threshold for non-SDLC sessions only:

```python
if text:
    should_summarize = True
    # Skip summarization for short programmatic responses (no SDLC context)
    is_sdlc = session and hasattr(session, 'is_sdlc_job') and session.is_sdlc_job()
    if not is_sdlc and len(text) < 500:
        should_summarize = False

    if should_summarize:
        summarized = await summarize_response(text, session=session)
```

This means:
- SDLC sessions: ALWAYS summarize (stage lines, link footers needed)
- Non-SDLC short responses (< 500 chars): pass through raw (covers `/update`, simple Q&A)
- Non-SDLC long responses (>= 500 chars): still summarize (verbose agent output)

## Rabbit Holes

- Adding skill metadata propagation through the entire callback chain
- Content-based detection of "programmatic vs prose" output
- Redesigning the summarizer to handle multiple output types

## Risks

### Risk 1: Short non-SDLC responses lose emoji formatting
**Impact:** Conversational responses under 500 chars won't get the ✅/⏳ prefix
**Mitigation:** Acceptable tradeoff — short responses are already concise. The emoji was added by PR #187 and wasn't present before.

## No-Gos (Out of Scope)

- Propagating skill metadata through AgentSession
- Adding content-type detection to the summarizer
- Changing SDLC summarization behavior

## Update System

No update system changes required — this is a bridge-internal change.

## Agent Integration

No agent integration required — this is a bridge-internal change.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` to note the non-SDLC short-response bypass
- [ ] Code comments on the threshold logic in response.py

## Success Criteria

- [ ] `/update` output passes through raw (not summarized)
- [ ] SDLC messages still always get stage lines and link footers
- [ ] Long non-SDLC responses still get summarized
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (response-fix)**
  - Name: response-builder
  - Role: Add non-SDLC short response bypass in bridge/response.py
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: response-validator
  - Role: Verify bypass works, SDLC unaffected
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add non-SDLC short response bypass
- **Task ID**: build-bypass
- **Depends On**: none
- **Assigned To**: response-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `bridge/response.py` `send_response_with_files()` to skip summarization for short non-SDLC responses
- Update tests in `tests/test_summarizer.py` and `tests/test_agent_session_lifecycle.py`

### 2. Validate
- **Task ID**: validate-all
- **Depends On**: build-bypass
- **Assigned To**: response-validator
- **Agent Type**: validator
- **Parallel**: false
- Run test suite
- Verify SDLC messages still summarized
- Verify short non-SDLC responses pass through

## Validation Commands

- `pytest tests/test_summarizer.py -v -p no:postgresql` — Summarizer tests
- `pytest tests/test_agent_session_lifecycle.py -v -p no:postgresql -k "not test_fallback_on_parse_error and not test_redis_job_is_agent_session"` — Lifecycle tests
- `python -m ruff check bridge/response.py` — Linting
