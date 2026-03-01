# Summarizer: Always-Summarize with SDLC Templates

Structured output format for Telegram delivery of agent work summaries. Every response is summarized via Haiku (no character threshold). SDLC messages use template-rendered stage progress and link footers.

## Key Behaviors

1. **SDLC: always summarize. Non-SDLC: summarize if >= 500 chars.** SDLC sessions always go through Haiku (stage lines + link footers needed). Non-SDLC short responses (< 500 chars) pass through raw — this preserves programmatic skill output like `/update` that's already formatted.
2. **SDLC template rendering**: Stage progress lines and link footers are rendered in Python code, not by the LLM. The LLM only generates bullet summaries and questions.
3. **Question extraction**: The LLM can surface questions, decisions, and items needing human input using a `---` separator and `? ` prefix. These are parsed and rendered after the summary bullets.

## Output Format

### SDLC Completions
```
✅ Infinite false-positive loop after squash merge
☑ ISSUE → ☑ PLAN → ☑ BUILD → ☑ TEST → ☑ REVIEW → ☑ DOCS
• Two-layer defense: branch tracking + merge detection
• 10 new tests, 63 passing
Issue #168 | Plan | PR #176
```

### SDLC Mid-Pipeline with Questions
```
⏳ Plan picker for free teams
☑ ISSUE → ☑ PLAN → ☑ BUILD → ▶ TEST → ☐ REVIEW → ☐ DOCS
• Replaced upgrade flow with plan picker UI
• Running test suite...

? Should we use modal or inline picker?
? 2 nits found in review — skip or patch?
Issue #273 | Plan
```

### Conversational
Simple prose format, no stage line or link footer. Still summarized via Haiku.

## Emoji Vocabulary

| Emoji | Meaning |
|-------|---------|
| ✅ | Completed |
| ⏳ | In progress |
| ❌ | Failed |
| ⚠️ | External blocker |

## Stage Progress Symbols

| Symbol | Meaning |
|--------|---------|
| ☑ | Stage completed |
| ▶ | Stage in progress |
| ☐ | Stage pending |

## Implementation

- `bridge/summarizer.py`: `summarize_response()` (always-summarize entry point), `_compose_structured_summary()` (template renderer), `_parse_summary_and_questions()` (question extractor), `_render_stage_progress()`, `_render_link_footer()`
- `bridge/response.py`: Always calls summarizer for non-empty text, passes `AgentSession` via `session=` kwarg
- `bridge/telegram_bridge.py`: `_send` callback accepts and forwards `session` parameter
- `agent/job_queue.py`: `SendCallback` type includes session parameter, `send_to_chat()` passes `agent_session`
- `bridge/markdown.py`: `send_markdown()` with plain-text fallback

## Session Freshness

Stage data (`[stage] BUILD completed ☑`) and links (`issue_url`, `pr_url`) are written to Redis by `tools/session_progress.py` during agent execution. By the time the summarizer runs, the session object passed through the callback chain may be stale (loaded before stages were recorded).

Both `response.py` and `summarizer.py` re-read the session from Redis before composing structured output:

```python
# Re-read for fresh stage/link data
fresh = list(AgentSession.query.filter(session_id=session.session_id))
if fresh:
    session = fresh[0]
```

Diagnostic logging in `_compose_structured_summary()` confirms when stage progress is rendered vs missing:
- `INFO "Rendered stage progress for session ..."` — template applied successfully
- `WARNING "SDLC session ... has no stage progress to render"` — stage data not found

## Callback Chain

```
agent/job_queue.py send_to_chat()
  → send_cb(chat_id, msg, message_id, agent_session)
  → bridge/telegram_bridge.py _send(chat_id, text, reply_to, session)
  → bridge/response.py send_response_with_files(..., session=session)
      ↳ re-reads session from Redis for fresh stage/link data
  → bridge/summarizer.py summarize_response(text, session=session)
  → _compose_structured_summary(summary, session=session)
      ↳ re-reads session from Redis for fresh stage/link data
```

## Adaptive Format Rules

1. **Simple completions**: "Done ✅" (still summarized for consistency)
2. **Conversational**: Prose, preserving tone
3. **Questions**: Preserved exactly, surfaced after bullets via `---` separator
4. **SDLC work**: Emoji + stage line + bullets + questions + link footer
5. **Status updates**: 2-4 bullet points

## Telegram Markdown

Basic `md` parse mode (not MarkdownV2). Supports bold, inline code, and `[text](url)` links. Falls back to plain text on parse errors.

## Related

- [AgentSession Model](agent-session-model.md) - Unified lifecycle model with stage progress helpers
- [Bridge Response Improvements](bridge-response-improvements.md) - Response pipeline
- [Coaching Loop](coaching-loop.md) - Output classification and auto-continue
