---
name: do-design-system
description: "Use when translating a moodboard (Cosmos, Pinterest, Are.na, image folder) into additive edits to a design system — tokens, components, charter, downstream CSS. Also use when organizing design files to the canonical docs/designs/ structure (design-system.pen, charter.md, gap-audit.md, inspiration/, product/). Triggered by 'apply this moodboard', 'tighten the design system', 'theme pass', 'design system pass', 'organize design files', or a moodboard URL with a design-system ask."
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

# Design System Skill

Translate a visual moodboard into concrete, additive edits to a design
system — `.pen` source, charter, downstream CSS tokens — and enforce
the canonical organization of design files in the repo.

Use when:

- Someone shares a moodboard URL and asks you to tighten, professionalize,
  or re-theme an existing design system.
- A new brand direction has mood imagery but no component spec.
- You need to audit drift between the design system and the aesthetic the
  team is actually pointing at.
- The repo's `docs/designs/` structure needs organizing (scattered `.pen`
  files, missing charter, flat inspiration folder).

Do **not** use when the moodboard is abstract (vibes only, no reusable
motifs) or when the existing system already matches — additive changes
without a concrete signal waste effort.

## Scope

This skill edits ONLY:

- `docs/designs/design-system.pen` (reserved filename — the system source)
- `docs/designs/charter.md` (when scaffolding)
- `docs/designs/gap-audit.md` (append-only)
- `docs/designs/inspiration/**` (adds images + per-pass README)
- `docs/designs/README.md` and `docs/designs/product/README.md` (indexes)
- Downstream CSS: brand tokens + Tailwind `@theme` bridge

The skill MUST NOT touch:

- Any `.pen` file other than `design-system.pen` (product/ wireframes,
  flows, mockups have different schemas and will be corrupted)
- Application code, templates, or component implementations
- Renames or deletions of existing tokens/components

## Canonical file structure

```
docs/designs/
├── README.md                    # Landing page, indexes everything
├── charter.md                   # Principles, voice, a11y, taxonomy, licensing
├── design-system.pen            # RESERVED — skill territory, source of truth
├── gap-audit.md                 # Append-only changelog of system changes
├── product/                     # Product team's .pen files (free-form)
│   ├── README.md                # Index with slug, kind, date, status
│   └── <slug>-<kind>.pen        # kind ∈ flow|wireframe|mockup|journey|sitemap|prototype
├── inspiration/
│   ├── README.md                # Index of moodboard passes
│   └── YYYY-MM-DD-<theme>/      # One folder per pass
│       ├── README.md            # Source URL + motif table + image legend
│       ├── cover.webp
│       └── NN-<author-or-theme>.webp
└── exports/                     # Optional — rendered component PNGs

<css-root>/                      # e.g. static/css/, assets/css/, styles/
├── brand.css                    # :root tokens — 1:1 mirror of design-system.pen
└── source.css                   # Tailwind @theme bridge (if project uses Tailwind)
```

### Invariants

| Rule | Why |
|---|---|
| Exactly one reserved `.pen` named `design-system.pen` | Unambiguous skill target |
| Skill refuses to edit any other `.pen` | Product `.pen` files have different schemas |
| Each moodboard pass gets its own `inspiration/YYYY-MM-DD-<theme>/` folder | Passes are referenceable forever |
| Motif table lives in `inspiration/<pass>/README.md` | Lives with the images it describes |
| `gap-audit.md` has a dated `## YYYY-MM-DD — <theme>` section per pass | Append-only log |
| `brand.css` and `source.css` token names match exactly | Divergence is silent |
| `product/` filenames follow `<slug>-<kind>.pen` | Sortable, greppable |
| `product/` files never appear in `gap-audit.md` | Audit is system-only |
| No version numbers or dates in `design-system.pen` filename | Git history is the version log |

## When to load sub-files

- Scaffolding `charter.md` in a new or legacy repo → read [charter-template.md](charter-template.md) and copy its body into `docs/designs/charter.md`

## Inputs

1. **Moodboard URL** (Cosmos / Pinterest / Are.na / plain image folder).
2. **`docs/designs/charter.md`** — principles, voice, a11y targets, token
   tiers, component taxonomy, font licensing. If missing, offer to
   scaffold from `charter-template.md`. MUST exist before moodboard
   edits can land.
3. **`docs/designs/design-system.pen`** — source of truth, plain JSON.
4. **Downstream CSS** — `brand.css` (tokens) + `source.css` (Tailwind
   bridge, if present).
5. **`docs/designs/gap-audit.md`** — append-only changelog.

## Pipeline

```
Moodboard URL
     │
     ▼  Step 0: audit file organization, verify charter exists
Canonical structure OK, charter present
     │
     ▼  Step 1: headless browser scrape (NOT WebFetch for JS SPAs)
Image URLs list
     │
     ▼  Step 2: curl into inspiration/YYYY-MM-DD-<theme>/
Local images + per-pass README (source URL, motif table, legend)
     │
     ▼  Step 3: read images + charter, critique against principles
Critique keyed to charter principles
     │
     ▼  Step 4: propose 3–7 additive edits, each citing a principle
Approved edit list
     │
     ▼  Step 5: safety-gated JSON edit to design-system.pen
Source file updated
     │
     ▼  Step 6: sync brand.css + Tailwind @theme
Downstream CSS updated
     │
     ▼  Step 7: append dated section to gap-audit.md
Changelog updated
     │
     ▼  Step 8: commit
```

## Step 0 — Audit file organization

Before any moodboard work, audit the current repo against the canonical
structure. Propose migrations; do NOT auto-apply.

Check each of:

- `docs/designs/` exists
- `docs/designs/charter.md` exists and is non-empty
- `docs/designs/design-system.pen` exists (may be under a legacy name)
- `docs/designs/gap-audit.md` exists
- `docs/designs/inspiration/` exists
- `docs/designs/product/` exists (may be empty)
- Downstream CSS files present and token names match

| Gap | Proposed migration |
|---|---|
| No `charter.md` | Read `charter-template.md`, scaffold to `docs/designs/charter.md`, **HALT** and ask user to fill it before proceeding |
| `.pen` under a non-canonical name | Rename to `design-system.pen` |
| Multiple `.pen` at `docs/designs/` root | Keep `design-system.pen`, move others to `product/<slug>-<kind>.pen` |
| No `gap-audit.md` | Create with header, empty body |
| No `inspiration/` | Create with `README.md` listing passes |
| `inspiration/` flat (images mixed) | Move all images into `inspiration/YYYY-MM-DD-initial/` with scaffolded `README.md` |
| No `product/` | Create with empty `README.md` |
| `brand.css` ↔ `source.css` token names diverge | List mismatches; do NOT auto-fix (renames are breaking) |

If `charter.md` is missing or empty, **HALT**. No moodboard edits until
a charter exists and reflects the product. The charter is not
boilerplate — it needs human judgment.

## Step 1 — Extract moodboard images

Cosmos, Pinterest, and Are.na are JavaScript-rendered SPAs. `WebFetch`
returns the shell HTML only — it will miss the image grid. Use a
headless browser.

```bash
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

## Step 3 — Critique the existing system

Load sources of truth **in this order**:

1. **`charter.md`** — principles, voice, a11y targets, token tiers,
   component taxonomy, font licensing. Edits are tested against it.
2. **`design-system.pen`** — current tokens + components (read JSON
   directly; see Step 5 note about MCP).
3. **`brand.css` and `source.css`** — downstream state.
4. **`gap-audit.md`** — recent changes, still-open items.

For each absent/partial motif, write one paragraph:

> **Problem.** What's missing and why the moodboard says it matters.
> **Evidence.** Specific image references (e.g. #04, #06).
> **Fix.** One concrete edit.
> **Principle.** Which charter principle this supports.

If an edit doesn't align with any charter principle, either:

- Drop it (the moodboard is pulling the system off-brand), or
- Propose a charter amendment FIRST, in a separate commit, before
  landing the edit.

Avoid these critique failure modes:

- **Narrating what's already there.** Only call out gaps.
- **Demanding renames.** Rename = breaking change for downstream
  templates. Defer to a separate pass.
- **Adding five cards.** If the system has N near-duplicate variants,
  flag for consolidation but don't ship the consolidation in this pass.

## Step 4 — Propose minimal additive edits

Present as a table. Target 3–7 edits — enough to shift the system,
few enough to land cleanly in one commit.

```markdown
| # | Edit | Tier | Why | Principle |
|---|---|---|---|---|
| 1 | Add --font-serif = Lora | semantic | Editorial voice for research titles | Editorial over marketing |
| 2 | Retune --status-operational #4CAF50 → #5C7A3E | semantic | Kill Material green | Honest, not clever |
| 3 | New component Annotation/Crosshair | component | Pairs with existing Annotation/Mark | Dense information before whitespace |
```

**Invariants:**

- **Additive only.** New tokens, new components, retuned values. No
  renames, no deletions.
- **Reuse existing orphan tokens before inventing new ones.** If the
  moodboard calls for gold and `--warm` exists but is unused, use
  `--warm`. Don't add `--gold`.
- **Tier-aware naming.** New tokens land in the charter's declared tier
  (primitive / semantic / component). Semantic by default.
- **Component taxonomy match.** New components use `Category/Variant`
  from charter's taxonomy list. If no category fits, propose adding
  one to the charter FIRST, in a separate edit.
- **Font licensing.** Any new font requires a listed license in the
  charter's fonts table. Add the row before adding the token.
- **Accessibility.** Retuned colors verified against charter's contrast
  targets. Don't land a token that fails the stated WCAG target.
- **State rationale in one line.** If you can't say why in one line,
  the edit is not tight enough.

Get explicit approval before applying. The user may swap typefaces,
cut edits, retune hexes, or contest the principle citation. Do not
proceed on assumed approval.

## Step 5 — Apply edits to `design-system.pen`

### Safety-gate updates (two parts)

The skill's safety gate has two distinct enforcement layers, both
intentional:

- **Layer A — inline Python assertion (agent-level, runtime).** Paste and
  execute the block below before any `.pen` write. It pins the write
  target to `design-system.pen` and refuses any other path. **Scope: the
  `.pen` source only.** It is intentionally not a guard for the
  generated artifacts (`design-system.md`, `brand.css`, `source.css`) —
  the assertion's `target` is hardcoded to the `.pen`, so an additional
  `endswith("brand.css")` check on the same `target` would be vacuous.
- **Layer B — `validate_design_system_readonly.py` PreToolUse hook
  (tool-level, runtime).** Registered in `.claude/settings.json` against
  the `Write`/`Edit` matchers. **Scope: the generated artifacts.** It
  blocks any direct Write/Edit tool call against `design-system.md`,
  `brand.css`, or `source.css` regardless of whether this skill is
  active. This is the real enforcement for the generated-artifact case.

The two layers are complementary, not redundant: Layer A discriminates
the correct `.pen` write path from a wrong `.pen` write path; Layer B
discriminates a write to a generated artifact from a write to anything
else. Do NOT remove either; they cover different call paths.

```python
from pathlib import Path

target = Path("docs/designs/design-system.pen")
assert target.name == "design-system.pen", \
    "do-design-system only edits design-system.pen — refuse"
assert target.exists(), f"design-system.pen not found at {target}"
```

If the Pencil MCP is connected, also verify the open editor is the
system file:

```python
# Pseudocode — via mcp__pencil__get_editor_state
state = mcp__pencil__get_editor_state()
assert state["activeFile"].endswith("design-system.pen"), \
    "active Pencil file is not design-system.pen — switch before editing"
```

Never run `batch_design`, `set_variables`, or direct JSON writes
against any other `.pen` file. Product-team wireframes, flows, and
mockups have different schemas and would be corrupted.

### Critical gotcha — MCP does not persist

The Pencil MCP `batch_design` and `set_variables` tools operate on an
**in-memory editor session**. They do NOT persist to disk unless the
Pencil desktop app has the file open and triggers a save. If you run
the MCP operations, see "Successfully executed," then close the MCP
session, the edits are **silently discarded**.

Symptoms:

- `get_editor_state` shows your new components after batch_design
  returned success.
- Reopening the document later shows the pre-edit state.
- Reading the `.pen` JSON on disk shows no changes.

### Reliable path: edit the JSON directly

`.pen` is plain JSON (indent=2). Edit it in Python:

```python
import json
from pathlib import Path

p = Path("docs/designs/design-system.pen")
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

### Conventions

- Preserve `indent=2` and trailing newline.
- IDs are arbitrary unique strings — 5 mixed-case chars is typical.
- Colors, fonts, spacing: always reference the variable with `$--name`,
  never hardcode a hex or font family.
- Reusable components: set `"reusable": True`, top-level in their
  parent frame's children list. `name` must follow the charter's
  `Category/Variant` taxonomy.
- **Never edit `product/*.pen` from this skill.** Different schema,
  different owner.

After the write, verify:

```python
doc2 = json.loads(p.read_text())
# count reusable components, check specific IDs exist, check variable values
```

You can then re-open in Pencil (`mcp__pencil__open_document`) — the
editor will reload the on-disk state.

## Step 6 — Run the generator

CSS files, `design-system.md`, and DTCG / Tailwind exports are GENERATED
from `design-system.pen`. Do NOT hand-edit `brand.css`, `source.css`, or
`design-system.md` — the `validate_design_system_readonly.py` PreToolUse
hook will block the Write/Edit tool call, and the
`validate_design_system_sync.py` hook will block the commit if drift is
detected.

Run the generator:

```bash
python -m tools.design_system_sync --all \
    --pen <consumer-repo>/docs/designs/design-system.pen \
    --css-root <consumer-repo>/<css-root>
```

Or rely on `design-system-sync.toml` adjacent to the `.pen`:

```bash
python -m tools.design_system_sync --all \
    --pen <consumer-repo>/docs/designs/design-system.pen
```

Verify lint exits 0:

```bash
npx --no-install @google/design.md lint \
    <consumer-repo>/docs/designs/design-system.md
```

The generator is deterministic: running twice produces byte-identical
output. If you need to force regeneration (e.g. after a `.pen` pattern
change), re-run `--all` and stage the resulting artifacts.

For each new component, decide whether to ship a CSS class now or defer
until a template needs it. Speculative classes rot; defer is usually
right.

**Note on Node:** `--all` requires `node` + `npx` (for lint + exports).
`--generate` alone auto-falls back to Python-only emission when Node is
absent (pass `--no-node` to be explicit). See
`docs/features/design-system-tooling.md` for the full Node-absent
fallback semantics.

## Step 7 — Produce the gap-audit diff (before commit)

Run `--audit` BEFORE `git commit`. The audit diffs against
`HEAD:<pen-dir>/design-system.md`, so `HEAD` must still hold the PRIOR
pass's `design-system.md`. Running `--audit` after commit produces an
empty diff; the generator emits a stderr `--stale-warn` when it detects
that case.

Canonical sequence:

```bash
# 1. Regenerate every artifact from the updated .pen
python -m tools.design_system_sync --all --pen <path-to>/design-system.pen

# 2. Produce the diff table (goes to stdout)
python -m tools.design_system_sync --audit --pen <path-to>/design-system.pen

# 3. Paste the audit output into gap-audit.md under a new dated heading,
#    THEN `git add` + `git commit`.
```

Append the audit output to `<pen-dir>/gap-audit.md` under:

```markdown
## YYYY-MM-DD — <theme slug>

**Moodboard:** `inspiration/YYYY-MM-DD-<theme>/` (source: <URL>)

<paste the output of `--audit` here>

### Still open
- Card consolidation (deferred — 5 near-duplicate variants)
- Serif body scale (deferred until first research page ships)
```

Update the running component count in the doc header.

**Never add `product/` files to this audit.** It is design-system
scope only.

## Step 8 — Commit

Stage only your own files:

```bash
git add docs/designs/design-system.pen \
        docs/designs/charter.md \
        docs/designs/gap-audit.md \
        docs/designs/inspiration/ \
        <css-root>/brand.css <css-root>/source.css
git status --short  # verify nothing unexpected is staged
git commit -m "design: <theme> pass — <one-line summary>

<body: variable changes, new components, downstream CSS updates,
gap-audit additions. Reference moodboard pass folder.>
"
```

## Gotchas reference

| Symptom | Cause | Fix |
|---|---|---|
| WebFetch returns "no images found" on Cosmos | JS-rendered SPA | Use `agent-browser` (Playwright) |
| `mcp__pencil__batch_design` reports success but file unchanged | MCP edits don't persist without Pencil UI save | Edit `.pen` JSON directly with Python |
| `get_screenshot` returns blank for newly-added Pencil nodes | Render cache | Not a real problem — verify via `batch_get` or `Read` the JSON |
| New `@theme` token doesn't work in templates | Tailwind name doesn't match brand file | Ensure both files use the same token name |
| `$--font-mono` "invalid" warning | False positive — variable refs in `fontFamily` do resolve | Ignore |
| Skill tries to edit a product wireframe | Scope violation | Safety gate — only `design-system.pen` is editable |
| Charter missing, skill won't proceed | By design | Scaffold `charter-template.md` and fill it before moodboard pass |
| Edit proposed with no principle citation | Skipped Step 3 grounding | Reject; require the citation |

## Reference implementation

Commit `a702484` on `yudame/cuttlefish` main (moodboard pass,
2026-04-20):

- Moodboard source: `https://www.cosmos.so/tomcounsell/yudame-research`
- Files changed: `docs/designs/pencil-design-system.pen` (legacy name,
  now `design-system.pen`), `static/css/brand.css`,
  `static/css/source.css`,
  `docs/designs/pencil-design-gap-audit.md` (legacy name, now
  `gap-audit.md`), `docs/designs/inspiration/` (19 images, flat — now
  would be `inspiration/2026-04-20-research-editorial/`).
- Shape: 3 variable edits + 5 new components, no renames, no deletions.

## Version history

- v1.2.1 (2026-04-25): Clarified the safety-gate two-layer scope.
  Layer A (inline Python assertion) covers the `.pen` source path only;
  Layer B (PreToolUse `validate_design_system_readonly.py` hook) covers
  generated artifacts. Removed three vacuous `endswith()` asserts that
  would never fire because Layer A's `target` is hardcoded to the
  `.pen` file.
- v1.2.0 (2026-04-24): Steps 6 and 7 rewritten to invoke
  `python -m tools.design_system_sync` (`--all` for regeneration,
  `--audit` for the gap-audit diff). Added
  `validate_design_system_readonly.py` PreToolUse hook for tool-level
  writes to generated artifacts (Layer B of the safety gate). See
  `docs/features/design-system-tooling.md`.
- v1.1.0 (2026-04-20): Added `charter.md` enforcement, file-organization
  Step 0, `product/` subfolder for non-system `.pen` files, safety gate
  on `.pen` writes, dated inspiration folders with per-pass READMEs,
  principle-citation requirement for each edit.
- v1.0.0 (2026-04-20): Initial — promoted from `cuttlefish` repo's
  `docs/guides/moodboard-to-design-system.md`.
