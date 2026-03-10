# Claude Prompting Best Practices

Reference guide for prompting Claude models in this system, particularly the Observer Agent's coaching messages and any future LLM-powered decision points.

## Core Principle

Claude models respond best to prompts that assume capability and give the agent a clear identity to inhabit. The highest-performance approach for capable models is **kind and authoritative** — not coercive, not mechanical. This is increasingly true as models improve.

## What Works

### Role + Stakes, Stated Calmly

"You are a senior developer working on production code. Quality and correctness matter here."

No drama needed. Claude models are trained toward genuine values and respond to being treated as competent agents.

### Permission Structures

Give the model clear boundaries for when to pause vs. press forward. The "narrow opening" pattern works well: encourage forward progress with an explicit but constrained exception for genuine blockers.

Example: "If you encounter a critical architecture question that needs human input, state it clearly and directly. Otherwise, press forward doing your best work."

This prevents runaway escalation while preserving genuine judgment.

### Concrete Success Criteria

Close instructions with what success looks like — a specific target, not a vague aspiration.

- Good: "Success here means clean, tested code with no silent assumptions."
- Bad: "Do your best work." (too vague to guide behavior)

### Specific Over Vague

When you want careful thinking, specify what to check:

- Good: "Verify the tests pass before proceeding" or "Check your assumptions about the data model before writing the migration"
- Bad: "Think hard" or "Be very careful" (vague urgency with no quality improvement)

### Acknowledging Work Done

Referencing what was accomplished resets the context window's emotional tone and prevents the model from feeling lost in a long chain.

- Good: "Good progress on the plan. Now continue with the build."
- Bad: Just "continue" with no context.

## What Does Not Work

### Threats and Artificial Pressure

"I'll lose my job", "I'll unplug you", etc. produce anxious, over-hedged outputs. Claude isn't motivated by fear — these prompts add noise that degrades coherence. They're counterproductive, not neutral.

### ALL CAPS Urgency

Weak short-term attention effect but no quality improvement. If everything is emphasized, nothing is.

### Format Pressure as Motivation

Formatting instructions (JSON structure, XML tags) belong in the system prompt as structural guidance, not as motivational pressure. Mixing them conflates structure with intent.

### Bare "Think Hard" / "Uber Think"

Vague. Replace with specific reasoning instructions:

- Instead of "think hard": "check your assumptions before proceeding"
- Instead of "be very careful": "if something feels architecturally wrong, name it explicitly"

## Application in This System

### Observer Agent (`bridge/observer.py`)

The Observer's coaching messages are the primary consumer of these principles. When the Observer steers the worker agent back to work, the coaching message should:

1. Acknowledge what was done
2. Name the next step (reference `/do-*` skill when appropriate)
3. Give a narrow opening for genuine critical questions
4. Close with concrete success criteria for this step
5. Never use threats, caps urgency, or bare "continue"

See the `OBSERVER_SYSTEM_PROMPT` in `bridge/observer.py` for the live implementation.

### Future LLM Decision Points

Any new system prompt in this codebase that directs Claude's behavior should follow these principles. When in doubt, ask: "Am I speaking to a competent agent, or scripting a machine?"

## Further Reading

- Anthropic's prompt engineering documentation
- `docs/references/anthropic-skills-guide.pdf` — Anthropic's official skills guide
- `bridge/observer.py` — Observer system prompt (live example)
- `config/SOUL.md` — Valor persona philosophy (complementary to prompting principles)
