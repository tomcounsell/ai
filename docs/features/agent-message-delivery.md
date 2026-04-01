# Agent-Controlled Message Delivery

## Overview

Gives the agent final say over its own output before it reaches the user. Instead of raw text flowing blindly through the summarizer to Telegram, the agent reviews a draft of its response and chooses how to deliver it.

## How It Works

### Stop Hook Review Gate (`agent/hooks/stop.py`)

When a Telegram-triggered session tries to stop:

1. **First stop** — the hook reads the agent's raw output from the transcript, runs it through the summarizer to produce a draft, then blocks the stop with a review prompt showing the draft and delivery choices
2. **Agent decides** — the agent sees its draft and picks one of:
   - `SEND` — deliver the draft as-is
   - `EDIT: <text>` — replace the draft with revised text
   - `REACT: <emoji>` — respond with only an emoji reaction
   - `SILENT` — send nothing at all
   - `CONTINUE` — resume working (false stop detected)
3. **Second stop** — the hook parses the choice, writes delivery instructions to the AgentSession, and allows completion
4. **Bridge executes** — `send_response_with_files()` reads the delivery instruction and acts accordingly

### Activation Rules

The review gate only fires when:
- Session was triggered by a Telegram message (`TELEGRAM_CHAT_ID` + `TELEGRAM_REPLY_TO` env vars set)
- Agent hasn't already self-messaged via PM tools (`has_pm_messages()` is false)
- Session has non-empty output

Skipped for: subagent sessions, programmatic sessions, local Claude Code sessions, PM self-messaging sessions.

### False Stop Detection

Simple heuristic: if the agent's output is short (<500 chars) and contains promise-like patterns ("I started...", "Let me check...", "I'm going to..."), the review prompt suggests CONTINUE. This is a suggestion, not forced — the agent decides.

## Delivery Execution (`bridge/response.py`)

Before the summarizer runs, `send_response_with_files()` checks `session.delivery_action`:

| `delivery_action` | Behavior |
|-------------------|----------|
| `"send"` | Send `delivery_text` (or filtered response) via Markdown, skip summarizer |
| `"react"` | Set `delivery_emoji` as reaction on the original message, send no text |
| `"silent"` | Do nothing — no text, no emoji |
| `None` | Fall through to existing summarizer path (backward compatible) |

## AgentSession Fields (`models/agent_session.py`)

Three nullable fields store the agent's delivery decision:
- `delivery_action` — "send", "react", "silent", or None
- `delivery_text` — final message text (for send/edit path)
- `delivery_emoji` — emoji for react-only path

## Classification Context (`agent/sdk_client.py`)

The routing classification result is passed as advisory text in the agent's enriched message:

```
[Routing context: classified as teammate (classifier confidence=92%). This is an initial guess — use your judgment.]
```

Both Teammate and PM sessions receive this context. The agent can disagree with the classification and act accordingly.

## Teammate Prompt (`agent/teammate_handler.py`)

The Teammate persona prompt includes a DELIVERY REVIEW section explaining the choices. Combined with conversational humility rules (hedged language, clarification-first, multi-perspective brevity), this gives the Teammate agent full control over tone and delivery.

## Test Coverage

- `tests/unit/test_stop_hook_review.py` — Review gate activation, transcript reading, false stop detection, choice parsing, state management, integration tests
- `tests/unit/test_delivery_execution.py` — send/react/silent/fallthrough paths
- `tests/unit/test_qa_handler.py` — Teammate prompt humility markers, review gate awareness
- `tests/unit/test_cli_sanitizer.py` — CLI leak stripping (defense in depth)
- `tests/unit/test_social_classifier.py` — 3-way classification tokens and emoji selection

## Related

- [Teammate Conversational Humility](qa-conversational-humility.md) — Teammate prompt design
- [Config-Driven Chat Mode](config-driven-chat-mode.md) — Persona routing
- [Chat-Dev Session Architecture](chat-dev-session-architecture.md) — Session types
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) — Tracking issue
