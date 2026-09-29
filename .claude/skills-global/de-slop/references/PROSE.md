# Prose people enjoy reading

Tell removal (SIGNS.md) gets a draft to *not-suspicious*; this pass gets it to *pleasant to read*. Stripping slop aggressively overshoots into punchy, too-clever prose and jargon dressed in grand metaphor, which reads as out of touch. **Warm and clear is the goal.**

Touchstones, and what each contributes. **Zinsser carries the most weight** — when principles pull in different directions, resolve toward him:

- **William Zinsser** (*On Writing Well*) — the primary reference. Simplicity with warmth; strip every word that does no work, but keep the humanity; write for one person, not an audience; every sentence should make the reader want the next. Clutter is the disease; the cure is plain, concrete, unhurried prose — not ornament.
- **Steven Pinker** (*The Sense of Style*) — classic style: writer and reader are equals, and the writing directs the reader's gaze at something interesting in the world, not at the writer's own performance.
- **Bill Bryson** — *stance only*: companionable curiosity, genuinely interested alongside the reader. Do NOT take his device habit — this pass never adds metaphors (see 4).
- **George Orwell / Stephen King** — plain words, active voice, cut what you're proudest of.

## The principles

### 1. One register, held throughout

The single worst post-de-slop failure: lab-report jargon and epic metaphor in the same paragraph — "the idempotent retry semantics form the beating heart of a symphony of microservices." The two registers each read as AI on their own; combined they read as a machine impersonating a poet impersonating an engineer. Pick one voice — conversational-intelligent, the way you'd explain it to a smart friend at dinner — and hold it. Jargon gets translated (see 7); metaphors get removed, not improved (see 4). Never decorate jargon *with* metaphor.

### 2. Warm beats clever

Cleverness that serves the reader (a comparison that makes something click) earns its place. Cleverness that performs (a knowing aside, a twist ending on every paragraph, a punchline where a plain sentence would do) is the writer looking at the mirror instead of the reader. When a line makes you feel smart for writing it, that's the one to cut — kill your darlings. The reader should come away thinking "I understand this now," not "this writer is sharp."

### 3. Be the reader's companion, not the topic's salesman

Bryson's engine isn't style, it's stance: he is discovering the material *with* you, delighted by it, honest about what's odd or boring or unresolved. Practical forms of this: anticipate the reader's next question and answer it in order; admit the genuinely surprising bit surprised you; let enthusiasm attach to the *subject*, never to your own product or prose. A companion says "here's the strange part"; a salesman says "here's the exciting part."

### 4. Concrete beats figurative — never add a metaphor

The Zinsser cure for a vague abstraction is not an image, it's a fact. Replace grandness with the actual number, the actual behavior, the actual consequence:

> Before: "The pipeline processes a vast torrent of events with remarkable efficiency."
> After: "The pipeline handles about 40 events a second and stays under 100ms each."

**This pass removes metaphors; it does not create them.** Editorial passes that invent comparisons are slop generators with better taste — the model's stock images ("like stations on an assembly line," "like a librarian for your data") are as recognizable as "delve." If the draft already contains a metaphor, keep at most one, and only if it's load-bearing and the author's own; strip the rest and say the plain thing.

### 5. Let sentences breathe

Countermand the overcorrection directly: relentless short sentences are the *new* tell. Punchy. Sharp. Like this. It reads as performance, and it's exhausting by paragraph two. Good rhythm mostly lives in medium-length sentences that carry a thought comfortably from one end to the other, with an occasional long, well-built one that takes the reader somewhere and an occasional short one for genuine emphasis. Fragments are seasoning — once a page, not once a paragraph. If three consecutive sentences are under ten words, merge two of them.

### 6. Plain words, unhurried tone

Simplicity is not curtness. "Use" not "leverage" — yes — but a de-slopped sentence can still be gracious: transitions survive ("even so," "as it happens," "which is why"), and so does the connective tissue that tells the reader how this sentence relates to the last one. Cutting every softener leaves prose that reads like a commit log. The test: plain vocabulary, complete thoughts, room to breathe.

### 7. Translate jargon once, then trust it

First use of a term of art gets a plain-language anchor in the same sentence ("idempotent — safe to run twice"). After that, use the term without apology or ornament. Two failure modes to catch: jargon never translated (excludes the human audience) and jargon re-explained or re-metaphored at every appearance (condescends to them).

### 8. Momentum

Zinsser's rule: the most important sentence is the next one. Each paragraph should end somewhere slightly different from where it began, and the reader should be able to feel the piece going somewhere. Cut anything that's true but doesn't advance — momentum, not completeness, is what keeps a reader who wasn't already interested.

### 9. Humor by understatement

If humor fits the medium at all, it works by precision and understatement, not zaniness or exclamation. The driest version of the joke is the funniest, and a joke that requires the reader to already agree with you isn't a joke, it's an in-group signal — the exact "out of touch" note to avoid.

### 10. The read-aloud test

Last check before verdict: read the piece aloud (literally, or in the mind's ear). Anywhere you'd stumble, run out of breath, or feel embarrassed to say the sentence to a colleague's face — that's the edit. Prose that can be comfortably spoken is prose that reads as human.

## How this pass applies

- It runs on the already-de-slopped text, including your own edits, the likeliest source of staccato and cleverness.
- It rewrites; it almost never blocks. Substance decides BLOCK, except that a voice wrong for its audience end to end counts toward the diagnosis.
- Change-log categories: `register` (jargon/metaphor mixing, voice drift), `rhythm` (staccato runs, uniform punch), `warmth` (clever-for-its-own-sake cut), `grounding` (metaphor replaced with the plain fact or number).
- Light touch: take the stance (plain, warm, curious, concrete), not a beloved writer's mannerisms; an imitation is its own slop.
