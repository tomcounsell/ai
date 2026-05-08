# Agent Reply Terminus Detection

**Status:** Shipped  
**Issues:** [#911](https://github.com/tomcounsell/ai/issues/911) (initial), [#1090](https://github.com/tomcounsell/ai/issues/1090) (question-aware Fast-Path 2), [#1318](https://github.com/tomcounsell/ai/issues/1318) (imperative Fast-Path 0 + few-shot prompt)

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

0. **Human sender + leading imperative verb on any line** → `RESPOND`  
   See [#1318](https://github.com/tomcounsell/ai/issues/1318). Human-only — the `not sender_is_bot` guard ensures bot loops containing the word "continue" never re-trigger Valor. Uses `_IMPERATIVE_LINE_RE` (multi-line aware) to match a small, deliberately-narrow set of high-precision continuation imperatives (`continue`, `proceed`, `resume`, `retry`, `redo`, `go ahead`, `ship it`, `do it`, `send it`, `try again`, `keep going`, `finish it`, `do this`, `handle it`, `move on`). Common verbs that frequently appear non-imperatively at message starts (`fix`, `run`, `merge`, `start`, `deploy`, `execute`, `push`) are EXCLUDED here and deferred to the few-shot LLM step.

1. **Bot sender + no standalone `?`** → `SILENT`  
   The primary loop-break signal. If the sender is a bot and the message contains no question, it's a loop continuation — silence it immediately.

2. **Acknowledgment token or ≤1 word** → `SILENT`  
   Checks `_ACKNOWLEDGMENT_TOKENS` set (shared with `classify_needs_response`). Fires **after** the bot check — never before — to avoid silencing human short replies. Also skipped entirely when `thread_messages` contains a question: if Valor's prior message in the thread contained a standalone `?` (per `_STANDALONE_QUESTION_RE`), the ≤1-word check is bypassed so a human short answer like "Yes" / "No" falls through to the LLM (or the RESPOND default). See [#1090](https://github.com/tomcounsell/ai/issues/1090).

3. **Standalone `?` in text** → `RESPOND`  
   Fast exit before any LLM call. Uses regex `(?<![=&\w])\?|(?<![=&])\?(?!\w+=)` to exclude URL query-string parameters like `?q=1`.

#### Fast-Path 0 Regex — Multi-Line Anchor

`_IMPERATIVE_LINE_RE` is anchored with `(?:^|\n)\s*<verb>\b` so it matches an imperative at the start of the message OR at the start of any line. The motivating May 7, 2026 incident was a two-line human reply:

```
I left a comment on PR 1316

Continue to finish all stage of SDLC
```

A regex anchored only to message start (`^\s*`) would have missed this. Mid-sentence usage (`"I would just continue this automatically"`) still does not match because there is no preceding newline-or-start before the verb — the sentence falls through to the LLM step, which has the few-shot examples to handle ambiguous cases.

#### Few-Shot LLM Prompt

The Ollama (`gemma4:e2b`) zero-shot prompt was too ambiguous to reliably distinguish continuation imperatives from conversation closers, leading to SILENT misclassifications of human action directives (May 6 and May 7, 2026 incidents). The prompt now includes labeled examples drawn from real misclassified messages plus canonical edge cases:

```
Examples:
"Continue to finish all stage of SDLC" → RESPOND
"Go ahead and merge" → RESPOND
"Run it again" → RESPOND
"Proceed with the plan" → RESPOND
"I left a comment on PR 1316\n\nContinue to finish all stage of SDLC" → RESPOND
"let's go with that" → RESPOND
"please ship it when ready" → RESPOND
"ok great" → REACT
"sounds good" → REACT
"nice work" → REACT
"👍" → SILENT
"thanks" → SILENT
"got it" → SILENT
```

Examples cover all three labels so the model anchors on the full RESPOND/REACT/SILENT decision boundary, not just the imperative case. The prompt grows by operational feedback — when a new misclassification surfaces in `terminus:` DEBUG logs, append a labeled example here.

#### DEBUG Log — Operational Feedback Loop

Both Fast-Path 0 hits and LLM-classified results are logged at `DEBUG` level:

```
terminus: 'RESPOND' — 'Continue to finish all stage of SDLC' (Fast-Path 0)
terminus: 'REACT' — 'ok great'
terminus: 'SILENT' — 'thanks'
```

Tail with `tail -f logs/bridge.log | grep terminus:` (or grep historical files) to surface misclassifications. When a new SILENT decision should have been RESPOND, add the verb to `_IMPERATIVE_VERBS` (if it's a clean continuation imperative) and/or add a labeled few-shot example to the prompt. The 15-verb starting set is not final — it's a seed list that grows by feedback.

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

**Question-aware Fast-Path 2 tests** (issue [#1090](https://github.com/tomcounsell/ai/issues/1090)):

- `test_classify_terminus_human_short_reply_to_valor_question_returns_respond` — human "Yes" + Valor question in thread → RESPOND
- `test_classify_terminus_human_short_reply_no_question_still_silent` — human "Yes" + declarative thread → SILENT (regression guard)
- `test_classify_terminus_bot_short_reply_to_valor_question_still_silent` — bot "Yes" + Valor question → SILENT via Fast-Path 1 (pins fast-path ordering)
- `test_classify_terminus_url_query_in_thread_not_treated_as_question` — URL `?q=1` in thread_messages → SILENT (URL query strings stay excluded)

**Fast-Path 0 imperative tests** (issue [#1318](https://github.com/tomcounsell/ai/issues/1318)):

- `test_classify_terminus_imperative_single_line_returns_respond` — single-line "Continue ..." → RESPOND
- `test_classify_terminus_imperative_multi_line_returns_respond` — May 7 incident verbatim (two-line message, imperative on line 2) → RESPOND
- `test_classify_terminus_imperative_go_ahead_multi_word_returns_respond` — "Go ahead and merge it" → RESPOND
- `test_classify_terminus_imperative_proceed_returns_respond` — "Proceed with the plan" → RESPOND
- `test_classify_terminus_imperative_single_word_continue_returns_respond` — single-word "continue" → RESPOND (Fast-Path 0 fires before Fast-Path 2's ≤1-word SILENT check)
- `test_classify_terminus_ok_great_does_not_respond` — "ok great" → REACT or SILENT, but never RESPOND
- `test_classify_terminus_thanks_still_silent_regression` — "thanks" → SILENT (acknowledgment token unchanged)
- `test_classify_terminus_imperative_from_bot_still_silent` — bot sender with imperative → SILENT via Fast-Path 1 (Fast-Path 0 is human-only)
- `test_classify_terminus_mid_line_imperative_does_not_match_regex` — mid-sentence "continue" must NOT match `_IMPERATIVE_LINE_RE` (verifies regex anchor directly)

## Related

- `bridge/routing.py` — `classify_conversation_terminus`, `should_respond_async`
- `bridge/response.py` — `set_reaction`
- `docs/features/config-driven-chat-mode.md` — Teammate persona passive listener (runs after this check)
- `docs/features/intake-classifier.md` — Haiku-powered intent triage (different use case)
