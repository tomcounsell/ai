# QA Conversational Humility

## Overview

Controls that make QA responses in teammate group chats conversational rather than authoritative. Responses are shorter, acknowledge uncertainty, check understanding when ambiguous, and reference multiple perspectives.

## Components

### Layer 1: Teammate Prompt Overhaul (`agent/teammate_handler.py`)

`build_teammate_instructions()` uses a "curious colleague" framing instead of "knowledgeable teammate who knows the codebase well". Key rules:

- Restate understanding before answering
- Ask for clarification when ambiguous
- Use hedged language ("I think", "from what I've seen", "it looks like")
- Cover 2-3 angles briefly, not one exhaustively
- End with a follow-up question when uncertain about the ask
- Reference internal systems only when directly asked

### Layer 2: Agent-Controlled Message Delivery (`agent/hooks/stop.py`)

The stop-hook review gate gives the agent final say over message delivery. When a session completes, the hook evaluates the output and chooses a delivery action (SEND, EDIT, REACT, SILENT, CONTINUE). React-only responses (e.g., emoji reactions to social banter) are handled through this gate rather than the classifier.

### Layer 3: Config Cleanup

**Config cleanup** (`config/personas/_base.md`, `config/SOUL.md`): Removed `valor-telegram send` examples from inline code blocks. Added "TOOL USAGE ONLY" warnings making clear the syntax is for programmatic use only. This addresses the root cause of CLI syntax leaking into responses.

### Social Token Classification (`bridge/routing.py`)

`classify_needs_response()` returns a boolean (`True`/`False`):

| Return value | Meaning | Action |
|-------------|---------|--------|
| `True` | Work request, question, instruction | Spawn full agent session |
| `False` | Acknowledgment, social banter, emoji | Do nothing |

Classification uses a two-stage approach:

1. **Fast-path token matching**: An `_ACKNOWLEDGMENT_TOKENS` set catches known acknowledgments and social banter tokens, returning `False`.
2. **Ollama fallback**: For messages not caught by fast-path, Ollama classifies as work/ignore (2-way). Returns `True` on failure (conservative -- no question goes unanswered).

## Test Coverage

- `tests/unit/test_qa_handler.py` -- Humility markers, curious colleague framing, brevity guidance
- `tests/e2e/test_message_pipeline.py` -- Bool return type for classify_needs_response

## Related

- [Config-Driven Chat Mode](config-driven-chat-mode.md) -- Teammate mode routing (predecessor)
- [Agent-Controlled Message Delivery](agent-message-delivery.md) -- Stop-hook review gate
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) -- Tracking issue
