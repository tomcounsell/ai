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

**Positive expectations** (the command must succeed or produce the expected output):

| Format | Meaning | Example |
|--------|---------|---------|
| `exit code N` | Command must exit with code N (positive exact-match) | `exit code 0` |
| `output > N` | Command output (as integer) must be greater than N | `output > 0` |
| `output contains X` | Command stdout must contain substring X | `output contains ok` |

**Inverse expectations / anti-criteria** (the command must NOT produce a forbidden result):

| Format | Meaning | Example |
|--------|---------|---------|
| `exit code != N` | Command must NOT exit with code N (passes when `exit_code != N`) | `exit code != 0` |
| `output does not contain X` | Stdout must NOT contain X, AND stdout must be non-empty | `output does not contain DROP TABLE` |
| `match count == 0` | Every non-blank stdout line must be "0" or end with ":0" (grep shapes), AND stdout must be non-empty | `match count == 0` |

**Important distinction:** `exit code N` is a positive exact-match — it passes when `exit_code == N`. `exit code != N` is the inverse — it passes when `exit_code != N`. The two are syntactically disjoint and unambiguous. The existing `exit code 1` check ("No stale xfails") is a positive exact-match: grep exits 1 when it finds no matches, so `exit code 1` asserts "no stale xfails found". It is NOT an inverse.

**Empty-stdout gate:** Both `output does not contain X` and `match count == 0` reject truly-empty stdout. An errored command or one that writes only to stderr produces empty stdout; without the gate, a trivially-absent substring or `all(...)` over an empty list would silently pass. A legitimately-clean `grep -c` returns a literal `0` (one byte of non-empty stdout), so the gate fires only when the command produced no output at all.

## Anti-Criteria: Verifying No-Gos

### Concept

No-Gos (from the `## No-Gos` plan section) declare what a plan explicitly excludes. Most No-Gos are advisory — they describe human/world actions (`[EXTERNAL]`, `[ORDERED]`) that cannot be mechanically checked. But some No-Gos describe a *forbidden code-level outcome*: a pattern that must NOT appear in the diff, a file that must NOT be modified, a symbol that must NOT be called.

These assertable No-Gos (typically `[DESTRUCTIVE]` and `[SEPARATE-SLUG]` tagged entries) can become **anti-criteria** — inverse rows in the `## Verification` table that assert the forbidden outcome is absent. Anti-criteria are:

- **Opt-in per No-Go**: only add an inverse row when you can write a command that mechanically detects the violation.
- **Not required for advisory No-Gos**: `[EXTERNAL]` and `[ORDERED]` No-Gos describe human/world actions, not checkable code outcomes.
- **Derived from, not replacing, No-Gos**: the `## No-Gos` section remains the human-readable declaration; the `## Verification` table holds the executable assertion. No second `## Anti-Criteria` section is introduced.

### Relationship to No-Gos

```
## No-Gos (human-readable declaration)
  [DESTRUCTIVE] Do not call r.delete() or r.srem() on Popoto-managed keys
       |
       | (opt-in derivation — only for assertable No-Gos)
       v
## Verification (machine-executable assertion)
  | No raw Redis deletes | grep -c "r\.delete\|r\.srem" agent/verification_parser.py | match count == 0 |
```

### Authoring Rule: Red-State Proof (Posture: Paper-Trail PR Checklist)

When you add an inverse Verification row, the build-time green pass is the **binding gate** (if the anti-criterion fails at `do-build` Step 5.1, the build fails). But a green pass alone does not prove the row actually detects violations — the pattern could be wrong, or the wrong file could be checked.

Before trusting an anti-criterion, demonstrate it FAILS against a deliberately-violating input:

1. Temporarily introduce the forbidden pattern (e.g., add a `r.delete(key)` call to a file covered by the grep).
2. Run the command manually and confirm it reports FAIL (non-zero count or non-zero exit).
3. Revert the temporary change.
4. **Paste the FAIL output into the PR description** as a paper trail.

The `do-pr-review` checklist confirms this paste is present. The paste is **non-binding evidence** — the live green Step 5.1 run is the enforcement mechanism, not the pasted blob.

### Worked Example: No Raw Redis Deletes Anti-Criterion

This project has a `[DESTRUCTIVE]` No-Go: "never use raw Redis on Popoto-managed keys". Here is how to convert it into a `match count == 0` anti-criterion.

**The Verification row:**

```markdown
| No raw Redis deletes | `grep -c "r\.delete\|r\.srem" agent/verification_parser.py` | match count == 0 |
```

**Green-state run (clean code — no violations):**

```
$ grep -c "r\.delete\|r\.srem" agent/verification_parser.py
0
```

Exit code: 1 (grep exits 1 when pattern is absent). Stdout: `0` (literal zero byte, non-empty).
`match count == 0` evaluation: stdout is non-empty, line `"0"` matches the bare-zero case. **PASS**.

**Red-state run (deliberately-violating input — for authoring proof only):**

Temporarily add `r.delete(key)` to `agent/verification_parser.py`, then run:

```
$ grep -c "r\.delete\|r\.srem" agent/verification_parser.py
1
```

Exit code: 0 (grep exits 0 when pattern is found). Stdout: `1`.
`match count == 0` evaluation: line `"1"` is neither `"0"` nor `:0`-suffixed. **FAIL**.

This FAIL output (`1`) is pasted into the PR description as the red-state proof. Revert the temporary change before committing.

**Multi-file variant (grep -rc on a directory):**

```
$ grep -rc "r\.delete\|r\.srem" agent/
agent/verification_parser.py:0
agent/output_handler.py:0
```

Exit code: 1. Stdout: two `path:0` lines (multi-line, all `:0`-suffixed). **PASS**.

All four canonical `grep` shapes (bare `0`, whitespace `0`, `path:0`, multi-line `path:0`) pass the `match count == 0` matcher. Truly-empty stdout (errored command) fails via the empty-stdout gate.

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
