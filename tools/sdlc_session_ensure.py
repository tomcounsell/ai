"""CLI tool for ensuring a local SDLC session exists for an issue.

Creates or finds an AgentSession keyed by issue number for local Claude Code
sessions where no bridge-injected session ID is available.

Usage:
    python -m tools.sdlc_session_ensure --issue-number 941
    python -m tools.sdlc_session_ensure --issue-number 941 --issue-url https://github.com/tomcounsell/ai/issues/941
    python -m tools.sdlc_session_ensure --kill-orphans --dry-run
    python -m tools.sdlc_session_ensure --kill-orphans
    python -m tools.sdlc_session_ensure --help

Exit codes:
    0 -- always (errors print {} and exit 0, never crash the calling skill)

Output:
    {"session_id": "<id>", "created": true, "run_id": "<hex>"}  -- new session created
    {"session_id": "<id>", "created": false, "run_id": "<hex>"} -- existing session found
    {"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": ...,
     "owner_run_id": ..., "owner_session_id": ...}
        -- a BARE session-ensure (no --reuse-run-id) found a LIVE supervised-run
        signal for this issue (issue #2026, WS1). The supervisor already owns
        the run; this mints NOTHING and returns the supervisor's run_id so the
        caller inherits it (pass it back via --run-id / --reuse-run-id). A
        stale/expired signal falls through to normal standalone semantics.
    {"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": ...,
     "owner_session_id": ..., "orphaned_lock": ...}
        -- a foreign live run holds the issue lock with no supervised-run
        signal to inherit (orphaned_lock=true means the owning run died before
        its next renewal; frees within the TTL)
    {"error": "RUN_BIND_FAILED", ...} -- lock acquired but the run_id could not be
        persisted to the session record (lock released via compare-and-delete)
    {} on error
    {"orphans": [...], "count": N, "killed": false} -- --kill-orphans --dry-run
    {"results": [...], "count": N, "failures": M, "killed": true} -- --kill-orphans

Run identity (issue #2003): ensure_session is the EXCLUSIVE minting site for
the pipeline run_id. Each top-level call generates a fresh uuid-hex candidate
and contests the issue lock (SET NX EX carrying it). The winner's run_id is
emitted in the JSON output and mirrored to AgentSession.active_run_id; every
state-mutating sdlc-tool call must then pass it back via --run-id. There is
deliberately NO adopt-from-record branch: a live foreign holder always means
ISSUE_LOCKED, regardless of what active_run_id the record carries. Recovery
after run_id loss = re-run session-ensure (ISSUE_LOCKED until the <=300s lock
TTL lapses, then a fresh contest mints a new run_id).

Verified reuse (#2003 cycle-3 BLOCKER 1): a caller that already holds a
run_id for this issue from an earlier stage of the SAME top-level invocation
passes it back via --reuse-run-id. The claim is honored only when the caller
can PROVE continuity: the live lock's owner run_id equals the claim, or the
lock is free and the session record's active_run_id equals the claim. A
verified claim renews/re-acquires under that same run_id instead of minting
fresh -- this is what lets the per-stage /sdlc router survive its own prior
stage's live lock instead of self-wedging at every stage boundary. An
unverified claim is silently ignored (falls through to the fresh-mint
contest); a live foreign holder still always means ISSUE_LOCKED. This is
claim-echo with proof, never adoption: the run_id must arrive FROM the
caller, and the lock/record must corroborate it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Idle window (in seconds) before a sdlc-local session is considered a zombie
# orphan. A session is only reaped when it has had no activity (no heartbeat AND
# no stage_states write refreshing updated_at) for this long. Sessions active
# within this window are exempt — including live worker-less CLI pipelines that
# never write last_heartbeat_at but do refresh updated_at on every stage
# advance (#1676).
ORPHAN_AGE_SECONDS = 600


def _validated_reuse_candidate(issue_number: int, session, reuse_run_id: str) -> str | None:
    """Return ``reuse_run_id`` when the caller proves it is the same top-level
    invocation; ``None`` otherwise (caller falls back to a fresh mint contest).

    Proof is one of (#2003 cycle-3 BLOCKER 1):

    1. **Live lock owner match**: the lock's owner run_id equals the claim --
       the caller IS the current holder. This is the consecutive-stage case:
       the prior stage's completion marker renewed the lock under this id,
       and the next /sdlc invocation seconds later carries it back.
    2. **Free lock + record mirror match**: the lock lapsed AND the session
       record's ``active_run_id`` equals the claim -- the mirror written by
       this pipeline's own earlier ``ensure_session()`` corroborates the
       claim after a TTL lapse.

    Anything else returns ``None`` -- never an error: an unverified claim
    simply falls through to the normal fresh-mint contest, where a live
    foreign holder still yields ISSUE_LOCKED. The no-adopt invariant holds:
    this helper never reads a run_id OUT of the lock or the record to hand
    to the caller; it only echoes back a claim the caller already carried,
    and only when the lock/record corroborates it.
    """
    from models.session_lifecycle import touch_issue_lock

    sid = getattr(session, "session_id", None) or ""
    try:
        peek = touch_issue_lock(issue_number, reuse_run_id, session_id=sid, peek=True)
    except Exception as e:
        logger.debug(
            "sdlc_session_ensure: reuse peek failed for issue #%s (%s: %s) -- "
            "falling back to fresh mint",
            issue_number,
            type(e).__name__,
            e,
        )
        return None

    if peek.acquired and peek.owner_run_id == reuse_run_id:
        # Live lock, owned by the claimed run_id: the caller is the holder.
        return reuse_run_id
    if peek.acquired and peek.owner_run_id is None:
        # Lock free: honor the claim only when the record mirror vouches for it.
        if getattr(session, "active_run_id", None) == reuse_run_id:
            return reuse_run_id
    return None


def _acquire_run_lock_and_bind(
    issue_number: int, session, reuse_run_id: str | None = None
) -> tuple[str | None, dict | None]:
    """Mint a fresh run_id candidate, contest the issue lock, and bind the winner.

    Called immediately before EVERY return point in ensure_session() (issues
    #1954/#2003) so no branch -- early-return or create-and-claim -- can skip
    the lock contest. Minting is decided by the LOCK, never by session
    status: each top-level call generates a fresh uuid-hex candidate and
    attempts ``SET NX EX`` carrying it. There is NO adopt-from-record branch
    (#2003 cycle-1 BLOCKER 1): a live foreign holder always means
    ISSUE_LOCKED, regardless of what ``active_run_id`` the record carries.

    Verified reuse (#2003 cycle-3 BLOCKER 1): when ``reuse_run_id`` is given
    AND :func:`_validated_reuse_candidate` corroborates the claim (live lock
    owner match, or free lock + record mirror match), the candidate is the
    claimed id instead of a fresh uuid -- the ``touch_issue_lock`` below then
    renews (same-owner EXPIRE) or re-acquires (SET NX on a free key) under
    the caller's existing identity. An unverified claim silently falls back
    to the fresh candidate, preserving no-adopt for foreign/stale callers.

    On acquisition, the candidate is saved to ``session.active_run_id`` and
    read back from Redis (post-save readback, Race 3). On save failure or
    readback mismatch, the lock is released via COMPARE-AND-DELETE
    (``release_issue_lock`` -- never a raw DEL, cycle-2 CONCERN 2) so the
    next caller acquires immediately instead of waiting out the 300s TTL.

    Target-repo pinning (issue #2012): this is the ONE place ``target_repo``
    is resolved for the issue-keyed ``PipelineLedger`` -- the process env
    (``GH_REPO``/``SDLC_TARGET_REPO``, set authoritatively by
    ``sdk_client.py``) is trustworthy here regardless of a takeover
    session's foreign slug or cwd. Resolved exactly once per call and
    passed into every ``touch_issue_lock`` call below so the lock payload
    carries it for every subsequent writer/reader to read from the lease
    instead of re-resolving via ``gh repo view`` per write. A ``None``
    resolution is passed through as-is -- lock acquisition is never blocked
    on repo resolution; a missing pinned repo is handled downstream as an
    observable degradation by the issue-keyed ledger's writers/readers, not
    here.

    Args:
        issue_number: The issue whose lock is contested.
        session: The AgentSession object to bind the run_id onto.

    Returns:
        ``(run_id, None)`` on success; ``(None, error_dict)`` when blocked
        (``ISSUE_LOCKED`` shape) or when binding failed (``RUN_BIND_FAILED``
        shape, lock already released).
    """
    from models.session_lifecycle import (
        ISSUE_LOCK_TTL_SECONDS,
        release_issue_lock,
        touch_issue_lock,
    )
    from tools._sdlc_utils import _resolve_target_repo

    session_id = getattr(session, "session_id", None) or ""

    # Supervised-run signal check (issue #2026, WS1). A BARE ensure (no
    # reuse_run_id) invoked while a LIVE supervised-run signal exists for this
    # issue must NOT contest the lock or mint — it returns the named
    # SUPERVISED_RUN_ACTIVE refusal carrying the supervisor's run_id so the
    # caller inherits that identity. This is structurally unbypassable: the
    # only path a bare ensure has under a live signal is "use the supervisor's
    # run_id", never a fresh mint (Risk 3). A stale/expired signal (lock
    # released or TTL lapsed) falls through to normal standalone semantics
    # below. Fail-open: any signal-read error degrades to "no live signal" so a
    # Redis hiccup never wedges the pipeline. Reuse callers (--reuse-run-id)
    # skip this — they are the supervisor's own consecutive-stage re-ensures,
    # verified against the live lock further down.
    if not reuse_run_id:
        try:
            from agent.supervised_run import supervised_run_status

            supervised = supervised_run_status(
                issue_number, working_dir=getattr(session, "working_dir", None)
            )
        except Exception as e:
            logger.debug(
                "sdlc_session_ensure: supervised-run status check failed for issue #%s "
                "(%s: %s) -- proceeding with standalone mint",
                issue_number,
                type(e).__name__,
                e,
            )
            supervised = None
        if supervised is not None and supervised.live:
            logger.debug(
                "sdlc_session_ensure: issue #%s has a LIVE supervised run (run_id=%s) -- "
                "bare ensure refuses with SUPERVISED_RUN_ACTIVE; inherit the run_id, do not mint",
                issue_number,
                supervised.run_id,
            )
            return None, {
                "blocked": True,
                "reason": "SUPERVISED_RUN_ACTIVE",
                "run_id": supervised.run_id,
                "owner_run_id": supervised.run_id,
                "owner_session_id": supervised.session_id,
            }

    candidate = uuid.uuid4().hex
    if reuse_run_id:
        candidate = _validated_reuse_candidate(issue_number, session, reuse_run_id) or candidate

    target_repo = _resolve_target_repo()

    lock_result = touch_issue_lock(
        issue_number,
        candidate,
        session_id=session_id,
        ttl=ISSUE_LOCK_TTL_SECONDS,
        target_repo=target_repo,
    )
    if not lock_result.acquired:
        logger.debug(
            "sdlc_session_ensure: issue #%s lock held by a foreign run (run_id=%s, "
            "session=%s) -- blocked",
            issue_number,
            lock_result.owner_run_id,
            lock_result.owner_session_id,
        )
        # Follow-up peek for the orphaned_lock flag (cycle-3 nit): the
        # non-peek refusal path does not compute it, and callers deciding
        # whether to wait out the TTL need the signal. Best-effort.
        orphaned = False
        try:
            orphaned = touch_issue_lock(
                issue_number,
                candidate,
                session_id=session_id,
                peek=True,
                target_repo=target_repo,
            ).orphaned_lock
        except Exception as peek_err:
            logger.debug(
                "sdlc_session_ensure: orphan peek failed for issue #%s (%s: %s)",
                issue_number,
                type(peek_err).__name__,
                peek_err,
            )
        return None, {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_run_id": lock_result.owner_run_id,
            "owner_session_id": lock_result.owner_session_id,
            "orphaned_lock": orphaned,
        }

    # Acquired: bind the run_id to the session record (inspection mirror +
    # the identity source for the in-process renewal paths).
    try:
        session.active_run_id = candidate
        session.save()
    except Exception as e:
        release_issue_lock(issue_number, candidate)
        logger.debug(
            "sdlc_session_ensure: active_run_id save failed for %s (%s: %s) -- "
            "lock released via compare-and-delete",
            session_id,
            type(e).__name__,
            e,
        )
        return None, {
            "error": "RUN_BIND_FAILED",
            "reason": f"active_run_id save failed ({type(e).__name__})",
            "session_id": session_id,
        }

    # Post-save readback: assert the record really carries the lock's run_id.
    try:
        from models.agent_session import AgentSession

        fresh_rows = list(AgentSession.query.filter(session_id=session_id))
        fresh = fresh_rows[0] if fresh_rows else None
        readback_run_id = getattr(fresh, "active_run_id", None) if fresh is not None else None
    except Exception as e:
        release_issue_lock(issue_number, candidate)
        logger.debug(
            "sdlc_session_ensure: post-save readback failed for %s (%s: %s) -- "
            "lock released via compare-and-delete",
            session_id,
            type(e).__name__,
            e,
        )
        return None, {
            "error": "RUN_BIND_FAILED",
            "reason": f"post-save readback failed ({type(e).__name__})",
            "session_id": session_id,
        }

    if readback_run_id != candidate:
        release_issue_lock(issue_number, candidate)
        logger.debug(
            "sdlc_session_ensure: post-save readback mismatch for %s "
            "(expected %s, read %s) -- lock released via compare-and-delete",
            session_id,
            candidate,
            readback_run_id,
        )
        return None, {
            "error": "RUN_BIND_FAILED",
            "reason": "post-save readback mismatch",
            "session_id": session_id,
        }

    # Publish/refresh the supervised-run signal (issue #2026, WS1): record the
    # verified run_id as the run-scoped signal a stage fork reads at spawn, so
    # the supervisor is the single lock owner and a subsequent BARE ensure
    # under this live signal is refused (SUPERVISED_RUN_ACTIVE) rather than
    # minting a competitor. Best-effort — a signal-write failure never fails
    # the ensure; the lock is already the authoritative owner record.
    try:
        from agent.supervised_run import write_supervised_run_signal

        write_supervised_run_signal(
            issue_number,
            candidate,
            session_id=session_id,
            working_dir=getattr(session, "working_dir", None),
        )
    except Exception as e:
        logger.debug(
            "sdlc_session_ensure: supervised-run signal write failed for issue #%s "
            "(%s: %s) -- lock remains authoritative",
            issue_number,
            type(e).__name__,
            e,
        )

    return candidate, None


def ensure_session(
    issue_number: int,
    issue_url: str | None = None,
    reuse_run_id: str | None = None,
) -> dict:
    """Ensure a local AgentSession exists for the given issue number.

    Resolution order (env-vs-issue reconciliation — concern C1, #1671/#1672):
    1. **Env-var short-circuit WITH issue-ownership check**: If VALOR_SESSION_ID
       or AGENT_SESSION_ID is set and resolves to a live (non-terminal) PM
       session, reconcile it against the requested issue number:
         - If that env session already **owns the issue** (its ``issue_url``
           endswith ``/issues/{issue_number}``), return it without creating
           anything — this is the legitimate bridge case (#1147 dedup), a true
           no-op, no ``find_session_by_issue`` detour.
         - If the env session exists but does **not** own the issue, consult
           :func:`find_session_by_issue` and prefer an existing issue-scoped
           session (e.g. ``sdlc-local-{N}``) over the divergent env session.
           This is the #1671 case: a forked subagent inherited a parent's
           ``VALOR_SESSION_ID`` that points at a different issue's session.
    2. **Issue-based lookup**: Scan PM sessions for a matching issue_url or
       message_text (case-insensitive word-boundary regex).
    3. **Create**: Fall through to creating a new sdlc-local-{N} session.

    The env short-circuit is *not* a blind reorder: it is kept only when the env
    session owns the issue, so the bridge dedup contract (#1147) holds and no
    duplicate is ever created for the bridge case. When no env session exists,
    the fall-through (issue lookup → create) is unchanged.

    The short-circuit falls through to the legacy/issue path when:
    - The env var is unset or empty
    - The env-resolved session does not exist in Redis (stale env)
    - The env-resolved session is not a PM session (e.g., a Dev session)
    - The env-resolved session is in a terminal status (completed, killed, etc.)
    - The env-resolved session is live but does NOT own the requested issue
      (reconciliation prefers the issue-scoped session)

    Args:
        issue_number: GitHub issue number.
        issue_url: Optional full issue URL (e.g., https://github.com/owner/repo/issues/N).
        reuse_run_id: Optional run_id the caller already holds for this issue
            from an earlier stage of the SAME top-level invocation. Honored
            only when verified against the live lock or the record mirror
            (see :func:`_validated_reuse_candidate`); otherwise ignored.

    Returns:
        Dict with session_id and created flag, or empty dict on error.
    """
    if not issue_number or issue_number < 1:
        logger.debug(f"sdlc_session_ensure: invalid issue_number {issue_number}")
        return {}

    try:
        # Env-var short-circuit: bridge-initiated sessions inject VALOR_SESSION_ID
        # into the subprocess environment. When set to a live PM session, return
        # it immediately — no scan, no create.
        env_session_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
        if env_session_id:
            try:
                from tools._sdlc_utils import find_session

                resolved = find_session(session_id=env_session_id)
                if resolved is not None:
                    # Gate on PM session type so PM stage_states never land on
                    # a Dev/Teammate session during cross-role debugging.
                    if getattr(resolved, "session_type", None) == "eng":
                        # Gate on non-terminal status (AD1): if the bridge session
                        # finalized between env injection and this call, fall
                        # through so we do not write stage state to a dead record.
                        from models.session_lifecycle import TERMINAL_STATUSES

                        status = getattr(resolved, "status", None)
                        if status not in TERMINAL_STATUSES:
                            # Reconciliation (C1, #1671): keep the env session
                            # only when it OWNS the requested issue. Guard
                            # issue_url against non-string values (a MagicMock
                            # default or None would otherwise truthily match).
                            env_issue_url = getattr(resolved, "issue_url", None) or ""
                            if env_issue_url.endswith(f"/issues/{issue_number}"):
                                # Legitimate bridge case — true no-op, no detour.
                                run_id, err = _acquire_run_lock_and_bind(
                                    issue_number, resolved, reuse_run_id=reuse_run_id
                                )
                                if err is not None:
                                    return err
                                return {
                                    "session_id": env_session_id,
                                    "created": False,
                                    "run_id": run_id,
                                }
                            # Env session is live but does NOT own this issue.
                            # Prefer an existing issue-scoped session if one
                            # exists; otherwise fall through to the issue
                            # lookup / create path below.
                            from tools._sdlc_utils import find_session_by_issue

                            owned = find_session_by_issue(issue_number)
                            if owned is not None:
                                owned_id = getattr(owned, "session_id", None)
                                if owned_id:
                                    run_id, err = _acquire_run_lock_and_bind(
                                        issue_number, owned, reuse_run_id=reuse_run_id
                                    )
                                    if err is not None:
                                        return err
                                    return {
                                        "session_id": owned_id,
                                        "created": False,
                                        "run_id": run_id,
                                    }
                            # No issue-scoped session yet — fall through to the
                            # legacy issue lookup + create path. (Do NOT return
                            # the divergent env session.)
            except Exception as e:
                logger.debug(f"sdlc_session_ensure: env short-circuit failed: {e}")
                # Fall through to the legacy path on any error.

        from tools._sdlc_utils import find_session_by_issue

        existing = find_session_by_issue(issue_number)
        if existing:
            session_id = getattr(existing, "session_id", None)
            if session_id:
                run_id, err = _acquire_run_lock_and_bind(
                    issue_number, existing, reuse_run_id=reuse_run_id
                )
                if err is not None:
                    return err
                return {"session_id": session_id, "created": False, "run_id": run_id}

        # No existing session — create one
        from models.agent_session import AgentSession

        local_session_id = f"sdlc-local-{issue_number}"

        # Check if a session with this exact ID already exists (idempotent)
        try:
            existing_by_id = list(AgentSession.query.filter(session_id=local_session_id))
            if existing_by_id:
                run_id, err = _acquire_run_lock_and_bind(
                    issue_number, existing_by_id[0], reuse_run_id=reuse_run_id
                )
                if err is not None:
                    return err
                return {"session_id": local_session_id, "created": False, "run_id": run_id}
        except Exception:
            pass

        # Build kwargs for create_local
        kwargs = {}
        if issue_url:
            kwargs["issue_url"] = issue_url
        # Write-once mirror field (issue #1954): issue_number is set ONLY here,
        # at session creation. It is never re-written on the four early-return
        # (continuing-session) branches above -- see the module note on
        # touch_issue_lock() for why ownership itself is never compared via
        # this field (or session_id).
        kwargs["issue_number"] = issue_number

        # Non-executable ledger flag (issue #2042): mark this CLI-created anchor
        # as a ledger row, not a live executable session. Must be set BEFORE
        # create_local() is called (not in a follow-up write) so the flag is
        # already present in the earliest window a worker could observe the
        # row -- closing the race where a worker claims the row before a
        # separate write lands.
        kwargs["is_ledger"] = True

        # Fix A (issue #1741): populate the originating intent so the PM prime has a real
        # goal anchor. Without this, message_text=None propagates to the executor and the
        # granite PM is primed with "MESSAGE: None" — silently producing no-op [/complete].
        # This must be a plain natural-language instruction referencing issue #N so the PM
        # can read the issue body for the goal. Steering messages are course-corrections
        # toward this goal, never redefinitions of it.
        kwargs["message_text"] = (
            f"Run the full SDLC pipeline for issue #{issue_number}. "
            f"Read the issue body for the work to be done"
            + (f" ({issue_url})." if issue_url else ".")
        )

        from tools.valor_session import (
            _resolve_project_working_directory,
            resolve_project_key,
        )

        # Resolve project_key from cwd (raises if unmatched — caught below by
        # the broad except Exception, which returns {} for idempotent failure).
        project_key = resolve_project_key(os.getcwd())
        # Derive working_dir from projects.json, NOT os.getcwd(). This enforces
        # the immutable project→repo pairing: the session runs in the repo
        # declared for its project_key, not wherever the caller happens to be.
        repo_root, _ = _resolve_project_working_directory(project_key)

        session = AgentSession.create_local(
            session_id=local_session_id,
            project_key=project_key,
            working_dir=str(repo_root),
            session_type="eng",
            **kwargs,
        )

        # Transition from default pending to running via lifecycle module.
        # Narrow SETNX run-claim (issue #1817 B2): this session was just
        # created by this call, so contention is unlikely, but the claim is
        # applied uniformly at every pending->running call site so no actor
        # (worker pop loop, CLI resume, catchup/reflections drip) is exempt.
        # See models.session_lifecycle.claim_pending_run for the rationale.
        try:
            from models.session_lifecycle import claim_pending_run, transition_status

            if claim_pending_run(session.session_id, worker_id="sdlc-session-ensure"):
                transition_status(session, "running", "local SDLC session started")
            else:
                logger.debug(
                    "sdlc_session_ensure: lost run-claim for %s -- leaving pending",
                    session.session_id,
                )
        except Exception as e:
            logger.debug(f"sdlc_session_ensure: transition_status failed: {e}")
            # Session is created but in pending state — still usable

        run_id, err = _acquire_run_lock_and_bind(issue_number, session, reuse_run_id=reuse_run_id)
        if err is not None:
            return err
        return {"session_id": local_session_id, "created": True, "run_id": run_id}

    except Exception as e:
        # ProjectKeyResolutionError and ProjectsConfigUnavailableError both
        # land here intentionally — if the project→repo pairing can't be
        # resolved, we return {} rather than creating a mis-scoped session.
        logger.debug(
            "sdlc_session_ensure: ensure_session failed: %s (%s)",
            e,
            type(e).__name__,
        )
        return {}


def _last_activity_at(session):
    """Return the most recent liveness timestamp for a session, or None.

    A CLI-driven (worker-less) ``sdlc-local-*`` pipeline never writes
    ``last_heartbeat_at`` — that field is stamped only by the worker's session
    executor. But every dispatch/verdict/meta write a live local pipeline makes
    goes through ``tools.stage_states_helpers.update_stage_states``, which calls
    ``session.save()`` and stamps ``updated_at`` (see
    ``AgentSession.save`` → ``utc_now()``). So ``updated_at`` IS a liveness
    signal the local pipeline produces naturally.

    Precedence (most→least authoritative as a "last activity" proxy):
    ``updated_at`` → ``started_at`` → ``created_at``. The fallbacks cover
    sessions that were created but have not yet written stage_states (no
    ``updated_at`` stamp) — they remain reapable on the ``created_at`` clock,
    preserving the original genuinely-dead-orphan semantics.
    """
    for attr in ("updated_at", "started_at", "created_at"):
        ts = getattr(session, attr, None)
        if ts is not None:
            return ts
    return None


def _iter_orphan_sessions():
    """Yield zombie sdlc-local PM sessions suitable for --kill-orphans.

    A session is considered a zombie orphan when ALL of these hold:
    - ``session_type == "eng"``
    - ``status == "running"``
    - ``session_id`` starts with ``"sdlc-local-"``
    - ``last_heartbeat_at`` is None (never received a worker turn)
    - **last activity** is older than ``ORPHAN_AGE_SECONDS`` (default 10 min),
      where last activity = ``updated_at`` (falling back to ``started_at``,
      then ``created_at``).

    The last-activity check (rather than a bare ``created_at`` check) is what
    keeps a LIVE worker-less pipeline alive (#1676). On a skills-only machine
    there is no worker to write ``last_heartbeat_at``, so a healthy CLI-driven
    ``/do-sdlc`` run matched the old (heartbeat-None AND old-created_at) zombie
    criteria by construction after 10 minutes — and ``--kill-orphans`` would
    then ``finalize(killed)`` it mid-run, destroying its ``stage_states`` (the
    durable dispatch trail and verdicts the router depends on). Because every
    stage_states write refreshes ``updated_at`` via ``session.save()``, a
    pipeline that advanced a stage within the last ``ORPHAN_AGE_SECONDS`` is now
    exempt regardless of whether a worker heartbeat exists. Only a session that
    is BOTH heartbeat-less AND has not advanced any stage for the full window is
    treated as genuinely dead.

    Sessions whose ``session_id`` does not start with ``"sdlc-local-"`` are
    NEVER yielded — bridge sessions and other running PM sessions are out of
    scope for this cleanup (the bridge watchdog handles stuck bridge sessions).

    Yields:
        AgentSession instances matching the orphan criteria.
    """
    from models.agent_session import AgentSession

    now = datetime.now(UTC)

    try:
        eng_running = list(AgentSession.query.filter(session_type="eng", status="running"))
    except Exception as e:
        logger.debug(f"_iter_orphan_sessions: query failed: {e}")
        return

    for s in eng_running:
        sid = getattr(s, "session_id", None) or ""
        if not sid.startswith("sdlc-local-"):
            continue
        if getattr(s, "last_heartbeat_at", None) is not None:
            continue
        last_activity = _last_activity_at(s)
        if last_activity is None:
            continue
        try:
            idle_seconds = (now - last_activity).total_seconds()
        except Exception:
            continue
        if idle_seconds >= ORPHAN_AGE_SECONDS:
            yield s


def _kill_orphans(dry_run: bool) -> dict:
    """Execute the --kill-orphans CLI path.

    Args:
        dry_run: If True, list zombies without modifying. If False, finalize each
            via ``finalize_session()`` (never ``transition_status()`` — that helper
            rejects terminal statuses by design).

    Returns:
        JSON-serializable dict with orphan/result details. Exit code is always 0
        at the CLI layer regardless of per-session failures; callers inspect the
        ``failures`` count.
    """
    orphans = list(_iter_orphan_sessions())

    # Observability signal (O1): emit a single stderr line when non-zero count
    # so scheduled cleanup runs log evidence of any regression in the
    # short-circuit. Stdout stays parseable as JSON.
    if orphans:
        print(
            f"[sdlc_session_ensure] found {len(orphans)} zombie sdlc-local session(s)",
            file=sys.stderr,
        )

    if dry_run:
        return {
            "orphans": [
                {
                    "session_id": getattr(s, "session_id", None),
                    "created_at": (
                        getattr(s, "created_at", None).isoformat()
                        if getattr(s, "created_at", None)
                        else None
                    ),
                    "issue_url": getattr(s, "issue_url", None),
                }
                for s in orphans
            ],
            "count": len(orphans),
            "killed": False,
        }

    # Real run: finalize each session. Each call runs inside its own try/except
    # so per-session failures are reported in the payload, never raised.
    from models.session_lifecycle import finalize_session

    results = []
    failures = 0
    for s in orphans:
        sid = getattr(s, "session_id", None)
        try:
            finalize_session(
                s,
                "killed",
                reason="zombie sdlc-local session cleanup",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )
            results.append({"session_id": sid, "result": "killed"})
        except Exception as e:
            failures += 1
            results.append({"session_id": sid, "result": "failed", "error": str(e)})

    return {
        "results": results,
        "count": len(orphans),
        "failures": failures,
        "killed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure a local SDLC session exists for an issue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number (required unless --kill-orphans is set)",
    )
    parser.add_argument(
        "--issue-url",
        default=None,
        help="Full GitHub issue URL (optional, used for issue_url field)",
    )
    parser.add_argument(
        "--reuse-run-id",
        default=None,
        help="Run_id already held for this issue from an earlier stage of the SAME "
        "top-level invocation. Verified against the live lock (owner match) or, on a "
        "free lock, against the session record's active_run_id; a verified claim "
        "renews/re-acquires under that id instead of minting fresh. An unverified "
        "claim is ignored (fresh mint contest; foreign holder still ISSUE_LOCKED).",
    )
    parser.add_argument(
        "--kill-orphans",
        action="store_true",
        help="Finalize zombie sdlc-local-* PM sessions (status=running, no heartbeat, "
        "older than ORPHAN_AGE_SECONDS). Mutually exclusive with --issue-number.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --kill-orphans: list zombie sessions without modifying them.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    if args.kill_orphans:
        if args.issue_number is not None:
            parser.error("--kill-orphans is mutually exclusive with --issue-number")
        try:
            result = _kill_orphans(dry_run=args.dry_run)
        except Exception as e:
            logger.debug(f"sdlc_session_ensure: --kill-orphans failed: {e}")
            result = {}
        print(json.dumps(result))
        return

    if args.issue_number is None:
        parser.error("--issue-number is required unless --kill-orphans is set")

    result = ensure_session(
        issue_number=args.issue_number,
        issue_url=args.issue_url,
        reuse_run_id=args.reuse_run_id,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
