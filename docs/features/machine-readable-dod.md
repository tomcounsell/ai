# Machine-Readable Definition of Done

## Overview

Plan documents now include a structured `## Verification` section with a markdown table of executable checks. This replaces the free-form `## Validation Commands` section with a machine-parseable format that `/do-build` and `/do-pr-review` execute automatically.

## The Problem

Previously, validation commands in plan documents were embedded in prose:

```markdown
## Validation Commands
- `pytest tests/ -x -q` - Tests pass
- `python -m ruff check .` - Lint clean
```

These were human-readable but not machine-verifiable. `/do-build` had to rely on LLM judgment to determine whether criteria were met, leading to subjective completion, silent skipping of hard-to-check criteria, and no automated verification.

## The Solution

### Verification Table Format

Plans now use a structured table:

```markdown
## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Module importable | `python -c "from agent.foo import Bar"` | exit code 0 |
| Feature doc exists | `test -f docs/features/foo.md` | exit code 0 |
| PR opened | `gh pr list --head session/foo --json number --jq length` | output > 0 |
```

Each row defines:
- **Check**: Human-readable name for the verification
- **Command**: Executable shell command (in backticks)
- **Expected**: Machine-parseable expectation

### Supported Expectations

| Format | Meaning | Example |
|--------|---------|---------|
| `exit code N` | Command must exit with code N | `exit code 0` |
| `output > N` | Command output (as integer) must be greater than N | `output > 0` |
| `output contains X` | Command stdout must contain substring X | `output contains ok` |

## Components

### Verification Parser (`agent/verification_parser.py`)

Pure-function module with no external dependencies beyond subprocess:

- `VerificationCheck(name, command, expected)` -- dataclass for a single check
- `CheckResult(check, passed, exit_code, output, error)` -- result of running a check
- `parse_verification_table(markdown)` -- extracts checks from a `## Verification` section
- `evaluate_expectation(expected, exit_code, output)` -- determines pass/fail
- `run_checks(checks, cwd, timeout)` -- executes all checks via subprocess
- `format_results(results)` -- produces a human-readable report

### Hook Validator (`.claude/hooks/validators/validate_verification_section.py`)

Enforces that new plan documents include a `## Verification` section with at least one table row. Follows the same pattern as `validate_documentation_section.py`:

- Auto-detects new plan files via `git status`
- Validates the section exists and has a proper table with data rows
- Exit 0 on pass, exit 2 on failure (blocks agent)

### Build Integration (`/do-build` Step 5.1)

After all build tasks complete, `/do-build` automatically:
1. Reads the plan document
2. Parses the `## Verification` table
3. Runs each check in the worktree
4. Reports structured pass/fail results
5. Triggers `/do-patch` if any check fails

### Review Integration (`/do-pr-review` Step 4.5)

During PR review, the reviewer:
1. Runs all verification checks on the PR branch
2. Includes a "Verification Results" section in the review comment
3. Classifies failed checks as blockers

## Backward Compatibility

Existing plans with `## Validation Commands` sections continue to work. The new `## Verification` table is only required for new plans (the hook validator only triggers on new/modified plan files detected via `git status`).

The plan template has been updated to use the new format, so all plans created via `/do-plan` going forward will use the structured table.

## Related

- [Goal Gates](goal-gates.md) -- deterministic stage enforcement (complementary)
- [Build Output Verification](build-output-verification.md) -- existing build verification gates
- [Documentation Lifecycle](documentation-lifecycle.md) -- similar hook validation pattern
- GitHub Issue: #330
