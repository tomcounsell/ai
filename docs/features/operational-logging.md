# Operational Logging

Consistent INFO-level logging at every decision point in the message processing pipeline, enabling end-to-end tracing of any message from arrival through routing, enrichment, agent invocation, and observer decision.

## Prefix Tags

All operational log lines use a consistent prefix tag enclosed in brackets. Filter with `grep '\[routing\]'` or similar.

| Tag | Source File | What It Logs |
|-----|------------|--------------|
| `[routing]` | `bridge/routing.py`, `bridge/telegram_bridge.py` | Classification result (sdlc/question/passthrough), session continuity decision, semantic routing match/miss |
| `[nudge]` | `agent/agent_session_queue.py` | Nudge loop routing decisions (deliver or nudge) with reason |
| `[enrichment]` | `bridge/enrichment.py` | Single summary line after all enrichment steps: media, youtube, links, reply chain counts, result length, failed steps |
| `[prompt-summary]` | `agent/sdk_client.py` | Message length, classification, workflow presence, task list ID, session context sections |

## Log Format

All new log lines follow this pattern:

```
[prefix-tag] Description: key=value, key=value
```

Values that could be long are truncated to 120 characters maximum to prevent log line bloat.

## Example Log Stream

A typical message produces this sequence:

```
INFO [routing] Classified as sdlc: fix the login timeout bug on the dashboard page
INFO [routing] Session tg_ai_12345_67890 (continuation=False)
INFO [routing] Semantic routing: no_match
INFO [enrichment] Summary: media=no, youtube=0, links=0, reply_chain=0 messages, result_length=342
INFO [prompt-summary] Sending to agent: 1205 chars, classification=sdlc, has_workflow=False, task_list=thread-12345-67890
INFO [prompt-summary] Context: soul=yes, sdlc_workflow=yes, workflow_context=no, session_id=tg_ai_12345_67890
INFO [nudge] Session tg_ai_12345_67890: is_sdlc=True, auto_continue=0/50, remaining_stages=True
INFO [nudge] Decision: nudge (reason: stages remaining, no question detected)
```

## Design Decisions

- **INFO level only**: All new logging is at INFO level (upgraded from DEBUG where applicable) so it appears in the default log configuration without requiring debug mode.
- **Prefix tags for filtering**: Consistent bracket-prefixed tags enable targeted grep filtering (e.g., `grep '\[observer\]' logs/bridge.log`).
- **120-char truncation**: All preview values are capped at 120 characters to prevent log lines from becoming unwieldy while still providing enough context for debugging.
- **No behavioral changes**: All logging is purely observational. No control flow, return values, or side effects are modified.
- **Summary over detail**: Enrichment logs a single summary line rather than per-step detail. Observer logs each iteration but truncates results.

## Files Modified

| File | Changes |
|------|---------|
| `agent/agent_session_queue.py` | Nudge loop routing decision logging |
| `bridge/routing.py` | Classification result logging for all paths (fast-path slash commands, acknowledgments, issue refs, LLM classification) |
| `bridge/telegram_bridge.py` | Session ID logging upgraded from DEBUG to INFO with `[routing]` prefix, semantic routing match/miss logging |
| `bridge/enrichment.py` | Failed step tracking, single `[enrichment]` summary line at end with all step counts |
| `bridge/pipeline_state.py` | State machine transition logging |
| `agent/sdk_client.py` | `[prompt-summary]` logging of message length, classification, workflow, task list, and context sections |

## Tracking

- Issue: https://github.com/valorengels/ai/issues/335
