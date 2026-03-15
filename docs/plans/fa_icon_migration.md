---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-03-15
tracking: https://github.com/yudame/psyoptimal/issues/314
last_comment_id:
---

# Migrate Legacy Font Awesome Icon Classes

## Problem

The psyoptimal codebase uses legacy Font Awesome class prefixes (`fal`, `fas`, `far`) throughout its templates. The two loaded FA Pro kits (`kit/647969f711.js` and `kit/226504bb65.js`) support both legacy and modern formats, but the legacy format is non-standard and creates inconsistency as the codebase grows.

**Current behavior:**
~118 usages of `fal` (FA light) and ~30 usages of `fas`/`far` (FA solid/regular) exist in `templates/`. The legacy short-prefix format was the FA4/early-FA5 convention.

**Desired outcome:**
All icon classes use the modern FA6 format: `fa-light`, `fa-solid`, `fa-regular`. No legacy prefixes remain in templates.

## Prior Art

No prior issues or PRs found related to Font Awesome icon class migration in this repository.

## Data Flow

Not applicable — this is a template string replacement with no runtime data flow changes. The rendered HTML output changes format only; the FA kits resolve both old and new class names identically.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — FA Pro kits support both formats; visual output is unchanged
- **Coupling**: No change
- **Data ownership**: No change
- **Reversibility**: Fully reversible — a reverse grep-replace restores legacy format

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The FA Pro kits already support the modern class format.

## Solution

### Key Elements

- **Grep scan**: Confirm exact counts and locations of `fal`, `fas`, `far` class usages in `templates/`
- **Bulk replacement**: Apply sed or find-replace across all template files
- **Verify exclusions**: Ensure replacements don't corrupt unrelated strings (e.g., `far` inside a word like `welfare`)

### Flow

Grep for occurrences → Review edge cases → Apply replacements → Manual spot-check renders

### Technical Approach

Three targeted replacements in `templates/`:

1. `class="fal ` → `class="fa-light "` (and any variant with multiple classes)
2. `class="fas ` → `class="fa-solid "`
3. `class="far ` → `class="fa-regular "`

The replacements must be pattern-aware to avoid false positives:
- Match `fal fa-`, `fas fa-`, `far fa-` as the canonical prefix patterns
- The FA class always appears as a word boundary: `fal fa-[icon-name]`

Actual sed patterns (applied to all `.html` and `.jinja2` files under `templates/`):
```bash
# fal -> fa-light
sed -i 's/\bfal fa-/fa-light fa-/g' templates/**/*.html

# fas -> fa-solid
sed -i 's/\bfas fa-/fa-solid fa-/g' templates/**/*.html

# far -> fa-regular
sed -i 's/\bfar fa-/fa-regular fa-/g' templates/**/*.html
```

Edge case: some usages may appear as `class="fal"` (prefix only without an icon name). Check for bare prefix usage and handle separately.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a template-only find/replace

### Empty/Invalid Input Handling
- Not applicable — no function inputs involved

### Error State Rendering
- Not applicable — the change is static HTML class attributes

## Rabbit Holes

- **Upgrading FA kit versions**: The kits are already loaded and working. Do not attempt to update the JS kit URLs or switch to self-hosted assets.
- **Auditing icon semantics**: Do not evaluate whether `fa-light` is the right weight for each icon — that's a design decision, not a migration task.
- **CSS customizations**: Do not touch any FA-related CSS overrides or custom icon definitions.

## Risks

### Risk 1: False positive replacements
**Impact:** Corrupted class names on non-FA HTML attributes containing `fas`, `fal`, or `far` as substrings.
**Mitigation:** Use word-boundary patterns (`\bfal fa-`, `\bfas fa-`, `\bfar fa-`) to only match the canonical icon prefix pattern. Review a diff before committing.

### Risk 2: Visual regressions
**Impact:** Icons render differently if the FA kits resolve `fa-light` vs `fal` differently in some edge case.
**Mitigation:** Both formats are documented as equivalent in FA6 Pro. The kits are already loaded with Pro license. Do a spot-check on a few pages post-deploy.

## Race Conditions

No race conditions identified — all operations are static template string replacements with no async behavior.

## No-Gos (Out of Scope)

- Changing icon choices or weights (light vs. solid vs. regular)
- Updating FA kit URLs or versions
- Fixing any other frontend styling issues encountered during the audit
- Adding new icons

## Update System

No update system changes required — this is a cross-repo (psyoptimal) template-only change with no impact on the AI system's deployment pipeline.

## Agent Integration

No agent integration required — this is a template string replacement in the psyoptimal repo and does not touch the AI bridge, tools, or MCP servers.

## Documentation

No documentation changes needed — this is a code hygiene chore with no user-facing behavior change. The FA class naming convention is self-evident from the modern format.

## Success Criteria

- [ ] Zero occurrences of `fal fa-`, `fas fa-`, `far fa-` patterns in `templates/`
- [ ] All replaced classes use `fa-light fa-`, `fa-solid fa-`, `fa-regular fa-` format
- [ ] Git diff shows only class prefix changes (no other modifications)
- [ ] Manual spot-check of 3-5 pages confirms icons render correctly
- [ ] No false positives in unrelated text (grep check on non-icon uses of these strings)

## Team Orchestration

### Team Members

- **Builder (templates)**
  - Name: template-builder
  - Role: Apply FA class prefix replacements across all template files
  - Agent Type: builder
  - Resume: true

- **Validator (templates)**
  - Name: template-validator
  - Role: Verify replacements are complete and no false positives introduced
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Audit and Replace
- **Task ID**: build-fa-replace
- **Depends On**: none
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Run grep to confirm exact counts: `grep -rn '\bfal fa-\|\bfas fa-\|\bfar fa-' templates/`
- Apply replacements using sed across all `.html` and `.jinja2` template files
- Check for any bare prefix usages (`class="fal"` without an icon name) and handle
- Commit with message: "Migrate FA icon classes from legacy to modern format"

### 2. Validate Replacements
- **Task ID**: validate-fa-replace
- **Depends On**: build-fa-replace
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify zero legacy prefix occurrences remain: `grep -rn '\bfal fa-\|\bfas fa-\|\bfar fa-' templates/` should return nothing
- Verify no false positives: check diff for any non-icon string corruption
- Verify modern prefix counts are reasonable (should match original legacy counts)

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-fa-replace
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm success criteria are all met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No legacy fal classes | `grep -rn '\bfal fa-' templates/` | exit code 1 |
| No legacy fas classes | `grep -rn '\bfas fa-' templates/` | exit code 1 |
| No legacy far classes | `grep -rn '\bfar fa-' templates/` | exit code 1 |
| Modern fa-light present | `grep -rn 'fa-light fa-' templates/` | output > 0 |
| Modern fa-solid present | `grep -rn 'fa-solid fa-' templates/` | output > 0 |

---

## Open Questions

1. Are there any template files outside the `templates/` directory (e.g., in `static/` or inline `<script>` blocks) that also use legacy FA prefixes?
2. Should bare `class="fal"` usages (prefix without icon name, if any exist) be migrated or left alone?
