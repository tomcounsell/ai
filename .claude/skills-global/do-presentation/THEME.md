# Theme: Yudame House

One theme. Every deck uses it. It is the house design system from `yudame.ai` — black on cream,
Lora serif headings, Inter body, IBM Plex Mono labels, and a single red used the way an architect
uses a red pen. Adapted from the Swiss-poster structure of SlideSpeak's *Basel* theme
([presentation-design-prompts](https://github.com/SlideSpeak/presentation-design-prompts), MIT).

## The theme in one paragraph

Slides are set on warm cream `#FAF9F6` with near-black ink. Headings at every level are `Lora` at
weight 500 with tight leading and negative tracking; body is `Inter` at 300–400; every label,
eyebrow, figure number and data value is `IBM Plex Mono` in uppercase at 0.08em tracking (all three
are Google Fonts). One accent only: annotation red `#B91C1C`, which marks *value* and appears at
most once per slide as a rule, a dot, a marked figure, or one word. Cross-references take cobalt
`#1E3A8A`; anything provisional or in-progress takes ochre `#92400E`. Structure comes from hairlines
and an 8px technical grid, never from boxes and shadows: every slide opens with a 1px rule carrying a
mono eyebrow left and the deck slug right, corners are square, and drawings sit inside a
graph-paper `.figure` panel with registration marks in all four corners and a mono `FIG. 01` footer.
Data appears as ruled tables with a warm-gray uppercase mono header and hairline rows, or as
one large `IBM Plex Mono` tabular figure over a mono caption. Charts are flat black bars on a hairline
baseline with the one key bar in red, values labeled directly on the bars, no gridlines, no axis, no
legend. Section breaks are cream, not black: a large muted mono numeral, a Lora title, one red rule.
Whitespace is the decoration. **Strictly avoid:** rounded corners beyond 2px; drop shadows;
gradients of any kind; more than one red per slide; emoji or icon sets; dark backgrounds; sans-serif
headings; centered body prose (centering is for cover and section slides only); multi-color chart
palettes, gridlines, axis lines or legends; stock photography; font sizes outside the scale below.

## Palette and type

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FAF9F6` | Slide ground |
| `--bg-panel` | `#FFFFFF` | Figure and data panels |
| `--bg-warm` | `#F5F4F1` | Table headers, plates |
| `--ink` | `#1A1A1A` | Headings |
| `--ink-body` | `#2D2D2D` | Body text |
| `--ink-gray` | `#5A5A5A` | Labels, captions |
| `--ink-muted` | `#8A8A8A` | Section numeral. The lightest ink that clears 3:1 |
| `--rule-dark` | `#3A3A3A` | Structural rules, panel borders |
| `--rule-mid` | `#C4C4C4` | Masthead and band hairlines |
| `--rule-light` | `#E5E5E5` | Table rows, grid lines |
| `--red` | `#B91C1C` | The one accent. Value only |
| `--red-deep` | `#7F1D1D` | Eyebrow on a rose plate |
| `--rose` | `#FECACA` | The one quoted-line plate, once per deck |
| `--cobalt` | `#1E3A8A` | Cross-references, links |
| `--ochre` | `#92400E` | Provisional, in-progress |

Fonts: `Lora` 500 (serif, all headings) · `Inter` 300/400/500 (body) · `IBM Plex Mono` 400/500
(labels, figures, data).

Sizes for a 1280×720 slide. Content type: cover 68 · section title 52 · statement 46 · h1 40 ·
h2 30 · h3 22 · body 22 · card 18. Display: big number 132 · section numeral 96. Utility: rose
plate 24 · plate 20 · table cell 17 · code block 15 · mono label 13 · table header 12 · mono
micro 11. Every size the theme uses is in that list; reach for an existing one before adding.

## Accent discipline

Red means *value*: the number that matters, the recommendation, the one word carrying the slide.
Once per slide, maximum. A slide with two reds has no accent at all. Cobalt and ochre are not
decoration either: cobalt marks a cross-reference to another slide or source, ochre marks something
provisional. A deck can run ten slides with no red and lose nothing.

## Slide archetypes

Three archetypes are Marp slide classes, set with `<!-- _class: name -->`. The other three need no
class: they are compositions of the inline components, and the default slide styling already carries
them. Do not invent a `_class` for those — an undefined class is a silent no-op that reads in the
source as though it were doing something.

| Class | Slide | Anatomy |
|---|---|---|
| `cover` | Title | Mono eyebrow (audience/client) top · Lora title center · red rule · `.stamp` metadata band bottom |
| `section` | Section break | Large muted mono numeral · Lora title · one red rule |
| `statement` | Single claim | One Lora sentence, 12 words maximum, 60% of the slide left empty |
| none | Concept | Mono eyebrow · Lora action title · Inter body or `.cols` |
| none | Diagram | Mono eyebrow · action title · a `.figure` div: graph-paper grid, corner marks, `.figure__meta` title block |
| none | Table or number | Mono eyebrow · action title · a markdown table, or one `.big` figure over a mono caption |

Inline components: `.eyebrow` `.cols` `.cols-3` `.plate` `.card` `.note` `.rose` `.stamp` `.big`
`.figure` `.figure__meta` `.rule-red`.

## The Marp style block

Paste this into the deck's front matter under `style: |`, then set exactly one thing: the deck slug
in the masthead rule (`section::before { content: "…" }`). Everything else is verbatim, `:root`
included.

**Choose the slug yourself when you start writing the deck.** It is chrome, not content, and needs
no approval: a short uppercase line naming the project and the deck's purpose, roughly two to five
words, for example `CYNDRA · ONBOARDING REVIEW` or `Q4 PLATFORM REVIEW`. Interpuncts separate parts.
Pick one and move on.

```css
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@300;400;500;600&family=Lora:ital,wght@0,400;0,500;1,400;1,500&display=swap');

:root {
  --bg: #FAF9F6;      --bg-panel: #FFFFFF;  --bg-warm: #F5F4F1;
  --ink: #1A1A1A;     --ink-body: #2D2D2D;  --ink-gray: #5A5A5A;
  --rule-dark: #3A3A3A; --rule-mid: #C4C4C4; --rule-light: #E5E5E5;
  --ink-muted: #8A8A8A;   /* lightest ink that clears 3:1 on the cream ground */
  --red: #B91C1C;     --red-deep: #7F1D1D;  --rose: #FECACA;
  --cobalt: #1E3A8A;  --ochre: #92400E;
  --serif: 'Lora', Georgia, serif;
  --sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --mono: 'IBM Plex Mono', 'Courier New', monospace;
  /* Figure-panel graph paper. 16px at 1px reads as an even texture in a 1x Marp
     render; the 8px/0.5px web value aliases into visible banding. */
  --grid: 16px;
}

/* === BASE === */
section {
  background: var(--bg); color: var(--ink-body);
  font-family: var(--sans); font-weight: 400; font-size: 22px; line-height: 1.55;
  /* Top padding reserves the masthead band so a dense slide can never collide with it. */
  padding: 96px 72px 60px;
}

/* Masthead hairline. Replace the content string with the deck slug. */
section::before {
  content: "DECK SLUG"; display: block; position: absolute;
  top: 30px; left: 72px; width: calc(100% - 144px);
  font-family: var(--mono); font-size: 11px; font-weight: 500;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-gray);
  border-bottom: 1px solid var(--rule-mid); padding-bottom: 10px;
}
section::after {  /* page number */
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  color: var(--ink-gray); right: 72px; bottom: 26px;
}

/* === HEADINGS ===
   Every element selector below is prefixed with `section` on purpose. Marp's
   default theme styles the same elements at `section h1` / `section a` / etc.
   (0,0,2), so a bare `h1` (0,0,1) silently loses on any property they share —
   Marp's heading border-bottom and link accent among them. Matching the
   specificity and coming later in the cascade is what makes these win.
   Do not "simplify" these back to bare element selectors. */
section h1, section h2, section h3, section h4 {
  font-family: var(--serif); font-weight: 500; color: var(--ink);
  text-wrap: balance; font-feature-settings: "kern","liga";
  margin: 0 0 18px; padding-bottom: 0; border-bottom: 0;
}
section h1 { font-size: 40px; line-height: 1.1;  letter-spacing: -0.02em; }
section h2 { font-size: 30px; line-height: 1.2;  letter-spacing: -0.015em; }
section h3 { font-size: 22px; line-height: 1.3;  letter-spacing: -0.01em; }

section strong { font-weight: 600; color: var(--ink); }
section em { font-style: italic; color: var(--ink-gray); }
section a { color: var(--cobalt); text-decoration: underline;
    text-decoration-thickness: 1px; text-underline-offset: 0.2em; }

section ul, section ol { margin: 0; padding-left: 22px; }
section li { margin-bottom: 10px; }
section ul li::marker { color: var(--rule-mid); content: "— "; }
/* Ordered markers are gray, NOT red. Red is the one-per-slide accent; a
   five-item numbered list in red puts five accents on a one-accent slide. */
section ol li::marker { font-family: var(--mono); font-size: 0.8em; color: var(--ink-gray); }

/* === MONO LABELS === */
.eyebrow {
  font-family: var(--mono); font-size: 13px; font-weight: 500;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-gray);
  display: block; margin-bottom: 14px;
}
.eyebrow--red { color: var(--red); }
.eyebrow--cobalt { color: var(--cobalt); }
.eyebrow--ochre { color: var(--ochre); }

/* === RULES === */
.rule-red { height: 0; border-top: 2px solid var(--red); width: 96px; margin: 22px 0; }
/* `---` is Marp's slide separator, so an <hr> only ever comes from `***`/`___`.
   Styled anyway so the rare one is a hairline, not Marp's 0.25em bar. */
section hr { border: 0; height: 1px; background: var(--rule-mid); margin: 26px 0; }

/* === COVER === */
section.cover {
  display: flex; flex-direction: column; justify-content: space-between;
  text-align: left; padding: 64px 72px;
}
section.cover::before { content: none; }
section.cover h1 {
  font-size: 68px; line-height: 1.05; letter-spacing: -0.025em; margin: 0;
}
section.cover .lede {
  font-family: var(--serif); font-size: 22px; font-style: italic; color: var(--ink-gray);
  margin-top: 14px; max-width: 22em;
}

/* === SECTION BREAK === */
section.section {
  display: flex; flex-direction: column; justify-content: center;
}
/* Numeral is --ink-muted, not --rule-mid. A rule colour behind 96px type gives
   1.66:1 against the cream, under even the 3:1 large-text floor, and the
   section number tells the reader where they are. --ink-muted clears it. */
section.section .num {
  font-family: var(--mono); font-size: 96px; font-weight: 400;
  color: var(--ink-muted); line-height: 1; margin-bottom: 18px;
}
section.section h1 { font-size: 52px; margin: 0; }

/* === STATEMENT === */
section.statement { display: flex; flex-direction: column; justify-content: center; }
section.statement h1 {
  font-size: 46px; line-height: 1.25; letter-spacing: -0.015em; max-width: 17em; margin: 0;
}
section.statement .attrib {
  font-family: var(--mono); font-size: 13px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-gray); margin-top: 28px;
}

/* === COLUMNS === */
.cols   { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }
.cols-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; }

/* === PLATE / CARD / NOTE === */
.plate {
  background: var(--bg-warm); border: 1px solid var(--rule-light);
  padding: 20px 24px; font-size: 20px; line-height: 1.5;
}
.card {
  border: 1px solid var(--rule-light); background: var(--bg-panel);
  padding: 18px 20px; font-size: 18px; line-height: 1.5;
}
.card strong { display: block; font-family: var(--mono); font-size: 12px; font-weight: 500;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-gray); margin-bottom: 8px; }

/* Red annotation leader — the architect's margin note. One per slide. */
.note {
  border-top: 1.5px solid var(--red); padding-top: 12px; margin-top: 24px;
  font-family: var(--mono); font-size: 13px; font-weight: 500;
  letter-spacing: 0.06em; color: var(--red); position: relative;
}
.note::before {
  content: ""; position: absolute; left: 0; top: -4px;
  width: 6px; height: 6px; border-radius: 50%; background: var(--red);
}

/* Rose plate — the one quoted or governing line. Once per deck, opt-in via the
   class. A bare markdown `>` is NOT aliased to it: aliasing made every casual
   blockquote the loudest object on the slide, against its own once-per-deck rule. */
.rose {
  background: var(--rose); padding: 22px 28px; margin: 20px 0;
  font-family: var(--serif); font-style: italic; font-size: 24px;
  line-height: 1.45; color: var(--ink); border: 0; border-radius: 0;
}
.rose .eyebrow { color: var(--red-deep); font-style: normal; }

/* Bare blockquote — quiet by default. Full hairline, never a left accent bar. */
section blockquote {
  background: transparent; color: var(--ink-body);
  border: 1px solid var(--rule-light); border-left: 1px solid var(--rule-light);
  border-radius: 0; padding: 18px 24px; margin: 20px 0;
  font-family: var(--serif); font-style: italic; font-size: 22px; line-height: 1.45;
}

/* === STAMP BAND === */
.stamp {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px 28px;
  border-top: 1px solid var(--rule-mid); border-bottom: 1px solid var(--rule-mid);
  padding: 16px 0; font-family: var(--mono); font-size: 11px;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-gray);
}
.stamp b { display: block; color: var(--ink); font-weight: 500; margin-top: 5px; }

/* === BIG NUMBER ===
   Proportional figures, not tabular — a 132px tabular comma opens a gap that
   reads as a typo. Tabular is for columns of numbers, which is the table rule. */
.big {
  font-family: var(--mono); font-size: 132px; font-weight: 400;
  letter-spacing: -0.03em; color: var(--ink); line-height: 1; margin-bottom: 12px;
}
/* The figure IS the slide's one accent. Whole number, never part of one. */
.big.accent { color: var(--red); }

/* === FIGURE PANEL === */
.figure {
  position: relative; background: var(--bg-panel);
  border: 1px solid var(--rule-dark); padding: 32px;
  background-image:
    repeating-linear-gradient(0deg,  rgba(196,193,185,.32) 0 1px, transparent 1px var(--grid)),
    repeating-linear-gradient(90deg, rgba(196,193,185,.32) 0 1px, transparent 1px var(--grid));
}
/* Registration marks, all four corners. */
.figure::before {
  content: ""; position: absolute; inset: 8px; pointer-events: none;
  --m: linear-gradient(var(--rule-dark), var(--rule-dark));
  background:
    var(--m) 0 0/14px 1px no-repeat,       var(--m) 0 0/1px 14px no-repeat,
    var(--m) 100% 0/14px 1px no-repeat,    var(--m) 100% 0/1px 14px no-repeat,
    var(--m) 0 100%/14px 1px no-repeat,    var(--m) 0 100%/1px 14px no-repeat,
    var(--m) 100% 100%/14px 1px no-repeat, var(--m) 100% 100%/1px 14px no-repeat;
}
.figure img, .figure svg { display: block; margin: 0 auto; max-height: 380px; }
.figure pre { background: transparent; border: 0; padding: 0; }
/* Title block. Opaque so the graph paper stops at the drawing. */
.figure__meta {
  display: flex; justify-content: space-between; margin: 20px -32px -32px;
  padding: 12px 32px; background: var(--bg-panel); border-top: 1px solid var(--rule-light);
  font-family: var(--mono); font-size: 11px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-gray);
}

/* === TABLES ===
   Horizontal rules only. Marp's default theme boxes every cell and stripes odd
   rows using `section table th` / `:nth-child` selectors, so these must match
   that specificity — a bare `td { border: 0 }` silently loses. */
section table {
  width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 17px;
}
section table tr,
section table tr:nth-child(2n),
section table tbody > tr:nth-child(odd) > th,
section table tbody > tr:nth-child(odd) > td {
  background: transparent; border-top: 0;
}
section table th,
section table > thead > tr > th {
  font-size: 12px; font-weight: 500; letter-spacing: 0.08em; text-transform: uppercase;
  text-align: left; color: var(--ink-gray); background: var(--bg-warm);
  padding: 10px 14px; border: 0; border-bottom: 2px solid var(--rule-dark);
}
section table td {
  padding: 10px 14px; border: 0; border-bottom: 1px solid var(--rule-light);
  font-variant-numeric: tabular-nums;
}
section table tr:last-child td { border-bottom: 1px solid var(--rule-mid); }
td strong { color: var(--red); font-weight: 500; }

/* === CODE ===
   Marp's default styles code at `section code` / `section pre` / and
   `section :not(pre)>code` (up to 0,1,2) with a 6px radius. Bare `code`/`pre`
   selectors lose to it and the deck exports with rounded code blocks, against
   the square-corner rule. Match the specificity and zero the radius. */
section code, section :not(pre) > code, section > code {
  font-family: var(--mono); font-size: 0.86em; background: var(--bg-warm);
  color: var(--ink); padding: 2px 6px; margin: 0;
  border: 1px solid var(--rule-light); border-radius: 0;
}
section pre {
  background: var(--bg-panel); border: 1px solid var(--rule-light); border-radius: 0;
  padding: 18px 20px; font-size: 15px; line-height: 1.7; filter: none;
}
section pre code, section pre > code {
  background: none; border: 0; padding: 0; border-radius: 0; color: var(--ink);
}

/* Syntax highlighting is OFF by design. Marp ships highlight.js, which emits
   reds, purples and blues — a multi-colour palette on a theme whose whole
   discipline is one accent per slide. Code reads as ink; comments recede. */
section pre code span { color: var(--ink) !important; }
section pre code .hljs-comment,
section pre code .hljs-quote { color: var(--ink-gray) !important; font-style: italic; }
```

## Charts

Flat vertical or horizontal bars in `--ink`, the one key bar in `--red`, values labeled directly on
the bars in `IBM Plex Mono`, category names below in `--ink-gray`, a 2px `--rule-dark` baseline.
No gridlines, no y-axis, no legend, no second accent. If the chart needs a legend to be read, label
the marks directly instead. For anything beyond a bar comparison, read the `dataviz` skill and
carry these palette constraints into it.

## There is one theme

Yudame House is it. Every deck, every audience, every repo. Do not detect a design system, do not
restyle per deck, do not offer the user a choice, and do not treat the values in `:root` as
adjustable. A deck about another company's product still ships in Yudame House, because the deck is
a Yudame artifact regardless of its subject. Their brand shows up in a logo, never in the slide's
colors or type.

Holding the theme constant is the whole point. A deck that looks like every other deck is
recognisable as ours on sight, which is worth more than any per-deck tailoring.
