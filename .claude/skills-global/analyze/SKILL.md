---
name: analyze
description: "Strategic analysis of a decision, idea, or plan. Triggered by 'analyze this', 'run an analysis', 'strategic/business analysis', 'evaluate this', 'should we X or Y', 'pressure test this idea'."
allowed-tools: Agent
---

# Analyze

Frame the question from what's already in context, then fire the strategic-analyst agent. Delivers a structured assessment with recommendation and first action.

Also fires on: 'do a full analysis', 'assess this', 'what's the strategic read', 'deep dive on this', 'give me your full take'. Do NOT trigger on simple factual questions, quick lookups, or casual requests without meaningful stakes.

## Framing

Before invoking the agent, compose a complete brief from what you already know:

- The user's raw question
- Relevant context from the current conversation (constraints, goals, numbers, prior decisions)
- Any workspace files already in context that bear on the question
- What's at stake

Do not ask the user for anything. Use what's available.

## Invoke

```
Agent(
  subagent_type="strategic-analyst",
  prompt="[the framed question]"
)
```

## Complete

Relay the file path from the agent's return message to the user.
