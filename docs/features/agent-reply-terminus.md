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

0. **Human sender + imperative continuation verb at start of any line** → `RESPOND`  
   Added in [#1318](https://github.com/tomcounsell/ai/issues/1318). Short-circuits explicit action directives ("Continue to finish all stage of SDLC", "Go ahead and merge", "Proceed with the plan") to RESPOND before any LLM call. Bot-only — never fires for bot senders, so loop suppression is unaffected. See [Fast-Path 0: Imperative Verbs](#fast-path-0-imperative-verbs) below.

1. **Bot sender + no standalone `?`** → `SILENT`  
   The primary loop-break signal. If the sender is a bot and the message contains no question, it's a loop continuation — silence it immediately.

2. **Acknowledgment token or ≤1 word** → `SILENT`  
   Checks `_ACKNOWLEDGMENT_TOKENS` set (shared with `classify_needs_response`). Fires **after** the bot check — never before — to avoid silencing human short replies. Also skipped entirely when `thread_messages` contains a question: if Valor's prior message in the thread contained a standalone `?` (per `_STANDALONE_QUESTION_RE`), the ≤1-word check is bypassed so a human short answer like "Yes" / "No" falls through to the LLM (or the RESPOND default). See [#1090](https://github.com/tomcounsell/ai/issues/1090).

3. **Standalone `?` in text** → `RESPOND`  
   Fast exit before any LLM call. Uses regex `(?<![=&\w])\?|(?<![=&])\?(?!\w+=)` to exclude URL query-string parameters like `?q=1`.

#### Fast-Path 0: Imperative Verbs

Added in [#1318](https://github.com/tomcounsell/ai/issues/1318) to fix a recurring SILENT misclassification: when a human replied to a Valor message with an explicit directive ("Continue to finish all stage of SDLC"), the zero-shot Ollama prompt frequently returned SILENT and the message was dropped.

The fix is a module-scope compiled regex `_IMPERATIVE_LINE_RE` that matches a deliberately narrow set of high-precision continuation imperatives at the start of any line:

```
continue, proceed, resume, retry, redo,
go ahead, ship it, do it, send it, try again,
keep going, finish it, do this, handle it, move on
```

**Anchor:** `(?:^|\n)\s*<verb>\b` — the imperative must lead a line (start of message or after a newline). Mid-sentence usage like "I would just continue this automatically" does NOT match, because there is no preceding newline-or-start. This is critical for the May 7 motivating incident, where the directive appeared on line 2 of a multi-line reply:

```
I left a comment on PR 1316

Continue to finish all stage of SDLC
```

A regex anchored only to message start (`^\s*`) would have missed this — the imperative is on line 2.

**Verbs deliberately excluded:** `run`, `fix`, `merge`, `start`, `deploy`, `execute`, `push`, `complete`. These appear too frequently in declarative speech to anchor a deterministic short-circuit ("the run was successful", "fix the bug at your leisure", "start of the meeting"). The few-shot LLM prompt covers them via examples instead.

**Bot-sender guard:** Fast-Path 0 is gated by `not sender_is_bot`. Bot loop suppression (Fast-Path 1) is unaffected. A bot saying "Continue with deployment" still hits Fast-Path 1 and returns SILENT.

#### Few-Shot LLM Prompt

The previous zero-shot prompt produced SILENT for explicit imperatives that didn't hit Fast-Path 0 (e.g., "merge it", "run it again"). The local Ollama classifier (`granite4.1:3b` via `OLLAMA_CLASSIFIER_MODEL`) benefits from the few-shot examples to reliably distinguish continuation imperatives from conversation closers.

The prompt now includes 14 labeled few-shot examples drawn from real misclassified messages and canonical patterns:

```
"Continue to finish all stage of SDLC" → RESPOND
"Go ahead and merge" → RESPOND
"Run it again" → RESPOND
"Proceed with the plan" → RESPOND
"merge it" → RESPOND
"deploy when ready" → RESPOND
"fix the failing test" → RESPOND
"I left a comment on PR 1316\n\nContinue to finish all stage of SDLC" → RESPOND
"ok great" → REACT
"sounds good" → REACT
"nice work" → REACT
"👍" → SILENT
"thanks" → SILENT
"got it" → SILENT
```

Cost: ~200 extra tokens per LLM call. Ollama is local — no $$ cost; latency impact is negligible.

#### DEBUG Log for Future Mining

After every LLM classification (i.e., when no fast-path fires), the function logs:

```python
logger.debug(f"terminus: {result!r} — {text_stripped[:80]!r}")
```

This is the operational feedback loop for verb-list drift. When a new SILENT misclassification surfaces, grep `logs/bridge.log` for `terminus: 'SILENT'` patterns, identify the missed imperative, extend `_IMPERATIVE_VERBS` and add a few-shot example. The verb list is a starting point, not a final list.

To enable: set log level to DEBUG for the `bridge.routing` logger.

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

- `test_classify_terminus_imperative_single_line_returns_respond` — "Continue to finish all stage of SDLC" → RESPOND
- `test_classify_terminus_imperative_multi_line_returns_respond` — multi-line, imperative on line 2 → RESPOND (the May 7 incident)
- `test_classify_terminus_imperative_go_ahead_returns_respond` — "Go ahead and merge it" → RESPOND
- `test_classify_terminus_imperative_proceed_returns_respond` — "Proceed with the plan" → RESPOND
- `test_classify_terminus_imperative_single_word_returns_respond` — single-word "continue" → RESPOND (overrides Fast-Path 2 ≤1-word silencing)
- `test_classify_terminus_ok_great_does_not_respond_via_fast_path_0` — "ok great" must not match `_IMPERATIVE_LINE_RE`
- `test_classify_terminus_thanks_still_silent` — "thanks" → SILENT (regression guard)
- `test_classify_terminus_bot_imperative_still_silent` — bot saying "Continue with deployment" → SILENT (Fast-Path 1 wins)
- `test_imperative_line_re_does_not_match_mid_sentence` — "I would just continue this automatically" must not match (mid-line falls through to LLM)

## Related

- `bridge/routing.py` — `classify_conversation_terminus`, `should_respond_async`
- `bridge/response.py` — `set_reaction`
- `docs/features/config-driven-chat-mode.md` — Teammate persona passive listener (runs after this check)
- `docs/features/intake-classifier.md` — Haiku-powered intent triage (different use case)
