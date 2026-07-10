"""Shared terminal merge predicate (issue #2003).

One deterministic predicate evaluated by BOTH the merge-guard hook
(``.claude/hooks/validators/validate_merge_guard.py``) and the ``/do-merge``
skill (via ``docs/sdlc/do-merge.md``). Consuming a single helper is what keeps
the hook and the skill from drifting apart (#1944 class).

Three check groups:

- **Group (a) — PR state** (always enforced, fail-closed on any ``gh`` error):
  state OPEN, mergeable MERGEABLE, mergeStateStatus CLEAN (or UNSTABLE with a
  green rollup), CI green (no FAILURE/ERROR; pending counts as not-green), and
  a word-boundary ``Closes/Fixes/Resolves #N`` issue link in the PR body.
- **Group (b) — DOCS stage gate** (substrate-present only): ``stages.DOCS ==
  completed`` passes; ``in_progress`` hard-fails; pending/empty degrades to a
  ``docs/features/{slug}.md`` existence check (slug from the PR head ref).
- **Group (c) — REVIEW verdict freshness** (substrate-present only): a recorded
  verdict must exist, contain APPROVED (case-insensitive), and be FRESH against
  the PR's latest commit — via the ``REVIEW_CONTEXT head_sha=`` trailer when
  present, else by comparing the verdict's ``recorded_at`` timestamp to the
  latest commit's committer date. A bare ``"APPROVED" in text`` check is
  explicitly insufficient (#2003 critique BLOCKER 2).

Ordered detection (cycle-2 CONCERN 3): the substrate is probed FIRST as a repo
property — present iff ``docs/sdlc/do-merge.md`` exists under the target repo
root AND ``sdlc-tool`` (or ``python -m tools.sdlc_stage_query``) is resolvable.
Substrate ABSENT → groups (b)/(c) skip with a logged notice; group (a) still
enforces. Substrate PRESENT but any predicate call raises / exits non-zero /
returns malformed output → FAIL CLOSED with a named check. An evaluation error
in a substrate-present repo is never misread as "foreign repo".

CLI::

    python -m tools.merge_predicate --pr-number 42 --json

Exit 0 iff the predicate allows the merge; 1 otherwise (2 on usage error).

Module-level imports are stdlib-only so the merge-guard hook can import this
under any interpreter; repo-internal helpers are imported lazily.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30

# Word-boundary GitHub closing keyword followed by #N. Mirrors
# tools.sdlc_stage_query._body_references_issue (the shared validator from
# PR #1998); the import is preferred at call time, this regex is the
# stdlib-only fallback for interpreters that cannot import the repo models.
_ISSUE_REF_CAPTURE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(\d+)(?!\d)",
    re.IGNORECASE,
)

# The stored verdict text may have passed through
# ``agent.sdlc_router.normalize_verdict`` (``sdlc-tool verdict record``
# uppercases and maps underscores to spaces), so the trailer must match both
# the raw ``REVIEW_CONTEXT head_sha=<hex>`` form the review skill emits and
# its normalized image ``REVIEW CONTEXT HEAD SHA=<HEX>``. SHA comparison is
# case-insensitive for the same reason.
_HEAD_SHA_TRAILER_RE = re.compile(
    r"REVIEW[_ ]CONTEXT\s+HEAD[_ ]SHA=([0-9A-Fa-f]{40})", re.IGNORECASE
)

# Head refs that can never yield a usable slug for the docs/features fallback.
_NO_SLUG_REFS = frozenset({"main", "master", "HEAD", ""})


@dataclass
class PredicateResult:
    """Structured outcome of one merge-predicate evaluation."""

    allowed: bool
    failed_checks: list[str] = field(default_factory=list)
    substrate_present: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Subprocess seams — small module-level functions tests monkeypatch.
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Default target repo root: git toplevel of the current working directory."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("cannot resolve repo root (git rev-parse --show-toplevel failed)")
    return Path(proc.stdout.strip())


def _sdlc_tool_resolvable(repo_root: Path) -> bool:
    """True when ``sdlc-tool`` (or the stage-query module) can be invoked."""
    if shutil.which("sdlc-tool") is not None:
        return True
    return (repo_root / "tools" / "sdlc_stage_query.py").is_file()


def _substrate_present(repo_root: Path) -> bool:
    """Probe the SDLC substrate as a REPO PROPERTY, before any evaluation.

    Present iff the repo ships the do-merge addendum AND the stage-query
    tooling is resolvable. This ordering is what distinguishes "foreign repo,
    skip groups b/c" from "substrate repo, evaluation error, fail closed".
    """
    addendum = repo_root / "docs" / "sdlc" / "do-merge.md"
    return addendum.is_file() and _sdlc_tool_resolvable(repo_root)


def _gh_pr_view(pr_number: int, repo_root: Path) -> dict:
    """Fetch PR state via ``gh pr view``. Raises on any failure."""
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,mergeable,mergeStateStatus,statusCheckRollup,reviewDecision,body,headRefName",
        ],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=repo_root,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr view exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return data


def _gh_latest_commit(pr_number: int, repo_root: Path) -> dict:
    """Return ``{"sha": ..., "date": ...}`` for the PR's latest commit.

    Raises on any failure — with the substrate present, missing latest-commit
    data must fail the predicate closed, never silently pass.
    """
    repo_proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=repo_root,
    )
    if repo_proc.returncode != 0 or not repo_proc.stdout.strip():
        raise RuntimeError("gh repo view failed while resolving repo name")
    repo = repo_proc.stdout.strip()
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/commits", "--jq", ".[-1]"],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=repo_root,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"gh api pulls/{pr_number}/commits exited {proc.returncode}")
    commit = json.loads(proc.stdout)
    if not isinstance(commit, dict):
        raise RuntimeError("latest-commit lookup returned non-object JSON")
    return {
        "sha": commit.get("sha") or "",
        "date": ((commit.get("commit") or {}).get("committer") or {}).get("date") or "",
    }


def _sdlc_tool_cmd(subcommand: list[str], repo_root: Path) -> list[str]:
    """Build the substrate invocation: prefer ``sdlc-tool``, else ``python -m``."""
    if shutil.which("sdlc-tool") is not None:
        return ["sdlc-tool", *subcommand]
    return [sys.executable, "-m", f"tools.sdlc_{subcommand[0].replace('-', '_')}", *subcommand[1:]]


def _run_stage_query(issue_number: int, repo_root: Path) -> dict:
    """Run ``sdlc-tool stage-query`` and return the parsed JSON payload."""
    cmd = _sdlc_tool_cmd(["stage-query", "--issue-number", str(issue_number)], repo_root)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, cwd=repo_root
    )
    if proc.returncode != 0:
        raise RuntimeError(f"stage-query exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("stage-query returned non-object JSON")
    return data


def _run_verdict_get(issue_number: int, repo_root: Path) -> dict:
    """Run ``sdlc-tool verdict get --stage REVIEW`` and return the parsed record."""
    cmd = _sdlc_tool_cmd(
        ["verdict", "get", "--stage", "REVIEW", "--issue-number", str(issue_number)], repo_root
    )
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, cwd=repo_root
    )
    if proc.returncode != 0:
        raise RuntimeError(f"verdict get exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("verdict get returned non-object JSON")
    return data


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_issue_number(body: str | None) -> int | None:
    """Extract the linked issue number from a Closes/Fixes/Resolves reference.

    Prefers the shared validator from ``tools.sdlc_stage_query`` (PR #1998)
    to confirm the match; falls back to the local mirror regex when that
    module is unimportable (e.g. hook interpreter without repo deps).
    """
    if not body:
        return None
    match = _ISSUE_REF_CAPTURE_RE.search(body)
    if not match:
        return None
    issue_number = int(match.group(1))
    try:
        from tools.sdlc_stage_query import _body_references_issue

        if not _body_references_issue(body, issue_number):
            return None
    except ImportError:
        pass  # local regex already validated the word-boundary reference
    return issue_number


def _derive_slug(head_ref: str) -> str:
    """Slug from a PR head ref: strip ``session/``; main/master/HEAD/empty → no slug."""
    slug = (head_ref or "").removeprefix("session/")
    if slug in _NO_SLUG_REFS:
        return ""
    return slug


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Check groups
# ---------------------------------------------------------------------------


def _check_pr_state(
    pr_number: int, repo_root: Path, failed: list[str]
) -> tuple[dict | None, int | None]:
    """Group (a): PR state. Always enforced; fail-closed on any gh error.

    Returns ``(pr_data, issue_number)`` — either may be None on failure.
    """
    try:
        pr = _gh_pr_view(pr_number, repo_root)
    except Exception as exc:
        failed.append(f"PR state unavailable (gh pr view failed: {exc})")
        return None, None

    state = pr.get("state")
    if state != "OPEN":
        failed.append(f"PR state is {state!r} (must be OPEN)")
    mergeable = pr.get("mergeable")
    if mergeable != "MERGEABLE":
        failed.append(f"PR mergeable is {mergeable!r} (must be MERGEABLE)")
    merge_state = pr.get("mergeStateStatus")
    if merge_state not in ("CLEAN", "UNSTABLE"):
        failed.append(f"PR mergeStateStatus is {merge_state!r} (must be CLEAN)")

    rollup = pr.get("statusCheckRollup") or []
    if not isinstance(rollup, list):
        failed.append("CI status rollup is malformed")
        rollup = []
    for check in rollup:
        if not isinstance(check, dict):
            continue
        name = check.get("name") or check.get("context") or "<check>"
        # CheckRun entries carry `conclusion`; StatusContext entries carry `state`.
        conclusion = (check.get("conclusion") or "").upper()
        status_state = (check.get("state") or "").upper()
        if conclusion in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") or status_state in (
            "FAILURE",
            "ERROR",
        ):
            failed.append(f"CI check {name!r} concluded {conclusion or status_state}")
        elif not conclusion and status_state != "SUCCESS":
            # In-flight check: no conclusion yet. Pending counts as not-green.
            failed.append(f"CI check {name!r} is still pending (not green)")

    issue_number = _extract_issue_number(pr.get("body"))
    if issue_number is None:
        failed.append("PR body lacks a Closes/Fixes/Resolves #N issue link")
    return pr, issue_number


def _check_docs_stage(
    issue_number: int,
    head_ref: str,
    repo_root: Path,
    failed: list[str],
    notes: list[str],
) -> None:
    """Group (b): DOCS stage gate (substrate-present only). Fail-closed on errors."""
    try:
        payload = _run_stage_query(issue_number, repo_root)
    except Exception as exc:
        failed.append(f"DOCS stage state unavailable (stage-query failed: {exc})")
        return

    stages = payload.get("stages")
    docs_status = (stages or {}).get("DOCS", "") if isinstance(stages, dict) else ""
    if docs_status == "completed":
        return
    if docs_status == "in_progress":
        # The sole affirmative "DOCS unfinished" signal (cuttlefish #577 shape).
        failed.append("DOCS stage in_progress")
        return

    # pending / empty stages: marker not authoritative — degrade to the
    # docs/features/{slug}.md existence check.
    shown = docs_status or "<empty>"
    slug = _derive_slug(head_ref)
    if not slug:
        failed.append(
            f"DOCS marker not authoritative (status={shown}) and no usable slug"
            " for the docs/features fallback"
        )
        return
    if (repo_root / "docs" / "features" / f"{slug}.md").is_file():
        notes.append(
            f"DOCS gate degraded pass: marker status={shown}, docs/features/{slug}.md present"
        )
        return
    failed.append(
        f"DOCS marker not authoritative (status={shown}) and docs/features/{slug}.md absent"
    )


def _check_verdict_freshness(
    pr_number: int,
    issue_number: int,
    repo_root: Path,
    failed: list[str],
    notes: list[str],
) -> None:
    """Group (c): recorded REVIEW verdict must be APPROVED and SHA/date fresh.

    Substrate-present only. Fail-closed on any evaluation error — a stale
    APPROVED verdict predating the PR head commit fails (#2003 BLOCKER 2).
    """
    try:
        record = _run_verdict_get(issue_number, repo_root)
    except Exception as exc:
        failed.append(f"REVIEW verdict unavailable (verdict get failed: {exc})")
        return

    verdict_text = record.get("verdict") or "" if isinstance(record, dict) else ""
    if not verdict_text:
        failed.append("no recorded REVIEW verdict")
        return
    if "APPROVED" not in verdict_text.upper():
        failed.append(f"REVIEW verdict is not APPROVED (got {verdict_text!r})")
        return

    try:
        commit = _gh_latest_commit(pr_number, repo_root)
    except Exception as exc:
        failed.append(f"PR latest commit unavailable for verdict freshness check ({exc})")
        return
    head_sha = commit.get("sha") or ""
    commit_date = commit.get("date") or ""

    trailer = _HEAD_SHA_TRAILER_RE.search(verdict_text)
    if trailer:
        if not head_sha:
            failed.append("PR head SHA unavailable for verdict freshness check")
            return
        if trailer.group(1).lower() == head_sha.lower():
            notes.append("REVIEW verdict fresh: head_sha trailer matches PR head commit")
            return
        failed.append("REVIEW verdict predates PR head commit (head_sha trailer mismatch)")
        return

    # No trailer: compare the verdict's recorded timestamp to the latest
    # commit's committer date.
    verdict_dt = _parse_iso(record.get("recorded_at") or "")
    commit_dt = _parse_iso(commit_date)
    if verdict_dt is None or commit_dt is None:
        failed.append(
            "REVIEW verdict freshness indeterminate (missing/unparseable verdict"
            " timestamp or latest-commit date)"
        )
        return
    if verdict_dt < commit_dt:
        failed.append("REVIEW verdict predates PR head commit")
        return
    notes.append("REVIEW verdict fresh: recorded after the PR's latest commit")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_merge_predicate(pr_number: int, repo_root: Path | None = None) -> PredicateResult:
    """Evaluate the terminal merge predicate for one PR number.

    Never raises for check failures — every failed leg lands in
    ``failed_checks`` by name. Only truly unrecoverable setup errors
    (e.g. repo root unresolvable) propagate; callers treat any raise as a
    fail-closed block.
    """
    failed: list[str] = []
    notes: list[str] = []
    root = Path(repo_root) if repo_root is not None else _resolve_repo_root()

    # Ordered detection: probe the substrate FIRST, as a repo property.
    substrate = _substrate_present(root)

    pr, issue_number = _check_pr_state(pr_number, root, failed)
    head_ref = (pr or {}).get("headRefName") or ""

    if not substrate:
        note = (
            "substrate absent (no docs/sdlc/do-merge.md or sdlc-tool unresolvable):"
            " DOCS-stage and verdict-freshness checks skipped; PR-state checks"
            " still enforced"
        )
        notes.append(note)
        logger.info("merge_predicate: %s", note)
    elif issue_number is None:
        # Group (a) already recorded the missing/unresolvable issue link as a
        # failed check, so the predicate is blocked regardless; groups (b)/(c)
        # have no issue number to query.
        notes.append("substrate checks not evaluated: issue number unresolvable from PR state")
    else:
        _check_docs_stage(issue_number, head_ref, root, failed, notes)
        _check_verdict_freshness(pr_number, issue_number, root, failed, notes)

    return PredicateResult(
        allowed=not failed,
        failed_checks=failed,
        substrate_present=substrate,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the terminal SDLC merge predicate for a PR",
    )
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument(
        "--repo-root", default=None, help="Target repo root (default: git toplevel)"
    )
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    args = parser.parse_args(argv)

    try:
        result = evaluate_merge_predicate(
            args.pr_number, repo_root=Path(args.repo_root) if args.repo_root else None
        )
    except Exception as exc:
        # Unrecoverable setup error — fail closed with a named reason.
        result = PredicateResult(
            allowed=False,
            failed_checks=[f"predicate evaluation failed ({exc})"],
            substrate_present=False,
            notes=[],
        )

    if args.json:
        print(json.dumps(asdict(result)))
    else:
        print(f"allowed: {result.allowed}")
        for check in result.failed_checks:
            print(f"FAIL: {check}")
        for note in result.notes:
            print(f"note: {note}")
    return 0 if result.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
