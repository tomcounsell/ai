---
status: Draft
type: fix
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/443
last_comment_id: IC_kwDOEYGa087ztVqR
---

# Plan Completion Gate

## Problem

The SDLC pipeline can mark a plan "Complete" while acceptance criteria and checkboxes remain unchecked. This was discovered during config consolidation (#416 / PR #438): `/do-docs` set `status: Complete` on a plan with 20+ unchecked items. The result is that requirements silently drop -- a PR can be merged with a "Complete" plan that has unfinished work.

**Root cause chain:**
1. `/do-docs` unconditionally marks plans Complete (SKILL.md lines 345-362) after touching docs, regardless of other plan checkboxes
2. No skill validates unchecked plan items before allowing completion
3. `/do-build` does not validate acceptance criteria against what was actually built
4. `/do-pr-review` evaluates code quality but never systematically walks `- [ ]` items to flag unaddressed ones

**Current behavior:**
- `/do-docs` runs, touches some docs, sets `status: Complete`
- `/do-merge` checks pipeline stages (TEST, REVIEW, DOCS) but never reads the plan's checkboxes
- `/do-pr-review` loosely references plan acceptance criteria but does not enumerate unchecked items as blockers
- `/do-build` has no post-build validation against the plan spec

**Desired outcome:**
- `/do-docs` does not set `status: Complete`
- A deterministic `scripts/validate_build.py` script validates builds against plan specs
- `/do-merge` includes a completion gate that scans for unchecked `- [ ]` items
- `/do-pr-review` systematically cross-references plan checkboxes against the PR diff
- Requirements cannot silently drop from the pipeline

## Prior Art

- **`scripts/check_prerequisites.py`** -- Parses plan markdown, extracts a table, runs check commands. Good pattern to follow for `validate_build.py`.
- **`.claude/hooks/validators/validate_merge_guard.py`** -- Hook that blocks merges without authorization. Similar gating pattern for the completion gate.
- **Issue #443 comment 1** -- Detailed spec for `/do-pr-review` plan checklist validation.
- **Issue #443 comment 2** -- Detailed spec for `scripts/validate_build.py` including file path assertions, verification table commands, and grep-based success criteria.

## Architectural Impact

- **Modified skills**: `/do-docs`, `/do-build`, `/do-pr-review`, `/do-merge` -- all receive targeted edits to their SKILL.md or command files
- **New script**: `scripts/validate_build.py` -- standalone Python script, no new dependencies
- **Interface changes**: None for external consumers. Internal pipeline behavior changes (stricter gating).
- **Coupling**: Slightly increases coupling between skills and plan document format, but the plan format is already well-established.
- **Reversibility**: High -- each change is independent and can be reverted without affecting others

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (well-specified issue)
- Review rounds: 1

## Prerequisites

No prerequisites -- all skill files and hook infrastructure already exist.

## Solution

### Key Elements

- **Fix 1**: Remove the `status: Complete` behavior from `/do-docs`
- **Fix 2**: Add completion gate to `/do-merge` that scans for unchecked plan items
- **Fix 3**: Create `scripts/validate_build.py` for deterministic post-build validation
- **Fix 4**: Enhance `/do-pr-review` to systematically validate plan checkboxes against the PR diff
- **Fix 5**: Wire `/do-build` to call `validate_build.py` after build completes

### Flow

Fix 1 (do-docs) -> Fix 3 (validate_build.py) -> Fix 5 (wire into do-build) -> Fix 4 (do-pr-review) -> Fix 2 (do-merge gate) -> Tests -> Verify

### Technical Approach

**Fix 1: Remove `status: Complete` from `/do-docs`**

Edit `.claude/skills/do-docs/SKILL.md` lines 345-362. Remove the entire "Plan Status Update" section that runs `sed -i '' 's/^status: .*/status: Complete/'`. The DOCS stage completion is already tracked via `session_progress` (lines 25-27 of the same file). Plan status should only be set to Complete by `/do-merge` after all gates pass.

**Fix 2: Completion gate in `/do-merge`**

Add a plan checkbox scan to `.claude/commands/do-merge.md` between the pipeline state check and the merge execution. The gate:
1. Derives the plan path from the PR branch slug
2. Reads the plan file and counts unchecked `- [ ]` items
3. Excludes items in `## Open Questions` and `## Critique Results` sections (these are not deliverables)
4. Reports unchecked items as blockers if any remain
5. Allows explicit override: if the plan frontmatter contains `allow_unchecked: true`, the gate warns but does not block

Implementation: Add a Python snippet to the prerequisites check section that parses the plan markdown.

**Fix 3: `scripts/validate_build.py`**

A deterministic Python script (no LLM) that validates a build against the plan spec. Following the pattern of `scripts/check_prerequisites.py`.

```
python scripts/validate_build.py docs/plans/{slug}.md
# Exit 0 = all checks pass
# Exit 1 = failures found
```

Three categories of checks:

1. **File path assertions** -- Regex over `- [ ]` lines for file paths. For each:
   - "Create X" or "Add X" -> check file exists in worktree
   - "Delete X" or "Remove X" -> check file does NOT exist
   - "Update X" -> check file was modified in `main..HEAD` diff

2. **Verification table commands** -- Parse the `## Verification` table (already a standardized format). Run each command, compare output to expected value.

3. **Grep-based success criteria** -- Parse `## Success Criteria` for items containing grep/ls/test commands. Run them, check exit codes.

Output format:
```
PASS: config/workspace_config.json deleted
FAIL: data/README.md does not exist (referenced in task build-cleanup)
SKIP: "Bridge starts and connects" -- requires runtime test

Result: 1 PASS, 1 FAIL, 1 SKIP
```

Design constraints:
- Pure Python, no LLM -- deterministic and fast
- Fails open: unparseable items get SKIP, not FAIL
- The plan is the spec; the script validates the build against it

**Fix 4: Enhance `/do-pr-review` plan validation**

Extend the code-review sub-skill (`.claude/skills/do-pr-review/sub-skills/code-review.md`) Step 4 to:
1. Walk each `- [ ]` item in Acceptance Criteria, Test Impact, Documentation, and Update System sections
2. For each unchecked item, assess whether the PR diff addresses it
3. Report unaddressed items with severity: BLOCKER for acceptance criteria, WARNING for others
4. Include the checklist assessment in the review output

This is LLM-based (the reviewer reads the diff and assesses whether items are addressed), complementing the deterministic `validate_build.py`.

**Fix 5: Wire `validate_build.py` into `/do-build`**

Add a step to `.claude/skills/do-build/SKILL.md` between step 16 (verify definition of done) and step 17 (advance to review stage):

```bash
python scripts/validate_build.py $PLAN_PATH
```

If exit code 1: feed the failure report into `/do-patch` or loop back for fixes (up to 3 iterations).
If exit code 0: advance to review stage.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_build.py` handles missing plan file gracefully (exit 0 with message)
- [ ] `validate_build.py` handles plan with no checkboxes (exit 0, nothing to validate)
- [ ] `validate_build.py` handles malformed verification table (SKIP, not crash)
- [ ] Merge gate handles missing plan file (warn, do not block)

### Empty/Invalid Input Handling
- [ ] `validate_build.py` with empty plan file -- should exit 0
- [ ] `validate_build.py` with plan containing only checked items -- should exit 0
- [ ] Merge gate with plan that has `allow_unchecked: true` -- warn but allow

### Error State Rendering
- [ ] `validate_build.py` output is human-readable and machine-parseable
- [ ] Merge gate failure messages clearly list which items are unchecked

## Test Impact

- [ ] `tests/unit/test_validate_build.py` -- CREATE: unit tests for the new `validate_build.py` script (plan parsing, file assertions, verification table parsing)
- [ ] `tests/unit/test_merge_gate.py` -- CREATE: unit tests for the plan checkbox scanning logic extracted from the merge gate

No existing tests affected -- this is additive work modifying skill definitions (markdown files) and creating new scripts. No existing Python code is being changed.

## Rabbit Holes

- Building a full plan-to-code traceability system -- this is about catching obvious drops (unchecked boxes), not building a requirements management tool
- Making `validate_build.py` understand semantic meaning of plan items -- it checks file existence and runs verification commands, not natural language understanding
- Retroactively fixing plans that were already marked Complete incorrectly -- out of scope, this prevents future occurrences
- Adding plan checkbox validation to every skill -- only the four skills identified in the issue need changes

## Risks

### Risk 1: `validate_build.py` produces false positives
**Impact:** Build loops indefinitely because the script fails on items that are actually addressed but not detectable by file/grep checks.
**Mitigation:** Fails open -- unparseable items get SKIP. Only items with clear file path references or verification table entries are checked. Items without detectable assertions are skipped.

### Risk 2: Merge gate blocks legitimate merges
**Impact:** Plans with intentionally deferred items cannot be merged.
**Mitigation:** The `allow_unchecked: true` frontmatter flag and the exclusion of `## Open Questions` / `## Critique Results` sections from the scan.

### Risk 3: `/do-pr-review` changes produce noisy reviews
**Impact:** Every review becomes a wall of plan checkbox status lines.
**Mitigation:** Only report unchecked items that are NOT addressed by the diff. Addressed items are silently passed.

## Race Conditions

No race conditions identified -- all operations are file reads and markdown parsing. No concurrent access patterns.

## No-Gos (Out of Scope)

- Retroactive fixing of plans already marked Complete incorrectly
- Full requirements traceability system beyond checkbox scanning
- Changing the plan document format (the script adapts to existing format)
- Modifying any other SDLC skills beyond the four identified

## Update System

No update system changes required -- this work modifies SDLC skill definitions and adds a validation script. No new dependencies, config files, or migration steps. The changes are internal to the development pipeline.

## Agent Integration

No agent integration required -- the changes are to skill markdown files (which the agent reads as instructions) and a standalone Python script called from within skill workflows. No MCP server changes needed. No bridge changes needed.

## Documentation

- [ ] Create `docs/features/plan-completion-gate.md` describing the completion gate, validate_build.py, and the pipeline changes
- [ ] Update `docs/features/README.md` index table with the new feature entry

## Success Criteria

- [ ] `/do-docs` no longer sets `status: Complete` on plan documents
- [ ] `scripts/validate_build.py` exists and passes its own unit tests
- [ ] `scripts/validate_build.py` correctly parses file path assertions from plan checkboxes
- [ ] `scripts/validate_build.py` correctly runs verification table commands
- [ ] `/do-build` calls `validate_build.py` after build tasks complete
- [ ] `/do-pr-review` walks plan checkboxes and reports unaddressed items
- [ ] `/do-merge` scans for unchecked plan items and blocks merge if any remain
- [ ] `/do-merge` respects `allow_unchecked: true` frontmatter override
- [ ] Tests pass (`pytest tests/ -x -q`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (skills)**
  - Name: skills-builder
  - Role: Modify the four skill markdown files (do-docs, do-build, do-pr-review, do-merge)
  - Agent Type: builder
  - Resume: true

- **Builder (script)**
  - Name: script-builder
  - Role: Create scripts/validate_build.py and its unit tests
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Remove status: Complete from /do-docs
- **Task ID**: build-fix-do-docs
- **Depends On**: none
- **Validates**: `grep -c 'status: Complete' .claude/skills/do-docs/SKILL.md` returns 0
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove the "Plan Status Update" section (lines 345-362) from `.claude/skills/do-docs/SKILL.md`
- The DOCS stage completion is already tracked via `session_progress` -- no replacement needed

### 2. Create scripts/validate_build.py
- **Task ID**: build-validate-script
- **Depends On**: none
- **Validates**: `python scripts/validate_build.py docs/plans/tools_audit_remediation.md; echo $?`
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/validate_build.py` following the spec from issue comment 2
- Parse plan markdown for three assertion categories: file paths, verification table, success criteria
- Output PASS/FAIL/SKIP for each check
- Exit 0 if all pass or skip, exit 1 if any fail
- Handle edge cases: missing file, empty plan, no checkboxes

### 3. Create unit tests for validate_build.py
- **Task ID**: build-validate-tests
- **Depends On**: build-validate-script
- **Validates**: `pytest tests/unit/test_validate_build.py -v`
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_validate_build.py`
- Test plan parsing (file path extraction, verification table parsing, success criteria parsing)
- Test file existence checks (mock filesystem)
- Test edge cases (empty plan, no checkboxes, malformed table)

### 4. Wire validate_build.py into /do-build
- **Task ID**: build-wire-validate
- **Depends On**: build-validate-script
- **Validates**: `grep 'validate_build' .claude/skills/do-build/SKILL.md`
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a validation step between "verify definition of done" (step 16) and "advance to review stage" (step 17)
- Run `python scripts/validate_build.py $PLAN_PATH` in the worktree
- If exit 1: report failures for patching, do not advance to review
- If exit 0: proceed to advance to review stage

### 5. Enhance /do-pr-review plan validation
- **Task ID**: build-enhance-review
- **Depends On**: none
- **Validates**: `grep -c 'unchecked' .claude/skills/do-pr-review/sub-skills/code-review.md` returns > 0
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend Step 4 in `.claude/skills/do-pr-review/sub-skills/code-review.md`
- Walk each `- [ ]` item in key plan sections (Acceptance Criteria, Test Impact, Documentation, Update System, Success Criteria)
- For each unchecked item, assess whether the PR diff addresses it
- Report unaddressed Acceptance Criteria / Success Criteria items as BLOCKERs
- Report unaddressed Test Impact / Documentation / Update System items as WARNINGs

### 6. Add completion gate to /do-merge
- **Task ID**: build-merge-gate
- **Depends On**: none
- **Validates**: `grep 'unchecked' .claude/commands/do-merge.md`
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Add plan checkbox scan between pipeline state check and merge execution in `.claude/commands/do-merge.md`
- Derive plan path from PR branch slug
- Count unchecked `- [ ]` items, excluding `## Open Questions` and `## Critique Results` sections
- Block merge if unchecked items remain (unless `allow_unchecked: true` in frontmatter)
- Report specific unchecked items in the failure message

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-fix-do-docs, build-validate-script, build-validate-tests, build-wire-validate, build-enhance-review, build-merge-gate
- **Assigned To**: skills-builder
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` to verify no regressions
- Run `python -m ruff check .` for lint
- Verify `/do-docs` no longer contains `status: Complete`
- Verify `scripts/validate_build.py` exists and runs
- Verify all four skill files have the expected changes

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| do-docs no Complete | `grep -c 'status: Complete' .claude/skills/do-docs/SKILL.md` | output 0 |
| validate_build exists | `test -f scripts/validate_build.py` | exit code 0 |
| validate_build runs | `python scripts/validate_build.py --help` | exit code 0 |
| do-build wired | `grep -q 'validate_build' .claude/skills/do-build/SKILL.md` | exit code 0 |
| do-merge gate | `grep -q 'unchecked' .claude/commands/do-merge.md` | exit code 0 |
| review enhanced | `grep -q 'unchecked' .claude/skills/do-pr-review/sub-skills/code-review.md` | exit code 0 |
| unit tests pass | `pytest tests/unit/test_validate_build.py -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- the issue and its two comments provide sufficient specification for all five fixes.
