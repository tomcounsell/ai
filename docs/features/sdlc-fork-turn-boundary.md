# SDLC Fork Turn Boundary

Invariants for `context: fork` skills (do-build, do-sdlc, sdlc, pthread, do-pr-review,
do-plan-critique, …): a forked skill gets exactly one non-resumable turn, so it must
never end that turn with a live background child, and it must own a single,
unambiguous worktree/branch identity for the issue it is building.

## Background

A supervised 6-issue parallel SDLC batch (#1898 #1899 #1901 #1902 #1904 #1905) shipped
every pipeline, but 4 of 6 needed manual supervisor recovery because the `context: fork`
dispatch path had three interlocking defects (tracked as issue #1915):

1. **Phantom-wait (Defect 1).** A fork spawning a subagent with `run_in_background: true`
   (or a template that resolves to `true`) returns immediately and expects a later
   notification — but a fork has no later turn to receive it. The fork reports "running
   in the background, I'll continue when it completes" and then never does, leaving
   uncommitted/unpushed work.
2. **Shared slug worktree, no lane seam (Defect 2).** Supervisors allocated
   `.worktrees/sdlc-{N}` lanes that nothing downstream read, causing a cross-issue
   worktree/branch conflict.
3. **Duplicate PRs (Defect 3).** A fork branch and a supervisor branch could each call
   `gh pr create --head session/{slug}` with no existence check, producing two
   byte-identical PRs per issue (#1908/#1910, #1911/#1912).

## The turn-boundary invariant (Defect 1)

**A `context: fork` skill never ends its turn with a live background child.** Dispatch
workers with `run_in_background: false` (or block-and-join every background child
in-turn) before proceeding to any commit/push/PR/cleanup step.

Commit `8542ffb19` (2026-07-07) landed the fix:

- `do-build/WORKFLOW.md:72` — builder dispatch is `run_in_background: false` (was
  `[true if Parallel: true]`).
- `do-build/WORKFLOW.md:76` — an explicit bold rule forbidding background dispatch
  inside the fork.
- `do-build/WORKFLOW.md:98-104` — the 15-minute poll/monitor/resume block is now an
  in-turn "results already in hand" verification.
- `do-build/SKILL.md:154` — orchestrator rule: "Run parallel tasks together, always
  in the foreground."
- `do-sdlc/SKILL.md:24` — Hard Rule 6: "ALWAYS dispatch with `run_in_background: false`,
  never end the turn waiting on a background child."
- `do-sdlc/SKILL.md:95` — §3c stage dispatch carries the explicit flag.

This is not new architecture: `docs/plans/completed/critique_await_all_critics_barrier.md`
fixed the identical background-then-await bug in do-plan-critique earlier, via a
filesystem roster barrier that blocks the driver in-turn until every critic writes its
result. `8542ffb19` applied the same "spawn → block-and-join in the same turn" pattern
to do-build and do-sdlc. See also `docs/plans/completed/sdlc-fork-issue-number-divert.md`,
which established and documented the "fork loses session context" failure class this
invariant guards against.

### Concurrent-foreground builder model

`run_in_background: false` does not mean *serial*. Multiple foreground `Task`/`Agent`
calls issued in one message run **concurrently** — the harness dispatches them together
and blocks for all results before the turn advances. This is deliberate: it gives
do-build real parallelism for `Parallel: true` tasks without ever leaving a child running
past the fork's own turn. The residual risk is that two concurrent builders writing to
the *same* git index (the shared `.worktrees/{slug}` checkout) can interleave staging —
bounded by do-build's existing convention that `Parallel: true` is only set for tasks
that write disjoint file sets. True per-builder-worktree isolation (N worktrees + a
merge-back step) was considered and declined as unnecessary scope for this fix; it
remains the escape hatch if within-issue index corruption is observed in practice.

### Regression guard

`tests/unit/test_sdlc_fork_no_background.py` discovers every skill file whose YAML
frontmatter declares `context: fork` (plus do-build's real dispatch file,
`WORKFLOW.md`, which carries the risk even though the `context: fork` marker itself
lives in `do-build/SKILL.md`) and asserts none of them contain an un-joined background
dispatch — no literal `run_in_background: true` in a dispatch position, and no
`run_in_background: [true if Parallel: ...]` template. Negated/prose mentions (e.g.
do-plan-critique's "never `run_in_background: true`") are excluded from the check so
they don't false-positive. The test also carries positive assertions that do-build and
do-sdlc explicitly declare `run_in_background: false`, so any revert of `8542ffb19` is
caught mechanically rather than only by prose review.

## Slug identity always wins (Defect 2)

Each issue's fork exclusively owns exactly one worktree/branch pair:
`.worktrees/{slug}` and branch `session/{slug}`, derived once from the issue's plan
slug (`agent/worktree_manager.py`, `agent/agent_session_queue.py::resolve_branch_for_stage`)
and reused by every stage subagent dispatched for that issue.

**Supervisors (`/do-sdlc`, PM sessions) must NOT allocate a separate
`.worktrees/sdlc-{N}` lane per issue or per run.** That lane-allocation pattern is
dropped because nothing downstream in the pipeline reads or honors it — it was the root
cause of a cross-issue worktree/branch conflict in the batch that motivated this fix.

This is a documentation-only decision, declared in `## Worktree & Branch Ownership`
sections of `.claude/skills-global/do-sdlc/SKILL.md` and `.claude/skills/sdlc/SKILL.md`.
No override seam was added: `worktree_manager.py` and `resolve_branch_for_stage` are
unchanged, and the existing slug→worktree→branch derivation remains the single source
of truth. Adding a worktree/branch override seam was considered and declined — it would
re-introduce the lane-allocation coupling this decision deliberately removes.

Slug-wins is what structurally collapses duplicate PRs: since fork and supervisor now
always converge on the same head branch, GitHub's one-open-PR-per-head rule does the
rest (see Defect 3 below).

## Live-ref PR dedup guard (Defect 3)

Before `gh pr create --head session/{slug}` (`do-build/PR_AND_CLEANUP.md`, Step 7),
do-build now runs a live-ref existence check:

```bash
if ! EXISTING_PR=$(gh pr list --head session/{slug} --state open --json number -q '.[0].number'); then
  echo "ERROR: 'gh pr list --head session/{slug}' failed — aborting PR step rather than risking a duplicate create." >&2
  exit 1
fi
if [ -n "$EXISTING_PR" ]; then
  echo "Reusing PR #$EXISTING_PR"
else
  gh pr create --head session/{slug} ...
fi
```

Three things matter about this guard:

- **Live-ref, not search.** `gh pr list --head` queries the live ref directly, unlike
  `gh pr list --search "#{issue_number}"` (used by the sdlc router's own existence
  probe in `.claude/skills/sdlc/SKILL.md`, step 2c), which lags GitHub's search index.
  The router keeps its issue-keyed search probe (the `--head` query can't replace it —
  it needs a known slug/branch first) but now carries a comment noting the live-ref
  cross-check is available once the branch name is known.
- **Cross-repo builds MUST pass `--repo $TARGET_GH_REPO` on *both* the list and the
  create.** Omitting it on the `--head` guard queries the wrong repo, always sees "no
  PR," and always creates a duplicate — silently reopening Defect 3 for cross-repo
  builds. This is mandatory, not optional.
- **The guard fails closed on a `gh` error, not open.** A blind
  `EXISTING_PR=$(gh pr list --head ...)` with no exit-code check makes a `gh` failure
  (auth expiry, network blip, rate limit) look identical to "no PR found" — an empty
  variable either way — so the script would fall through to `gh pr create` and risk a
  duplicate PR exactly when the existence check is least reliable. The guard above
  captures `gh`'s own exit status (`if !`) and aborts the PR step instead.

**Dependency on Defect 2.** This guard only de-dupes correctly *because* slug-wins
(above) guarantees every dispatch path for an issue converges on the same head branch.
If that convergence is ever broken — e.g. a lane seam reappears and a supervisor starts
pushing to a different branch for the same issue — the `--head` lookup silently stops
catching duplicates created on the other branch. The two fixes are a pair: Defect 2
makes "one head per issue" true, Defect 3 makes PR creation respect it.

As a structural backstop even under a true race (two forks calling `gh pr list --head`
at the same microsecond), GitHub itself rejects a second `gh pr create` for an
already-open head branch — the guard makes the common case graceful, the platform makes
the race case safe.

## Root cause pattern

The identical background-then-await bug has been fixed twice by point patches — first
in do-plan-critique, then in do-build/do-sdlc (`8542ffb19`) — each time applied only to
the skill that had most recently burned a pipeline, never generalized to the whole
`context: fork` family. The durable fix generalized in this issue is a mechanical,
discovery-based enforcement test that scans *every* `context: fork` skill (not just the
two that had already burned a pipeline), so a regression in an untouched fork skill
(pthread, do-pr-review, …) is caught before it ships rather than after.

## See also

- [Headless Session Runner](headless-session-runner.md) — the `claude -p` subprocess
  execution model that `context: fork` skills run inside.
- [Eng Session Architecture](eng-session-architecture.md) — how Eng sessions dispatch
  SDLC work via forked stage skills and the resumable `dev` subagent.
- `docs/plans/completed/critique_await_all_critics_barrier.md` — prior art for the
  spawn-then-block-and-join-in-turn pattern, applied first to do-plan-critique.
- `docs/plans/completed/sdlc-fork-issue-number-divert.md` — established the "fork loses
  session context" failure class this invariant guards against.
- `tests/unit/test_sdlc_fork_no_background.py` — the regression-guard test.
