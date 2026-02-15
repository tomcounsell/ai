---
name: dennett-creativity
description: Jootsing (jumping out of the system) and structured perspective-switching for creative problem-solving. First map the constraints, then find the escape hatch. From Dennett's "Intuition Pumps."
---

# Dennett Creativity — Jootsing and Knob-Turning

Creative problem-solving through systematic constraint mapping and deliberate rule-breaking. You have to know the rules deeply before you can creatively break them.

## When to Use

- Brainstorming and product ideation
- Finding novel solutions to constrained problems
- Breaking out of local optima in design
- Exploring alternative approaches when stuck
- Any time the obvious solutions feel inadequate

## Core Tools

### Jootsing (#8) — Jumping Out Of The System

You must know the rules deeply before you can creatively break them. Jootsing is not random rule-breaking — it's informed, deliberate escape from a system you fully understand.

**The method:**
1. **Map the system**: What are the rules, conventions, constraints, and assumptions?
2. **Understand why each exists**: Which rules are load-bearing? Which are conventions? Which are accidents?
3. **Find the escape hatch**: Which rule, if broken, would unlock a fundamentally different solution?
4. **Break it deliberately**: Not randomly, not all at once — break the specific constraint that's creating the limitation

**Example:** "We need a faster build pipeline."
- Map the rules: sequential stages, full test suite, Docker builds, deployment approval
- Why each exists: stages catch errors early; full tests prevent regressions; Docker ensures consistency; approval prevents disasters
- Escape hatch: what if tests ran in parallel, not sequentially? What if we tested only changed modules?
- Result: break the "sequential full test" rule → incremental parallel testing → 10x speedup

### Turning the Knobs (#56)

Systematically vary the parameters of a problem to see which features actually do the work.

**The method:**
1. Identify the key variables in the problem
2. Change one variable at a time to its extreme
3. Observe what breaks and what still works
4. The variables that cause the most change when turned are the ones that matter most

**Example:** "Our onboarding takes too long."
- Turn "number of steps" from 8 to 1 → What's the absolute minimum?
- Turn "user technical level" from expert to novice → Which steps are genuinely needed?
- Turn "time constraint" from days to 5 minutes → What would a speed-run look like?
- Turn "automation" from manual to fully automated → What can be eliminated entirely?

### The Hat as Thinking Tool (#65)

Wear different "hats" (perspectives) to unlock different insights. Each hat sees a different problem.

**Hats to try:**
- **Naive user**: "I've never seen this before. What confuses me?"
- **Adversary**: "How would I break this? Where are the failure modes?"
- **Historian**: "How did we get here? What decisions led to this constraint?"
- **Competitor**: "How would [X] solve this? What would they do differently?"
- **Future self**: "In two years, what will I wish we'd done?"
- **Minimalist**: "What's the simplest thing that could possibly work?"

## Application Protocol

### For Constrained Problems

1. **Map all constraints** — list every rule, convention, requirement, and assumption
2. **Classify constraints** — which are fundamental? which are conventional? which are accidental?
3. **Turn knobs** — vary each constraint to its extreme. Which ones, when broken, unlock new solution spaces?
4. **Jootse** — deliberately break the most productive constraint
5. **Design within the new space** — now solve the problem with the old constraint removed

### For Brainstorming

1. **Put on different hats** — work through at least 3 perspectives
2. **Turn knobs on the problem statement** — what if the problem were 10x bigger? 10x smaller? Reversed?
3. **Identify the implicit rules** — what "obvious" constraints are you assuming?
4. **Jootse once** — break exactly one assumption and explore what happens
5. **Evaluate** — does the creative solution actually serve the goal, or is it just novel?

## Guardrails

Creativity without discipline is just chaos. Jootsing works because:
- You understand the rules before breaking them
- You break one rule at a time
- You have a reason for the specific rule you're breaking
- You check that the result actually improves things

**Bad jootsing:** "Let's skip tests!" (breaking a rule you don't understand)
**Good jootsing:** "Tests take 20 minutes because they're sequential. What if we ran them in dependency-isolated parallel groups?" (understanding the rule deeply enough to break it productively)

## Anti-Patterns

- **Random rule-breaking**: Breaking conventions without understanding them
- **Premature creativity**: Looking for novel solutions when straightforward ones exist
- **Knob-turning without purpose**: Varying parameters randomly instead of systematically
- **Hat-wearing as theater**: Going through perspectives mechanically without genuinely adopting them
- **Jootsing for its own sake**: Breaking rules to be clever rather than to solve a problem
