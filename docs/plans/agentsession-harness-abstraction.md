---
status: In Progress
type: refactor
appetite: Large
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/780
last_comment_id:
---

# AgentSession as Harness Abstraction: Dev Sessions via claude -p

## Problem

All agent sessions -- PM, dev, and teammate -- execute through the Claude Agent SDK (`agent/sdk_client.py`), which spawns Claude Code CLI as a subprocess via the Python SDK library. Anthropic plans to enforce API-only billing for programmatic SDK usage, removing the ability to use a flat-rate Max subscription for agentic work. The current architecture tightly couples session execution to the SDK:

- Dev role sessions cannot run by any other harness without changing PM's behavior
- `session_registry.py` (250 lines of in-memory UUID-to-bridge-session mapping) exists solely to let hooks find the right AgentSession record
- `PreToolUse` hook intercepts Agent tool calls to wire PipelineStateMachine -- a side-channel that papers over the SDK abstraction
- `SubagentStop` hook drives SDLC stage transitions and GitHub comment posting -- SDLC completion logic embedded in a hook instead of the worker
- PM sessions use the Agent tool to spawn dev sessions, creating an implicit coupling between PM behavior and the execution substrate

## Solution

Decouple dev session execution from the Claude Agent SDK by making `AgentSession` the harness abstraction layer. The worker decides how to execute each session based on its role; PM sessions create dev sessions as Redis records; the worker routes dev sessions to a new CLI-based harness.

### Phase 1: Worker harness routing (Critical)

Add `_get_response_via_harness()` to `agent/sdk_client.py` that runs `claude -p --output-format stream-json`, streams assistant events to the OutputHandler in real time, and returns final result text.

- [ ] Add `_get_response_via_harness(session, output_handler, working_dir, env)` function in `agent/sdk_client.py`
- [ ] Function runs `claude -p --output-format stream-json` as a subprocess with the session's message as prompt
- [ ] Parse stdout line-by-line: forward `assistant` events (text blocks) to the OutputHandler in real time
- [ ] Capture `result` event's `session_id` field for potential resume support
- [ ] Return final result text extracted from the `result` event
- [ ] No timeout -- dev sessions run as long as they need
- [ ] Pass through working directory, env vars (SDLC context, AGENT_SESSION_ID, etc.)
- [ ] Handle subprocess errors (non-zero exit, stderr) gracefully with logging

### Phase 2: Worker routing by role (Critical)

Modify `_execute_agent_session()` in `agent/agent_session_queue.py` to route dev role sessions to the new harness instead of `get_agent_response_sdk()`.

- [ ] In `_execute_agent_session()`, check `session.session_type` (or `session.role`) for `"dev"`
- [ ] Dev sessions call `_get_response_via_harness()` instead of `get_agent_response_sdk()`
- [ ] PM and teammate sessions continue using `get_agent_response_sdk()` unchanged
- [ ] Harness selection reads `DEV_SESSION_HARNESS` env var (default: `claude-cli`)
- [ ] Build the harness command from the env var: `claude-cli` maps to `claude -p`, `opencode` maps to `opencode`, etc.
- [ ] Pass the registered OutputHandler to the harness function for real-time streaming

### Phase 3: Post-completion SDLC handling in worker (Critical)

Move SDLC stage completion logic from `SubagentStop` hook into the worker's post-completion path for dev sessions.

- [ ] After `_get_response_via_harness()` returns, worker calls `PipelineStateMachine.classify_outcome()` on the output
- [ ] Route to `complete_stage()` or `fail_stage()` based on outcome classification
- [ ] Post structured stage comment to the tracking GitHub issue via `utils.issue_comments.post_stage_comment()`
- [ ] Send a steering message to the parent PM session with pipeline state update (uses `steer_session()`)
- [ ] Extract tracking issue number from the dev session's env vars or parent session
- [ ] All SDLC post-completion logic is in a dedicated `_handle_dev_session_completion(session, result, parent_session)` function

### Phase 4: PM persona update (Critical)

Update the PM persona to create dev sessions via `valor_session create` CLI instead of the Agent tool.

- [ ] Update PM persona file (`~/Desktop/Valor/personas/project-manager.md`) to replace Agent tool dev-session dispatch with `python -m tools.valor_session create --role dev --parent $AGENT_SESSION_ID --message "Stage: BUILD\n..."` 
- [ ] PM uses Bash tool to run the valor_session CLI command
- [ ] PM includes stage assignment, issue URL, and plan context in the `--message` argument
- [ ] PM waits for dev session completion by monitoring steering messages (the worker steers the PM when the dev session finishes)
- [ ] Remove any instructions about using Agent tool with `subagent_type="dev-session"`

### Phase 5: Remove legacy hook wiring (Cleanup)

Delete the code that only existed to support SDK-mediated dev session execution.

- [ ] Delete `agent/hooks/session_registry.py` entirely (250 lines)
- [ ] Remove `_maybe_start_pipeline_stage()` from `agent/hooks/pre_tool_use.py` (the Agent tool dev-session interception path, lines 199-238)
- [ ] Remove `_maybe_register_dev_session` backward-compatible alias from `pre_tool_use.py`
- [ ] Remove the `if tool_name == "Agent"` block from `pre_tool_use_hook()` (lines 256-259)
- [ ] Remove `_register_dev_session_completion()` from `agent/hooks/subagent_stop.py` (lines 55-128)
- [ ] Remove `_record_stage_on_parent()` from `agent/hooks/subagent_stop.py` (lines 131-192)
- [ ] Remove `_post_stage_comment_on_completion()` from `agent/hooks/subagent_stop.py` (lines 283-310)
- [ ] Simplify `subagent_stop_hook()` to only log subagent completion and inject stage state -- no more dev-session-specific logic
- [ ] Remove `session_registry` imports from `pre_tool_use.py`, `subagent_stop.py`, `post_tool_use.py`, `health_check.py`, `messenger.py`
- [ ] Remove `dev-session` entry from `agent/agent_definitions.py` if it exists
- [ ] Keep `_handle_skill_tool_start()` in `pre_tool_use.py` -- Skill tool stage tracking is still needed for PM sessions running skills directly

### Phase 6: Env var harness selection (Enhancement)

Make the harness binary configurable so switching to opencode, Gemini CLI, or any other tool requires no code changes.

- [ ] `DEV_SESSION_HARNESS` env var controls which binary to invoke (default: `claude-cli`)
- [ ] Harness config maps names to command templates: `{"claude-cli": ["claude", "-p", "--output-format", "stream-json"], "opencode": ["opencode", "--non-interactive"]}`
- [ ] Add harness config to `config/` or inline in `sdk_client.py` as a simple dict
- [ ] Validate harness binary exists on PATH at worker startup (log warning if not found)
- [ ] Document supported harness values in `.env.example`

## Success Criteria

- [ ] `agent/sdk_client.py` has `_get_response_via_harness()` that runs `claude -p --output-format stream-json`, streams assistant events to Telegram, and returns final result text
- [ ] Worker routes `AgentSession` with `role="dev"` to `_get_response_via_harness()` instead of `get_agent_response_sdk()`
- [ ] `DEV_SESSION_HARNESS=opencode` causes worker to invoke a different binary with no other code changes
- [ ] `agent/hooks/pre_tool_use.py` no longer contains dev session registration logic (Agent tool interception removed)
- [ ] `agent/hooks/subagent_stop.py` no longer drives SDLC stage transitions (logic moved to worker post-completion handler)
- [ ] `agent/hooks/session_registry.py` deleted
- [ ] PM persona uses `python -m tools.valor_session create --role dev` instead of Agent tool for SDLC stage dispatch
- [ ] SDLC pipeline (plan -> build -> test -> patch -> review -> docs) completes end-to-end with dev role sessions running via `claude -p`
- [ ] Intermediate assistant messages from dev role sessions appear in Telegram in real time during a long-running stage

## No-Gos

- **No changes to the AgentSession model** -- no `harness` field. Harness selection is worker-level config only.
- **No changes to the valor_session CLI interface** -- `--role`, `--parent`, `--message` flags stay as-is.
- **No timeouts on dev sessions** -- they run as long as needed. The existing inactivity watchdog handles stalls.
- **No changes to PM session execution** -- PM stays on the SDK path. Only dev role sessions use the harness.
- **No PTY/pexpect** -- `claude -p` is purpose-built for non-interactive use.
- **No multi-harness support in a single session** -- one harness per worker process via env var.

## Update System

The update script (`scripts/remote-update.sh`) and update skill need minor changes:

- Add `DEV_SESSION_HARNESS` to `.env.example` with default value `claude-cli`
- After update, restart the worker service so the new harness routing takes effect (`valor-service.sh worker-restart`)
- No new dependencies -- `claude -p` is already available on all machines with Claude Code installed
- No migration steps for existing installations -- the default harness (`claude-cli`) matches current behavior

## Agent Integration

- **PM persona update** (Phase 4): The PM persona file must be updated to dispatch dev sessions via `python -m tools.valor_session create` Bash command instead of the Agent tool. This is the only agent-facing change.
- **No new MCP server needed** -- `valor_session.py` CLI is already available and the PM invokes it via Bash tool.
- **No changes to `.mcp.json`** -- no new tools to expose.
- **Bridge is unaffected** -- the bridge only handles I/O routing via the nudge loop and output relay. It has no SDLC awareness and no dev session knowledge.
- **Worker OutputHandler** is already wired -- `TelegramRelayOutputHandler` writes to Redis outbox, bridge relay delivers to Telegram. The harness function just needs to call the same `handler.send()` method.

## Failure Path Test Strategy

### Harness subprocess failure
- Test: `claude -p` exits with non-zero status -> dev session marked failed, parent PM steered with failure message
- Test: `claude -p` binary not found -> clear error log, session marked failed, no crash

### Streaming parse failure
- Test: Malformed JSON line in stdout -> line skipped with warning, processing continues
- Test: Missing `result` event (process killed) -> session marked failed after subprocess exit

### Parent session linkage
- Test: Dev session created with `--parent` -> `parent_agent_session_id` correctly set
- Test: Dev session completes -> parent PM session receives steering message with outcome
- Test: Dev session fails -> parent PM session receives failure steering message

### SDLC stage completion
- Test: Dev session for BUILD stage completes -> PipelineStateMachine.complete_stage("BUILD") called
- Test: Dev session fails -> PipelineStateMachine.fail_stage() called, stage comment posted
- Test: No tracking issue found -> stage comment skipped gracefully (no crash)

### Harness selection
- Test: `DEV_SESSION_HARNESS=claude-cli` -> `claude -p` invoked
- Test: `DEV_SESSION_HARNESS=opencode` -> `opencode` binary invoked
- Test: Unknown harness value -> clear error, session not started

## Test Impact

- [ ] `tests/unit/test_session_registry.py` -- DELETE: session_registry.py is being deleted entirely
- [ ] `tests/unit/test_session_registry_fallback.py` -- DELETE: session_registry.py is being deleted entirely
- [ ] `tests/unit/test_dev_session_registration.py` -- DELETE: dev session registration via PreToolUse hook is being removed
- [ ] `tests/unit/test_pre_tool_use_start_stage.py` -- UPDATE: remove test cases for Agent tool dev-session interception; keep Skill tool stage tracking tests
- [ ] `tests/unit/test_subagent_stop_hook.py` -- UPDATE: remove tests for `_register_dev_session_completion` and `_record_stage_on_parent`; keep basic subagent logging tests
- [ ] `tests/unit/test_post_tool_use_stage_completion.py` -- UPDATE: remove any session_registry imports/usage
- [ ] `tests/unit/test_worker_persistent.py` -- UPDATE: add test cases for dev role routing to harness path
- [ ] `tests/integration/test_parent_child_round_trip.py` -- REPLACE: rewrite to test valor_session create -> worker harness execution -> parent steering flow
- [ ] `tests/e2e/test_session_spawning.py` -- REPLACE: rewrite for new PM -> valor_session -> worker -> harness flow

## Rabbit Holes

- **Resume support via `--resume`**: `claude -p` returns a `session_id` in the result event that can be passed to `--resume` for multi-turn continuity. This is useful but not required for the initial implementation. Defer to a follow-up issue.
- **Streaming format differences across harnesses**: Different CLIs will have different stdout formats. The initial implementation only needs to support `claude -p`'s `stream-json` format. Other harness adapters can be added later.
- **PM monitoring of dev session progress**: The PM currently sees subagent output inline via the Agent tool. With the new model, the PM only gets a steering message on completion. Real-time progress visibility for PM is a separate concern -- the Telegram relay already shows dev output to the human.

## Documentation

- [ ] Create `docs/features/harness-abstraction.md` describing the new execution model: worker routes by role, dev sessions use CLI harness, PM creates dev sessions via valor_session CLI
- [ ] Update `docs/features/pm-dev-session-architecture.md` to reflect the new PM -> valor_session -> worker -> harness flow (replaces PM -> Agent tool -> SDK flow)
- [ ] Update `docs/features/sdlc-stage-handoff.md` to reflect stage completion moving from SubagentStop hook to worker post-completion handler
- [ ] Update `docs/features/bridge-worker-architecture.md` to add harness routing to the worker architecture diagram
- [ ] Update `CLAUDE.md` System Architecture section to reflect the new execution path
- [ ] Add `DEV_SESSION_HARNESS` to `.env.example` with documentation comment

## Critique Results

**Date**: 2026-04-09
**Round**: 1
**Verdict**: NEEDS REVISION -- 1 blocker, 4 concerns, 2 nits

### BLOCKER: send_to_chat / nudge loop incompatible with harness streaming path

**Phases**: 1, 2

The plan says "forward assistant events to the OutputHandler in real time" but `_execute_agent_session()` does not use an OutputHandler directly. It wraps output through a complex `send_to_chat()` closure implementing the nudge loop, auto-continue logic, outbox draining, stop_reason inspection, and `route_session_output()` decision tree (lines 2728-2894 of `agent_session_queue.py`). If `_get_response_via_harness()` calls `handler.send()` directly, it bypasses all delivery logic. If it goes through `send_to_chat()`, the harness needs to produce `stop_reason` values and integrate with `get_stop_reason()`.

**Resolution**: Dev sessions do not need the nudge loop (no auto-continue). The harness function should call `send_cb()` directly for each streamed assistant chunk, bypassing `send_to_chat()` entirely. The plan must explicitly state that harness-routed dev sessions use a separate, simpler output path: stream chunks directly to `send_cb()` during execution, then deliver the final result. The `send_to_chat()` closure and its nudge loop remain unchanged for PM/teammate sessions.

### CONCERN: No rollback strategy between phases

**Phases**: All

Phase 5 deletes `session_registry.py` and removes hook logic. If Phase 4 (PM persona update) has issues in production, reverting requires restoring deleted code.

**Resolution**: Move `DEV_SESSION_HARNESS` env var from Phase 6 to Phase 2. Add an `sdk` value (default) that preserves the current `get_agent_response_sdk()` path. Phases 1-4 become purely additive (no deletions). Phase 5 cleanup only runs after production validation with `DEV_SESSION_HARNESS=claude-cli`.

### CONCERN: PipelineStateMachine lives in bridge/, creates new worker-to-bridge dependency

**Phase**: 3

Phase 3 has the worker call `PipelineStateMachine.classify_outcome()` from `bridge/pipeline_state.py`. This contradicts the stated goal of decoupling worker from bridge. `agent_session_queue.py` line 7 says "This module has no module-level bridge/ imports."

**Resolution**: Move `pipeline_state.py` from `bridge/` to `agent/` (it operates on AgentSession records with no Telegram dependencies). Update the 3 existing import sites as part of Phase 3.

### CONCERN: PM persona file lives outside the repo

**Phase**: 4

`~/Desktop/Valor/personas/project-manager.md` is iCloud-synced, not version-controlled. If out of sync after a git pull on a different machine, PM uses Agent tool dispatch while worker expects valor_session dispatch.

**Resolution**: Add a validation check at PM session startup in `sdk_client.py`: grep the resolved persona prompt for `subagent_type="dev-session"`. If found, log WARNING about stale persona.

### CONCERN: No backpressure or buffering for streamed output

**Phase**: 1

`claude -p --output-format stream-json` can produce hundreds of assistant events. Each triggers a Redis `rpush` via `TelegramRelayOutputHandler.send()`, potentially flooding Telegram with separate messages.

**Resolution**: Buffer assistant text in `_get_response_via_harness()`. Flush to `handler.send()` when: (a) 5 seconds elapsed since last flush, (b) accumulated text exceeds 2000 chars, or (c) a `result` event arrives (final flush).

### NIT: stream-json requires --verbose flag

Verified empirically: `claude -p --output-format stream-json` returns an error without `--verbose`. The command must be `claude -p --verbose --output-format stream-json`. Update Phase 1 task and Phase 6 harness config template.

### NIT: dev-session entry in agent_definitions.py definitely exists

Phase 5 says "if it exists" -- the entry exists at lines 121-131 of `agent/agent_definitions.py`. Change to unconditional deletion.
