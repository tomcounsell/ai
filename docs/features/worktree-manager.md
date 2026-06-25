# Worktree Manager

`agent/worktree_manager.py` owns the lifecycle of git worktrees used for
session filesystem isolation. Each work item (slug) gets its own directory
under `.worktrees/{slug}/` checked out to branch `session/{slug}`.

This document covers the **branch verification** subsystem added by issue
[#1377](https://github.com/tomcounsell/ai/issues/1377). For the broader
lifecycle (creation, cleanup, busy-checking) see `session-isolation.md`.

## Why branch verification exists

A BUILD-stage dev session creates `.worktrees/{slug}/` checked out to
`session/{slug}`. When the PM later dispatches a follow-up dev session (e.g.
MERGE) reusing the same slug, the worker reads the existing path and asks
`resolve_branch_for_stage(slug, stage)` what branch to use. If
`current_stage` is `None` on the AgentSession — or maps to `main` — the
resolver returns `("main", False)` and worktree provisioning is skipped.
Without a verification step, the executor would launch the Claude Code
subprocess inside the worktree while it was still on `session/{slug}`,
producing zero output until the startup watchdog killed it 6+ minutes
later.

`verify_worktree_branch` closes that gap. It is invoked from
`session_executor.py` after the main-checkout protection guard (issue #887)
and before the harness launches.

## Behavior

`verify_worktree_branch(worktree_path: Path, expected_branch: str) -> None`

| Condition | Outcome | Log |
|-----------|---------|-----|
| `worktree_path` is `None` | `TypeError` | — |
| `expected_branch` is empty/whitespace | `ValueError` | — |
| `worktree_path` does not exist | `WorktreeBranchMismatchError` | — |
| HEAD branch equals expected | return silently | (silent) |
| HEAD branch differs, working tree clean | run `git checkout <expected>`, return | INFO `[worktree-branch-recovery]` with slug/from/to |
| HEAD branch differs, working tree dirty | `WorktreeBranchMismatchError(dirty_files=[...])` | — |
| Underlying git command fails | `WorktreeBranchMismatchError(cause=...)` | — |

### Why auto-checkout on clean

Two policies were considered: always raise, or auto-recover when safe. The
"always raise" policy maximizes signal — every stale-slug condition becomes
a visible session failure — but it also turns every benign stage handoff
(BUILD finishes clean, MERGE picks up) into an operator chore. The
auto-checkout policy is the conservative middle ground: it can only run
when the working tree is clean (so no uncommitted work is at risk), it
emits a structured `[worktree-branch-recovery]` log line so dashboard
reflections can still surface the condition, and it raises on the
dangerous case (dirty tree on the wrong branch).

### Why raise on dirty

Auto-checking-out a dirty worktree would either silently move uncommitted
changes to a different branch (data loss risk) or fail mid-checkout in a
git-specific way that does not name the underlying problem. Raising
`WorktreeBranchMismatchError` with `dirty_files` populated preserves the
work for human inspection and produces a `last_error` value the dashboard
can render.

## Invocation site

```python
# agent/session_executor.py (just after the issue #887 main-checkout guard)
if _stype == "dev" and slug and WORKTREES_DIR in str(working_dir):
    from agent.worktree_manager import (
        WorktreeBranchMismatchError,
        verify_worktree_branch,
    )
    try:
        verify_worktree_branch(working_dir, branch_name)
    except WorktreeBranchMismatchError as e:
        logger.error(...)
        raise
```

The guard fires for **every dev session with a slug**, not just MERGE —
defense-in-depth that covers any future regression where stage inference
returns the wrong branch.

## Failure surface

When the guard raises, the executor propagates the exception. The session
ends with `status=failed` and `last_error` populated with the rendered
exception message (including expected branch, actual branch, worktree
path, and dirty file list). This is a visible failure mode, in contrast to
the silent `communicated=False` hang it replaces.

## Related

- Issue [#1377](https://github.com/tomcounsell/ai/issues/1377) — bug
  report and root cause.
- PR #1367 — refuse-busy guard. Complementary; checks "is another session
  using this worktree", not "is it on the expected branch".
- PR #1291 — pre-commit guard against `session/*` commits outside
  `.worktrees`.
- PR #1280 — synthetic-slug funnel that routes every dev session through
  `get_or_create_worktree`, concentrating traffic through the
  now-guarded code path.
- Issue #887 — main-checkout protection guard. The branch-mismatch guard
  runs immediately after it in `session_executor.py`.
- `docs/features/session-isolation.md` — overall worktree lifecycle.
