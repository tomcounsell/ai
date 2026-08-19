#!/usr/bin/env python3
"""Require an explicit issue disposition on code commits to ``main`` (#2540).

**The gap this closes.** This repo ships work two ways. The SDLC pipeline
lands via a pull request, and ``do-merge`` refuses a PR whose body carries no
closing keyword. The hotfix path lands directly on ``main`` with no PR and had
no equivalent gate, so a hotfix could resolve an open issue and leave it open
indefinitely, indistinguishable from live work. A 2026-08-06 audit of 29 open
``bug`` issues found exactly one stale issue, and it was stale because of a
hotfix rather than any merged PR.

**The rule.** A ``git commit`` on ``main`` that stages any file outside
``docs/plans/`` must say what it does to the issue tracker. One of:

- a closing keyword and issue -- ``Closes #123`` / ``Fixes #123`` /
  ``Resolves #123``. GitHub auto-closes on push to the default branch.
- ``Refs #123`` -- this commit touches #123 but does NOT resolve it. The
  explicit form of what a bare ``#123`` mention used to leave ambiguous.
- ``No-issue: <reason>`` -- nothing to link, and here is why.

A bare ``#123`` with no keyword satisfies nothing: it is precisely the
ambiguity the gate exists to remove. ``tools/sdlc_stage_query.py`` already
refuses to treat a bare mention as a link on the PR side; this is the same
judgement on the hotfix side.

**Why ``docs/plans/`` is exempt.** Plan-document commits (``Migrate completed
plan: X``, ``Plan (slug): ...``) are the bulk of legitimate direct-to-``main``
traffic and essentially never resolve an issue by themselves. Exempting them
keeps the hotfix path fast, which is the whole reason it exists. Everything
else -- source, tests, config, skills, feature docs, runbooks -- is in scope.
Skill and doc files are deliberately NOT exempt: commit ``f695d2bed`` ("Hotfix
SDLC skill-file defects: #2493, #2492, #2465, #2419") changed only ``.md``
skill files, mentioned four issues with no keyword, and left three of them
open with no record of whether that was deliberate.

**What this does not catch.** An issue that a hotfix *obsoletes by deletion*
-- issue #2479's shape, where the defect stopped existing because the code was
removed. That hotfix's author was not thinking about #2479 and had no reason
to be, so a gate asking "which issue does this close?" cannot surface it. This
is a stated limitation, not an oversight: detecting it means re-checking open
issues against the code they cite, which is a periodic audit, not a
commit-time gate. See ``docs/features/hotfix-issue-disposition.md``.

**Two enforcement points, because there are two ways to land on ``main``.**

- ``.githooks/commit-msg`` -- fires when the commit is authored ON ``main``.
  This is the earliest possible moment: the message can be fixed before the
  commit exists.
- ``.githooks/pre-push`` -- fires when commits authored on a side branch are
  pushed TO ``main`` (``git push origin HEAD:main``, the shape a worktree-based
  hotfix takes). The commit-msg leg never sees these, because at commit time
  the branch was not ``main``. Without this leg the gate would miss the way
  most agent hotfixes actually land.

Both legs share :func:`find_violation`, so they cannot drift.

**Fail direction.** The gate decision is fail-CLOSED: a message with no
disposition is refused. Infrastructure failures (no git, unreadable message
file, an unknown remote SHA on a first push) fail OPEN, matching
``.githooks/pre-commit``'s convention -- a broken environment must never brick
every commit on the machine. ``--no-verify`` bypasses this like any git hook;
that is the break-glass.

Invoked by ``.githooks/commit-msg`` with the message file path as argv[1], or
by ``.githooks/pre-push`` with ``--pre-push`` and git's ref lines on stdin.
Exit 0 allows, exit 1 blocks with a message on stderr.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Paths whose commits never need a disposition. Directory prefixes, matched
# against git's forward-slash-separated paths from the repo root.
EXEMPT_PREFIXES = ("docs/plans/",)

# Ceiling on the local `git` reads below. Named rather than inline because this
# script runs as a git hook in a bare environment and cannot import
# ``config.settings``; the timeout guard's other remedy is unavailable here.
GIT_READ_TIMEOUT_SECONDS = 10

# GitHub's closing keywords. Matching GitHub's own set exactly means the gate
# passes precisely when GitHub will actually auto-close on push.
_CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+",
    re.IGNORECASE,
)

# "Touches but does not resolve." The explicit disposition for a commit that
# would otherwise carry a bare `#N`.
_REFS_RE = re.compile(r"\brefs?\b\s*:?\s+#\d+", re.IGNORECASE)

# "Nothing to link." Requires a reason -- a bare `No-issue:` says no more than
# silence does.
_NO_ISSUE_RE = re.compile(r"^\s*no[-_ ]issue\s*:\s*\S+", re.IGNORECASE | re.MULTILINE)

# Messages git generates or rewrites itself, where demanding a trailer would
# fight the tool rather than the author.
_GENERATED_PREFIXES = ("merge ", "revert ", "fixup!", "squash!", "amend!")

BLOCK_MESSAGE = """
COMMIT BLOCKED (#2540): this commit lands code on `main` with no issue disposition.

Direct-to-`main` commits have no PR body, so nothing records what they do to
the issue tracker. GitHub closes an issue only on a closing keyword; a bare
`#123` mention closes nothing and says nothing about whether that was intended.

Add ONE of these to the commit message:

  Closes #123          this commit resolves #123 (also Fixes / Resolves)
  Refs #123            this commit touches #123 but does NOT resolve it
  No-issue: <reason>   nothing to link, and here is why

Staged files outside docs/plans/ ({count}):
{files}

Bypass with `git commit --no-verify` if this gate is wrong for your case.
Full rationale: docs/features/hotfix-issue-disposition.md
"""

PLAN_CLOSING_MESSAGE = """
COMMIT BLOCKED (#2890): this plan-only commit carries a GitHub closing keyword.

Plan-document commits need no disposition, but GitHub still honours a closing
keyword in the body and will close the issue on push to `main`. A plan that
quotes the `Closes #N` its PR body must eventually carry hands that keyword to
`main` months early, closing a live issue against code that never changed.

Rewrite the keyword so it does not fire, e.g.:

  the PR body carries a closing keyword for 123
  Refs #123

Leave the literal `Closes #123` only where it must fire: the PR body itself.

Bypass with `git commit --no-verify` if this gate is wrong for your case.
Full rationale: docs/features/hotfix-issue-disposition.md
"""


def _git(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Run git; return ``(returncode, stdout)``. Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_READ_TIMEOUT_SECONDS,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def current_branch(cwd: str | None = None) -> str | None:
    """Return the current branch name, or None when detached or not a repo."""
    code, out = _git(["symbolic-ref", "--short", "HEAD"], cwd=cwd)
    return out if code == 0 and out else None


def staged_paths(cwd: str | None = None) -> list[str]:
    """Return repo-relative paths staged for this commit."""
    code, out = _git(["diff", "--cached", "--name-only"], cwd=cwd)
    if code != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def in_scope_paths(paths: list[str]) -> list[str]:
    """Return the staged paths that require a disposition (non-exempt)."""
    return [p for p in paths if not p.startswith(EXEMPT_PREFIXES)]


def has_disposition(message: str) -> bool:
    """Return True iff the message carries one of the three accepted forms."""
    return bool(
        _CLOSING_RE.search(message) or _REFS_RE.search(message) or _NO_ISSUE_RE.search(message)
    )


def is_generated_message(message: str) -> bool:
    """Return True for messages git authors itself (merge, revert, fixup)."""
    for line in message.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.lower().startswith(_GENERATED_PREFIXES)
    return False


def find_violation(
    message: str,
    branch: str | None,
    paths: list[str],
) -> str | None:
    """Pure predicate: return a block message, or None to allow.

    Separated from the CLI so tests exercise the decision without a real repo
    or a real commit.
    """
    if branch != "main":
        return None
    if is_generated_message(message):
        return None

    scoped = in_scope_paths(paths)
    if not scoped:
        # Plan-document-only commit (or an empty stage): exempt from *declaring*
        # a disposition, but not licensed to close an issue. Plan prose is
        # summarised into the commit body, and plans routinely quote the literal
        # `Closes #N` their PR body must eventually carry, so the keyword reaches
        # `main` and GitHub honours it (#2890).
        if _CLOSING_RE.search(message):
            return PLAN_CLOSING_MESSAGE
        return None

    if has_disposition(message):
        return None

    shown = scoped[:10]
    listing = "\n".join(f"  {p}" for p in shown)
    if len(scoped) > len(shown):
        listing += f"\n  ... and {len(scoped) - len(shown)} more"
    return BLOCK_MESSAGE.format(count=len(scoped), files=listing)


MAIN_REF = "refs/heads/main"
_ZERO_SHA = "0" * 40


def commit_message(sha: str, cwd: str | None = None) -> str:
    """Return the full commit message for ``sha`` (empty string on failure)."""
    code, out = _git(["log", "-1", "--format=%B", sha], cwd=cwd)
    return out if code == 0 else ""


def commit_paths(sha: str, cwd: str | None = None) -> list[str]:
    """Return the paths ``sha`` changed, relative to the repo root."""
    code, out = _git(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=cwd,
    )
    if code != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def check_pushed_range(local_sha: str, remote_sha: str, cwd: str | None = None) -> str | None:
    """Return a block message for the first undisposed commit in the range.

    ``remote_sha..local_sha`` is exactly the set of commits this push would add
    to ``main``, so commits already on the remote are never re-judged. Each is
    evaluated as if it had been authored on ``main`` -- which, once the push
    lands, is what happened.
    """
    if not local_sha or local_sha == _ZERO_SHA:
        # Branch deletion. Nothing to judge.
        return None
    if not remote_sha or remote_sha == _ZERO_SHA:
        # No known remote tip (first push to a fresh remote). The range would
        # be the whole history; fail open rather than judge every past commit.
        return None

    code, out = _git(["rev-list", f"{remote_sha}..{local_sha}"], cwd=cwd)
    if code != 0:
        # Unresolvable range (shallow clone, missing object). Fail open.
        return None

    for sha in [line.strip() for line in out.splitlines() if line.strip()]:
        violation = find_violation(
            message=commit_message(sha, cwd=cwd),
            branch="main",
            paths=commit_paths(sha, cwd=cwd),
        )
        if violation:
            subject = commit_message(sha, cwd=cwd).splitlines()[:1]
            label = subject[0] if subject else "(no subject)"
            return (
                f"PUSH BLOCKED (#2540): commit {sha[:12]} would land on `main` "
                f"with no issue disposition.\n\n  {label}\n{violation}\n"
                "Amend or rebase to add a disposition, or push with --no-verify."
            )
    return None


def _run_pre_push(stdin_text: str) -> int:
    """Git pre-push protocol: ``<local ref> <local sha> <remote ref> <remote sha>``.

    Acts only on the line whose remote ref is ``refs/heads/main``.
    """
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = parts
        if remote_ref != MAIN_REF:
            continue
        violation = check_pushed_range(local_sha, remote_sha)
        if violation:
            print(violation, file=sys.stderr)
            return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--pre-push":
        try:
            return _run_pre_push(sys.stdin.read())
        except OSError:
            return 0

    if len(argv) < 2:
        # No message file: nothing to evaluate. Fail open.
        return 0

    try:
        message = Path(argv[1]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Fail open: an unreadable message file is an infrastructure problem,
        # not a policy violation.
        return 0

    violation = find_violation(
        message=message,
        branch=current_branch(),
        paths=staged_paths(),
    )
    if violation:
        print(violation, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
