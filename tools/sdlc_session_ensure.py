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
    {"session_id": "<id>", "created": true}  -- new session created
    {"session_id": "<id>", "created": false} -- existing session found
    {} on error
    {"orphans": [...], "count": N, "killed": false} -- --kill-orphans --dry-run
    {"results": [...], "count": N, "failures": M, "killed": true} -- --kill-orphans
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Idle window (in seconds) before a sdlc-local session is considered a zombie
# orphan. A session is only reaped when it has had no activity (no heartbeat AND
# no stage_states write refreshing updated_at) for this long. Sessions active
# within this window are exempt — including live worker-less CLI pipelines that
# never write last_heartbeat_at but do refresh updated_at on every stage
# advance (#1676).
ORPHAN_AGE_SECONDS = 600


def ensure_session(issue_number: int, issue_url: str | None = None) -> dict:
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
                                return {"session_id": env_session_id, "created": False}
                            # Env session is live but does NOT own this issue.
                            # Prefer an existing issue-scoped session if one
                            # exists; otherwise fall through to the issue
                            # lookup / create path below.
                            from tools._sdlc_utils import find_session_by_issue

                            owned = find_session_by_issue(issue_number)
                            if owned is not None:
                                owned_id = getattr(owned, "session_id", None)
                                if owned_id:
                                    return {"session_id": owned_id, "created": False}
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
                return {"session_id": session_id, "created": False}

        # No existing session — create one
        from models.agent_session import AgentSession

        local_session_id = f"sdlc-local-{issue_number}"

        # Check if a session with this exact ID already exists (idempotent)
        try:
            existing_by_id = list(AgentSession.query.filter(session_id=local_session_id))
            if existing_by_id:
                return {"session_id": local_session_id, "created": False}
        except Exception:
            pass

        # Build kwargs for create_local
        kwargs = {}
        if issue_url:
            kwargs["issue_url"] = issue_url

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

        # Transition from default pending to running via lifecycle module
        try:
            from models.session_lifecycle import transition_status

            transition_status(session, "running", "local SDLC session started")
        except Exception as e:
            logger.debug(f"sdlc_session_ensure: transition_status failed: {e}")
            # Session is created but in pending state — still usable

        return {"session_id": local_session_id, "created": True}

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
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
