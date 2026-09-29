# Content Guide: Educational Presentation Best Practices

## Teach, don't tell

The goal is understanding, not information transfer: take the audience from "I don't know what this is" to "I get it, and it's interesting." A reliable arc for technical explanation: **anchor** (one jargon-free sentence, ideally an analogy a 16-year-old would get), **motivation** (a concrete problem the audience can feel, making the solution feel inevitable), **mechanism** (the happy path first, at most 5 steps, with a visual), **depth** (the trade-off or clever bit), **connection** (how it fits the bigger system; leave a reusable mental model).

## Action titles

A slide title is not a label for what the slide covers. It is the slide's conclusion, written as a
full sentence, and it is the largest text on the slide. This is the discipline the top strategy
decks are built on, and it is the cheapest quality gain available.

| Topic label | Action title |
|---|---|
| Market overview | Three competitors left the mid-market last year |
| Architecture | Every message crosses exactly one queue |
| Retry behavior | Retries cost more than the outage they prevent |
| Next steps | You decide the launch date; we handle everything before it |

**Rules:** under 15 words. Never more than two lines. A conclusion, not a question. Read every title
in the deck end to end with nothing else — that sequence should be the argument. If it reads as a
table of contents, the deck has topic labels, not action titles.

Topic labels are correct in exactly two places: the appendix, and a section-break slide whose whole
job is to name the section.

## Budgets

Numbers, so the call is not a matter of taste:

| Budget | Limit |
|---|---|
| Statement slide | 12 words |
| Concept slide body | 40 words |
| Table | 5 rows, short cells |
| New terms per slide | 2 |
| Code block | 8 lines |
| Accent color uses per slide | 1 |
| Talk time per slide | 60 seconds |

Over budget means split the slide. It does not mean shrink the type — the theme's size scale is
fixed, and a slide that only fits at a smaller size is two slides.

The accent budget is the one people skip. Annotation red marks *value*: the number that matters, the
recommendation, the one word carrying the slide. Spend it once. A slide with two reds has no accent
at all, and a deck where every slide is red is a deck with no argument.

## Slide Types

Mix these to maintain engagement. Never use more than 3 of the same type in a row.

### Cover / Section Break
- `<!-- _class: cover -->` and `<!-- _class: section -->` in Marp
- Large Lora heading, mono eyebrow above
- Section breaks signal topic transitions and carry a numeral
- The cover includes a hook: question, surprising fact, or bold claim

### Statement Slide
- `<!-- _class: statement -->`
- One sentence, 12 words maximum, most of the slide left empty
- The load-bearing claim of a section, or the answer the deck exists to deliver

### Concept Slide
- Action title, then 3-5 bullets or a `.cols` split
- Bold the key term on first use
- End with a connection to the next slide

### Figure Slide
- No slide class. The visual goes inside a `.figure` div
- Minimal text — the action title and a mono `.figure__meta` title block
- Label everything on the diagram itself, not in surrounding text

### Example Slide
- Show a real, concrete instance of the concept
- Code snippets: max 8 lines, highlighted key parts
- Before/after comparisons work well

### Comparison / Table Slide
- No slide class. Use a markdown table instead of side-by-side bullets
- 2-4 columns max, clear headers
- Mark the recommended option with the slide's one accent

### Quote / Takeaway Slide
- `.rose` plate for the governing line, once per deck
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

## Language

High-school reading level: define jargon on first use, sentences under 20 words, active voice, concrete nouns ("the server", not "the infrastructure layer"), one analogy per major concept.

## Client-Facing Decks: Why → How → What

Educational decks explain a system to people who want to understand it. Client-facing decks — proposals, working sessions, decision briefs — serve a different function: they help a decision-maker act. The structure is different.

**This section is for decision-driving decks only.** The Why → How → What arc and the reflect-their-problem-back rule earn their place where there is a decision to drive. A deliverable whose job is description — an architecture overview, a vendor inventory, audit findings, a status report — leads with the content: a one-line scope/method note, then the subject itself. It describes accurately; the audience supplies their own conclusions.

**The failure mode for decision-driving decks:** opening with the solution. Jumping to scope, architecture, or features before the client sees their own problem reflected back puts them in a passive receiver role instead of an active decision-maker. They disengage or start objecting to details before the framing is established.

**The fix is structural, not cosmetic:**

| Section | What it does | Typical slides |
|---|---|---|
| **Why** | Shows you understand the client's world before proposing anything | Who is the client, their operating reality, the specific problem they face, the goal in their own terms |
| **How** | Explains the approach / governing principle at a level of abstraction above the solution | The mechanism, the philosophy, why this approach and not others |
| **What** | The specific scope, features, decisions, or plan | Details, trade-offs, options, timelines |

**Rule:** A client should never see a scope table, architecture diagram, or decision matrix before they have nodded at a slide that describes their own situation accurately. That nod is the moment they trust the presenter enough to engage with the solution.

**For working sessions specifically** (where the audience must make decisions, not just learn): each decision item should be a discrete slide or pair of slides — context + the ask. State your recommended default and let them veto. Never open a decision item with an open question; always with a proposed path.

### The title slide: audience first

For a client-facing deck, the title slide is not about the deck — it is about *them*. The audience and the value to them outrank the deck's topic, so order the slide top-to-bottom:

1. **Top — who it's for.** A small kicker line naming the audience/client (e.g. `Prepared for <Client>`). This is the first thing they read, and it signals the deck was built for them, not a generic template reused.
2. **Middle — the title.** What the deck is ("the what"). Necessary, but secondary — give it the visual weight of a title, but not the top of the slide.
3. **Bottom — the identity that anchors it.** The presenter, product, or subject as a name + photo **side by side** (a real face, not a logo, when there is a person/persona involved). This is who they're dealing with.

**Rule:** never bury the client attribution in small muted print. Client name on top, a real name/face at the bottom, the deck title in between. Implement with a full-height flex column (`justify-content: space-between`) rather than absolute positioning — it stays balanced regardless of title length.

## Failures the budgets don't catch

- **Orphan bullets**: a single bullet is a sentence, not a list.
- **Burying the lead**: conclusion first, then why; don't build to a reveal.
- **Slide numbers as content**: "Step 1, Step 2..." is a document, not a presentation.
- **More than 4 slides without a figure, table, or visual break.**
- **Belittling the audience or manufactured fear**: never tell the audience what they lack, imply they are exposed, impotent, or behind, or manufacture urgency to make a point land. Describe the subject, not the audience's inadequacy. A finding is a neutral technical fact with a remediation, never a warning about the reader's competence or a countdown to disaster.

## Diagram Best Practices

### Diagram Rules
- **Max 7 nodes** — more than that, abstract into groups first
- **Label every arrow** — unlabeled connections are ambiguous
- **Left-to-right or top-to-bottom** — never mix flow directions
- **Color sparingly** — accent color for the focus element only
- **Label the marks, not a legend.** A legend makes the reader hold a color-to-name mapping in their
  head while reading the chart. Put the name on the bar, the line, or the node. Reach for a legend
  only when direct labels genuinely collide

### Charts

Flat bars in theme ink, the one key bar in the accent, values labeled directly on the marks in mono,
category names below in gray, a hairline baseline. No gridlines, no y-axis, no legend, no second
accent. Anything past a bar comparison goes through the `dataviz` skill, carrying these constraints
in as the palette — it owns form choice, color ramps, and accessibility, and duplicating its rules
here would only let the two drift.

### Diagrams That Should NOT Be Mermaid Flowcharts

Some concepts look like they should be diagrams but produce spaghetti when rendered:

| Concept | Bad approach | Good approach |
|---------|-------------|---------------|
| Feature comparison (A vs B) | Flowchart with cross-subgraph edges | Side-by-side table with shared items highlighted |
| Venn overlap | Subgraphs with connections to a shared group | Table with columns: "Only A", "Both", "Only B" |
| Many-to-many relationships | Flowchart with N edges crossing | Matrix/grid table |
| Capability matrix | Flowchart nodes for each capability | Table with checkmarks |

**Rule of thumb:** If the diagram has more edges than nodes, use a table instead. Edges crossing between subgraphs ALWAYS produce spaghetti in Mermaid→Excalidraw renders.
