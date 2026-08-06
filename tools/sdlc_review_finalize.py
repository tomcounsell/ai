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
- ``REVIEW_VERDICT_UNRECOGNIZED`` -- the verdict normalizes to none of the
  four tokens the review contract defines (see ``RECOGNIZED_REVIEW_VERDICTS``).
- ``REVIEW_TRAILER_MISSING`` -- the recorded verdict lacks a well-formed
  ``REVIEW_CONTEXT head_sha=<40-hex>`` trailer matching the PR's current
  head commit (or the head SHA itself could not be resolved via ``gh``).
- ``REVIEW_MARKER_INCOMPLETE`` -- the REVIEW stage marker is not
  ``completed``.

**Fail-closed contract:** every probe in :func:`check_review_persistence`
(verdict presence, trailer well-formedness, marker completion) treats ANY
exception -- a Redis hiccup, a ``gh`` failure, a malformed record -- as the
corresponding named failure, never as a silent pass. On the APPROVED path,
:func:`finalize` never records a trailer-less verdict on a ``gh`` failure; it
refuses loudly with ``REVIEW_TRAILER_MISSING`` instead (see Risk 2 in the
plan). Non-APPROVED verdicts carry no trailer by design, so this gate never
applies to them (see plan No-Gos). This mirrors the existing fail-closed
convention of ``_review_verdict_readable`` / ``_review_artifact_posted`` in
``tools/sdlc_stage_marker.py``.

**APPROVED-only scope:** the trailer and marker checks are gated on the
verdict normalizing to APPROVED. CHANGES REQUESTED / BLOCKED_ON_CONFLICT /
PR_CLOSED verdicts legitimately carry no trailer and leave the marker
``in_progress`` by contract -- :func:`check_review_persistence` reports
``ok: true`` for those the moment a verdict is present, exactly mirroring the
APPROVED-only scope of the completion-marker gate extension in
``tools/sdlc_stage_marker.py`` (see plan Risk 1 / No-Gos).

**Closed verdict vocabulary (#2548):** the APPROVED-only scope above is only
safe when every non-APPROVED verdict is a verdict the contract actually
defines. A free-form string that matches none of the four tokens -- the real
incident was ``"APPROVE WITH COMMENTS"``, which is not ``"APPROVED"`` -- used
to fall through the ``is_approved`` test into the non-APPROVED exemption:
:func:`finalize` skipped the trailer AND the marker write, and
:func:`check_review_persistence` returned ``ok: true`` with
``trailer_matches_head``/``marker_completed`` both ``false`` and ``reason:
null``. The REVIEW marker never landed, so the router re-dispatched REVIEW
forever while the tool reported success. Both paths now check the verdict
against :data:`RECOGNIZED_REVIEW_VERDICTS` and fail closed with
``REVIEW_VERDICT_UNRECOGNIZED``: :func:`finalize` refuses before any write,
and :func:`check_review_persistence` refuses on read so a verdict recorded
out-of-band (``sdlc-tool verdict record --stage REVIEW``) cannot claim the
exemption either.
"""

from __future__ import annotations

import logging

from tools._sdlc_utils import (
    _HEAD_SHA_TRAILER_RE,
    normalize_verdict,
    resolve_target_repo_for_read,
)

logger = logging.getLogger(__name__)


# The complete REVIEW verdict vocabulary (#2548). These are the normalized
# images (``normalize_verdict`` maps ``_`` to space and uppercases) of every
# verdict ``/do-pr-review`` is contracted to emit: the APPROVED path plus the
# three non-APPROVED exits documented in the module docstring. Membership is a
# SUBSTRING test, matching how ``agent/sdlc_router`` and
# ``tools/merge_predicate`` read the same field, so a decorated verdict like
# ``"APPROVED (0 BLOCKERS)"`` still resolves. A verdict matching none of these
# is an authoring error, never a silent non-APPROVED exemption.
RECOGNIZED_REVIEW_VERDICTS = (
    "APPROVED",
    "CHANGES REQUESTED",
    "BLOCKED ON CONFLICT",
    "PR CLOSED",
)


def _verdict_is_recognized(normalized: str) -> bool:
    """Return True iff ``normalized`` carries one of the contract's verdict tokens.

    ``normalized`` must already have been through ``normalize_verdict``.
    """
    return any(token in normalized for token in RECOGNIZED_REVIEW_VERDICTS)


class ReviewFinalizeError(Exception):
    """Raised by :func:`finalize` when a write or its readback fails.

    The message is always prefixed with the named error taxon (e.g.
    ``"REVIEW_TRAILER_MISSING: ..."``) so ``tools/sdlc_verdict.py``'s CLI
    ``main()`` -- which prints any exception message to stderr and exits
    non-zero -- surfaces the named reason loudly to the operator.
    """


class HeadShaResolutionError(Exception):
    """Raised by :func:`_fetch_pr_head_sha` when the PR head SHA is unresolvable.

    Fail-LOUD replacement for the prior silent ``return None`` (#2377 /
    absorbed #2394). A silent ``None`` here produced a false-negative REVIEW
    gate whose root cause -- a wrong-repo or unauthenticated ``gh`` call --
    was buried at ``debug`` level. The message names the concrete failure
    (missing ``gh``, non-zero exit + stderr, timeout, or empty output) and the
    repo the lookup targeted. Callers convert it to the ``REVIEW_TRAILER_MISSING``
    named error: :func:`finalize` re-raises it loudly as
    :class:`ReviewFinalizeError` (write path); :func:`check_review_persistence`
    catches it and fails closed WITHOUT raising (read / ``selfcheck`` path,
    which must stay exit-0).
    """


def _fetch_pr_head_sha(pr: int, repo: str | None = None) -> str:
    """Authoritatively resolve the PR's current head commit SHA (#2404).

    Resolution is git-first via ``tools.pr_head_resolver.resolve_pr_head_sha``
    (``git ls-remote origin refs/pull/N/head``, which shares no response cache
    with ``gh`` and cannot be served stale in the fail-open direction), falling
    back to ``gh pr view --json headRefOid`` when the git read yields nothing.
    Recording the trailer from the same authoritative source the merge/router
    checks read (#2404) means record and check agree, so a fresh approval is
    never spuriously fail-closed by a record/check SHA-source mismatch.

    When ``repo`` (an ``owner/name`` slug) is provided it is threaded through so
    the lookup targets the correct repository regardless of the tool's process
    cwd (#2377 Mode 1): ``sdlc-tool`` forces cwd to ``~/src/ai``, so an
    unscoped read resolves the wrong repo on a cross-repo local ``/do-sdlc``
    run. The resolver's git step self-guards against this — it only trusts the
    local ``origin`` when it points at ``repo`` — and the gh fallback carries
    ``--repo``.

    Raises :class:`HeadShaResolutionError` on ANY failure (both sources
    unresolvable) -- it never returns a falsy value. Callers fail closed
    (Risk 2: never record a trailer-less verdict when the head SHA cannot be
    confirmed).
    """
    from tools.pr_head_resolver import resolve_pr_head_sha

    try:
        sha = resolve_pr_head_sha(pr, repo=repo)
    except Exception as e:
        logger.error(
            "sdlc_review_finalize: head SHA resolution raised for PR #%s (repo=%s): %s",
            pr,
            repo or "<cwd>",
            e,
        )
        raise HeadShaResolutionError(
            f"could not resolve PR #{pr}'s head SHA (repo={repo or '<cwd>'}): {e}"
        ) from e

    if not sha:
        logger.error(
            f"sdlc_review_finalize: head SHA unresolvable for PR #{pr} "
            f"(repo={repo or '<cwd>'}) via git ls-remote or gh pr view"
        )
        raise HeadShaResolutionError(
            f"could not resolve PR #{pr}'s head SHA (repo={repo or '<cwd>'}): "
            f"neither `git ls-remote refs/pull/{pr}/head` nor `gh pr view` returned a SHA"
        )
    return sha


def check_review_persistence(pr: int, issue_number: int, run_id: str | None = None) -> dict:
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
            "approved": bool,
            "trailer_matches_head": bool,
            "marker_completed": bool,
            "reason": str | None,  # one of the named errors, or None
        }

    ``ok`` is the verdict of the WHOLE check, not the conjunction of the three
    booleans below it (#2548). Those booleans report which sub-checks were run
    and passed; ``approved`` says which contract applies and therefore which of
    them were checked at all. Read them together:

    - ``approved: true``  -- the APPROVED contract. ``ok`` requires
      ``verdict_present`` AND ``trailer_matches_head`` AND ``marker_completed``
      AND a confirmed marker write, so here ``ok`` IS their conjunction.
    - ``approved: false`` -- a recognized non-APPROVED verdict (CHANGES
      REQUESTED / BLOCKED_ON_CONFLICT / PR_CLOSED). These carry no trailer and
      leave the marker ``in_progress`` by contract, so
      ``trailer_matches_head``/``marker_completed`` stay ``False`` **because
      they were never checked**, and ``ok: true`` is correct.

    ``reason`` is populated on every ``ok: false`` return and is ``None`` only
    when ``ok`` is ``True``.
    """
    result: dict = {
        "ok": False,
        "verdict_present": False,
        "approved": False,
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

        if not _verdict_is_recognized(normalized):
            # #2548: an unrecognized verdict must NEVER reach the non-APPROVED
            # exemption below -- that is the fail-open path that reported
            # ok:true on a REVIEW whose marker was never written. Fail closed.
            result["reason"] = "REVIEW_VERDICT_UNRECOGNIZED"
            return result

        is_approved = "APPROVED" in normalized
        result["approved"] = is_approved

        if not is_approved:
            # Recognized non-APPROVED verdicts are exempt from the
            # trailer/marker checks by contract (Risk 1 / No-Gos) -- a verdict
            # being readable is the whole story here.
            result["ok"] = True
            return result

        # Resolve the target repo (lease-first, env fallback) so the head-SHA
        # lookup targets the right repository even when cwd is forced to
        # ~/src/ai (#2377 Mode 1). This read path has no run_id, so it uses the
        # lease-peek resolver rather than the write path's owned-lease slug.
        repo = resolve_target_repo_for_read(issue_number)
        try:
            head_sha = _fetch_pr_head_sha(pr, repo=repo)
        except HeadShaResolutionError as e:
            # Fail CLOSED, never raise (selfcheck must stay exit-0). The root
            # cause is already logged at error level inside _fetch_pr_head_sha.
            logger.warning(
                f"sdlc_review_finalize: head SHA unresolved for PR #{pr}/issue #{issue_number}: {e}"
            )
            result["reason"] = "REVIEW_TRAILER_MISSING"
            return result

        trailer = _HEAD_SHA_TRAILER_RE.search(verdict_text)
        if trailer and trailer.group(1).lower() == head_sha.lower():
            result["trailer_matches_head"] = True
        else:
            result["reason"] = "REVIEW_TRAILER_MISSING"
            return result

        stages = query_stage_states(issue_number=issue_number)
        result["marker_completed"] = stages.get("REVIEW") == "completed"
        if not result["marker_completed"]:
            result["reason"] = "REVIEW_MARKER_INCOMPLETE"
            return result

        # Loud-degraded-ledger gate (issue #2451): a run whose entire trail was
        # reconstructed by retry with ZERO confirmed live-lease writes must fail
        # selfcheck loud, not report success on an unwritable ledger (the #2439
        # gap). Assert this run recorded >=1 ok marker write -- i.e.
        # `sdlc:marker_writes:{issue}:{run_id}:ok` > 0. run_id is explicit
        # on the finalize path (it just wrote the REVIEW marker under it); the
        # read-only selfcheck path has none, so resolve the current lease owner
        # (the run holding the lease during REVIEW). Fail-closed only on this
        # already-terminal REVIEW gate -- it hardens the existing gate, never
        # adds a mid-pipeline one.
        effective_run_id = run_id
        if not effective_run_id:
            try:
                from models.session_lifecycle import touch_issue_lock

                effective_run_id = touch_issue_lock(issue_number, None, peek=True).owner_run_id
            except Exception as e:
                logger.debug(
                    "sdlc_review_finalize: lease-owner peek failed for issue #%s (%s: %s)",
                    issue_number,
                    type(e).__name__,
                    e,
                )
                effective_run_id = None

        from tools._sdlc_marker_telemetry import marker_ok_write_count

        if marker_ok_write_count(issue_number, effective_run_id) <= 0:
            result["reason"] = "NO_CONFIRMED_MARKER_WRITE"
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
        ReviewFinalizeError: on ANY gap -- empty verdict, a verdict outside
            :data:`RECOGNIZED_REVIEW_VERDICTS`, no/foreign/repo-less lease, an
            unresolvable PR head SHA (APPROVED verdicts only -- see below), a
            failed verdict write, a failed marker write, or a readback that
            comes back ``ok: False``. The message is always prefixed with the
            named reason.

    Note:
        The mandatory head-SHA fetch and ``REVIEW_TRAILER_MISSING`` hard-fail
        apply only when ``verdict`` normalizes to APPROVED. Non-APPROVED
        verdicts (CHANGES REQUESTED / BLOCKED_ON_CONFLICT / PR_CLOSED) carry
        no trailer by design and are recorded as-is, with no coupling to
        ``gh`` at all (see plan No-Gos).
    """
    if not isinstance(verdict, str) or not verdict.strip():
        # Mirrors record_verdict's empty-verdict guard (tools/sdlc_verdict.py
        # lines 312-314): refuse before touching anything, no partial write.
        raise ReviewFinalizeError(
            "REVIEW_VERDICT_MISSING: verdict is empty/whitespace; refusing to "
            "finalize with no partial write"
        )

    if not _verdict_is_recognized(normalize_verdict(verdict)):
        # #2548: refuse an off-vocabulary verdict BEFORE any write. Left
        # unchecked it silently took the non-APPROVED exemption -- no trailer,
        # no REVIEW marker -- and the readback then reported ok:true, stalling
        # the router in a re-review loop with nothing to show for it.
        raise ReviewFinalizeError(
            f"REVIEW_VERDICT_UNRECOGNIZED: verdict {verdict.strip()!r} matches none of "
            f"{', '.join(RECOGNIZED_REVIEW_VERDICTS)}. Re-run with the contract's "
            "verdict (an APPROVED review with reservations is still APPROVED; use "
            "'CHANGES REQUESTED' if the findings block). Nothing was written."
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

    # is_approved is computed once, early, from the raw (pre-trailer) verdict
    # and reused for both the head-SHA gate below and the marker-write gate
    # further down -- never recomputed, so the two can't drift out of sync.
    normalized = normalize_verdict(verdict)
    is_approved = "APPROVED" in normalized

    if is_approved:
        # Fail CLOSED on an unresolvable head SHA (Risk 2), APPROVED path
        # only: never record a trailer-less APPROVED verdict just because
        # `gh` hiccuped -- the loud failure here is strictly better than the
        # silent stall the incident describes. Non-APPROVED verdicts carry no
        # trailer by design (see plan No-Gos) and never touch `gh` at all.
        #
        # Thread the lease's pinned target_repo slug into the `gh` lookup so it
        # targets the right repository even when cwd is forced to ~/src/ai
        # (#2377 Mode 1). _fetch_pr_head_sha now fails LOUD (raises) rather than
        # returning a silent None -- re-raise as the named REVIEW_TRAILER_MISSING
        # with the concrete gh cause attached (absorbed #2394).
        try:
            head_sha = _fetch_pr_head_sha(pr, repo=target_repo)
        except HeadShaResolutionError as e:
            raise ReviewFinalizeError(
                f"REVIEW_TRAILER_MISSING: {e} -- refusing to record a trailer-less verdict"
            ) from e

        trailered_verdict = (
            verdict
            if _HEAD_SHA_TRAILER_RE.search(verdict)
            else f"{verdict.strip()} REVIEW_CONTEXT head_sha={head_sha}"
        )
    else:
        trailered_verdict = verdict

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

    result = check_review_persistence(pr, issue_number, run_id=run_id)
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
