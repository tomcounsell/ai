---
name: do-design-review
description: "Review an existing web UI against premium design criteria. Screenshots pages and evaluates visual hierarchy, typography, color, spacing, consistency, and more. Use when the user wants to evaluate design quality or says 'review this design', 'check the UI', or provides a URL for design feedback."
allowed-tools: Bash(agent-browser:*)
---

This skill evaluates existing web interfaces against premium design criteria. It is the review-time companion to `/frontend-design` — that skill builds to the standard; this one audits against it.

Be opinionated. Call out what fails clearly. "Acceptable" is not a compliment.

## When to Use

Trigger this skill when the user:
- Provides a URL and asks for design feedback
- Says "review this design", "check the UI", "what do you think of this interface"
- Asks whether something looks professional, polished, or premium
- Wants to know what to improve before launching

## Variables

- `url` (required): The URL to review. Can be a local dev server (`http://localhost:3000`) or a deployed URL.
- `--pages` (optional): Comma-separated paths to screenshot explicitly (e.g. `/,/about,/pricing`). If omitted, the skill discovers pages by following navigation links from the start URL.

## Workflow

### Step 1: Open and Screenshot the Starting Page

```bash
agent-browser open <url>
agent-browser screenshot
```

Take a screenshot immediately after opening. Do not interact yet — capture the first impression.

### Step 2: Discover Pages

**If `--pages` was provided:** Navigate to each listed path in turn and screenshot each one.

```bash
agent-browser open <url>/about
agent-browser screenshot
# repeat for each path
```

**If `--pages` was NOT provided:** Take a snapshot to find navigation elements, then follow nav links to discover reachable pages. Screenshot each unique page. Stop after visiting 6 pages or exhausting navigation links, whichever comes first.

```bash
agent-browser snapshot -i
# identify nav links in the snapshot
agent-browser click @e<nav-link-ref>
agent-browser screenshot
# repeat for each navigation destination
```

Desktop viewport only. Do not resize for mobile in v1.

### Step 3: Evaluate

Evaluate ALL collected screenshots against the 10 rubric dimensions below. Do not rush — look carefully at each screenshot before scoring.

### Step 4: Produce the Report

Output the structured report format defined in the Output Format section. Every row in the evaluation table must have a specific finding, not a generic one.

---

## Evaluation Rubric

Score each dimension: ✅ **Premium**, ⚠️ **Acceptable**, or ❌ **Needs work**.

Be specific. "Typography could be improved" is a non-answer. "The heading and body text use the same font weight, eliminating any sense of hierarchy between the two" is a finding.

### 1. Visual Hierarchy

Does the page establish clear focal points? Can a user scan it in under 3 seconds and know what the primary action or message is? Does whitespace guide the eye, or does everything compete at equal weight?

Look for: a dominant heading or hero moment, intentional use of scale and contrast to direct attention, meaningful negative space between sections, and clear visual separation between primary and secondary content.

Fail conditions: everything set at similar size and weight, walls of content with no breathing room, no obvious "start here" signal, primary CTA blending into surrounding elements.

### 2. Typography

Does the page use a deliberate typographic hierarchy — a display font for headings, a body font for copy — with meaningful size and weight contrast between levels? Are the fonts interesting and purposeful, or default and forgettable?

Look for: a clearly distinct heading font paired with a complementary body font, a modular type scale (not arbitrary sizes), readable line length (45–75 characters), appropriate line height for body text.

Fail conditions: Inter, Roboto, Arial, Open Sans, or system-ui as the sole typeface; heading and body text at identical or near-identical weights; type sizes that jump arbitrarily rather than following a scale; monospace used as a lazy "technical" signal.

### 3. Color & Contrast

Does the palette feel cohesive and intentional? Is there a dominant color with one or two sharp accents, or a timid even spread across too many colors? Do text elements meet contrast standards?

Look for: a committed palette with clear hierarchy (background → surface → accent), neutrals tinted toward the brand hue, accent color used sparingly for emphasis, no pure black (#000) or pure white (#fff).

Fail conditions: cyan-on-dark or purple-to-blue gradients ("AI palette"), gradient text on headings, gray text on colored backgrounds, more than four competing accent colors, washed-out low-contrast text, dark mode with glowing neon accents as the default aesthetic.

### 4. Spacing & Alignment

Does the layout use a consistent spacing system with intentional variation — tight groupings for related elements, generous gaps between sections? Are elements aligned to an implicit grid?

Look for: a clear vertical rhythm, content that aligns to a column structure, spacing that increases as content relationship decreases (proximity principle), fluid spacing that doesn't feel cramped at smaller widths.

Fail conditions: identical padding everywhere (no rhythm), elements that appear slightly misaligned with no intentional reason, sections that bleed together without clear separation, margins that feel arbitrary rather than derived from a base unit.

### 5. Visual Details

Is imagery high-quality and purposefully cropped? Do decorative elements reinforce the aesthetic or feel like filler? Are aspect ratios consistent within grid patterns?

Look for: intentional decorative elements (noise textures, geometric patterns, layered transparencies, gradient meshes) that match the design tone, consistent image treatment across the layout, details that reward close inspection.

Fail conditions: rounded rectangles with generic drop shadows, glassmorphism used decoratively everywhere, icons-above-every-heading template layouts, sparklines or tiny charts used as decoration with no data meaning, rounded elements with a thick colored border on one side (the lazy accent), placeholder-quality imagery that hasn't been swapped out.

### 6. Micro-interactions

Do interactive elements signal their interactivity? Do hover and focus states feel designed or like browser defaults? Even without live interaction, does the HTML structure suggest transitions and feedback were considered?

Look for: styled focus rings that match the brand (not the default blue outline), hover states that change more than just cursor, transitions on state changes, button states (default, hover, active, disabled) that look distinct.

Infer from HTML/CSS if direct interaction isn't possible: presence of transition properties, custom focus styles, aria attributes, and disabled states all signal intentional interaction design.

Fail conditions: buttons with no visible hover state, browser-default blue focus outlines on non-default-styled elements, no visual feedback on interactive surfaces, all buttons styled as primary with no hierarchy.

### 7. Consistency

Do repeated elements look like they belong to the same system? Is there visual rhythm — the same pattern appearing in recognizable variations — or does each section feel designed independently?

Look for: a coherent component vocabulary (button styles, card treatments, heading patterns), consistent use of the spacing system across sections, repeating motifs (shapes, colors, treatments) that create a unified identity.

Fail conditions: two different button styles with no apparent reason, sections that look like they came from different templates, inconsistent corner radii between components, alternating card styles with no clear logic, heading sizes that don't follow a scale across pages.

### 8. Trust Signals

Does the page look like someone cared? Are there signs of professional polish — deliberate details, nothing overlooked — or does it have the roughness of an unfinished or hastily assembled interface?

Look for: correct punctuation and typographic details (real quotes, em-dashes), no placeholder text left in production, footer completeness (privacy policy, company info), social proof rendered cleanly (testimonials, logos), error states and edge cases that appear considered.

Fail conditions: "Lorem ipsum" in any visible area, broken images or layout artifacts, misaligned elements that suggest no QA pass, footer with three words and nothing else, testimonials or logos cropped or inconsistently sized.

### 9. Mobile Responsiveness

Even though we only screenshot desktop in v1, assess whether the layout would likely hold up at mobile width. Does the structure rely on side-by-side columns that would collapse badly? Are text sizes large enough to scale down gracefully?

Look for: layouts that suggest fluid or column-based structure (flexbox/grid), font sizes large enough to remain readable at 375px width, no hover-dependent interactions without alternatives, touch targets that appear to be at least 44×44px.

Note: This is an informed prediction, not a tested measurement. Flag specific concerns rather than giving a blanket pass.

Fail conditions: four-column grids with no apparent breakpoint strategy, tiny font sizes that would become unreadable on mobile, interactions that are clearly hover-only (dropdown menus with no tap alternative), full-width decorative elements that would break at narrower widths.

### 10. AI Slop Check

Would someone immediately recognize this as AI-generated? Does it feel templated and generic — or does it have a point of view? Could this have been made by a specific studio or designer with a distinctive sensibility?

Look for signs of intentionality: an unexpected aesthetic choice, a typeface that isn't a default pick, a layout that breaks the grid deliberately, a color that isn't "safe." The goal is an interface that makes someone ask "who made this?" rather than "which AI made this?"

Fail conditions (direct from `/frontend-design` DON'Ts):
- Big number + small label + supporting stats + gradient accent (hero metric template)
- Rounded icon above every heading in a feature grid
- Cards inside cards inside cards
- Same-sized cards with icon + heading + text, repeated endlessly
- Cyan-on-dark or purple gradient color palette
- Dark mode with glowing neon accents as the default
- Gradient text for impact on headings or metrics
- Everything centered, nothing asymmetric
- Every button styled as primary

---

## Rating Scale

| Rating | Meaning |
|--------|---------|
| ✅ **Premium** | Exceptional — deliberate, polished, no action needed |
| ⚠️ **Acceptable** | Passes the bar — functional, inoffensive, but with clear room to improve |
| ❌ **Needs work** | Specific issue identified — actionable fix recommended |

"Acceptable" is not a compliment. It means "won't embarrass you, but won't impress anyone either."

---

## Output Format

Produce the following report after evaluating all screenshots:

```markdown
## Design Review: [URL]

**Pages reviewed:** [list pages that were screenshotted]

---

### Evaluation

| Dimension | Rating | Findings |
|-----------|--------|----------|
| Visual hierarchy | ✅/⚠️/❌ | [specific observation] |
| Typography | ✅/⚠️/❌ | [specific observation] |
| Color & contrast | ✅/⚠️/❌ | [specific observation] |
| Spacing & alignment | ✅/⚠️/❌ | [specific observation] |
| Visual details | ✅/⚠️/❌ | [specific observation] |
| Micro-interactions | ✅/⚠️/❌ | [specific observation] |
| Consistency | ✅/⚠️/❌ | [specific observation] |
| Trust signals | ✅/⚠️/❌ | [specific observation] |
| Mobile responsiveness | ✅/⚠️/❌ | [specific observation] |
| AI Slop Check | ✅/⚠️/❌ | [specific observation] |

---

### Top 3 Improvements

**1. [Most impactful fix — name the problem, not just the solution]**
[Specific, actionable description: what to change, where, and why it matters. Not "improve spacing" — "increase the gap between the nav bar and the hero section from 12px to 48px; right now they read as connected, not separated."]

**2. [Second priority]**
[Specific, actionable description]

**3. [Third priority]**
[Specific, actionable description]

---

### Overall Assessment
[1-2 sentences. State the overall quality level plainly. Call out the design's strongest quality and its most critical weakness. Do not hedge.]
```

Findings must be specific. Identify the exact element, section, or pattern that is failing — not a category. "The hero heading and the body paragraph below it use identical font weights, making the page feel flat" is a finding. "Typography needs work" is not.

Top 3 improvements must be actionable. A developer or designer should be able to act on each recommendation without asking a follow-up question.
