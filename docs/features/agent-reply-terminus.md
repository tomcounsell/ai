# Agent Reply Terminus Detection

**Status:** Shipped  
**Issue:** [#911](https://github.com/tomcounsell/ai/issues/911)

## Problem

When Valor and another AI agent (e.g., a third-party bot) are both active in the same Telegram group, they can get trapped in an endless reply loop. Each agent receives the other's message as a "reply to themselves," which triggers a response unconditionally — before any passive-listener or persona rules fire.

**Before:** Agent A replies to Valor → Valor responds → Agent A responds → infinite loop. Must be broken manually.

**After:** Valor classifies each reply-to-Valor message as `RESPOND`, `REACT`, or `SILENT` before deciding whether to reply, breaking bot loops naturally.

## How It Works

The entry point is `should_respond_async()` in `bridge/routing.py`. When a message arrives that is a reply to Valor's own message (`replied_msg.out == True`), the function now calls `classify_conversation_terminus()` before returning.

### Three-State Decision

| Result | Meaning | Action |
|--------|---------|--------|
| `RESPOND` | Genuine question or continuation — reply needed | `return True, True` (existing behavior preserved) |
| `REACT` | Thread winding down naturally, human sender | Set 👍 reaction via `set_reaction`; `return False, True` |
| `SILENT` | Bot loop or pure acknowledgment | `return False, True` (no reply, no reaction) |

The `(False, True)` return preserves `is_reply_to_valor=True` so downstream session-continuation logic still works correctly.

### `classify_conversation_terminus` Function

Located in `bridge/routing.py`. Signature:

```python
async def classify_conversation_terminus(
    text: str,
    thread_messages: list[str],  # recent turns, oldest first
    sender_is_bot: bool = False,
) -> str:  # "RESPOND" | "REACT" | "SILENT"
```

### Fast-Path Priority Order

Fast-paths are checked before any LLM call, in this exact order:

1. **Bot sender + no standalone `?`** → `SILENT`  
   The primary loop-break signal. If the sender is a bot and the message contains no question, it's a loop continuation — silence it immediately.

2. **Acknowledgment token or ≤1 word** → `SILENT`  
   Checks `_ACKNOWLEDGMENT_TOKENS` set (shared with `classify_needs_response`). Fires **after** the bot check — never before — to avoid silencing human short replies.

3. **Standalone `?` in text** → `RESPOND`  
   Fast exit before any LLM call. Uses regex `(?<![=&\w])\?|(?<![=&])\?(?!\w+=)` to exclude URL query-string parameters like `?q=1`.

### LLM Classification

When no fast-path fires, Ollama (local model) is tried first, with Haiku as fallback. The prompt describes the RESPOND/REACT/SILENT semantics and injects sender and thread context.

**Conservative default:** If both Ollama and Haiku fail, returns `"RESPOND"` — genuine questions are never silently dropped due to classifier error.

**REACT collapse for bots:** When `sender_is_bot=True` and the LLM returns `REACT`, the result is collapsed to `SILENT`. REACT (emoji acknowledgment) is reserved for human-sender threads winding down naturally — not bot loops.

### Circular Import Avoidance

`bridge/response.py` already defers `from bridge.routing import DEFAULT_MENTIONS` at its line 331. Adding a top-level `from bridge.response import set_reaction` in `routing.py` would create a circular import at module load time.

**Solution:** The `set_reaction` import is a deferred local import inside `should_respond_async`, executed only when terminus is `REACT` and sender is human:

```python
if terminus == "REACT" and not sender_is_bot:
    try:
        from bridge.response import set_reaction  # deferred to avoid circular import
        await set_reaction(client, event.chat_id, message.id, "👍")
    except Exception as react_err:
        logger.debug(f"set_reaction failed (non-fatal): {react_err}")
```

No top-level `from bridge.response import` exists in `routing.py`.

### Log Level

Terminus decisions are logged at `INFO` (not `DEBUG`) so they appear in production log tails without enabling debug verbosity:

```
Reply to Valor detected - continuing session
Reply to Valor: terminus=SILENT, not responding
```

## Bot Detection

`sender_is_bot` is obtained via `event.get_sender()` inside the reply-to-Valor block. This is safe because `event.get_sender()` is already used elsewhere in the bridge handler and the result is guarded with a try/except that defaults to `False`.

## Thread Context

Only the already-fetched `replied_msg` is used as thread context (one message). Full multi-turn thread fetching was deliberately excluded (see Rabbit Holes in the plan) to avoid additional API calls. Richer context can be added in a follow-up if detection quality proves insufficient.

## Testing

Unit tests in `tests/unit/test_routing.py` cover all required scenarios:

- `test_classify_terminus_bot_no_question_returns_silent` — bot + declarative → SILENT
- `test_classify_terminus_human_question_returns_respond` — human + `?` → RESPOND
- `test_classify_terminus_url_with_query_param_not_respond` — URL `?q=1` not treated as question for bot sender
- `test_classify_terminus_acknowledgment_token_returns_silent` — "got it" from human → SILENT
- `test_classify_terminus_acknowledgment_fires_after_bot_check` — "yes" from bot → SILENT
- `test_classify_terminus_ollama_failure_defaults_to_respond` — Ollama + Haiku both fail → RESPOND
- `test_classify_terminus_empty_text_returns_respond` — empty text → RESPOND
- `test_classify_terminus_bot_react_collapses_to_silent` — LLM REACT + bot sender → SILENT

## Related

- `bridge/routing.py` — `classify_conversation_terminus`, `should_respond_async`
- `bridge/response.py` — `set_reaction`
- `docs/features/config-driven-chat-mode.md` — Teammate persona passive listener (runs after this check)
- `docs/features/intake-classifier.md` — Haiku-powered intent triage (different use case)
