# Plan Completion Gate

Prevents the SDLC pipeline from marking a plan "Complete" or merging a PR while plan checkboxes remain unchecked. Addresses silent requirement drops where `/do-docs` would set `status: Complete` regardless of unfinished work.

## Problem

Before this feature, the pipeline could mark a plan Complete and merge a PR with unchecked plan items. The `/do-docs` skill unconditionally set `status: Complete` after touching docs, and no other skill verified that plan checkboxes were actually addressed. Requirements silently dropped from the pipeline.

## Components

### 1. `/do-docs` no longer sets plan status

The "Plan Status Update" section was removed from `.claude/skills/do-docs/SKILL.md`. Previously, this section ran `sed` to change the plan frontmatter to `status: Complete` after any documentation commit. The DOCS stage completion is already tracked via `session_progress` in the pipeline state machine, so this status write was redundant and premature.

### 2. `scripts/validate_build.py` -- deterministic build validator

A standalone Python script (no LLM) that validates a build against its plan specification. Three categories of checks:

- **File path assertions** -- Scans checkbox lines for Create/Add/Delete/Remove/Update patterns with backtick-quoted paths. Checks file existence or modification in `main..HEAD` diff.
- **Verification table commands** -- Parses the `## Verification` markdown table, runs each command, and compares output or exit code to the expected value.
- **Success criteria commands** -- Parses `## Success Criteria` checkboxes for backtick-quoted runnable commands (python, pytest, grep, test, ls, cat, ruff) and checks exit codes.

Usage:
```bash
python scripts/validate_build.py docs/plans/my-feature.md
```

Output format:
```
PASS: scripts/validate_build.py exists
FAIL: data/README.md does not exist (expected by: Create `data/README.md`)
SKIP: some-check -- timed out after 30s

Result: 1 PASS, 1 FAIL, 1 SKIP
```

Design principles:
- Exit 0 if all checks pass or skip, exit 1 if any fail
- Fails open: unparseable items get SKIP, not FAIL
- Missing or empty plan file returns exit 0 with a message
- Pure Python with no external dependencies

### 3. `/do-build` integration

Step 16b in `.claude/skills/do-build/SKILL.md` runs `validate_build.py` after the definition-of-done check. If validation fails (exit 1), the failure report feeds into `/do-patch` for fixes, with up to 3 retry iterations before advancing to review.

### 4. `/do-pr-review` plan checkbox validation

Step 4b in `.claude/skills/do-pr-review/sub-skills/code-review.md` walks each unchecked `- [ ]` item in key plan sections and assesses whether the PR diff addresses it:

- **Acceptance Criteria / Success Criteria** -- reported as BLOCKER if unaddressed
- **Test Impact / Documentation / Update System** -- reported as WARNING if unaddressed

Items addressed by the diff are silently passed. Only genuinely unaddressed items are reported, minimizing false positives.

### 5. `/do-merge` completion gate

A plan checkbox scan in `.claude/commands/do-merge.md` runs between the pipeline state check and the merge execution. The gate:

1. Derives the plan path from the PR branch slug
2. Reads the plan file and counts unchecked `- [ ]` items
3. Excludes items in `## Open Questions` and `## Critique Results` sections (these are not deliverables)
4. Blocks merge if unchecked items remain
5. Respects `allow_unchecked: true` in plan frontmatter as an explicit override (warns but does not block)
6. Gracefully handles missing plan files (warns, does not block)

## Files

| File | Role |
|------|------|
| `scripts/validate_build.py` | Deterministic plan-to-build validator |
| `tests/unit/test_validate_build.py` | Unit tests for the validator |
| `.claude/skills/do-docs/SKILL.md` | Removed plan status update section |
| `.claude/skills/do-build/SKILL.md` | Added validate_build.py step (16b) |
| `.claude/skills/do-pr-review/sub-skills/code-review.md` | Added checkbox validation (4b) |
| `.claude/commands/do-merge.md` | Added completion gate with checkbox scan |

## How It Works End-to-End

1. Developer creates a plan with checkboxes across sections (Acceptance Criteria, Test Impact, Documentation, etc.)
2. `/do-build` implements the plan and runs `validate_build.py` to verify file assertions and verification commands pass
3. `/do-pr-review` cross-references unchecked plan items against the PR diff and flags unaddressed items
4. `/do-docs` updates documentation but does NOT mark the plan Complete
5. `/do-merge` scans for any remaining unchecked items and blocks the merge if requirements are still open
6. Only after all checkboxes are addressed (or `allow_unchecked: true` is set) can the PR be merged
