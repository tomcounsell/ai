---
name: do-design-system
description: "Turn a moodboard into additive design-system edits. Triggered by 'apply this moodboard', 'tighten the design system', 'theme pass', 'design system pass', 'organize design files', or a moodboard URL."
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

# Design System Skill

Translate a visual moodboard (Cosmos, Pinterest, Are.na, a plain image
folder) into concrete, additive edits to a design system — `.pen`
source, charter, downstream CSS tokens — and enforce the canonical
organization of design files (`docs/designs/`: `design-system.pen`,
`charter.md`, `gap-audit.md`, `inspiration/`, `product/`) in the repo.
Success: 3–7 charter-grounded additive edits landed in one commit, with
downstream CSS in sync and the pass logged in `gap-audit.md`.

Do **not** run a pass when the moodboard is abstract (vibes only, no
reusable motifs) or when the existing system already matches — additive
changes without a concrete signal waste effort.

## Repo Context Probe

If `.claude/skill-context/do-design-system.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The moodboard scrape, `.pen` JSON editing, motif tables, and gap-audit log are all generic — they need only a browser surface and a JSON-editable `.pen`. The context file declares the repo-specific machinery for Steps 5–7: the **deterministic generator** that emits downstream artifacts (`brand.css`, `source.css`, `design-system.md`) from the `.pen` source, any **PreToolUse hooks** that guard those generated artifacts, and the reference implementation. When the file is absent (the common case in a foreign repo), there is no generator — sync `brand.css` from the `.pen` variables by hand (Step 6) and skip the hook-specific guidance.

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

## When to load sub-files

| Sub-file | Load when... |
|---|---|
| [references/file-organization.md](references/file-organization.md) | Step 0 — canonical `docs/designs/` structure, invariants, audit checklist, gap→migration table |
| [charter-template.md](charter-template.md) | Scaffolding `charter.md` in a new or legacy repo — copy its body into `docs/designs/charter.md` |
| [references/moodboard-capture.md](references/moodboard-capture.md) | Steps 1–2 — browser scrape commands, download naming, per-pass README + motif-table format |
| [references/pen-editing.md](references/pen-editing.md) | Step 5 — safety-gate code, MCP persistence gotcha, direct-JSON editing pattern, gotchas table |

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

0. Audit file organization; verify charter exists
1. Headless browser scrape of the moodboard (NOT WebFetch for JS SPAs) → image URLs
2. Download into `inspiration/YYYY-MM-DD-<theme>/` + per-pass README (source URL, motif table, legend)
3. Read images + charter; critique the system against its principles
4. Propose 3–7 additive edits, each citing a principle; get approval
5. Safety-gated JSON edit to `design-system.pen`
6. Sync `brand.css` + Tailwind `@theme`
7. Append dated section to `gap-audit.md`
8. Commit

## Step 0 — Audit file organization

Before any moodboard work, audit the repo against the canonical
structure — read [references/file-organization.md](references/file-organization.md)
for the full structure, invariants, checklist, and gap→migration table.
Propose migrations; do NOT auto-apply.

If `charter.md` is missing or empty, **HALT**. Scaffold it from
[charter-template.md](charter-template.md) and ask the user to fill it
before proceeding. The charter is not boilerplate — it needs human
judgment. No moodboard edits until a charter exists and reflects the
product.

## Steps 1–2 — Capture the moodboard

Follow [references/moodboard-capture.md](references/moodboard-capture.md):

1. **Extract images.** Moodboard sites are JS-rendered SPAs — `WebFetch`
   returns shell HTML only. Drive the user's real browser (BYOB MCP,
   `BYOB_ALLOW_EVAL=1`), scroll to trigger lazy tiles, enumerate images,
   and `curl` them at usable resolution into
   `docs/designs/inspiration/YYYY-MM-DD-<theme>/` with stable numbered
   filenames (`NN-<author-or-theme>.webp`, `cover.webp`).
2. **Read the images yourself** with the `Read` tool — do NOT delegate
   to a subagent; the critique depends on *your* direct pattern
   recognition. Write the per-pass `README.md` (source URL, image
   legend, motif table). Absent (`❌`) and partial (`⚠`) motifs are the
   ONLY candidates for edits; present motifs are confirmation — leave
   them alone.

## Step 3 — Critique the existing system

Load sources of truth **in this order**: `charter.md` (edits are tested
against it) → `design-system.pen` (read the JSON directly; see Step 5)
→ `brand.css`/`source.css` (downstream state) → `gap-audit.md` (recent
changes, still-open items).

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

Read [references/pen-editing.md](references/pen-editing.md) before
touching the file. Non-negotiables:

- **Safety gate first.** Run the inline Python assertion (in the
  reference) that pins the write target to `design-system.pen` and
  refuses any other path. Never run `batch_design`, `set_variables`,
  or direct JSON writes against any other `.pen` file — product
  wireframes have different schemas and would be corrupted. A repo may
  also register a PreToolUse hook guarding generated artifacts; the two
  guards are complementary — where both exist, do NOT remove either.
- **Pen MCP does not persist.** `batch_design`/`set_variables` edit
  an in-memory editor session and are silently discarded without a
  desktop-app save. The reliable path is editing the `.pen` JSON
  directly with Python (plain JSON, indent=2) — full pattern, ID and
  `$--variable` conventions, and post-write verification are in the
  reference.

## Step 6 — Sync downstream CSS

Downstream artifacts — `brand.css` (`:root` tokens), `source.css` (Tailwind `@theme` bridge), and
any `design-system.md` — must mirror the `.pen` source 1:1.

- **Context file declares a generator** → run it exactly as specified. Such a generator is
  typically deterministic (running twice produces byte-identical output) and emits every artifact
  from the `.pen`. In that case do NOT hand-edit the generated files — the repo may register a
  PreToolUse hook that blocks direct Write/Edit against them and a commit-time hook that blocks on
  drift. Follow the context file's invocation and lint step.
- **No context file (generic case)** → there is no generator. Hand-edit `brand.css` so its token
  names and values match the `.pen` variables exactly (and `source.css` if the project uses
  Tailwind). Token-name divergence between the `.pen` and the CSS is silent, so mirror them
  carefully.

For each new component, decide whether to ship a CSS class now or defer
until a template needs it. Speculative classes rot; defer is usually
right.

## Step 7 — Produce the gap-audit diff (before commit)

Record what this pass changed in `gap-audit.md` as a dated section, BEFORE `git commit`.

- **Context file declares a generator with an audit mode** → run its audit step (which diffs the
  regenerated artifacts against `HEAD`) before committing, and follow its ordering constraints
  (the diff must be produced while `HEAD` still holds the prior pass's generated state). Paste its
  output into `gap-audit.md`.
- **No context file (generic case)** → hand-write the dated section: list the variable edits, new
  components, and downstream CSS updates this pass made.

Append the audit/change summary to `<pen-dir>/gap-audit.md` under:

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

## Reference implementation

A worked reference pass (variable edits + new components, no renames or deletions) is recorded
in `.claude/skill-context/do-design-system.md` when the repo declares one.
