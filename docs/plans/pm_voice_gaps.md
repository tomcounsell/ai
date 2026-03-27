---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/571
last_comment_id:
---

# PM Voice Gaps: Implementation Details Leak and Dual Messages

## Problem

Two gaps remain from the PM voice refinement work (#540, PR #548):

1. **Implementation details leak into PM output.** The `SUMMARIZER_SYSTEM_PROMPT` suppresses developer metrics (line counts, test numbers) but has no rule banning root-cause explanations or internal code references. Result: PM channels receive messages like "Root cause was lazy deserialization not populating the key tracking fields. Fix: eagerly decode only KeyField values during `_create_lazy_model`..." — meaningless to stakeholders.

2. **Dual messages in SDLC flows.** The pm_bypass guard in `bridge/response.py:439` checks `session.has_pm_messages()`, but in SDLC flows `session` is the **DevSession** (child). The PM's self-authored messages are recorded on the **ChatSession** (parent) via `pm_sent_message_ids`. The DevSession has no PM messages, so the guard never fires — producing two messages for the same result (one from PM, one from summarizer).

**Current behavior:**
- PM channels show root-cause explanations with internal method/class names
- SDLC results produce two messages: PM self-message + summarizer bullet list

**Desired outcome:**
- PM output describes WHAT was fixed and the outcome, never HOW the code works internally
- One coherent message per SDLC result in PM channels

## Prior Art

- **Issue #540 / PR #548**: PM voice refinement — addressed 8 items including metrics suppression and dual-personality guard. The two gaps in this issue were missed: the metrics rule was too narrow (metrics only, not implementation details), and the dual-personality guard was tested with a single session but not with the ChatSession+DevSession split used in SDLC flows.
- **Issue #497**: PM self-messaging via `send_telegram` tool — established the `pm_sent_message_ids` tracking mechanism and the bypass guard. The guard logic is correct for non-SDLC sessions where a single session sends both PM messages and agent output.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #548 (item 6) | Added "DEVELOPER METRICS SUPPRESSION" rule to suppress line/file/test counts | Rule scope too narrow — only targets numeric metrics, not root-cause explanations or internal code references |
| PR #548 (item 7) | Added pm_bypass guard checking `session.has_pm_messages()` | Guard checks the DevSession, but PM messages are recorded on the parent ChatSession. SDLC flows always pass a DevSession to `send_response_with_files`, so the guard is never triggered. |

**Root cause pattern:** Both fixes were tested against single-session scenarios but not against the ChatSession→DevSession parent-child architecture used in SDLC flows.

## Data Flow

1. **Entry point**: Human sends message → ChatSession (PM persona) processes it
2. **ChatSession**: PM sends its own message via `send_telegram` tool → `pm_sent_message_ids` recorded on ChatSession's AgentSession
3. **ChatSession**: PM spawns DevSession for coding work → DevSession has `parent_chat_session_id` pointing to ChatSession
4. **DevSession**: Completes work, returns text output
5. **Bridge**: Calls `send_response_with_files(client, event, response, session=dev_session)` — passes the **DevSession** as `session`
6. **Guard check** (line 439): `dev_session.has_pm_messages()` → always False → guard does not fire
7. **Summarizer**: Runs on DevSession output, produces bullet summary including implementation details
8. **Output**: Two messages appear in PM channel — the PM's self-message and the summarizer's bullet list

**Fixed flow (after this change):**
- Step 6: Guard also checks `dev_session.get_parent_chat_session().has_pm_messages()` → True → guard fires, summarizer skipped
- Step 7: `SUMMARIZER_SYSTEM_PROMPT` bans implementation details even if summarizer runs for non-SDLC paths

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

- **Summarizer prompt hardening**: Extend the "DEVELOPER METRICS SUPPRESSION" section to also ban root-cause explanations, internal method/class/function names, architectural component names, and deserialization/serialization details
- **PM bypass parent lookup**: In the pm_bypass guard, if the session has a `parent_chat_session_id`, look up the parent ChatSession and check `has_pm_messages()` on it

### Flow

**DevSession completes** → `send_response_with_files(session=dev_session)` → Guard checks dev_session AND parent ChatSession for PM messages → If either has PM messages, skip summarizer → If summarizer runs, hardened prompt strips implementation details

### Technical Approach

- Rename "DEVELOPER METRICS SUPPRESSION" to "DEVELOPER INTERNALS SUPPRESSION" in `bridge/summarizer.py` and add explicit prohibitions on: root-cause explanations, internal method/function/class names, deserialization/serialization logic, architectural component names, and code line references
- In `bridge/response.py` around line 439, expand the pm_bypass logic: if `session` has `parent_chat_session_id`, call `session.get_parent_chat_session()` and check `has_pm_messages()` on the parent. The `get_parent_chat_session()` method already exists on `AgentSession` (line 368 of `models/agent_session.py`).
- Add two new tests to `tests/unit/test_summarizer.py`: one verifying the parent-session bypass fires, and one verifying implementation detail patterns are suppressed in summarizer output

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `get_parent_chat_session()` already handles missing parents gracefully (returns None). The bypass code must handle None without crashing — test that a DevSession with a dangling `parent_chat_session_id` does not bypass (falls through to summarizer).

### Empty/Invalid Input Handling
- [ ] If `parent_chat_session_id` is set but empty string, `get_parent_chat_session()` returns None — no special handling needed.

### Error State Rendering
- No user-visible error states — both changes are internal to the output pipeline.

## Test Impact

- [ ] `tests/unit/test_summarizer.py::TestSummarizerBypass::test_bypass_when_pm_has_messages` — UPDATE: Still valid but add a parallel test for the parent-session case
- [ ] `tests/unit/test_summarizer.py::TestSummarizerBypass::test_no_bypass_when_no_pm_messages` — UPDATE: Ensure mock session has no `parent_chat_session_id` to confirm it doesn't accidentally trigger parent lookup

No other existing tests affected — the prompt change is additive (new suppression rule alongside existing one) and does not alter any function signatures or return values.

## Rabbit Holes

- Do not redesign the ChatSession/DevSession architecture or message routing — only fix the guard check
- Do not add infrastructure for deduplicating messages (Redis-based dedup, message fingerprinting) — the guard fix is sufficient
- Do not attempt to change which session object is passed to `send_response_with_files` — that would require bridge-wide refactoring

## Risks

### Risk 1: Over-aggressive suppression strips useful context from PM output
**Impact:** PM messages become too vague to be actionable ("Fixed a bug" instead of "Fixed the KeyField migration bug that caused data loss on save")
**Mitigation:** The prompt rule targets *how the code works internally* (root causes, method names, deserialization logic) — not *what was fixed* (feature names, user-visible behaviors). The distinction between "what" and "how" is well-established in the existing prompt. Test with examples from real PM channel output.

### Risk 2: Parent session lookup adds latency or fails silently
**Impact:** Redis query for parent session could add latency to every DevSession response delivery
**Mitigation:** `get_parent_chat_session()` is a single Redis GET by key (O(1)), already used elsewhere. If it fails, it returns None and the guard falls through to the summarizer — same as current behavior.

## Race Conditions

No race conditions identified — the pm_bypass check reads `pm_sent_message_ids` after the DevSession has completed and the ChatSession has already sent its PM messages. The `send_response_with_files` call happens in the bridge event loop (single-threaded), and the parent ChatSession's `pm_sent_message_ids` are written synchronously before the DevSession is spawned or during the ChatSession's execution (before DevSession completes).

## No-Gos (Out of Scope)

- Do not change the dual-personality architecture (ChatSession + DevSession)
- Do not modify `get_parent_chat_session()` or `has_pm_messages()` — they work correctly
- Do not add new fields to AgentSession
- Do not change how `send_telegram` tool records messages

## Update System

No update system changes required — this is a bridge-internal change affecting only prompt text and guard logic. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. No MCP server changes, no `.mcp.json` updates, no new tools. The fix modifies the summarizer prompt (text) and the response delivery guard (bridge code).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-telegram-tool.md` — add a note about the parent-session bypass in the "Fallback Behavior" section

### Inline Documentation
- [ ] Update the comment block at `bridge/response.py:433-438` to document the parent-session lookup

## Success Criteria

- [ ] `SUMMARIZER_SYSTEM_PROMPT` contains an explicit rule banning root-cause explanations and internal method/class/function names
- [ ] A test verifies that summarizer output containing root-cause language does not leak implementation details
- [ ] The pm_bypass guard fires when a parent ChatSession has PM messages, even when called with a DevSession
- [ ] A test verifies the parent-session bypass (DevSession with parent that has PM messages → bypass fires)
- [ ] A test verifies that a DevSession without a parent (or with a dangling parent) does not bypass
- [ ] Existing summarizer tests continue to pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-fixes)**
  - Name: bridge-builder
  - Role: Implement prompt hardening and guard fix
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: bridge-validator
  - Role: Verify both fixes work correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden Summarizer Prompt
- **Task ID**: build-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py (update)
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename "DEVELOPER METRICS SUPPRESSION" to "DEVELOPER INTERNALS SUPPRESSION" in `bridge/summarizer.py`
- Add explicit prohibition on: root-cause explanations, internal method/function/class names, architectural component names (e.g., `_create_lazy_model`, `KeyField`), deserialization/serialization logic, and code line references
- Keep the existing metrics suppression rules (line counts, file counts, test numbers)
- Add a test in `tests/unit/test_summarizer.py` that passes implementation-detail-heavy text through the summarizer and verifies the output does not contain internal code references

### 2. Fix PM Bypass Guard for Parent Session
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py (update)
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/response.py` around line 439, expand the pm_bypass logic:
  ```python
  pm_bypass = session and hasattr(session, "has_pm_messages") and session.has_pm_messages()
  if not pm_bypass and session and hasattr(session, "get_parent_chat_session"):
      parent = session.get_parent_chat_session()
      if parent and hasattr(parent, "has_pm_messages") and parent.has_pm_messages():
          pm_bypass = True
  ```
- Update the log message to indicate whether bypass was triggered by the session or parent
- Add test: DevSession with parent ChatSession that has PM messages → bypass fires
- Add test: DevSession with dangling parent_chat_session_id → bypass does not fire (falls through)

### 3. Update Documentation
- **Task ID**: document-feature
- **Depends On**: build-prompt, build-guard
- **Assigned To**: bridge-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-telegram-tool.md` Fallback Behavior section to document parent-session lookup
- Update inline comments at `bridge/response.py:433-438`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-prompt, build-guard, document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_summarizer.py -x -q` — all tests pass
- Run `python -m ruff check bridge/summarizer.py bridge/response.py` — lint clean
- Verify the "DEVELOPER INTERNALS SUPPRESSION" section exists in the prompt
- Verify the parent-session lookup exists in the guard

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_summarizer.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/summarizer.py bridge/response.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/summarizer.py bridge/response.py` | exit code 0 |
| Prompt updated | `grep -c "DEVELOPER INTERNALS SUPPRESSION" bridge/summarizer.py` | output contains 1 |
| Guard updated | `grep -c "get_parent_chat_session" bridge/response.py` | output contains 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the issue is well-scoped with confirmed recon findings, the fix paths are clear, and the existing `get_parent_chat_session()` method is already tested.
