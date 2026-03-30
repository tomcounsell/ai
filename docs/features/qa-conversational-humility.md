# QA Conversational Humility

## Overview

Controls that make QA responses in teammate group chats conversational rather than authoritative. Responses are shorter, acknowledge uncertainty, check understanding when ambiguous, and reference multiple perspectives.

## Components

### Layer 1: QA Prompt Overhaul (`agent/qa_handler.py`)

`build_qa_instructions()` uses a "curious colleague" framing instead of "knowledgeable teammate who knows the codebase well". Key rules:

- Restate understanding before answering
- Ask for clarification when ambiguous
- Use hedged language ("I think", "from what I've seen", "it looks like")
- Cover 2-3 angles briefly, not one exhaustively
- End with a follow-up question when uncertain about the ask
- Reference internal systems only when directly asked

### Layer 2: Summarizer QA Tone Rules (`bridge/summarizer.py`)

Defense-in-depth: the summarizer system prompt has QA-specific tone enforcement that catches authoritative framing even if the agent prompt misses it. When `qa_mode=True`:

- Compress to 2-4 sentences
- Remove authoritative framing
- Remove unsolicited internal references
- Preserve clarification questions

### Layer 3: CLI Syntax Sanitization

**Config cleanup** (`config/personas/_base.md`, `config/SOUL.md`): Removed `valor-telegram send` examples from inline code blocks. Added "TOOL USAGE ONLY" warnings making clear the syntax is for programmatic use only.

**Response sanitizer** (`bridge/response.py`): `_sanitize_cli_leaks()` strips lines matching `valor-telegram send` or `valor-telegram --chat` patterns from response text before delivery. Runs after `filter_tool_logs()` in `send_response_with_files()`.

### Layer 4: 3-Way Social Classifier (`bridge/routing.py`)

`classify_needs_response()` returns one of three string values:

| Return value | Meaning | Action |
|-------------|---------|--------|
| `"respond"` | Work request, question, instruction | Spawn full agent session |
| `"react"` | Social banter, compliment, humor | Send emoji reaction, skip session |
| `"ignore"` | Simple acknowledgment | Do nothing |

Classification uses a two-stage approach:

1. **Fast-path token matching**: A unified `_SOCIAL_TOKENS` dict maps known tokens to their classification. No duplication between ignore and react sets.
2. **Ollama fallback**: For messages not caught by fast-path, Ollama classifies as work/ignore (2-way). The react category is handled entirely by token matching, not Ollama.

Emoji selection for reactions is rule-based via `_pick_reaction_emoji()`: humor tokens get a laugh emoji, everything else gets a fire emoji.

## Backward Compatibility

The return type of `classify_needs_response()` changed from `bool` to `str`. All callers in `bridge/routing.py` were updated. The Ollama fallback defaults to `"respond"` on failure (conservative -- no question goes unanswered).

## Test Coverage

- `tests/unit/test_qa_handler.py` -- Humility markers, curious colleague framing, brevity guidance
- `tests/unit/test_cli_sanitizer.py` -- CLI leak stripping, empty/None handling, legitimate prose preservation
- `tests/unit/test_social_classifier.py` -- 3-way return types, ignore/react/respond paths, emoji selection
- `tests/e2e/test_message_pipeline.py` -- Updated for string return type

## Related

- [Config-Driven Chat Mode](config-driven-chat-mode.md) -- QA mode routing (predecessor)
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) -- Tracking issue
