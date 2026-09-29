# Moodboard capture (Steps 1-2 reference)

## Extract images

Cosmos, Pinterest, and Are.na render client-side, so `WebFetch` misses the image grid. Drive the user's real Chrome with BYOB MCP; image enumeration needs `browser_eval`, so `BYOB_ALLOW_EVAL=1` must be set. Logged-in private boards work the same way.

```text
mcp__byob__browser_navigate(url="https://www.cosmos.so/<user>/<board>", waitUntil="networkidle")
# Scroll twice with ~2s waits to trigger lazy-loaded tiles
mcp__byob__browser_scroll(tabId=<tab>, y=4000)
mcp__byob__browser_scroll(tabId=<tab>, y=8000)
mcp__byob__browser_eval(tabId=<tab>, expression="
  JSON.stringify(Array.from(document.querySelectorAll('img'))
    .map(i => ({src: i.src, alt: i.alt, w: i.naturalWidth, h: i.naturalHeight}))
    .filter(i => i.w > 100))
")
```

The page shows 400px thumbnails; request usable resolution from CDN sources (for example `?format=webp&w=800`) and `curl` into `docs/designs/inspiration/YYYY-MM-DD-<theme-slug>/`. Name files `NN-<author-or-theme>.webp` in board order, plus `cover.webp` for the board header, so later passes can cite "image #07".

## Per-pass README

`docs/designs/inspiration/YYYY-MM-DD-<theme>/README.md`:

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

## Motif table

| Motif | Examples | Present in system? |
|---|---|---|
| Dot constellations | cover, #18 | ❌ no |
| Architectural ledger paper | #04, #06 | ⚠ partial |
| Red as structural overlay | #07, #08, #09 | ✅ yes |
```

Aim for 6-10 distinct motifs, each citing image numbers, with the third column one of `✅ yes / ⚠ partial / ❌ no`.
