"""Refuse a push to ``main`` that carries an OPEN PR branch's ancestry (issue #2026).

The merge guard (``.claude/hooks/validators/validate_merge_guard.py``) only fires on
``gh pr merge`` commands. A plain ``git push origin main`` never invokes ``gh pr
merge`` — so when a lane's worktree HEAD is left detached at a PR branch head and a
subsequent docs-cascade ``git push`` runs, GitHub registers the push as the PR's
merge with no gate ever consulted (the #2026 push-ancestry bypass, benign that time,
a merge-gate bypass class in general).

This guard closes that hole. It runs as a git ``pre-push`` hook body AND as an
explicit call in the do-docs cascade push step (defense in depth). It refuses a push
to ``refs/heads/main`` when the pushed HEAD is descended from (contains) the head of
any OPEN PR branch, unless an explicit break-glass authorization is present.

Design / fail direction
-----------------------
- **Scope strictly to ``refs/heads/main``.** Feature-branch pushes are never impeded.
- **Fail-closed on an ancestry match** — a push carrying an open PR's commits is
  refused (named ``PUSH_CARRIES_OPEN_PR_ANCESTRY``, exit 1) unless authorized.
- **Fail-open on a ``gh`` outage** — an offline machine is not bricked: the remote
  open-PR query is skipped and the push is allowed (logged). BUT the purely-local
  detached-HEAD check still fires without ``gh``: a HEAD detached exactly at a
  non-``main`` local branch tip is the #2026 shape and is refused locally.
- **Break-glass override.** ``data/merge_authorized_{pr}`` carrying an
  ``override: <reason>`` line (the SAME file the merge guard honors) authorizes the
  intended path. A squash-merged PR drops out of ``--state open``, so the guard no
  longer treats its ancestry as a bypass.

Invocation
----------
Git pre-push protocol: git passes ``<local ref> <local sha> <remote ref>
<remote sha>`` lines on stdin. This guard reads those lines and acts only on the
line whose remote ref is ``refs/heads/main``. When invoked with no stdin (an explicit
skill call), it inspects the current HEAD against ``origin/main``'s push target.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from config.settings import settings

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _REPO_ROOT / "data"
_OVERRIDE_LINE_RE = re.compile(r"^\s*override:\s*(.+)$", re.MULTILINE)

MAIN_REF = "refs/heads/main"
ERR_ANCESTRY = "PUSH_CARRIES_OPEN_PR_ANCESTRY"
ERR_DETACHED = "PUSH_DETACHED_AT_PR_BRANCH_TIP"


def _git(args: list[str]) -> tuple[int, str]:
    """Run a git command in the repo; return ``(returncode, stdout)``. Never raises."""
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(_REPO_ROOT),
        )
        return proc.returncode, proc.stdout.strip()
    except Exception:
        return 1, ""


def _is_ancestor(ancestor_sha: str, descendant_sha: str) -> bool:
    """True IFF ``ancestor_sha`` is an ancestor of (contained in) ``descendant_sha``."""
    rc, _ = _git(["merge-base", "--is-ancestor", ancestor_sha, descendant_sha])
    return rc == 0


def _pr_authorized(pr_number: int) -> bool:
    """True IFF ``data/merge_authorized_{pr}`` carries an ``override: <reason>`` line."""
    auth_file = _DATA_DIR / f"merge_authorized_{pr_number}"
    try:
        if not auth_file.exists():
            return False
        return bool(_OVERRIDE_LINE_RE.search(auth_file.read_text()))
    except OSError:
        return False


def _open_prs() -> list[dict] | None:
    """Return the repo's open PRs as ``[{number, headRefName, headRefOid}, ...]``.

    Returns ``None`` when ``gh`` is unreachable (the fail-open signal): the caller
    skips the remote ancestry check but still runs the local detached-HEAD check.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,headRefName,headRefOid"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(_REPO_ROOT),
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "[]")
        return data if isinstance(data, list) else None
    except Exception:
        return None


def _head_sha() -> str | None:
    rc, out = _git(["rev-parse", "HEAD"])
    return out if rc == 0 and out else None


def _head_is_detached() -> bool:
    """True IFF HEAD is detached (not on a named branch)."""
    rc, out = _git(["symbolic-ref", "-q", "HEAD"])
    # symbolic-ref exits non-zero (no output) when HEAD is detached.
    return rc != 0 or not out


def _local_branch_tips() -> dict[str, str]:
    """Map local branch name -> tip sha, excluding ``main``."""
    rc, out = _git(["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/"])
    tips: dict[str, str] = {}
    if rc != 0:
        return tips
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] != "main":
            tips[parts[0]] = parts[1]
    return tips


def _refuse(name: str, message: str) -> int:
    print(f"[ERROR] {name}: {message}", file=sys.stderr)
    return 1


def _pushes_to_main(argv: list[str], stdin_text: str) -> str | None:
    """Return the local SHA being pushed to ``refs/heads/main``, or None if none.

    Reads the git pre-push stdin protocol (``<local ref> <local sha> <remote ref>
    <remote sha>`` per line). When stdin is empty (explicit skill call), falls back
    to the current HEAD as the pushed SHA (the skill only calls this right before a
    ``git push origin main``).
    """
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            _local_ref, local_sha, remote_ref, _remote_sha = parts
            if remote_ref == MAIN_REF:
                # A branch deletion pushes the all-zero sha; nothing to guard.
                if set(local_sha) == {"0"}:
                    return None
                return local_sha
    # No stdin protocol lines: explicit call. Treat HEAD as the pushed sha.
    if not stdin_text.strip():
        return _head_sha()
    return None


def check(pushed_sha: str) -> int:
    """Core guard: refuse (1) or allow (0) a push of ``pushed_sha`` to main.

    Fail-closed on an open-PR ancestry match; fail-open on a ``gh`` outage (but the
    local detached-HEAD check still fires).
    """
    open_prs = _open_prs()

    if open_prs is None:
        # gh unreachable: fail open for the remote query, but a detached HEAD sitting
        # exactly at a non-main local branch tip is the #2026 shape — refuse locally.
        if _head_is_detached():
            head = _head_sha()
            for branch, tip in _local_branch_tips().items():
                if head and tip == head:
                    return _refuse(
                        ERR_DETACHED,
                        f"HEAD is detached exactly at local branch '{branch}' tip ({head[:12]}); "
                        "a push to main here would register that branch's ancestry as a merge. "
                        "Checkout main (or merge through `gh pr merge`) before pushing.",
                    )
        print(
            "[warn] push_ancestry_guard: gh unreachable — skipped open-PR ancestry check "
            "(local detached-HEAD check passed). Allowing push.",
            file=sys.stderr,
        )
        return 0

    for pr in open_prs:
        head_oid = pr.get("headRefOid")
        number = pr.get("number")
        if not head_oid or not number:
            continue
        if _is_ancestor(head_oid, pushed_sha):
            if _pr_authorized(int(number)):
                print(
                    f"[warn] push_ancestry_guard: push carries open PR #{number} ancestry but "
                    f"data/merge_authorized_{number} authorizes it — allowing.",
                    file=sys.stderr,
                )
                continue
            return _refuse(
                ERR_ANCESTRY,
                f"push to main carries the ancestry of OPEN PR #{number} "
                f"(head {head_oid[:12]}, branch '{pr.get('headRefName')}'). A plain push must "
                "not register an open PR's branch as its merge — run `gh pr merge --squash` "
                f"through the gate, or write 'override: <reason>' to "
                f"data/merge_authorized_{number} to break glass.",
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI / pre-push hook entry point.

    Reads the git pre-push stdin protocol; acts only on a push to ``refs/heads/main``.
    Returns 0 (allow) or 1 (refuse). A malformed/absent push target is a no-op (0).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin_text = ""
    if not sys.stdin.isatty():
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""

    pushed_sha = _pushes_to_main(argv, stdin_text)
    if not pushed_sha:
        # Not pushing to main (or nothing to push): nothing to guard.
        return 0
    return check(pushed_sha)


if __name__ == "__main__":
    sys.exit(main())
