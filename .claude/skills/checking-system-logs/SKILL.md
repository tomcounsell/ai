---
name: checking-system-logs
description: Find bridge events, agent responses, timeouts, and errors in system logs. Use when debugging system behavior, investigating errors, or checking what the agent did.
---

# System Logs

**Location**: `/Users/valorengels/src/ai/logs/bridge.events.jsonl`

JSONL format with event types:
- `agent_response` - Valor's responses
- `agent_timeout` - Response generation timed out
- `message_received` - Incoming messages
- `error` - System errors

```bash
# Recent events
tail -50 /Users/valorengels/src/ai/logs/bridge.events.jsonl

# Filter by type
grep '"type": "agent_response"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -20
grep '"type": "error"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10

# Pretty print
tail -10 /Users/valorengels/src/ai/logs/bridge.events.jsonl | jq .

# Search for keyword
grep -i "keyword" /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10
```
