---
name: checking-system-logs
description: Find bridge events, agent responses, timeouts, and errors in system logs. Use when debugging system behavior, investigating errors, or checking what the agent did.
---

# System Logs

**Location**: `/Users/valorengels/src/ai/logs/bridge.events.jsonl`

JSONL format with fields:
- `type` - event type (see below)
- `project` - project context ("DM", "Valor", etc.)
- `chat` - group name (null for DMs)
- `session_id` - e.g., "tg_dm_179144806"
- `sender` - who triggered the event

Event types:
- `message_received` - incoming message
- `agent_request` - request sent to agent
- `agent_response` - Valor's response
- `agent_timeout` - response timed out
- `error` - system error

## Query Examples

```bash
# Recent events
tail -50 /Users/valorengels/src/ai/logs/bridge.events.jsonl | jq .

# Filter by event type
grep '"type": "agent_response"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10 | jq .
grep '"type": "error"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10 | jq .

# Filter by project
grep '"project": "Valor"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10 | jq .
grep '"project": "DM"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10 | jq .

# Combine filters (project + type)
grep '"project": "Valor"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | grep '"type": "agent_response"' | tail -5 | jq .

# Search for keyword
grep -i "keyword" /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10 | jq .
```
