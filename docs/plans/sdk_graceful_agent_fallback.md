---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/539
last_comment_id:
---

# SDK Graceful Agent Definition Fallback

## Problem

When an agent definition file (e.g., `.claude/agents/builder.md`) is missing from disk, the SDK client crashes with an unhandled `FileNotFoundError`, killing the entire session. The user receives only "Sorry, I ran into an issue and couldn't recover."

**Current behavior:**
On 2026-03-25, three consecutive user sessions crashed because `builder.md` was not yet synced to the machine. The `_parse_agent_markdown()` function in `agent/agent_definitions.py` calls `path.read_text()` with no error handling. The exception propagates through `get_agent_definitions()` to `_create_options()` in `sdk_client.py`, hitting the top-level catch-all.

**Desired outcome:**
Missing agent definition files produce a logged warning and the agent continues with a minimal fallback prompt. The session degrades gracefully instead of failing completely. Bridge startup surfaces missing files early so operators can fix them before users hit the problem.

## Prior Art

No prior issues or PRs found addressing this failure mode. The closest related pattern is `_load_dev_session_prompt()` (agent_definitions.py:117-135), which already implements the correct graceful fallback with an `.exists()` check and a minimal hardcoded prompt.

## Data Flow

1. **Entry point**: User sends a Telegram message, bridge routes to `sdk_client.query()`
2. **sdk_client._create_options()**: Calls `get_agent_definitions()` to build `ClaudeAgentOptions`
3. **agent_definitions.get_agent_definitions()**: Calls `_parse_agent_markdown()` for each agent (builder, validator, code-reviewer)
4. **_parse_agent_markdown()**: Calls `path.read_text()` -- crashes here if file missing
5. **Exception propagates**: Back through `_create_options()` to `query()` catch-all, returns generic error
6. **Output**: User gets "Sorry, I ran into an issue" instead of a useful response

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `_parse_agent_markdown()` gains error handling; `get_agent_definitions()` return type unchanged (still `dict[str, AgentDefinition]`)
- **Coupling**: No change -- same components, same interfaces
- **Data ownership**: No change
- **Reversibility**: Trivially reversible -- just error handling additions

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single file change for the critical fix, plus one new test file and a bridge startup check. Straightforward pattern already exists in `_load_dev_session_prompt()`.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Graceful fallback in `_parse_agent_markdown()`**: Catch `FileNotFoundError`, log warning, return a minimal agent definition instead of crashing
- **Bridge startup validation**: On bridge start, verify all referenced agent files exist and log warnings for missing ones
- **CI test**: Unit test that verifies agent file paths referenced in `get_agent_definitions()` actually exist on disk

### Flow

**Message arrives** -> `_create_options()` -> `get_agent_definitions()` -> `_parse_agent_markdown()` -> [file missing] -> log warning, return fallback -> session continues with degraded prompt

### Technical Approach

- Refactor `get_agent_definitions()` to wrap each `_parse_agent_markdown()` call in a try/except `FileNotFoundError`, mirroring the pattern in `_load_dev_session_prompt()`
- On fallback, use the agent's description from the function call site (hardcoded minimal description) and an empty prompt body
- Add a `validate_agent_files()` function to `agent/agent_definitions.py` that checks all expected agent files exist and returns a list of missing ones
- Call `validate_agent_files()` during bridge startup in `bridge/telegram_bridge.py`, logging warnings for any missing files
- Add unit test `tests/unit/test_agent_definitions.py` covering: normal load, missing file fallback, and file existence validation

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `FileNotFoundError` catch in `get_agent_definitions()` must log a warning (test asserts `logger.warning` is called with the missing path)
- [ ] The fallback `AgentDefinition` must have a non-empty description and prompt (test asserts returned definition is functional)

### Empty/Invalid Input Handling
- [ ] `_parse_agent_markdown()` with a missing file returns a fallback dict rather than raising
- [ ] `get_agent_definitions()` returns a complete dict even when all agent files are missing (degraded but functional)

### Error State Rendering
- [ ] No user-visible error rendering in this change -- the fix prevents errors from reaching the user

## Test Impact

No existing tests affected -- there are no existing tests for `agent/agent_definitions.py`. The `tests/unit/test_sdk_client.py` and `tests/unit/test_sdk_client_sdlc.py` files do not test `get_agent_definitions()` directly and mock or bypass it in their test setups.

## Rabbit Holes

- Implementing a full agent definition registry with database-backed fallbacks -- a simple hardcoded fallback string is sufficient
- Auto-syncing missing agent files from GitHub -- that is an update system concern, not an SDK client concern
- Refactoring the entire agent definition loading to be lazy/on-demand -- unnecessary complexity for this fix

## Risks

### Risk 1: Degraded prompt quality on fallback
**Impact:** Agent responds without its specialized system prompt, potentially giving lower-quality or less-focused responses
**Mitigation:** The fallback prompt includes the agent's role description. A degraded response is vastly better than a complete session crash. The bridge startup warning surfaces the root cause for quick resolution.

## Race Conditions

No race conditions identified -- all operations are synchronous file reads during session initialization. No concurrent access to agent definition files.

## No-Gos (Out of Scope)

- Auto-downloading or syncing missing agent files from remote
- Caching agent definitions across sessions (premature optimization)
- Restructuring the agent definition format or registry system
- Adding hot-reload for agent definitions

## Update System

No update system changes required -- this is a defensive error-handling improvement within existing code. The update script already syncs `.claude/agents/` files; this fix handles the window between code deployment and file sync.

## Agent Integration

No agent integration required -- this is an internal SDK client change. No new tools, MCP servers, or bridge imports needed. The fix is transparent to the agent.

## Documentation

- [ ] Update `docs/features/README.md` index table with a row for agent definition fallback behavior
- [ ] Add inline code comments in `agent/agent_definitions.py` explaining the fallback pattern and why it exists

No standalone feature doc needed -- this is a small defensive fix, not a user-facing feature.

## Success Criteria

- [ ] `get_agent_definitions()` returns a complete dict even when agent `.md` files are missing from disk
- [ ] Missing agent files produce a `logger.warning()` with the file path
- [ ] Bridge startup logs warnings for any missing agent definition files
- [ ] All existing tests continue to pass
- [ ] New unit tests cover: normal load, single file missing, all files missing, `validate_agent_files()`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (agent-fallback)**
  - Name: fallback-builder
  - Role: Implement graceful fallback in agent_definitions.py, bridge startup validation, and unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (agent-fallback)**
  - Name: fallback-validator
  - Role: Verify fallback behavior works correctly and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add graceful fallback to agent definition loading
- **Task ID**: build-fallback
- **Depends On**: none
- **Validates**: tests/unit/test_agent_definitions.py (create)
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: true
- Refactor `_parse_agent_markdown()` to catch `FileNotFoundError` and return a fallback dict with empty body and a warning log
- Update `get_agent_definitions()` to handle the fallback case, creating minimal `AgentDefinition` instances when files are missing
- Add `validate_agent_files()` function that returns a list of missing agent file paths
- Create `tests/unit/test_agent_definitions.py` with tests for: normal load, missing file fallback, all files missing, validate function

### 2. Add bridge startup validation
- **Task ID**: build-bridge-validation
- **Depends On**: build-fallback
- **Validates**: tests/unit/test_agent_definitions.py::test_validate_agent_files
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `validate_agent_files` in bridge startup code
- Call it during bridge initialization and log warnings for missing files
- Ensure it does not block startup -- warnings only

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-bridge-validation
- **Assigned To**: fallback-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify `get_agent_definitions()` handles missing files without raising
- Verify bridge startup logs warnings for missing agent files
- Confirm all success criteria met

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: fallback-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/README.md` with agent definition fallback entry
- Ensure inline code comments are present

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_definitions.py -x -q` | exit code 0 |
| All tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_definitions.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_definitions.py` | exit code 0 |
| Fallback works | `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); assert 'builder' in d"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the issue is well-defined, the fix pattern already exists in the codebase (`_load_dev_session_prompt`), and the scope is narrow.
