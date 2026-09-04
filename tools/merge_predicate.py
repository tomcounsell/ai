"""Shared terminal merge predicate (issue #2003).

One deterministic predicate evaluated by BOTH the merge-guard hook
(``.claude/hooks/validators/validate_merge_guard.py``) and the ``/do-merge``
skill (via ``docs/sdlc/do-merge.md``). Consuming a single helper is what keeps
the hook and the skill from drifting apart (#1944 class).

Five check groups:

- **Group (a) — PR state** (always enforced, fail-closed on any ``gh`` error):
  state OPEN, mergeable MERGEABLE, mergeStateStatus CLEAN (or UNSTABLE with a
  green rollup), CI green (no FAILURE/ERROR; pending counts as not-green), and
  a word-boundary ``Closes/Fixes/Resolves #N`` issue link in the PR body.
- **Group (b) — DOCS stage gate** (substrate-present only): ``stages.DOCS ==
  completed`` passes; ``in_progress`` hard-fails; pending/empty degrades to a
  ``docs/features/{slug}.md`` existence check (slug from the PR head ref).
  DOCS is never skippable, so this leg is unchanged for a PR that did not
  originate in the pipeline — such a PR reaches ``completed`` by recording its
  never-dispatched PLAN/CRITIQUE stages as ``skipped`` first, which lets the
  DOCS marker's predecessor backfill run (issue #2577). See
  ``docs/features/off-pipeline-merge-path.md``.
- **Group (c) — REVIEW verdict freshness** (substrate-present only): a recorded
  verdict must exist, contain APPROVED (case-insensitive), and be FRESH against
  the PR's latest commit — via the ``REVIEW_CONTEXT head_sha=`` trailer when
  present, else by comparing the verdict's ``recorded_at`` timestamp to the
  latest commit's committer date. A bare ``"APPROVED" in text`` check is
  explicitly insufficient (#2003 critique BLOCKER 2).
- **Group (d) — single-owner MERGE lease** (substrate-present, ``run_id``
  supplied only): the merge actor's ``run_id`` must hold the current per-issue
  SDLC lease. This refuses the Race 2 fork/lineage that never held the lease
  from merging past a supervisor's still-blocked gate (issue #2026, WS1). When
  no ``run_id`` is passed (the merge-guard hook), the gate is skipped so that
  second layer keeps working; the ``/do-merge`` skill passes ``--run-id`` for
  the primary enforcement. Fails open on Redis errors (lease confirmed),
  closed on a substrate-present import failure.
- **Group (e) — verification outcomes** (substrate-present, plan-tracked
  only): the #3080 / ``ba092a06d`` owner ruling -- "FAIL and UNEVALUATED both
  hold the PR at REVIEW" -- lived only in plan prose, so PR #3080 merged past
  it. This reads the graded aggregate the verification runner persists to the
  lane's ``PipelineLedger`` (``_verification_outcomes``, #3065 Cluster B/C)
  and refuses on a ``FAIL`` or ``UNEVALUATED`` row, naming it. Source of truth
  is RECORDED state, never live re-execution (PM ruling, 2026-09-03): this
  never shells out to a plan-authored command. A lane with no plan document,
  or a plan document with no recorded aggregate, is reported and NOT
  enforced -- there is no ruling to make machine-readable. A *present*
  aggregate is checked for freshness against the PR's current head before
  being trusted, fail-closed on a mismatch or an unresolvable anchor; see
  ``_check_verification_outcomes``.

Tracked-issue resolution for groups (b)/(c) (#2034, corrected mechanism): the
two SDLC-substrate checks key on the **SDLC-tracked issue looked up from the
durable ``PipelineLedger`` by PR number**, not the first ``Closes #N`` in the PR
body. A PR that closes several sub-issues under an umbrella tracking issue
records its DOCS marker and REVIEW verdict on the umbrella, so keying on the
first-match body issue false-fails the gate.

An earlier mechanism (PR #2035) attempted this via ``AgentSession.query.filter(
slug=..., issue_number=...)``, but that shape is empirically inert: ``slug`` and
``issue_number`` are populated by disjoint creation paths and 0 live sessions
co-populate both, so the resolver always degraded to NO_SIGNAL in production.
``_resolve_tracked_issue`` now maps the PR number → ``PipelineLedger.query.filter(
pr_number=...)``, scoped to the current ``target_repo`` (``gh repo view``); when
exactly one distinct ``issue_number`` resolves, groups (b)/(c) use it; when none
resolves they fall back to the first ``Closes #N`` (single-issue PRs are
unchanged); and genuine ambiguity (>1 distinct tracked issue for the PR number)
**fails closed** with a named gate failure rather than guessing. Group (a)'s
body-link presence check always uses the raw body issue. ``pr_number`` is
written by ``sdlc-tool meta-set --key pr_number`` at PR creation time (``/do-build``),
so it is populated before the merge gate ever runs.

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
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
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

# Head refs that can never yield a usable slug for the docs/features fallback.
_NO_SLUG_REFS = frozenset({"main", "master", "HEAD", ""})

# Named refusal reason for a recorded `_verification_outcomes` aggregate whose
# stamped `head_sha` does not match the PR's current head (#3065, task 8).
# Defined HERE, where the predicate consumes it, rather than beside the writer:
# this is the ship-side rule, and a Verification row greps this file for the
# symbol. Never read the prose; grep the name.
VERIFICATION_OUTCOMES_STALE_REASON = "verification outcome predates PR head commit"


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


def _gh_repo_name_with_owner(repo_root: Path) -> str:
    """Resolve the target repo's ``owner/name`` slug via ``gh repo view``.

    Raises on any failure. Shared by the latest-commit lookup and the
    PipelineLedger tracked-issue resolution (#2034), both of which need the
    same repo-scoping value.
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
    return repo_proc.stdout.strip()


def _gh_latest_commit(pr_number: int, repo_root: Path) -> dict:
    """Return ``{"sha": ..., "date": ...}`` for the PR's latest (head) commit.

    Raises on any failure — with the substrate present, missing latest-commit
    data must fail the predicate closed, never silently pass.

    #2404: the ``sha`` is resolved authoritatively via
    ``tools.pr_head_resolver.resolve_pr_head_sha`` (``git ls-remote origin
    refs/pull/N/head``, which shares no response cache with ``gh`` and cannot be
    served stale in the fail-open direction). The ``gh api …/commits`` read
    still provides the committer ``date`` (used only by the no-trailer freshness
    branch, where a stale date fails *closed*), and is the fallback SHA source
    when the git read yields nothing (foreign/cross-repo checkout). Without the
    git-authoritative SHA, a stale current-head read matches the verdict's
    trailer and makes a stale approval look fresh — the fail-open hole #2404
    closes.
    """
    # Lazy import to preserve this module's stdlib-only load posture for the
    # merge-guard hook (see module docstring); pr_head_resolver is stdlib-only.
    from tools.pr_head_resolver import resolve_pr_head_sha

    repo = _gh_repo_name_with_owner(repo_root)
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
    gh_sha = commit.get("sha") or ""
    authoritative_sha = resolve_pr_head_sha(pr_number, repo=repo, repo_root=str(repo_root))
    return {
        "sha": authoritative_sha or gh_sha,
        "date": ((commit.get("commit") or {}).get("committer") or {}).get("date") or "",
    }


def _sdlc_tool_cmd(subcommand: list[str], repo_root: Path) -> list[str]:
    """Build the substrate invocation: prefer ``sdlc-tool``, else ``python -m``."""
    if shutil.which("sdlc-tool") is not None:
        return ["sdlc-tool", *subcommand]
    return [
        sys.executable,
        "-m",
        f"tools.sdlc_{subcommand[0].replace('-', '_')}",
        *subcommand[1:],
    ]


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
        ["verdict", "get", "--stage", "REVIEW", "--issue-number", str(issue_number)],
        repo_root,
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
    """Slug from a PR head ref: strip ``session/``; main/master/HEAD/empty → no slug.

    A slug still containing ``/`` (any non-``session/`` prefixed head ref,
    e.g. ``fix/router-blocked-on-conflict``) is likewise unusable -- the
    docs/features fallback would otherwise probe a nested path that can never
    exist. See #2891.
    """
    slug = (head_ref or "").removeprefix("session/")
    if slug in _NO_SLUG_REFS or "/" in slug:
        return ""
    return slug


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tracked-issue resolution (#2034)
# ---------------------------------------------------------------------------


class _TrackedOutcome(Enum):
    """Outcome of a PR-number→SDLC-tracked-issue resolution.

    - ``TRACKED``: exactly one ``PipelineLedger`` record for this PR number
      (scoped to ``target_repo``) carries the tracked ``issue_number`` —
      groups (b)/(c) key on it.
    - ``NO_SIGNAL``: no matching ledger, repo unresolvable, or a degraded
      import/query — the caller falls back to the body issue.
    - ``AMBIGUOUS``: >1 distinct tracked ``issue_number`` for the PR number —
      the caller fails closed rather than guessing.
    """

    TRACKED = "tracked"
    NO_SIGNAL = "no_signal"
    AMBIGUOUS = "ambiguous"


@dataclass
class _TrackedIssue:
    """Tri-state result of :func:`_resolve_tracked_issue`."""

    outcome: _TrackedOutcome
    issue_number: int | None = None
    note: str = ""
    distinct_count: int = 0


def _resolve_tracked_issue(pr_number: int, repo_root: Path) -> _TrackedIssue:
    """Resolve the SDLC-tracked issue carried by the PR's PipelineLedger record (#2034).

    Groups (b)/(c) must key on the umbrella tracking issue where the DOCS marker
    and REVIEW verdict actually live, not the first ``Closes #N`` in the PR body
    (which, for a multi-issue-closure PR, points at a sub-issue with no SDLC
    substrate). This looks up the PR number in the durable ``PipelineLedger``,
    which is keyed by ``(target_repo, issue_number)`` and carries a unique
    ``pr_number`` field written by ``sdlc-tool meta-set --key pr_number`` at PR
    creation time (``/do-build``) — long before the merge gate ever runs.

    An earlier mechanism (PR #2035) resolved this via
    ``AgentSession.query.filter(slug=..., issue_number=...)``, keyed on the PR's
    branch slug. That mechanism is empirically inert: ``slug`` and
    ``issue_number`` are populated by disjoint AgentSession creation paths, so
    0 of the live sessions in production co-populate both fields, and the
    resolver always degraded to NO_SIGNAL. ``PipelineLedger.pr_number`` is a
    single-writer, unique-per-PR field with no such gap.

    Resolution:

    1. Lazy import of ``agent.pipeline_ledger.PipelineLedger``, guarded by a
       broad ``except Exception`` — import-time failures include Redis/Popoto
       client init, not just ``ImportError``. Any failure degrades to
       **NO_SIGNAL**.
    2. Resolve ``target_repo`` via ``_gh_repo_name_with_owner(repo_root)``,
       guarded the same way — a ``gh`` failure degrades to **NO_SIGNAL**.
    3. Query ``PipelineLedger.query.filter(pr_number=pr_number)``, guarded the
       same way — a Redis outage degrades to **NO_SIGNAL**, never crashes the
       merge-guard hook.
    4. Keep only ledgers whose ``target_repo`` matches the resolved repo (a
       ``pr_number`` could in principle collide across repos in a shared test
       Redis; this keeps resolution repo-scoped in production too).
    5. Distinct non-null ``issue_number`` values across the survivors: exactly
       one → **TRACKED**; zero → **NO_SIGNAL**; more than one → **AMBIGUOUS**.
    """
    # Guard 1: lazy import. Broad except — import-time failures include
    # Redis/Popoto client init, not just ImportError.
    try:
        from agent.pipeline_ledger import PipelineLedger
    except Exception:
        return _TrackedIssue(
            _TrackedOutcome.NO_SIGNAL,
            note="PipelineLedger unimportable; body-issue fallback",
        )

    # Guard 2: repo-name resolution via gh. Broad except — any gh failure
    # degrades to the body issue, never crashes the hook.
    try:
        target_repo = _gh_repo_name_with_owner(repo_root)
    except Exception:
        return _TrackedIssue(
            _TrackedOutcome.NO_SIGNAL,
            note="target repo unresolvable; body-issue fallback",
        )

    # Guard 3: the Redis-backed query. Broad except — an outage degrades to
    # the body issue, never crashes the hook.
    try:
        ledgers = list(PipelineLedger.query.filter(pr_number=pr_number).all())
    except Exception:
        return _TrackedIssue(
            _TrackedOutcome.NO_SIGNAL,
            note="ledger query failed; body-issue fallback",
        )

    distinct: set[int] = set()
    for ledger in ledgers:
        if getattr(ledger, "target_repo", None) != target_repo:
            continue
        issue = getattr(ledger, "issue_number", None)
        if issue is not None:
            distinct.add(int(issue))

    if len(distinct) == 1:
        return _TrackedIssue(_TrackedOutcome.TRACKED, issue_number=next(iter(distinct)))
    if not distinct:
        return _TrackedIssue(
            _TrackedOutcome.NO_SIGNAL,
            note=f"no PipelineLedger found for pr_number {pr_number}",
        )
    return _TrackedIssue(_TrackedOutcome.AMBIGUOUS, distinct_count=len(distinct))


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
        if conclusion in (
            "FAILURE",
            "ERROR",
            "CANCELLED",
            "TIMED_OUT",
        ) or status_state in (
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

    # Lazy import: this module's other imports are stdlib-only so the
    # merge-guard hook can load it under any interpreter (see module
    # docstring). tools._sdlc_utils pulls in models.agent_session, so the
    # trailer reader is fetched here rather than at module level.
    from tools._sdlc_utils import head_sha_of_record

    # Reads the record's `head_sha` FIELD first, falling back to the legacy
    # in-token trailer (#2769). A record carrying neither drops through to the
    # weaker recorded_at-vs-commit-date comparison below.
    trailer = head_sha_of_record(record) if isinstance(record, dict) else ""
    if trailer:
        if not head_sha:
            failed.append("PR head SHA unavailable for verdict freshness check")
            return
        if trailer.lower() == head_sha.lower():
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


def _check_lease_ownership(
    issue_number: int,
    run_id: str | None,
    failed: list[str],
    notes: list[str],
) -> None:
    """Group (d): single-owner MERGE lease gate (issue #2026, WS1).

    Refuses the merge unless the merge actor's ``run_id`` holds the current
    per-issue SDLC lease. A fork that never held the lease — the Race 2
    lineage that tries to merge past a supervisor's still-blocked gate — is
    refused here. Under the single-owner invariant this transitively enforces
    "``run_id`` matches the run that recorded the operative REVIEW verdict":
    verdict recording is itself lease-gated (``sdlc-tool verdict record``
    revalidates the lease before writing), and the supervisor holds the one
    lease continuously for the whole run, so the run holding the lease at MERGE
    is the run that recorded the REVIEW verdict.

    Enforced only when a ``run_id`` is supplied (the ``/do-merge`` skill passes
    ``--run-id``). When absent — e.g. the merge-guard hook, which carries no
    run identity — the check is SKIPPED with a note rather than failing closed,
    so that second guard layer keeps working; the do-merge skill body mandates
    ``--run-id`` for the primary enforcement path.

    Fail-open on Redis errors: ``touch_issue_lock``'s peek returns
    ``owner_run_id`` equal to the supplied ``run_id`` on any Redis exception,
    so a hiccup degrades to "lease confirmed" rather than blocking a legitimate
    merge. A genuine import failure in a substrate-present repo fails closed
    with a named reason.
    """
    if not run_id:
        notes.append(
            "single-owner MERGE lease check skipped: no run_id supplied"
            " (pass --run-id to enforce; hook layer is exempt)"
        )
        return

    try:
        from models.session_lifecycle import touch_issue_lock
    except Exception as exc:
        failed.append(f"single-owner MERGE: cannot verify issue lease (lock import failed: {exc})")
        return

    try:
        peek = touch_issue_lock(issue_number, run_id, peek=True)
    except Exception as exc:
        failed.append(f"single-owner MERGE: issue lease peek failed ({exc})")
        return

    owner_run_id = getattr(peek, "owner_run_id", None)
    if owner_run_id and owner_run_id == run_id:
        notes.append("single-owner MERGE: merge actor holds the issue lease")
        return
    if not owner_run_id:
        failed.append(
            f"single-owner MERGE: no issue lease held for #{issue_number}"
            f" — the supervising run must hold the lease to merge"
        )
        return
    failed.append(
        f"single-owner MERGE: merge actor run_id does not hold the issue lease for"
        f" #{issue_number} (held by run_id={owner_run_id!r}); a fork that never held"
        " the lease cannot merge past a blocked gate"
    )


def _find_plan_doc(issue_number: int, repo_root: Path) -> Path | None:
    """Locate the plan document that *tracks* this issue, or ``None``.

    Delegates to ``tools.lane_identity.find_plan_path``, whose single
    resolution rung is a ``tracking:`` frontmatter line naming the issue. A
    filename match is deliberately NOT a rung: the lane slug and the plan
    filename are allowed to differ, and a bare ``#N`` mention in a "Not
    building" No-Gos line means the opposite of ownership (#2735).

    ``find_plan_path`` scopes its search by ``SDLC_TARGET_REPO``, else the
    cwd's git toplevel. The merge predicate is handed an explicit
    ``repo_root`` that need not be the cwd — the merge-guard hook and
    ``/do-merge`` both pass one — so the override is set for the duration of
    the call and restored afterwards. Duplicating the resolver's regex here
    instead would be the replicated-value defect this lane exists to remove.
    """
    from tools.lane_identity import find_plan_path

    prior = os.environ.get("SDLC_TARGET_REPO")
    os.environ["SDLC_TARGET_REPO"] = str(repo_root)
    try:
        return find_plan_path(issue_number)
    finally:
        if prior is None:
            os.environ.pop("SDLC_TARGET_REPO", None)
        else:
            os.environ["SDLC_TARGET_REPO"] = prior


def _check_verification_outcomes(
    issue_number: int,
    pr_number: int,
    repo_root: Path,
    failed: list[str],
    notes: list[str],
) -> None:
    """Group (e): the plan's graded verification outcomes (#3080, Cluster C).

    Makes the #3080 / ``ba092a06d`` owner ruling -- "FAIL and UNEVALUATED both
    hold the PR at REVIEW" -- machine-readable. That ruling lived only in plan
    prose, so PR #3080 merged past it; this reads the aggregate
    ``agent.verification_parser.record_verification_outcomes`` persists to the
    lane's ``PipelineLedger`` instead of re-deriving anything.

    Source of truth is RECORDED state, never live re-execution (PM ruling,
    2026-09-03, #3065): this never shells out to a plan-authored command --
    re-running a verification suite inside a merge gate is a non-starter, and
    every other check in this module already reads recorded state via ``gh``
    / ``sdlc-tool`` rather than executing anything.

    Three outcomes are deliberately kept distinguishable, none collapsing into
    another:

    - **No plan document** tracks this issue
      (:func:`tools.lane_identity.find_plan_path`) -> reported, not enforced.
      This plan has no evidence a plan-less lane should be blocked by a check
      that exists only because a plan declared a ruling.
    - **A plan document exists but no aggregate was ever recorded** -> also
      reported, not enforced. There is no ruling to enforce when nothing was
      graded; a new fail-closed behavior here has no incident backing it.
    - **A recorded aggregate exists** -> it is graded, but ONLY after its
      freshness against the PR's CURRENT head is established, fail-closed on
      all three dispositions (task 8, round-2 concern):
        * match -> the aggregate is fresh; grade its outcome.
        * mismatch -> stale, treated as equivalent to ``UNEVALUATED``, refused
          with ``VERIFICATION_OUTCOMES_STALE_REASON``. The cached PASS is
          never read.
        * missing/unparseable ``head_sha``, or a PR head that cannot be
          resolved -> refuse. Deliberately stricter than group (c)'s
          ``recorded_at`` fallback: there are no legacy
          ``_verification_outcomes`` records to be compatible with, so a
          weaker comparison would be a #2404-shaped fail-open hole added on
          purpose.

    A ``FAIL`` or ``UNEVALUATED`` row holds the PR; the refusal names the row
    rather than returning a bare failure. The build-vs-ship split lives
    entirely on this consumer -- nothing marks a row with severity, and the
    build gate (a different consumer) is free to treat ``UNEVALUATED`` as a
    pause that still allows build progression.
    """
    try:
        plan_path = _find_plan_doc(issue_number, repo_root)
    except Exception as exc:
        notes.append(f"verification-outcomes check skipped: plan-doc resolution failed ({exc})")
        return

    if plan_path is None:
        notes.append(
            "verification-outcomes check skipped: no plan document tracks issue"
            f" #{issue_number} (reported, not enforced)"
        )
        return

    try:
        target_repo = _gh_repo_name_with_owner(repo_root)
    except Exception as exc:
        failed.append(f"verification outcomes: target repo unresolvable ({exc})")
        return

    # Lazy import: keeps this module's stdlib-only load posture for the
    # merge-guard hook (see module docstring). agent.verification_parser pulls
    # in agent.pipeline_ledger, same posture as the _check_verdict_freshness
    # trailer reader below.
    from agent.verification_parser import (
        VerificationOutcomesUnavailableError,
        read_verification_outcomes,
    )

    try:
        aggregate = read_verification_outcomes(target_repo, issue_number)
    except VerificationOutcomesUnavailableError as exc:
        # An unreadable record is not an absent one. Absence is a lane this
        # gate deliberately does not block; a failed read is a lane about
        # which nothing is known, and passing it would turn a recorded FAIL
        # into an unenforced pass on a transient store error.
        failed.append(f"verification outcomes: recorded aggregate unreadable ({exc})")
        return

    if aggregate is None:
        notes.append(
            "verification-outcomes check skipped: no recorded aggregate for"
            f" issue #{issue_number} (reported, not enforced)"
        )
        return

    from tools._sdlc_utils import head_sha_of_record
    from tools.pr_head_resolver import resolve_pr_head_sha

    recorded_sha = head_sha_of_record(aggregate) if isinstance(aggregate, dict) else ""

    try:
        current_head = resolve_pr_head_sha(pr_number, repo=target_repo, repo_root=str(repo_root))
    except Exception as exc:
        failed.append(f"verification outcomes: PR head unresolvable ({exc})")
        return

    if not current_head:
        failed.append("verification outcomes: PR head unresolvable")
        return

    if not recorded_sha:
        # Kept distinct from the stale case on purpose: "graded against an
        # older commit" and "we cannot tell what it was graded against" are
        # different facts, and collapsing them would hide which one occurred.
        # Both refuse; neither reads the cached outcome.
        failed.append(
            "verification outcomes: no usable head_sha on the recorded aggregate;"
            " freshness is indeterminate and the aggregate cannot be trusted"
        )
        return

    if recorded_sha.lower() != current_head.lower():
        failed.append(
            f"verification outcomes: {VERIFICATION_OUTCOMES_STALE_REASON}"
            f" (graded against {recorded_sha[:12]}, PR head is {current_head[:12]})"
        )
        return

    # The tri-state tokens come from the writer's own enum, never re-spelled
    # here: a literal "UNEVALUATED" in this module would be a replicated value
    # that silently stops matching if the enum is ever renamed.
    from agent.verification_parser import CheckOutcome

    rows = aggregate.get("rows")
    if not isinstance(rows, list):
        failed.append("verification outcomes: recorded aggregate has no readable rows")
        return

    blocking = {CheckOutcome.FAIL.value, CheckOutcome.UNEVALUATED.value}
    offending = 0
    for row in rows:
        if not isinstance(row, dict):
            offending += 1
            failed.append("verification outcomes: a recorded row is malformed")
            continue
        row_outcome = str(row.get("outcome") or "")
        if row_outcome not in blocking:
            continue
        offending += 1
        reason = str(row.get("reason") or "").strip()
        failed.append(
            f"verification row {(row.get('name') or '<unnamed>')!r} is {row_outcome}"
            + (f": {reason}" if reason else "")
            + " — FAIL and UNEVALUATED both hold the PR (owner ruling on #3080)"
        )

    malformed = aggregate.get("malformed") or 0
    if isinstance(malformed, int) and malformed > 0:
        offending += 1
        failed.append(
            f"verification outcomes: {malformed} malformed row(s) were never executed;"
            " an unrunnable check is not a passing check"
        )

    outcome = str(aggregate.get("outcome") or "")
    if offending:
        return
    if outcome != CheckOutcome.PASS.value:
        # No blocking row and no malformed row, yet the aggregate is not PASS:
        # a run with no checks at all, which grades UNEVALUATED rather than a
        # vacuous PASS. Refuse rather than guess what it meant.
        failed.append(
            f"verification outcomes: recorded outcome is {(outcome or '<absent>')!r},"
            f" not {CheckOutcome.PASS.value} ({len(rows)} row(s) recorded)"
        )
        return
    notes.append(
        f"verification outcomes fresh: {len(rows)} row(s), all {CheckOutcome.PASS.value}"
        f" for issue #{issue_number}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_merge_predicate(
    pr_number: int,
    repo_root: Path | None = None,
    run_id: str | None = None,
) -> PredicateResult:
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
    else:
        # Groups (b)/(c) key on the SDLC-tracked issue resolved from the
        # PipelineLedger by PR number (#2034), not the first-match body
        # ``Closes #N``. Group (a)'s body-link presence check (in
        # _check_pr_state) is unaffected.
        tracked = _resolve_tracked_issue(pr_number, root)
        if tracked.outcome is _TrackedOutcome.AMBIGUOUS:
            # Fail closed: refuse to guess which issue carries the substrate.
            failed.append(
                f"tracked-issue lookup ambiguous: {tracked.distinct_count} distinct"
                f" issues for PR #{pr_number}; cannot determine which issue"
                " carries the SDLC substrate"
            )
        else:
            if tracked.outcome is _TrackedOutcome.TRACKED:
                effective_issue = tracked.issue_number
                if issue_number is None:
                    notes.append(
                        f"substrate checks keyed on SDLC-tracked issue #{effective_issue}"
                        " (PipelineLedger pr_number lookup)"
                    )
                elif effective_issue != issue_number:
                    notes.append(
                        f"substrate checks keyed on SDLC-tracked issue #{effective_issue}"
                        f" (PipelineLedger pr_number lookup), not first Closes #{issue_number}"
                    )
            else:  # NO_SIGNAL — fall back to the body-parsed issue (today's path).
                effective_issue = issue_number
                if tracked.note:
                    notes.append(f"tracked-issue lookup: {tracked.note}; using body issue")

            if effective_issue is None:
                # Group (a) already recorded the missing/unresolvable issue link
                # as a failed check; groups (b)/(c) have no issue number to query.
                notes.append(
                    "substrate checks not evaluated: issue number unresolvable from PR state"
                )
            else:
                _check_docs_stage(effective_issue, head_ref, root, failed, notes)
                _check_verdict_freshness(pr_number, effective_issue, root, failed, notes)
                # Group (d): single-owner MERGE lease gate (issue #2026, WS1).
                # Keyed on the same SDLC-tracked issue as groups (b)/(c) — the
                # lease is per-issue.
                _check_lease_ownership(effective_issue, run_id, failed, notes)
                # Group (e): verification outcomes (#3080, #3065 Cluster C).
                # Keyed on the same SDLC-tracked issue — the plan and its
                # graded aggregate live on the tracking issue, not a sub-issue.
                _check_verification_outcomes(effective_issue, pr_number, root, failed, notes)

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
    parser.add_argument(
        "--run-id",
        default=None,
        help="Merge actor's SDLC run_id. When supplied, the single-owner MERGE gate"
        " (issue #2026, WS1) refuses the merge unless this run_id holds the current"
        " issue lease. Omitted (e.g. the merge-guard hook) skips only that gate.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    args = parser.parse_args(argv)

    try:
        result = evaluate_merge_predicate(
            args.pr_number,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            run_id=args.run_id,
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
