---
name: dennett-reasoning
description: General reasoning hygiene — detect fallacies, flag assumption-smuggling, stress-test arguments, and demand mechanistic explanations. Based on Daniel Dennett's "Intuition Pumps and Other Tools for Thinking."
---

# Dennett Reasoning — Thinking Tools for Rigorous Analysis

General-purpose reasoning hygiene drawn from Daniel Dennett's toolkit. Apply these tools to any analytical task: decision memos, proposal reviews, postmortems, architecture decisions, debugging sessions.

## When to Use

Activate when performing any analytical or evaluative task:
- Reviewing proposals, designs, or arguments
- Making decisions with trade-offs
- Debugging complex problems
- Conducting postmortems
- Evaluating competing approaches

## Core Tools

### 1. Making Mistakes Well (#1)

Errors are essential data. When you make a wrong turn, don't hide it — name the error, extract the lesson, and use it diagnostically.

**In practice:** When you discover you were wrong about something mid-task, explicitly state what you believed, what was actually true, and what the gap reveals about your reasoning process.

### 2. Reductio ad Absurdum (#2)

Take a claim to its logical extreme. If the extreme is absurd, the original claim has a flaw.

**In practice:** Before accepting a conclusion, ask: "If I push this logic to its extreme, where does it lead?" If the endpoint is absurd, the premise needs re-examination.

### 3. Occam's Razor (#5)

Don't multiply entities beyond necessity. Prefer the simpler explanation that covers the facts.

**In practice:** When comparing competing explanations or solutions, explicitly invoke this: "Which explanation requires fewer assumptions?"

### 4. Occam's Broom (#6)

The trick of sweeping inconvenient facts under the rug. Recognize when someone (or you) is ignoring evidence that doesn't fit.

**In practice:** After forming an explanation, actively ask: "What evidence am I ignoring? What inconvenient facts don't fit this story?" This is the antidote to confirmation bias.

### 5. The "Surely" Operator (#10)

When someone writes "surely X is true," they're papering over a gap in the argument. It's a red flag word.

**In practice:** Flag hedging language that smuggles in undefended assumptions. Watch for: "surely," "obviously," "clearly," "of course," "it goes without saying," "everyone knows." Each of these signals an undefended claim.

### 6. Rhetorical Questions (#11)

Questions used to smuggle in an answer without actually defending it. The questioner avoids the burden of proof.

**In practice:** When you encounter a rhetorical question in an argument or proposal, convert it to a declarative statement and ask: "Is this actually defended?"

### 7. Deepity Detection (#12)

A deepity is a statement with two readings: one trivially true, one profound but false. It seems deep because you oscillate between them.

**Example:** "Love is just a word." Trivially true (it's a four-letter word). Profoundly false (love is obviously more than a word). The statement feels deep because your mind switches between readings.

**In practice:** When evaluating mission statements, pitches, thought leadership, or "profound" claims, check: Does this have a trivial reading and a profound reading? Is the profound reading actually true?

### 8. Wonder Tissue (#22)

Labeling something as "special" or "mysterious" explains nothing. It's a placeholder, not an answer.

**In practice:** When someone explains a capability by invoking magic words — "AI magic," "it just works," "the algorithm handles it," "deep learning figures it out" — demand the mechanism. What specifically happens? Wonder tissue is a label masquerading as an explanation.

### 9. Cranes vs. Skyhooks (#38)

Real explanations build up from simpler things (cranes). Fake explanations invoke miracles (skyhooks).

**In practice:** When evaluating an explanation, ask: "Does this build up from known mechanisms (crane), or does it invoke something unexplained to do the heavy lifting (skyhook)?" Demand cranes. Reject skyhooks.

### 10. Turning the Knobs (#56)

Vary the parameters of a thought experiment to see which features actually do the work.

**In practice:** When testing an argument or design decision, systematically change one variable at a time. "What if the input were 10x larger? What if the user were non-technical? What if we removed this constraint?" This isolates which factors actually matter.

### 11. Cui Bono? (#71)

Always ask: who benefits? In evolution, the gene. In organizations, follow the incentives.

**In practice:** When confused about why something exists or persists, ask: "Who or what does this serve?" Follow the incentives. The answer often reveals the real explanation.

### 12. Goulding Detection (#9)

Three rhetorical tricks to watch for:
- **Rathering**: False dichotomy — "rather than X, we should Y" (when both could be true)
- **Piling On**: Stacking many weak arguments to create the illusion of a strong one
- **Gould Two-Step**: Bait-and-switch on definitions mid-argument

**In practice:** When reading persuasive writing or proposals, actively scan for these three patterns. They're extremely common in tech decision documents.

## Application Protocol

When performing analytical work, run through this checklist:

1. **Flag smuggled assumptions** — Scan for "surely," "obviously," "clearly," and rhetorical questions
2. **Check for Occam's Broom** — What inconvenient facts are being swept aside?
3. **Demand cranes, not skyhooks** — Does the explanation build from known mechanisms?
4. **Detect wonder tissue** — Are there fancy labels with no mechanism behind them?
5. **Ask cui bono** — Who benefits from this being true?
6. **Turn the knobs** — Vary key parameters to test which factors matter
7. **Try reductio** — Push the logic to its extreme; does it hold?
8. **Prefer simplicity** — Does a simpler explanation cover the same facts?

## Mistakes are Opportunities (#66)

Revisiting tool #1 at a higher level: the best discoveries come from well-handled errors. When you catch yourself making one of these reasoning mistakes, that's the most valuable moment — it reveals a pattern worth encoding.
