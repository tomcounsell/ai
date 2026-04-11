# Harness Abstraction

**Status**: Shipped (Phases 1-5 of issue #780, PRs #868 and #902)

## Overview

Dev role sessions can now execute via a CLI harness (`claude -p --output-format stream-json`) instead of the Claude Agent SDK. The harness is selected by a single environment variable (`DEV_SESSION_HARNESS`), making the execution backend swappable without changing any PM, bridge, or session model code.

PM and teammate sessions remain on the SDK path. Only dev sessions are routed through the harness abstraction.

**Phase 6 (end-to-end validation)** remains pending — production validation with `DEV_SESSION_HARNESS=claude-cli` running a full SDLC pipeline.

## How It Works

### Harness Selection

The worker reads `DEV_SESSION_HARNESS` at session execution time:

| Value | Behavior |
|-------|----------|
| `sdk` (default) | Dev sessions use `get_agent_response_sdk()` -- current behavior, zero change |
| `claude-cli` | Dev sessions use `get_response_via_harness()` -- CLI subprocess |
| Any other value | Treated as a harness name; must have an entry in `_HARNESS_COMMANDS` |

PM and teammate sessions always use the SDK regardless of this setting.

### Routing Path

```
Worker receives pending AgentSession
    |
    v
session_type == "dev" AND DEV_SESSION_HARNESS != "sdk"?
    |                           |
   YES                         NO
    |                           |
    v                           v
get_response_via_harness()   get_agent_response_sdk()
    |                           |
    v                           v
claude -p subprocess         Claude Agent SDK
    |
    v
stream-json stdout parsing
    |
    v
Batched text delivery via send_cb
```

### Streaming and Batching

`get_response_via_harness()` in `agent/sdk_client.py` spawns `claude -p --output-format stream-json` as an async subprocess and parses stdout line-by-line:

- **`content_block_delta` events**: Text chunks are accumulated in a buffer
- **Flush triggers**: Buffer is flushed to `send_cb` when it reaches 2000 characters or 3 seconds have elapsed since the last flush
- **`result` event**: Contains the final response text and a `session_id` for potential future resume support
- **Error handling**: Malformed JSON lines are skipped; non-zero exit codes are logged with stderr

The function returns the final result text (from the `result` event if present, otherwise accumulated text).

### Startup Health Check

When `DEV_SESSION_HARNESS` is set to a non-`sdk` value, the worker runs `verify_harness_health()` at startup:

1. Checks the binary exists on `PATH` via `shutil.which()`
2. Runs a minimal test command (`claude -p --output-format stream-json "test"`)
3. Verifies a `system` init event appears in the output
4. Logs a warning if `apiKeySource` indicates API key billing (the point of the CLI harness is to use subscription billing)
5. On failure: logs a warning but does not block startup -- sessions fall back to SDK

### Security

- `ANTHROPIC_API_KEY` is explicitly stripped from the subprocess environment to prevent the CLI from using API billing when a subscription is available
- Extra env vars (`AGENT_SESSION_ID`, `CLAUDE_CODE_TASK_LIST_ID`) are passed through for session isolation

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
| `agent/sdk_client.py` | Added `get_response_via_harness()`, `verify_harness_health()`, `_HARNESS_COMMANDS` registry |
| `agent/agent_session_queue.py` | Routing logic in `_execute_agent_session()`: checks `DEV_SESSION_HARNESS` env var for dev sessions |
| `worker/__main__.py` | Startup health check when harness is non-`sdk` |
| `agent/__init__.py` | Exports `get_response_via_harness` and `verify_harness_health` |
| `tests/unit/test_harness_streaming.py` | 18 unit tests covering streaming, batching, error paths, health checks |

## Post-Completion SDLC Handler (Phase 3)

After `get_response_via_harness()` returns, the worker calls `_handle_dev_session_completion()` in `agent/agent_session_queue.py` to handle SDLC lifecycle:

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

## Legacy Hook Cleanup (Phase 5)

The following are removed in PR #902:

- **`agent/hooks/session_registry.py`** deleted (250 lines) — UUID-to-bridge-session mapping no longer needed; hooks now use `AGENT_SESSION_ID` env var directly
- **`subagent_stop.py`** stripped to logging only — `_register_dev_session_completion`, `_record_stage_on_parent`, `_post_stage_comment_on_completion` removed; SDLC tracking moved to worker post-completion handler
- **`pre_tool_use.py`** — `_maybe_start_pipeline_stage` and Agent tool interception removed; `_handle_skill_tool_start()` now uses `AGENT_SESSION_ID` env var instead of `session_registry.resolve()`
- **`dev-session` entry removed** from `agent_definitions.py` — dev sessions are created as AgentSession records, not via the Agent tool
- `bridge/pipeline_state.py` and `bridge/pipeline_graph.py` are now shims that re-export from `agent/pipeline_state.py` and `agent/pipeline_graph.py` (canonical locations after Phase 3 move)

## Pending Work (Phase 6)

- **Phase 6**: End-to-end validation — production validation with `DEV_SESSION_HARNESS=claude-cli` running a full SDLC pipeline end-to-end
