---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2025
last_comment_id:
---

# Drop deprecated mdast@3.0.0 from the npm tree (bump @google/design.md 0.1.1 → 0.3.0)

## Problem

The `/update` flow emits an npm deprecation warning on every `npm ci` run:

```
npm warn deprecated mdast@3.0.0: `mdast` was renamed to `remark`
```

**Current behavior:**
`package.json` pins the only direct npm dependency `@google/design.md@0.1.1`, whose own dependency graph declares `mdast: ^3.0.0`. `mdast@3.0.0` was renamed to `remark`, so npm prints the deprecation warning during install. It is a warning, not an error, but we want a clean update run.

**Desired outcome:**
`npm ci --omit=dev` runs with zero warnings, and the deprecated `mdast@3.0.0` package is gone from the dependency tree.

## Freshness Check

**Baseline commit:** `fc272d4ea4f4c239f243d08e2044bde0141b2d75`
**Issue filed at:** 2026-07-11T08:26:36Z
**Disposition:** Minor drift

The issue described **two** fixes. One of them already landed after the issue was filed, so this plan's scope narrows to the mdast drop only.

**File:line references re-verified:**
- `scripts/remote-update.sh:125` — issue claimed it read `npm ci --only=prod` and Fix 1 should change it to `--omit=dev`. **It already reads `npm ci --omit=dev`.** Fix 1 was completed by PR #2041 (commit `bdfdd019`, merged 2026-07-12, *after* this issue was filed 2026-07-11). No action needed for Fix 1.
- `package.json:7` — still pins `@google/design.md@0.1.1`. Fix 2 (the mdast drop) is **not** done. Confirmed still needed.
- `package-lock.json:818–822` — `mdast@3.0.0` present with `deprecated: mdast was renamed to remark`. Confirmed.

**Cited sibling issues/PRs re-checked:**
- PR #2041 — merged 2026-07-12; changed only `scripts/remote-update.sh` (`--only=prod` → `--omit=dev`). It did not touch `package.json`, so the mdast warning it did *not* address remains.

**Commits on main since issue was filed (touching referenced files):**
- `bdfdd019` fix(update): use npm ci --omit=dev instead of deprecated --only=prod — **already fixes Fix 1**. This is the only relevant commit; it does not touch the mdast dependency.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Because Fix 1 is already merged, the No-Gos section marks it out of scope, and the acceptance criterion "`npm ci --omit=dev` runs with zero warnings" is satisfied by *this* plan removing the last remaining warning (mdast).

## Prior Art

- **PR #2041**: fix(update): use npm ci --omit=dev instead of deprecated --only=prod — merged 2026-07-12; fixed the `npm warn config only` half of this issue. Relevant: it is the reason Fix 1 is out of scope here.
- **PR #1170 (#1162)**: DESIGN.md integration on .pen ground truth (Phase 1) — introduced the `@google/design.md` dependency and `tools/design_system_sync.py`. Relevant: establishes that the package is consumed only via `npx @google/design.md <lint|diff|export>`, bounding the blast radius of a version bump.

No prior attempt to bump `@google/design.md` exists.

## Research

**Queries used (npm registry, not WebSearch — the relevant source is the npm package metadata):**
- `npm view @google/design.md versions` / `dependencies` / `bin`
- Isolated install of `@google/design.md@0.3.0` to inspect the resolved tree and CLI surface.

**Key findings:**
- `@google/design.md@0.3.0` is the current latest (versions: 0.1.0, 0.1.1, 0.2.0, 0.3.0).
- 0.3.0 dependencies are `citty`, `remark-frontmatter`, `remark-mdx`, `remark-parse`, `remark-stringify`, `unified`, `unist-util-visit`, `yaml`, `zod` — **no `mdast`**. The migration from `mdast` to the `remark-*`/`unified` stack is complete in 0.3.0.
- Isolated `npm install @google/design.md@0.3.0` produces a tree with the deprecated `mdast` package **absent** (only `@types/mdast@4.x`, a non-deprecated TypeScript-types package pulled by remark, remains — this does not trigger the deprecation warning).
- 0.3.0 CLI (`design.md --help`) exposes `lint`, `diff`, `export`, `spec`. Every subcommand `tools/design_system_sync.py` calls is present: `lint`, `diff`, `export --format dtcg`, `export --format tailwind` (in 0.3.0 `tailwind` is an alias for `json-tailwind`; `dtcg` emits W3C Design Tokens). No subcommand or format the code uses was removed.

## Spike Results

### spike-1: 0.3.0 drops the deprecated mdast package while preserving the CLI surface the code uses
- **Assumption**: "Bumping `@google/design.md` to 0.3.0 removes `mdast@3.0.0` from the tree AND keeps `lint`/`diff`/`export --format dtcg`/`export --format tailwind` working."
- **Method**: prototype (isolated `npm install` in a scratch dir) + code-read of `tools/design_system_sync.py`.
- **Finding**: Confirmed on both counts. Isolated install of 0.3.0 shows no `mdast` package (only `@types/mdast@4.x`); `design.md --help` lists `lint|diff|export|spec`; the `export` help confirms `dtcg` and `tailwind` (alias) formats still exist.
- **Confidence**: high
- **Impact on plan**: The bump is safe to make as a single-line `package.json` edit plus a lockfile regeneration. The only code/test follow-on is the test probe and doc references (below).

## Data Flow

Not applicable to runtime data — this is a build-time dependency change. The consumption path for completeness: `python -m tools.design_system_sync` → `_probe_npx()` (`design_system_sync.py:565`) checks `npx --no-install @google/design.md --version` → `_run_npx()` (`:597`) shells out to `npx --no-install @google/design.md <lint|diff|export …>`. The version bump changes only which resolved package `npx` invokes; the CLI contract used by these calls is unchanged (spike-1).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Node + npm on PATH | `command -v npm` | Regenerate `package-lock.json` and validate `npm ci --omit=dev` |
| npm registry reachable | `npm view @google/design.md@0.3.0 version` | Confirm the target version resolves |

No repo secrets required.

## Solution

### Key Elements

- **`package.json` pin**: bump `@google/design.md` from `0.1.1` to `0.3.0`.
- **`package-lock.json`**: regenerated from the new pin so `mdast@3.0.0` is removed and the `remark-*`/`unified` subtree is recorded.
- **Integration-test version probe**: `tests/integration/test_design_system_pipeline.py::_npx_present()` currently gates on `"0.1.1" in result.stdout`; update it to the new version so the design-system pipeline tests continue to run (rather than silently skip) after the bump.
- **Documentation references**: update the handful of docs/skill files that name the `0.1.1` pin, and the one stale `--only=prod` mention in the update skill left over from PR #2041.

### Flow

`package.json` (0.3.0 pin) → `npm install` regenerates `package-lock.json` (mdast gone) → `npm ci --omit=dev` runs clean → `npx @google/design.md <lint|diff|export>` still resolves the same CLI contract.

### Technical Approach

- Edit `package.json:7` → `"@google/design.md": "0.3.0"`.
- Run `npm install` (not `npm ci`) once to regenerate `package-lock.json` against the new pin; commit the regenerated lockfile. Verify `mdast` (the deprecated meta-package) no longer appears via `npm ls mdast` / grep of the lockfile.
- Update `_npx_present()` in `tests/integration/test_design_system_pipeline.py` to check for `"0.3.0"` (and update the skip `reason` string at :76 that names 0.1.1). Prefer keeping it a pinned exact match to the new version, matching the existing pattern.
- Update the doc/skill references that name `0.1.1` (see Documentation) and fix the stale `npm ci --only=prod` line in `.claude/skills/update/SKILL.md:113` → `--omit=dev`.
- No change to `tools/design_system_sync.py` itself — `_probe_npx()` there checks only that `--version` exits 0 and does not pin a version string (spike-1).

## Failure Path Test Strategy

### Exception Handling Coverage
- No new exception handlers are introduced. The existing `_run_npx`/`_probe_npx` handlers in `design_system_sync.py` are unchanged by this plan. "No exception handlers added in scope."

### Empty/Invalid Input Handling
- No new functions accept input. The version-bump surface is a static dependency pin; there is no runtime input path to exercise. Existing `test_design_system_sync.py` graceful-degradation tests (package missing) remain valid and are re-run.

### Error State Rendering
- No user-visible output changes. The design-system-sync error messages already tested (`"@google/design.md not installed"`, `"differs from generated output"`) are unaffected by the version bump and are re-run to confirm.

## Test Impact

- [ ] `tests/integration/test_design_system_pipeline.py::_npx_present` — UPDATE: change the `"0.1.1" in result.stdout` gate (line 59) and the skip `reason` at line 76 to `0.3.0`; otherwise every test in this module silently skips after the bump.
- [ ] `tests/integration/test_design_system_pipeline.py::test_fixture_design_md_passes_lint` — VERIFY (no code change expected): re-run against 0.3.0 to confirm the committed fixture `design-system.md` still passes 0.3.0 `lint`. If 0.3.0 lint is stricter and the fixture fails, regenerate the fixture via `--all` (tracked as a build-time contingency, not a planned edit).
- [ ] `tests/unit/tools/test_design_system_sync.py` — NO CHANGE: these unit tests mock/probe the CLI presence and do not pin a version. The docstring at line 326 mentions `npm ci --only=prod` in prose only; optionally corrected to `--omit=dev` for accuracy (non-functional).

No other existing tests reference the pinned version or the mdast package.

## Rabbit Holes

- **Rewriting `design_system_sync.py` for 0.3.0's new `spec` subcommand or the `css-tailwind` export format.** Out of scope — the code uses `dtcg` and `tailwind`, both still valid. Do not chase new 0.3.0 features.
- **Regenerating committed design-system artifacts across the whole repo.** There are no committed real artifacts (only test fixtures under `tests/`); do not go hunting for a repo-wide regen.
- **Auditing every transitive dep of 0.3.0 for other deprecations.** The acceptance criterion is specifically that `mdast@3.0.0` is gone and `npm ci --omit=dev` is warning-free; verify that exact outcome, don't expand into a general dependency audit.

## Risks

### Risk 1: 0.3.0 export output differs from 0.1.1, breaking the committed fixture lint or the round-trip check
**Impact:** `test_design_system_pipeline.py` could fail if 0.3.0's `export`/`lint` produces different bytes than the committed `tests/.../design-system.md` fixture expects.
**Mitigation:** The pipeline tests are round-trip consistent (they generate artifacts then `--check` that regeneration matches — no golden comparison against 0.1.1 bytes), so format drift is self-healing for `--all`/`--check`. The one golden-ish assertion is `test_fixture_design_md_passes_lint`. Build step re-runs the module; if the fixture fails 0.3.0 lint, regenerate it via `python -m tools.design_system_sync --all` and commit.

### Risk 2: Version-probe left un-updated → design-system pipeline tests silently skip
**Impact:** Loss of coverage without a visible failure; the bump could ship with the pipeline untested.
**Mitigation:** The probe update is a first-class task and a Verification-table check (`_npx_present` names 0.3.0, not 0.1.1). Reviewer confirms the module actually runs (not skips) in CI/local.

## Race Conditions

No race conditions identified — the change is a static dependency pin plus lockfile regeneration; there are no concurrent or async code paths touched.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2025] Fix 1 (`npm ci --only=prod` → `--omit=dev` in `scripts/remote-update.sh`) — already completed by merged PR #2041; the file already reads `--omit=dev`. Re-doing it is out of scope. (Same tracking issue; the freshness check reclassified this half as done.)
- Adopting 0.3.0's new `spec` subcommand or `css-tailwind` export format — not required by the acceptance criteria.

## Update System

`scripts/remote-update.sh` already runs `npm ci --omit=dev` (PR #2041). No change to the update *script* is needed. One stale doc line in the update skill (`.claude/skills/update/SKILL.md:113`, which still says `npm ci --only=prod`) is corrected to `--omit=dev` as part of the Documentation tasks. The regenerated `package-lock.json` propagates automatically on the next `/update` via the existing `npm ci` step — no migration function required (this is not a Popoto model change).

## Agent Integration

No agent integration required. `@google/design.md` is invoked as an npm CLI via `npx` inside `tools/design_system_sync.py`; the version bump changes only which package version `npx` resolves. No MCP server, `.mcp.json`, or bridge wiring is involved.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/design-system-tooling.md` — replace the `@google/design.md@0.1.1` pin references (lines ~17, ~299) with `0.3.0`.
- [ ] Update `docs/features/README.md:52` — change the `@google/design.md@0.1.1` mention in the Design-System Tooling row to `0.3.0`.
- [ ] Update `.claude/skills/update/SKILL.md:113` — bump the `0.1.1` mention if present and fix the stale `npm ci --only=prod` → `npm ci --omit=dev`.

### External Documentation Site
- Not applicable — this repo has no external docs site for this area.

### Inline Documentation
- [ ] (Optional) Correct the `npm ci --only=prod` prose in `tests/unit/tools/test_design_system_sync.py:326` docstring to `--omit=dev` for accuracy.

## Success Criteria

- [ ] `package.json` pins `@google/design.md` at `0.3.0`.
- [ ] `package-lock.json` regenerated; `npm ls mdast` shows no deprecated `mdast@3.0.0` (only `@types/mdast@4.x` may remain).
- [ ] `npm ci --omit=dev` completes with zero warnings.
- [ ] `tests/integration/test_design_system_pipeline.py` runs (does not skip) and passes; `_npx_present()` gates on `0.3.0`.
- [ ] `tests/unit/tools/test_design_system_sync.py` passes.
- [ ] Doc references to the `0.1.1` pin updated to `0.3.0`; the stale `--only=prod` update-skill line corrected.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Small solo-dev chore — a single builder handles the bump, lockfile, test probe, and docs; one validator confirms the clean install and passing tests.

### Team Members

- **Builder (mdast-bump)**
  - Name: `mdast-builder`
  - Role: Bump the pin, regenerate the lockfile, update the test probe and docs
  - Agent Type: builder
  - Resume: true

- **Validator (mdast-bump)**
  - Name: `mdast-validator`
  - Role: Verify zero-warning `npm ci --omit=dev`, mdast absence, and passing design-system tests
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Uses Tier 1 `builder` and `validator` only. No domain framing required — this is a dependency pin.

## Step by Step Tasks

### 1. Bump pin and regenerate lockfile
- **Task ID**: build-bump
- **Depends On**: none
- **Validates**: `npm ci --omit=dev` warning-free; `npm ls mdast` shows no `mdast@3.0.0`
- **Informed By**: spike-1 (0.3.0 drops mdast, keeps lint/diff/export)
- **Assigned To**: mdast-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `package.json` → `"@google/design.md": "0.3.0"`.
- Run `npm install` to regenerate `package-lock.json`; confirm deprecated `mdast` is gone (`npm ls mdast`, grep lockfile).
- Run `npm ci --omit=dev` and capture stderr to confirm zero warnings.

### 2. Update test probe and docs
- **Task ID**: build-probe-docs
- **Depends On**: build-bump
- **Validates**: `tests/integration/test_design_system_pipeline.py` (runs, not skips)
- **Assigned To**: mdast-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_npx_present()` gate (line 59) and skip `reason` (line 76) in `test_design_system_pipeline.py` to `0.3.0`.
- Update `0.1.1` references in `docs/features/design-system-tooling.md`, `docs/features/README.md:52`, and `.claude/skills/update/SKILL.md` (including the stale `--only=prod` line).

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-bump, build-probe-docs
- **Assigned To**: mdast-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `npm ci --omit=dev` is warning-free and `mdast@3.0.0` is absent.
- Run `pytest tests/integration/test_design_system_pipeline.py tests/unit/tools/test_design_system_sync.py` and confirm pass (not skip).
- Run `python -m ruff check .` and `python -m ruff format --check .`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Pin bumped | `grep -c '"@google/design.md": "0.3.0"' package.json` | output contains 1 |
| Deprecated mdast gone from lockfile | `grep -c '"node_modules/mdast"' package-lock.json` | match count == 0 |
| Clean install (no warnings) | `npm ci --omit=dev 2>&1 \| grep -c 'npm warn'` | match count == 0 |
| Probe updated to 0.3.0 | `grep -c '0.1.1' tests/integration/test_design_system_pipeline.py` | match count == 0 |
| Design-system tests pass | `pytest tests/integration/test_design_system_pipeline.py tests/unit/tools/test_design_system_sync.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

No blocking open questions. The scope, target version, and test/doc touch-points are all verified (freshness check + spike-1). Ready for critique.
