---
status: Done
type: bug
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/678
last_comment_id:
---

# Fix REACT: Emoji Leak as Literal Text

## Problem

When the agent outputs `REACT: 😅`, the literal text `REACT: 😅` is sent as a Telegram message instead of being applied as an emoji reaction on the original message.

**Current behavior:**
The user sees a text message containing `REACT: 😅` in the Telegram chat. No emoji reaction is applied.

**Desired outcome:**
The `😅` emoji is applied as a Telegram reaction on the original message via `set_reaction()`. No text message is sent.

## Prior Art

- **PR #602**: Agent-controlled message delivery — implemented the stop-hook review gate, delivery choices (`REACT:`, `DELIVER:`, `WITHHOLD:`), and classification context. This is the system that *should* handle reactions but has the race condition that causes this bug.

No other closed issues or merged PRs address this specific leak.

## Data Flow

The bug manifests because two independent paths process agent output concurrently:

1. **Entry point**: Agent outputs `REACT: 😅` as its final message
2. **Stop hook** (`agent/hooks/stop.py:155-159`): Parses `REACT: 😅` → writes `delivery_action="react"`, `delivery_emoji="😅"` to `AgentSession` in Redis (`stop.py:187-193`)
3. **Nudge loop** (`agent/agent_session_queue.py:2046-2145`): Independently detects agent output → classifies as "deliver" → calls `send_cb()` with the raw text and a **stale** session object
4. **Send callback** (`bridge/telegram_bridge.py:1684-1696`): Runs `filter_tool_logs()` on the text — but `REACT: 😅` doesn't match the emoji-first pattern (`response.py:175-177`), so it passes through
5. **`send_response_with_files()`** (`bridge/response.py:407-415`): Re-reads session from Redis — but the stop hook may not have saved yet
6. **Delivery check** (`bridge/response.py:421`): `session.delivery_action` is `None` (stale) → falls through to summarizer
7. **Output**: Raw `REACT: 😅` sent as text message to user

**Race window**: Between step 2 (stop hook saves) and step 5 (session re-read). If the nudge loop fires before the stop hook completes its Redis write, the session object has no `delivery_action`.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — only adding a guard in the send callback
- **Coupling**: No change — the fix adds filtering at the callback boundary
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — removing a text filter

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

- **Delivery choice filter**: A pattern matcher that recognizes delivery-choice syntax (`REACT:`, `SEND`, `EDIT:`, `SILENT`, `CONTINUE`) in raw text and suppresses it from being sent as a message
- **Callback-level guard**: Applied in the send callback before `send_response_with_files()` is called, eliminating the race window entirely

### Flow

**Agent outputs REACT: 😅** → nudge loop classifies as "deliver" → **send callback intercepts** → detects delivery-choice pattern → suppresses text message → stop hook writes delivery_action → `send_response_with_files()` handles reaction via delivery_action on a subsequent call (or the callback itself triggers the reaction)

### Technical Approach

The simplest and most robust fix is **defense in depth** at two levels:

1. **`filter_tool_logs()` in `response.py`**: Add a pattern to recognize delivery-choice prefixes (`REACT:`, `EDIT:`, `SILENT`, `SEND`, `CONTINUE`) and filter them out. These are internal agent control signals, never user-facing text. This catches the leak at the earliest text-filtering stage.

2. **Send callback in `telegram_bridge.py`**: After `filter_tool_logs()` returns empty (because the delivery choice was filtered), the `if filtered:` guard at line 1688 already prevents `send_response_with_files()` from being called. No additional code needed here — the existing guard handles it.

This approach is better than synchronization because:
- It's stateless — no timing dependencies
- It's additive — doesn't change the stop hook or delivery system
- The stop hook still writes `delivery_action` and the response system still executes reactions — this fix just prevents the *text leak* through the parallel path

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No new exception handlers introduced. Existing `filter_tool_logs()` has no exception handling (pure string processing).

### Empty/Invalid Input Handling
- [x] Test `filter_tool_logs()` with `"REACT:"` (no emoji) — should filter the line
- [x] Test `filter_tool_logs()` with `"REACT:  "` (whitespace only) — should filter the line
- [x] Test `filter_tool_logs()` with mixed content: `"Hello\nREACT: 😅"` — should filter only the REACT line

### Error State Rendering
- [x] Not applicable — this fix suppresses internal control signals, no user-visible error states

## Test Impact

- [x] `tests/unit/test_delivery_execution.py::test_no_delivery_action_falls_through` — UPDATE: may need adjustment if filter_tool_logs now strips delivery choices before they reach send_response_with_files
- [x] `tests/unit/test_stop_hook_review.py::test_react_with_emoji` — No change needed, tests stop hook parsing which is unmodified

No other existing tests affected — the new filter pattern in `filter_tool_logs()` is additive and doesn't change any existing filtering behavior.

## Rabbit Holes

- **Synchronizing stop hook and nudge loop**: Architecturally complex, fragile timing, and unnecessary — filtering is simpler and more robust
- **Refactoring the entire delivery pipeline**: The pipeline works correctly when delivery_action is set; the only issue is the race window. Don't redesign the pipeline for a filtering fix.
- **Handling all possible agent output formats**: Focus on the known delivery-choice prefixes (`REACT:`, `SEND`, `EDIT:`, `SILENT`, `CONTINUE`). Don't try to build a general-purpose agent instruction parser.

## Risks

### Risk 1: False positive filtering
**Impact:** Legitimate user-facing text starting with "REACT:" gets suppressed
**Mitigation:** The pattern matches only exact delivery-choice syntax (uppercase, at start of line). Agent output intended for users never starts with these control prefixes — they're internal protocol signals defined in the stop hook.

## Race Conditions

### Race 1: Nudge loop sends before stop hook writes delivery_action (THE BUG)
**Location:** `agent/agent_session_queue.py:2145` vs `agent/hooks/stop.py:193`
**Trigger:** Agent completes → nudge loop fires "deliver" → calls send_cb with raw text before stop hook writes delivery_action to Redis
**Data prerequisite:** `session.delivery_action` must be written to Redis before `send_response_with_files()` reads it
**State prerequisite:** Stop hook must complete before nudge loop delivers
**Mitigation:** Filter delivery-choice text in `filter_tool_logs()` so the raw text never reaches `send_response_with_files()`. The reaction itself is still applied when the response system later reads `delivery_action="react"` from the session.

## No-Gos (Out of Scope)

- Refactoring the nudge loop / stop hook synchronization
- Changing the delivery action protocol format
- Adding new delivery action types
- Modifying the stop hook parsing logic

## Update System

No update system changes required — this is a bridge-internal bug fix with no new dependencies or config changes.

## Agent Integration

No agent integration required — this is a bridge-internal change. The fix modifies `filter_tool_logs()` in `response.py`, which is already used by the send callback. No MCP server changes needed.

## Documentation

- [x] Update `docs/features/agent-controlled-delivery.md` (if it exists) to document that delivery-choice prefixes are filtered from raw output as defense-in-depth
- [x] Add inline code comments in `filter_tool_logs()` explaining the delivery-choice filter pattern

## Success Criteria

- [x] Agent output starting with `REACT:` is never sent as literal text to Telegram
- [x] Agent output starting with `SEND`, `EDIT:`, `SILENT`, `CONTINUE` is also filtered
- [x] Emoji reactions are correctly applied via the existing delivery system (no regression)
- [x] `filter_tool_logs("REACT: 😅")` returns empty string
- [x] `filter_tool_logs("Hello\nREACT: 😅")` returns `"Hello"`
- [x] Mixed content with delivery choices filters only the control lines
- [x] All existing delivery execution and stop hook tests pass
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (filter-fix)**
  - Name: filter-builder
  - Role: Add delivery-choice pattern to filter_tool_logs and write tests
  - Agent Type: builder
  - Resume: true

- **Validator (filter-fix)**
  - Name: filter-validator
  - Role: Verify filtering works and no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add delivery-choice filter to filter_tool_logs
- **Task ID**: build-filter
- **Depends On**: none
- **Validates**: tests/unit/test_filter_delivery_choices.py (create), tests/unit/test_delivery_execution.py
- **Assigned To**: filter-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a regex pattern to `filter_tool_logs()` in `bridge/response.py` (~line 178) that matches delivery-choice prefixes: `^(REACT:\s*|SEND\s*$|EDIT:\s*|SILENT\s*$|CONTINUE\s*$)` (case-insensitive)
- Place this check BEFORE the generic emoji+word pattern check (line 194) so it catches text-first patterns
- Create `tests/unit/test_filter_delivery_choices.py` with test cases:
  - `REACT: 😅` → empty string
  - `REACT:` (no emoji) → empty string
  - `SEND` → empty string
  - `EDIT: revised text here` → empty string
  - `SILENT` → empty string
  - `Hello\nREACT: 😅` → `"Hello"`
  - `Regular message about reacting` → unchanged (no false positive)
  - `react: 😅` (lowercase) → empty string (case-insensitive match)
- Run existing tests: `pytest tests/unit/test_delivery_execution.py tests/unit/test_stop_hook_review.py -x`

### 2. Validate fix
- **Task ID**: validate-filter
- **Depends On**: build-filter
- **Assigned To**: filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `filter_tool_logs()` correctly filters all delivery-choice patterns
- Verify no false positives on normal text containing "react" or "send" as substrings
- Verify existing delivery execution tests pass unchanged
- Run full test suite: `pytest tests/unit/ -x -q`

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-filter
- **Assigned To**: filter-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add inline comments in `filter_tool_logs()` explaining the delivery-choice filter
- Check if `docs/features/agent-controlled-delivery.md` exists and update if so

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/response.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/response.py` | exit code 0 |
| Filter REACT | `python -c "from bridge.response import filter_tool_logs; assert filter_tool_logs('REACT: 😅') == ''"` | exit code 0 |
| Filter preserves text | `python -c "from bridge.response import filter_tool_logs; assert 'Hello' in filter_tool_logs('Hello\nREACT: 😅')"` | exit code 0 |
| No false positive | `python -c "from bridge.response import filter_tool_logs; assert 'reacting' in filter_tool_logs('I am reacting to this')"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

None — the fix is straightforward and the root cause is well-understood.
