---
name: do-presentation
description: "Create a polished Marp slide deck about a feature, concept, or system. Triggered by 'make a presentation', 'create slides', 'do-presentation', or 'explain this as a deck'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
argument-hint: "<topic or feature name>"
context: fork
---

# Make a Presentation

Produce a polished slide deck the audience actually understands: researched from the codebase, structured for the audience (educational or client-facing), set in the Cuttlefish house theme, and exported via Marp to PDF/HTML (PPTX on request). Success is judged at Step 10's verify checklist — exports exist, fonts loaded, slide count matches plan, every editorial flag addressed.

## Repo Context Probe

If `.claude/skill-context/do-presentation.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The static-deck flow (research → structure → theme → diagrams → editorial gate → Marp export) is fully generic — it needs only `npx`/Marp, `curl`, and network access for the Google Fonts import. No repo-specific tooling. The context file declares one optional capability: the **narrated `--video` mode** and the repo-provided CLI that powers it. When the file is absent (the common case in a foreign repo), the static deck (PDF/HTML/PPTX) is the deliverable and `--video` is unavailable.

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `CONTENT_GUIDE.md` | Structuring slide content — educational best practices, slide types, pacing |
| `THEME.md` | Writing the Marp front matter — the Cuttlefish theme, its style block, slide archetypes |

## Quick start

The topic comes from `$ARGUMENTS`. If empty, ask the user what to present on.

### Step 1: Scope the topic

Determine what the presentation covers:
- A single feature? A system overview? A concept explanation?
- Who is the audience? Default: **general technical audience, high-school reading level**
- How long? Default: **10-15 slides** (5-8 minute talk)

Ask the user only if the scope is genuinely ambiguous. Otherwise, make a reasonable call.

### Step 2: Research

Spawn an Explore agent to deeply research the topic:
- Read relevant source files, docs, READMEs, and config
- Trace how the feature works end-to-end
- Identify the 3-5 key concepts someone must understand
- Find concrete examples, real data, or code snippets
- Note any diagrams that would clarify architecture or flow

**Research output should answer:**
1. What is this? (one sentence a teenager could understand)
2. Why does it exist? (the problem it solves)
3. How does it work? (the mechanism, simplified)
4. What are the key parts? (components, steps, or layers)
5. What's interesting about it? (the clever bit, the trade-off, the insight)

### Step 3: Design the slide structure

Read `CONTENT_GUIDE.md` for educational best practices.

**First, determine the presentation type — it changes the opening structure:**

| Type | Audience | Opening structure |
|---|---|---|
| **Educational / internal** | Technical teammates, general audience | What → How → Why it matters |
| **Client-facing / working session** | Client decision-makers, executives | Why (their problem) → How (the approach) → What (the specifics) |

For client-facing decks, the first 3–4 slides must establish: (1) who the client is and what their operating reality looks like, (2) the problem they are experiencing in their own terms, (3) the governing principle or goal — before any solution, scope, or technical content appears. Opening with a solution before the client sees their problem reflected back is the single most common failure mode.

**Default slide structure (educational):**
```
1. Title slide (hook + subtitle)
2. The Problem (why this exists — relatable scenario)
3. The Big Idea (one-sentence thesis)
4. How It Works — Overview (diagram or visual)
5-8. Key Concepts (one per slide, with examples)
9. Architecture/Flow Diagram
10. Real Example (concrete, from the actual codebase)
11. Trade-offs / Design Decisions
12. Summary (3 bullet takeaway)
13. Questions / Further Reading
```

**Client-facing / working session structure:**
```
1. Title + session framing (not a pitch — a working session)
2. Why: Who is the client? (their context, their operating reality)
3. Why: The problem they are experiencing (in their terms)
4. How: The approach / governing principle
5. How: The mechanism (what the system does, simply)
6. What: The specific scope or decisions
7+. Decision / agenda items (one per slide)
N-1. Summary / next steps
N.  Appendix
```

Adjust count based on topic complexity. Aim for **one idea per slide**.

Write the outline as **action titles** — each line a full sentence stating that slide's conclusion,
not a topic label. Read the outline back top to bottom: it should read as the argument. If it reads
as a table of contents, the deck will too. See "Action titles" in `CONTENT_GUIDE.md`.

### Step 4: Apply the theme

Every deck uses **Cuttlefish**, the house theme. Read `THEME.md`, copy its style block verbatim into
the deck's front matter, and set the deck slug in the masthead rule. There is no theme detection and
no per-deck restyling — the theme is the constant, and holding it constant is what makes a deck read
as a Yudame artifact rather than a template.

Two rules from `THEME.md` govern everything downstream and belong in working memory now:

- **One accent per slide.** Annotation red marks *value* — the number that matters, the
  recommendation, the one word carrying the slide. A slide with two reds has no accent at all.
- **Light ground, square corners, hairlines over boxes.** Cream `#FAF9F6`, no shadows, no gradients,
  no rounded cards.

`THEME.md`'s closing section covers the single override case: a deck whose subject *is* another
product, presented to that product's own audience. Swap the `:root` values, keep the structure.

### Step 5: Collect brand logos

When the presentation mentions companies, products, or branded technologies, pull in their logos for visual polish. Logos appear inline next to brand names or as small icons in tables/lists.

**Source priority:**

1. **Simple Icons (GitHub raw)** — 3000+ tech/business brands, monochrome SVGs, no auth
   ```bash
   # Download SVG (slug is lowercase brand name, no spaces)
   curl -s "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/{slug}.svg" \
     -o diagrams/logo-{slug}.svg
   ```
   Common slugs: `anthropic`, `stripe`, `github`, `slack`, `redis`, `python`, `docker`, `linear`, `sentry`, `notion`, `telegram`, `postgresql`

   To find a slug, check: `https://raw.githubusercontent.com/simple-icons/simple-icons/develop/slugs.md`

2. **Google Favicons** — universal fallback, any domain, PNG
   ```bash
   curl -sL "https://www.google.com/s2/favicons?domain={domain}&sz=128" \
     -o diagrams/logo-{name}.png
   ```

**Colorizing SVGs:**

Simple Icons SVGs ship with no fill and default to black, which reads correctly on the Cuttlefish
cream ground. Set the theme ink explicitly so the logos match the rest of the deck rather than
sitting a shade darker:

```bash
sed -i '' 's/<path/<path fill="#1A1A1A"/' diagrams/logo-{slug}.svg
```

A brand's official color is the exception, not the default — it spends the slide's one accent on a
logo. Use theme ink unless the brand color *is* the point of the slide.

**Converting SVG to PNG (if needed for Marp compatibility):**

```bash
# macOS built-in, no dependencies, good quality at 512px
qlmanage -t -s 512 -o diagrams/ diagrams/logo-{slug}.svg 2>/dev/null
mv diagrams/logo-{slug}.svg.png diagrams/logo-{slug}.png
```

**Using logos in Marp slides:**

```markdown
<!-- Inline next to text (small, 24-32px) -->
![w:28](diagrams/logo-{slug}.svg) Anthropic ships Managed Agents

<!-- In a table cell -->
| ![w:24](diagrams/logo-{slug}.svg) Stripe | Payment processing |

<!-- Larger, standalone -->
![w:80](diagrams/logo-{slug}.svg)
```

**Rules:**
- Only fetch logos for brands **central to the slide content**, and only for polished decks — skip for internal/informal ones
- Keep logos small (24-32px inline, 64-80px standalone) — they accent, not dominate
- Prefer SVG over PNG for sharpness (SVGs render in Marp with `--allow-local-files`)

### Step 6: Generate diagrams

For any architectural or flow concepts, create diagrams:

1. **Prefer ASCII art** in code blocks for simple flows (always renders correctly)
2. **Use Mermaid** for complex diagrams — check if `mermaid-render` skill is available:
   - Write `.mmd` file, render to PNG, embed as image
   - Fallback: include as fenced code block (renders in HTML export)
3. **Use tables** for comparisons, feature matrices, component lists

Diagram guidelines:
- Max 7 nodes/boxes per diagram (cognitive load limit)
- Label every arrow/connection
- Annotation red marks one emphasis node. Everything else is ink and hairline
- Put the diagram inside a `.figure` panel with a mono `.figure__meta` footer (`FIG. 01` left,
  caption right) so it reads as a plate rather than a floating image

For charts specifically, read the "Charts" section of `THEME.md` — flat bars, values labeled on the
marks, no gridlines, no axis, no legend. Anything more involved than a bar comparison goes through
the `dataviz` skill carrying those constraints.

### Step 7: Write the Marp markdown

Create the presentation file. Location priority:
1. If user specifies a path, use that
2. If a `docs/` directory exists, use `docs/presentations/<slug>.md`
3. Otherwise, use `<repo-root>/presentations/<slug>.md`

**Marp file structure:**
```markdown
---
marp: true
theme: default
paginate: true
backgroundColor: '#FAF9F6'
color: '#2D2D2D'
style: |
  <the Cuttlefish style block from THEME.md, verbatim, with the deck slug set>
---

<!-- _class: cover -->
<span class="eyebrow">Prepared for <Client></span>

# The title

<p class="lede">One serif line of framing.</p>

<div class="stamp">
  <div>Date<b>17 Aug 2026</b></div>
  <div>Prepared by<b>Yudame</b></div>
</div>

---

<span class="eyebrow">Section · 02</span>

## Retries cost more than the outage they prevent.

content...
```

**Writing rules:**
- One idea per slide — if you need a scroll bar, split it
- **Action titles.** Every content slide's `##` is a full sentence stating that slide's conclusion,
  under 15 words, and it is the largest text on the slide. `## Queue depth doubled after the retry
  change` beats `## Queue metrics`. A reader who sees only the titles should get the argument.
  Topic-label titles are for the appendix
- **Word budget by archetype.** Statement slide ≤ 12 words. Concept slide ≤ 40 words of body.
  Table ≤ 5 rows with short cells. Over budget means split the slide, not shrink the type
- **Accent budget.** One red per slide, spent on the value that matters. Count it before moving on
- Use `<!-- _class: section -->` for section breaks, `statement` for a single claim,
  `figure` for a diagram plate, `data` for a table or a `.big` number
- Use tables over bullet lists when comparing things
- Use code blocks sparingly — only when the actual code IS the point
- Every 3rd-4th slide should be visual (figure plate, table, or `.big` number)
- Use `.rose` for the one governing quote or line, once per deck
- Bold key terms on first use
- Use analogies liberally — connect technical concepts to everyday things

Layout components come from `THEME.md`: `.cols` / `.cols-3` break a slide that would otherwise be a
dense list into scannable sections, `.plate` holds a governing metric or key fact, `.note` is the red
margin annotation for a risk or caveat, `.card` inside `.cols-3` gives A/B/C decision options.

### Step 8: Editorial gate

One gate, run once, on the finished draft. Invoke `Skill('de-slop')` as a **fresh-context review** —
a subagent that receives only the deck file path, the medium (`presentation`), the audience, and the
addendum below. It must not receive this drafting conversation; the author of a draft is the worst
judge of its slop.

Pass this addendum along with the standard inputs, since these checks are deck-shaped and de-slop's
generic catalog does not cover them:

```
Additional checks for this deck, alongside the standard pass:

STRUCTURE — does it open with Why (the audience's problem and context), then How (the approach),
then What (the specifics)? For a client-facing deck, flag it if the first three slides do not
establish who the audience is and what problem they are experiencing before any solution appears.

ACTION TITLES — every content slide's title should be a full sentence stating that slide's
conclusion, under 15 words. Flag topic-label titles ("Market overview", "Architecture") outside
the appendix, and rewrite them from the slide's own body.

BUDGETS — flag any statement slide over 12 words, any concept slide over 40 words of body, any
table over 5 rows with verbose cells, and any slide spending the accent color more than once.
For each, say which: split the slide, move to a .cols layout, or cut to one sentence.
```

- **PASS** → apply the change log and export.
- **BLOCK** → revise per the diagnosis and re-run the gate. After 2 BLOCKs, stop and surface both
  diagnoses to the user instead of exporting.

Act on every flag. A split slide costs two minutes; a dense deck sent to a client costs a revision
cycle.

### Step 9: Export

Run Marp CLI to generate outputs:

```bash
# PDF (primary deliverable)
npx --yes @marp-team/marp-cli "<source>.md" --pdf --allow-local-files -o "<source>.pdf"

# HTML (interactive, with slide navigation)
npx --yes @marp-team/marp-cli "<source>.md" --html --allow-local-files -o "<source>.html"

# PPTX (only if user requests editable format)
npx --yes @marp-team/marp-cli "<source>.md" --pptx --allow-local-files -o "<source>.pptx"
```

Marp renders through headless Chrome, which fetches the Google Fonts `@import` over the network. An
offline or proxied run silently falls back to a system face and the deck looks wrong in a way no
error reports. Step 10 checks for it.

### Step 10: Verify

After export, confirm:
- [ ] PDF generated without errors
- [ ] HTML generated without errors
- [ ] Slide count matches plan
- [ ] **Fonts actually loaded** — open the PDF and check a heading is Lora (serif, tight) and a label
      is IBM Plex Mono. A deck rendered in Times or Helvetica means the `@import` did not resolve;
      re-run with network access rather than shipping it
- [ ] No slide flagged by the editorial gate was left unaddressed
- [ ] Report file locations to user

## Output

Tell the user:
1. What files were created and where
2. Slide count and estimated talk time (~30 seconds per slide)
3. How to edit (it's just markdown) and re-export

## Narrated deck video (`--video` mode)

`/do-presentation <topic> --video` produces a **narrated MP4** of the deck: each slide held on screen for the length of its spoken narration, voiceover muxed in, exported as a single `deck.mp4` next to the deck.

This mode depends on a repo-provided deck-video CLI that owns the full compositing pipeline (Marp PNG-per-slide export → per-slide TTS synthesis → ffmpeg mux). The skill does not re-implement compositing; it authors the deck (with per-slide narration blocks) and shells out to that CLI.

- **Context file present** → it declares the deck-video CLI invocation and the per-slide narration-block schema. Author the deck with one narration comment per slide and invoke the declared CLI exactly as specified.
- **Context file absent** → `--video` is unavailable in this repo. Produce the static deck (PDF/HTML/PPTX) as the deliverable and tell the user that narrated-video export requires a repo-provided CLI this repo does not declare. The static-export flow above is unaffected.

## Narration / voiceover

If the user wants a spoken voiceover or narration track as its own audio file (separate from the `--video` mode), **defer to `/do-voice-recording`** — it is the canonical text-to-speech step (portable TTS-CLI resolution, voice catalog, prosody rules). Feed it the per-slide speaker notes. Do not hand-roll synthesis for this path.
