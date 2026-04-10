# Content Guide: Educational Presentation Best Practices

## Core Principle: Teach, Don't Tell

The goal is **understanding**, not information transfer. A slide deck that lists facts is a document pretending to be a presentation. Instead, build a narrative arc that takes the audience from "I don't know what this is" to "I get it, and I find it interesting."

## The Explanation Stack

Use this framework to structure any technical explanation. Each level builds on the previous:

### Level 1: Anchor (What is it?)
- One sentence, no jargon
- Use an analogy to something the audience already knows
- Example: "Redis is like a giant sticky-note board that every part of the system can read and write to instantly"

### Level 2: Motivation (Why does it exist?)
- Frame as a **problem** the audience can feel
- Make it concrete: "Imagine you're texting a friend, but every message takes 3 seconds to deliver..."
- The problem should make the solution feel inevitable

### Level 3: Mechanism (How does it work?)
- Walk through the **happy path** first — the simplest, most common case
- Use a diagram or step-by-step visual
- Max 5 steps — if more, you need to chunk into sub-concepts first

### Level 4: Depth (What's clever about it?)
- This is where you earn engagement — the "oh, that's smart" moment
- Trade-offs, design decisions, or elegant solutions
- Only for audiences who made it through Level 3

### Level 5: Connection (How does it fit?)
- Connect back to the bigger system or real-world impact
- "Without this, the whole system would..."
- Leave them with a mental model they can build on

## Slide Types

Mix these to maintain engagement. Never use more than 3 of the same type in a row.

### Title / Section Break
- `<!-- _class: lead -->` in Marp
- Large heading, optional subtitle
- Use to signal topic transitions
- Include a hook: question, surprising fact, or bold claim

### Concept Slide
- One heading, 3-5 bullet points max
- Bold the key term on first use
- End with a connection to the next slide

### Diagram Slide
- Visual fills most of the slide
- Minimal text — just a title and optional caption
- Label everything on the diagram itself, not in surrounding text

### Example Slide
- Show a real, concrete instance of the concept
- Code snippets: max 8 lines, highlighted key parts
- Before/after comparisons work well

### Comparison / Table Slide
- Use tables instead of side-by-side bullets
- 2-4 columns max, clear headers
- Highlight the "winner" or recommended option if applicable

### Quote / Takeaway Slide
- Use `>` blockquote for emphasis
- One key insight, large text
- Good for punctuation between dense sections

### Summary Slide
- 3 bullets maximum — the "if you remember nothing else" points
- Each bullet is a complete thought, not a fragment
- Numbered for recall

## Pacing Rules

| Slides | Talk Time | Density |
|--------|-----------|---------|
| 5-8 | 3-4 min | Lightning talk — one concept, no depth |
| 10-15 | 5-8 min | Standard — full explanation stack |
| 20-25 | 10-15 min | Deep dive — multiple concepts with examples |
| 30+ | Too many — split into multiple presentations |

**Pacing formula:** ~30 seconds per slide. If a slide needs more than 60 seconds of explanation, split it.

## Writing for Accessibility

### Language
- **High-school reading level** — avoid jargon, or define it immediately on first use
- Short sentences (under 20 words)
- Active voice: "The bridge sends messages" not "Messages are sent by the bridge"
- Concrete nouns over abstract ones: "the server" not "the infrastructure layer"

### Analogies (use one per major concept)
Good analogy patterns:
- **Kitchen analogy**: Queues are like order tickets, workers are like chefs
- **Mail analogy**: APIs are like postal addresses, payloads are like letters
- **Highway analogy**: Load balancers are like traffic cops, lanes are like server instances
- **Library analogy**: Databases are like card catalogs, indexes are like the sorting system

Test your analogy: Would a 16-year-old get it? If not, simplify.

### Visual Hierarchy
- Headings: what the slide is about (scannable)
- Body: the explanation (readable)
- Bold: key terms and concepts (findable)
- Code: only when the actual syntax matters
- Blockquotes: memorable takeaways

## Anti-Patterns (What NOT to Do)

- **Wall of text**: If a slide has more than 6 lines of body text, split it
- **Orphan bullets**: A single bullet point is not a list — make it a sentence
- **Jargon avalanche**: Never introduce more than 2 new terms per slide
- **Code dumps**: Code blocks over 10 lines lose the audience — excerpt the key part
- **No visuals**: If you go 4+ slides without a diagram, table, or visual break, add one
- **Burying the lead**: Put the conclusion FIRST, then explain why — don't build to a reveal
- **Slide numbers as content**: "Step 1, Step 2..." is a document, not a presentation

## Engagement Hooks

Use at least 2-3 of these across the deck:

- **Opening question**: "What happens when 10,000 users hit the same endpoint?"
- **Surprising fact**: "This system processes 50,000 messages per day with zero human intervention"
- **Contrast**: "Without this, X takes 3 hours. With it, 4 seconds."
- **Failure story**: "Last quarter, this exact scenario caused a 2-hour outage..."
- **Scale visualization**: "If each request were a grain of sand, this is a beach"

## Diagram Best Practices

### When to Use Each Type

| Diagram Type | Best For |
|-------------|----------|
| **Flowchart** | Step-by-step processes, decision trees |
| **Architecture** | System components and how they connect |
| **Sequence** | Message flow between actors over time |
| **Comparison table** | Feature matrices, trade-off analysis |
| **Timeline** | Ordered events, pipeline stages |
| **ASCII art** | Simple flows that must render everywhere |

### Diagram Rules
- **Max 7 nodes** — more than that, abstract into groups first
- **Label every arrow** — unlabeled connections are ambiguous
- **Left-to-right or top-to-bottom** — never mix flow directions
- **Color sparingly** — accent color for the focus element only
- **Include a legend** if using symbols, shapes, or colors for meaning
