"""Atomic write+verify helper for REVIEW verdict/trailer/marker persistence (#2193).

**Incident this closes:** a local ``/do-sdlc`` run posted a correct APPROVED
review on GitHub but never persisted the local substrate state (verdict,
``REVIEW_CONTEXT head_sha=`` trailer, REVIEW ``completed`` marker) the router
reads to advance the pipeline. The three writes were a hand-executed,
non-atomic sequence with no fail-closed backstop -- a skill that skipped any
of them left the router re-dispatching REVIEW forever. See
``docs/plans/issue-2193-pr-review-verdict-persistence-fail-closed.md``.

This module collapses the sequence into two entry points, both reachable via
``sdlc-tool verdict finalize`` / ``sdlc-tool verdict selfcheck`` (subparsers
wired in ``tools/sdlc_verdict.main()`` -- there is deliberately no top-level
``review-finalize``/``review-selfcheck`` command; see the plan's "Tool
Surface Decision"):

- :func:`finalize` -- state-mutating. Computes the PR head SHA, records the
  verdict with the ``REVIEW_CONTEXT head_sha=<40-hex>`` trailer appended
  (idempotent), writes the REVIEW ``completed`` marker on the APPROVED path,
  then reads all three back via :func:`check_review_persistence`. Raises
  :class:`ReviewFinalizeError` with a **named error** on any gap -- it cannot
  partially complete.
- :func:`check_review_persistence` -- the single shared read-back function,
  used by BOTH :func:`finalize` (write+verify) and the read-only
  ``selfcheck`` CLI path, so the two paths can never disagree (single-source
  invariant, mirroring ``tools/sdlc_verdict.py``'s single-writer pattern).

Named error taxonomy (mirrors the existing WS3c/WS-D gate vocabulary in
``tools/sdlc_stage_marker.py``):

- ``REVIEW_VERDICT_MISSING`` -- no readable REVIEW verdict for the issue.
- ``REVIEW_TRAILER_MISSING`` -- the recorded verdict lacks a well-formed
  ``REVIEW_CONTEXT head_sha=<40-hex>`` trailer matching the PR's current
  head commit (or the head SHA itself could not be resolved via ``gh``).
- ``REVIEW_MARKER_INCOMPLETE`` -- the REVIEW stage marker is not
  ``completed``.

**Fail-closed contract:** every probe in :func:`check_review_persistence`
(verdict presence, trailer well-formedness, marker completion) treats ANY
exception -- a Redis hiccup, a ``gh`` failure, a malformed record -- as the
corresponding named failure, never as a silent pass. :func:`finalize` never
records a trailer-less verdict on a ``gh`` failure; it refuses loudly with
``REVIEW_TRAILER_MISSING`` instead (see Risk 2 in the plan). This mirrors the
existing fail-closed convention of ``_review_verdict_readable`` /
``_review_artifact_posted`` in ``tools/sdlc_stage_marker.py``.

**APPROVED-only scope:** the trailer and marker checks are gated on the
verdict normalizing to APPROVED. CHANGES REQUESTED / BLOCKED_ON_CONFLICT /
PR_CLOSED verdicts legitimately carry no trailer and leave the marker
``in_progress`` by contract -- :func:`check_review_persistence` reports
``ok: true`` for those the moment a verdict is present, exactly mirroring the
APPROVED-only scope of the completion-marker gate extension in
``tools/sdlc_stage_marker.py`` (see plan Risk 1 / No-Gos).
"""

from __future__ import annotations

import logging
import subprocess

from tools._sdlc_utils import _HEAD_SHA_TRAILER_RE, normalize_verdict

logger = logging.getLogger(__name__)


class ReviewFinalizeError(Exception):
    """Raised by :func:`finalize` when a write or its readback fails.

    The message is always prefixed with the named error taxon (e.g.
    ``"REVIEW_TRAILER_MISSING: ..."``) so ``tools/sdlc_verdict.py``'s CLI
    ``main()`` -- which prints any exception message to stderr and exits
    non-zero -- surfaces the named reason loudly to the operator.
    """


def _fetch_pr_head_sha(pr: int) -> str | None:
    """Resolve the PR's current head commit SHA via ``gh pr view``.

    Returns ``None`` on any failure (missing ``gh``, non-zero exit, timeout,
    empty output) -- never raises. Callers must treat ``None`` as "head SHA
    unresolvable" and fail closed (Risk 2: never record a trailer-less
    verdict when the head SHA cannot be confirmed).
    """
    try:
        from config.settings import settings

        timeout = settings.timeouts.git_subprocess_s
    except Exception:
        timeout = 10

    try:
        proc = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid"],
            capture_output=True,
            text=True,
            timeout=timeout,  # timeout-guard: allow
        )
    except Exception as e:
        logger.debug(f"sdlc_review_finalize: gh pr view failed for PR #{pr}: {e}")
        return None

    if proc.returncode != 0:
        logger.debug(
            f"sdlc_review_finalize: gh pr view rc={proc.returncode} for PR #{pr}: "
            f"{(proc.stderr or '').strip()}"
        )
        return None

    sha = (proc.stdout or "").strip()
    return sha or None


def check_review_persistence(pr: int, issue_number: int) -> dict:
    """Read back verdict + trailer + marker state for a REVIEW (#2193).

    The single shared read-back function -- :func:`finalize` calls this to
    self-verify its own writes, and the read-only ``selfcheck`` CLI path
    calls it directly. Never raises; every failure mode (missing verdict,
    unreadable substrate, unresolvable PR head SHA, Redis/gh errors) is
    reported through the returned dict, fail CLOSED (``ok: False``).

    Returns::

        {
            "ok": bool,
            "verdict_present": bool,
            "trailer_matches_head": bool,
            "marker_completed": bool,
            "reason": str | None,  # one of the three named errors, or None
        }

    APPROVED-only scope: once a verdict is present but does NOT normalize to
    APPROVED, this returns ``ok: True`` immediately -- non-APPROVED verdicts
    legitimately carry no trailer and leave the marker ``in_progress`` (see
    module docstring). ``trailer_matches_head``/``marker_completed`` stay
    ``False`` in that case (they were never checked), which is expected and
    does not affect ``ok``.
    """
    result: dict = {
        "ok": False,
        "verdict_present": False,
        "trailer_matches_head": False,
        "marker_completed": False,
        "reason": None,
    }

    try:
        from tools.sdlc_stage_query import _resolve_issue_record, query_stage_states
        from tools.sdlc_verdict import get_verdict

        record = _resolve_issue_record(issue_number)
        verdict_text = ""
        if record is not None:
            verdict_record = get_verdict(record, "REVIEW")
            if isinstance(verdict_record, dict):
                verdict_text = verdict_record.get("verdict") or ""

        result["verdict_present"] = bool(verdict_text)
        if not result["verdict_present"]:
            result["reason"] = "REVIEW_VERDICT_MISSING"
            return result

        normalized = normalize_verdict(verdict_text)
        is_approved = "APPROVED" in normalized

        if not is_approved:
            # Non-APPROVED verdicts are exempt from the trailer/marker
            # checks by contract (Risk 1 / No-Gos) -- a verdict being
            # readable is the whole story here.
            result["ok"] = True
            return result

        head_sha = _fetch_pr_head_sha(pr)
        trailer = _HEAD_SHA_TRAILER_RE.search(verdict_text)
        if head_sha and trailer and trailer.group(1).lower() == head_sha.lower():
            result["trailer_matches_head"] = True
        else:
            result["reason"] = "REVIEW_TRAILER_MISSING"
            return result

        stages = query_stage_states(issue_number=issue_number)
        result["marker_completed"] = stages.get("REVIEW") == "completed"
        if not result["marker_completed"]:
            result["reason"] = "REVIEW_MARKER_INCOMPLETE"
            return result

        result["ok"] = True
        return result
    except Exception as e:
        # Fail CLOSED: any unexpected error (Redis hiccup, malformed
        # record, gh raising outside _fetch_pr_head_sha's own guard) must
        # never read as a false pass. Preserve a reason already set by a
        # sub-check; otherwise default to the earliest-stage named error.
        logger.debug(
            f"sdlc_review_finalize: check_review_persistence failed for "
            f"PR #{pr}/issue #{issue_number}: {e}"
        )
        result["reason"] = result["reason"] or "REVIEW_VERDICT_MISSING"
        return result


def finalize(
    pr: int,
    issue_number: int,
    verdict: str,
    run_id: str | None,
    blockers: int | None = None,
    tech_debt: int | None = None,
) -> dict:
    """Atomically record verdict + head_sha trailer + REVIEW marker, then verify.

    Collapses the historically hand-run 3-call sequence (``verdict record``,
    ``stage-marker REVIEW completed``, ``verdict get`` readback) into one
    operation that cannot partially complete: any failure at any step raises
    :class:`ReviewFinalizeError` (or, for a lease-ownership refusal,
    :class:`tools.sdlc_verdict.OwnershipError` is NOT used here -- lease
    failures are reported as :class:`ReviewFinalizeError` too, prefixed with
    the same ``LEASE_ABSENT``/``ISSUE_LOCKED``/``TARGET_REPO_MISSING``
    reasons ``tools/sdlc_verdict.py``'s ``_cli_record`` uses) with no
    partial write left behind.

    Args:
        pr: PR number -- source of the head_sha trailer.
        issue_number: GitHub issue number (the ledger key component).
        verdict: Free-form verdict string (e.g. ``"APPROVED"``). May already
            carry a ``REVIEW_CONTEXT head_sha=`` trailer -- if so it is left
            untouched (idempotent append).
        run_id: The caller's run identity (``sdlc-tool session-ensure``).
        blockers: Optional blocker count.
        tech_debt: Optional tech-debt count.

    Returns:
        The :func:`check_review_persistence` result dict on success
        (``ok`` is always ``True`` when this returns instead of raising).

    Raises:
        ReviewFinalizeError: on ANY gap -- empty verdict, no/foreign/repo-less
            lease, unresolvable PR head SHA, a failed verdict write, a failed
            marker write, or a readback that comes back ``ok: False``. The
            message is always prefixed with the named reason.
    """
    if not isinstance(verdict, str) or not verdict.strip():
        # Mirrors record_verdict's empty-verdict guard (tools/sdlc_verdict.py
        # lines 312-314): refuse before touching anything, no partial write.
        raise ReviewFinalizeError(
            "REVIEW_VERDICT_MISSING: verdict is empty/whitespace; refusing to "
            "finalize with no partial write"
        )

    if not issue_number or not run_id:
        raise ReviewFinalizeError(
            "LEASE_ABSENT: finalize requires both --issue-number and --run-id"
        )

    from tools._sdlc_utils import resolve_ledger_lease, revalidate_ledger_lease

    target_repo, lease_error = resolve_ledger_lease(issue_number, run_id)
    if lease_error is not None:
        reason = lease_error.get("reason", "LEASE_ABSENT")
        if reason == "ISSUE_LOCKED":
            raise ReviewFinalizeError(
                f"ISSUE_LOCKED: issue lock held by a foreign run "
                f"(run_id={lease_error.get('owner_run_id')}, "
                f"session={lease_error.get('owner_session_id')}); refusing finalize"
            )
        raise ReviewFinalizeError(
            f"LEASE_ABSENT: no live issue lease for issue #{issue_number} owned by "
            f"run_id={run_id!r}; run `sdlc-tool session-ensure` first."
        )
    if not target_repo:
        raise ReviewFinalizeError(
            f"TARGET_REPO_MISSING: issue lease for issue #{issue_number} has no "
            "pinned target_repo; refusing to write with a None key component."
        )

    # Fail CLOSED on an unresolvable head SHA (Risk 2): never record a
    # trailer-less verdict just because `gh` hiccuped -- the loud failure
    # here is strictly better than the silent stall the incident describes.
    head_sha = _fetch_pr_head_sha(pr)
    if not head_sha:
        raise ReviewFinalizeError(
            f"REVIEW_TRAILER_MISSING: could not resolve PR #{pr}'s head SHA via "
            "`gh pr view` -- refusing to record a trailer-less verdict"
        )

    trailered_verdict = (
        verdict
        if _HEAD_SHA_TRAILER_RE.search(verdict)
        else f"{verdict.strip()} REVIEW_CONTEXT head_sha={head_sha}"
    )

    # TOCTOU close (mirrors _cli_record): re-validate the lease non-peek
    # immediately before the write, never trusting the earlier peek.
    if not revalidate_ledger_lease(issue_number, run_id, target_repo):
        raise ReviewFinalizeError(
            f"ISSUE_LOCKED: lease for issue #{issue_number} was taken by a foreign "
            "run between resolve and write; refusing finalize write"
        )

    from agent.pipeline_ledger import PipelineLedger
    from tools.sdlc_verdict import record_verdict

    ledger = PipelineLedger.get_or_create(target_repo, issue_number)
    record = record_verdict(
        ledger,
        stage="REVIEW",
        verdict=trailered_verdict,
        blockers=blockers,
        tech_debt=tech_debt,
        issue_number=issue_number,
    )
    if not record:
        raise ReviewFinalizeError(
            f"REVIEW_VERDICT_MISSING: record_verdict returned no record for issue "
            f"#{issue_number} -- the write did not persist"
        )

    normalized = normalize_verdict(trailered_verdict)
    is_approved = "APPROVED" in normalized

    if is_approved:
        from tools.sdlc_stage_marker import write_marker

        marker_result, marker_exit = write_marker(
            stage="REVIEW", status="completed", issue_number=issue_number, run_id=run_id
        )
        if marker_exit != 0:
            reason = marker_result.get("reason", "REVIEW_MARKER_INCOMPLETE")
            raise ReviewFinalizeError(
                f"{reason}: REVIEW completion marker write failed for issue "
                f"#{issue_number}: {marker_result}"
            )

    result = check_review_persistence(pr, issue_number)
    if not result.get("ok"):
        reason = result.get("reason") or "REVIEW_MARKER_INCOMPLETE"
        raise ReviewFinalizeError(
            f"{reason}: finalize readback failed for issue #{issue_number}/PR #{pr}: {result}"
        )

    return result


# ---------------------------------------------------------------------------
# CLI entry points -- imported and wired as subparsers by
# tools/sdlc_verdict.main() (`sdlc-tool verdict finalize` / `... selfcheck`).
# ---------------------------------------------------------------------------


def _cli_finalize(args) -> dict:
    """``sdlc-tool verdict finalize`` entry point.

    Delegates straight into :func:`finalize`. Any :class:`ReviewFinalizeError`
    propagates to ``sdlc_verdict.main()``'s generic exception handler, which
    prints the named reason to stderr and exits non-zero -- the loud signal
    an operator (or a supervising ``/do-sdlc`` run) needs to see.
    """
    return finalize(
        pr=args.pr,
        issue_number=args.issue_number,
        verdict=args.verdict,
        run_id=args.run_id,
        blockers=args.blockers,
        tech_debt=args.tech_debt,
    )


def _cli_selfcheck(args) -> dict:
    """``sdlc-tool verdict selfcheck`` entry point.

    Read-only: always returns (never raises), so ``sdlc_verdict.main()``
    always exits 0 for this subcommand. The typed JSON ``ok`` field -- not
    the process exit code -- carries the verdict, mirroring how other
    read-only ``sdlc-tool`` subcommands (``stage-query``, ``verdict get``)
    behave. Callers (e.g. the ``/do-sdlc`` supervisor) branch on the JSON.
    """
    return check_review_persistence(args.pr, args.issue_number)
