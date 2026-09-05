---
status: Planning
type: chore
tracking: https://github.com/tomcounsell/ai/issues/3178
appetite: Small
---

# Move mechanical plan checks out of critique rounds

## Problem

Critic subagents spend LLM budget re-deriving facts a shell command could establish. In the one run measured (#2733 / PR #3174), round 3 produced 11 findings of which 6 were mechanically checkable — a verification command that does not behave as the plan claims, a `file:line` citation asserting something false, a declared appetite contradicting the plan's own counts, a disposition table missing an entry. Only 5 needed judgment.

Two of those reproduce directly:

- Six Verification rows phrased "match count == 0" were satisfied by a bare `grep -c`, which prints `0` and **exits 1**. Re-verified here: `grep -c nonexistent_token README.md` → prints `0`, exit `1`. A validator gating on exit status reads six correct results as failures.
- The plan asserted a call site was "not inside a `try`". False: `draft_message` is called at `agent/output_handler.py:754` inside a `try` opened at `:751` with `except Exception` at `:885`.

Neither needs a model. Both cost a critic round.

## Appetite

**Small.** One new module, one test file, one wiring point, one doc. The coding is a focused session. Parts B and C below are explicitly *not* in this appetite.

## Solution

A `plan-lint` pass that runs before the first critic is dispatched and reports mechanical defects in a plan document, so critics receive a plan whose checkable claims are already true.

Three checks, in priority order:

1. **Verification-table execution.** Parse the `## Verification` table, execute each command against the working tree, and report claimed vs. actual exit code and stdout.
2. **Citation resolution.** Extract `file:line` references from the plan, confirm each resolves, and surface the cited line so a drifted or false citation is visible.
3. **Disposition-table completeness.** Diff the file set named in `## Test Impact` and `## Documentation` against the files the plan says it will touch.

## Technical Approach

**Where it lives: a standalone module invoked by the critique skill.** `tools/plan_lint.py` with a `plan-lint` console script, called from `/do-plan-critique` before critic dispatch. Rejected alternatives: inside `/do-plan` (the author checking its own work is the weaker position, and a revision pass would need to re-run it anyway); as a pre-commit hook (plans commit incrementally by design, so a blocking hook would fire on every partial write).

**Findings are advisory, reported to `/do-plan` as a revision.** Not blocking. The two existing plan-document validators block, but they check for *presence* of a required section — a binary fact. Plan-lint reports *behavioral* mismatches whose correct resolution is sometimes "the expectation was written loosely", not "the plan is wrong". Blocking on a judgment call would trade critic rounds for lint rounds.

**Safety: commands are executed, so the blast radius must be bounded.** Plan documents are agent-authored, and a Verification row is arbitrary shell. Mitigations, all three required:
- Run each command with a hard timeout and captured output, never interactively.
- Refuse to execute a row whose command matches a destructive-pattern denylist (`rm`, `git push`, `git reset --hard`, `>` redirection, `curl`/`ssh`, `pkill`) and report it as `skipped: not auto-executable` rather than running it.
- Execute in the lane's own worktree, never the primary checkout.

This is the one part of the design that is genuinely dangerous if done casually, and the denylist is a mitigation rather than a guarantee. See Open Questions.

**Parsing.** The `## Verification` table is a stable three-column markdown form (`| Check | Command | Expected |`) with the command in backticks, present in 37 of 43 plans. A parser over that section found 25 rows in the #2733 plan. The `Expected` column is free text — see Rabbit Holes.

## Freshness Check

Baseline `origin/main` at `d4d1519b2`. Issue #3178 filed 2026-09-05T13:22:07Z; three commits have landed since, all plan revisions for unrelated lanes (`db-derivation-guard`, `sibling-reflections`, `#2712`). None touch the validators, skills, or plan-format surfaces this plan changes.

**Disposition: Unchanged.** All issue claims re-verified above against this baseline.

## Research

Phase 0.7 skipped: this work is purely internal — repo skills, a repo-local module, and plan-document format. No external libraries, APIs, or ecosystem patterns are involved. The one question with external literature (whether cross-critic duplication is a sound saturation proxy) belongs to Part C, which this plan defers.

## Prior Art

**#1760 — `investigation: /do-sdlc PLAN↔CRITIQUE router never converges to BUILD (notes-only revision re-stales a clean verdict)`** (closed 2026-07-11). This is the load-bearing prior art and it directly constrains scope.

It documents the same loop from the other end: a revision pass embeds critique notes into the plan text and sets `revision_applied: true`, which busts the plan hash and re-stales the just-recorded verdict, sending the router back to re-critique indefinitely. Both observed runs "had to manually drive the BUILD stage against a plan that was already marked build-ready, with zero code-correctness blockers ever raised."

It also records a lineage of five prior dead-end fixes to this router area: `3e1e3dae` (#1668), `6e943ea9` (#1639), `5bc6243a` (#1638/#1640/#1641), `8218c5af` (#1554), `627e3cf0` (#1755).

## Why Previous Fixes Failed

Every fix in that lineage adjusted *when the router re-dispatches* — stale-verdict supersession, empty-verdict dead-ends, re-fire guards. None reduced *how many findings each round produces*. The loop kept running because each pass minted fresh non-blocking prose findings, and the machinery had no way to run out of them.

That is the gap this plan targets, and it is why the ordering matters: **the concern re-critique bound is best understood as the circuit breaker for this known-recurring loop.** Removing or loosening it (issue #3178 Part C) without first reducing finding volume would remove a brake from a mechanism that has already resisted five repairs. Part A reduces the input to the loop and touches no router logic, so it is safe to land alone and makes any later Part C decision measurable rather than speculative.

## Step by Step Tasks

1. **`tools/plan_lint.py`** — parse `## Verification`, `## Test Impact`, `## Documentation` sections; extract commands and `file:line` citations.
2. **Verification executor** — run each row with timeout + captured output; apply the destructive-pattern denylist; emit claimed vs. actual exit code and stdout.
3. **Citation resolver** — for each `path:line`, confirm the path exists and the line is in range; emit the cited line's text.
4. **Disposition differ** — set-difference the files named in the disposition tables against files the plan's tasks name.
5. **Report format** — a markdown findings block `/do-plan` can consume as a revision input.
6. **Console script** — register `plan-lint` in `pyproject.toml [project.scripts]`.
7. **Wire into `/do-plan-critique`** — run before critic dispatch; attach the findings block to the critique input so critics see what is already known-broken and do not spend a finding on it.
8. **Tests** — `tests/unit/test_plan_lint.py`, including the `grep -c` exit-1 case and a denylisted command.

## Failure Path Test Strategy

- A Verification command that times out → row reported `timeout`, lint continues, exit status unaffected.
- A denylisted command → row reported `skipped: not auto-executable`, never executed. Assert the subprocess was not invoked.
- A malformed or absent `## Verification` section → lint reports "no verification table" and exits 0. A plan without one is valid (6 of 43 have none); absence is not a defect.
- A `file:line` citation whose file was deleted → reported as unresolved, not raised.
- Plan-lint itself raising → the critique skill proceeds to critic dispatch regardless. Lint is advisory; it must never be able to block a critique round.

## Test Impact

- [ ] `tests/unit/test_plan_lint.py` — NEW: parser, executor, denylist, citation resolver, disposition differ, and the fail-open path.
- [ ] No existing tests are affected. `tools/plan_lint.py` is a new module with no importers; wiring into `/do-plan-critique` edits a skill markdown body, which carries no test coverage today. Verified: `grep -rl "plan_lint" tests/` returns nothing.

## Rabbit Holes

- **The free-text `Expected` column.** Observed forms include `exit code 1`, `output contains 2`, and `match count == 0`. Do **not** build a general expectation-grammar interpreter. Report claimed text alongside actual behavior and let the reader compare. Constraining the vocabulary is a separate change to the plan template.
- **Do not rewrite the Verification-table format.** 37 existing plans use it.
- **Do not extend into linting prose quality.** Mechanical checks only; judgment stays with critics.

## No-Gos

Load-bearing, from the issue's Non-goals — these are the repo owner's stated position:

- Do **not** reduce planning thoroughness.
- Do **not** skip or shorten critique rounds, reduce roster size, or lower the round bound.
- Do **not** defer tech-debt review findings to follow-up issues.

The goal is strictly to change *what critics spend their budget on*, never *how much checking happens*.

Also out of scope for this plan: Part B (post-revision sweep) and Part C (convergence-based exit). Part C is deferred with reasoning recorded under Why Previous Fixes Failed.

## Update System

No update-system changes required. `tools/plan_lint.py` ships inside the repo and reaches every machine through the normal `scripts/remote-update.sh` pull. The `plan-lint` console script is installed by the existing `uv sync` step in that script, the same path every other `tools.*` entry point already uses. No new dependency, config file, or migration.

## Agent Integration

A `plan-lint` console script is registered in `pyproject.toml [project.scripts]`, which is how the agent reaches it via Bash. No bridge-internal import is needed: the caller is the `/do-plan-critique` skill body, which invokes it as a shell command like every other `sdlc-tool` step. No new Telegram-facing surface.

## Documentation

- [ ] Create `docs/features/plan-lint.md` — what the checks are, the denylist and its limits, why findings are advisory, and the #1760 relationship.
- [ ] Add a row to `docs/features/README.md` index table.
- [ ] Update `docs/sdlc/do-plan-critique.md` (or create it) to name the pre-dispatch lint step.

## Verification

Every command below was executed against `origin/main` at `d4d1519b2` before being written down; the "Pre-build actual" column records what it really did. Rows are read from stdout unless the row says "exit code".

| Check | Command | Expected | Pre-build actual |
|---|---|---|---|
| Module exists | `test -f tools/plan_lint.py` | exit code 0 | exit 1 (absent, as expected pre-build) |
| Console script registered | `grep -q '^plan-lint' pyproject.toml` | exit code 0 | exit 1 (absent, as expected pre-build) |
| Verification table is parseable | `python3 -c "import re;t=open('docs/plans/rtr-unconditional-2733.md').read();m=re.search(r'^## Verification.*?(?=^## )',t,re.S\|re.M);print(len([l for l in m.group(0).splitlines() if l.startswith('\|') and '\`' in l]))"` | prints a positive integer | printed `25`, exit 0 |
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_plan_lint.py` | exit code 0 | n/a — file does not exist yet |
| Lint runs on a real plan | `plan-lint docs/plans/rtr-unconditional-2733.md` | exit code 0, findings block on stdout | n/a — not yet built |
| No test references plan_lint yet | `git grep -l "plan_lint" -- tests/ ; test $? -eq 1` | exit code 0 | exit 0 (no matches, confirming greenfield) |

Note the last row's construction: a bare `git grep -l` returning no matches exits 1, so the row wraps it in an explicit `test $? -eq 1` rather than gating on the grep's own status. This is the exact defect class the plan exists to catch, written correctly here on purpose.

## Success Criteria

- `plan-lint <plan.md>` executes every non-denylisted Verification row and prints claimed vs. actual exit code and stdout for each.
- Run against the #2733 plan, it flags the six `grep -c`-style rows whose actual exit status is 1 while the row reads as a success condition.
- It resolves `file:line` citations and surfaces the cited line text.
- A denylisted command is reported skipped and demonstrably not executed.
- Plan-lint raising an exception leaves critique dispatch unaffected.
- `/do-plan-critique` runs it before dispatching critics and passes the findings block into the critique input.
- No change to round counts, roster size, or the concern re-critique bound.

## Open Questions

1. **Executing plan-authored shell.** A denylist is a mitigation, not a guarantee — a plan could carry a destructive command in a form the patterns miss. Is advisory execution acceptable with the denylist plus timeout plus worktree confinement, or should execution be opt-in per run (`--execute`), defaulting to parse-and-report-only?
2. **Advisory vs. blocking.** The plan argues advisory. If lint findings are routinely ignored, blocking becomes the stronger position. Worth revisiting after real runs.
3. **Part C interaction.** Given #1760's five-fix lineage, does landing Part A measurably reduce findings per round? If it does not, the saturation hypothesis behind Part C loses its main support and should be reconsidered rather than built.
4. **Does the review side need the same treatment?** `/do-pr-review` findings were not categorized in the measured run, so there is no evidence yet either way.
