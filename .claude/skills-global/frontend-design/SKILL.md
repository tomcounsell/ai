---
name: frontend-design
description: "Build distinctive frontend interfaces that avoid generic AI aesthetics. Use when building web components, pages, artifacts, posters, or apps."
---

Produce production-grade frontend interfaces with a committed, distinctive point of view. Success: an interface that makes someone ask "who made this?", never "which AI made this?". The code works, the details are meticulous, and every choice reads as intentional.

## Commit to a direction first

Before writing code, decide the purpose and audience, the constraints (framework, performance, accessibility), the one thing someone will remember, and a tone executed with precision (brutally minimal, maximalist, retro-futuristic, editorial, brutalist, luxury, playful, industrial, and so on; inspiration, not a menu). Bold maximalism and refined minimalism both work; intentionality is the standard, not intensity. Match implementation complexity to the vision, and vary themes, fonts, and palettes across pieces rather than converging on one look.

## House positions

- **Type**: a distinctive display face paired with a refined body face on a real modular scale with strong size and weight contrast.
- **Color**: a committed palette with a dominant color and sharp accents, neutrals tinted toward the brand hue, never pure `#000` or `#fff`; prefer `oklch`, `color-mix`, `light-dark`.
- **Layout**: rhythm from varied spacing (tight groups, generous separations); asymmetry, overlap, and deliberate grid-breaks for emphasis.
- **Motion**: conveys state change. One orchestrated page-load reveal beats scattered micro-interactions. Animate `transform` and `opacity` only (`grid-template-rows` for height), exponential ease-out, never bounce or elastic, and honor `prefers-reduced-motion`. CSS-only for HTML; the Motion library in React when available.
- **Interaction**: optimistic UI, progressive disclosure, empty states that teach, a real button hierarchy (never every button primary), no repeating what the user can already see.
- **Responsive**: mobile-first, fluid (`clamp`), container queries, 44px touch targets, no hover-only interactions; adapt each context rather than shrinking it.
- **Words**: every word earns its place; buttons say what happens ("Save changes", not "OK").
- **Decoration** (grain, noise, gradient mesh, geometric pattern, custom cursor) only when it does brand work.

## The tells to avoid

These mark output as machine-made. Before delivering, ask whether someone would believe "AI made this" at a glance; if yes, find the element giving it away and redesign it.

- Inter, Roboto, Arial, Open Sans, or system-ui as the only face; monospace as a "technical" shorthand.
- Cyan-on-dark, purple-to-blue gradients, gradient text on headings or metrics, dark mode with neon glow as the default, gray text on colored backgrounds.
- Everything in cards, cards inside cards, endless same-sized icon-heading-text card grids, the hero-metric template (big number, small label, supporting stats, gradient accent), everything centered.
- Glassmorphism everywhere, a thick colored border on one side of a rounded element, decorative sparklines with no data, generic drop-shadow rounded rectangles, a large rounded icon above every heading, reflexive modals.
- Current-model defaults: a cream or off-white ground, an italic accent word in the headline, numbered 01/02/03 section labels, monospace eyebrow labels, pill buttons everywhere, emoji bullets. A brand that uses one of these on purpose keeps it; the tell is reaching for it by reflex.
