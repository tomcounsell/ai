# Moodboard capture (Steps 1–2 reference)

Load this when scraping moodboard images and writing the per-pass README + motif table.

## Step 1 — Extract moodboard images

Cosmos, Pinterest, and Are.na are JavaScript-rendered SPAs. `WebFetch`
returns the shell HTML only — it will miss the image grid. Use BYOB MCP
to drive the user's real Chrome.

> **Browser surface:** BYOB MCP (`mcp__byob__browser_*`). The image
> enumeration below uses `mcp__byob__browser_eval`; set
> `BYOB_ALLOW_EVAL=1` in the agent's environment before invoking the
> skill. The same flow works for public moodboards and for logged-in
> sources (private Cosmos boards behind your account) — BYOB just
> drives whichever Chrome session you're in.

```text
mcp__byob__browser_navigate(url="https://www.cosmos.so/<user>/<board>", waitUntil="networkidle")

# Scroll to trigger lazy-loaded tiles (twice with waits is usually enough)
mcp__byob__browser_scroll(tabId=<tab>, y=4000)
# (sleep 2s)
mcp__byob__browser_scroll(tabId=<tab>, y=8000)
# (sleep 2s)

# Enumerate all images > 100px wide (skip favicons, avatars). Requires BYOB_ALLOW_EVAL=1.
mcp__byob__browser_eval(tabId=<tab>, expression="
  JSON.stringify(
    Array.from(document.querySelectorAll('img'))
      .map(i => ({src: i.src, alt: i.alt, w: i.naturalWidth, h: i.naturalHeight}))
      .filter(i => i.w > 100)
  )
")
```

Download at usable resolution (request `?format=webp&w=800` or similar
for CDN-served sources — the page shows 400px thumbnails):

```bash
THEME_SLUG=research-editorial  # concise kebab-case theme name
PASS_DIR="docs/designs/inspiration/$(date -u +%Y-%m-%d)-${THEME_SLUG}"
mkdir -p "$PASS_DIR"

# For each {src, alt} entry, curl with a stable filename:
#   e.g. 01-<author-slug>.webp, 02-<author-slug>.webp, cover.webp
```

Naming: `NN-<author-or-theme>.webp` with `cover.webp` for the board
header image. Numbering preserves board order so future passes can
refer to "image #07" and everyone knows which one.

## Step 2 — Read images, write per-pass README

Use the `Read` tool on each `.webp` — Claude Code can view them. Do NOT
delegate this to a subagent; the critique depends on *your* direct
pattern recognition.

Then write `docs/designs/inspiration/YYYY-MM-DD-<theme>/README.md`:

```markdown
# <Theme> — YYYY-MM-DD

**Source:** <moodboard URL>
**Board title:** <as shown on the source>
**Collected by:** <person who ran this pass>

## Image legend

| # | File | Author / context |
|---|---|---|
| cover | cover.webp | board header |
| 01 | 01-<author>.webp | ... |
| ... | ... | ... |

## Motif table

| Motif | Examples | Present in system? |
|---|---|---|
| Dot constellations | cover, #18 | ❌ no |
| Architectural ledger paper | #04, #06 | ⚠ partial |
| Editorial serif voice | #14, #15 | ❌ no serif typeface |
| Red as structural overlay | #07, #08, #09 | ✅ yes |
```

Rules for a good motif table:

- One row per distinct motif (aim for 6–10, not 20).
- Reference specific images by number.
- Third column is ternary: `✅ yes / ⚠ partial / ❌ no`.
- Absent and partial motifs are the ONLY candidates for edits. Present
  motifs are confirmation the system is on-brand; leave them alone.
