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

`evaluate_expectation` (`agent/verification_parser.py`) is the sole grader.
It is three-valued: every row grades `PASS`, `FAIL`, or `UNEVALUATED` (see
"Three-Valued Grading" below), never a plain boolean.

**Positive expectations** (the command must succeed or produce the expected output):

| Format | Meaning | Example |
|--------|---------|---------|
| `exit code N` | Command must exit with code N (positive exact-match, prefix-matched) | `exit code 0` |
| `exit N` | Same as `exit code N`, shorter spelling (anchored) | `exit 0` |
| `output contains X` | Command stdout must contain substring X | `output contains ok` |
| `output > N` / `> N` | Stdout (stripped, parsed as an integer) must be greater than N | `output > 0`, `> 0` |
| `>= N` / `output >= N` | Stdout must be numeric and `>= N` | `>= 1` |
| `== N` / `output == N` | Stdout must be numeric and exactly N | `== 3` |
| `prints \`N\`` | Stripped stdout must equal N exactly (backticks optional) | ``prints `ok` `` |
| `empty output` | Stdout must be empty or whitespace-only | `empty output` |

**Inverse expectations / anti-criteria** (the command must NOT produce a forbidden result):

| Format | Meaning | Example |
|--------|---------|---------|
| `exit code != N` | Command must NOT exit with code N (passes when `exit_code != N`) | `exit code != 0` |
| `output does not contain X` | Stdout must NOT contain X, AND stdout must be non-empty | `output does not contain DROP TABLE` |
| `match count == 0` | Every non-blank stdout line must be "0" or end with ":0" (grep shapes), AND stdout must be non-empty | `match count == 0` |

**Important distinction:** `exit code N` is a positive exact-match — it passes when `exit_code == N`. `exit code != N` is the inverse — it passes when `exit_code != N`. The two are syntactically disjoint and unambiguous. The existing `exit code 1` check ("No stale xfails") is a positive exact-match: grep exits 1 when it finds no matches, so `exit code 1` asserts "no stale xfails found". It is NOT an inverse.

The inverse forms are matched before their positive counterparts (`exit code != N` before `exit code N`, `output does not contain X` before `output contains X`), so an inverse row can never be misread as a positive one.

A command containing a `|` needs the escape rule in "Authoring Rule: Pipes Must Be Escaped" below — and the escape composes differently for `grep -E` than for basic-regex `grep -c`, which is the single most common way an otherwise-correct row silently stops asserting what it says. `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` carries the same rule and a worked alternation sample inline, so a plan author meets it while writing the row rather than by following a link.

**Empty-stdout gate:** Both `output does not contain X` and `match count == 0` reject truly-empty stdout. An errored command or one that writes only to stderr produces empty stdout; without the gate, a trivially-absent substring or `all(...)` over an empty list would silently pass. A legitimately-clean `grep -c` returns a literal `0` (one byte of non-empty stdout), so the gate fires only when the command produced no output at all.

**Prefix-matched vs. anchored.** The three pre-existing forms — `exit code N`, `output > N`, `output contains X` — stay prefix-matched: a trailing gloss (`exit code 0 (verified 2026-09-02 to return exactly one EnvCall today)`) is an established authoring idiom in this repo's live plans, and anchoring them would have turned already-working rows into blocking `UNEVALUATED` for a change nobody asked for. Every form added afterward (`exit N`, `` prints `N` ``, `>= N`, `> N`, `== N`, `empty output`) is **anchored**: the cell must match the pattern to its end. A bare comparator followed by prose is easy to write by accident, and grading a sentence as if it were a number is exactly the guess this module exists to stop making — an anchored form that fails to match reports `UNEVALUATED` naming the cell, telling the author what to fix rather than silently misgrading it.

Non-numeric stdout under a numeric comparator (`> N`, `>= N`, `== N`) is a genuine `FAIL`, not `UNEVALUATED`: the expectation was understood, the command just answered with something that is not a number.

### Three-Valued Grading: `UNEVALUATED`

A check never grades a plain pass/fail boolean. `evaluate_expectation` and
`run_checks` return one of `CheckOutcome.PASS`, `CheckOutcome.FAIL`, or
`CheckOutcome.UNEVALUATED`. `UNEVALUATED` means *the grader could not answer
the question* — it is produced by:

- an expectation cell that is empty, whitespace-only, or `None`
- an expectation form the grammar above does not recognise
- a command cell carrying no backticked span
- a command timeout (`DEFAULT_TIMEOUT_S`, currently 120s — provisional and
  tunable; the one bound shared by both runners of these tables, #2901)
- any runner exception

`UNEVALUATED` is **blocking** — it never counts as a pass — but it is
reported as its own token, never folded into `[FAIL]`. The distinction
matters for whoever reads the report: a `FAIL` says the code under test is
wrong; an `UNEVALUATED` says the grader itself could not answer, which is a
claim about the check's authoring (a malformed expectation, a missing
backtick span, a suite that took too long), not about the code. The 2026-09
supervisor batch hand-verified every `UNEVALUATED` "failure" it encountered
as code that was actually correct — the row's expectation was unreadable,
not the code under test.

The distinction changes what a human does next, but not whether the gate
stops:

- **Build gate** (`/do-build` Step 5.1, `scripts/validate_build.py`):
  `UNEVALUATED` blocks exactly like `FAIL` — the script's exit code is `1` if
  either count is non-zero — but the printed report line reads
  `UNEVALUATED: <reason>` rather than `FAIL: ...`, so the triaging agent knows
  to fix the *row* (rewrite the expectation, add the missing backtick span)
  rather than debug the code.
- **Merge/review gate** (`/do-pr-review` Step 4.5): the reviewer runs the
  same table and reports `UNEVALUATED` rows as their own category in the
  review, never as a code-quality finding — a blocker that reads "grader
  could not answer" is a different fix than a blocker that reads "code is
  wrong," and conflating them sends the wrong person down the wrong path.

A run with zero checks at all (an empty `## Verification` table, or a section
that produced nothing executable) grades `UNEVALUATED` for the run, never a
vacuous `PASS` — there being nothing to check is not evidence that
everything checked out.

### Table Scoping: One `## Verification` Section, Many Pipe-Blocks (#2836)

A `## Verification` section can carry more than one markdown table -- a check
table plus a red/green summary, or a second check table headed
`Anti-criterion | Command | Expected`. The parser scopes to **pipe-blocks**:
contiguous runs of `|`-prefixed lines, which is the GitHub Flavored Markdown
definition of one table (a blank line, or a line that cannot be part of the
table, ends it). Every pipe-block in the section is classified independently:

- **Check table** -- at least three columns, one of the first three column
  names is exactly `Command` (case-insensitive). Every data row contributes a
  check, parsed exactly as a single-table section always was. A second check
  table (e.g. an `Anti-criterion` table) is not special-cased away; both
  contribute.
- **Non-check table** -- a summary, a findings recap, anything without a
  `Command` column. Reported as a `SkippedTable(header, row_count, reason)`:
  named in both runners' output, **non-failing**. Before this, a second table's
  header and separator row parsed as guaranteed-fail checks, so a clean build
  could fail on rows the plan's author never intended as checks.
- **Rows present, no check table anywhere in the section** -- a loud failure:
  exactly one `MalformedRow` per pipe-block (never one per row). A section
  whose author wrote table rows and produced zero executable checks is told so,
  rather than silently gating on nothing. `skipped` stays empty in this branch
  -- a block is either skipped or malformed, never both.

`ParsedTable` therefore carries three fields: `checks`, `malformed`, and
`skipped`.

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
  | No raw Redis deletes | grep -c "r\.delete\\|r\.srem" agent/verification_parser.py | match count == 0 |
```

### Authoring Rule: Pipes Must Be Escaped

A `|` is the table's own column separator, so a command containing one has to
be escaped as `\|` — standard GitHub-flavored Markdown. The parser unescapes it
after splitting, so `\|` reaches the shell as a literal pipe. A bare `|` is
rejected as a plan-authoring error rather than executed truncated (#2570).

The escape composes, which is the part worth reading twice:

| In the table cell | Reaches the shell as | Under `grep -E` | Under `grep -c` (BRE) |
|---|---|---|---|
| `a\|b` | `a\|b` | alternation | literal `a\|b` |
| `a\\\|b` | `a\\|b` | literal `a\\|b` | alternation |
| `a\|b` (unescaped) | rejected as malformed | — | — |

So an anti-criterion using basic-regex `grep -c` alternation — the shape most
anti-criteria take — writes a **doubled** backslash in the table. The worked
example below does exactly that.

Before this rule existed, both the escaped and unescaped forms truncated at the
pipe, and the `Expected` cell silently received the fragment after it. A scan of
`docs/plans/` found 544 rows carrying that signature. The checks failed rather
than false-passed, but they failed for a parsing reason while looking like a
finding about the code, which is why several reached `completed/` with the
broken row still in place. Anti-criteria were the most affected class, because
they are the ones that need alternation.

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
| No raw Redis deletes | `grep -c "r\.delete\\|r\.srem" agent/verification_parser.py` | match count == 0 |
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

Pure-function module with no external dependencies beyond subprocess. This is
the **sole** definition of what a `## Verification` table is and what an
expectation means (#2836, #2843) -- `scripts/validate_build.py` imports from
here and carries no table parser or evaluator of its own:

- `VerificationCheck(name, command, expected)` -- dataclass for a single check
- `MalformedRow(line, reason)` -- a row, or a whole non-check pipe-block, that
  could not be read as the author wrote it
- `SkippedTable(header, row_count, reason)` -- a non-check pipe-block, named
  and reported but non-failing
- `CheckResult(check, passed, exit_code, output, error)` -- result of running a check
- `ParsedTable(checks, malformed, skipped)` -- everything a section yielded
- `parse_verification_table(markdown)` -- extracts a `ParsedTable` from a `## Verification` section, scoped per pipe-block (see "Table Scoping" above)
- `evaluate_expectation(expected, exit_code, output)` -- determines pass/fail
- `run_checks(checks, cwd, timeout)` -- executes all checks via subprocess
- `format_results(results, table)` -- produces a human-readable report; `table` is required (both parameters are), and reads `table.malformed` and `table.skipped` for their own report sections

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
