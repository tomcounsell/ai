---
name: checking-system-logs
description: "Investigate Valor bridge, worker, session, timeout, or error behavior using structured events and service logs."
---

# Checking System Logs

Resolve the project, session/chat/request identifier, incident window, and timezone. Prefer structured events when correlating behavior: inspect `python scripts/analyze_logs.py --help`, then use its recent, timeouts, or stats commands. Access event records through the ORM, never raw Redis.
Read the relevant local `logs/` files for bridge, worker, reflections, and errors. Correlate timestamps and identifiers across components rather than inferring a cause from the nearest error line. Use `python -m tools.valor_session telemetry --id <ID>` and `inspect --id <ID>` for session events and state when needed.
If a filter returns nothing, inspect available event types and verify the project/ID and time range before concluding nothing happened. Distinguish a stale log or wrong checkout from current service evidence. Return a short causal timeline, exact supporting excerpts with secrets removed, and the next diagnostic or fix. Do not restart services as a substitute for investigation unless the task calls for recovery.
