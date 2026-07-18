"""Self-healing SDLC run identity for resumed pipeline turns (issue #2144).

The SDLC pipeline's state-mutating ``sdlc-tool`` subcommands (``stage-marker``,
``dispatch record``, ``verdict record``, ``meta-set``) each require a *run
identity* — a ``run_id`` minted once by ``sdlc-tool session-ensure`` and passed
back via ``--run-id``. That ``run_id`` lives only in the driving turn's
conversation context. When a PM turn is killed mid-pipeline (worker restart —
issues #2141/#2143) and the session resumes from transcript, the resumed turn
continues pipeline work **without** the ``run_id``, so every marker/verdict
write refuses (``RUN_ID_REQUIRED`` when no flag, ``LEASE_ABSENT`` when a stale
id whose lock TTL lapsed) — and the skill convention's ``2>/dev/null || true``
swallows both stderr and the non-zero exit. The ledger silently freezes while
real work (commits, PRs) proceeds. Observed live on issue #2133.

This module re-establishes run identity **in the tool**, deterministically,
so a resumed LLM turn that does not know it was resumed still writes correctly.
It never blocks pipeline work: every path is best-effort and fail-open.

Design (critique-corrected, issue #2144):

1. **Supervised-inherit FIRST, directly.** The supervised-run inherit branch in
   ``tools.sdlc_session_ensure._acquire_run_lock_and_bind`` is guarded by
   ``if not reuse_run_id:`` — so it is unreachable the moment a ``reuse_run_id``
   is passed. Routing a supervisor's id through ``ensure_session(reuse_run_id=…)``
   would therefore NOT inherit; it would fail ``_validated_reuse_candidate``
   against the live foreign supervisor lock and either refuse ``ISSUE_LOCKED``
   or mint a competitor. So we consult ``supervised_run_status`` directly and
   return the live supervisor's ``run_id`` without touching the reuse gate.
2. **Else reuse an env-corroborated candidate**, precedence corrected so a
   corroborated environment signal (``.sdlc-run`` / ``active_run_id``) outranks
   a possibly-stale caller-supplied id. For a bridge-originated PM pipeline
   (the #2133 shape) there is no supervised signal and no ``.sdlc-run`` file —
   healing there rests solely on ``AgentSession.active_run_id`` surviving the
   resume (the record mirror ``_resume_active`` never touches).
3. ``ensure_session(reuse_run_id=candidate)`` re-acquires on a free/expired
   lock (verified reuse) or mints fresh. **Terminal guard:** a *fresh* mint
   (candidate not corroborated) on a ``MERGE == completed`` pipeline would
   resurrect a finished run's lease — decline (release + return ``None``).
   A foreign live holder (``ISSUE_LOCKED``) is unhealable → ``None``.

Visibility: every self-heal attempt (healed or not) appends one JSON line to
``logs/sdlc_run_identity.log`` at the **git-common-dir root** so all worktrees
converge on one operator-tailable file. The ``healed: bool`` field separates a
recovery from a genuine unhealable refusal.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Short, fail-open timeout for the best-effort `git rev-parse` that resolves the
# git-common-dir root for the visibility log path. Local one-off; not a tunable.
_GIT_ROOT_TIMEOUT_S = 5

# Refusal reasons that indicate a *run-identity* problem the self-heal path can
# attempt to repair. Both the upper-case ledger-lease reasons and the
# lower-case ``sdlc_stage_marker`` error sentinels are included. A foreign
# ``ISSUE_LOCKED`` is listed too: it triggers a heal *attempt*, which correctly
# declines (returns None) rather than adopting a foreign live lease.
RUN_IDENTITY_REFUSAL_REASONS = frozenset(
    {
        "RUN_ID_REQUIRED",
        "LEASE_ABSENT",
        "ISSUE_LOCKED",
        # lower-case sentinels emitted by tools/sdlc_stage_marker.write_marker
        "lease_absent",
        "issue_locked",
    }
)


def classify_refusal(result: dict | None) -> str | None:
    """Return the run-identity refusal reason in ``result``, or ``None``.

    Reads both the ``reason`` (dispatch/verdict/meta_set) and ``error``
    (stage_marker) fields. Returns the reason string only when it names a
    run-identity problem this module can attempt to heal.
    """
    if not isinstance(result, dict):
        return None
    reason = result.get("reason") or result.get("error")
    if reason in RUN_IDENTITY_REFUSAL_REASONS:
        return reason
    return None


def _active_run_id_for_issue(issue_number: int) -> str | None:
    """Best-effort read of the surviving ``active_run_id`` record mirror.

    This is the ONLY carrier that survives a resume for a bridge-originated
    (non-supervised) PM pipeline — the supervised signal / ``.sdlc-run`` file
    are written only for ``/do-sdlc`` supervised runs. ``_resume_active`` seeds
    resume scalars but never touches the session record's ``active_run_id``.
    """
    try:
        from tools._sdlc_utils import find_session_by_issue

        session = find_session_by_issue(issue_number)
        if session is None:
            return None
        return getattr(session, "active_run_id", None) or None
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("reestablish_run_id: active_run_id lookup failed: %s", e)
        return None


def _pipeline_is_terminal(issue_number: int) -> bool:
    """Return True iff the issue's pipeline ledger has ``MERGE == completed``.

    Fail-open to ``False`` (proceed with heal) on any read error — favoring the
    availability of the fix over the rare terminal-resurrection edge, matching
    the module's best-effort contract.
    """
    try:
        from agent.pipeline_state import PipelineStateMachine
        from tools._sdlc_utils import resolve_target_repo_for_read

        target_repo = resolve_target_repo_for_read(issue_number)
        if not target_repo:
            return False
        sm = PipelineStateMachine.for_issue(target_repo, issue_number)
        return sm.states.get("MERGE") == "completed"
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("reestablish_run_id: terminal check failed: %s", e)
        return False


def reestablish_run_id(
    issue_number: int | None,
    prior_run_id: str | None = None,
    working_dir: str | None = None,
) -> str | None:
    """Re-establish the SDLC run identity for ``issue_number``, or ``None``.

    Returns a ``run_id`` a state-mutating write can retry under, or ``None``
    when identity cannot be safely re-established (foreign live lease, no
    issue-number to key on, a terminally-done pipeline that a stray fresh mint
    would resurrect, or any error). Never raises.
    """
    if not issue_number:
        return None
    if working_dir is None:
        working_dir = os.getcwd()

    try:
        from agent.supervised_run import (
            read_supervised_run_signal,
            supervised_run_status,
        )

        # 1. Supervised-inherit FIRST — directly, never via reuse_run_id
        #    (the #2144 BLOCKER: the inherit branch is gated `if not
        #    reuse_run_id`, so routing it through ensure_session would refuse).
        try:
            status = supervised_run_status(issue_number, working_dir=working_dir)
            if getattr(status, "live", False) and getattr(status, "run_id", None):
                return status.run_id
        except Exception as e:  # pragma: no cover - fail-open
            logger.debug("reestablish_run_id: supervised status check failed: %s", e)

        # 2. Env-corroborated reuse candidate. Precedence: signal/.sdlc-run →
        #    active_run_id (record mirror) → possibly-stale caller-supplied id.
        candidate: str | None = None
        try:
            signal = read_supervised_run_signal(issue_number, working_dir=working_dir)
            if signal and signal.get("run_id"):
                candidate = signal["run_id"]
        except Exception as e:  # pragma: no cover - fail-open
            logger.debug("reestablish_run_id: signal read failed: %s", e)
        if not candidate:
            candidate = _active_run_id_for_issue(issue_number)
        if not candidate:
            candidate = prior_run_id

        # 3. Re-acquire (verified reuse) or fresh mint on a free/expired lock.
        from tools.sdlc_session_ensure import ensure_session

        result = ensure_session(issue_number, reuse_run_id=candidate) or {}

        # A live supervised run can only surface here on a bare fall-through
        # (candidate was None); honor it.
        if result.get("reason") == "SUPERVISED_RUN_ACTIVE" and result.get("run_id"):
            return result["run_id"]

        new_run_id = result.get("run_id")
        if not new_run_id:
            # ISSUE_LOCKED (foreign) / RUN_BIND_FAILED / {} — unhealable.
            return None

        # Terminal guard: a FRESH mint (not a corroborated reuse) on a
        # MERGE-completed pipeline resurrects a finished run — decline + release
        # the just-acquired lease so it does not linger.
        if new_run_id != candidate and _pipeline_is_terminal(issue_number):
            try:
                from models.session_lifecycle import release_issue_lock

                release_issue_lock(issue_number, new_run_id)
            except Exception as e:  # pragma: no cover - fail-open
                logger.debug("reestablish_run_id: terminal-guard release failed: %s", e)
            return None

        return new_run_id
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("reestablish_run_id: failed for issue #%s: %s", issue_number, e)
        return None


def _log_path() -> Path:
    """Resolve ``logs/sdlc_run_identity.log`` at the git-common-dir root.

    A worktree-isolated run (``.worktrees/{slug}/``) must NOT write to a
    cwd-relative ``logs/`` — that file would be invisible to an operator
    tailing the canonical repo's log. ``git rev-parse --path-format=absolute
    --git-common-dir`` yields the main checkout's ``.git`` dir from any
    worktree; its parent is the shared repo root. Falls back to cwd on error.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=_GIT_ROOT_TIMEOUT_S,
        )
        if out.returncode == 0 and out.stdout.strip():
            root = Path(out.stdout.strip()).parent
            return root / "logs" / "sdlc_run_identity.log"
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("log_run_identity_event: git-root resolution failed: %s", e)
    return Path.cwd() / "logs" / "sdlc_run_identity.log"


def _record_refusal_redis(
    issue_number: int | None,
    subcommand: str,
    reason: str | None,
    healed: bool,
) -> None:
    """Best-effort raw-Redis rolling counter/last-event for operators.

    Writes a compact hash at ``sdlc:run_identity:refusals:{issue}`` (a NEW,
    non-Popoto-managed observability key — never touching gated ledger state,
    per the plan's chicken-and-egg guard). Uses the same raw-Redis idiom as the
    issue lock. Fail-open: any error is swallowed.
    """
    if not issue_number:
        return
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        key = f"sdlc:run_identity:refusals:{issue_number}"
        pipe = _R.pipeline()
        pipe.hincrby(key, "attempts", 1)
        pipe.hincrby(key, "healed" if healed else "unhealed", 1)
        pipe.hset(
            key,
            mapping={
                "last_ts": datetime.now(UTC).isoformat(),
                "last_subcommand": subcommand,
                "last_reason": reason or "",
                "last_healed": "1" if healed else "0",
            },
        )
        pipe.execute()
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("_record_refusal_redis: update failed: %s", e)


def log_run_identity_event(
    issue_number: int | None,
    subcommand: str,
    reason: str | None,
    healed: bool,
    old_run_id: str | None,
    new_run_id: str | None,
) -> None:
    """Append one JSON line recording a self-heal attempt. Best-effort.

    ``healed`` is the ground-truth field separating a recovery from a genuine
    unhealable refusal. Fail-open: an I/O error never propagates into the
    calling tool. Also bumps the raw-Redis observability counter (independently
    fail-open) so an operator without log access can still see the freeze.
    """
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "issue": issue_number,
                "subcommand": subcommand,
                "reason": reason,
                "healed": bool(healed),
                "old_run_id": old_run_id,
                "new_run_id": new_run_id,
            }
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:  # pragma: no cover - fail-open
        logger.debug("log_run_identity_event: append failed: %s", e)
    _record_refusal_redis(issue_number, subcommand, reason, healed)


def heal_run_identity(
    issue_number: int | None,
    prior_run_id: str | None,
    subcommand: str,
    reason: str | None,
    working_dir: str | None = None,
) -> str | None:
    """Attempt to re-establish identity once and record the attempt.

    Convenience wrapper the four state-mutating CLIs call at their refusal
    site: it runs :func:`reestablish_run_id` and logs the outcome to the
    visibility sink. Returns the healed ``run_id`` (retry the write under it)
    or ``None`` (refusal stands). Never raises.
    """
    healed = reestablish_run_id(issue_number, prior_run_id, working_dir=working_dir)
    log_run_identity_event(
        issue_number,
        subcommand,
        reason,
        healed=bool(healed),
        old_run_id=prior_run_id,
        new_run_id=healed,
    )
    return healed


def heal_missing_run_id(
    issue_number: int | None,
    subcommand: str,
    working_dir: str | None = None,
) -> str | None:
    """Front-gate heal for a state-mutating call that carries **no** ``--run-id``.

    Replaces the unconditional ``RUN_ID_REQUIRED`` hard-exit: a resumed turn
    that lost its ``run_id`` from context can still re-establish identity from
    the environment (``.sdlc-run`` / ``active_run_id`` / a live supervisor).
    Returns a ``run_id`` to write under, or ``None`` — in which case the caller
    keeps the original ``RUN_ID_REQUIRED`` refusal. Records the attempt. Never
    raises.
    """
    if not issue_number:
        return None
    return heal_run_identity(
        issue_number,
        None,
        subcommand,
        "RUN_ID_REQUIRED",
        working_dir=working_dir,
    )


def maybe_heal_after_write(
    result: dict | None,
    prior_run_id: str | None,
    issue_number: int | None,
    subcommand: str,
    working_dir: str | None = None,
) -> str | None:
    """Post-write heal for a refusal a state-mutating write already returned.

    Inspects ``result`` for a run-identity refusal (``LEASE_ABSENT`` / stale
    ``ISSUE_LOCKED`` echoes via :func:`classify_refusal`); when one is present
    **and** an ``issue_number`` keys the heal, attempts re-establishment once.
    Returns a healed ``run_id`` to retry the write under, or ``None`` (refusal
    stands — a foreign live lease, an unkeyed call, or an unrecoverable state).

    The healed id **may equal** ``prior_run_id``: that means the SAME run's
    lapsed lease was re-acquired (the lock is now held again), which is the
    stale-``--run-id`` + lapsed-lease resume — the bug's most common real
    manifestation (the ``session-ensure --reuse-run-id`` live-ops pattern). A
    re-acquired same-id is a legitimate retry trigger, not a no-op, so it is
    returned. The at-most-once contract is the caller's: it retries the write
    exactly once under the returned id and never re-enters this path, so a
    same-id return cannot loop. Never raises.
    """
    if not issue_number:
        return None
    reason = classify_refusal(result)
    if not reason:
        return None
    return heal_run_identity(
        issue_number,
        prior_run_id,
        subcommand,
        reason,
        working_dir=working_dir,
    )
