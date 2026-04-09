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

Add `_get_response_via_harness()` to `agent/sdk_client.py` that runs `claude -p --verbose --output-format stream-json`, streams assistant events directly to the Telegram send callback in real time (bypassing the nudge loop entirely), and returns final result text.

- [ ] Add `_get_response_via_harness(session, send_cb, working_dir, env)` function in `agent/sdk_client.py`
- [ ] Function runs `claude -p --verbose --output-format stream-json` as a subprocess with the session's message as prompt
- [ ] Parse stdout line-by-line: forward `assistant` events (text blocks) directly to `send_cb()` -- NOT through `send_to_chat()` or the nudge loop
- [ ] **Output bypass**: Dev sessions do not need auto-continue, nudge loop, stop_reason inspection, or `route_session_output()`. The harness streams chunks directly to `send_cb()` (the raw Telegram delivery callback) during execution, then delivers the final result. The `send_to_chat()` closure and its nudge loop remain unchanged for PM/teammate sessions only.
- [ ] **Time/size-based batching**: Buffer assistant text chunks in `_get_response_via_harness()`. Flush to `send_cb()` when: (a) 3 seconds elapsed since last flush, (b) accumulated text exceeds 2000 chars, or (c) a `result` event arrives (final flush). This prevents flooding Telegram with hundreds of small messages.
- [ ] Capture `result` event's `session_id` field for potential resume support
- [ ] Return final result text extracted from the `result` event
- [ ] No timeout -- dev sessions run as long as they need
- [ ] Pass through working directory, env vars (SDLC context, AGENT_SESSION_ID, etc.)
- [ ] Handle subprocess errors (non-zero exit, stderr) gracefully with logging

### Phase 2: Worker routing by role (Critical)

Modify `_execute_agent_session()` in `agent/agent_session_queue.py` to route dev role sessions to the new harness instead of `get_agent_response_sdk()`. Introduce the `DEV_SESSION_HARNESS` env var here (moved from Phase 6) with `sdk` as the default, preserving current behavior until explicitly switched.

- [ ] In `_execute_agent_session()`, check `session.session_type` (or `session.role`) for `"dev"`
- [ ] Harness selection reads `DEV_SESSION_HARNESS` env var (default: `sdk` -- preserves current `get_agent_response_sdk()` path)
- [ ] When `DEV_SESSION_HARNESS=sdk` (default): dev sessions use `get_agent_response_sdk()` unchanged (rollback-safe)
- [ ] When `DEV_SESSION_HARNESS=claude-cli`: dev sessions call `_get_response_via_harness()` with `send_cb()` (the raw Telegram callback, NOT `send_to_chat()`)
- [ ] PM and teammate sessions always use `get_agent_response_sdk()` regardless of env var
- [ ] Build the harness command from the env var: `claude-cli` maps to `claude -p --verbose`, `opencode` maps to `opencode`, etc.
- [ ] Pass `send_cb()` directly to the harness function -- dev sessions bypass the nudge loop and auto-continue logic entirely

### Phase 3: Post-completion SDLC handling in worker (Critical)

Move SDLC stage completion logic from `SubagentStop` hook into the worker's post-completion path for dev sessions.

- [ ] **Move `pipeline_state.py` from `bridge/` to `agent/`** -- it operates on AgentSession records with no Telegram dependencies, so it belongs in `agent/`. Update the 3 existing import sites (`bridge/telegram_bridge.py`, `agent/hooks/pre_tool_use.py`, `agent/hooks/subagent_stop.py`) to import from `agent.pipeline_state`.
- [ ] After `_get_response_via_harness()` returns, worker calls `PipelineStateMachine.classify_outcome()` on the output (now from `agent.pipeline_state`)
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
- [ ] **Startup validation**: Add a check in `sdk_client.py` PM session startup path that greps the resolved persona prompt for `subagent_type="dev-session"`. If found, log `WARNING: PM persona still contains Agent tool dispatch instructions -- update ~/Desktop/Valor/personas/project-manager.md`. This guards against stale persona files on machines that pulled code but did not update the iCloud-synced persona.

### Phase 5: Remove legacy hook wiring (Cleanup -- gated on production validation)

**Gate**: Phase 5 cleanup ONLY proceeds after successful production runs with `DEV_SESSION_HARNESS=claude-cli`. The rollback path is to set `DEV_SESSION_HARNESS=sdk` (default), which routes dev sessions through the existing `get_agent_response_sdk()` path. Because Phases 1-4 are purely additive (no deletions), reverting to `sdk` requires zero code changes.

- [ ] Delete `agent/hooks/session_registry.py` entirely (250 lines)
- [ ] Remove `_maybe_start_pipeline_stage()` from `agent/hooks/pre_tool_use.py` (the Agent tool dev-session interception path, lines 199-238)
- [ ] Remove `_maybe_register_dev_session` backward-compatible alias from `pre_tool_use.py`
- [ ] Remove the `if tool_name == "Agent"` block from `pre_tool_use_hook()` (lines 256-259)
- [ ] Remove `_register_dev_session_completion()` from `agent/hooks/subagent_stop.py` (lines 55-128)
- [ ] Remove `_record_stage_on_parent()` from `agent/hooks/subagent_stop.py` (lines 131-192)
- [ ] Remove `_post_stage_comment_on_completion()` from `agent/hooks/subagent_stop.py` (lines 283-310)
- [ ] Simplify `subagent_stop_hook()` to only log subagent completion and inject stage state -- no more dev-session-specific logic
- [ ] Remove `session_registry` imports from `pre_tool_use.py`, `subagent_stop.py`, `post_tool_use.py`, `health_check.py`, `messenger.py`
- [ ] Delete `dev-session` entry from `agent/agent_definitions.py` (lines 121-131)
- [ ] Keep `_handle_skill_tool_start()` in `pre_tool_use.py` -- Skill tool stage tracking is still needed for PM sessions running skills directly

### Phase 6: Extended harness config (Enhancement)

The `DEV_SESSION_HARNESS` env var and basic routing were added in Phase 2. This phase adds multi-harness config and validation.

- [ ] Harness config maps names to command templates: `{"sdk": null, "claude-cli": ["claude", "-p", "--verbose", "--output-format", "stream-json"], "opencode": ["opencode", "--non-interactive"]}`
- [ ] Add harness config to `config/` or inline in `sdk_client.py` as a simple dict
- [ ] Validate harness binary exists on PATH at worker startup (log warning if not found)
- [ ] Document supported harness values in `.env.example`

## Success Criteria

- [ ] `agent/sdk_client.py` has `_get_response_via_harness()` that runs `claude -p --verbose --output-format stream-json`, streams assistant events to Telegram via `send_cb()` (bypassing nudge loop), with time/size-based batching, and returns final result text
- [ ] Worker routes `AgentSession` with `role="dev"` to `_get_response_via_harness()` when `DEV_SESSION_HARNESS=claude-cli`; defaults to `sdk` (current behavior) for safe rollback
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

- Add `DEV_SESSION_HARNESS` to `.env.example` with default value `sdk` (safe default preserving current behavior; set to `claude-cli` to activate new harness)
- After update, restart the worker service so the new harness routing takes effect (`valor-service.sh worker-restart`)
- No new dependencies -- `claude -p` is already available on all machines with Claude Code installed
- No migration steps for existing installations -- the default harness (`sdk`) preserves current behavior; operators opt in to `claude-cli` when ready

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

### Round 1

**Date**: 2026-04-09
**Verdict**: NEEDS REVISION -- 1 blocker, 4 concerns, 2 nits

#### BLOCKER: send_to_chat / nudge loop incompatible with harness streaming path -- RESOLVED

Phases 1 and 2 now explicitly state that dev sessions bypass `send_to_chat()` and the nudge loop entirely. The harness streams chunks directly to `send_cb()` (the raw Telegram delivery callback). Time/size-based batching (3s / 2000 chars) added to Phase 1 to prevent flooding.

#### CONCERN: No rollback strategy between phases -- RESOLVED

`DEV_SESSION_HARNESS` env var moved from Phase 6 to Phase 2 with `sdk` as default (preserves current behavior). Phases 1-4 are purely additive. Phase 5 cleanup is gated on successful production runs with `DEV_SESSION_HARNESS=claude-cli`.

#### CONCERN: PipelineStateMachine lives in bridge/ -- RESOLVED

Phase 3 now includes moving `pipeline_state.py` from `bridge/` to `agent/` and updating all 3 import sites.

#### CONCERN: PM persona file lives outside the repo -- RESOLVED

Phase 4 now includes a startup validation check in `sdk_client.py` that warns if the PM persona still contains `subagent_type="dev-session"` Agent tool dispatch instructions.

#### CONCERN: No backpressure or buffering for streamed output -- RESOLVED

Phase 1 now includes time/size-based batching: buffer assistant text, flush to `send_cb()` on 3-second timer, 2000-char threshold, or `result` event (final flush).

#### NIT: stream-json requires --verbose flag -- RESOLVED

All references to `claude -p` now include `--verbose` flag: Phase 1 command, Phase 6 harness config template, and Success Criteria.

#### NIT: dev-session entry in agent_definitions.py definitely exists -- RESOLVED

Phase 5 now states unconditional deletion of `dev-session` entry at lines 121-131 of `agent/agent_definitions.py`.

### Round 2

**Date**: 2026-04-09
**Verdict**: NEEDS REVISION -- 2 blockers, 4 concerns

#### BLOCKER: Phase 3 undercounts pipeline_state import sites by 7x

Plan says "Update the 3 existing import sites (`bridge/telegram_bridge.py`, `agent/hooks/pre_tool_use.py`, `agent/hooks/subagent_stop.py`)". Actual count:

**Production files (10):** `agent/agent_session_queue.py`, `agent/hooks/pre_tool_use.py`, `agent/hooks/subagent_stop.py`, `agent/hooks/post_tool_use.py`, `bridge/pipeline_state.py` (self-import in doctest), `models/agent_session.py` (3 sites), `tools/sdlc_stage_query.py`, `tools/sdlc_stage_marker.py`, `ui/data/sdlc.py`

**Test files (3 unique):** `tests/unit/test_pipeline_state_machine.py`, `tests/integration/test_parent_child_round_trip.py` (5 imports), `tests/integration/test_artifact_inference.py`, `tests/integration/test_stage_aware_auto_continue.py`

`bridge/telegram_bridge.py` is listed but has **zero** pipeline_state imports -- it should be removed from the list. The plan must enumerate all actual import sites to avoid a broken build after the move.

Additionally, `pipeline_graph.py` has zero Telegram dependencies (it is a pure graph/config module) and `pipeline_state.py` imports from it. If `pipeline_state.py` moves to `agent/` but `pipeline_graph.py` stays in `bridge/`, `agent/` now depends on `bridge/` -- the opposite of the intended decoupling. Both files should move together.

#### BLOCKER: Phase 5 deletes session_registry.py but _handle_skill_tool_start depends on it

Phase 5 says to keep `_handle_skill_tool_start()` in `pre_tool_use.py` for PM Skill tool stage tracking. But `_handle_skill_tool_start()` at line 187 calls `session_registry.resolve()` -- deleting `session_registry.py` breaks this function. The plan must either: (a) rewrite `_handle_skill_tool_start` to resolve sessions without the registry, or (b) keep the minimal `resolve()` function somewhere.

Phase 5 also lists removal sites as `pre_tool_use.py`, `subagent_stop.py`, `post_tool_use.py`, `health_check.py`, `messenger.py` -- but misses `sdk_client.py` (lines 1028, 1235: `register_pending`, `cleanup_stale`, `unregister`) and `agent_session_queue.py` (line 2181: `get_activity`).

#### CONCERN: send_cb is async but plan does not specify async subprocess streaming design

`_get_response_via_harness()` must stream stdout line-by-line and call `send_cb()` which is an async callback. The plan does not specify whether the subprocess reading loop uses `asyncio.create_subprocess_exec` with async stdout iteration or runs `subprocess.Popen` in a thread. This affects error handling, cancellation, and integration with the existing async worker loop.

#### CONCERN: pipeline_graph.py should move with pipeline_state.py

`pipeline_graph.py` defines `STAGE_TO_SKILL`, `get_next_stage`, `DISPLAY_STAGES` -- pure pipeline configuration with zero Telegram/bridge dependencies. `pipeline_state.py` imports from it. Moving only `pipeline_state.py` to `agent/` creates a cross-package dependency (`agent/` -> `bridge/`) that contradicts the decoupling goal. Both should move to `agent/` together. Import sites for `pipeline_graph.py`: `ui/data/sdlc.py`, `bridge/pipeline_state.py`, `tests/e2e/test_routing.py`, `tests/integration/test_artifact_inference.py`, `tests/unit/test_pipeline_graph.py`, `tests/unit/test_pipeline_integrity.py`.

#### CONCERN: Stale persona file on production machines could break PM dispatch with no fallback

Phase 4 adds a startup WARNING log if the persona still references `subagent_type="dev-session"`. But a warning log is easily missed. If the persona is stale, PM sessions will attempt Agent tool dispatch which will fail (since Phase 5 deletes the `dev-session` agent definition). The plan should specify what happens at runtime when the PM actually tries Agent tool dispatch after the definition is deleted -- does it error? Does the PM retry? A runtime guard or graceful degradation path is needed, not just a startup warning.

#### CONCERN: PM needs clarity on which ID to pass as --parent

The plan says PM runs `python -m tools.valor_session create --role dev --parent $AGENT_SESSION_ID`. But `AGENT_SESSION_ID` is the PM's own AgentSession UUID. The `valor_session create` CLI `--parent` flag needs to be documented: does it accept the UUID string? Is it the same as `parent_agent_session_id` on the model? This is a minor detail but ambiguity here will cause wiring bugs.
