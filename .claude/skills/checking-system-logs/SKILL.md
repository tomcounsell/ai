---
name: checking-system-logs
description: "Use when finding bridge events, agent responses, timeouts, or errors in system logs. Triggered by requests to debug system behavior, investigate errors, or check what the agent did."
allowed-tools: Read, Grep, Glob, Bash
user-invocable: false
---

# System Logs

Find what the system did: structured bridge events live in Redis (`BridgeEvent`
Popoto model), plain-text service logs under `~/src/ai/logs/`, and per-session
detail in session telemetry. Narrow by project or keyword; raw tails of a busy
bridge log bury the signal.

## Structured events (Redis)

Query via the analyzer script:

```bash
cd ~/src/ai && python scripts/analyze_logs.py recent 20   # recent events, correlated by request
cd ~/src/ai && python scripts/analyze_logs.py stats       # counts by type/project
```

For project-filtered queries, use the ORM (never raw Redis):

```bash
cd ~/src/ai && python -c "
from models.bridge_event import BridgeEvent
events = [e for e in BridgeEvent.query.all() if e.project_key == 'Valor']
events.sort(key=lambda e: e.timestamp or 0, reverse=True)
for e in events[:20]:
    print(e.event_type, e.chat_id, e.data)
"
```

Fields: `event_type`, `chat_id`, `project_key`, `timestamp`, `data` (dict with
`sender`, `chat`, `message_id`, ...). The bridge emits only `message_received`
(so `analyze_logs.py timeouts` finds nothing; look for timeouts in `worker.log`
and session telemetry). Events expire after about 7 days (`BridgeEvent.cleanup_old`).

## Text logs (`~/src/ai/logs/`)

| File | What's in it |
|------|--------------|
| `bridge.log` / `bridge.error.log` | Telegram bridge — message handling, routing, delivery |
| `worker.log` | Session execution engine |
| `reflection_worker.log` | Reflection scheduler subprocess (`python -m reflections`) |
| `email_bridge.log` | IMAP polling + SMTP relay |
| `nightly_tests.log` / `nightly_tests_error.log` | Nightly regression runs |

```bash
# Recent bridge activity for a project/chat
grep -i "psyoptimal" ~/src/ai/logs/bridge.log | tail -20

# Errors across the bridge
grep -iE "error|exception|traceback" ~/src/ai/logs/bridge.log | tail -20
tail -50 ~/src/ai/logs/bridge.error.log
```

## Session-level debugging

For what a specific agent session did, telemetry beats log archaeology:

```bash
python -m tools.valor_session telemetry --id <ID>   # turn events, tokens, status transitions
python -m tools.valor_session inspect --id <ID>     # raw Popoto fields
```

## If a filter returns nothing

List what actually exists before concluding the event never happened:
`python scripts/analyze_logs.py stats` shows event-type counts; for the set of
project keys, swap the ORM one-liner's filter for
`sorted({e.project_key for e in BridgeEvent.query.all()})`. Then retry with a
name from the list.
