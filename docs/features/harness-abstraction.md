# Harness Abstraction

**Status**: Shipped (Phases 1-2 of issue #780, PR #868)

## Overview

Dev role sessions can now execute via a CLI harness (`claude -p --output-format stream-json`) instead of the Claude Agent SDK. The harness is selected by a single environment variable (`DEV_SESSION_HARNESS`), making the execution backend swappable without changing any PM, bridge, or session model code.

PM and teammate sessions remain on the SDK path. Only dev sessions are routed through the harness abstraction.

**Phases 3-6 are pending** in follow-up PRs. They will remove legacy hook wiring (`session_registry`, `SubagentStop` SDLC logic, `PreToolUse` dev registration), migrate PM persona to use `valor_session create --role dev`, and validate end-to-end SDLC pipeline execution via the CLI harness.

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

## Pending Work (Phases 3-6)

The following phases from issue #780 are not yet implemented:

- **Phase 3**: PM persona migration -- PM uses `valor_session create --role dev` instead of Agent tool
- **Phase 4**: Remove legacy wiring -- delete `session_registry`, `SubagentStop` SDLC logic, `PreToolUse` dev registration
- **Phase 5**: Worker post-completion handler -- SDLC stage transitions and GitHub comments after harness completion
- **Phase 6**: End-to-end validation -- full SDLC pipeline running dev sessions via CLI harness
