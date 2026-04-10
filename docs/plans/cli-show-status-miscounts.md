---
slug: cli-show-status-miscounts
status: planned
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/869
---

# CLI Show Status Miscounts

## Problem

`python -m agent.agent_session_queue --status` prints a summary line at the bottom with per-status counts (e.g., `Total: 12 sessions (3 pending, 2 running, 7 completed)`). The counts are wrong whenever sessions span multiple statuses because line 3715 uses `session.status` (a variable leaked from a prior loop) instead of the loop variable `entry.status`. Every session gets counted under whatever status the last session of the last chat happened to have.

## Appetite

Tiny (< 15 minutes). Single-line fix plus one regression test.

## Solution

Change both occurrences of `session.status` on `agent/agent_session_queue.py:3715` to `entry.status`.

Add a `test_summary_counts_mixed_status` test case in `tests/unit/test_agent_session_status_cli.py` that constructs mock sessions across multiple statuses and multiple chats, calls `_cli_show_status()`, and asserts the summary line counts match the input.

## Step by Step Tasks

- [ ] Fix `agent/agent_session_queue.py:3715`: change `session.status` to `entry.status` (both occurrences on that line).
- [ ] Add `test_summary_counts_mixed_status` to `tests/unit/test_agent_session_status_cli.py` covering at least 3 statuses across 2+ chats.
- [ ] Run `pytest tests/unit/test_agent_session_status_cli.py` to verify the new test passes.
- [ ] Run `python -m ruff check agent/agent_session_queue.py tests/unit/test_agent_session_status_cli.py` to confirm lint.

## Success Criteria

- Line 3715 reads `status_counts[entry.status] = status_counts.get(entry.status, 0) + 1`.
- New test constructs sessions with mixed statuses across multiple chats and asserts the summary line reflects correct per-status counts.
- New test would fail against the pre-fix code (verified by the test design: distinct statuses must appear separately in summary).

## Risks

None. The fix is a variable rename with no behavioral ambiguity.

## No-Gos

- Do not refactor `_cli_show_status()` beyond the one-line fix.
- Do not scan for other leaked-loop-variable patterns in the file (out of scope for this issue).
- Do not modify existing test cases.

## Update System

No update system changes required -- this is an internal CLI bug fix with no deployment or dependency implications.

## Agent Integration

No agent integration required -- `_cli_show_status()` is an operator-facing CLI function not exposed through MCP or the agent tool layer.

## Failure Path Test Strategy

The regression test directly exercises the failure path: it feeds sessions with distinct statuses and asserts each status appears with its correct count. If the bug regresses, the test fails because all counts collapse to one status.

## Test Impact

- [ ] `tests/unit/test_agent_session_status_cli.py::test_shows_sessions` -- no change needed; it uses a single status and would not catch this bug regardless.

No existing tests affected -- the new test is additive. The existing two test cases (`test_empty_queue`, `test_shows_sessions`) remain valid as-is.

## Rabbit Holes

- Auditing all loop variables in `agent_session_queue.py` for similar leaks. Worth doing separately but not in this fix.
- Adding end-to-end testing against a live Redis queue. Out of scope.

## Documentation

No documentation changes needed -- this is a bug fix to an internal CLI utility with no user-facing documentation.
