---
name: analyze
description: "Strategic analysis of a decision, idea, or plan. Triggered by 'analyze this', 'evaluate/assess this', 'deep dive', 'strategic read', 'your full take', 'should we X or Y'. NOT quick factual lookups."
allowed-tools: Agent
---

# Analyze

Deliver a structured strategic assessment — convergence, tensions, overlooked factors, a direct recommendation, and one first move — on a decision, idea, or plan with meaningful stakes. Do not run it for simple factual questions, quick lookups, or casual requests without meaningful stakes.

## Framing

Compose a complete brief from what you already know: the user's raw question, relevant conversation context (constraints, goals, numbers, prior decisions), workspace files already in context that bear on the question, and what's at stake. Do not ask the user for anything.

## Run

**If a `strategic-analyst` agent type is available** (listed among this session's agent types), delegate the whole protocol to it and relay the report path it returns:

```
Agent(subagent_type="strategic-analyst", prompt="[the framed question]")
```

**Otherwise, run the same protocol yourself** with general-purpose agents:

1. **Five lenses in parallel** — spawn 5 agents in a single message, each applying one lens to the framed question (150-300 words, no hedging):
   - *Downside* — failure points, fatal flaws; assumes a critical weakness exists and hunts for it
   - *Foundational* — challenges the question's assumptions; may conclude the wrong question is being asked
   - *Upside* — overlooked opportunities and undervalued assets, unbounded by risk
   - *Outside* — zero domain context; catches what experts assume is obvious
   - *Execution* — "what do you do Monday morning?"; flags ideas with no clear first step
2. **Cross-examine** — with all 5 responses in hand, identify: the strongest analysis, the biggest blind spot, and what all five missed.
3. **Synthesize** the assessment yourself, in this structure:
   - **What the Analysis Converges On** — points multiple lenses reached independently
   - **Points of Tension** — genuine disagreements, both sides
   - **Overlooked Factors** — insights that only emerged in cross-examination
   - **The Recommendation** — a real answer with reasoning, not "it depends"
   - **The First Move** — one concrete next step, not a list

## Complete

Deliver the assessment (or the report path, in delegated mode) to the user. Be direct; don't hedge.
