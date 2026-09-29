---
name: do-test
description: "Run the project's test suite and aggregate results. Triggered by 'run tests', 'test this', or any request about testing."
argument-hint: "[test-path-or-filter]"
effort: medium
---

# Do Test

Run the requested tests plus lint, classify any failures with evidence, and end
with a machine-readable verdict the pipeline can route on. This skill runs tests;
it never edits source or test files. The workflow is language-agnostic: Python
and pytest are the worked example (`PYTHON.md`); on other stacks use the
project's runner (`cargo test`, `npm test`, `go test ./...`).

**Done when:** every requested suite has run (or is named as skipped with the
reason), failures are classified per `baseline-verification.md`, the swallow gate
has run on a green result, and the final line is the OUTCOME contract. In a
pipeline run a text-only turn without the OUTCOME line is a progress note, not
the end.

## Repo context

If `docs/sdlc/do-test.md` exists, read it and honor it: it declares the runner,
tiers, lint commands, happy-path runner, source-to-test mappings, gitignored
files the baseline run needs, and the OUTCOME parser. Without it, use the
conventional runner against conventional `tests/` directories. Also read any
other test skill docs the project ships
(`ls .claude/skills/*test*/*.md .claude/skills-global/*test*/*.md`); they may add
runners or targets.

## Sub-files (load on demand)

| Sub-file | Load when |
|---|---|
| `PYTHON.md` | Python project: commands, lint defaults, changed-file mapping, exit codes |
| `parallel-dispatch.md` | All-tests run with 50+ test files and no `--direct` |
| `baseline-verification.md` | Any test failed on a feature branch |
| `quality-gates.md` | Tests passed, before the OUTCOME line |
| `special-targets.md` | Target is `frontend` or `happy-paths` |

## Arguments

`TEST_ARGS`: $ARGUMENTS (if empty or literally `$ARGUMENTS`, take whatever
followed `/do-test` in the user's message).

| Input | Runs |
|---|---|
| _(empty)_ | all test directories |
| `unit` / `integration` / `e2e` / `tools` / `performance` | `tests/<tier>/` |
| a file or directory path | that path |
| `--changed` | tests mapped from files changed against `main` (`HEAD~1` when on `main`); a changed test file maps to itself; keep only files that exist; if none map, say so and run lint only |
| `--no-lint` | skip lint/format checks (otherwise they always run) |
| `--direct` | never dispatch subagents |
| `frontend <url> "<scenario>"`, `happy-paths` | see `special-targets.md`; neither runs the unit-test runner |

Flags combine with any target. If `--changed` git commands fail, run everything.

## Execution

Run a single target, or fewer than 50 test files, directly in this context with
one command. Otherwise use `parallel-dispatch.md`. Run in the current working
directory as-is; the caller has already placed you in the right checkout. Skip a
missing test directory silently; report a missing runner as an error.

If the plan describes cross-component wiring (tool A feeds component B), note
whether any test exercises the full chain.

## Report

A table per suite (Status, Passed, Failed, Skipped, Duration), lint rows, and the
failure tracebacks, with the raw runner output kept visible. All pass: `ALL
TESTS PASSED`, then `quality-gates.md`. Any failure: `baseline-verification.md`
decides the verdict; only regressions block.

## OUTCOME contract

The last line of the final response, parsed by the pipeline before any text
matching:

- Success: `<!-- OUTCOME {"status":"success","stage":"TEST","artifacts":{"passed":<N>,"failed":0}} -->`
- Fail: `<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"passed":<N>,"failed":<N>}} -->`
- Partial (flaky or pre-existing only): `<!-- OUTCOME {"status":"partial","stage":"TEST","artifacts":{"passed":<N>,"failed":0,"flaky":<N>}} -->`

`baseline-verification.md` adds the `blocked` form and the circuit-breaker
artifacts; `quality-gates.md` adds the swallow-gate failure form. Scratch files
go in `/tmp`, never the repo.
