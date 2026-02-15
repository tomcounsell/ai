---
name: dennett-decomposition
description: Break complex systems into cascading layers of simpler, stupider parts. No homunculi, no wonder tissue, no central controller. Based on Daniel Dennett's "Intuition Pumps."
---

# Dennett Decomposition — Homuncular Decomposition

Break any complex capability into successively simpler parts until no intelligence remains at any single point. This is how you go from "it's magic" to "it's engineering."

## When to Use

- Architecture reviews and system design
- Breaking down monoliths or complex features
- Organizational design and role decomposition
- Debugging complex emergent behavior
- Sprint planning for large features
- Any time someone says "it just works" and you need to know how

## Core Tools

### A Cascade of Homunculi (#20)

Decompose an intelligent-seeming whole into simpler and stupider parts until no "magic" remains.

**The method:**
1. Start with the complex capability: "The system understands user intent"
2. Break it into sub-capabilities: "It parses input → classifies intent → retrieves context → generates response"
3. Break each sub-capability into even simpler parts: "Classifies intent" becomes "tokenizes → embeds → compares to known patterns → selects highest match"
4. Keep going until each piece is so simple it obviously works

**The test:** At the bottom layer, can each piece be explained without invoking intelligence? If yes, you're done. If any piece still seems "smart," decompose it further.

### Wonder Tissue Detection (#22)

When a decomposition stops at a layer that's still mysterious, someone has inserted "wonder tissue" — a label that explains nothing.

**Red flags:**
- "The AI handles that part"
- "The algorithm figures it out"
- "Machine learning takes care of it"
- "It uses deep learning"
- "The system is intelligent enough to..."

Each of these is a placeholder, not an explanation. Demand the next layer of decomposition.

### Trapped in the Robot Control Room (#23)

You can't run a complex system by micromanaging every detail from a central control point. The central controller becomes a bottleneck, and you've just moved the complexity instead of reducing it.

**In practice:**
- If your architecture has a "god object" or "master controller," you haven't decomposed — you've relocated
- If one team/person must approve everything, the organization hasn't really distributed work
- If one service must be consulted for every decision, you have a single point of failure disguised as architecture

### The Cartesian Theater (#50)

The mistaken idea that there's a single place where "it all comes together" for a central viewer. In the brain, there is no theater. In systems, there shouldn't be one either.

**In practice:** Reject single-point-of-control designs. Decisions should emerge from distributed processes, not funnel through a central bottleneck.

## Decomposition Protocol

### Step 1: Name the Capability

State what the system does at the highest level.
- "The recommendation engine suggests relevant products"
- "The hiring process selects good candidates"
- "The build system ships tested code"

### Step 2: First Decomposition

Break the capability into 3-7 sub-capabilities. Each should be noticeably simpler than the whole.

### Step 3: Check for Homunculi

For each sub-capability, ask: "Does this require intelligence to work?" If yes, it's a homunculus — a little person inside the machine doing the hard work. Decompose it further.

### Step 4: Check for Central Controllers

Is there one piece that coordinates everything? If so, you've created a Cartesian Theater. Distribute the coordination.

### Step 5: Check for Wonder Tissue

Are there any pieces described with vague "smart" language? Replace wonder tissue with mechanism.

### Step 6: Bottom Out

Keep decomposing until every piece is "stupid" — simple enough that its operation is obvious. The test: could you explain each piece to someone who doesn't know the domain?

## Example: Decomposing "The System Understands User Requests"

**Level 0 (Wonder Tissue):** "The AI understands what users want"

**Level 1:** Input parsing → Intent classification → Context retrieval → Response generation

**Level 2 (Intent Classification):**
- Tokenize input into words
- Embed tokens into vector space (lookup table)
- Compare vector to known intent clusters (nearest neighbor)
- Return intent label with confidence score

**Level 3 (Compare to known clusters):**
- Compute cosine similarity between input vector and each cluster centroid
- Sort by similarity score
- Return top match if score > threshold, else "unknown"

**Bottom:** Every piece is arithmetic (dot products, sorting, thresholds). No intelligence required at any single point. The "understanding" is an emergent property of the cascade.

## Anti-Patterns

- **Premature abstraction**: Stopping decomposition because "that's handled by [library/service]" — you need to understand what it does, not just that it exists
- **Central controller**: Moving complexity to a master orchestrator instead of distributing it
- **Wonder tissue**: Labeling a component "smart" and moving on
- **Homunculus smuggling**: A sub-component that's just as complex as the whole
- **Level-skipping**: Going from "the system works" to implementation details without the intermediate decomposition layers
