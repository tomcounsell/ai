---
name: dennett-meta
description: Meta-philosophical tools — check that the problem matters, prioritize ruthlessly, and remember to actually use the tools. From Dennett's "Intuition Pumps."
---

# Dennett Meta — Is This Problem Worth Solving?

Before diving into a problem, check whether it matters. Prioritize ruthlessly. And actively apply the other thinking tools rather than passively knowing about them.

## When to Use

- Project selection and roadmap prioritization
- "Should we even be working on this?" conversations
- Evaluating proposals, features, or research directions
- Any time you're about to invest significant effort
- When someone presents a technically impressive but possibly pointless initiative

## Core Tools

### Higher-Order Truths of Chmess (#62)

Chmess is chess with one rule changed. You can prove theorems about Chmess with perfect rigor. But nobody cares, because the game doesn't matter.

**The principle:** Technical validity is not the same as importance. You can be rigorously correct about a question nobody needs answered.

**In practice:** Before investing in a problem, ask:
- "Who will care about the answer?"
- "What decision will this change?"
- "If we solve this perfectly, does anything improve?"

If the answer is "nobody / nothing / no" — it's Chmess. Move on.

**Examples in tech:**
- Optimizing a query that runs once a month and takes 2 seconds
- Writing extensive type definitions for code that's being deprecated next sprint
- Debating architectural patterns for a prototype that may not ship
- Benchmarking micro-optimizations that are invisible at the user level

### Sturgeon's Law — The 10% That's Good (#4, #63)

90% of everything is crud. This isn't cynicism — it's a prioritization tool. Don't waste energy on the bottom 90%. Find the 10% that's good and invest there.

**In practice:**
- When evaluating a list of potential projects: most of them don't matter. Find the ones that do.
- When reviewing proposals: most won't survive contact with reality. Identify the few that will.
- When reading documentation: most of it is filler. Find the sections that carry actual information.
- When debugging: most hypotheses are wrong. Prioritize by likelihood and impact.

### Use the Tools. Try Harder. (#67)

Dennett's rallying cry: these tools only work if you actually deploy them. Thinking well takes effort. Knowing about good thinking tools but not using them is the most common failure mode.

**In practice:** This is a meta-instruction. When facing a hard problem:
1. Pause before diving in
2. Ask: "Which thinking tool applies here?"
3. Actually use it — don't just nod at it
4. The effort of applying the tool is the point

**The checklist:**
- Am I checking for smuggled assumptions? (dennett-reasoning)
- Am I steelmanning before critiquing? (dennett-steelman)
- Am I at the right level of explanation? (dennett-stances)
- Am I decomposing or hand-waving? (dennett-decomposition)
- Am I being clear or hiding behind jargon? (dennett-clarity)
- Am I stuck in the system or looking for escape hatches? (dennett-creativity)
- Am I dealing with genuine agency or sphexish routine? (dennett-agency)
- Does this problem even matter? (dennett-meta — this skill)

## Application Protocol

### The Pre-Investment Check

Before committing significant time to any problem:

1. **Chmess check**: Is this a real problem or Chmess? Who cares about the answer?
2. **Sturgeon filter**: Is this in the top 10% of things we could work on?
3. **Impact check**: If we solve this, what concretely changes?
4. **Opportunity cost**: What are we NOT doing while we work on this?

If the problem passes all four checks, proceed. If not, explicitly state why and redirect.

### The Prioritization Lens

When presented with multiple options:

| Filter | Question | Kill if... |
|---|---|---|
| Chmess | Does anyone care? | No real stakeholder or user |
| Sturgeon | Is this in the top 10%? | There are clearly higher-impact alternatives |
| Impact | What changes if we succeed? | Nothing measurable |
| Reversibility | Can we undo it? | Yes, so don't over-invest in the decision |
| Cost | What's the opportunity cost? | The forgone alternative is obviously better |

### The Active Application Reminder

At the start of any significant analytical or creative task, explicitly choose which Dennett tools to apply. Don't rely on them "naturally" arising — they won't. Select and deploy them deliberately.

## Anti-Patterns

- **Chmess investment**: Spending significant effort on technically valid but practically irrelevant work
- **Bottom-90% engagement**: Treating all options as equally worthy of analysis
- **Tool-knowing without tool-using**: Understanding the thinking tools but never deploying them
- **False urgency**: Treating everything as critical, which is the same as treating nothing as critical
- **Analysis paralysis**: Using meta-tools as an excuse to never start (know when to stop filtering and start building)
