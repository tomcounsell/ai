---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/625
last_comment_id:
---

# Watchdog Judge Context Enrichment

## Problem

The watchdog health check's Haiku judge receives lossy summaries that make legitimate work look like stuck loops, causing false positive UNHEALTHY verdicts that stall productive sessions.

**Current behavior:**

1. `_summarize_input()` (health_check.py:247-254) drops Read tool's `offset`/`limit` parameters. When Claude reads a large file in chunks, the judge sees `"Read: agent_session_queue.py"` repeated eleven times and concludes the agent is looping -- when it is actually reading different sections.

2. `_get_session_context()` (health_check.py:151-179) only passes `session_type`, first 200 chars of `message_text`, and recent `gh` CLI commands. Missing: tool distribution stats, commit count, total tool call count.

3. `JUDGE_PROMPT` (health_check.py:98-111) has no guidance on common legitimate patterns. Haiku has no way to distinguish chunked reads from stuck loops.

**Observed impact:** Session `tg_valor_-1003449100931_326` working on issue #609 was flagged UNHEALTHY at tool call #20 during setup/recon. The session had already invoked `/do-build`, was in a git worktree, and went on to make 100+ tool calls with real commits -- but the premature verdict caused the nudge loop to deliver instead of auto-continue, stalling the session.

**Desired outcome:**

The judge receives enough context to distinguish productive chunked operations from genuine stuck loops. False positive rate on `/do-build` sessions drops to near zero while still catching actual loops (e.g., retrying the same failing command without changes).

## Prior Art

- **Issue #374**: Observer returns early on continuation sessions due to session cross-wire -- Fixed stale counts from prior sessions via `reset_session_count()`. Same file, different root cause. That fix prevented premature watchdog firing; this fix improves verdict accuracy when the watchdog fires correctly.
- **PR #603**: Fix hook session ID resolution via bridge-level registry -- Solved UUID mapping so the watchdog fires with the correct session ID. Prerequisite work that enables this fix (context is correctly attributed to the right session).
- **PR #512 / Issue #501**: Async job queue with branch-session mapping and session observability -- Added the activity stream JSONL and watchdog infrastructure that this plan builds on.

## Data Flow

1. **Entry point**: PostToolUse hook fires after every tool call in the Claude Code subprocess
2. **`_summarize_input()`**: Extracts tool name + key args into a brief string for logging
3. **`_write_activity_stream()`**: Appends summarized entry to `logs/sessions/{session_id}/activity.jsonl`
4. **At CHECK_INTERVAL (20)**: `_read_recent_activity()` reads last 20 entries from the JSONL transcript
5. **`_get_session_context()`**: Queries AgentSession model for session metadata, extracts gh commands
6. **`JUDGE_PROMPT`**: Formats context + activity into a prompt for Haiku
7. **`_judge_health()`**: Sends prompt to Haiku API, parses JSON verdict
8. **Output**: If unhealthy, sets `AgentSession.watchdog_unhealthy` flag and injects STOP directive

The lossy information happens at steps 2 (summarization drops offset/limit), 5 (context lacks statistics), and 6 (prompt lacks pattern guidance). All three are in `agent/health_check.py`.

## Architectural Impact

- **New dependencies**: None -- all data comes from the existing activity stream JSONL and git CLI
- **Interface changes**: None -- `_summarize_input()`, `_get_session_context()`, and `JUDGE_PROMPT` are internal to `health_check.py`
- **Coupling**: No change -- stays within health_check.py
- **Data ownership**: No change
- **Reversibility**: Trivially reversible -- all changes are string formatting in a single file

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (clear scope from issue)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are internal to `agent/health_check.py` and its test file.

## Solution

### Key Elements

- **Enriched tool summaries**: `_summarize_input()` includes offset/limit for Read, old_string length for Edit, so chunked operations are distinguishable
- **Session statistics**: `_get_session_context()` computes tool distribution, commit count, and total tool call count from the activity stream
- **Pattern-aware judge prompt**: `JUDGE_PROMPT` includes guidance on legitimate patterns (chunked reads, read-edit cycles, setup phases, high-count build sessions)

### Flow

**Tool call fires** -> `_summarize_input()` captures offset/limit -> activity stream logged -> at interval 20 -> `_get_session_context()` computes stats from activity stream -> `JUDGE_PROMPT` formats enriched context + pattern guidance -> Haiku renders accurate verdict

### Technical Approach

**Change 1: `_summarize_input()` (lines 247-254)**

For Read tool: append `[offset=N, limit=N]` when those parameters are present in tool_input. For Edit tool: append `[old_string len=N]` to indicate edit size context.

```
# Before: "Read: agent_session_queue.py"
# After:  "Read: agent_session_queue.py [offset=200, limit=100]"
# After:  "Edit: agent_session_queue.py [old_string len=45]"
```

**Change 2: `_get_session_context()` (lines 151-179)**

Read the full activity stream JSONL and compute:
- Tool distribution: count by tool name (e.g., "5 Read, 3 Grep, 12 Edit")
- Total tool call count from `_tool_counts[session_id]`
- Commit count via counting Bash entries containing "git commit" in the activity stream (no subprocess call needed)

Format as a compact stats block appended to the existing context string.

**Change 3: `JUDGE_PROMPT` (lines 98-111)**

Add a "Common legitimate patterns" section between the numbered criteria and the activity log. Keep it under ~100 tokens to avoid bloating the prompt:
- Chunked reads of the same file with different offsets are normal for large files
- Read-then-edit cycles on the same file are productive
- Setup phases (ToolSearch, Skill) look repetitive but are one-time operations
- Sessions with commits in the activity log are making real progress

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_get_session_context()` has a top-level `except Exception: return ""` (line 178) -- existing test `test_no_session_returns_empty` covers the empty path; new tests will verify stats computation returns gracefully on malformed JSONL

### Empty/Invalid Input Handling
- [ ] `_summarize_input()` for Read with empty/missing offset/limit -- should fall back to path-only (current behavior preserved)
- [ ] `_get_session_context()` with empty activity stream -- should return context without stats block
- [ ] `_get_session_context()` with malformed JSONL lines -- should skip bad lines gracefully

### Error State Rendering
- [ ] Not applicable -- no user-visible output. The judge prompt is only seen by Haiku.

## Test Impact

- [ ] `tests/unit/test_health_check.py::TestSummarizeInput::test_read_returns_path` -- UPDATE: assert includes offset/limit when present, and still returns path-only when absent
- [ ] `tests/unit/test_health_check.py::TestGetSessionContext::test_returns_context_with_session_type` -- UPDATE: assert context includes tool distribution stats
- [ ] `tests/unit/test_health_check.py::TestGetSessionContext::test_handles_none_fields` -- UPDATE: verify stats block is still generated even with None session fields
- [ ] `tests/unit/test_health_check.py::TestJudgePromptEnrichment::test_prompt_formats_with_context` -- UPDATE: verify pattern guidance text appears in formatted prompt

## Rabbit Holes

- **Increasing CHECK_INTERVAL beyond 20** -- Would delay real loop detection. Better to improve context quality at the current interval.
- **Reading activity history beyond 20 calls** -- Would bloat the Haiku prompt. Tool statistics are a better signal than raw history.
- **Adding subprocess calls (git log) in `_get_session_context()`** -- The issue explicitly says "Do NOT add new API calls". Parse commit signals from the existing activity stream instead.
- **Trying to detect SDLC phase from AgentSession fields** -- Adds coupling to the SDLC pipeline. Tool distribution and commit count are sufficient phase signals.

## Risks

### Risk 1: Prompt token budget
**Impact:** If the enriched prompt exceeds ~500 tokens, Haiku's JSON compliance may degrade, returning malformed verdicts.
**Mitigation:** Keep pattern guidance under ~100 tokens. Test the fully formatted prompt to verify it stays under budget.

### Risk 2: Activity stream parsing performance
**Impact:** If the activity stream JSONL grows very large (1000+ lines), reading the full file for stats could add latency.
**Mitigation:** Only read the full file at CHECK_INTERVAL boundaries (every 20 calls), not every call. The file read is I/O-bound and fast for typical session sizes (< 200 lines).

## Race Conditions

No race conditions identified -- `_get_session_context()` and `_summarize_input()` are synchronous, read-only functions called from the PostToolUse hook which fires sequentially per session.

## No-Gos (Out of Scope)

- Changing CHECK_INTERVAL (20) -- explicitly constrained in the issue
- Adding new API calls or subprocess calls in `_get_session_context()`
- Modifying the nudge loop or any code outside `agent/health_check.py`
- Changing the activity stream JSONL format (consumers depend on current schema)
- Adding SDLC phase detection or task list state to the context

## Update System

No update system changes required -- all changes are internal to `agent/health_check.py` with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a hook-internal change. The watchdog fires automatically as a PostToolUse hook; no MCP server, bridge import, or tool registration changes are needed.

## Documentation

- [ ] Update `docs/features/session-health-check.md` (if it exists) or create it to document the enriched context signals the watchdog uses
- [ ] Update docstrings on `_summarize_input()`, `_get_session_context()`, and `JUDGE_PROMPT` in `agent/health_check.py`

## Success Criteria

- [ ] `_summarize_input()` includes `offset`/`limit` for Read tool and `old_string` length for Edit tool
- [ ] `_get_session_context()` includes tool call distribution statistics from the activity stream
- [ ] `_get_session_context()` includes total tool call count and commit count
- [ ] `JUDGE_PROMPT` includes guidance on common legitimate patterns (chunked reads, read-edit cycles, setup phases)
- [ ] Existing tests in `tests/unit/test_health_check.py` updated to cover new summary fields
- [ ] All tests pass (`/do-test`)
- [ ] Lint and format clean (`ruff check .` and `ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (health-check)**
  - Name: health-check-builder
  - Role: Implement all three changes in health_check.py and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (health-check)**
  - Name: health-check-validator
  - Role: Verify enriched summaries, context stats, and prompt guidance
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Enrich _summarize_input()
- **Task ID**: build-summarize
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py::TestSummarizeInput
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_summarize_input()` (health_check.py:247-254), modify the Read/Write/Edit branch:
  - For Read: extract `offset` and `limit` from `tool_input` and append `[offset=N, limit=N]` when present
  - For Edit: extract `old_string` length and append `[old_string len=N]` when present
  - For Write: keep path-only (no additional context needed)
- Update `TestSummarizeInput::test_read_returns_path` to test both with and without offset/limit
- Add `test_read_with_offset_limit` testing Read with offset/limit parameters
- Add `test_edit_with_old_string_length` testing Edit with old_string parameter

### 2. Enrich _get_session_context()
- **Task ID**: build-context
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py::TestGetSessionContext
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_get_session_context()` (health_check.py:151-179), after the existing gh_commands block:
  - Read the full activity stream JSONL for this session
  - Count tool calls by tool name and format as "Tool distribution: N Read, N Edit, ..."
  - Count entries where tool is "Bash" and args contain "git commit" for commit count
  - Get total tool count from `_tool_counts.get(session_id, 0)`
  - Append stats block to the context string
- Add helper `_compute_activity_stats(session_id)` that returns a dict with tool_distribution, commit_count, total_tool_count
- Update existing tests to assert stats appear in context
- Add `test_context_includes_tool_distribution` with a mocked activity stream

### 3. Improve JUDGE_PROMPT
- **Task ID**: build-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py::TestJudgePromptEnrichment
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: true
- Add pattern guidance section to `JUDGE_PROMPT` (health_check.py:98-111) between criteria list and activity log
- Guidance must cover: chunked reads with different offsets, read-then-edit cycles, setup tool calls (ToolSearch/Skill), sessions with commits
- Keep guidance under ~100 tokens
- Update `test_prompt_formats_with_context` to verify pattern guidance text appears

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-summarize, build-context, build-prompt
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_health_check.py -v`
- Run lint: `python -m ruff check agent/health_check.py tests/unit/test_health_check.py`
- Run format check: `python -m ruff format --check agent/health_check.py tests/unit/test_health_check.py`
- Verify JUDGE_PROMPT total length stays under ~500 tokens
- Verify all success criteria are met

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: health-check-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update docstrings on `_summarize_input()`, `_get_session_context()`, and `JUDGE_PROMPT`
- Create or update `docs/features/session-health-check.md` if needed

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_health_check.py -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/health_check.py tests/unit/test_health_check.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/health_check.py tests/unit/test_health_check.py` | exit code 0 |
| Read summary includes offset | `python -c "from agent.health_check import _summarize_input; r = _summarize_input('Read', {'file_path': '/f.py', 'offset': 100, 'limit': 50}); assert 'offset=100' in r and 'limit=50' in r, r"` | exit code 0 |
| Context includes stats | `python -c "from agent.health_check import JUDGE_PROMPT; assert 'legitimate' in JUDGE_PROMPT.lower() or 'pattern' in JUDGE_PROMPT.lower(), JUDGE_PROMPT"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue provides clear scope, constraints, and acceptance criteria. All three changes are well-defined and scoped to a single file.
