---
name: dennett-stances
description: Navigate levels of explanation — intentional, design, and physical stances. Pick the right abstraction level for the audience and problem. Based on Daniel Dennett's "Intuition Pumps."
---

# Dennett Stances — Levels of Explanation

Navigate between different levels of explanation for any system. The right level depends on the question and the audience, not on what's "really" going on.

## When to Use

- System design and architecture discussions
- Stakeholder communication across technical levels
- Debugging (is this a logic bug, a design flaw, or a hardware issue?)
- Explaining AI systems to non-engineers
- Choosing the right abstraction level for documentation

## Core Tools

### The Three Stances (#18)

Every system can be explained at three levels. Choose deliberately.

**Intentional Stance** — Treat the system as a rational agent with beliefs and goals.
- "The load balancer *wants* to distribute traffic evenly."
- "The cache *expects* frequently accessed data."
- Best for: High-level architecture, user-facing explanations, predicting behavior.

**Design Stance** — Explain how the system is built to achieve its purpose.
- "The load balancer uses round-robin with health checks."
- "The cache uses LRU eviction with a 5-minute TTL."
- Best for: Engineering discussions, debugging design flaws, code reviews.

**Physical Stance** — Explain the actual mechanism, down to the implementation.
- "The load balancer iterates through a server list array, incrementing an index."
- "The cache stores entries in a hash map with a doubly-linked list for eviction ordering."
- Best for: Debugging specific bugs, performance optimization, low-level troubleshooting.

### Manifest Image vs. Scientific Image (#16)

Two ways of seeing the same thing:
- **Manifest image**: The everyday, folk-level understanding ("the app is slow")
- **Scientific image**: The precise, technical understanding ("P95 latency is 800ms due to N+1 queries on the user endpoint")

Both are valid. The manifest image is how users experience the system. The scientific image is how engineers fix it. Translation between them is a core skill.

### Folk Psychology (#17)

Our everyday framework for predicting behavior: beliefs, desires, intentions. It's a useful fiction — powerful for prediction, but don't confuse the map for the territory.

**In practice:** When explaining system behavior, folk-psychological language ("the service wants," "the queue believes") is a powerful shortcut. Use it deliberately, but know when to drop down to mechanism.

### Competence Without Comprehension (#30, #68)

Systems can be competent — even brilliant — without understanding what they're doing. This is not a bug; it's a fundamental feature of complex systems.

**In practice:** Don't anthropomorphize competence as understanding. An LLM that gives correct answers doesn't "understand" the way a human does. A well-tuned algorithm that outperforms experts doesn't "know" the domain. Recognizing this prevents both over-trust and under-use.

### The Personal/Sub-personal Distinction (#19)

Distinguish between person-level explanations ("she decided to refactor") and mechanism-level ones ("the diff shows 47 files changed with a new abstraction layer"). Know which level is appropriate.

### Greedy Reductionism (#41)

Reducing everything to the lowest level ("it's all just bits") misses real patterns at higher levels. The right level of abstraction depends on the question.

**In practice:** Don't skip levels. If someone asks why the deploy failed, "cosmic rays flipped a bit" is technically possible but useless. Match the explanation to the question.

## Application Protocol

### Choosing a Stance

| Question Type | Best Stance | Example |
|---|---|---|
| "What does this system do?" | Intentional | "It routes requests to the healthiest server" |
| "How does it work?" | Design | "Round-robin with weighted health scores" |
| "Why is it broken?" | Physical (usually) | "The health check goroutine is deadlocked on line 142" |
| "Should we build it?" | Intentional | "We need something that knows when servers are overwhelmed" |

### Shifting Between Stances

When an explanation at one level isn't working, shift:
- **Up** (physical → design → intentional): When drowning in detail, zoom out
- **Down** (intentional → design → physical): When the abstraction hides the problem, zoom in

### Audience Matching

| Audience | Default Stance | When to Shift |
|---|---|---|
| End users | Intentional + manifest image | Never go below design |
| Product managers | Intentional + design | Physical only for "why is this hard?" |
| Engineers | Design + physical | Intentional for "why are we doing this?" |
| Executives | Intentional + manifest | Design for "what's the approach?" |

## Anti-Patterns

- **Stance confusion**: Debugging at the intentional level ("the service doesn't want to respond") when you need the physical level
- **Greedy reduction**: Explaining everything at the lowest level when a higher abstraction would be clearer
- **Anthropomorphism-as-explanation**: Saying a system "wants" something and treating that as a complete explanation
- **Level-skipping**: Jumping from "it's broken" to "rewrite it" without passing through design-level diagnosis
