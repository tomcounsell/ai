"""Classify post-review head drift as documentation-only or code (#3228).

**The defect this closes.** ``sdlc-tool verdict finalize`` pins the REVIEW
verdict to the head SHA the reviewer read, and every consumer then compares
that SHA to the PR's *live* head with strict equality
(``tools.sdlc_review_finalize.check_review_persistence`` and
``tools.merge_predicate._check_verdict_freshness``). But ``/do-docs`` is a
MANDATORY pipeline stage that runs AFTER review and commits, so the live head
has moved by the time ``/do-merge`` evaluates the gate. The mismatch is
structural rather than exceptional: it fired on every lane, and the only
correct response to it (never re-run ``finalize``, because minting APPROVED
against an uninspected head is self-clearing) is a human escalation. Routine
merges became manual authorizations.

**What this module changes, and what it deliberately does not.** A post-review
commit whose entire diff is documentation does not invalidate a code review,
so it stops counting as staleness. Everything else still fails closed:

- a changed path outside :data:`DOCS_ONLY_PREFIXES` is ``"code"`` — including a
  docstring fix inside a source file. That file was not in the reviewed diff,
  and the honest remedy is to re-run REVIEW, not to widen the rule.
- a head that does not strictly DESCEND from the reviewed SHA is ``"code"``.
  This is the force-push / rebase case: the reviewed commit is no longer in the
  history, so "only docs changed since" is not even a well-formed claim. A
  tree-hash-over-non-docs-paths scheme (the other direction considered in
  #3228) would have waved exactly that case through.
- any error, timeout, unparseable payload, missing field, or truncated file
  list is ``"unknown"``, which callers treat exactly like ``"code"``.

**Why the compare API rather than git.** The two call sites run from different
working directories (the merge-guard hook from the target repo, ``selfcheck``
from ``~/src/ai`` under #2377 Mode 1) and neither is guaranteed to hold a
checkout with both SHAs fetched. ``gh api repos/{repo}/compare/{base}...{head}``
needs no local objects and returns the ancestry verdict (``status``) and the
changed-path list in one call. Unlike the head-SHA read in #2404, a stale
response here cannot fail OPEN: this module only ever *narrows* an
already-detected mismatch, and a stale file list that omits a code change would
have to also report ``status: "ahead"`` for the same range — at which point the
worst case is the same tolerance a correct response would grant one commit
later. The gate it feeds is still backed by the authoritative head read.

This module is stdlib-only on purpose: ``tools/merge_predicate.py`` is loaded by
the merge-guard hook under an arbitrary interpreter and documents that posture.
"""

from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 20

# The GitHub compare API returns at most 300 file entries. A range at or above
# that cap may be silently truncated, and a truncated list cannot prove
# docs-only. See _classify_files.
_COMPARE_FILE_CAP = 300

# Directory prefixes whose contents are documentation.
#
# The test is "could this file change what an agent or program DOES?" A file
# that is read as instructions at runtime is code for review purposes, however
# much it looks like prose. So the rule accepts prose directories and carves
# out every documentation-shaped path that is actually an instruction surface:
#
# - `docs/sdlc/` is excluded from `docs/`: those files are the repo-specific
#   addenda the SDLC skills load and follow (docs/features/skill-context-convention.md).
# - `CLAUDE.md` / `AGENTS.md` are excluded from top-level `*.md`: agent harnesses
#   load them as standing instructions, and the worker parses CLAUDE.md headings
#   into prompts.
# - `.claude/` is never documentation (skills, commands, agents, hooks), nor are
#   `mkdocs.yml`, `.github/`, or `tests/`.
#
# A post-review edit to any excluded path is "code": the verdict goes stale and
# REVIEW re-runs, which is the strict-equality behaviour this module otherwise
# relaxes. Erring toward re-review is the fail-closed direction.
DOCS_ONLY_PREFIXES = ("docs/",)
_INSTRUCTION_PREFIXES = ("docs/sdlc/",)
_INSTRUCTION_TOP_LEVEL = frozenset({"CLAUDE.md", "AGENTS.md"})


def is_docs_only_path(path: str) -> bool:
    """Return True iff ``path`` is documentation by the rule above.

    Accepts anything under :data:`DOCS_ONLY_PREFIXES` except the instruction
    surfaces carved out of it, plus any TOP-LEVEL ``*.md`` (``README.md``,
    ``CHANGELOG.md``) except ``CLAUDE.md`` / ``AGENTS.md``. The top-level
    restriction is the point: a nested ``*.md`` is waved through only when its
    directory is already a documentation directory, so a behaviour-bearing
    ``.claude/skills/x/SKILL.md`` is code.
    """
    if not isinstance(path, str) or not path:
        return False
    if path.startswith(_INSTRUCTION_PREFIXES) or path in _INSTRUCTION_TOP_LEVEL:
        return False
    if path.startswith(DOCS_ONLY_PREFIXES):
        return True
    return "/" not in path and path.endswith(".md")


def _classify_files(files: list) -> str:
    """Classify a compare-API ``files`` array. Never raises."""
    if not isinstance(files, list):
        return "unknown"
    if not files:
        # `status: ahead` with an empty file list means the range changed no
        # files at all (an empty or revert-pair commit). Nothing to review.
        return "docs_only"
    if len(files) >= _COMPARE_FILE_CAP:
        # Possibly truncated — a docs-only claim would be unprovable.
        logger.warning(
            "sdlc_review_drift: compare returned %d files (>= cap %d); "
            "refusing to classify a possibly-truncated list",
            len(files),
            _COMPARE_FILE_CAP,
        )
        return "unknown"
    for entry in files:
        if not isinstance(entry, dict):
            return "unknown"
        name = entry.get("filename")
        if not isinstance(name, str) or not name:
            return "unknown"
        if not is_docs_only_path(name):
            return "code"
        # A rename moves two paths; both ends must be documentation.
        previous = entry.get("previous_filename")
        if isinstance(previous, str) and previous and not is_docs_only_path(previous):
            return "code"
    return "docs_only"


def classify_head_drift(
    base_sha: str,
    head_sha: str,
    repo: str,
    repo_root: str | None = None,
) -> str:
    """Classify the drift from ``base_sha`` (reviewed) to ``head_sha`` (live).

    Args:
        base_sha: the SHA the REVIEW verdict was recorded against.
        head_sha: the PR's current head SHA.
        repo: ``owner/name`` of the repository holding both commits.
        repo_root: optional cwd for the ``gh`` call (credential/host resolution).

    Returns one of:

    - ``"identical"`` — the two SHAs are the same commit. No drift.
    - ``"docs_only"`` — ``head_sha`` strictly descends from ``base_sha`` and
      every path changed in between is documentation.
    - ``"code"`` — a non-documentation path changed, or the two commits are not
      in an ancestor relationship (force-push / divergence).
    - ``"unknown"`` — the classification could not be made. **Callers MUST
      treat this exactly like** ``"code"``; it is a separate value only so the
      operator-facing message can distinguish "reviewed code changed" from
      "could not tell", never so a caller can be lenient about it.
    """
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        return "unknown"
    if not base_sha or not head_sha:
        return "unknown"
    if base_sha.lower() == head_sha.lower():
        return "identical"
    if not repo:
        return "unknown"

    cmd = ["gh", "api", f"repos/{repo}/compare/{base_sha}...{head_sha}"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=repo_root or None,
        )
    except Exception as e:
        # Timeout, missing `gh`, OS error. Fail closed via "unknown".
        logger.warning(
            "sdlc_review_drift: compare %s...%s in %s failed to run (%s: %s)",
            base_sha[:7],
            head_sha[:7],
            repo,
            type(e).__name__,
            e,
        )
        return "unknown"

    if proc.returncode != 0:
        logger.warning(
            "sdlc_review_drift: gh compare %s...%s in %s exited %d: %s",
            base_sha[:7],
            head_sha[:7],
            repo,
            proc.returncode,
            (proc.stderr or "").strip()[:200],
        )
        return "unknown"

    try:
        payload = json.loads(proc.stdout or "")
    except Exception:
        logger.warning("sdlc_review_drift: gh compare returned unparseable JSON")
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"

    status = payload.get("status")
    if status == "identical":
        # Different SHAs pointing at the same tree state still means the
        # reviewed commit is not the head; treat as ancestry-clean drift with
        # no files, which _classify_files reads as docs_only.
        return _classify_files(payload.get("files") or [])
    if status != "ahead":
        # "behind" (head is an ANCESTOR of the reviewed SHA — the recorded
        # verdict judged something that is no longer reachable) and "diverged"
        # (force-push / rebase) both mean the reviewed commit is not in the
        # head's history. Never tolerated.
        logger.warning(
            "sdlc_review_drift: compare status %r for %s...%s in %s is not 'ahead'",
            status,
            base_sha[:7],
            head_sha[:7],
            repo,
        )
        return "code"

    return _classify_files(payload.get("files"))
