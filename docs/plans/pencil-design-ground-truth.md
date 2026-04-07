---
status: Planning
type: feature
appetite: Medium
owner: Tom
created: 2026-04-07
tracking: https://github.com/yudame/cuttlefish/issues/223
last_comment_id:
---

# Pencil Design Ground Truth

## Problem

Design ground truth is split across three files with no declared authority. This creates silent divergence: token names differ between `source.css` and `brand.css` (e.g., `--color-cream` vs `--color-bg-cream`), the Pencil file covers only 24 of 36 component classes, and the living style guide at `/ui/examples/` documents only ~60% of production components.

**Current behavior:**
- `static/css/brand.css` acts as the de facto source of truth but has no visual representation
- `docs/designs/pencil-design-system.pen` exists with 24 components but is treated as optional reference, not authority
- `static/css/source.css` uses shorter token names (`--color-cream`, `--color-ink`) that silently diverge from brand.css (`--color-bg-cream`, `--color-text-black`)
- Seven production components (`.mcp-container`, `.mcp-header`, `.install-note`, `.details-accordion`, `.input-brand`, `pre.brand-code`, `.divider-technical`) have no interactive states documented in Pencil
- `apps/public/templates/examples.html` is missing ~40% of production components: MCP layout, forms, code blocks, copy button, accordion, divider

**Desired outcome:**
`pencil-design-system.pen` is declared the single source of truth. `brand.css` is its downstream CSS implementation. `source.css` token names align with `brand.css`. The living style guide documents 100% of production components.

## Prior Art

- **PR #185** (merged 2026-03-19): Template brand CSS rewrite — applied `brand.css` to all production templates. Established the 36-class / 8-token-category structure that is now the full scope. This is the direct predecessor; the current work is establishing Pencil as authoritative over what #185 shipped.
- **Issue #105** (closed): Plan tracking issue for the CSS rewrite. No design-ground-truth work was scoped.

No prior attempts to establish Pencil as authoritative — this is greenfield relative to that goal.

## Architectural Impact

- **Interface changes**: `source.css` `@theme` block token names change to match `brand.css` custom property names. Any Tailwind utility classes that reference the old short names (e.g., `bg-cream`, `text-ink`) will break and must be updated at the same time.
- **New dependencies**: None — Pencil MCP tools already available.
- **Coupling**: Declaring Pencil authoritative means future CSS changes require a Pencil update first. This is the desired coupling direction.
- **Reversibility**: Token rename in `source.css` is the only hard-to-reverse step; all other changes are additive.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM (Tom reviews visual output in Pencil and the style guide)

**Interactions:**
- PM check-ins: 1-2 (Pencil coverage review, style guide completeness sign-off)
- Review rounds: 1 (PR review before merge)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Pencil MCP available | `echo $MCP_PENCIL_ENABLED` or confirm `pencil` in `.mcp.json` | Read/write `.pen` file |

## Solution

### Key Elements

- **Gap audit**: Use Pencil MCP `batch_get` to enumerate all nodes and variables in `pencil-design-system.pen`. Compare against every CSS custom property in `brand.css` `:root` block and every component class. Produce a written gap table.
- **Pencil extension**: Add missing components and token categories to the `.pen` file using `batch_design`. For the seven undocumented production components, define all interactive states (hover, focus, active, disabled).
- **Token name alignment**: Rename `source.css` `@theme` token names to exactly match `brand.css` custom property names. Update any template or utility references that use the old short names.
- **BRANDING_BRIEF update**: Add a canonical paragraph declaring `pencil-design-system.pen` as the visual source of truth and `brand.css` as its downstream CSS implementation.
- **Style guide completion**: Add the missing ~40% of components to `examples.html`: MCP layout (`.mcp-container`, `.mcp-header`, `.install-note`), forms (`.input-brand`), code blocks (`pre.brand-code`, `.copy-btn`), accordion (`.details-accordion`), and divider (`.divider-technical`).

### Flow

Gap audit → Pencil extension → source.css rename + template fixes → BRANDING_BRIEF update → examples.html completion → validation

### Technical Approach

- All `.pen` file reads and writes go through Pencil MCP tools (`batch_get`, `batch_design`) — never raw file reads
- Token rename in `source.css` is a targeted find-replace; search all templates for old Tailwind utility names (e.g., `bg-cream`, `text-ink`, `text-charcoal`) and update at the same time
- `examples.html` additions follow the existing card-per-component pattern already established in that file
- No new CSS variables — every token added to Pencil must have a corresponding custom property already in `brand.css`, and vice versa

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this work touches CSS, HTML templates, and the Pencil file. No Python exception handlers.

### Empty/Invalid Input Handling
- Token rename: verify no Tailwind utility class silently falls back to a default (causes invisible visual regression). The verification command below catches this.

### Error State Rendering
- The style guide itself is the error surface — missing components would appear blank. Full coverage is verified by visual inspection and the component checklist in Success Criteria.

## Test Impact

No existing tests affected — this plan modifies CSS token names, a Pencil design file, an HTML style guide template, and a Markdown doc. There are no unit or integration tests covering these files.

## Rabbit Holes

- **Redesigning components in Pencil**: The goal is documentation coverage, not redesign. Do not change visual appearance of existing components — only add what is missing.
- **Tailwind v4 purge optimization**: `source.css` token rename may prompt investigation of which Tailwind utilities are actually used. That is a separate performance concern; skip it.
- **Animating interactive states**: Pencil interactive state documentation should capture static snapshots (hover color, focus ring), not full animation specs.
- **Full Pencil design system overhaul**: Only extend to cover gaps — do not restructure or reorganize the existing 24 components.

## Risks

### Risk 1: source.css token rename breaks Tailwind utility classes in templates
**Impact:** Visual regressions on pages that use Tailwind utilities derived from the old short names (e.g., `bg-cream` → `bg-color-bg-cream`).
**Mitigation:** Search all templates for old token-derived Tailwind classes before renaming; update in the same commit. Verify with a browser smoke test of the homepage and MCP page after the change.

### Risk 2: Pencil MCP batch_design call creates duplicate nodes
**Impact:** `.pen` file has redundant components; future reads are confusing.
**Mitigation:** Run `batch_get` to confirm a node does not already exist before adding it. Audit the full node list in the gap table first.

### Risk 3: Gap audit misses components added after the last Pencil update
**Impact:** Plan ships with an incomplete Pencil file, silently re-creating the divergence problem.
**Mitigation:** The success criterion requires a complete cross-reference table (all 36 classes, all 8 token categories) committed to the plan before Pencil extension begins.

## Race Conditions

No race conditions identified — all operations are synchronous file edits and Pencil MCP calls with no shared mutable state.

## No-Gos (Out of Scope)

- No new component classes added to `brand.css` as part of this work
- No visual redesign of existing components
- No changes to `base.html` or any application logic
- No migration of Tailwind utility classes to brand.css classes (separate concern)
- No new Pencil frames or canvases beyond the existing design system structure

## Update System

No update system changes required — this work modifies CSS, HTML templates, a Pencil design file, and documentation. No new dependencies or config files to propagate.

## Agent Integration

No agent integration required — this is a design documentation and CSS alignment task. No new MCP tools or bridge changes needed. The Pencil MCP server is already registered and available.

## Documentation

- [ ] Update `docs/BRANDING_BRIEF.md` — add canonical paragraph declaring Pencil as visual source of truth
- [ ] Commit the gap audit table to `docs/designs/pencil-design-gap-audit.md` as a permanent record

## Success Criteria

- [ ] Gap audit complete — `docs/designs/pencil-design-gap-audit.md` lists every token/component in `brand.css` with its corresponding Pencil node ID (or "MISSING" if absent before this work)
- [ ] `pencil-design-system.pen` contains all 36 component classes and all 8 token categories from `brand.css`
- [ ] All seven previously undocumented production components have hover, focus, active, and disabled states in Pencil
- [ ] `source.css` `@theme` token names match `brand.css` custom property names — confirmed by running `grep --color` to show no old short names remain
- [ ] `docs/BRANDING_BRIEF.md` contains explicit statement naming `pencil-design-system.pen` as visual source of truth
- [ ] `apps/public/templates/examples.html` documents all 36 component classes — verified by cross-referencing the class list from `brand.css`
- [ ] No new CSS custom properties exist in `brand.css` or `source.css` without a corresponding Pencil variable
- [ ] Browser smoke test: homepage and MCP page render correctly after `source.css` token rename

## Team Orchestration

### Team Members

- **Builder (gap-audit)**
  - Name: audit-builder
  - Role: Read `pencil-design-system.pen` via Pencil MCP `batch_get`, compare against `brand.css`, produce gap table
  - Agent Type: builder
  - Resume: true

- **Builder (pencil-extension)**
  - Name: pencil-builder
  - Role: Add missing components and interactive states to `pencil-design-system.pen` using `batch_design`
  - Agent Type: designer
  - Resume: true

- **Builder (token-alignment)**
  - Name: token-builder
  - Role: Rename `source.css` `@theme` tokens to match `brand.css` names; update template references
  - Agent Type: builder
  - Resume: true

- **Builder (style-guide)**
  - Name: styleguide-builder
  - Role: Add missing components to `examples.html`; update `BRANDING_BRIEF.md`
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria against the gap audit table and live browser output
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation
- `validator` - Read-only verification
- `designer` - UI implementation, design system adherence

## Step by Step Tasks

### 1. Gap Audit
- **Task ID**: build-gap-audit
- **Depends On**: none
- **Parallel**: true
- Use Pencil MCP `batch_get` to enumerate all nodes and variables in `docs/designs/pencil-design-system.pen`
- List every CSS custom property from `brand.css` `:root` block (8 token categories)
- List every component class from `brand.css` (36 classes)
- For each item, note whether a corresponding Pencil node exists
- Write the complete gap table to `docs/designs/pencil-design-gap-audit.md`
- **Assigned To**: audit-builder
- **Agent Type**: builder

### 2. Pencil Extension
- **Task ID**: build-pencil-extension
- **Depends On**: build-gap-audit
- **Parallel**: false
- Open `docs/designs/pencil-design-system.pen` via Pencil MCP
- For each MISSING token/component in the gap table, add it using `batch_design`
- For each of the seven undocumented production components, add hover, focus, active, and disabled state frames
- **Assigned To**: pencil-builder
- **Agent Type**: designer

### 3. Token Name Alignment
- **Task ID**: build-token-alignment
- **Depends On**: build-gap-audit
- **Parallel**: true
- In `static/css/source.css`, rename all `@theme` tokens to exactly match `brand.css` custom property names (e.g., `--color-cream` → `--color-bg-cream`, `--color-ink` → `--color-text-black`)
- Search all templates for Tailwind utility classes derived from old token names; update references
- Verify no old short-name utilities remain in templates
- **Assigned To**: token-builder
- **Agent Type**: builder

### 4. Style Guide and BRANDING_BRIEF
- **Task ID**: build-style-guide
- **Depends On**: build-gap-audit
- **Parallel**: true
- Add all missing component sections to `apps/public/templates/examples.html`: `.mcp-container`, `.mcp-header`, `.install-note`, `.input-brand`, `pre.brand-code`, `.copy-btn`, `.details-accordion`, `.divider-technical`
- Update `docs/BRANDING_BRIEF.md` to declare `pencil-design-system.pen` as visual source of truth
- **Assigned To**: styleguide-builder
- **Agent Type**: builder

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-pencil-extension, build-token-alignment, build-style-guide
- **Parallel**: false
- Cross-reference gap audit table against Pencil file to confirm all 36 classes and 8 token categories are present
- Confirm `source.css` has no old short-name tokens
- Confirm `examples.html` documents all 36 classes
- Confirm `BRANDING_BRIEF.md` contains the authoritative statement
- Run browser smoke test on homepage and MCP page
- **Assigned To**: final-validator
- **Agent Type**: validator

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No old token names in source.css | `grep -E '\-\-color\-(cream|ink|charcoal|gray|light-gray|border)' static/css/source.css` | exit code 1 |
| All 36 classes present in examples.html | `python -c "import re; css=open('static/css/brand.css').read(); html=open('apps/public/templates/examples.html').read(); classes=[m.group(1) for m in re.finditer(r'^\\.([a-z][a-z-]+)\\s*\\{', css, re.M)]; missing=[c for c in classes if c not in html]; print(missing); assert not missing"` | exit code 0 |
| Gap audit doc exists | `test -f docs/designs/pencil-design-gap-audit.md` | exit code 0 |
| BRANDING_BRIEF updated | `grep -i 'source of truth' docs/BRANDING_BRIEF.md` | output contains "source of truth" |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Should `source.css` token names match `brand.css` exactly (e.g., `--color-bg-cream`) or use a semantic alias pattern? The issue specifies exact alignment, but some Tailwind utilities are more ergonomic with shorter names — confirm before renaming.
2. Are there any production templates that use the Tailwind short-name utilities (e.g., `bg-cream`, `text-ink`) extensively enough that a rename would require a large sweep? (This can be answered by a grep before starting Task 3.)
