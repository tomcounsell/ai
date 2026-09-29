---
name: do-design-system
description: "Moodboard-driven design-system edits. Triggered by 'apply this moodboard', 'tighten the design system', 'theme pass', 'design system pass', 'organize design files', or a moodboard URL."
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

# Design System Skill

Translate a visual moodboard (Cosmos, Pinterest, Are.na, a plain image folder) into concrete, additive edits to a design system (`.pen` source, charter, downstream CSS tokens), and keep the repo's design files in the canonical `docs/designs/` layout.

**Done:** 3-7 charter-grounded additive edits, approved by the user, landed in one commit, with downstream CSS in sync and the pass logged in `gap-audit.md`. Skip the pass when the moodboard is vibes only (no reusable motifs) or the system already matches it.

## Repo Context Probe

If `.claude/skill-context/do-design-system.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file declares the repo-specific machinery for Steps 5-7: a deterministic generator that emits downstream artifacts (`brand.css`, `source.css`, `design-system.md`) from the `.pen`, any hooks guarding those artifacts, and a reference pass. Without it there is no generator: hand-sync `brand.css` from the `.pen` variables and hand-write the gap-audit entry.

## Scope

Edit only: `docs/designs/design-system.pen` (reserved name, the source of truth, plain JSON), `charter.md` (scaffolding), `gap-audit.md` (append-only), `inspiration/**`, the `docs/designs/` and `product/` README indexes, and downstream CSS (brand tokens and the Tailwind `@theme` bridge).

Never touch: any other `.pen` file (product wireframes, flows, and mockups have different schemas and get corrupted), application code or templates, or existing token and component names (no renames, no deletions).

## Sub-files

| Sub-file | Load when |
|---|---|
| [references/file-organization.md](references/file-organization.md) | Step 0: canonical layout, invariants, gap-to-migration table |
| [charter-template.md](charter-template.md) | Scaffolding `docs/designs/charter.md` |
| [references/moodboard-capture.md](references/moodboard-capture.md) | Steps 1-2: scrape, download naming, per-pass README and motif table |
| [references/pen-editing.md](references/pen-editing.md) | Step 5: safety gate, direct-JSON edit pattern, gotchas |

## Pipeline

0. **Audit file organization** against the canonical layout; propose migrations, never auto-apply. If `charter.md` is missing or empty, scaffold it from the template and **halt** until the user fills it: the charter needs human judgment, and no moodboard edit lands without one.
1. **Capture the moodboard.** Moodboard sites are JS-rendered, so `WebFetch` returns only the shell; drive the real browser (BYOB MCP with `BYOB_ALLOW_EVAL=1`) and download images into `docs/designs/inspiration/YYYY-MM-DD-<theme>/`.
2. **Read the images yourself** (not via a subagent; the critique rests on your own pattern recognition) and write the per-pass README with its motif table. Only absent and partial motifs are edit candidates; present motifs are confirmation.
3. **Critique the system** against `charter.md`, then `design-system.pen`, CSS, and recent `gap-audit.md` entries. Per gap: problem, evidence (image numbers), one concrete fix, charter principle. An edit no principle supports is dropped, or a charter amendment is proposed first in a separate commit. Call out gaps only; defer renames and consolidations.
4. **Propose 3-7 edits** as a table (edit, tier, why, principle), then get explicit approval; the user may swap faces, cut edits, or retune values. Invariants: additive only; reuse an unused existing token before inventing one; new tokens land in the charter's declared tier (semantic by default); components use the charter's `Category/Variant` taxonomy (a new category is a charter edit first); any new font needs a license row in the charter first; retuned colors meet the charter's contrast targets.
5. **Apply to `design-system.pen`** following `references/pen-editing.md`: run the safety-gate assertion first, and edit the JSON directly, since Pen MCP edits do not persist to disk. Where a repo also has a hook guarding generated artifacts, keep both guards.
6. **Sync downstream CSS** so tokens mirror the `.pen` 1:1. With a declared generator, run it and never hand-edit its outputs. Without one, hand-edit `brand.css` (and `source.css` under Tailwind); name divergence fails silently. Defer CSS classes for new components until a template needs them.
7. **Log the pass** in `gap-audit.md` before committing: a `## YYYY-MM-DD — <theme slug>` section with the moodboard folder and source URL, the change list (or the generator's audit output), and a "Still open" list; update the header's component count. Never list `product/` files there.
8. **Commit** only your own files (the `.pen`, charter, gap-audit, inspiration folder, CSS), checking `git status --short` first, with message `design: <theme> pass — <summary>`.
