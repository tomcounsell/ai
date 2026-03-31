# Correlation IDs for End-to-End Request Tracing

## Overview

Every message journey through the system now carries a shared `correlation_id` -- a 12-character hex string generated at message receipt in the Telegram bridge. This ID propagates through the session queue, SDK client, observer, transcripts, and session snapshots, enabling instant grep-based tracing from receipt to response.

## How It Works

1. **Generation**: When `handler()` in `bridge/telegram_bridge.py` decides to process a message, it generates `correlation_id = uuid.uuid4().hex[:12]`.

2. **Storage**: The correlation_id is stored on the `AgentSession` model via a new `correlation_id` field (`Field(null=True)`). Existing sessions without the field simply have `None`.

3. **Propagation path**:
   - `bridge/telegram_bridge.py` generates the ID and passes it to `enqueue_agent_session()`
   - `agent/agent_session_queue.py` stores it in the AgentSession via `_push_agent_session()` and reads it back in `_execute_agent_session()` for use as a log prefix
   - `agent/sdk_client.py` receives it via `get_agent_response_sdk()` and uses it as the log prefix (replacing the internally-generated `request_id`)
   - `bridge/session_transcript.py` includes it in the transcript file header
   - `bridge/session_logs.py` receives it via `extra_context` in snapshot metadata

4. **Auto-continue inheritance**: The `correlation_id` is listed in `_AGENT_SESSION_FIELDS`, so it is automatically preserved across the delete-and-recreate pattern used by `_enqueue_continuation()`. Continuation sessions inherit the parent's correlation_id.

5. **Fallback**: If `get_agent_response_sdk()` is called without a correlation_id (e.g., direct SDK usage outside the bridge), it generates one locally to ensure all SDK log lines still have a tracing prefix.

## Usage for Debugging

To trace a complete message journey:

```bash
# Find all log lines for a specific correlation ID
grep "abc123def456" logs/bridge.log

# Find the transcript header
grep "correlation_id=abc123def456" logs/sessions/*/transcript.txt

# Find session snapshots
grep -r "abc123def456" logs/sessions/*/
```

## Files Modified

| File | Change |
|------|--------|
| `models/agent_session.py` | Added `correlation_id = Field(null=True)` |
| `bridge/telegram_bridge.py` | Generate correlation_id at message receipt |
| `agent/agent_session_queue.py` | Added parameter to `enqueue_agent_session()`, `_push_agent_session()`, `_AGENT_SESSION_FIELDS` entry; use as log prefix in `_execute_agent_session()` |
| `agent/sdk_client.py` | Added parameter to `get_agent_response_sdk()`; use as log prefix with local fallback |
| `agent/agent_session_queue.py` | Used as log prefix in nudge loop and routing |
| `bridge/session_transcript.py` | Added parameter to `start_transcript()`; include in header when provided |

## Design Decisions

- **12-character hex**: Short enough for log readability, unique enough for tracing (2^48 = 281 trillion possible values)
- **Pass-through string**: The correlation_id is purely additive -- no behavioral dependency, no coupling
- **null=True on Popoto field**: Safe to add to existing Redis data; old sessions simply have None
- **request_id replaced**: The `request_id` variable in `sdk_client.py` now uses the correlation_id value as its source instead of generating a separate identifier, keeping a single consistent prefix across all log lines

## Related

- Issue: [#334](https://github.com/valorengels/ai/issues/334)
- Plan: `docs/plans/correlation_ids.md`
- [Structured Logging & Telemetry](structured-logging-telemetry.md) -- the metrics layer built on top of correlation IDs
