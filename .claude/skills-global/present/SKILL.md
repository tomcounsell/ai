---
name: present
description: "Turn what's being communicated into a crafted single-page HTML explainer with diagrams. Triggered by 'present this', 'make this visual', 'show me this as a page'."
allowed-tools: Read, Write, Bash
argument-hint: "<topic or notes — defaults to the current conversation>"
user-invocable: true
effort: medium
---

# /present — Explain It As a Crafted Page

Take whatever is being communicated right now and render it as a single, self-contained HTML page that makes the idea **easier to grasp** than prose would. Then show it: open it in the local Chrome, or — if this session is running through a communication bridge — print it to PDF and send the PDF back over that bridge.

**Done:** a smart newcomer understands the idea faster from the page than from a paragraph, and the page has been opened locally or delivered over the bridge.

## Repo Context Probe

If `.claude/skill-context/present.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares **how it detects bridge mode** and the **bridge-delivery command** that sends the rendered PDF back to the human. When the file is absent (the common case in a foreign repo), there is no bridge: the page opens in the local browser and the skill reports the file paths.

## What makes the page work

- **Teach.** Name the one thing the reader must grasp first, then the next; a concrete example before the abstraction; rank ideas rather than presenting five as equal. Before writing HTML, state in one sentence what the reader must walk away understanding; if it is fuzzy, sharpen it.
- **Guide the eye.** A title that states the takeaway, section headers that skim as an outline, signposts and "notice this" callouts, no walls of text, an ending the reader can act on. Cut any section that neither teaches nor guides.
- **Show structure.** Pick the shape from the content (flow, parts diagram, side-by-side comparison, decision with a marked recommendation, timeline, one analogy then the mechanism, a few stat tiles) and reach for a diagram before another paragraph: Mermaid for flows, sequences, timelines, and state; inline SVG where Mermaid fights you; HTML and CSS for boxes, tables, and cards. A diagram must show a relationship the prose cannot, not restate the bullets. Restyled prose with no restructuring adds nothing.

The subject is `$ARGUMENTS`, or the current conversation when that is empty.

## Write one self-contained HTML file

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

- **Self-contained**: one `.html` file with inline CSS. The only external fetch allowed is the Mermaid CDN, so the PDF prints identically offline.
- **Light mode throughout, every component**: diagrams and code blocks too. A single dark panel in a light page is the most common thing that makes these pages look unfinished. Tinted-dark text, never pure `#000`/`#fff`, one restrained accent.
- **Calm and editorial**: generous whitespace, a ~50rem measure, a committed type pairing, spacing rhythm over decoration. One memorable visual choice is enough.
- **Prints like it screens**: `@media print` margins and `break-inside: avoid` on figures and cards.
- **Skip the tells**: cyan-on-dark, purple-to-blue gradients, gradient text, glow, identical card grids, and reflexive model defaults (cream or off-white ground, an italic accent word in the headline, numbered 01/02/03 section labels, monospace eyebrow labels, pill buttons, emoji bullets) unless the piece has a reason for one.

Minimal Mermaid include when you use a diagram (light theme, to match the page):

```html
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: "base" });</script>
```

Keep diagram nodes light-filled with a soft border; use the accent only to mark what matters (a decision, the recommended path), not every box.

## Show it

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
