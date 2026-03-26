---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/552
last_comment_id:
---

# Local Claude Code Session Observability

## Problem

Local Claude Code CLI sessions are invisible on the dashboard. No session tracking, no lifecycle events, no logs in the same format as Telegram-originated sessions. A developer working locally has zero observability parity with agent sessions. Additionally, the memory system has two documented asymmetries between the Claude Code hooks path and the SDK/agent path.

**Current behavior:**
- Local Claude Code sessions create no AgentSession record in Redis
- Dashboard at `localhost:8500` shows only Telegram-originated sessions
- Deja vu signals (vague recognition + novel territory) exist in `memory_bridge.py` but not in `agent/memory_hook.py`
- Post-merge learning extraction has no Claude Code trigger path

**Desired outcome:**
- Local Claude Code sessions create AgentSession records with full lifecycle tracking (pending -> running -> completed/failed)
- Dashboard shows local sessions alongside Telegram sessions without any dashboard code changes
- Memory system capabilities are identical across both paths

## Prior Art

- **Issue #519 / PR #525**: Claude Code memory integration -- established the hooks-based ingest/recall/extract pipeline that this work extends. Shipped successfully.
- **Issue #188 / PR #191**: Back up Claude Code JSONL transcripts to session logs on stop -- established the Stop hook transcript backup pattern we reuse.
- **PR #512**: Job dependency tracking and session observability -- added branch mapping and observability features to AgentSession. Relevant as prior art for session field usage.
- **Issue #459 / PR #490**: SDLC Redesign -- established ChatSession/DevSession discriminator pattern with `create_dev()` factory. Direct foundation for this work.

## Data Flow

### Part 1: AgentSession Lifecycle

1. **Entry point**: User submits a prompt in Claude Code CLI
2. **UserPromptSubmit hook**: Creates AgentSession via `create_local()` factory with `session_type="dev"`, `project_key` derived from cwd, `session_id=f"local-{session_id}"`. Stores `job_id` in sidecar JSON.
3. **PostToolUse hook**: Reads `job_id` from sidecar, updates `last_activity` timestamp and increments `tool_call_count` on the AgentSession.
4. **Stop hook**: Reads `job_id` from sidecar, sets `completed_at`, marks status completed (or failed if `stop_reason` indicates error), writes `log_path`.
5. **Output**: Dashboard `get_all_sessions()` picks up the session automatically -- no dashboard changes needed.

### Part 2: Memory Parity

1. **Deja vu signals**: Port the bloom-hit-but-no-results ("vague recognition") and zero-bloom-hits-many-keywords ("novel territory") logic from `memory_bridge.recall()` to `agent/memory_hook.check_and_inject()`.
2. **Post-merge learning**: Add a call to `extract_post_merge_learning()` from the Stop hook when the session's current branch has been merged (detect via `git log --merges`), or from the PostToolUse hook after a `gh pr merge` command completes.

## Architectural Impact

- **New dependencies**: None -- uses existing Popoto model and sidecar pattern
- **Interface changes**: New `create_local()` factory on AgentSession (parallel to `create_dev()` and `create_chat()`)
- **Coupling**: Hooks gain a new dependency on `models.agent_session` (currently only Stop hook imports it). This is acceptable because the import is lazy and wrapped in try/except.
- **Data ownership**: No change -- AgentSession stays in Redis, sidecar files stay on disk
- **Reversibility**: Fully reversible -- remove the hook additions, local sessions simply stop appearing

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Two distinct work streams (AgentSession lifecycle + memory parity) with moderate integration complexity in the hooks. The hooks are well-understood from PR #525 and the patterns are established.

## Prerequisites

No prerequisites -- this work has no external dependencies. Uses existing Redis, Popoto, and hook infrastructure.

## Solution

### Key Elements

- **AgentSession `create_local()` factory**: New classmethod on AgentSession for local CLI sessions. Minimal required fields: session_id, project_key, working_dir, session_type="dev".
- **Session sidecar extension**: Add `agent_session_job_id` to the existing sidecar JSON file (`data/sessions/{session_id}/`) so PostToolUse and Stop hooks can find the AgentSession record.
- **Deja vu parity in `memory_hook.py`**: Port the two deja vu signal paths from `memory_bridge.recall()` into `agent/memory_hook.check_and_inject()`.
- **Post-merge learning trigger**: Detect `gh pr merge` in PostToolUse Bash tracking and call `extract_post_merge_learning()` from the Stop hook when merge is detected.

### Flow

**Local session start** -> UserPromptSubmit creates AgentSession + stores job_id in sidecar -> **Tool calls** -> PostToolUse updates last_activity + tool_call_count -> **Session end** -> Stop marks completed/failed, runs memory extraction, cleans up sidecar

### Technical Approach

- **Project key resolution**: Reuse `_get_project_key()` from `memory_bridge.py` which reads `VALOR_PROJECT_KEY` env var or falls back to config default. Also attempt to match cwd against `projects.json` for more specific project identification.
- **Session ID format**: Use `local-{claude_session_id}` where `claude_session_id` comes from the hook input's `session_id` field. This avoids collisions with Telegram session IDs.
- **Performance**: AgentSession.save() is a single Redis HSET -- well under the sub-50ms PostToolUse budget. The UserPromptSubmit creation is a one-time cost per session.
- **Idempotency**: UserPromptSubmit may fire multiple times per session. Use sidecar presence check: if `agent_session_job_id` already exists in sidecar, skip creation.
- **Fail-silent**: All AgentSession operations wrapped in try/except with no re-raise, matching existing hook patterns.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] All new AgentSession operations in hooks use `except Exception: pass` -- test that hook completes normally when Redis is unavailable
- [ ] Verify sidecar corruption (invalid JSON) doesn't prevent hook execution

### Empty/Invalid Input Handling
- [ ] UserPromptSubmit with empty session_id still completes without creating AgentSession
- [ ] PostToolUse with missing sidecar file (no prior UserPromptSubmit) skips AgentSession update gracefully
- [ ] Stop hook with missing sidecar skips AgentSession completion gracefully

### Error State Rendering
- [ ] Dashboard renders local sessions without errors (no Telegram-specific fields like chat_id)
- [ ] Sessions with null chat_id, telegram_message_id display correctly on dashboard

## Test Impact

- [ ] `tests/unit/test_memory_bridge.py` -- UPDATE: add tests for deja vu signal emission in recall()
- [ ] `tests/unit/test_memory_hook.py` -- UPDATE: add tests for deja vu signals in check_and_inject()
- [ ] `tests/unit/test_stop_hook.py` -- UPDATE: add test for AgentSession completion on stop
- [ ] `tests/unit/test_dev_session_registration.py` -- UPDATE: add test for create_local() factory

No existing tests broken -- all changes are additive. The updates above add new test cases for new functionality.

## Rabbit Holes

- **Dashboard UI changes for local sessions**: The dashboard already renders all AgentSessions generically. Do not add special UI for local vs Telegram sessions -- the data model handles it.
- **Telegram messaging from local sessions**: Explicitly out of scope. This is pure observability, not I/O.
- **Process-level session detection**: Do not try to detect when Claude Code CLI starts/stops via OS-level process monitoring. The hooks are the correct integration point.
- **Real-time session streaming**: Do not add WebSocket push for live session updates. The dashboard already polls.

## Risks

### Risk 1: Hook latency budget exceeded
**Impact:** PostToolUse hook becomes noticeably slow, degrading CLI experience
**Mitigation:** AgentSession.save() is a single Redis HSET (~1-2ms). Profile in development. If over 10ms, batch updates (update every N tool calls instead of every call).

### Risk 2: Sidecar file race conditions between hooks
**Impact:** Two hooks running simultaneously could corrupt the sidecar JSON
**Mitigation:** Use atomic write pattern (tmp + rename) already established in memory_bridge.py. Hooks run sequentially per Claude Code's hook execution model (not concurrent).

## Race Conditions

No race conditions identified -- Claude Code hooks execute sequentially (one hook completes before the next fires). The sidecar file is the single source of cross-hook state, and atomic writes prevent partial reads.

## No-Gos (Out of Scope)

- Telegram messaging from local sessions
- PM orchestration or steering for local sessions
- Dashboard UI redesign for local session differentiation
- Session resumption or continuation tracking across CLI restarts
- Hook-to-hook in-memory state sharing (hooks are separate processes)

## Update System

No update system changes required -- this feature modifies only hook scripts and model code that are already part of the standard git-pull update path. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a Claude Code hooks-internal change. The hooks modify local files and Redis records. No new MCP server, no `.mcp.json` changes, no bridge modifications. The agent (Telegram path) already has its own session tracking via the bridge.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/claude-code-memory.md` to document AgentSession lifecycle tracking
- [ ] Update `docs/features/subconscious-memory.md` to close the parity gaps in any gap table
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is created

### Inline Documentation
- [ ] Docstrings on `create_local()` factory method
- [ ] Code comments explaining sidecar job_id persistence pattern

## Success Criteria

- [ ] Local Claude Code sessions create an AgentSession record in Redis on first user prompt
- [ ] AgentSession status transitions through pending -> running -> completed/failed during session lifecycle
- [ ] Dashboard at `localhost:8500` shows local Claude Code sessions with correct status, timestamps, and project key
- [ ] `last_activity` updates on each tool call (via PostToolUse hook)
- [ ] Session is marked completed on normal Stop, failed on error Stop
- [ ] Deja vu signals (vague recognition + novel territory) are emitted by SDK agent path (`agent/memory_hook.py`)
- [ ] Post-merge learning extraction is triggered from Claude Code sessions
- [ ] All hook operations fail silently -- never block the CLI session
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hooks)**
  - Name: hooks-builder
  - Role: Implement AgentSession lifecycle in Claude Code hooks and memory parity
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify AgentSession records appear on dashboard and memory parity works end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature documentation for claude-code-memory and subconscious-memory
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `create_local()` factory to AgentSession
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_dev_session_registration.py (update)
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `create_local()` classmethod to `models/agent_session.py` that creates a session with `session_type="dev"`, minimal required fields (session_id, project_key, working_dir), and `status="pending"`
- Ensure null-safe for Telegram-specific fields (chat_id, telegram_message_id, sender_name)

### 2. Wire AgentSession lifecycle into Claude Code hooks
- **Task ID**: build-hooks
- **Depends On**: build-model
- **Validates**: tests/unit/test_stop_hook.py (update), tests/unit/test_memory_bridge.py (update)
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- In `user_prompt_submit.py`: after memory ingest, create AgentSession via `create_local()`, store `job_id` in sidecar
- In `post_tool_use.py`: read `job_id` from sidecar, update `last_activity` and increment `tool_call_count` on AgentSession
- In `stop.py`: read `job_id` from sidecar, mark AgentSession completed/failed based on `stop_reason`, set `completed_at` and `log_path`
- Add sidecar read/write helpers to `hook_utils/constants.py` or `memory_bridge.py`
- All operations wrapped in try/except, fail silently

### 3. Port deja vu signals to SDK agent path
- **Task ID**: build-deja-vu
- **Depends On**: none
- **Validates**: tests/unit/test_memory_hook.py (update)
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/memory_hook.py` `check_and_inject()`: after bloom check, add "vague recognition" path (bloom hits >= threshold but no ContextAssembler results) and "novel territory" path (zero bloom hits, many keywords)
- Use same thresholds as `memory_bridge.py` (import from shared config or define locally)
- Move deja vu threshold constants to `config/memory_defaults.py` for single source of truth

### 4. Add post-merge learning trigger for Claude Code path
- **Task ID**: build-post-merge
- **Depends On**: none
- **Validates**: tests/unit/test_stop_hook.py (update)
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- In `post_tool_use.py`: when `gh pr merge` is detected in Bash command, set a `merge_detected` flag in the SDLC sidecar state
- In `stop.py`: if `merge_detected` flag is set, call `extract_post_merge_learning()` with the merged PR info
- Extract PR number from the merge command via regex

### 5. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-hooks, build-deja-vu, build-post-merge
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify AgentSession records are created in Redis by running a simulated hook sequence
- Verify dashboard renders local sessions without errors
- Verify deja vu signals fire in `check_and_inject()` under correct conditions
- Run full test suite

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/claude-code-memory.md` with AgentSession lifecycle section
- Update `docs/features/subconscious-memory.md` parity notes
- Update `docs/features/README.md` index if needed

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| create_local exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'create_local')"` | exit code 0 |
| Hook imports work | `python -c "from hooks.hook_utils.memory_bridge import recall, ingest, extract"` | exit code 0 |
| Deja vu in memory_hook | `grep -c 'deja\|vague recognition\|novel territory' agent/memory_hook.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue recon resolved all assumptions. The patterns are established by PR #525 (memory hooks) and the AgentSession model already supports the required fields.
