# Agent-Controlled Delivery

The agent uses delivery-choice prefixes to control how its output is routed to the user. These are internal protocol signals parsed by the stop hook (`agent/hooks/stop.py`), not user-facing text.

## Delivery Choices

| Prefix | Action | Description |
|--------|--------|-------------|
| `SEND` | deliver | Send the message text to the user (default) |
| `REACT: <emoji>` | react | Apply emoji as a Telegram reaction on the original message |
| `EDIT: <text>` | edit | Edit a previous message with new text |
| `SILENT` | silent | Suppress output entirely |
| `CONTINUE` | continue | Signal the agent should keep working |

## Data Flow

1. Agent outputs text with a delivery-choice prefix (e.g., `REACT: thumbs_up`)
2. Stop hook (`agent/hooks/stop.py`) parses the prefix and writes `delivery_action` + metadata to `AgentSession` in Redis
3. Response system (`bridge/response.py`) reads `delivery_action` from the session and executes the appropriate action (send, react, edit, etc.)

## Defense-in-Depth Filtering

Delivery-choice prefixes are also filtered by `filter_tool_logs()` in `bridge/response.py` as a defense-in-depth measure. This prevents a race condition where the nudge loop's parallel send path delivers raw control signal text to the user before the stop hook has written `delivery_action` to Redis.

The filter uses a case-insensitive regex matching delivery-choice prefixes at the start of a line. Lines matching the pattern are stripped from the output. This is stateless and has no timing dependencies.

## Related

- `agent/hooks/stop.py` -- Stop hook that parses delivery choices
- `bridge/response.py` -- `filter_tool_logs()` with delivery-choice filter, `send_response_with_files()` that executes delivery actions
- `bridge/telegram_bridge.py` -- Send callback that invokes `filter_tool_logs()`
- PR #602 -- Original implementation of agent-controlled message delivery
