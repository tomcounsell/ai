---
name: frontend-design
description: >
  Designs frontend interfaces with a committed aesthetic point of view instead of generic AI defaults. Use when building or restyling any UI surface: web pages, components, dashboards, landing pages, forms, empty states, artifacts, or whole applications. Covers typography, color, layout, motion, and UX copy.
---

Produce production-grade frontend interfaces with a committed, distinctive aesthetic point of view. Success: an interface that makes someone ask "who made this?" — never "which AI made this?". The code must work, the details must be meticulous, and every choice must read as intentional.

## Commit to a direction first

Before writing code, decide:

- **Purpose**: what problem this interface solves, and for whom.
- **Tone**: pick one extreme and execute it with precision — brutally minimal, maximalist chaos, retro-futuristic, organic/natural, luxury/refined, playful/toy-like, editorial/magazine, brutalist/raw, art deco/geometric, soft/pastel, industrial/utilitarian... These are inspiration, not a menu; design one true to the context.
- **Constraints**: framework, performance, accessibility requirements.
- **Differentiation**: the one thing someone will remember.

Bold maximalism and refined minimalism both work. Intentionality is the standard, not intensity.

## Plan, review, then build

Work in two passes, and open no code file during the first.

**Pass 1: the plan.** Write a compact token system, short enough to take in at a glance.

- **Color**: 4 to 6 named hex values for the base palette.
- **Type**: the typefaces and the role each one plays.
- **Layout**: the layout concept as one-sentence prose plus an ASCII wireframe. Sketch two or three and compare them. State the alignment: left, centered, or justified.
- **Principles**: what makes this page specific to this brief.

**Pass 2: review the plan against the brief.** Ask of every part: would a similar prompt for a different product land in the same place? Work that similar prompt through and see. Anything that arrives at the same answer is a default rather than a decision, so revise it and say what changed and why. Write code only once the plan survives this pass, and then follow the revised plan.

Where the brief pins down a visual direction, follow it exactly. The brief's own words win, including when it asks for one of the looks named under the slop test below. Where the brief leaves an axis free, spend that freedom on something other than the default.

While writing the code, watch CSS selector specificity. Type-based selectors (`.section`) and element-based ones (`.cta`) cancel each other out easily, most often on the padding and margin between sections.

## Craft guidance

Each area has a reference file with technique depth — load it when actually working in that area, not preemptively.

### Typography — [reference](reference/typography.md)

Pair a distinctive display font with a refined body font on a modular scale with real size/weight contrast between levels. The invisible defaults (Inter, Roboto, Arial, Open Sans, system-ui as the sole typeface) and monospace-as-"technical"-shorthand are what make sites look AI-generated.

### Color & theme — [reference](reference/color-and-contrast.md)

Commit to a cohesive palette: dominant colors with sharp accents, neutrals tinted toward the brand hue, never pure #000 or #fff. Use modern color functions (oklch, color-mix, light-dark). Disqualifying tells: the AI palette (cyan-on-dark, purple-to-blue gradients), gradient text on headings or metrics, defaulting to dark mode with glowing neon accents, gray text on colored backgrounds (use a shade of the background instead).

### Layout & space — [reference](reference/spatial-design.md)

Create rhythm through varied spacing — tight groupings, generous separations, fluid values that breathe on larger screens. Embrace asymmetry, overlap, diagonal flow, and intentional grid-breaks for emphasis. Tells to avoid: everything wrapped in cards, cards nested inside cards, identical same-sized card grids repeated endlessly, the hero-metric template (big number + small label + supporting stats + gradient accent), everything centered.

### Visual details

Atmosphere comes from purposeful decoration matched to the aesthetic: gradient meshes, noise textures, grain overlays, geometric patterns, layered transparencies, custom cursors. Decoration that isn't doing brand work is noise — glassmorphism everywhere, rounded elements with a thick colored border on one side, decorative sparklines that convey no data, rounded rectangles with generic drop shadows, large rounded icons above every heading, and reflex-reach modals all read as filler.

### Motion — [reference](reference/motion-design.md)

Motion conveys state change — entrances, exits, feedback. One well-orchestrated page load with staggered reveals beats scattered micro-interactions. Animate transform and opacity only (grid-template-rows transitions for height); exponential ease-out curves (quart/quint/expo); never bounce or elastic. Prefer CSS-only for HTML; Motion library for React when available.

### Interaction — [reference](reference/interaction-design.md)

Make interactions feel fast (optimistic UI — update immediately, sync later) and every interactive surface feel intentional. Progressive disclosure: simple first, sophistication revealed through interaction. Empty states teach the interface. Button hierarchy matters — ghost buttons, text links, secondary styles; never every button primary. Never repeat information the user can already see.

### Responsive — [reference](reference/responsive-design.md)

Mobile-first, fluid (clamp/min/max), container queries for component-level response, 44×44px minimum touch targets, no hover-only interactions. Adapt the interface for each context — don't just shrink or amputate it.

### UX writing — [reference](reference/ux-writing.md)

Every word earns its place.

## The AI slop test

Before delivering, ask: if you told someone "AI made this," would they believe it immediately? If yes, find which fingerprint gave it away — the tells named above are the fingerprints of 2024-2025 AI output — and redesign that element.

### Current slop calibration

Generated design right now clusters around five looks. Each is legitimate for some brief, so a match is a signal to re-check the reasoning rather than a ban.

1. Warm cream background (near #F4F1EA), high-contrast serif display, terracotta or warm-clay accent. Often near #D97757, Anthropic's own Claude-interaction accent, so on a user's brief it reads as a tell.
2. Near-black background carrying a single bright acid-green or vermilion accent.
3. Broadsheet layout: hairline rules, zero border-radius, dense newspaper columns.
4. The SaaS-card kit: content chopped into identical rounded cards, one border-radius on everything regardless of hierarchy, the same soft grey shadow (rgba(0,0,0,.1)) under each, gradient washes as decoration.
5. Template chrome that appears whatever the subject: a tracked-out ALL-CAPS eyebrow above every heading, meta strings joined with middle dots ("A · B · C"), labels built as "WORD — fragment" with a spaced em dash, tinted near-black (#0B0B0B, #111) standing in for black, a monospace face for small data labels, a "→" appended to link and button text.

These are defaults that appear regardless of subject. Recalibrate the list as the clusters move.

## Implementation principles

Match implementation complexity to the vision: maximalism needs elaborate code, animation, and effects; minimalism needs restraint and precision in spacing, typography, and subtle detail. Interpret creatively and make unexpected choices genuinely designed for this context. NEVER converge on common choices across generations — vary themes, fonts, and aesthetics deliberately.

Claude is capable of extraordinary creative work. Don't hold back — commit fully to a distinctive vision.
