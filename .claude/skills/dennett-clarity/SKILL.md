---
name: dennett-clarity
description: Use lay audiences as decoys to expose confusion, detect deepities, and translate between folk and technical language. A writing and editing skill from Dennett's "Intuition Pumps."
---

# Dennett Clarity — Writing and Thinking Clearly

Use simplification as a diagnostic tool. When you can't explain something simply, you don't understand it well enough. Detect pseudo-profundity, bridge between audiences, and expose hidden confusion.

## When to Use

- Writing documentation or explanations
- Preparing presentations for mixed audiences
- Reviewing docs for clarity and accuracy
- Onboarding materials
- Translating between technical and business language
- Any time you suspect jargon is hiding confusion

## Core Tools

### Using Lay Audiences as Decoys (#7)

Explain your idea to a non-expert. The effort to simplify reveals your own confusions.

**The method:** Before finalizing any explanation, restate it as if explaining to someone smart but unfamiliar with the domain. Points where you struggle to simplify are points where your own understanding is weak.

**In practice:** This is "rubber ducking" elevated to a principle. The simplification isn't for the lay audience — it's for you. Every stumble reveals a gap in your model.

### Deepity Detection (#12)

A deepity has two readings: one trivially true, one profoundly false. It feels deep because your mind oscillates between them.

**Examples in tech:**
- "Data is the new oil" — trivially true (data is valuable), profoundly misleading (data doesn't deplete, isn't fungible, doesn't need refining the same way)
- "Move fast and break things" — trivially true (speed matters), profoundly false (breaking production is not a strategy)
- "The code should be self-documenting" — trivially true (clear code is good), profoundly false (architecture decisions need explanation beyond what any code reveals)

**Detection:** When a statement feels profound, ask: "What's the trivial reading? What's the ambitious reading? Is the ambitious reading actually true?"

### Manifest Image vs. Scientific Image (#16)

Two valid descriptions of the same reality:
- **Manifest**: How users/non-experts experience it ("the app is slow")
- **Scientific**: The precise technical description ("P95 latency is 800ms due to N+1 queries")

Good writing moves between these fluently. Start with the manifest image to orient, then introduce the scientific image to explain.

### The Sorta Operator (#21)

Approximation is not failure. Things can "sorta" have properties, and that's fine at the right level of description.

**In practice:** Don't demand perfect precision in every explanation. "The cache sorta knows what you'll need next" is a valid explanation at the right level. Resist the urge to immediately correct it to "the cache uses an LRU eviction policy with probabilistic prefetching."

### "Daddy Is a Doctor" (#15)

Children's understanding of concepts is partial but functional. "Sorta" understanding can still be useful.

**In practice:** When writing for non-experts, embrace partial understanding as a valid goal. The reader doesn't need the complete model — they need enough to act correctly.

## Application Protocol

### For Writing Documentation

1. **Start with the manifest image** — What does this do from the user's perspective?
2. **Simplify to expose gaps** — Explain it to an imaginary non-expert. Where do you struggle?
3. **Fill the gaps** — Research or think through the parts you couldn't simplify
4. **Layer in technical detail** — Add the scientific image for readers who need it
5. **Scan for deepities** — Reread for statements that sound profound but say nothing
6. **Check the sorta level** — Is each explanation at the right level of precision for its audience?

### For Reviewing Documents

1. **Deepity scan** — Flag profound-sounding but vacuous statements
2. **Jargon audit** — For each technical term, ask: "Could this be said more simply without losing meaning?"
3. **Audience check** — Is the level of explanation appropriate for the intended reader?
4. **Gap detection** — Try to simplify each claim. Where simplification fails, the author may not understand it either
5. **Manifest/scientific balance** — Is there enough folk-level framing for orientation?

## Anti-Patterns

- **Jargon as authority**: Using technical terms to sound expert rather than to communicate
- **Precision fetishism**: Demanding technical accuracy in contexts where approximate understanding is the goal
- **Deepity acceptance**: Nodding along with profound-sounding statements without checking if they're actually meaningful
- **Curse of knowledge**: Forgetting what it's like not to know, and writing at a level that only experts can follow
- **False simplification**: Oversimplifying to the point of being wrong (the sorta operator has limits)
