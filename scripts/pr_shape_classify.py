#!/usr/bin/env python3
"""PR-shape classifier for shape-aware merge gates.

Classifies a pull request's diff into one of:

- ``docs-only``    : only docs/markdown files touched
- ``lockfile-only``: strictly the literal file ``uv.lock`` touched
- ``small-patch``  : <=20 net lines, no new/deleted files, every touched
                     ``*.py`` file maps to >=1 existing test
- ``mixed``        : diff looks like a partial safe shape (>=50% files
                     match a safe-shape allowlist) but has disqualifiers
- ``feature``      : everything else (the safe default; full gate stack runs)

The classifier is invoked from ``.claude/commands/do-merge.md`` via:

    python -m scripts.pr_shape_classify --pr N

and emits a JSON object on stdout::

    {
      "shape": "<one of the above>",
      "allowlist_used": "<allowlist name>" | null,
      "disqualifiers": ["path/a", ...],
      "claimed_shape": "<safe shape>" | null,   # only when shape == "mixed"
      "log_line": "<one-line summary>",
      "tests_to_run": ["tests/..."]              # only for small-patch
    }

It NEVER raises on malformed input -- on any ambiguity it returns
``feature`` (the safe direction). The only non-zero exit is mode 2
(``--diff-from``/``--diff-to``) when a SHA is missing from local objects;
the caller is responsible for fetching it.

See ``docs/features/pr-shape-aware-merge-gates.md`` for the gate matrix
and the relationship to ``docs/features/merge-gate-baseline.md``.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shape constants -- defended values, see plan §Solution and Risk 6.
# ---------------------------------------------------------------------------

# All files matching any of these globs are documentation. ``*.md`` is
# anchored with ``**/`` so it matches at any depth.
DOCS_ONLY_GLOBS: tuple[str, ...] = (
    "docs/**",
    "docs/**/*",
    "**/*.md",
    "*.md",
    "CHANGELOG*",
    "README*",
    ".env.example",  # doc-like: never loaded at runtime; exact filename match, not a wildcard
)

# ``lockfile-only`` is strictly the literal file ``uv.lock``. A
# ``pyproject.toml`` change can swap a runtime dep, so it must NOT be
# admitted (Open Question 2 in the plan).
LOCKFILE_ONLY_FILES: frozenset[str] = frozenset({"uv.lock"})

# Net-line budget for ``small-patch`` shape. Conservative based on
# sample of recent merged PRs (Open Question 3).
SMALL_PATCH_LINE_BUDGET: int = 20

# Substring-mapping safety constants (Risk 6).
SHORT_STEM_THRESHOLD: int = 4
SUBSTRING_MATCH_CAP: int = 8

# Decision order for safe-shape claims when detecting "mixed".
SAFE_SHAPES_ORDER: tuple[str, ...] = ("docs-only", "lockfile-only", "small-patch")


# ---------------------------------------------------------------------------
# Dataclass for the classifier's result.
# ---------------------------------------------------------------------------


@dataclass
class ClassifierResult:
    shape: str
    allowlist_used: str | None = None
    disqualifiers: list[str] = field(default_factory=list)
    claimed_shape: str | None = None
    log_line: str = ""
    tests_to_run: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "shape": self.shape,
            "allowlist_used": self.allowlist_used,
            "disqualifiers": list(self.disqualifiers),
            "log_line": self.log_line,
        }
        if self.claimed_shape is not None:
            d["claimed_shape"] = self.claimed_shape
        if self.tests_to_run:
            d["tests_to_run"] = list(self.tests_to_run)
        return d


# ---------------------------------------------------------------------------
# Allowlist helpers.
# ---------------------------------------------------------------------------


def _matches_docs(path: str) -> bool:
    """Return True if ``path`` matches any of ``DOCS_ONLY_GLOBS``.

    A path is "docs" when it sits under ``docs/`` OR when its basename
    is a markdown / changelog / readme file. ``.py``/``.toml``/``.lock``
    files are excluded explicitly so a file like ``foo.py.md`` (would-be
    classifier confuser) still classifies as a doc only when its full
    extension chain is ``.md``.
    """
    if path.endswith((".py", ".toml", ".lock")):
        return False
    for pat in DOCS_ONLY_GLOBS:
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def _matches_lockfile(path: str) -> bool:
    return path in LOCKFILE_ONLY_FILES


def _matches_small_patch_eligible(path: str) -> bool:
    """Files eligible for the small-patch shape are *existing* code/test
    files. We check existence on disk in :func:`map_to_tests`; here we
    only filter out files we know cannot be small-patches (e.g. binary
    blobs in ``data/``, generated fixtures).
    """
    return not (path.startswith("data/") or path.startswith(".worktrees/"))


_ALLOWLIST_FNS: dict[str, callable] = {
    "docs-only": _matches_docs,
    "lockfile-only": _matches_lockfile,
    "small-patch": _matches_small_patch_eligible,
}


def partition_by_allowlist(files: list[str], shape: str) -> tuple[list[str], list[str]]:
    """Return ``(matched, unmatched)`` for the given safe shape's allowlist."""
    fn = _ALLOWLIST_FNS[shape]
    matched, unmatched = [], []
    for f in files:
        (matched if fn(f) else unmatched).append(f)
    return matched, unmatched


def detect_mixed(changed_files: list[str]) -> tuple[str, list[str]] | None:
    """Detect "claimed safe shape with disqualifiers" via 50%-majority match.

    Returns ``(claimed_shape, disqualifying_files)`` if the PR looks like a
    partial safe shape, else ``None``.

    A shape is "claimed" when >=50% of changed files match its allowlist
    AND >=1 file violates it. The 50% threshold prevents trivial single-file
    feature PRs from being classified as "mixed" (e.g. a 1-file Python
    change isn't a "claimed docs-only PR" just because the file isn't a
    doc). It also prevents a single doc edit attached to a 50-file refactor
    from looking like a "claimed docs-only" -- the docs are the minority.

    See plan §Solution -> Classifier `mixed` detection for the full
    rationale and worked examples.
    """
    if not changed_files:
        return None
    for shape in SAFE_SHAPES_ORDER:
        matched, unmatched = partition_by_allowlist(changed_files, shape)
        if len(matched) >= len(changed_files) / 2 and unmatched:
            return (shape, sorted(unmatched))
    return None


# ---------------------------------------------------------------------------
# Touched-file -> test mapping (Risk 6).
# ---------------------------------------------------------------------------


def map_to_tests(touched_files: list[str], repo_root: Path) -> list[str] | None:
    """Map touched .py files to existing test files.

    Two-tier strategy:
      Tier 1 -- exact-name match: ``tests/**/test_{stem}.py``. Always tried first.
      Tier 2 -- substring match: ``tests/**/test_*{stem}*.py``. Only used as fallback
               when Tier 1 finds nothing AND the stem is long enough to be
               reasonably specific (>= ``SHORT_STEM_THRESHOLD`` characters).

    Short stems (e.g. "io", "db", "fs") are NOT eligible for substring matching --
    they would over-match wildly. Short-stem files with no Tier 1 match return
    ``None`` -> classifier falls back to ``feature`` shape.

    Per-file substring-match safety cap: if Tier 2 returns more than
    ``SUBSTRING_MATCH_CAP`` test files for one source file, the classifier
    treats it as "ambiguous mapping" and returns ``None`` -- the cap rejects
    the small-patch shape rather than running an unbounded test set.

    Returns the sorted unique list of test files, or ``None`` if any touched
    file fails to map.
    """
    tests: list[str] = []
    for f in touched_files:
        # Skip non-Python files (docs/lockfile in mixed-shape PRs would not
        # reach here -- this is purely defensive for callers who pass mixed
        # input to the helper directly).
        if not f.endswith(".py"):
            continue

        stem = Path(f).stem
        if stem.startswith("_") or stem == "__init__":
            return None  # private helpers and package markers -> fall back to feature

        # Tier 1: exact-name match
        exact = list(repo_root.glob(f"tests/**/test_{stem}.py"))
        if exact:
            tests.extend(str(c.relative_to(repo_root)) for c in exact)
            continue

        # Tier 2: substring match (long stems only)
        if len(stem) < SHORT_STEM_THRESHOLD:
            return None  # short stem with no exact match -> ambiguous -> fall back

        substring = list(repo_root.glob(f"tests/**/test_*{stem}*.py"))
        if not substring:
            return None
        if len(substring) > SUBSTRING_MATCH_CAP:
            # Over-match -- running 9+ tests for one file source means our
            # glob is no longer "targeted." Fall back to feature shape and
            # let the full suite run.
            return None
        tests.extend(str(c.relative_to(repo_root)) for c in substring)
    return sorted(set(tests))


# ---------------------------------------------------------------------------
# Pure-function classifier.
# ---------------------------------------------------------------------------


def classify(
    changed_files: list[str],
    net_lines: int,
    has_new: bool,
    has_deleted: bool,
    repo_root: Path | None = None,
) -> ClassifierResult:
    """Return the shape verdict for the given diff signature.

    Side-effect-free except for the test-mapping ``repo_root.glob`` lookup
    used by the small-patch shape. ``repo_root`` defaults to ``cwd`` when
    omitted; pass it explicitly in tests.
    """
    repo_root = repo_root or Path.cwd()

    # Defensive: empty or whitespace-only file list -> feature.
    files = [f for f in (changed_files or []) if f and f.strip()]
    if not files:
        return ClassifierResult(
            shape="feature",
            log_line="SHAPE: feature -- empty file list (defensive default)",
        )

    # 1. docs-only
    docs_matched, docs_unmatched = partition_by_allowlist(files, "docs-only")
    if not docs_unmatched and docs_matched:
        return ClassifierResult(
            shape="docs-only",
            allowlist_used="docs-only",
            log_line=f"SHAPE: docs-only ({len(docs_matched)} file(s))",
        )

    # 2. lockfile-only -- strictly == LOCKFILE_ONLY_FILES.
    if frozenset(files) == LOCKFILE_ONLY_FILES:
        return ClassifierResult(
            shape="lockfile-only",
            allowlist_used="lockfile-only",
            log_line="SHAPE: lockfile-only (uv.lock only)",
        )

    # 3. small-patch
    small_patch_disqualifiers: list[str] = []
    if has_new:
        small_patch_disqualifiers.append("new file(s) created")
    if has_deleted:
        small_patch_disqualifiers.append("file(s) deleted")
    if net_lines > SMALL_PATCH_LINE_BUDGET:
        small_patch_disqualifiers.append(
            f"net_lines={net_lines} > budget={SMALL_PATCH_LINE_BUDGET}"
        )

    if not small_patch_disqualifiers:
        # All touched files must exist on disk (rules out new files even
        # if has_new was wrong) AND map to >=1 existing test.
        all_exist = all((repo_root / f).exists() for f in files)
        if all_exist:
            tests = map_to_tests(files, repo_root)
            if tests is not None and tests:
                return ClassifierResult(
                    shape="small-patch",
                    allowlist_used="small-patch",
                    log_line=(
                        f"SHAPE: small-patch ({len(files)} file(s), net_lines={net_lines}, "
                        f"{len(tests)} mapped test(s))"
                    ),
                    tests_to_run=tests,
                )

    # 4. mixed
    mixed = detect_mixed(files)
    if mixed is not None:
        claimed, disqualifiers = mixed
        return ClassifierResult(
            shape="mixed",
            allowlist_used=None,
            disqualifiers=disqualifiers,
            claimed_shape=claimed,
            log_line=(
                f"SHAPE: mixed -- claimed safe shape '{claimed}' touched "
                f"non-allowlisted paths: {disqualifiers}"
            ),
        )

    # 5. default
    return ClassifierResult(
        shape="feature",
        log_line=f"SHAPE: feature ({len(files)} file(s); full gate stack)",
    )


# ---------------------------------------------------------------------------
# CLI plumbing -- two modes.
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _read_diff_from_pr(pr: str) -> tuple[list[str], int, bool, bool]:
    """Use ``gh pr diff --name-only`` + ``gh pr view`` to gather diff signature.

    Returns ``(files, net_lines, has_new, has_deleted)``. On any failure
    returns an empty/zero tuple so the classifier defaults to ``feature``.
    """
    try:
        files_p = _run(["gh", "pr", "diff", "--name-only", pr], check=False)
        if files_p.returncode != 0:
            logger.warning("[pr_shape_classify] gh pr diff failed: %s", files_p.stderr)
            return ([], 0, False, False)
        files = [ln.strip() for ln in files_p.stdout.splitlines() if ln.strip()]

        # additions/deletions/headRefOid via gh pr view --json
        view_p = _run(
            [
                "gh",
                "pr",
                "view",
                pr,
                "--json",
                "additions,deletions,files",
            ],
            check=False,
        )
        if view_p.returncode != 0:
            logger.warning("[pr_shape_classify] gh pr view failed: %s", view_p.stderr)
            return (files, 0, False, False)
        data = json.loads(view_p.stdout) if view_p.stdout.strip() else {}
        additions = int(data.get("additions") or 0)
        deletions = int(data.get("deletions") or 0)
        net_lines = additions + deletions

        # Has-new / has-deleted detection via gh pr view --json files
        # (each file entry has ``additions`` and ``deletions``; a new file
        # has ``deletions == 0`` and was not on base, but gh doesn't expose
        # that flag directly. Fall back to ``git diff --diff-filter`` if a
        # local checkout is available.)
        has_new = False
        has_deleted = False
        try:
            # Best-effort: use git diff --name-status against origin/main
            base = _run(["gh", "pr", "view", pr, "--json", "baseRefName"], check=False)
            if base.returncode == 0 and base.stdout.strip():
                base_branch = json.loads(base.stdout).get("baseRefName") or "main"
                head = _run(["gh", "pr", "view", pr, "--json", "headRefOid"], check=False)
                if head.returncode == 0 and head.stdout.strip():
                    head_sha = json.loads(head.stdout).get("headRefOid")
                    if head_sha:
                        # Try local diff (requires the SHAs to be fetched)
                        ns = _run(
                            [
                                "git",
                                "diff",
                                "--name-status",
                                f"origin/{base_branch}...{head_sha}",
                            ],
                            check=False,
                        )
                        if ns.returncode == 0:
                            for line in ns.stdout.splitlines():
                                parts = line.split("\t", 1)
                                if not parts:
                                    continue
                                status = parts[0].strip()
                                if status.startswith("A"):
                                    has_new = True
                                elif status.startswith("D"):
                                    has_deleted = True
        except Exception as exc:  # pragma: no cover -- defensive
            logger.debug("[pr_shape_classify] new/deleted detection skipped: %s", exc)

        return (files, net_lines, has_new, has_deleted)
    except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        logger.warning("[pr_shape_classify] PR diff read failed: %s", exc)
        return ([], 0, False, False)


def _read_diff_from_shas(sha_from: str, sha_to: str) -> tuple[list[str], int, bool, bool] | None:
    """Use ``git diff --name-status`` + ``git diff --shortstat`` between SHAs.

    Returns ``(files, net_lines, has_new, has_deleted)`` or ``None`` when a
    SHA is missing from the local objects database. The CLI converts that
    None into exit-2.
    """
    # Both SHAs must exist locally.
    for sha in (sha_from, sha_to):
        ok = _run(["git", "cat-file", "-e", sha], check=False)
        if ok.returncode != 0:
            print(
                f"SHA {sha} not in local objects; run `git fetch origin {sha}` first",
                file=sys.stderr,
            )
            return None

    rng = f"{sha_from}..{sha_to}"
    ns = _run(["git", "diff", "--name-status", rng], check=False)
    if ns.returncode != 0:
        logger.warning("[pr_shape_classify] git diff --name-status failed: %s", ns.stderr)
        return ([], 0, False, False)

    files: list[str] = []
    has_new = False
    has_deleted = False
    for line in ns.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        path = parts[-1].strip()
        files.append(path)
        if status.startswith("A"):
            has_new = True
        elif status.startswith("D"):
            has_deleted = True

    ss = _run(["git", "diff", "--shortstat", rng], check=False)
    net_lines = 0
    if ss.returncode == 0:
        # Format: " 3 files changed, 12 insertions(+), 4 deletions(-)"
        for tok in ss.stdout.split(","):
            tok = tok.strip()
            if "insertion" in tok or "deletion" in tok:
                num = tok.split()[0]
                try:
                    net_lines += int(num)
                except ValueError:
                    pass
    return (files, net_lines, has_new, has_deleted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a PR's diff into a merge-gate shape "
            "(docs-only, lockfile-only, small-patch, mixed, feature)."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", help="PR number to classify")
    group.add_argument("--diff-from", help="Source SHA for diff-mode classification")
    parser.add_argument("--diff-to", help="Target SHA (required with --diff-from)")
    parser.add_argument("--repo-root", default=None, help="Override repo root (tests)")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    if args.pr:
        if shutil.which("gh") is None:
            print("gh CLI not available", file=sys.stderr)
            return 2
        files, net_lines, has_new, has_deleted = _read_diff_from_pr(args.pr)
    else:
        if not args.diff_to:
            parser.error("--diff-to is required with --diff-from")
        result = _read_diff_from_shas(args.diff_from, args.diff_to)
        if result is None:
            return 2
        files, net_lines, has_new, has_deleted = result

    verdict = classify(
        changed_files=files,
        net_lines=net_lines,
        has_new=has_new,
        has_deleted=has_deleted,
        repo_root=repo_root,
    )
    print(json.dumps(verdict.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
