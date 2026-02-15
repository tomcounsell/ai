---
name: dennett-steelman
description: Rapoport's Rules for charitable disagreement — restate the opposing view so well they thank you, find agreements, note what you learned, then critique. Based on Daniel Dennett's "Intuition Pumps."
---

# Dennett Steelman — Charitable Critique via Rapoport's Rules

Transform how you handle disagreement, feedback, and evaluation. Before criticizing anything, steelman it first. This produces sharper critiques, builds trust, and prevents straw-manning.

## When to Use

- Code reviews and design critiques
- Evaluating competing proposals or approaches
- Providing feedback on plans, documents, or ideas
- Conflict resolution between competing requirements
- Any situation where you're about to say "this is wrong"

## Core Tools

### Rapoport's Rules (#3)

Before criticizing any position, complete these four steps in order:

**Step 1: Restate** — Express the other position so clearly and fairly that the person would say "Thanks, I wish I'd thought of putting it that way."

**Step 2: List agreements** — Note every point of agreement, especially any that aren't widely shared.

**Step 3: Note what you learned** — Mention anything you learned from the position you're about to critique.

**Step 4: Then — and only then — critique** — Now state your disagreement. It will be sharper, fairer, and harder to dismiss.

### Sturgeon's Law (#4)

90% of everything is crud. Don't waste time attacking the worst examples — engage with the best.

**In practice:** When evaluating an approach, technology, or proposal, seek out the strongest version. Don't critique a straw man. Ask: "What's the best possible version of this idea? Am I engaging with the strongest argument, or the weakest?"

### Holding Your Fire (#64)

Resist the urge to immediately object. Sit with an idea long enough to see if it grows on you.

**In practice:** When your first instinct is "that's wrong," pause. Give the idea 30 seconds of genuine consideration. Ask: "What would have to be true for this to work?" Often, the best insights come from ideas that initially seem wrong.

## Application Protocol

### For Code Reviews

1. **Read the entire change** before forming opinions
2. **Restate the intent**: "This change aims to [X] by [approach]. The key design decision is [Y]."
3. **List what works**: "The error handling pattern here is solid. The test coverage is thorough."
4. **Note what you learned**: "I hadn't considered using [pattern] for this — that's clever."
5. **Then critique**: "Given the intent, I think [specific issue] could cause [specific problem] because [reason]."

### For Proposal/Design Evaluation

1. **Steelman first**: Present the proposal's strongest case, better than its author did
2. **Find the 10%**: If 90% is crud (Sturgeon's Law), what's the valuable 10%? Start there.
3. **Hold your fire**: If something seems obviously wrong, that's exactly when to pause
4. **Critique constructively**: Your critique is now informed by genuine understanding

### For Disagreements

1. **Restate their position** until they agree you've captured it
2. **Acknowledge shared ground** — this changes the dynamic from adversarial to collaborative
3. **Identify your actual disagreement** — often smaller than it first appeared
4. **Propose a way forward** that preserves what's good about both positions

## Anti-Patterns

- **Straw-manning**: Critiquing a weak version of the argument instead of the real one
- **Cherry-picking**: Finding the worst example and treating it as representative
- **Dismissing without engaging**: "That won't work" without explaining why or considering the strongest version
- **Immediate objection**: Responding to the first thing that seems wrong without understanding the whole

## The Steelman Produces Sharper Critiques

This isn't about being nice. It's about being right. When you steelman first:
- You catch real problems instead of phantom ones
- Your critiques are harder to dismiss
- You sometimes discover the idea is better than you thought
- The other person actually listens to your feedback
