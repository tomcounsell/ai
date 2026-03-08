---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-08
tracking: https://github.com/tomcounsell/ai/issues/312
---

# Features README Alphabetical Sort Check

## Problem

`docs/features/README.md` has the instruction "Keep entries sorted alphabetically by feature name" but entries are routinely added out of order by both humans and agents.

**Current behavior:**
- New entries get added at the end or in random positions
- PR reviews flag alphabetical ordering as a nit (e.g., PR #311)
- Multiple PRs touching README.md create merge conflicts due to inconsistent ordering
- Manual re-sorting is error-prone and wastes review cycles

**Desired outcome:**
- A pre-commit hook blocks commits that add entries out of alphabetical order
- A script auto-sorts the table so developers can fix violations with one command
- Zero future review nits or merge conflicts caused by table ordering

## Prior Art

- **PR #311**: "Add deep architectural analysis to plan skill" -- review flagged "Deep Plan Analysis" placed after "do-patch Skill" instead of alphabetically. Merged 2026-03-08.
- No prior issues or PRs specifically addressed automating alphabetical enforcement.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Sort validator script**: Python script that parses the markdown table, extracts feature names from link text, and verifies alphabetical order (case-insensitive)
- **Auto-sort script**: Companion script (or flag on the same script) that re-sorts the table in place
- **Claude Code hook**: PostToolUse hook on Write/Edit that runs the validator when `docs/features/README.md` is modified

### Flow

**Developer adds feature entry** -> Write/Edit `docs/features/README.md` -> PostToolUse hook fires validator -> If out of order: error message with fix command -> Developer runs auto-sort -> Re-commit succeeds

### Technical Approach

- Single Python script at `.claude/hooks/validators/validate_features_readme_sort.py` with two modes:
  1. `--check` (default): exit 0 if sorted, exit 2 if not (with helpful error showing which entries are out of order)
  2. `--fix`: re-sort the table in place and exit 0
- Parse markdown table between `## Features` and `## Adding New Entries` headers
- Extract feature name from `[Feature Name](filename.md)` link syntax in each row
- Case-insensitive comparison using Python's `str.lower()`
- Hook registered in `.claude/settings.json` under PostToolUse for Write and Edit matchers, only firing when the file path contains `docs/features/README.md`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers exist in the scope of this work (new script)
- [ ] Script should exit cleanly with informative error if README.md is malformed (no table found)

### Empty/Invalid Input Handling
- [ ] Handle empty table (0 rows) -- pass validation
- [ ] Handle table with 1 row -- pass validation
- [ ] Handle rows without link syntax -- skip those rows or warn
- [ ] Handle missing `## Features` header -- pass through (nothing to validate)

### Error State Rendering
- [ ] Error output clearly shows which entries are out of order and their expected position
- [ ] Error output includes the fix command to run

## Rabbit Holes

- Parsing full markdown AST -- regex on the table rows is sufficient
- Supporting multiple tables -- only the Features table matters
- CI/CD integration -- Claude Code hooks are sufficient for this repo's workflow
- Sorting by anything other than feature name (e.g., status, description) -- not needed

## Risks

### Risk 1: Table format changes break parser
**Impact:** Validator silently passes or crashes on new table format
**Mitigation:** If `## Features` header exists but no table rows found, emit a warning instead of passing silently

## Race Conditions

No race conditions identified -- this is a synchronous, single-file validation script with no shared state.

## No-Gos (Out of Scope)

- CI/CD pipeline integration (GitHub Actions) -- hooks are sufficient
- Validating other markdown tables in the repo
- Enforcing sort order in plan docs or other index files (could be a follow-up)
- Auto-sorting on git pre-commit (Claude Code hooks handle this)

## Update System

No update system changes required -- this is a development-time hook that only runs in the Claude Code environment. The validator script is committed to the repo and will propagate via normal git pull.

## Agent Integration

No agent integration required -- this is a Claude Code hook that fires automatically during Write/Edit operations. No MCP server exposure needed. The hook is registered in `.claude/settings.json` which is already part of the repo.

## Documentation

- [ ] Create `docs/features/features-readme-sort-check.md` describing the hook and auto-sort script
- [ ] Add entry to `docs/features/README.md` index table (and ensure it's alphabetically sorted)

## Success Criteria

- [ ] Validator script correctly identifies out-of-order entries in `docs/features/README.md`
- [ ] Validator script exits 0 when entries are alphabetically sorted
- [ ] Auto-sort mode (`--fix`) re-sorts the table in place
- [ ] Claude Code hook fires on Write/Edit to `docs/features/README.md`
- [ ] Current `docs/features/README.md` passes validation (may need initial sort)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (validator)**
  - Name: sort-validator-builder
  - Role: Implement the sort validation script and hook registration
  - Agent Type: builder
  - Resume: true

- **Validator (validator)**
  - Name: sort-validator-checker
  - Role: Verify the hook fires correctly and catches out-of-order entries
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build sort validator script
- **Task ID**: build-validator
- **Depends On**: none
- **Assigned To**: sort-validator-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/validators/validate_features_readme_sort.py` with `--check` and `--fix` modes
- Parse the markdown table between `## Features` and `## Adding New Entries`
- Extract feature names from `[Name](file.md)` link syntax
- Case-insensitive alphabetical comparison
- Helpful error messages showing which entries are out of order

### 2. Register Claude Code hook
- **Task ID**: register-hook
- **Depends On**: build-validator
- **Assigned To**: sort-validator-builder
- **Agent Type**: builder
- **Parallel**: false
- Add PostToolUse hook in `.claude/settings.json` for Write and Edit matchers
- Hook should invoke the validator script on `docs/features/README.md`

### 3. Fix current README ordering
- **Task ID**: fix-current-order
- **Depends On**: build-validator
- **Assigned To**: sort-validator-builder
- **Agent Type**: builder
- **Parallel**: false
- Run the auto-sort on the current `docs/features/README.md` to fix existing ordering issues

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-validator
- **Assigned To**: sort-validator-builder
- **Agent Type**: builder
- **Parallel**: false
- Test sorted table passes validation
- Test unsorted table fails validation with correct error
- Test `--fix` mode sorts correctly
- Test edge cases: empty table, single row, missing header

### 5. Validate everything works
- **Task ID**: validate-all
- **Depends On**: register-hook, fix-current-order, build-tests
- **Assigned To**: sort-validator-checker
- **Agent Type**: validator
- **Parallel**: false
- Run validator against current README -- must pass
- Manually disorder an entry and verify validator catches it
- Run test suite
- Verify hook registration in settings.json

## Validation Commands

- `python .claude/hooks/validators/validate_features_readme_sort.py --check docs/features/README.md` - Validates sort order
- `python .claude/hooks/validators/validate_features_readme_sort.py --fix docs/features/README.md` - Auto-sorts table
- `pytest tests/test_features_readme_sort.py -v` - Runs unit tests
- `python -m ruff check .claude/hooks/validators/validate_features_readme_sort.py` - Lint check
