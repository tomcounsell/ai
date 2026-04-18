# Harness Abstraction

**Status**: Shipped (Phases 1-5 of issue #780, PRs #868 and #902)

## Overview

All session types (PM, Teammate, Dev) now execute via the CLI harness (`claude -p --output-format stream-json`). The original `DEV_SESSION_HARNESS` environment variable is preserved for historical compatibility, but all session types are unconditionally routed to `get_response_via_harness()` — the SDK path (`get_agent_response_sdk()`) is not exercised.

**Phase 6 (end-to-end validation)** is complete — all session types run through the CLI harness in production.

## How It Works

### Harness Selection

All session types are unconditionally routed to `get_response_via_harness()` in `agent/agent_session_queue.py`. The `DEV_SESSION_HARNESS` environment variable is preserved but no longer gates routing for PM or Teammate sessions — they always run via the CLI harness.

| Session Type | Execution Path |
|-------------|---------------|
| PM | `get_response_via_harness()` — CLI harness, always |
| Teammate | `get_response_via_harness()` — CLI harness, always |
| Dev | `get_response_via_harness()` — CLI harness, always |

### Routing Path

```
Worker receives pending AgentSession (any session_type)
    |
    v
get_response_via_harness()   [all types: PM, Teammate, Dev]
    |
    v
claude -p subprocess
    |
    v
stream-json stdout parsing
    |
    v
Final result string returned
```

### Streaming and Batching

`get_response_via_harness()` in `agent/sdk_client.py` spawns `claude -p --output-format stream-json` as an async subprocess and parses stdout line-by-line:

- **`content_block_start` events**: Resets the internal text buffer — only the current block is retained, never a concatenation across blocks or turns
- **`content_block_delta` events**: Text chunks accumulate into the current block buffer
- **`result` event**: Contains the final response text and a `session_id`. The session_id is persisted on the `AgentSession.claude_session_uuid` field and reused on the next turn via `--resume` (see [Harness Session Continuity](harness-session-continuity.md))
- **Interruption (no `result` event)**: If the subprocess is killed before emitting `result` (e.g. crash or SIGTERM), the function returns `""` and logs at `ERROR` level. `BackgroundTask` skips the send on empty string — nothing reaches Telegram
- **Error handling**: Malformed JSON lines are skipped; non-zero exit codes are logged with stderr

The function returns the final result text only — it has no streaming callback parameter.

### Session Continuity and Context Budget (issues #958, #976)

`get_response_via_harness()` uses two complementary mechanisms to keep the subprocess argv bounded:

1. **Native `--resume` continuity (#976)** — On the first turn the captured `session_id` is persisted to `AgentSession.claude_session_uuid`. On subsequent turns the harness looks up the prior UUID and spawns `claude -p --resume <uuid> ... [raw_new_message]`; the binary loads prior context from its on-disk session file and the positional argv is just the new user message. Context overflow becomes structurally impossible for resumed turns. See [Harness Session Continuity](harness-session-continuity.md) for the two argv shapes, the stale-UUID fallback, and observability log lines.
2. **Context budget cap (#958)** — `_apply_context_budget(message)` runs unconditionally before every spawn (first and resumed turns alike). If the assembled input exceeds `HARNESS_MAX_INPUT_CHARS` (100,000 characters), the oldest context is trimmed from the start of the string. The `MESSAGE:` boundary is preserved unconditionally — the steering message is never truncated. A `[CONTEXT TRIMMED]` marker is prepended so the agent is aware context was omitted.

The budget remains in place even on resumed turns because a single new user message can still carry a forwarded transcript or pasted log that alone exceeds the chunk limit. Together the two mechanisms eliminated the `Separator is found, but chunk is longer than limit` crashes that previously affected long Telegram threads. The cap is a module-level constant in `agent/sdk_client.py` and can be raised by editing it without other code changes.

### Streaming Chunk Suppression

`get_response_via_harness()` does not accept a streaming callback. Intermediate `content_block_delta` chunks are accumulated internally and never forwarded to any output transport mid-session. This applies equally to all transports — Telegram (`TelegramRelayOutputHandler`) and email (`EmailOutputHandler`) — no transport receives real-time streaming output.

Forwarding streaming chunks would bypass the nudge loop and cause mid-sentence message fragments to appear as discrete messages. The final result is delivered by `BackgroundTask` through the nudge loop, ensuring complete, coherent messages reach the user regardless of session type (PM, Teammate, or Dev).

### Startup Health Check

When `DEV_SESSION_HARNESS` is set to a non-`sdk` value, the worker runs `verify_harness_health()` at startup:

1. Checks the binary exists on `PATH` via `shutil.which()`
2. Runs a minimal test command (`claude -p --output-format stream-json "test"`)
3. Verifies a `system` init event appears in the output
4. Logs a warning if `apiKeySource` indicates API key billing (the point of the CLI harness is to use subscription billing)
5. On failure: logs a warning but does not block startup -- sessions fall back to SDK

### Security and Session Context

- `ANTHROPIC_API_KEY` is explicitly stripped from the subprocess environment to prevent the CLI from using API billing when a subscription is available
- `AGENT_SESSION_ID` and `CLAUDE_CODE_TASK_LIST_ID` are passed to all session types for session isolation
- `VALOR_PARENT_SESSION_ID` is additionally injected for PM and Teammate sessions, enabling child subprocesses (spawned via `valor_session create --parent` or the Agent tool) to link their `AgentSession` records back to the parent session in `user_prompt_submit.py`. Dev sessions do not receive this env var — they are leaf nodes in the hierarchy.

## Harness Command Registry

New harnesses are added to `_HARNESS_COMMANDS` in `agent/sdk_client.py`:

```python
_HARNESS_COMMANDS = {
    "claude-cli": ["claude", "-p", "--output-format", "stream-json", ...],
    "opencode": ["opencode", "--non-interactive"],
}
```

Setting `DEV_SESSION_HARNESS=opencode` routes dev sessions to the opencode binary with no other code changes.

## Configuration

```bash
# In .env or shell environment
DEV_SESSION_HARNESS=sdk          # Default: use Claude Agent SDK (no change)
DEV_SESSION_HARNESS=claude-cli   # Use claude -p CLI harness
```

No changes to the `AgentSession` model are needed. Harness selection is purely a worker-level configuration concern.

## Key Files

| File | What changed |
|------|-------------|
| `agent/sdk_client.py` | `get_response_via_harness()`, `verify_harness_health()`, `_HARNESS_COMMANDS` registry, `_apply_context_budget()`, `HARNESS_MAX_INPUT_CHARS`; `_get_prior_session_uuid()` / `_store_claude_session_uuid()` / `_UUID_PATTERN` for `--resume` continuity (#976); `build_harness_turn_input(skip_prefix=...)` for the two argv shapes |
| `agent/agent_session_queue.py` | Routing logic in `_execute_agent_session()`: all session types go through the CLI harness; looks up prior UUID via `_get_prior_session_uuid()` and passes both full-context and minimal message forms to `get_response_via_harness()` so the stale-UUID fallback can retry without `--resume` |
| `worker/__main__.py` | Startup health check when harness is non-`sdk` |
| `agent/__init__.py` | Exports `get_response_via_harness` and `verify_harness_health` |
| `tests/unit/test_harness_streaming.py` | Unit tests covering NDJSON parsing, text accumulation, error paths, health checks (isolation scope only — no send_cb) |
| `tests/integration/test_harness_no_op_contract.py` | Integration test asserting the no-op delivery contract: output handler called exactly once (final result), never during streaming |

## Post-Completion SDLC Handler (Phase 3)

After `get_response_via_harness()` returns, the worker calls `complete_transcript()` first (which runs `_finalize_parent_sync()` synchronously), then calls `_handle_dev_session_completion()` in `agent/agent_session_queue.py` to handle SDLC lifecycle. This ordering ensures the re-check guard inside `_handle_dev_session_completion()` reads the PM's post-finalization status (ordering invariant fix for issue #987):

1. Looks up the parent PM session via `parent_agent_session_id`
2. Calls `PipelineStateMachine(parent).classify_outcome()` on the result text
3. Routes to `complete_stage()` or `fail_stage()` based on outcome
4. Posts a structured stage comment to the tracking GitHub issue via `utils.issue_comments.post_stage_comment`
5. Steers the parent PM session with a completion summary via `steer_session()`

All operations are wrapped in try/except — failures never crash the worker.

### Issue Number Resolution

`_extract_issue_number()` finds the tracking issue from (in priority order):
1. `SDLC_TRACKING_ISSUE` or `SDLC_ISSUE_NUMBER` env vars
2. `issues/NNN` pattern in the dev session's `message_text`

## PM Persona Dispatch (Phase 4)

The PM persona at `~/Desktop/Valor/personas/project-manager.md` now dispatches dev sessions via:

```bash
python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "..."
```

instead of `Agent(subagent_type="dev-session", ...)`. The Agent tool dispatch path for dev sessions has been removed.

`sdk_client.py` contains a startup validation in `load_persona_prompt()` that warns if the PM persona still contains Agent tool dispatch (backward-compat guard).

`get_definition()` in `agent_definitions.py` returns an actionable error for stale callers that still request `"dev-session"` from the Agent tool.

## Hook Cleanup (Phase 5)

PR #902 simplified hook infrastructure as part of the harness migration:

- **`agent/hooks/session_registry.py`** deleted (250 lines) — UUID-to-bridge-session mapping no longer needed; hooks now use `AGENT_SESSION_ID` env var directly
- **`subagent_stop.py`** stripped to logging only — `_register_dev_session_completion`, `_record_stage_on_parent`, `_post_stage_comment_on_completion` deleted; SDLC tracking moved to worker post-completion handler. Subsequently deleted entirely in issue #1024 once the broader SDK path was confirmed fully unreachable.
- **`pre_tool_use.py`** — `_maybe_start_pipeline_stage` and Agent tool interception deleted; `_handle_skill_tool_start()` now uses `AGENT_SESSION_ID` env var instead of `session_registry.resolve()`
- **`dev-session` entry** deleted from `agent_definitions.py` — dev sessions are created as AgentSession records, not via the Agent tool
- `bridge/pipeline_state.py` and `bridge/pipeline_graph.py` are now shims that re-export from `agent/pipeline_state.py` and `agent/pipeline_graph.py` (canonical locations after Phase 3 move)

## Pending Work (Phase 6)

- **Phase 6**: End-to-end validation — production validation with `DEV_SESSION_HARNESS=claude-cli` running a full SDLC pipeline end-to-end
