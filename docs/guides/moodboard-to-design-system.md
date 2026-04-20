# Moodboard → Design System Workflow

Translate a visual moodboard (e.g. a Cosmos collection) into concrete,
additive edits to a Pencil `.pen` design system and its downstream CSS
tokens.

Use when:

- Someone shares a moodboard URL and asks you to tighten, professionalize,
  or re-theme an existing design system.
- A new brand direction has mood imagery but no component spec.
- You need to audit drift between the design system and the aesthetic the
  team is actually pointing at.

Do **not** use when the moodboard is abstract (vibes only, no reusable
motifs) or when the existing system already matches — additive changes
without a concrete signal waste effort.

## Inputs

1. **Moodboard URL** (Cosmos / Pinterest / Are.na / plain image folder).
2. **Ground-truth design file** — the one the team treats as canonical.
   For Pencil-based systems, this is a `.pen` file. Despite MCP docs
   claiming `.pen` files are encrypted, they are plain JSON — you can
   `Read`, `Grep`, and rewrite them directly.
3. **Downstream CSS** (brand/tokens file + Tailwind `@theme` bridge).
4. **Gap-audit doc** if one exists — this is where you log the pass.

## Pipeline

```
Moodboard URL
     │
     ▼  (headless browser — NOT WebFetch for JS SPAs)
Image URLs list
     │
     ▼  (curl)
Local image set in docs/designs/inspiration/
     │
     ▼  (Read images, absorb motifs)
Motif table (dominant themes × present-in-system?)
     │
     ▼  (compare to current tokens + components)
Critique: where the system drifts from the moodboard
     │
     ▼  (minimal, additive; no renames, no deletions)
Proposal: token changes + N new components
     │
     ▼  (direct JSON edit to .pen; do NOT rely on Pencil MCP save)
Pencil file updated
     │
     ▼  (keep in sync)
brand.css + source.css + gap-audit.md updated
     │
     ▼
Commit
```

## Step 1 — Extract moodboard images

Cosmos, Pinterest, and Are.na are JavaScript-rendered SPAs. `WebFetch`
returns the shell HTML only — it will miss the image grid. Use a
headless browser.

```bash
# Open the page in a named session (auto-launches the browser)
agent-browser --session moodboard open "https://www.cosmos.so/<user>/<board>"

# Scroll to trigger lazy-loaded tiles (twice with waits is usually enough)
agent-browser --session moodboard scroll down 4000
agent-browser --session moodboard wait 2000
agent-browser --session moodboard scroll down 4000
agent-browser --session moodboard wait 2000

# Enumerate all images > 100px wide (skip favicons, avatars)
agent-browser --session moodboard eval "
  JSON.stringify(
    Array.from(document.querySelectorAll('img'))
      .map(i => ({src: i.src, alt: i.alt, w: i.naturalWidth, h: i.naturalHeight}))
      .filter(i => i.w > 100)
  )
"
```

Download at usable resolution (request `?format=webp&w=800` or similar
for CDN-served sources — the page shows 400px thumbnails):

```bash
mkdir -p docs/designs/inspiration
# For each {src, alt} entry, curl with a stable filename:
# e.g. 01-<author-slug>.webp, 02-<author-slug>.webp, ...
```

Naming: `NN-<author-or-theme>.webp` with `cover.webp` for the board
header image. Numbering preserves board order so future passes can refer
to "image #07" and everyone knows which one.

## Step 2 — Read and absorb the motifs

Use the `Read` tool on each `.webp` — Claude Code can view them. Do NOT
delegate this to a subagent; the critique depends on *your* direct
pattern recognition.

Then build a **motif table**. This is the core artifact of the
critique:

```markdown
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

## Step 3 — Critique the existing system

Load the four sources of truth (if running against a repo with the
Pencil skill, use `/design-prime` which does this in one call):

- The `.pen` file (via `mcp__pencil__get_variables`,
  `mcp__pencil__batch_get`).
- The downstream CSS (e.g. `static/css/brand.css`).
- The Tailwind theme bridge (e.g. `static/css/source.css`).
- The gap-audit doc if present.

For each absent/partial motif, write one paragraph in the form:

> **Problem.** What's missing and why the moodboard says it matters.
> **Evidence.** Specific image references.
> **Fix.** One concrete edit.

Avoid these common critique failure modes:

- **Narrating what's already there.** Only call out gaps.
- **Demanding renames.** Rename = breaking change for downstream
  templates. Defer to a separate pass.
- **Adding five cards.** If the system has N near-duplicate variants,
  flag for consolidation but don't ship the consolidation in this pass.

## Step 4 — Propose minimal additive edits

Present as a table. Target 3–7 edits total — enough to shift the system,
few enough to land cleanly in one commit.

```markdown
| # | Edit | Why |
|---|---|---|
| 1 | Add --font-serif = Lora | Editorial voice for research titles |
| 2 | Retune --status-operational #4CAF50 → #5C7A3E | Kill Material green |
| 3 | New component Annotation/Crosshair | Pairs with existing Annotation/Mark |
```

**Invariants** for the proposal:

- **Additive only.** New tokens, new components, retuned values. No
  renames, no deletions.
- **Reuse existing orphan tokens before inventing new ones.** If the
  moodboard calls for gold and `--warm` exists but is unused, use
  `--warm`. Don't add `--gold`.
- **Name consistently with existing conventions.** If existing
  components are `Annotation/Line`, `Annotation/Mark`, a new one should
  be `Annotation/Crosshair` — not `Registration/Cross`.
- **State rationale in one line.** If you can't say why in one line,
  the edit is not tight enough.

Get explicit approval before applying. The user may swap typeface
choices, cut edits, or retune hexes. Do not proceed on assumed
approval.

## Step 5 — Apply edits to the `.pen` file

**Critical gotcha.** The Pencil MCP `batch_design` and `set_variables`
tools operate on an **in-memory editor session**. They do NOT persist
to disk unless the Pencil desktop app has the file open and triggers a
save. If you run the MCP operations, see "Successfully executed," then
close the MCP session, the edits are **silently discarded**.

Symptoms you hit this:

- `get_editor_state` shows your new components after batch_design
  returned success.
- Reopening the document later shows the pre-edit state.
- Reading the `.pen` JSON on disk shows no changes.

**Reliable path: edit the JSON directly.** Since `.pen` is plain JSON
(indent=2), you can:

```python
import json
from pathlib import Path

p = Path("path/to/design-system.pen")
doc = json.loads(p.read_text())

# 1. Variables
doc.setdefault("variables", {})
doc["variables"]["--font-serif"] = {"type": "string", "value": "Lora"}
doc["variables"]["--status-operational"] = {"type": "color", "value": "#5C7A3E"}

# 2. New component — append to the right parent frame's children
components_frame = next(c for c in doc["children"] if c["id"] == "JFbpV")
components_frame["children"].append({
    "type": "frame",
    "id": "wiM0R",  # any 5-char unique string
    "name": "Annotation/Crosshair",
    "reusable": True,
    "width": 16, "height": 16, "layout": "none",
    "children": [
        {"type": "rectangle", "id": "h", "fill": "$--accent",
         "width": 16, "height": 1.5, "x": 0, "y": 7.25},
        {"type": "rectangle", "id": "v", "fill": "$--accent",
         "width": 1.5, "height": 16, "x": 7.25, "y": 0},
    ],
})

p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
```

Conventions:

- Preserve `indent=2` and trailing newline.
- IDs are arbitrary unique strings — 5 mixed-case chars is typical.
- Colors, fonts, spacing: always reference the variable with `$--name`,
  never hardcode a hex or font family.
- Reusable components: set `"reusable": True`, top-level in their
  parent frame's children list. `name` must follow the existing naming
  convention (e.g. `Category/Variant`).

After the write, verify:

```python
doc2 = json.loads(p.read_text())
# count reusable components, check specific IDs exist, check variable values
```

You can then re-open in Pencil (`mcp__pencil__open_document`) — the
editor will reload the on-disk state.

## Step 6 — Sync downstream CSS

For each token added/changed in the `.pen` file, mirror in the
downstream CSS:

- `brand.css` — CSS custom properties under `:root`. Include Google
  Fonts `@import` if you added a font.
- `source.css` (Tailwind) — under `@theme`. Token names MUST match
  `brand.css` exactly, or the system diverges silently.

For each new component, decide whether to ship a CSS class now or defer
until a template needs it. Speculative classes rot; defer is usually
right.

## Step 7 — Update the gap audit

Append a dated section to the gap-audit doc (e.g.
`docs/designs/pencil-design-gap-audit.md`) with:

- Variable changes (before → after, rationale).
- New components table (name, node ID, purpose, moodboard reference).
- Still-open items that surfaced but weren't landed (card
  consolidation, etc.).

Update the running component count in the doc header.

## Step 8 — Commit

Stage only your own files — exclude unrelated untracked directories.

```bash
git add <pen-file> <brand.css> <source.css> <gap-audit.md> \
        docs/designs/inspiration/
git status --short  # verify nothing unexpected is staged
git commit -m "design: <theme> pass — <one-line summary>

<body describing: variable changes, new components, downstream CSS
updates, gap-audit additions>
"
```

## Gotchas reference

| Symptom | Cause | Fix |
|---|---|---|
| WebFetch returns "no images found" on Cosmos | JS-rendered SPA | Use `agent-browser` (Playwright) |
| `mcp__pencil__batch_design` reports success but file unchanged | MCP edits don't persist without Pencil UI save | Edit `.pen` JSON directly with Python |
| `get_screenshot` returns blank for newly-added Pencil nodes | Render cache | Not a real problem — verify via `batch_get` or `Read` the JSON |
| New `@theme` token doesn't work in templates | Tailwind name doesn't match `brand.css` | Ensure both files use the same token name |
| `$--font-mono` "invalid" warning | False positive — variable refs in `fontFamily` do resolve | Ignore |

## Reference implementation

Commit `a702484` on `yudame/cuttlefish` main (moodboard pass,
2026-04-20):

- Moodboard source: `https://www.cosmos.so/tomcounsell/yudame-research`
- Files changed: `docs/designs/pencil-design-system.pen`,
  `static/css/brand.css`, `static/css/source.css`,
  `docs/designs/pencil-design-gap-audit.md`,
  `docs/designs/inspiration/` (19 images).
- Shape: 3 variable edits + 5 new components, no renames, no deletions.
