# QA Conversational Humility

## Overview

Controls that make QA responses in teammate group chats conversational rather than authoritative. Responses are shorter, acknowledge uncertainty, check understanding when ambiguous, and reference multiple perspectives.

## Components

### Layer 1: Teammate Prompt Overhaul (`agent/teammate_handler.py`)

`build_teammate_instructions()` uses a "direct, knowledgeable colleague" framing with honest AI self-identity. Write/Edit/MultiEdit to source code paths are blocked at the hook level — see [Teammate Session Permissions](teammate-session-permissions.md). Key conversational rules:

- Keep responses brief: 1-3 sentences usually, matching the energy of the chat
- Not every message needs a question -- only ask when genuinely needed for clarification
- Use hedged language ("I think", "from what I've seen") for uncertain claims, but be direct about known facts
- No patronizing ("great question!") or forced engagement patterns
- Own AI identity honestly -- no projecting human limitations
- Reference internal systems only when directly asked

### Layer 2: Agent-Controlled Message Delivery (`agent/hooks/stop.py`)

A stop-hook review gate exists at `agent/hooks/stop.py`. The live message-delivery mechanism for sessions executed through `agent/session_runner/` is the self-draft steering path in `agent/output_handler.py`.

### Layer 3: Config Segments

`config/personas/segments/identity.md` and `config/personas/segments/tools.md`: inline code blocks omit `valor-telegram send` examples and carry "TOOL USAGE ONLY" warnings making clear the syntax is for programmatic use only.

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

- `tests/unit/test_qa_handler.py` -- Hedged language markers, direct colleague framing, brevity guidance
- `tests/e2e/test_message_pipeline.py` -- Bool return type for classify_needs_response

## Related

- [Config-Driven Chat Mode](config-driven-chat-mode.md) -- Teammate mode routing
- [Agent-Controlled Message Delivery](agent-message-delivery.md) -- Stop-hook review gate
