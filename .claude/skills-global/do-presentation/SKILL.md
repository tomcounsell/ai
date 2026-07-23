---
name: do-presentation
description: "Create a polished Marp slide deck about a feature, concept, or system. Triggered by 'make a presentation', 'create slides', 'do-presentation', or 'explain this as a deck'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
argument-hint: "<topic or feature name>"
context: fork
---

# Make a Presentation

Produce a polished slide deck the audience actually understands: researched from the codebase, structured for the audience (educational or client-facing), themed to the repo's design system, and exported via Marp to PDF/HTML (PPTX on request). Success is judged at Step 10's verify checklist — exports exist, slide count matches plan, every review flag addressed.

## Repo Context Probe

If `.claude/skill-context/do-presentation.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The static-deck flow (research → structure → theme → diagrams → Marp export) is fully generic — it needs only `npx`/Marp and `curl`, no repo-specific tooling. The context file declares one optional capability: the **narrated `--video` mode** and the repo-provided CLI that powers it. When the file is absent (the common case in a foreign repo), the static deck (PDF/HTML/PPTX) is the deliverable and `--video` is unavailable.

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `CONTENT_GUIDE.md` | Structuring slide content — educational best practices, slide types, pacing |
| `THEME_DETECTION.md` | Building the Marp CSS theme — how to find and adapt the repo's design system |

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

### Step 4: Detect and build the theme

Read `THEME_DETECTION.md` for the full detection process. Quick version:

1. Search for CSS/design tokens in the repo:
   - `**/*.css`, `**/tailwind.config.*`, `**/theme.*`, `**/variables.*`, `**/tokens.*`
   - `**/styles/**`, `**/design-system/**`, `**/ui/**`
2. Extract: accent colors, fonts, border radius, spacing
3. If no design system found, use the clean light fallback theme
4. Build the Marp `style:` block from extracted tokens

**IMPORTANT: All presentations use light backgrounds.** Even if the repo's design system is dark, adapt it to light mode. Keep the accent colors and fonts, invert backgrounds to white/light gray, use dark text. See the "Light Mode Mandate" section in `THEME_DETECTION.md` for the full dark→light token mapping.

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

**Colorizing SVGs for dark backgrounds:**

Simple Icons SVGs have no fill color (default black — invisible on dark slides). Inject a fill:

```bash
# White (safe default for dark themes)
sed -i '' 's/<path/<path fill="#e6edf3"/' diagrams/logo-{slug}.svg

# Or use the brand's official color (Simple Icons provides these)
sed -i '' 's/<path/<path fill="#FF6600"/' diagrams/logo-{slug}.svg
```

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
- Use the repo's accent color for emphasis nodes

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
backgroundColor: <from-design-system>
color: <from-design-system>
style: |
  /* Theme CSS generated from repo design system */
  ...
---

<!-- _class: lead -->
# Title
subtitle

---

## Slide Title
content...
```

**Writing rules:**
- One idea per slide — if you need a scroll bar, split it
- Use `<!-- _class: lead -->` for section dividers
- Use tables over bullet lists when comparing things
- Use code blocks sparingly — only when the actual code IS the point
- Every 3rd-4th slide should be visual (diagram, table, or formatted example)
- Use `>` blockquotes for key takeaways or memorable quotes
- Bold key terms on first use
- Use analogies liberally — connect technical concepts to everyday things

**Include these utility CSS classes in every theme** — they get used on almost every deck:

```css
/* Two-column grid */
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 10px; }
.cols-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-top: 10px; }

/* Callout — neutral/info. Full border, NOT a left accent bar (see THEME_DETECTION "Avoid AI-Slop Tells") */
.stat {
  background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 6px;
  padding: 14px 20px; margin: 10px 0;
  font-size: 0.96em; font-weight: 600; color: #1a3a6b; line-height: 1.5;
}

/* Warning / risk callout. Full border, not a left accent bar. */
.warn {
  background: #fef3c7; border: 1px solid #fcd97a; border-radius: 6px;
  padding: 10px 18px; margin: 10px 0;
  font-size: 0.88em; color: #92400e; line-height: 1.5;
}

/* Option/path cards — for A/B/C decision slides */
.path-card {
  border: 1px solid #e5e7eb; border-radius: 6px;
  padding: 14px 16px; font-size: 0.86em;
}
.path-card strong { display: block; margin-bottom: 4px; }
```

Use `.cols` / `.cols-3` to break a slide that would otherwise be a dense list into scannable side-by-side sections. Use `.stat` for governing metrics and key facts. Use `.warn` for risks and blockers. Use `.path-card` inside `.cols-3` for A/B/C decision options.

**Callouts use a full border, never a single colored left-edge stripe** (`border-left: 4px solid …` with one rounded side). That stripe is the top "AI slop" tell — see the "Avoid AI-Slop Tells" section in `THEME_DETECTION.md`.

### Step 8: Self-review pass (before export)

Before running Marp, spawn a subagent to critique the draft. This catches structural and density issues before the user sees a bad first version.

**Spawn a `plan-reviewer` agent** with this prompt template:

```
Review this Marp presentation draft for two things only — structure and density.
Do not evaluate content correctness.

STRUCTURE: Does it open with Why (the audience's problem / context), then How (the approach),
then What (the specifics)? For client-facing decks especially, flag if the first 3 slides do not
establish who the audience is and what problem they are experiencing before any solution content appears.

DENSITY: List every slide that exceeds 6 lines of body text, or has more than 2 dense paragraphs,
or has a table with more than 5 rows and verbose cell text. For each, suggest: split into 2 slides,
convert prose to a .cols layout, or trim to a single key sentence.

Return a short list — flagged slides with one-line diagnosis each. No other feedback.
```

Act on every flag before exporting. A split slide costs 2 minutes. Sending a dense deck to a client costs a revision cycle.

### Step 8b: De-slop gate (before export)

Run `Skill('de-slop')` on the deck markdown as a **fresh-context review** — a subagent that gets only the deck file path, the medium ("presentation"), and the audience; never this drafting conversation. It removes AI-writing tells (slop vocabulary, rule-of-three slides, inflated-significance titles, bullet walls) and blocks hollow decks.

- **PASS** → proceed to export.
- **BLOCK** → revise per the diagnosis and re-run the gate. After 2 BLOCKs, stop and surface both diagnoses to the user instead of exporting.

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

### Step 10: Verify

After export, confirm:
- [ ] PDF generated without errors
- [ ] HTML generated without errors
- [ ] Slide count matches plan
- [ ] No slide flagged in the review pass was left unaddressed
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
