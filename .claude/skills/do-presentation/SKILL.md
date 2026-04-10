---
name: do-presentation
description: "Use when creating a presentation about a feature, concept, or system. Researches the topic, structures content for accessibility, generates diagrams, and exports polished Marp slides styled to the repo's design system. Triggered by 'make a presentation', 'create slides', 'do-presentation', or 'explain this as a deck'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
argument-hint: "<topic or feature name>"
context: fork
---

# Make a Presentation

Creates polished, educational presentations about any feature, concept, or system in the current repo. Outputs Marp markdown with PDF and HTML exports, styled to match the repo's design system.

## What this skill does

1. Researches the topic deeply across the codebase
2. Structures content using proven educational frameworks (high-school accessible)
3. Detects the repo's design system and builds a matching Marp theme
4. Generates diagrams (Mermaid/ASCII) for architecture and flows
5. Exports to PDF, HTML, and optionally PPTX

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

Read `CONTENT_GUIDE.md` for educational best practices. Structure slides following this proven flow:

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

Adjust count based on topic complexity. Aim for **one idea per slide**.

### Step 4: Detect and build the theme

Read `THEME_DETECTION.md` for the full detection process. Quick version:

1. Search for CSS/design tokens in the repo:
   - `**/*.css`, `**/tailwind.config.*`, `**/theme.*`, `**/variables.*`, `**/tokens.*`
   - `**/styles/**`, `**/design-system/**`, `**/ui/**`
2. Extract: background colors, text colors, accent colors, fonts, border radius, spacing
3. If no design system found, use a clean dark theme as default
4. Build the Marp `style:` block from extracted tokens

### Step 5: Generate diagrams

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

### Step 6: Write the Marp markdown

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

### Step 7: Export

Run Marp CLI to generate outputs:

```bash
# PDF (primary deliverable)
npx --yes @marp-team/marp-cli "<source>.md" --pdf --allow-local-files -o "<source>.pdf"

# HTML (interactive, with slide navigation)
npx --yes @marp-team/marp-cli "<source>.md" --html --allow-local-files -o "<source>.html"

# PPTX (only if user requests editable format)
npx --yes @marp-team/marp-cli "<source>.md" --pptx --allow-local-files -o "<source>.pptx"
```

### Step 8: Verify

After export, confirm:
- [ ] PDF generated without errors
- [ ] HTML generated without errors
- [ ] Slide count matches plan
- [ ] Report file locations to user

## Output

Tell the user:
1. What files were created and where
2. Slide count and estimated talk time (~30 seconds per slide)
3. How to edit (it's just markdown) and re-export

## Version history
- v1.0.0 (2026-04-10): Initial — research, structure, theme detection, Marp export
