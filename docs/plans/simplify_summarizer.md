---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/241
---

# Simplify Summarizer

## Problem

The summarizer has three issues making responses look unprofessional:

1. **Message echo**: Every response starts by repeating the user's message (`✅ that's very strange that 0 chars returned.`). This is redundant because Telegram's reply feature already shows the original message.

2. **Remaining bypass paths**: When the SDK returns empty/short text, the summarizer may be skipped entirely, causing raw unstyled output to reach the user.

3. **Accumulated complexity**: The summarizer has grown special cases, thresholds, and conditional paths. It should follow two simple rule sets: one for chat, one for SDLC.

**Current behavior:**
Responses echo the user's message on line 1, sometimes bypass summarization for short outputs, and have complex conditional formatting.

**Desired outcome:**
Every response goes through summarization. Two simple modes: chat and SDLC. No message echo. The persona is always a senior developer reporting to a PM.

## Appetite

**Size:** Small

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Remove message echo**: Delete the `_get_original_request()` label logic from `_compose_structured_summary()`
- **Always summarize**: Remove the early return for empty/short responses in `summarize_response()`
- **Simplify rules**: Two modes only -- chat and SDLC

### Technical Approach

#### Change 1: Remove message echo (lines 978-995 of summarizer.py)

Delete the entire label block in `_compose_structured_summary()`:

```python
# DELETE THIS BLOCK (lines 978-995):
# Status emoji + first-line label from the ORIGINAL request
emoji = _get_status_emoji(session, is_completion)
label = ""
if session:
    original_request = _get_original_request(session)
    if original_request:
        first_line = original_request.split("\n")[0].strip()
        ...
        label = first_line
if label:
    parts.append(f"{emoji} {label}")
else:
    ...
```

Replace with just the emoji prefix:

```python
emoji = _get_status_emoji(session, is_completion)
parts.append(emoji)
```

Also delete `_get_original_request()` function (lines 924-936) -- no longer needed.

#### Change 2: Always summarize

In `summarize_response()` (line 1043), the early return for empty responses should still construct an SDLC progress report if the session has stage data. Replace:

```python
if not raw_response or not raw_response.strip():
    return SummarizedResponse(text=raw_response or "", was_summarized=False)
```

With:

```python
if not raw_response or not raw_response.strip():
    # Even with empty response, render SDLC progress if available
    if session:
        fallback = _compose_structured_summary("", session=session, is_completion=True)
        if fallback.strip():
            return SummarizedResponse(text=fallback, was_summarized=True)
    return SummarizedResponse(text=raw_response or "", was_summarized=False)
```

This also fixes issue #240 (empty SDK response bypasses SDLC progress delivery).

#### Change 3: Simplify _compose_structured_summary

The function should follow two simple paths:

**Chat (non-SDLC):**
```
{emoji}
{summary bullets or prose}

{questions if any}
```

**SDLC:**
```
{emoji}
{stage progress line}
{summary bullets}

{questions if any}
{link footer}
```

No conditional label, no complex branching.

### Flow

**Agent output** -> `summarize_response()` -> always runs LLM summarization -> `_compose_structured_summary()` -> emoji + (stage line if SDLC) + bullets + (questions) + (links if SDLC) -> Telegram

## Rabbit Holes

- Rewriting the `SUMMARIZER_SYSTEM_PROMPT` -- it's already good, just simplify the wrapper logic
- Changing how questions are parsed -- `_parse_summary_and_questions()` works fine
- Touching the classifier -- that's a separate concern (issue #232 already addressed it)

## Risks

### Risk 1: Removing echo breaks context for auto-continued sessions
**Impact:** Late-arriving auto-continued replies might lack context about what they're responding to
**Mitigation:** Telegram reply-to already provides this context. The echo was always redundant for reply threads.

## No-Gos (Out of Scope)

- Changing the classifier prompt or classification logic
- Changing the SUMMARIZER_SYSTEM_PROMPT content (just the wrapper)
- Modifying auto-continue behavior
- Changing how the summarizer is called from job_queue.py

## Update System

No update system changes required -- this is a bridge-internal change to summarizer.py only.

## Agent Integration

No agent integration required -- this is a bridge-internal change affecting response formatting only. No new MCP tools or tool exposure needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/summarizer-format.md` -- remove references to echo/label, document simplified two-mode behavior

### Inline Documentation
- [ ] Update docstring for `_compose_structured_summary()` -- remove reference to "Original request summary" in format example

## Success Criteria

- [ ] Responses never echo the user's original message
- [ ] Empty SDK responses still render SDLC stage progress (fixes #240 too)
- [ ] All responses go through summarization (no bypass for short text)
- [ ] Chat responses: emoji + summary
- [ ] SDLC responses: emoji + stage line + summary + links
- [ ] Existing summarizer tests still pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (summarizer-simplify)**
  - Name: summarizer-builder
  - Role: Remove echo, simplify compose function, ensure always-summarize
  - Agent Type: builder
  - Resume: true

- **Validator (summarizer-verify)**
  - Name: summarizer-validator
  - Role: Verify no echo, SDLC progress renders, all tests pass
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update summarizer-format.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Remove echo and simplify compose
- **Task ID**: build-summarizer
- **Depends On**: none
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_get_original_request()` function
- Remove label echo block from `_compose_structured_summary()`
- Replace with simple emoji prefix
- Add SDLC fallback for empty responses in `summarize_response()`
- Run existing tests to verify no regressions

### 2. Validate changes
- **Task ID**: validate-summarizer
- **Depends On**: build-summarizer
- **Assigned To**: summarizer-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no echo in output format
- Verify SDLC progress renders for empty responses
- Run full test suite

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-summarizer
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/summarizer-format.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: summarizer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `cd /Users/valorengels/src/ai && python -m pytest tests/test_summarizer.py -v -p no:postgresql` -- summarizer tests
- `cd /Users/valorengels/src/ai && python -m pytest tests/test_cross_wire_fixes.py -v -p no:postgresql` -- cross-wire tests (related)
- `cd /Users/valorengels/src/ai && python -m ruff check bridge/summarizer.py` -- lint
