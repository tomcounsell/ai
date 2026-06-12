"""CLI tool for querying SDLC stage_states from a PM session.

Invoked by the SDLC router skill (SKILL.md) to read the current pipeline
state from Redis. Default output (``--format json``) is the enriched payload
used by the router's Legal Dispatch Guards::

    {
        "stages": {"ISSUE": "completed", "PLAN": "completed", ...},
        "_meta": {
            "patch_cycle_count": 0,
            "pr_merge_state": "CLEAN",
            "ci_all_passing": true,
            "critique_cycle_count": 1,
            "latest_critique_verdict": "NEEDS REVISION",
            "latest_review_verdict": null,
            "revision_applied": false,
            "pr_number": null,
            "same_stage_dispatch_count": 2,
            "last_dispatched_skill": "/do-plan-critique"
        }
    }

The legacy flat shape (``{"ISSUE": "completed", ...}``) is preserved under
``--format legacy`` for transitional backward compatibility. Old callers that
don't care about _meta can ignore the new keys.

Usage:
    python -m tools.sdlc_stage_query --session-id <SESSION_ID>
    python -m tools.sdlc_stage_query --issue-number <ISSUE_NUMBER>
    python -m tools.sdlc_stage_query --issue-number 1040 --format legacy
    python -m tools.sdlc_stage_query --help

Exit codes:
    0 — always (errors return empty JSON ``{}``)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from tools._sdlc_utils import find_plan_path as _find_plan_path

logger = logging.getLogger(__name__)


def _find_session_by_id(session_id: str):
    """Find an AgentSession by session_id.

    Returns the session object or None.
    """
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None
        # Prefer PM sessions (they own stage_states)
        for s in sessions:
            if getattr(s, "session_type", None) == "pm":
                return s
        return sessions[0]
    except Exception as e:
        logger.debug(f"_find_session_by_id failed: {e}")
        return None


def _find_session_by_issue(issue_number: int):
    """Find a PM session tracking the given issue number.

    Delegates to the shared implementation in tools._sdlc_utils.
    Returns the session object or None.
    """
    try:
        from tools._sdlc_utils import find_session_by_issue

        return find_session_by_issue(issue_number)
    except Exception as e:
        logger.debug(f"_find_session_by_issue failed: {e}")
        return None


def _load_raw_states(session) -> dict:
    """Return the full stage_states dict, including underscore metadata."""
    try:
        raw = session.stage_states
        if not raw:
            return {}
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = dict(raw)
        else:
            return {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.debug(f"_load_raw_states failed: {e}")
        return {}


def _get_stage_states(session) -> dict[str, str]:
    """Extract stage_states from a session, returning a dict.

    Returns an empty dict if stage_states is unavailable or malformed.
    """
    data = _load_raw_states(session)
    if not data:
        return {}

    try:
        from agent.pipeline_state import ALL_STAGES

        return {k: v for k, v in data.items() if k in ALL_STAGES}
    except Exception as e:
        logger.debug(f"_get_stage_states filter failed: {e}")
        # Fall back to a conservative filter: non-underscore keys only.
        return {k: v for k, v in data.items() if not k.startswith("_")}


def _fetch_pr_merge_state(pr_number: int | None) -> tuple[str | None, bool | None]:
    """Fetch live PR merge state and CI status from GitHub.

    Returns a tuple of (pr_merge_state, ci_all_passing):
    - ``pr_merge_state``: value of ``mergeStateStatus`` (e.g. "CLEAN", "BLOCKED",
      "DIRTY") or ``None`` on any failure.
    - ``ci_all_passing``: ``True`` if all ``statusCheckRollup`` conclusions are
      ``"SUCCESS"`` (empty list also returns ``True`` — a repo with no required
      checks has no failing checks), ``None`` on failure.

    On any ``gh`` CLI failure (network error, unknown PR, timeout), both fields
    default to ``None``. Guard G6 will not fire if either is ``None``.
    """
    if not pr_number:
        return None, None

    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "mergeStateStatus,statusCheckRollup",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            logger.debug(f"_fetch_pr_merge_state: gh returned {proc.returncode}")
            return None, None
        data = json.loads(proc.stdout or "{}")
        merge_state = data.get("mergeStateStatus")
        if not isinstance(merge_state, str):
            merge_state = None
        rollup = data.get("statusCheckRollup")
        if not isinstance(rollup, list):
            ci_all_passing = None
        else:
            # Empty statusCheckRollup: no required checks → no failing checks.
            # all() on empty sequence returns True in Python, which is correct here.
            ci_all_passing = all(
                isinstance(check, dict) and check.get("conclusion") == "SUCCESS" for check in rollup
            )
        return merge_state, ci_all_passing
    except Exception as e:
        logger.debug(f"_fetch_pr_merge_state failed: {e}")
        return None, None


def _gh_pr_list(args: list[str]) -> int | None:
    """Run ``gh pr list`` with the given args and return the first PR number.

    Returns the PR number or None on any failure. Never raises.
    """
    # GH_REPO is automatically respected by the ``gh`` CLI; no --repo flag
    # needed for cross-repo work.
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", *args, "--json", "number"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return None
        prs = json.loads(proc.stdout or "[]")
        if not isinstance(prs, list) or not prs:
            return None
        first = prs[0]
        if isinstance(first, dict) and isinstance(first.get("number"), int):
            return first["number"]
    except Exception as e:
        logger.debug(f"_gh_pr_list failed: {e}")
    return None


def _lookup_pr_number(issue_number: int | None, slug: str | None = None) -> int | None:
    """Attempt to find the open PR number for this issue via ``gh``.

    Resolution order (D4):
    1. Issue-number search (``gh pr list --search "#{issue_number}"``) —
       primary path; resolves PRs whose body references the issue.
    2. Branch-head fallback (``gh pr list --head session/{slug}``) — recovers
       out-of-band PRs whose body never referenced the issue. Uses the
       canonical SDLC branch shape ``session/{slug}`` (NOT a fabricated
       ``session/sdlc-{issue_number}`` form this repo never creates); only
       runs when a slug is available.

    Returns the PR number or None. Never raises.
    """
    if issue_number:
        pr = _gh_pr_list(["--search", f"#{issue_number}", "--state", "open"])
        if pr is not None:
            return pr

    if slug:
        pr = _gh_pr_list(["--head", f"session/{slug}", "--state", "open"])
        if pr is not None:
            return pr

    return None


_FRONTMATTER_REVISION_RE = re.compile(
    r"^revision_applied:\s*(true|false)\s*$", re.IGNORECASE | re.MULTILINE
)


def _parse_revision_applied(plan_path: Path | None) -> bool:
    """Read the plan frontmatter and return whether ``revision_applied: true``."""
    if plan_path is None:
        return False
    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    # Only scan the frontmatter block if present
    if text.startswith("---"):
        end = text.find("\n---", 3)
        frontmatter = text[: end if end > 0 else len(text)]
    else:
        frontmatter = text[:2000]  # first ~2k chars as a cheap fallback
    match = _FRONTMATTER_REVISION_RE.search(frontmatter)
    if not match:
        return False
    return match.group(1).lower() == "true"


def _extract_verdict_text(record) -> str | None:
    """Read a verdict text from a _verdicts[stage] entry (dict or str)."""
    if isinstance(record, dict):
        text = record.get("verdict")
        if isinstance(text, str):
            return text
    elif isinstance(record, str):
        return record
    return None


def _compute_meta(
    raw_states: dict,
    session,
    issue_number: int | None,
) -> dict:
    """Build the ``_meta`` payload for the enriched query response."""
    verdicts = raw_states.get("_verdicts") or {}
    if not isinstance(verdicts, dict):
        verdicts = {}

    latest_critique = _extract_verdict_text(verdicts.get("CRITIQUE"))
    latest_review = _extract_verdict_text(verdicts.get("REVIEW"))

    pr_number = None
    # Resolution order (D4): explicit session attribute → the writable
    # `_pr_number` meta key (set via `meta-set`, the primary out-of-band
    # recovery path) → a gh lookup (issue-search then branch-head fallback).
    session_pr = getattr(session, "pr_number", None) if session is not None else None
    meta_pr = raw_states.get("_pr_number")
    slug = getattr(session, "slug", None) if session is not None else None
    if isinstance(session_pr, int) and session_pr > 0:
        pr_number = session_pr
    elif isinstance(meta_pr, int) and meta_pr > 0:
        pr_number = meta_pr
    else:
        pr_number = _lookup_pr_number(issue_number, slug=slug)

    # Fetch live PR merge state and CI status for G6 guard
    pr_merge_state, ci_all_passing = _fetch_pr_merge_state(pr_number)

    # Compute dispatch-history derived fields.
    # D5: pass the LIVE stage snapshot so the count resets when state has
    # moved past the last recorded dispatch (G4 self-clears on a real
    # transition instead of latching on a stale recorded count).
    same_stage_count = 0
    last_skill: str | None = None
    try:
        from agent.sdlc_router import build_stage_snapshot, compute_same_stage_count

        live_snapshot = build_stage_snapshot(raw_states, {"pr_number": pr_number})
        same_stage_count, last_skill = compute_same_stage_count(
            raw_states, current_snapshot=live_snapshot
        )
    except Exception as e:
        logger.debug(f"_compute_meta: compute_same_stage_count failed: {e}")

    plan_path = None
    revision_applied = False
    if issue_number:
        plan_path = _find_plan_path(issue_number)
        revision_applied = _parse_revision_applied(plan_path)

    # plan_exists: True when a plan doc was found on disk (#1640).
    # Used by the router to distinguish "PLAN=ready with a real doc" from
    # "PLAN=ready because the state machine pre-advanced before /do-plan ran".
    plan_exists = bool(plan_path)

    # Plan-revising lock (G7): set by critique skill, cleared by plan skill.
    plan_revising_raw = raw_states.get("_plan_revising")
    plan_revising = bool(plan_revising_raw) if plan_revising_raw is not None else False

    plan_hash_at_build_start = raw_states.get("_plan_hash_at_build_start") or None
    if isinstance(plan_hash_at_build_start, str) and not plan_hash_at_build_start:
        plan_hash_at_build_start = None

    return {
        "patch_cycle_count": int(raw_states.get("_patch_cycle_count", 0) or 0),
        "critique_cycle_count": int(raw_states.get("_critique_cycle_count", 0) or 0),
        "latest_critique_verdict": latest_critique,
        "latest_review_verdict": latest_review,
        "revision_applied": revision_applied,
        "pr_number": pr_number,
        "pr_merge_state": pr_merge_state,
        "ci_all_passing": ci_all_passing,
        "same_stage_dispatch_count": int(same_stage_count),
        "last_dispatched_skill": last_skill,
        "plan_revising": plan_revising,
        "plan_hash_at_build_start": plan_hash_at_build_start,
        "plan_exists": plan_exists,
        "issue_number": issue_number,
    }


def _default_meta() -> dict:
    """Return a safe ``_meta`` dict when no session is available."""
    return {
        "patch_cycle_count": 0,
        "critique_cycle_count": 0,
        "latest_critique_verdict": None,
        "latest_review_verdict": None,
        "revision_applied": False,
        "pr_number": None,
        "pr_merge_state": None,
        "ci_all_passing": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
        "plan_revising": False,
        "plan_hash_at_build_start": None,
        "plan_exists": False,
        "issue_number": None,
    }


def query_stage_states(
    session_id: str | None = None,
    issue_number: int | None = None,
) -> dict[str, str]:
    """Query stage_states for a session (legacy flat shape).

    Returns the stage-status dict, unchanged from prior behavior. This
    function is preserved for backward compatibility and is exercised by
    ``--format legacy``.
    """
    session = None

    if session_id:
        session = _find_session_by_id(session_id)

    if session is None and issue_number is not None:
        session = _find_session_by_issue(issue_number)

    if session is None:
        return {}

    return _get_stage_states(session)


def query_enriched(
    session_id: str | None = None,
    issue_number: int | None = None,
) -> dict:
    """Query stage_states and return the enriched router payload.

    Returns::

        {
            "stages": {stage_name: status, ...},
            "_meta": {patch_cycle_count, critique_cycle_count,
                      latest_critique_verdict, latest_review_verdict,
                      revision_applied, pr_number,
                      same_stage_dispatch_count, last_dispatched_skill}
        }

    If no session is found, returns ``{"stages": {}, "_meta": {...defaults}}``.
    """
    session = None
    if session_id:
        session = _find_session_by_id(session_id)
    if session is None and issue_number is not None:
        session = _find_session_by_issue(issue_number)

    if session is None:
        return {"stages": {}, "_meta": _default_meta()}

    raw_states = _load_raw_states(session)
    stages = {}
    try:
        from agent.pipeline_state import ALL_STAGES

        stages = {k: v for k, v in raw_states.items() if k in ALL_STAGES}
    except Exception:
        stages = {k: v for k, v in raw_states.items() if not k.startswith("_")}

    meta = _compute_meta(raw_states, session, issue_number)
    return {"stages": stages, "_meta": meta}


def main():
    parser = argparse.ArgumentParser(
        description="Query SDLC stage_states from a PM session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tools.sdlc_stage_query --session-id tg_project_123_456
  python -m tools.sdlc_stage_query --issue-number 704
  python -m tools.sdlc_stage_query --issue-number 704 --format legacy

Default output (enriched):
  {"stages": {...}, "_meta": {...}}

Legacy output (--format legacy):
  {"ISSUE": "completed", "PLAN": "completed", ...}
""",
    )
    parser.add_argument(
        "--session-id",
        help="Session ID to look up (e.g., VALOR_SESSION_ID)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        help="GitHub issue number to find the PM session for",
    )
    parser.add_argument(
        "--format",
        choices=["json", "legacy"],
        default="json",
        help="Output format (default: json = enriched; legacy = flat shape)",
    )

    args = parser.parse_args()

    if not args.session_id and args.issue_number is None:
        session_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
        if session_id:
            args.session_id = session_id
        else:
            # No args and no env vars — return format-appropriate empty response.
            if args.format == "legacy":
                print("{}")
            else:
                print(json.dumps({"stages": {}, "_meta": _default_meta()}))
            sys.exit(0)

    try:
        if args.format == "legacy":
            result = query_stage_states(
                session_id=args.session_id,
                issue_number=args.issue_number,
            )
        else:
            result = query_enriched(
                session_id=args.session_id,
                issue_number=args.issue_number,
            )
        print(json.dumps(result))
    except Exception:
        # Never crash — always return format-appropriate empty JSON
        if args.format == "legacy":
            print("{}")
        else:
            print(json.dumps({"stages": {}, "_meta": _default_meta()}))

    sys.exit(0)


if __name__ == "__main__":
    main()
