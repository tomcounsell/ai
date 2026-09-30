---
name: present
description: "Turn what's being communicated into a crafted single-page HTML explainer with diagrams. Triggered by 'present this', 'make this visual', 'show me this as a page'."
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

These pages are **ephemeral** — a look-and-discard artifact, not a saved document. Write into a swept temp root so old runs clean themselves up:

```bash
ROOT="${TMPDIR:-/tmp}/present"
# Sweep runs older than 2h first — never touches the one you're about to view.
find "$ROOT" -maxdepth 1 -type d -mmin +120 -exec rm -rf {} + 2>/dev/null
SCRATCH="$ROOT/$$"; mkdir -p "$SCRATCH"; echo "$SCRATCH"   # file: $SCRATCH/present.html
```

Shell variables (and `$$`) don't survive across separate Bash calls — capture the echoed
concrete path once and use it **literally** in every later step.

Requirements:

- **Self-contained**: one `.html` file. All CSS inline in a `<style>` block. The only external fetch allowed is the Mermaid CDN (needed for diagram rendering); everything else must be local so the PDF prints identically offline.
- **Legible hierarchy.** A title that states the takeaway (not just the topic), section headers that a reader could skim as an outline, short paragraphs, and callouts for the "notice this" moments.

### Default styling direction

Start from this so you're not re-deciding the look every time. It's a direction, not a component library — deviate when the content clearly wants something else.

- **Light mode, always — every component.** Warm off-white ground, tinted-dark text (never pure `#000`/`#fff`), one restrained accent. No dark panels breaking the page: **diagrams and code blocks are light too.** A single dark box in an otherwise light page is the most common thing that makes these pages look unfinished.
- **Calm and editorial.** Generous whitespace, a comfortable reading measure (~50rem), a committed type pairing, and deliberate spacing rhythm over decoration. One memorable visual choice is enough.
- **Prints like it screens.** `@media print { ... }` for margins and `break-inside: avoid` on figures/cards so diagrams don't split across pages.
- **Skip the AI tells:** cyan-on-dark, purple→blue gradients, gradient text on headings, glow, everything-in-identical-cards.

If `frontend-design` is available and the piece deserves a stronger point of view, borrow its taste — but keep the light-mode-throughout rule.

Minimal Mermaid include when you use a diagram (light theme, to match the page):

```html
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: "base" });</script>
```

Keep diagram nodes light-filled with a soft border; use the accent only to mark what matters (a decision, the recommended path), not every box.

## Step 4 — Show it

**Detect the mode.** Follow the context file's bridge-detection rule if present. Absent a context file, assume **local mode**.

### Local mode — open in Chrome

```bash
open -a "Google Chrome" "$SCRATCH/present.html"
```

`open` is async and Chrome keeps reading the file while the tab is open, so **do not delete it now** — that would blank the tab. The start-of-run sweep is the cleanup: this file self-destructs on the next `/present` run (or in ~2h). Report the path so the user can reopen or share it before then.

### Bridge mode — print to PDF and send

Render the page to PDF with headless Chrome, hand the PDF to the repo's bridge-delivery command (declared in the context file), then clean up.

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PDF="$SCRATCH/present.pdf"
PROFILE="$(mktemp -d)"                      # isolated: never touch the user's real Chrome profile

# Launch-poll-kill: headless Chrome doesn't reliably self-exit when another Chrome
# is already running, so run it in the background and stop it once the PDF lands.
"$CHROME" --headless=new --disable-gpu \
  --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
  --no-pdf-header-footer --virtual-time-budget=5000 \
  --print-to-pdf="$PDF" "file://$SCRATCH/present.html" >/dev/null 2>&1 &
CPID=$!
for i in $(seq 1 40); do [ -s "$PDF" ] && sleep 0.4 && break; sleep 0.5; done
kill "$CPID" 2>/dev/null; wait "$CPID" 2>/dev/null
rm -rf "$PROFILE"                           # profile is disposable once Chrome is stopped

[ -s "$PDF" ] || { echo "present: PDF render failed" >&2; }
```

- **Isolated `--user-data-dir` + launch-poll-kill are both required.** Without the temp profile, headless Chrome contends with a running Chrome; without the poll-kill, it can hang instead of exiting after writing the PDF. The loop stops as soon as a non-empty PDF exists (the file is written in one shot at the end of the virtual-time budget), with a 20s ceiling.
- `--virtual-time-budget=5000` gives Mermaid time to render before the PDF snapshots. Increase it (and the loop ceiling) if a diagram-heavy page prints blank diagrams. Benign `gcm`/first-run lines on STDERR are noise; only a missing/zero-byte PDF is a failure.
- On non-macOS, resolve the Chrome/Chromium binary accordingly (`google-chrome`, `chromium`).

Then deliver the PDF over the bridge exactly as the context file specifies (prefer a send that owns the file's lifecycle, e.g. a `--cleanup-after-send` flag, so the PDF is deleted after it lands), and confirm delivery to the user.

**Cleanup (bridge mode).** The delivery step owns the PDF — do **not** `rm` it synchronously, or you race the send. Remove everything else now: `rm -rf "$SCRATCH/present.html"` (the PDF, if still in `$SCRATCH`, is left for the relay). The start-of-run sweep is the backstop for anything left behind.

If no context file declares a delivery command, you are effectively in local mode — fall back to opening the HTML and report that bridge delivery is unavailable in this repo.

## Anti-patterns

- **Dumping the prose into a styled box.** Same words, nicer font, no diagram — that adds nothing. The value is re-structuring the idea visually.
- **Over-decorating.** Grain, glass, glow, and five gradients don't teach. Decoration that isn't doing brand or wayfinding work is noise.
- **Everything equally weighted.** A teacher ranks. If the page has no clear "most important thing," you skipped Step 1.
- **Diagram that restates the text.** A good diagram shows a relationship the sentence can't — a flow, a structure, a comparison. If it just lists the same bullets, cut it.
- **Multi-file output.** One HTML file. External assets break the PDF and the "just open it" promise.
