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
            "revision_applied_at": null,
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
import time
from datetime import datetime
from pathlib import Path

from tools._sdlc_utils import _resolve_target_repo
from tools._sdlc_utils import find_plan_path as _find_plan_path
from tools._sdlc_utils import is_pipeline_ledger as _is_pipeline_ledger
from tools._sdlc_utils import resolve_target_repo_for_read as _resolve_target_repo_for_read

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounded read-path retry for class-set lookups (issue #1720)
# ---------------------------------------------------------------------------
# Mirrors the retry constants in tools/valor_session.py.  Both reader sites
# use the same parameters: 5 attempts × 200ms = 1000ms total, covering the
# measured spike-1 p99 class-set-empty window of 651ms (150 sessions).
# See tools/valor_session.py for the full design rationale.
_CLASS_SET_RETRY_ATTEMPTS = 5
_CLASS_SET_RETRY_BACKOFF_S = 0.20  # seconds between attempts


def _find_session_by_id(session_id: str):
    """Find an AgentSession by session_id.

    Returns the session object or None.

    Bounded retry (issue #1720): popoto's rebuild_indexes() transiently empties
    the class set ($Class:AgentSession); a concurrent query.filter(session_id=...)
    returns empty for a live session during that window.  We retry up to
    _CLASS_SET_RETRY_ATTEMPTS times (total cap sized to exceed the measured p99
    class-set-empty window of 651ms), then return None on genuine absence.
    The retry sits inside the try/except so a cap-exhausted genuine miss still
    returns None cleanly (unchanged behavior).
    """
    try:
        from models.agent_session import AgentSession

        for attempt in range(_CLASS_SET_RETRY_ATTEMPTS):
            sessions = list(AgentSession.query.filter(session_id=session_id))
            if sessions:
                # Prefer eng sessions (they own stage_states)
                for s in sessions:
                    if getattr(s, "session_type", None) == "eng":
                        return s
                return sessions[0]
            if attempt < _CLASS_SET_RETRY_ATTEMPTS - 1:
                logger.debug(
                    "_find_session_by_id: query.filter(session_id=%r) returned empty"
                    " on attempt %d/%d — class-set may be mid-rebuild, retrying in %.0fms",
                    session_id,
                    attempt + 1,
                    _CLASS_SET_RETRY_ATTEMPTS,
                    _CLASS_SET_RETRY_BACKOFF_S * 1000,
                )
                time.sleep(_CLASS_SET_RETRY_BACKOFF_S)
        return None
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
    """Return the full stage_states dict, including underscore metadata.

    ``session`` may be an ``AgentSession`` (field ``stage_states``) or a
    ``PipelineLedger`` (field ``stage_states_json`` -- issue #2012 task 2).
    """
    try:
        field = "stage_states_json" if _is_pipeline_ledger(session) else "stage_states"
        raw = getattr(session, field, None)
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


def _fetch_pr_merge_state(
    pr_number: int | None, repo: str | None = None
) -> tuple[str | None, bool | None]:
    """Fetch live PR merge state and CI status from GitHub.

    Returns a tuple of (pr_merge_state, ci_all_passing):
    - ``pr_merge_state``: value of ``mergeStateStatus`` (e.g. "CLEAN", "BLOCKED",
      "DIRTY") or ``None`` on any failure.
    - ``ci_all_passing``: ``True`` if all ``statusCheckRollup`` conclusions are
      ``"SUCCESS"`` (empty list also returns ``True`` — a repo with no required
      checks has no failing checks), ``None`` on failure.

    On any ``gh`` CLI failure (network error, unknown PR, timeout), both fields
    default to ``None``. Guard G6 will not fire if either is ``None``.

    Args:
        pr_number: The PR number to query.
        repo: Optional owner/name slug for cross-repo PRs (e.g. "tomcounsell/popoto").
            When provided, ``--repo`` is passed to ``gh pr view``. The function
            never calls ``_resolve_target_repo`` internally; callers are responsible
            for resolving the repo and threading it in.
    """
    if not pr_number:
        return None, None

    try:
        cmd = [
            "gh",
            "pr",
            "view",
            str(pr_number),
            *(["--repo", repo] if repo else []),
            "--json",
            "mergeStateStatus,statusCheckRollup",
        ]
        proc = subprocess.run(
            cmd,
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


def _gh_pr_list(args: list[str], repo: str | None = None) -> int | None:
    """Run ``gh pr list`` with the given args and return the first PR number.

    Returns the PR number or None on any failure. Never raises.

    Args:
        args: Extra arguments to pass to ``gh pr list``.
        repo: Optional owner/name slug for cross-repo lookup. When provided,
            ``--repo`` is added to the command. The function never calls
            ``_resolve_target_repo`` internally; callers resolve and thread it in.
    """
    # Resolution ladder: _resolve_target_repo() is called exactly once per
    # _compute_meta invocation and threaded into _gh_pr_list via the ``repo``
    # param. This ensures cross-repo SDLC sessions (cwd=~/src/ai, target
    # repo=tomcounsell/popoto) resolve PR state against the correct repo.
    try:
        cmd = ["gh", "pr", "list", *args, "--json", "number"]
        if repo:
            cmd = ["gh", "pr", "list", "--repo", repo, *args, "--json", "number"]
        proc = subprocess.run(
            cmd,
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


def _body_references_issue(body: str | None, issue_number: int) -> bool:
    """Return True iff ``body`` contains a closing-keyword reference to the issue.

    Matches a word-boundary GitHub closing keyword (``close``/``closes``/``closed``,
    ``fix``/``fixes``/``fixed``, ``resolve``/``resolves``/``resolved``, case-insensitive)
    immediately followed by ``#{issue_number}`` with no trailing digit. The trailing
    ``(?!\\d)`` negative lookahead is the numeric boundary that prevents ``#195`` from
    matching a body that says ``Closes #1950``. Empty/None body → False.

    A bare ``#N`` mention with no closing keyword is intentionally NOT a match: fuzzy
    ``gh pr list --search "#N"`` tokenizes the digits and can surface an unrelated PR,
    so a literal closing-keyword reference is required to trust the match.
    """
    if not body:
        return False
    pattern = re.compile(
        rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#{issue_number}(?!\d)",
        re.IGNORECASE,
    )
    return pattern.search(body) is not None


def _gh_pr_search_issue_ref(issue_number: int, repo: str | None = None) -> int | None:
    """Search open PRs for ``#{issue_number}`` and return the first whose body validates.

    Runs ``gh pr list --search "#{issue_number}" --state open --json number,body`` and
    iterates the returned candidates in order, returning the ``number`` of the first PR
    whose ``body`` passes :func:`_body_references_issue`. Fuzzy search alone is untrusted:
    it can return an unrelated PR whose text merely contains the digits, so the body must
    carry a literal ``Closes/Fixes/Resolves #{issue_number}`` reference to be trusted.

    Returns the validated PR number, or None on any failure or when no candidate validates.
    Never raises.
    """
    try:
        cmd = ["gh", "pr", "list", "--search", f"#{issue_number}", "--state", "open"]
        if repo:
            cmd = [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--search",
                f"#{issue_number}",
                "--state",
                "open",
            ]
        cmd += ["--json", "number,body"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            return None
        prs = json.loads(proc.stdout or "[]")
        if not isinstance(prs, list):
            return None
        for pr in prs:
            if not isinstance(pr, dict):
                continue
            number = pr.get("number")
            if not isinstance(number, int):
                continue
            if _body_references_issue(pr.get("body"), issue_number):
                return number
    except Exception as e:
        logger.debug(f"_gh_pr_search_issue_ref failed: {e}")
    return None


def _lookup_pr(
    issue_number: int | None, slug: str | None = None, repo: str | None = None
) -> int | None:
    """Attempt to find the open PR number for this issue via ``gh``.

    Resolution order (D4):
    1. Issue-number search (``gh pr list --search "#{issue_number}"``) —
       primary path; returns a PR only when its body carries a literal
       word-boundary closing-keyword reference (``Closes/Fixes/Resolves
       #{issue_number}``) to the exact issue. Fuzzy search matches alone are
       NOT trusted (they can surface an unrelated PR whose text merely contains
       the digits); validation runs in :func:`_gh_pr_search_issue_ref`.
    2. Branch-head fallback (``gh pr list --head session/{slug}``) — recovers
       out-of-band PRs whose body never referenced the issue. Uses the
       canonical SDLC branch shape ``session/{slug}`` (NOT a fabricated
       ``session/sdlc-{issue_number}`` form this repo never creates); an exact
       head-ref match needs no body validation. Only runs when a slug is
       available.

    Returns the PR number or None. Never raises.
    """
    if issue_number:
        pr = _gh_pr_search_issue_ref(issue_number, repo=repo)
        if pr is not None:
            return pr

    if slug:
        pr = _gh_pr_list(["--head", f"session/{slug}", "--state", "open"], repo=repo)
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


_FRONTMATTER_REVISION_APPLIED_AT_RE = re.compile(
    r"^revision_applied_at:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)


def _parse_revision_applied_at(plan_path: Path | None) -> str | None:
    """Read the plan frontmatter and return the ``revision_applied_at`` timestamp.

    Structural twin of :func:`_parse_revision_applied` (#1760): the sticky
    ``revision_applied: true`` boolean alone can't distinguish "this is the
    settle-and-build dispatch" from "this is some later unrelated /do-plan
    dispatch" because /do-plan sets it on every revision pass. Pairing it with
    an event-scoped ``revision_applied_at:`` ISO-8601 timestamp lets the
    router's convergence latch (``_critique_verdict_is_stale``) compare it
    against dispatch history instead of trusting the boolean alone.

    Returns ``None`` (latch inert, fail-safe) when the plan is missing, the
    field is absent, or the value fails ``datetime.fromisoformat`` parsing —
    never raises.
    """
    if plan_path is None:
        return None
    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        frontmatter = text[: end if end > 0 else len(text)]
    else:
        frontmatter = text[:2000]  # first ~2k chars as a cheap fallback
    match = _FRONTMATTER_REVISION_APPLIED_AT_RE.search(frontmatter)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        datetime.fromisoformat(raw_value)
    except Exception:
        return None
    return raw_value


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
    # Resolve the target repo exactly once per _compute_meta invocation.
    # Resolution ladder (in _resolve_target_repo):
    #   GH_REPO env (already an owner/name slug) →
    #   SDLC_TARGET_REPO env (filesystem path used as cwd for gh repo view) →
    #   git working-tree root (also used as cwd for gh repo view) →
    #   None (degrades to current-repo behavior).
    # The resolved slug is threaded into _fetch_pr_merge_state and _gh_pr_list
    # via their ``repo=`` param; neither function calls _resolve_target_repo itself.
    resolved_repo = _resolve_target_repo()

    verdicts = raw_states.get("_verdicts") or {}
    if not isinstance(verdicts, dict):
        verdicts = {}

    latest_critique = _extract_verdict_text(verdicts.get("CRITIQUE"))
    latest_review = _extract_verdict_text(verdicts.get("REVIEW"))

    pr_number = None
    # Resolution ladder (#2003 T1.7 single-writer): the AgentSession.pr_number
    # FIELD first — its single writer is `sdlc-tool meta-set --key pr_number`,
    # invoked by /do-build at PR creation (and by out-of-band operator
    # recovery). When the field is unset, fall through to READ-ONLY recovery
    # rungs that never write anything back: the validated gh issue-search
    # (#1998: fuzzy matches trusted only with a word-boundary
    # Closes/Fixes/Resolves body reference), then the `session/{slug}`
    # branch-head fallback — both inside _lookup_pr.
    session_pr = getattr(session, "pr_number", None) if session is not None else None
    slug = getattr(session, "slug", None) if session is not None else None
    if isinstance(session_pr, int) and session_pr > 0:
        pr_number = session_pr
    else:
        pr_number = _lookup_pr(issue_number, slug=slug, repo=resolved_repo)

    # Fetch live PR merge state and CI status for G6 guard
    pr_merge_state, ci_all_passing = _fetch_pr_merge_state(pr_number, repo=resolved_repo)

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
    revision_applied_at = None
    if issue_number:
        plan_path = _find_plan_path(issue_number)
        revision_applied = _parse_revision_applied(plan_path)
        revision_applied_at = _parse_revision_applied_at(plan_path)

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
        "revision_applied_at": revision_applied_at,
        "pr_number": pr_number,
        "pr_merge_state": pr_merge_state,
        "ci_all_passing": ci_all_passing,
        "same_stage_dispatch_count": int(same_stage_count),
        "last_dispatched_skill": last_skill,
        "plan_revising": plan_revising,
        "plan_hash_at_build_start": plan_hash_at_build_start,
        "plan_exists": plan_exists,
        "issue_number": issue_number,
        "_resolved_target_repo": resolved_repo,
        # Completion-guard refusal counter (issue #2158). Persisted on the
        # ledger keyed by issue_number so the refusal ladder does not restart
        # on session resume. Surfaced here so the runner reads it without a
        # second ledger fetch.
        "completion_refusal_count": int(raw_states.get("_completion_refusal_count", 0) or 0),
    }


def _default_meta() -> dict:
    """Return a safe ``_meta`` dict when no session is available."""
    return {
        "patch_cycle_count": 0,
        "critique_cycle_count": 0,
        "latest_critique_verdict": None,
        "latest_review_verdict": None,
        "revision_applied": False,
        "revision_applied_at": None,
        "pr_number": None,
        "pr_merge_state": None,
        "ci_all_passing": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
        "plan_revising": False,
        "plan_hash_at_build_start": None,
        "plan_exists": False,
        "issue_number": None,
        "_resolved_target_repo": None,
        "completion_refusal_count": 0,
    }


def _resolve_issue_record(issue_number: int):
    """Resolve the record to read for ``issue_number`` -- issue-keyed
    PipelineLedger first, with a retained session fallback for pre-cutover
    records (issue #2012 task 2, reader side).

    Resolution:

    1. ``_resolve_target_repo_for_read()`` -- lease-first (peek, no run_id
       claim), env-fallback. If this resolves to ``None`` at all, returns
       ``None`` immediately: this is the defined empty-ledger outcome
       (Risk 5) -- never touch a phantom ``PipelineLedger[(None, issue)]``
       key, and never fall back to a session either (a caller who genuinely
       cannot resolve a repo has no coherent issue-keyed context to read).
    2. If a ``target_repo`` DOES resolve: load
       ``PipelineLedger.get_or_create(target_repo, issue_number)``. If it
       carries any recorded stage state, return it.
    3. Ledger resolved but empty (never written, or pre-cutover) -- retained
       cold-path session fallback via ``find_session_by_issue()``: the belt
       for issues whose work started before this migration and whose
       ``AgentSession`` still carries the old data, or a session created
       between a migration backfill run and this deploy.

    Returns the ``PipelineLedger``, the fallback ``AgentSession``, or
    ``None`` if nothing resolves. Never raises.
    """
    target_repo = _resolve_target_repo_for_read(issue_number)
    if not target_repo:
        return None

    ledger = None
    try:
        from agent.pipeline_ledger import PipelineLedger

        ledger = PipelineLedger.get_or_create(target_repo, issue_number)
    except Exception as e:
        logger.debug(f"_resolve_issue_record: ledger load failed for issue #{issue_number}: {e}")

    if ledger is not None and _load_raw_states(ledger):
        return ledger

    return _find_session_by_issue(issue_number)


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
        session = _resolve_issue_record(issue_number)

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

    If no session/ledger is found, returns ``{"stages": {}, "_meta": {...defaults}}``.
    """
    session = None
    if session_id:
        session = _find_session_by_id(session_id)
    if session is None and issue_number is not None:
        session = _resolve_issue_record(issue_number)

    if session is None:
        return {"stages": {}, "_meta": _default_meta()}

    raw_states = _load_raw_states(session)
    stages = {}
    try:
        from agent.pipeline_state import ALL_STAGES

        stages = {k: v for k, v in raw_states.items() if k in ALL_STAGES}
    except Exception:
        stages = {k: v for k, v in raw_states.items() if not k.startswith("_")}

    # Thread the router-helper underscore keys into the stages dict. The router's
    # staleness rules (``_critique_verdict_is_stale`` / ``_latest_dispatch_at`` →
    # row 2b/8b) read ``_verdicts`` and ``_sdlc_dispatches`` directly off the
    # ``stage_states`` arg. Without them here, those rules are structurally inert
    # in the CLI path: a revised plan with a stale NEEDS REVISION verdict can never
    # route to re-critique and dead-ends on ``/do-plan`` until G4 oscillation fires.
    for _router_key in ("_verdicts", "_sdlc_dispatches"):
        if _router_key in raw_states:
            stages[_router_key] = raw_states[_router_key]

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
