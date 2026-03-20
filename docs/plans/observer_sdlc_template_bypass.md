---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/457
last_comment_id:
---

# Observer message_for_user bypasses SDLC template formatting

## Problem

When the Observer delivers output to Telegram via `deliver_to_telegram` with a curated `message_for_user`, SDLC sessions lose their structured formatting (stage progress line, link footers). The user receives plain text instead of the expected SDLC template.

**Current behavior:**
- Observer provides `message_for_user` → short curated text replaces full worker output
- In `job_queue.py:1890`: `delivery_msg = decision.get("message_for_user", msg)` discards the full worker output
- The curated text does flow through the summarizer (via `send_response_with_files`), and `_compose_structured_summary()` is called
- However, the Observer LLM sometimes misclassifies SDLC sessions as "non-SDLC" based on output content analysis, ignoring `session.is_sdlc`
- The Observer prompt says `DELIVER when: ... non-SDLC job` without clarifying that `is_sdlc` from session state is authoritative

**Desired outcome:**
- All SDLC session deliveries include stage progress line and link footers
- Observer respects `session.is_sdlc` as authoritative — never overrides based on content analysis
- Non-SDLC message delivery is unaffected

## Prior Art

- **PR #408**: "Fix observer reason leak and false promise halts" — Related work on Observer delivery behavior, but focused on reason field isolation, not SDLC template formatting.

## Data Flow

1. **Entry point**: Worker agent stops producing output → `send_to_chat()` in `job_queue.py:1583`
2. **Observer**: `Observer.run()` in `observer.py:568` — classifies via deterministic guards (Phase 1-3) or falls through to LLM Observer (Phase 4). Returns decision dict with optional `message_for_user`
3. **Delivery selection**: `job_queue.py:1890` — `delivery_msg = decision.get("message_for_user", msg)` — if Observer provided `message_for_user`, full worker output (`msg`) is discarded
4. **Send callback**: `send_cb(chat_id, delivery_msg, msg_id, agent_session)` → `telegram_bridge.py:1501` `_send()` callback
5. **Response processing**: `send_response_with_files()` in `response.py:334` — calls `summarize_response()` when `is_sdlc or len(text) >= 200`
6. **Summarization**: `summarizer.py:1439` `summarize_response()` → LLM summarizes (Haiku) → `_compose_structured_summary()` adds emoji, stage line, link footer
7. **Output**: Formatted message sent to Telegram

**Root Cause 1 — `message_for_user` short-circuits context**: When the Observer's curated `message_for_user` is short (< 200 chars), `response.py:397` still processes it (because `is_sdlc` forces summarization). The Haiku summarizer receives a pre-summarized string and may further condense it, but `_compose_structured_summary()` should still add stage lines. The real problem is that if `session` is not properly passed or `is_sdlc` isn't detected, the structured formatting is skipped entirely.

**Root Cause 2 — Observer LLM misclassifies SDLC sessions**: The Observer prompt at `observer.py:282-283` says `DELIVER when: ... non-SDLC job`. The LLM reads the `read_session` result which includes `is_sdlc: true`, but may reason that the worker's task is "non-SDLC" based on output content. The prompt doesn't explicitly state that `is_sdlc` from session state is authoritative and must not be overridden. When the Observer treats an SDLC session as non-SDLC, its coaching and message_for_user lack SDLC context, compounding the formatting loss.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Observer prompt hardening**: Make `is_sdlc` authoritative in the Observer's system prompt
- **SDLC template wrapping in job_queue.py**: When delivering SDLC session output, always apply `_compose_structured_summary()` to the `message_for_user` before delivery, as a safety net
- **Deterministic `is_sdlc` injection**: Add explicit SDLC classification to Observer system prompt context so LLM can't misclassify

### Flow

**Observer delivers** → `message_for_user` selected → **SDLC check in job_queue.py** → wrap with `_compose_structured_summary()` → **send_cb** → Telegram (with stage lines + link footer)

### Technical Approach

**Fix 1: Harden Observer prompt (root cause 2)**

In `observer.py`, update `OBSERVER_SYSTEM_PROMPT_BODY` to explicitly state:
```
## CRITICAL: is_sdlc from read_session is AUTHORITATIVE.
Never classify a session as "non-SDLC" if is_sdlc is true.
```

**Fix 2: SDLC template safety net in job_queue.py (root cause 1)**

In `job_queue.py` around line 1890, after selecting `delivery_msg`, if the session is SDLC, ensure the delivery message goes through `_compose_structured_summary()` to add stage progress and link footer. This is a deterministic safety net — even if the summarizer in `response.py` somehow skips formatting, the SDLC template will be applied.

The approach: instead of modifying the existing `send_response_with_files` pipeline (which already handles this when `is_sdlc` is correctly detected), add a lightweight check in `job_queue.py` that verifies the SDLC formatting was applied. If `delivery_msg` is an SDLC session delivery but doesn't contain stage progress markers, wrap it with `_compose_structured_summary()` before sending.

However, the simpler and more reliable approach is: in `job_queue.py`, when the Observer provides `message_for_user` for an SDLC session, pass BOTH the `message_for_user` (as the summary text) and the session to `_compose_structured_summary()` directly in `send_to_chat()`, then pass the composed result to `send_cb`. This way the structured formatting is applied deterministically, regardless of what happens downstream.

**Chosen approach**: Apply `_compose_structured_summary()` in `job_queue.py:send_to_chat()` when:
1. The session is SDLC (`_is_sdlc` is True)
2. The Observer chose `deliver_to_telegram`
3. A `message_for_user` was provided

This wraps the Observer's curated text with the SDLC stage line and link footer before it reaches `send_cb`. The downstream summarizer in `response.py` will detect the already-formatted message and avoid double-formatting (since it will see the stage progress markers already present).

To prevent double-formatting, the delivery path in `response.py` should check if the text already contains SDLC stage progress markers before re-applying `_compose_structured_summary()`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_compose_structured_summary()` already handles missing session gracefully — verify via test
- [ ] Import of `_compose_structured_summary` in `job_queue.py` guarded with try/except

### Empty/Invalid Input Handling
- [ ] Test `_compose_structured_summary("")` with an SDLC session — should produce stage line + link footer even with empty summary text
- [ ] Test `message_for_user` being empty string — falls through to raw msg (existing guard at line 1892)

### Error State Rendering
- [ ] Verify that if `_compose_structured_summary` raises, the original `message_for_user` is still delivered (fallback)

## Test Impact

- [ ] `tests/unit/test_observer_message_for_user.py::TestDeliveryMessageWithGateWarnings` — UPDATE: add tests for SDLC template wrapping when `message_for_user` is provided
- [ ] `tests/unit/test_summarizer.py` — UPDATE: add test for double-formatting prevention (already-formatted text should not get re-wrapped)

## Rabbit Holes

- Refactoring the entire Observer→summarizer pipeline — out of scope, this is a surgical fix
- Changing the Observer's `deliver_to_telegram` tool schema to include SDLC fields — unnecessary complexity
- Adding SDLC template logic inside the Observer LLM itself — the Observer shouldn't format messages

## Risks

### Risk 1: Double-formatting
**Impact:** Stage progress line or link footer appears twice in the message
**Mitigation:** Add a guard in `response.py` that checks if stage progress markers (e.g., `→` pipeline characters) are already present before applying `_compose_structured_summary()`. Also test explicitly.

### Risk 2: Import cycle
**Impact:** Importing `_compose_structured_summary` from `summarizer.py` in `job_queue.py` could create a circular import
**Mitigation:** Use lazy import (import inside the function body), matching existing patterns in the codebase (e.g., `response.py:400`)

## Race Conditions

No race conditions identified — the delivery path is sequential within a single `send_to_chat()` call. The session is re-read from Redis at multiple points (response.py:386, summarizer.py:1389) to get fresh stage data, but these reads are not concurrent with writes in this path.

## No-Gos (Out of Scope)

- Restructuring the Observer agent's tool set
- Changing how non-SDLC messages are formatted
- Modifying the summarizer's Haiku/OpenRouter pipeline
- Adding new fields to the Observer's deliver_to_telegram tool

## Update System

No update system changes required — this is a bridge-internal code change with no new dependencies or config.

## Agent Integration

No agent integration required — this is a bridge-internal change. The Observer agent's tool schema is unchanged; only its system prompt and the delivery wiring in job_queue.py are modified.

## Documentation

- [ ] Update `docs/features/observer-agent.md` to document the SDLC template guarantee (all SDLC deliveries get structured formatting)
- [ ] Add inline code comments explaining the SDLC template safety net in `job_queue.py`

## Success Criteria

- [ ] SDLC session deliveries always include stage progress line and link footers, even when Observer provides `message_for_user`
- [ ] Observer prompt explicitly states `is_sdlc` is authoritative
- [ ] No double-formatting when `message_for_user` goes through both `job_queue.py` wrapping and `response.py` summarization
- [ ] Existing non-SDLC message delivery is unaffected
- [ ] Unit tests cover: Observer `deliver_to_telegram` with `message_for_user` on SDLC session → output includes stage progress
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sdlc-template-fix)**
  - Name: template-builder
  - Role: Implement the SDLC template safety net and Observer prompt fix
  - Agent Type: builder
  - Resume: true

- **Validator (sdlc-template-fix)**
  - Name: template-validator
  - Role: Verify SDLC formatting applied correctly, no double-formatting
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden Observer prompt
- **Task ID**: build-observer-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_observer.py
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `OBSERVER_SYSTEM_PROMPT_BODY` in `bridge/observer.py` to state `is_sdlc` from `read_session` is authoritative
- Add explicit instruction: "Never classify a session as non-SDLC when is_sdlc is true"

### 2. Add SDLC template safety net in job_queue.py
- **Task ID**: build-template-safety-net
- **Depends On**: none
- **Validates**: tests/unit/test_observer_message_for_user.py (update)
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- In `job_queue.py` `send_to_chat()`, after `delivery_msg = decision.get("message_for_user", msg)`, add: if `_is_sdlc` and `message_for_user` was used, wrap `delivery_msg` with `_compose_structured_summary(delivery_msg, session=agent_session)`
- Guard the import with try/except to avoid circular imports
- Add fallback: if `_compose_structured_summary` raises, deliver the original `delivery_msg`

### 3. Add double-formatting prevention
- **Task ID**: build-double-format-guard
- **Depends On**: build-template-safety-net
- **Validates**: tests/unit/test_summarizer.py (update)
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: false
- In `response.py`, before calling `summarize_response()`, check if the text already contains SDLC stage progress markers (the `→` pipeline pattern)
- If already formatted, skip summarization and pass through directly

### 4. Add unit tests
- **Task ID**: build-tests
- **Depends On**: build-template-safety-net, build-double-format-guard
- **Validates**: tests/unit/test_observer_message_for_user.py, tests/unit/test_summarizer.py
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: false
- Add test: SDLC session + `message_for_user` → delivery includes stage progress markers
- Add test: already-formatted SDLC text → not double-formatted by response.py
- Add test: non-SDLC session + `message_for_user` → no SDLC formatting applied

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: template-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/observer-agent.md` with SDLC template guarantee
- Add inline code comments

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_observer_message_for_user.py tests/unit/test_summarizer.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/observer.py bridge/summarizer.py bridge/response.py agent/job_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/observer.py bridge/summarizer.py bridge/response.py agent/job_queue.py` | exit code 0 |
| Observer prompt mentions is_sdlc authoritative | `grep -c 'authoritative\|AUTHORITATIVE' bridge/observer.py` | output > 0 |
| Safety net exists in job_queue | `grep -c '_compose_structured_summary' agent/job_queue.py` | output > 0 |

## Open Questions

No open questions — the root causes are clearly identified and the fix is surgical.
