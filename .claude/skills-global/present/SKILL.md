---
name: present
description: "Turn whatever is being communicated into a well-crafted HTML explainer — layout, diagrams, and formatting that make a complex idea land simply. Triggered by 'present this', 'make this visual', '/present', 'show me this as a page', or any request to explain something as a designed page."
allowed-tools: Read, Write, Bash
argument-hint: "<topic or notes — defaults to the current conversation>"
user-invocable: true
---

# /present — Explain It As a Crafted Page

Take whatever is being communicated right now and render it as a single, self-contained HTML page that makes the idea **easier to grasp** than prose would. Then show it: open it in the local Chrome, or — if this session is running through a communication bridge — print it to PDF and send the PDF back over that bridge.

The output is not a document dump. It is a **taught explanation**. Success is when a smart person who is new to the topic understands it faster from the page than from a paragraph.

## Repo Context Probe

If `.claude/skill-context/present.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares **how it detects bridge mode** and the **bridge-delivery command** that sends the rendered PDF back to the human. When the file is absent (the common case in a foreign repo), there is no bridge: the page opens in the local browser and the skill reports the file paths.

## The two mental postures

Hold both at once while you build the page. They are the whole point of the skill.

- **Think like a teacher.** Start from what the reader already knows and build one step at a time. Name the *one* thing they must understand first, then the next. Use a concrete example before the abstraction. Anticipate the question each section raises and answer it in the next. Never present five things as equally important — rank them.
- **Think like a tour guide.** Control the eye's path down the page. Give a clear "you are here" at the top and signposts between sections. Point at the interesting thing and say why it matters ("notice how…"). Keep momentum — no dead stretches, no wall of text. End where the reader can act.

If a section doesn't teach or guide, cut it.

## Step 1 — Get the content

The subject comes from `$ARGUMENTS`. If empty, the subject **is the current conversation** — the concept, plan, result, or comparison you and the user have been discussing. Summarize to yourself in one sentence what the reader must walk away understanding. If that sentence is fuzzy, the page will be too — sharpen it before writing any HTML.

## Step 2 — Choose the shape that fits the idea

Pick the layout from the *content*, not a template. Match the structure to what the idea actually is:

| If the idea is… | Lead with… |
|---|---|
| A process or flow | a numbered path / flow diagram, left-to-right or top-down |
| A system with parts | a labeled diagram of the parts + how they connect |
| A comparison / trade-off | a side-by-side table or two columns, differences highlighted |
| A decision | the question at top, options as cards, recommendation marked |
| A sequence of events | a timeline |
| A single hard concept | one strong analogy up top, then the mechanism |
| A set of numbers | a small number of clear stat tiles or a chart, not a spreadsheet |

Most pages need a **diagram**. Reach for it before reaching for another paragraph. Use whichever is simplest to get right:
- **Mermaid** (flowcharts, sequence, timeline, state) via the CDN — write the diagram in a `<pre class="mermaid">` block and load `mermaid.min.js`.
- **Inline SVG** for a bespoke picture where Mermaid would fight you.
- **HTML + CSS** (grid, flex, borders) for boxes-and-arrows layouts, tables, and cards.

## Step 3 — Write one self-contained HTML file

Write it to a scratch path (`SCRATCH="${TMPDIR:-/tmp}/present-$$"`; `mkdir -p "$SCRATCH"`; file `$SCRATCH/present.html`). Requirements:

- **Self-contained**: one `.html` file. All CSS inline in a `<style>` block. The only external fetch allowed is the Mermaid CDN (needed for diagram rendering); everything else must be local so the PDF prints identically offline.
- **Crafted, not generic.** This is an explainer someone will look at — it must not read as boilerplate AI output. A committed type pairing, a real palette (tinted neutrals, not pure `#000`/`#fff`), deliberate spacing rhythm, and one memorable visual choice. If `frontend-design` is available, borrow its taste. Avoid the tells: cyan-on-dark, purple→blue gradients, gradient text on headings, everything-in-identical-cards.
- **Readable on paper and screen.** Print-friendly: dark text on light background by default, `@media print { ... }` to set margins and avoid cutting diagrams across pages (`break-inside: avoid` on figures/cards), a sensible max content width (~50rem) so lines aren't too long.
- **Legible hierarchy.** A title that states the takeaway (not just the topic), section headers that a reader could skim as an outline, short paragraphs, and callouts for the "notice this" moments.

Minimal Mermaid include when you use a diagram:

```html
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: "neutral" });</script>
```

## Step 4 — Show it

**Detect the mode.** Follow the context file's bridge-detection rule if present. Absent a context file, assume **local mode**.

### Local mode — open in Chrome

```bash
open -a "Google Chrome" "$SCRATCH/present.html"
```

Report the file path so the user can reopen or share it.

### Bridge mode — print to PDF and send

Render the page to PDF with headless Chrome, then hand the PDF to the repo's bridge-delivery command (declared in the context file).

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PDF="$SCRATCH/present.pdf"
"$CHROME" --headless=new --disable-gpu \
  --no-pdf-header-footer \
  --virtual-time-budget=4000 \
  --print-to-pdf="$PDF" \
  "file://$SCRATCH/present.html"
```

- `--virtual-time-budget=4000` gives Mermaid time to render before the PDF snapshots. Increase it if a diagram-heavy page prints blank diagrams.
- On non-macOS, resolve the Chrome/Chromium binary accordingly (`google-chrome`, `chromium`).

Then deliver the PDF over the bridge exactly as the context file specifies, and confirm delivery to the user. If no context file declares a delivery command, you are effectively in local mode — fall back to opening the HTML and report that bridge delivery is unavailable in this repo.

## Anti-patterns

- **Dumping the prose into a styled box.** Same words, nicer font, no diagram — that adds nothing. The value is re-structuring the idea visually.
- **Over-decorating.** Grain, glass, glow, and five gradients don't teach. Decoration that isn't doing brand or wayfinding work is noise.
- **Everything equally weighted.** A teacher ranks. If the page has no clear "most important thing," you skipped Step 1.
- **Diagram that restates the text.** A good diagram shows a relationship the sentence can't — a flow, a structure, a comparison. If it just lists the same bullets, cut it.
- **Multi-file output.** One HTML file. External assets break the PDF and the "just open it" promise.
```
