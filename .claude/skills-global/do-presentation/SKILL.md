---
name: do-presentation
description: "Create a polished Marp slide deck about a feature, concept, or system. Triggered by 'make a presentation', 'create slides', 'do-presentation', or 'explain this as a deck'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
argument-hint: "<topic or feature name>"
context: fork
effort: medium
---

# Make a Presentation

Produce a polished slide deck the audience actually understands: researched from the codebase, structured for the audience (educational, client-facing, or informational), set in the Yudame House theme, and exported via Marp to PDF/HTML (PPTX on request). **Done:** the Step 10 checks pass (exports exist, fonts loaded, slide count matches the plan, every editorial flag addressed) and the user has the file locations.

## Repo Context Probe

If `.claude/skill-context/do-presentation.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The static-deck flow (research → structure → theme → diagrams → editorial gate → Marp export) is fully generic — it needs only `npx`/Marp, `curl`, and network access for the Google Fonts import. No repo-specific tooling. The context file declares one optional capability: the **narrated `--video` mode** and the repo-provided CLI that powers it. When the file is absent (the common case in a foreign repo), the static deck (PDF/HTML/PPTX) is the deliverable and `--video` is unavailable.

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `CONTENT_GUIDE.md` | Structuring slide content — educational best practices, slide types, pacing |
| `THEME.md` | Writing the Marp front matter — the Yudame House theme, its style block, slide archetypes |

## Workflow

The topic comes from `$ARGUMENTS`; if empty, ask the user what to present on.

### Step 1: Scope the topic

Settle what the deck covers, the audience (default: general technical audience, high-school reading level), and the length (default: 10-15 slides, a 5-8 minute talk). Ask only if the scope is genuinely ambiguous.

### Step 2: Research

Research the topic in the codebase and docs (delegate to an Explore agent when the ground is wide) until you can answer: what it is in one plain sentence, why it exists, how it works simplified, its 3-5 key parts, and what is interesting about it (the clever bit or trade-off), with concrete examples and real data.

### Step 3: Design the slide structure

Read `CONTENT_GUIDE.md` for educational best practices.

**First, determine the presentation type — it changes the opening structure:**

| Type | Audience | Opening structure |
|---|---|---|
| **Educational / internal** | Technical teammates, general audience | What → How → Why it matters |
| **Client-facing / working session** | Client decision-makers, executives | Why (their problem) → How (the approach) → What (the specifics) |
| **Informational / descriptive** | Anyone receiving a factual report or overview | Subject first — a one-line scope/method note, then straight into the content |

The Why-first opening belongs to **persuasion and decision decks** — proposals, working sessions, decision briefs. For those, the first 3–4 slides must establish: (1) who the client is and what their operating reality looks like, (2) the problem they are experiencing in their own terms, (3) the governing principle or goal — before any solution, scope, or technical content appears. Opening with a solution before the client sees their problem reflected back is the single most common failure mode of a persuasion deck.

An **informational / descriptive** deck — an architecture overview, a vendor inventory, audit findings, a status report — skips the problem-reflection opening entirely. Its job is to describe accurately, not to persuade or drive a decision, and it has no problem to reflect back. Lead with the subject: one line stating scope and method, then the content itself. Grafting a problem opening onto a descriptive deck manufactures a crisis the audience never raised.

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

**Informational / descriptive structure:**
```
1. Title + one-line scope/method note (what this covers, how it was gathered)
2-N. The subject itself, one idea per slide — grouped by component, area, or finding
N-1. Summary (3 bullet takeaway)
N.  Appendix / further reading
```

Adjust count based on topic complexity. Aim for **one idea per slide**.

Write the outline as **action titles** — each line a full sentence stating that slide's conclusion,
not a topic label. Read the outline back top to bottom: it should read as the argument. If it reads
as a table of contents, the deck will too. See "Action titles" in `CONTENT_GUIDE.md`.

### Step 4: Apply the theme

There is one theme, **Yudame House**, and every deck uses it. This is not a decision to make or to
put to the user. Read `THEME.md`, paste its style block into the deck's front matter, and set the
deck slug in the masthead rule. Choose that slug yourself, a short uppercase line naming the project
and the deck's purpose. Nothing else in the block changes: no design-system detection, no per-deck
restyling, no adjusting `:root`.

A deck about another company's product still ships in Yudame House. Their brand appears as a logo
(Step 5), never in the slide's colors or type.

### Step 5: Collect brand logos

For polished decks only, fetch logos for brands central to a slide's content (skip internal or informal decks). Sources, in order:

```bash
# Simple Icons: monochrome SVGs, no auth. Slug list: https://raw.githubusercontent.com/simple-icons/simple-icons/develop/slugs.md
curl -s "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/{slug}.svg" -o diagrams/logo-{slug}.svg
sed -i '' 's/<path/<path fill="#1A1A1A"/' diagrams/logo-{slug}.svg   # theme ink, not default black
# Fallback, any domain (PNG)
curl -sL "https://www.google.com/s2/favicons?domain={domain}&sz=128" -o diagrams/logo-{name}.png
# SVG to PNG if needed (macOS): qlmanage -t -s 512 -o diagrams/ diagrams/logo-{slug}.svg; mv diagrams/logo-{slug}.svg.png diagrams/logo-{slug}.png
```

Embed with `![w:28](diagrams/logo-{slug}.svg)`: 24-32px inline or in table cells, 64-80px standalone. Prefer SVG (renders with `--allow-local-files`). Use theme ink, not the brand color: a brand color spends the slide's one accent, so use it only when the color is the point.

### Step 6: Generate diagrams

ASCII art in code blocks for simple flows (always renders), tables for comparisons and matrices, Mermaid for complex diagrams. The `mermaid-render` skill is user-invoked only, so read its `SKILL.md` and follow its render workflow to produce a PNG; if that is unavailable, include the diagram as a fenced block. Diagrams follow the rules in `CONTENT_GUIDE.md` (at most 7 nodes, labeled arrows, one red emphasis node) and sit in a `.figure` panel with a mono `.figure__meta` footer (`FIG. 01` left, caption right). Charts follow the "Charts" section of `THEME.md`; anything beyond a bar comparison goes through the `dataviz` skill carrying those constraints.

### Step 7: Write the Marp markdown

Create the presentation file. Location priority:
1. If user specifies a path, use that
2. If `~/work-vault/` exists, use `~/work-vault/<Project>/presentations/<slug>/<name>.md`, where
   `<Project>` is the vault folder for the subject (e.g. `Cyndra`, `Yudame`, `Popoto`). Decks live
   in the vault, not in the source repo. Each deck gets its own folder holding the `.md` and every
   export. `~/work-vault/` is a synced folder: commit and push after writing, per `~/.claude/CLAUDE.md`.
3. Otherwise, use `<repo-root>/presentations/<slug>/<name>.md`

**Marp file structure:**
```markdown
---
marp: true
theme: default
paginate: true
backgroundColor: '#FAF9F6'
color: '#2D2D2D'
style: |
  <the Yudame House style block from THEME.md, verbatim, with the deck slug set>
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

**Writing rules** (budgets and archetypes are detailed in `CONTENT_GUIDE.md`):
- One idea per slide — if you need a scroll bar, split it
- **Action titles.** Every content slide's `##` is a full sentence stating that slide's conclusion,
  under 15 words, and it is the largest text on the slide. `## Queue depth doubled after the retry
  change` beats `## Queue metrics`. A reader who sees only the titles should get the argument.
  Topic-label titles are for the appendix
- **Word budget by archetype.** Statement slide ≤ 12 words. Concept slide ≤ 40 words of body.
  Table ≤ 5 rows with short cells. Over budget means split the slide, not shrink the type
- **Accent budget.** One red per slide, spent on the value that matters. Count it before moving on
- The only slide classes are `cover`, `section` and `statement`. Diagram, table and number slides
  need no class; they are compositions of `.figure`, a markdown table, or `.big`. An undefined
  `_class` is a silent no-op
- Every 3rd-4th slide should be visual (figure plate, table, or `.big` number)
- Use `.rose` for the one governing quote or line, once per deck. A plain markdown `>` is the quiet
  treatment (hairline box, serif italic) and carries no such limit
- **Write each paragraph on one source line.** Marp renders a newline inside a paragraph as a line
  break, so markdown you wrapped for readability in the editor comes out broken mid-sentence on the
  slide. Let the source line run long

Layout components come from `THEME.md`: `.cols` / `.cols-3` break a slide that would otherwise be a
dense list into scannable sections, `.plate` holds a governing metric or key fact, `.note` is the red
margin annotation for a risk or caveat, `.card` inside `.cols-3` gives A/B/C decision options.

### Step 8: Editorial gate

One gate, run once, on the finished draft, per de-slop's "Cold read" section: a **foreground** Agent
call (`run_in_background: false`) whose prompt carries only the deck file path, the medium
(`presentation`), the audience, and the addendum below, says "you did not write this draft", and
invokes `Skill('de-slop')`. Wait for its verdict before publishing. It must not receive this drafting
conversation; the author of a draft is the worst judge of its slop.

Pass this addendum along with the standard inputs, since these checks are deck-shaped and de-slop's
generic catalog does not cover them:

```
Additional checks for this deck, alongside the standard pass:

STRUCTURE — match the check to the deck's job. A persuasion / decision deck opens with Why (the
audience's problem and context), then How (the approach), then What (the specifics); flag it if
the first three slides do not establish who the audience is and what problem they are experiencing
before any solution appears. An informational / descriptive deck leads with its subject after a
one-line scope note; flag any manufactured problem opening or framing built around what the
audience lacks.

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

`--html` enables raw HTML tags in the markdown. It is not an output-format flag — the format comes
from `--pdf` / `--pptx` / `--images` or the `-o` extension. Every Yudame House archetype is built from
`<div>` and `<span>` markup, so **every** export needs it. Raw HTML happens to be on by default in
the current CLI, which means leaving the flag off works right up until that default changes and a
deck silently exports with its layout stripped.

```bash
# PDF (primary deliverable)
npx --yes @marp-team/marp-cli "<source>.md" --pdf --html --allow-local-files -o "<source>.pdf"

# HTML (interactive, with slide navigation)
npx --yes @marp-team/marp-cli "<source>.md" --html --allow-local-files -o "<source>.html"

# PPTX (only if user requests editable format)
npx --yes @marp-team/marp-cli "<source>.md" --pptx --html --allow-local-files -o "<source>.pptx"
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

`/do-presentation <topic> --video` produces a narrated `deck.mp4` next to the deck, each slide held for the length of its narration. It needs a repo-provided deck-video CLI that owns compositing; the context file declares it and the per-slide narration schema. Without a context file, `--video` is unavailable: ship the static deck and say so.

## Narration / voiceover

If the user wants a spoken voiceover or narration track as its own audio file (separate from the `--video` mode), **defer to `/do-voice-recording`** — it is the canonical text-to-speech step (portable TTS-CLI resolution, voice catalog, prosody rules). Feed it the per-slide speaker notes. Do not hand-roll synthesis for this path.
