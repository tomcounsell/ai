# Hotfix Issue Disposition

A commit that lands code on `main` without a pull request must say what it does
to the issue tracker. Two git hooks enforce it, and `python -m tools.doctor`
reports whether they are installed.

## Why

This repo ships work two ways. The SDLC pipeline routes an issue through plan,
build, test, review and merge, landing via a pull request. The hotfix path lets
small or urgent fixes land directly on `main`.

The PR path enforces issue linkage: `do-merge` requires a closing keyword in the
PR body, and `tools/sdlc_stage_query.py` deliberately refuses to treat a bare
`#N` mention as a link. The hotfix path had no equivalent. Nothing required a
hotfix commit to name the issues it resolved and nothing swept afterward, so a
hotfix could resolve an open issue and leave it open indefinitely,
indistinguishable from live work.

An audit of all 29 open `bug` issues on 2026-08-06 found exactly one stale
issue. It was stale because of a hotfix, not because of any merged PR: the
single largest source of stale open issues was the one path with no linkage
gate. Three commits show the whole range:

| Commit | Linkage | Outcome |
|---|---|---|
| `9fe58f45d` "Hotfix: remove the watchdog crash-storm alert delivery path" | none | Deleted the exact symbols #2479 named, fully obsoleting it, and resolved #2429's Group C. Both stayed open two days until the audit. |
| `f695d2bed` "Hotfix SDLC skill-file defects: #2493, #2492, #2465, #2419" | bare `#N` | #2493 closed; the other three still open. A bare mention carries no resolved/partial semantics, so nothing records whether that was deliberate. |
| `ac3a87d51` "Fix #2489: clear `deferred_self_draft_pending` on successful resend" | closing keyword | Auto-closed correctly. The mechanism already works on the hotfix path when used. |

## The rule

A commit landing on `main` that touches any file outside `docs/plans/` must
carry one of:

| Form | Meaning |
|---|---|
| `Closes #123` (also `Fixes`, `Resolves`) | This commit resolves #123. GitHub auto-closes it on push to the default branch. |
| `Refs #123` | This commit touches #123 but does **not** resolve it. |
| `No-issue: <reason>` | Nothing to link, and here is why. A reason is required. |

A bare `#123` satisfies nothing. It is exactly the ambiguity the gate removes.

## Where it fires

There are two ways to land on `main`, so there are two hooks. Both share
`find_violation()` in `scripts/check_issue_disposition.py`, so they cannot
drift.

| Hook | Covers | Why it alone is not enough |
|---|---|---|
| `.githooks/commit-msg` | Commits authored **on** `main` | Never sees a commit authored on `session/{slug}` and later pushed to `main`. |
| `.githooks/pre-push` | Commits pushed **to** `refs/heads/main` from any branch | Fires after the commit exists, so the fix is an amend or rebase rather than an edit. |

The commit-msg stage is the enforcement point rather than a Claude Code
PreToolUse Bash hook because it sees the final message regardless of how it
arrived. The `git commit -F -` heredoc form carries the message on stdin, where
no inspector of the command string can read it.

`.githooks/pre-push` also runs `tools/push_ancestry_guard.py`, which refuses a
push to `main` carrying an open PR branch's ancestry (#2026).

## Scope

Only `docs/plans/` is exempt. Plan-document commits (`Migrate completed plan:
X`, `Plan (slug): ...`) are the bulk of legitimate direct-to-`main` traffic and
essentially never resolve an issue by themselves; exempting them is what keeps
the hotfix path fast, which is the whole reason the path exists. The cost of the
gate on a real hotfix is one trailer line.

Prose is deliberately **not** exempt. `f695d2bed` changed only `.md` skill files
and is one of the two commits that motivated this feature.

Feature branches are never gated. The PR path already enforces linkage through
the PR body; gating side-branch commits would charge for it twice.

Messages git authors itself (`Merge ...`, `Revert "..."`, `fixup!`, `squash!`)
are exempt.

## What it does not catch

An issue **obsoleted by deletion** — #2479's shape, where a defect stopped
existing because the code was removed rather than repaired. The author of
`9fe58f45d` was not thinking about #2479 and had no reason to be. A gate that
asks "which issue does this close?" cannot surface an issue the author never
considered.

This is a stated limitation, not an oversight. Catching it means re-checking
open issues against the code they cite, which is a periodic audit rather than a
commit-time gate. The 2026-08-06 audit is the mechanism that found #2479, and it
remains the mechanism for that class.

## Fail direction

The gate decision is fail-closed: a message with no disposition is refused.
Infrastructure failures fail open — no git, an unreadable message file, a
detached HEAD, an unknown remote SHA on a first push, a missing checker script.
A broken environment must never brick every commit on the machine. This mirrors
`.githooks/pre-commit`'s convention.

`git commit --no-verify` and `git push --no-verify` bypass the gate, as with any
git hook. That is the break-glass.

## Installation, and how a dead gate announces itself

The hooks run only when `core.hooksPath` is set to `.githooks`. `/update`
configures it (`scripts/update/git.py`); `/setup` does not. A machine that has
only ever run `/setup` silently skips these hooks, and every other phase of
`.githooks/pre-commit` along with them.

Silence is the failure mode that matters here: a skipped gate looks exactly like
a passing one. `python -m tools.doctor` therefore reports
`git_hooks_installed`, which fails with the one-line fix when `core.hooksPath`
is unset or points elsewhere.

## Files

| Path | Role |
|---|---|
| `scripts/check_issue_disposition.py` | The rule. `find_violation()` is the pure decision; `check_pushed_range()` applies it across a push range. |
| `.githooks/commit-msg` | Commit-time leg. |
| `.githooks/pre-push` | Push-time leg, plus the #2026 ancestry guard. |
| `tools/doctor.py::_check_git_hooks_installed` | Reports an uninstalled hooks path. |
| `tests/unit/test_commit_issue_disposition.py` | Predicate tests plus both hooks subprocessed against an ephemeral repo. |
