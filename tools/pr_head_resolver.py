"""Authoritative, cache-immune resolution of a PR's head commit SHA (#2404).

The SDLC verdict-staleness gate (#2062) compares a recorded REVIEW verdict's
``REVIEW_CONTEXT head_sha=`` trailer against the PR's *current* head commit to
decide whether an approval still applies to the code in front of it. If the
current-head read is stale in the fail-open direction -- returning the pre-push
value, which is exactly the value the trailer already carries -- the gate sees a
match and passes a verdict that predates newly pushed code. A path deliberately
designed fail-closed becomes fail-open, silently.

The robust fix is to resolve the current head over the **git** transport
(``git ls-remote origin refs/pull/{N}/head``), which shares no response cache
with ``gh``'s HTTP layer and is authoritative for the ref. This is the same
"reconcile against observable git ground truth" direction #2395's recovery path
took, and it needs no per-machine configuration (no ``GH_*`` env var to drift
out of place across bridge / worker / launchd / local / subagent contexts).

Empirical note (gh 2.89.0): the on-disk ``gh`` cache (``~/.cache/gh``) is
written only by ``gh api --cache <ttl>``; bare ``gh pr view --json`` /
``gh api`` do not touch it, and this repo passes ``--cache`` nowhere. So the
gating reads never hit the disk cache. The residual staleness this module
guards against is GitHub's server-side eventual consistency in the
seconds-to-minutes window after a push -- which a local cache flag cannot fix
but an authoritative git read sidesteps.

Stdlib-only on purpose: ``tools/merge_predicate.py`` is stdlib-only (it must
load under any interpreter for the merge-guard hook and avoids
``tools._sdlc_utils`` because that pulls in ``models``), so it can import this
module at module level.
"""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

_SHA_RE = re.compile(r"\b([0-9a-fA-F]{40})\b")
# Provisional/tunable: a git ls-remote or gh subprocess for a single ref is
# fast; 10s is a generous ceiling that fails a wedged network call closed rather
# than hanging the gate. No env override -- this is a fixed infra guard, not a
# behavior knob (matches the 10s used by the callers being consolidated here).
_SUBPROCESS_TIMEOUT = 10


def _origin_matches_repo(repo: str | None, repo_root: str | None) -> bool:
    """Whether the local ``origin`` remote points at ``repo`` (``owner/name``).

    Guards the git-authoritative read against a cross-repo cwd hazard: callers
    like ``sdlc_review_finalize`` run with cwd forced to ``~/src/ai``, so a bare
    ``git ls-remote origin`` there would target the ai repo even when the PR
    lives in another repo. When ``repo`` is ``None`` the caller is responsible
    for pointing ``repo_root`` at the correct checkout (the same contract the
    old code had), so we trust the local origin.
    """
    if not repo:
        return True
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=repo_root,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if proc.returncode != 0:
        return False
    url = (proc.stdout or "").strip().removesuffix(".git")
    # Match "owner/name" as a path component of the origin URL (ssh or https).
    return url.endswith(f"/{repo}") or url.endswith(f":{repo}")


def _git_ls_remote_pr_head(pr: int, repo: str | None, repo_root: str | None) -> str | None:
    """Authoritative head SHA via ``git ls-remote origin refs/pull/{pr}/head``.

    Queries the remote directly over the git transport (no dependence on local
    fetch refspec, no shared cache with ``gh``). Returns the 40-hex SHA, or
    ``None`` when the local ``origin`` does not point at ``repo``, there is no
    ``origin`` remote, the ref is absent, or the call fails.
    """
    if not _origin_matches_repo(repo, repo_root):
        return None
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/pull/{int(pr)}/head"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=repo_root,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("git ls-remote for PR #%s failed: %s", pr, e)
        return None
    if proc.returncode != 0:
        return None
    match = _SHA_RE.search(proc.stdout or "")
    return match.group(1) if match else None


def _gh_pr_head(pr: int, repo: str | None) -> str | None:
    """Fallback head SHA via ``gh pr view --json headRefOid``.

    Used when the authoritative git read yields nothing (no ``origin`` remote,
    or a cross-repo ``GH_REPO`` checkout where ``origin`` is a different repo).
    ``gh pr view --json`` does not touch gh's disk cache on gh 2.89.0.
    """
    cmd = ["gh", "pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid"]
    if repo:
        cmd = [
            "gh",
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "headRefOid",
            "-q",
            ".headRefOid",
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("gh pr view headRefOid for PR #%s failed: %s", pr, e)
        return None
    if proc.returncode != 0:
        return None
    match = _SHA_RE.search(proc.stdout or "")
    return match.group(1) if match else None


def resolve_pr_head_sha(
    pr: int,
    repo: str | None = None,
    repo_root: str | None = None,
    *,
    cross_check: bool = True,
) -> str | None:
    """Resolve a PR's head commit SHA, git-first and cache-immune (#2404).

    Order:
      1. ``git ls-remote origin refs/pull/{pr}/head`` -- authoritative, shares
         no cache with ``gh``. Used whenever it resolves.
      2. ``gh pr view --json headRefOid`` -- fallback when (1) yields nothing.

    ``cross_check`` (default on): when both sources resolve and **disagree**,
    the git value wins (authoritative) and a WARNING is logged naming both SHAs
    -- this surfaces a fail-open staleness event rather than hiding it. Set
    ``cross_check=False`` to skip the extra gh call once git has answered (the
    gate does not need the second read to be correct; the cross-check is
    observability).

    Returns the 40-hex SHA, or ``None`` when neither source resolves one --
    callers must treat ``None`` as "could not determine" and fail closed, never
    invent a SHA.
    """
    git_sha = _git_ls_remote_pr_head(pr, repo, repo_root)
    if git_sha and not cross_check:
        return git_sha

    gh_sha = _gh_pr_head(pr, repo)

    if git_sha:
        if gh_sha and gh_sha.lower() != git_sha.lower():
            logger.warning(
                "PR #%s head-SHA disagreement: git ls-remote=%s vs gh=%s -- "
                "using authoritative git value (possible gh read-staleness)",
                pr,
                git_sha,
                gh_sha,
            )
        return git_sha

    return gh_sha
