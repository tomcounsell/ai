# Branding Brief: Yudame Research / Cuttlefish

## The Vibe

This is a brand for people who think in systems. It looks like the notebook of someone who reads architectural theory for pleasure and keeps their references organized by ontological category. The aesthetic is what happens when an INTJ designs a research platform — every element is there for a reason, nothing is decorative, and the restraint itself communicates confidence.

The dominant visual experience is **black ink on warm cream paper**. Not white — cream. The kind of off-white you find in a Moleskine or a university press monograph. Against this quiet backdrop, the only color that earns its place is **red** — used the way an architect uses a red pen on a site plan: to annotate, to emphasize structure, to mark what matters. Red doesn't fill surfaces. It draws lines, labels critical elements, and signals active states. Everything else is grayscale.

The typography is precise without being cold. Monospace type (IBM Plex Mono) handles labels, data, and navigation — it says "this is a system built by someone who reads terminal output." Body text is clean sans-serif (Inter) that stays out of the way. Headers are uppercase, letter-spaced, understated. The overall feeling is a technical document that happens to be beautiful.

The layout draws from architectural plan drawings: visible grids, generous white space used as structural negative space (not just emptiness), systematic numbering conventions, and information organized with the rigor of a site analysis diagram. Components have square corners. Borders are precise. The grid isn't hidden — it's celebrated as part of the design language.

This brand exudes high IQ without performing it. There are no clever taglines, no "look how smart we are" moments. The intelligence is in the structure — in how information is organized, how hierarchy is established through spacing rather than decoration, and how the interface trusts the user to understand what they're looking at without being guided by hand. It respects the viewer's intelligence by refusing to dumb anything down or add visual noise for engagement.

The personality is **INTJ**: strategic, systematic, independent-minded. It values depth over breadth, precision over approximation, and silence over noise. It would rather show you a well-structured data hierarchy than a colorful infographic. It prefers one red annotation mark on a clean page over a dozen design flourishes.

If someone showed you this interface and asked "what kind of person made this?" the answer would be: someone who owns more books than furniture, thinks Le Corbusier was onto something, and believes the best design is the design you don't notice until you realize everything just works.

---

## Visual Identity

### Color System

The palette is intentionally constrained. Black on cream is the design. Color is the exception.

**Backgrounds**
```
Cream:      #FAF9F6   — Primary surface (paper-like warmth)
White:      #FFFFFF   — Cards and containers
Warm Gray:  #F5F4F1   — Subtle section differentiation
```

**Ink (Text)**
```
Black:      #1A1A1A   — Headlines, emphasis
Charcoal:   #2D2D2D   — Body text
Gray:       #5A5A5A   — Labels, secondary text
Light Gray: #8A8A8A   — Tertiary text, footnotes
```

**Borders**
```
Dark:       #3A3A3A   — Primary structure
Medium:     #C4C4C4   — Standard dividers
Light:      #E5E5E5   — Subtle separators
```

**Red Annotation Accent** — The architect's red pen
```
Primary:    #B91C1C   — Default accent (annotations, active states, emphasis)
Light:      #DC2626   — Hover states
Dark:       #7F1D1D   — Deep accents, pressed states
Muted:      #991B1B   — Inline text emphasis
Subtle:     rgba(185, 28, 28, 0.08) — Background tint (use rarely)
```

**Secondary Warmth** — Gold for quiet diagram lines
```
Warm:       #B8935F   — Diagram lines, tertiary accents
Warm Light: #D4A574   — Subtle warmth touches
```

Red is never a surface color. It annotates, marks, and highlights. The moment red fills a large area, the design has gone wrong.

### Typography

**IBM Plex Mono** (300, 400, 500, 600)
- Labels, navigation, data displays, code
- Letter-spacing: 0.08em–0.15em for uppercase treatments
- The "system" voice — technical, precise, trustworthy

**Inter** (300, 400, 500, 600, 700)
- Body text, descriptions, paragraphs
- Clean, readable, stays out of the way
- The "human" voice — warm, clear, unpretentious

**Hierarchy**
| Use | Font | Size | Weight | Transform |
|-----|------|------|--------|-----------|
| Page title | Inter | 2rem | 600 | uppercase, 0.05em |
| Section header | Inter | 1.25rem | 600 | uppercase, 0.05em |
| Body text | Inter | 0.875–0.95rem | 400 | normal |
| Label | IBM Plex Mono | 0.75rem | 500 | uppercase, 0.08em |
| Data/code | IBM Plex Mono | 0.75–0.875rem | 400 | normal |
| Annotation label | IBM Plex Mono | 0.7rem | 500 | uppercase, 0.08em (red) |

### Layout

**8px Grid** — The mathematical foundation. All spacing is a multiple.
```
XS:   8px    (1x)    — Micro-spacing
SM:   16px   (2x)    — Within components
MD:   24px   (3x)    — Component padding
LG:   32px   (4x)    — Between elements
XL:   48px   (6x)    — Between components
2XL:  64px   (8x)    — Between sections
3XL:  96px   (12x)   — Major section breaks
```

**Page widths**: 1000px for landing pages, 1200px for app pages.

**Square corners** on everything except buttons and status dots. No border-radius on cards, containers, sections, or code blocks. This is architectural — precise, structural, deliberate.

**Visible grids** are part of the aesthetic, not hidden infrastructure. The `technical-bg` class renders an 8px grid that can be shown behind content where appropriate.

### Components

**Buttons**
- Primary: white background, dark border, monospace uppercase. Inverts on hover.
- Accent: white background, red border, red text. Fills red on hover. Used sparingly — one per page maximum.

**Cards**
- `card-technical`: 1px medium border, white background, darkens on hover
- `technical-spec-box`: 2px dark border with 6px-inset inner border (hero sections)
- `card-corner-marks`: No full border, just 16px L-shaped marks at top-left and bottom-right (blueprint aesthetic)

**Labels**: Technical numbering convention — `MCP_SERVER_01`, `TOOL_03`, `BENEFIT_01`. Monospace, uppercase, gray. Annotation labels use red for emphasis.

**Dividers**: 1px line with centered 6px circular node. Simple, architectural.

**Status indicators**: 8px circles. Green (operational), orange (development), red (offline).

### What This Brand Is Not

- Not dark mode with glowing accents
- Not gradient-heavy or glossy
- Not rounded, soft, or "friendly"
- Not colorful — color is the exception, not the rule
- Not decorated — every element has a structural purpose
- Not trying to look "techy" with circuit boards or neon
- Not performing intelligence — the intelligence is structural

---

## Voice & Tone

Precise but not pedantic. Knowledgeable without being condescending. Technical clarity over marketing language. A subtle dry wit where appropriate.

**Content labeling**: Systematic, versioned (`MCP_SERVER_01` not "Server 1")
**CTAs**: Uppercase, imperative, no filler ("VIEW DOCS" not "Learn More")
**Descriptions**: Short, declarative, fact-based

---

## Brand Personality

| Spectrum | Position |
|----------|----------|
| Friend ←→ Authority | 75% toward Authority |
| Serious ←→ Playful | 70% toward Serious |
| Reliable ←→ Risk-Taking | 55% toward Reliable |
| Contemporary ←→ Classic | 45% toward Classic |
| Minimal ←→ Maximal | 85% toward Minimal |
| Warm ←→ Cold | 60% toward Warm |

**INTJ energy**: Strategic vision, systematic execution, independent thinking, depth over breadth. The brand doesn't follow trends — it has a point of view and commits to it completely.

---

## Known Weaknesses & Guardrails

The restraint that defines this brand creates predictable failure modes. Every page and feature should be stress-tested against these:

### 1. Sparseness masquerading as simplicity
Minimalism works when every element carries weight. When a page has a title, three bullet points, and nothing else, it reads as unfinished — not restrained. **Guardrail:** If a section has fewer than three visual anchors (heading, body copy, image/diagram, CTA, code block), it needs to earn its emptiness or be tightened.

### 2. No clear action on landing
The brand's authority posture ("we don't beg for clicks") easily produces hero sections that are inert title cards. Users bounce without a next step. **Guardrail:** Every top-of-page section must have a visible CTA or an obvious scroll affordance. "Get Started" linking to the install guide is the minimum. A one-line code snippet or architecture diagram is better.

### 3. Monospace overuse kills readability
IBM Plex Mono is the brand's voice — but when everything is monospace, nothing is. Long-form copy in monospace is objectively harder to read. **Guardrail:** Monospace is for labels, navigation, data, and code. Body paragraphs, value propositions, and descriptions must be Inter. If a section is mostly monospace and mostly prose, something is wrong.

### 4. Flat visual hierarchy
When every section uses the same size, weight, and spacing, the page becomes a wall of equal-weight content. The brand's refusal to decorate makes this worse. **Guardrail:** Use size, spacing, and the red accent to create exactly two focal points per page. The hero and the primary content section should feel visibly dominant. Everything else recedes.

### 5. Labels without meaning
Technical labeling (`BENEFIT_01`, `MCP_SERVER_01`) is a brand signature, but labels are not copy. "Standardized Interface" as a heading with no supporting sentence tells the user nothing. **Guardrail:** Every label must be followed by a user-centric sentence that answers "why should I care?" Labels name the thing; body copy sells it.

### 6. Energy deficit
Black-on-cream with restrained typography can feel like a museum placard: beautiful, passive, zero urgency. The brand needs conviction without resorting to color or animation. **Guardrail:** Use confident, opinionated copy. Diagrams and code snippets inject life. A well-placed visual (architecture flow, usage example) does more than any gradient.

### 7. Decorative ambiguity
Elements like green status dots or UNDERSCORE_CASE naming work when they communicate something. When they're purely stylistic, they create confusion. **Guardrail:** Every visual element must pass the "what does this mean?" test. If a user would need the brand guide to understand it, it's decoration and should be cut or labeled.

---

## Implementation Files

| File | Purpose |
|------|---------|
| `static/css/brand.css` | Complete design system — variables, components, utilities |
| `static/css/source.css` | Tailwind v4 theme config with brand tokens |
| `static/css/base.css` | Primary color utilities, transitions, modal sizing |
| `docs/BRANDING_BRIEF.md` | This document |

---

## Aesthetic References

Mood board: https://www.cosmos.so/tomcounsell/yudame-research

Key reference themes:
- Architectural floor plans with red structural overlays
- Numbered ledger and graph paper grids
- Site analysis iteration diagrams (deep red fills on white)
- "Hierarchy of Insight" diagram (Data → Information → Knowledge → Wisdom)
- Audio hardware interfaces (white, monospace, red record indicator)
- Editorial stipple illustration
- Dot matrix and systematic data patterns
