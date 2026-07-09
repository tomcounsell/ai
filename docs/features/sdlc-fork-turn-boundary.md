# SDLC Fork Turn-Boundary Invariant

**Status:** Shipped (issue #1915)

A `context: fork` skill (`do-build`, `do-sdlc`, `sdlc`, `pthread`, `do-pr-review`, `do-plan-critique`, etc.) runs in a forked subagent context that gets exactly **one turn**. It is never resumed. If a fork ends that turn with a live background child, no later turn exists to receive the child's result. The fork reports "running in the background, I'll continue when it completes" and then never does, leaving work uncommitted.

A supervised six-issue SDLC batch (#1898, #1899, #1901, #1902, #1904, #1905) hit exactly this bug: four of six builds needed manual supervisor recovery because `do-build` and `do-sdlc` spawned builders with background dispatch and had no turn left to collect the result.

## The turn-boundary invariant

**A `context: fork` skill never ends its turn with a live background child.** Dispatch workers with `run_in_background: false`, or block-and-join every background child in-turn, before any commit, push, PR, or cleanup step runs.

Commit `8542ffb19` ("Fix do-sdlc/do-build fork phantom-wait: force `run_in_background: false`") landed this invariant in prose:

- `.claude/skills-global/do-build/WORKFLOW.md:72`: builder dispatch is `run_in_background: false` (previously `[true if Parallel: true]`).
- `.claude/skills-global/do-build/WORKFLOW.md:76`: explicit rule forbidding background dispatch inside the fork, with the failure mode spelled out.
- `.claude/skills-global/do-build/WORKFLOW.md:98`: the old 15-minute poll/resume block is gone. Step 4 verifies builder results in-turn, since they are already in hand.
- `.claude/skills-global/do-build/SKILL.md:154`: orchestrator rule, "Run parallel tasks together, always in the foreground."
- `.claude/skills-global/do-sdlc/SKILL.md:24`: Hard Rule 6, always dispatch with `run_in_background: false`.
- `.claude/skills-global/do-sdlc/SKILL.md:99`: the stage-dispatch prompt itself carries the explicit `run_in_background: false` flag.

## The concurrent-foreground builder model

Forcing `run_in_background: false` does not serialize builders. To run builders in parallel inside a fork, make multiple foreground `Task` calls in the **same message**. The harness executes them concurrently and blocks for all results before the fork's next turn. This gets parallelism without background scheduling: no notification to miss, because there is no later turn waiting for one.

Because concurrent builders still share one worktree's git index, `Parallel: true` is only valid for tasks that write disjoint file sets. do-build's existing convention (never mark two tasks `Parallel: true` if they touch the same files) is the mitigation for in-progress index interleaving; this plan did not add per-builder worktrees or serialization on top of it.

## Slug-identity-always-wins ownership

Each issue's build fork exclusively owns `.worktrees/{slug}` and `session/{slug}`, derived from the plan slug. This derivation is the single source of truth in `agent/worktree_manager.py` and `agent/agent_session_queue.py::resolve_branch_for_stage`, and neither was changed by this work.

Supervisors and the resumable `dev` subagent do not allocate separate `.worktrees/sdlc-{N}` lanes. Nothing in the codebase reads a lane override, so lane instructions were silently dropped, which is exactly how #1904, #1899, and #1898 collided: a supervisor's separate branch and the fork's own branch both tried to own the same issue's build.

Converging fork and supervisor onto one branch per plan structurally collapses duplicate PRs, because GitHub permits only one open PR per head branch. The ownership rule is declared in `.claude/skills-global/do-sdlc/SKILL.md:26` and `.claude/skills/sdlc/SKILL.md:13`, under a "Worktree & branch ownership" heading in both files.

## The live-ref PR dedup guard

Before `gh pr create`, `do-build` runs a live-ref lookup:

```bash
gh pr list --head session/{slug} --state open --json number -q '.[0].number'
```

If a PR already exists for that head, `do-build` reuses its number and skips `gh pr create`. This replaces the pipeline's only prior dedup probe, a search-based `gh pr list --search "#{issue_number}"` check (`sdlc/SKILL.md:95`), which queries GitHub's search index and lags behind live state. The search probe stays in place because it is issue-number-keyed and the `--head` query cannot replace it; `sdlc/SKILL.md:97` now carries a comment cross-referencing the live-ref check.

Cross-repo builds **must** pass `--repo $TARGET_GH_REPO` on both the list guard and the create call (`.claude/skills-global/do-build/PR_AND_CLEANUP.md:63` and `:101`). Omitting `--repo` on the guard makes it query the wrong repository, always see "no PR," and always create a duplicate.

If two forks somehow call the guard at the same instant and both see "no PR," GitHub itself rejects the second `gh pr create` for an already-open head branch. The `--head` guard makes the common case graceful; GitHub's one-PR-per-head rule is the backstop for the rare race.

## Why this matters

Every defect above traces back to the same root cause: a `context: fork` skill has no enforced invariant that it must reach terminal state within its own turn. The identical background-then-await bug was fixed twice by point patches before this work, each time only in the skill that had most recently burned a pipeline:

- `docs/plans/completed/critique_await_all_critics_barrier.md` fixed `do-plan-critique`, which spawned critics with `run_in_background: true` and could record a verdict before late critics finished, silently dropping their findings. The fix replaced background-and-await with an in-turn filesystem roster barrier.
- `docs/plans/completed/sdlc-fork-issue-number-divert.md` established and documented the underlying failure class: a `context: fork` skill is a single non-resumable turn that loses session context if it doesn't finish its own work.
- `8542ffb19` fixed `do-build` and `do-sdlc`, the two skills the #1915 batch implicated, but left every other `context: fork` skill relying on the tool's background default with no mechanical guard against a future regression.

This work generalizes the fix into an enforced invariant instead of a fourth point patch.

## Where the guard lives

`tests/unit/test_sdlc_fork_no_background.py` discovers every `context: fork` skill by scanning `.claude/skills-global/**/SKILL.md` and `.claude/skills/**/SKILL.md` for `context: fork` frontmatter, plus do-build's multi-file dispatch sub-files (`WORKFLOW.md`, `PR_AND_CLEANUP.md`). For each discovered file it asserts:

- the file exists and is non-empty (a missing or empty fork skill file is a test failure, never a silent pass);
- the file contains no un-joined background dispatch pattern (`run_in_background: true`, or the old `run_in_background: [true if Parallel:` template), with negated or backtick-quoted prose mentions correctly excluded so a sentence like do-plan-critique's "never `run_in_background: true`" does not false-positive;
- the named dispatching skills (`do-build/WORKFLOW.md`, `do-sdlc/SKILL.md`) carry the literal `run_in_background: false`;
- `do-build/PR_AND_CLEANUP.md`'s `gh pr list --head` reuse guard precedes `gh pr create`;
- `sdlc/SKILL.md` carries the router's live-ref cross-check note.

A revert of any part of `8542ffb19`, or a new fork skill added without the invariant, fails this test loudly and names the offending file and pattern.

## See also

- [Headless Session Runner](headless-session-runner.md): the resumable `dev` subagent that calls leaf `context: fork` skills like `/do-build` directly
- [Eng Session Architecture](eng-session-architecture.md): session-type routing for the Eng session that owns SDLC work
- `docs/plans/completed/critique_await_all_critics_barrier.md`: prior art for the background-then-await bug class
- `docs/plans/completed/sdlc-fork-issue-number-divert.md`: prior art establishing the fork-loses-context failure class
