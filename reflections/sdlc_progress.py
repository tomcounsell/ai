"""
reflections/sdlc_progress.py — Stalled SDLC pipeline auto-resume (state-layer).

Companion to `agent.session_health` (process-layer). This reflection inspects
open SDLC PRs (`session/sdlc-<N>`) and, when a lane is genuinely stalled, acts
on it instead of paging a human.

Gates, in order, per open non-draft SDLC PR:

    1-4  branch shape, not draft, issue still open, last commit older than
         ``SDLC_STALL_THRESHOLD_HOURS``
    5    lane liveness, read from the **issue lock**, never from session rows:
         ``session:issuelock:{N}`` is read directly and classified by
         ``models.session_lifecycle._lock_owner_is_live``. Key absent → free.
         Malformed payload, Redis error, or any exception → *unknown*, and
         unknown always declines to act. This is a **one-signal gate**: the
         lock is the sole liveness authority.
    6    this machine owns the project (``projects.<key>.machine``).

Then the action ladder, keyed on ``(slug, head-sha)``:

    escalation key already set                 -> stop acting entirely
    SDLC_STALL_RESUME_ENABLED=false            -> escalate once, stop
    attempts >= SDLC_STALL_RESUME_MAX_ATTEMPTS -> escalate once, stop
    action cooldown live                       -> skip this tick
    rung 1  live non-ledger eng session        -> steer_session(...)
    rung 2  resumable eng session              -> resume_session(...)
    rung 3  no target                          -> create_session(slug=sdlc-N)
    rung 4  action failed (non-benign)         -> escalate once, stop

Rungs 1 and 2 both rank candidates ``(same_lane, recency)``, so a session
carrying the stalled lane's own slug always wins and recency decides only among
equals. Acting on another lane's session would land this issue's work as commits
on that lane's branch.

"Escalate once and **stop acting**" is literal: once the escalation key exists
for a ``(slug, head-sha)``, every later tick skips the lane before it can claim
an action window. That, plus the invariant that the attempts TTL is never
shorter than the escalation TTL, is what makes a create-loop impossible.

**Benign races are not attempts.** Another actor (typically
``reflections.crash_recovery``, or the lane re-taking its own issue lock) can
get there first. A resume failure whose row re-reads non-terminal, and a create
refusal against a lock that is now live, charge no attempt, fire no escalation,
and return early. The steer rung has no benign-race branch: every
``steer_session`` failure is a real dead end and charges an attempt.

The reflection never raises. Every external boundary (lock read, session query,
steer, resume, create, Redis, telegram) logs a warning and continues, and every
gate that returns *unknown* leaves a short marker in ``findings`` so a Redis or
``gh`` degradation is visible rather than looking like a healthy quiet tick.

Configuration (all optional, all provisional and tunable):
    SDLC_STALL_THRESHOLD_HOURS              default 4    minimum age of last commit
    SDLC_STALL_RESUME_ENABLED               default true break-glass; false =
                                                         notification-only, still
                                                         escalate-once
    SDLC_STALL_RESUME_MAX_ATTEMPTS          default 3    attempts per (slug, sha)
    SDLC_STALL_CREATE_MAX_PER_TICK          default 1    creations per project per tick
    SDLC_STALL_RESUME_COOLDOWN_HOURS        default 1    action cooldown per (slug, sha)
    SDLC_STALL_ATTEMPTS_TTL_DAYS            default 30   TTL on the attempts key; floored
                                                         at the escalation TTL
    SDLC_STALL_ESCALATION_TTL_DAYS          default 30   TTL on the escalation key

See ``docs/features/pm-session-liveness.md`` for the full state-layer rationale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings

# _LOCK_KEY and _lock_says_live moved to reflections/utilities.py (#2717) so
# the sibling reflection reflections/sdlc_upvote_lanes.py can share the same
# liveness rule instead of forking it. Re-exported here so this module's own
# call sites (and this module's own name, ``sdlc_progress._LOCK_KEY``) keep
# resolving unchanged.
from reflections.utilities import (  # noqa: F401
    _LOCK_KEY,
    _lock_says_live,
    machine_owns_project,
    run_per_project_audit,
)

logger = logging.getLogger("reflections.sdlc_progress")

# Raw Redis namespace — NOT Popoto-managed. Pure bookkeeping per
# CLAUDE.md "no raw Redis on Popoto-managed keys" exception (precedent:
# docs_auditor.py REDIS_ISSUE_DEDUP_PREFIX).
#
# Three keys, three lifetimes. The action cooldown is short (retrying an action
# is cheap); the attempt budget is the convergence guard; the escalation key is
# the anti-ladder key that guarantees a human hears about a stalled head sha at
# most once.
_COOLDOWN_KEY = "sdlc:stall:resume:cooldown:{slug}:{sha}"
_ATTEMPTS_KEY = "sdlc:stall:resume:attempts:{slug}:{sha}"
_ESCALATED_KEY = "sdlc:stall:escalated:{slug}:{sha}"

# The issue lock, owned by models.session_lifecycle. Read directly (never via
# touch_issue_lock, which fails OPEN and so cannot express "unknown").
# _LOCK_KEY is imported from reflections.utilities above.

# Only branches matching session/sdlc-<N> are flagged. Excludes session/<other-slug>
# and ad-hoc branches — those aren't SDLC pipelines.
_SDLC_BRANCH_RE = re.compile(r"^session/sdlc-\d+$")

# --- Thresholds -------------------------------------------------------------
# Every one of these is provisional and tunable via the paired env var. Take
# them with a grain of salt: they were chosen from one observed incident, not
# from a measured distribution.
_DEFAULT_THRESHOLD_HOURS = 4
_DEFAULT_RESUME_ENABLED = True
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_CREATE_MAX_PER_TICK = 1
_DEFAULT_RESUME_COOLDOWN_HOURS = 1
# INVARIANT: attempts TTL >= escalation TTL. If the attempts key lapsed first,
# the budget would re-arm while the escalation key still suppressed the page —
# the ladder would dispatch a fresh MAX_ATTEMPTS actions per attempts-TTL
# window, silently, for the whole escalation window. Both keys are sha-scoped,
# so a short attempts TTL buys nothing anyway: a new commit mints fresh keys.
# Enforced at read time by _attempts_ttl_seconds().
_DEFAULT_ATTEMPTS_TTL_DAYS = 30
_DEFAULT_ESCALATION_TTL_DAYS = 30


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import, error-tolerant)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _env_float(name: str, default: float) -> float:
    """Read a numeric env override, falling back to the module default."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _threshold_seconds() -> int:
    return int(_env_float("SDLC_STALL_THRESHOLD_HOURS", _DEFAULT_THRESHOLD_HOURS) * 3600)


def _action_cooldown_seconds() -> int:
    return int(
        _env_float("SDLC_STALL_RESUME_COOLDOWN_HOURS", _DEFAULT_RESUME_COOLDOWN_HOURS) * 3600
    )


def _attempts_ttl_seconds() -> int:
    """TTL on the attempts key, floored at the escalation TTL.

    The floor enforces the module invariant "attempts TTL >= escalation TTL":
    an attempts key that expires first re-arms the budget while the escalation
    key still suppresses the page, which turns "escalate once and stop" into
    "act forever, silently".
    """
    configured = int(_env_float("SDLC_STALL_ATTEMPTS_TTL_DAYS", _DEFAULT_ATTEMPTS_TTL_DAYS) * 86400)
    return max(configured, _escalation_ttl_seconds())


def _escalation_ttl_seconds() -> int:
    return int(_env_float("SDLC_STALL_ESCALATION_TTL_DAYS", _DEFAULT_ESCALATION_TTL_DAYS) * 86400)


def _max_attempts() -> int:
    return int(_env_float("SDLC_STALL_RESUME_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS))


def _create_max_per_tick() -> int:
    return int(_env_float("SDLC_STALL_CREATE_MAX_PER_TICK", _DEFAULT_CREATE_MAX_PER_TICK))


def _resume_enabled() -> bool:
    raw = os.environ.get("SDLC_STALL_RESUME_ENABLED")
    if raw is None:
        return _DEFAULT_RESUME_ENABLED
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _run_gh(
    args: list[str], *, cwd: str, timeout: int | None = None
) -> subprocess.CompletedProcess | None:
    """Run a gh CLI command. Returns CompletedProcess on success, None on failure."""
    if timeout is None:
        timeout = int(settings.timeouts.git_subprocess_s)
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: gh CLI not on PATH; skipping")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: gh %s timed out", " ".join(args[:2]))
        return None
    except Exception as exc:
        logger.warning("sdlc_progress: gh %s failed: %s", " ".join(args[:2]), exc)
        return None


def _list_open_sdlc_prs(cwd: str) -> list[dict[str, Any]]:
    """Return open non-draft PRs whose head ref matches session/sdlc-<N>."""
    proc = _run_gh(
        ["pr", "list", "--state", "open", "--json", "number,headRefName,isDraft,baseRefName"],
        cwd=cwd,
    )
    if proc is None or proc.returncode != 0:
        return []
    try:
        prs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        logger.warning("sdlc_progress: gh pr list returned non-JSON")
        return []
    return [
        pr
        for pr in prs
        if isinstance(pr, dict)
        and not pr.get("isDraft")
        and _SDLC_BRANCH_RE.match(pr.get("headRefName") or "")
    ]


def _issue_is_open(cwd: str, number: int) -> bool | None:
    """True if issue is open, False if closed, None if lookup failed/unknown."""
    proc = _run_gh(["issue", "view", str(number), "--json", "state"], cwd=cwd)
    if proc is None or proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    state = (data.get("state") or "").upper()
    if state == "OPEN":
        return True
    if state == "CLOSED":
        return False
    return None


def _slug_from_branch(branch: str) -> str | None:
    """Return 'sdlc-<N>' for 'session/sdlc-<N>', else None."""
    if not branch.startswith("session/"):
        return None
    return branch[len("session/") :]


def _issue_number_from_slug(slug: str) -> int | None:
    """Extract numeric issue id from 'sdlc-<N>'."""
    m = re.match(r"sdlc-(\d+)$", slug)
    return int(m.group(1)) if m else None


def _last_commit(cwd: str, branch: str) -> tuple[str, int] | None:
    """Return (sha, unix_ts) of the last commit on ``origin/<branch>``.

    Returns None if the local ref isn't present — caller silently skips.
    Why ``origin/<branch>``? The worker may not have the local branch checked
    out (worktrees expose it only inside the worktree). The remote ref is the
    canonical view from the orchestrator's perspective.
    """
    ref = f"origin/{branch}"
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%H %ct", ref],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: git not on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: git log timed out for %s", ref)
        return None
    except Exception as exc:
        logger.warning("sdlc_progress: git log raised for %s: %s", ref, exc)
        return None

    if proc.returncode != 0:
        # Branch not present locally — silent skip per success criteria.
        logger.debug("sdlc_progress: %s not present locally (rc=%d)", ref, proc.returncode)
        return None
    parts = (proc.stdout or "").strip().split()
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


# --- Gate 5': liveness from the lock, not from session rows -----------------
#
# _lock_says_live is imported from reflections.utilities above (#2717) —
# not redefined here. See that module for the implementation and rationale.


def _lane_is_live(issue_number: int) -> bool | None:
    """True = a live lane owns this issue, False = free, None = unknown.

    A deliberately **one-signal** gate: the issue lock is the sole liveness
    authority. An earlier design OR-ed in a secondary ``AgentSession`` row
    check keyed on ``issue_number``; that branch was structurally dead, because
    no non-ledger creation path populates ``AgentSession.issue_number`` (the
    same gap documented at ``tools/merge_predicate.py``), and every row that
    does carry one is a ledger anchor the check skips. Inferring liveness from
    a row's existence is also the mirage the lock was adopted to kill. Do not
    reintroduce it without first plumbing ``issue_number`` through the
    creation path.
    """
    return _lock_says_live(issue_number)


# --- Redis bookkeeping ------------------------------------------------------


def _action_cooldown_set(slug: str, sha: str) -> bool:
    """Claim the action window for this ``(slug, sha)``. True = we may act.

    ``SET NX EX`` — atomic, so two overlapping ticks cannot both act. Redis
    unavailable → False (decline), preserving the "under-act while blind"
    posture.
    """
    key = _COOLDOWN_KEY.format(slug=slug, sha=sha)
    try:
        return bool(_get_redis().set(key, "1", nx=True, ex=_action_cooldown_seconds()))
    except Exception as exc:
        logger.warning("sdlc_progress: cooldown set failed for %s: %s", key, exc)
        return False


def _action_cooldown_release(slug: str, sha: str) -> None:
    """Hand the action window back after a tick that dispatched nothing.

    The claim in ``_action_cooldown_set`` happens before rung selection so it
    can double as the overlapping-tick guard. Paths that then bail without
    acting (target query unknown, create brake, a declined rung) would
    otherwise burn a full cooldown on a no-op tick — the plan says a braked
    lane waits for the *next tick*, not the next hour. Releasing is safe
    precisely because the releasing tick did nothing: there is no action for a
    concurrent tick to duplicate.
    """
    key = _COOLDOWN_KEY.format(slug=slug, sha=sha)
    try:
        _get_redis().delete(key)
    except Exception as exc:
        logger.warning("sdlc_progress: cooldown release failed for %s: %s", key, exc)


def _attempts_count(slug: str, sha: str) -> int | None:
    """Read the attempt counter WITHOUT charging it. None = unknown.

    This is a bare ``GET`` (absent key = 0), consulted only by the pre-action
    budget gate. It must never be an ``INCR``: a lane sitting in the action
    cooldown would then charge an attempt every tick and escalate to a human
    without the ladder ever dispatching a real action.

    A read failure OR a non-integer payload both read as *unknown*, matching
    every other unknown in this module. Treating a corrupt payload as 0 would
    say "under budget, act" on the strength of data we cannot parse.
    """
    key = _ATTEMPTS_KEY.format(slug=slug, sha=sha)
    try:
        raw = _get_redis().get(key)
    except Exception as exc:
        logger.warning("sdlc_progress: attempts read failed for %s: %s", key, exc)
        return None
    if raw is None:
        return 0
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("sdlc_progress: attempts payload not an integer for %s: %r", key, raw)
        return None


def _bump_attempts(slug: str, sha: str) -> bool:
    """Charge one attempt (``INCR`` + TTL refresh). False = the write failed.

    Invoked from exactly one place: the post-classification charge step in
    ``_attempt_action``. A failure here cannot un-dispatch the action that was
    already sent, so the caller logs a marker and continues — it must never
    itself trigger an escalation.
    """
    key = _ATTEMPTS_KEY.format(slug=slug, sha=sha)
    try:
        r = _get_redis()
        r.incr(key)
        r.expire(key, _attempts_ttl_seconds())
        return True
    except Exception as exc:
        logger.warning("sdlc_progress: attempts bump failed for %s: %s", key, exc)
        return False


def _escalation_exists(slug: str, sha: str) -> bool | None:
    """Has a human already been paged about this ``(slug, sha)``? None = unknown.

    Read-only companion to ``_escalation_set``, consulted before the lane
    claims an action window. "Escalate once and stop acting" is only literal if
    a later tick can *see* the escalation: without this read the ladder would
    keep dispatching actions while ``_escalation_set``'s ``SET NX`` silently
    swallowed every further page.

    Unknown declines to act, consistent with every other unknown here.
    """
    key = _ESCALATED_KEY.format(slug=slug, sha=sha)
    try:
        return _get_redis().get(key) is not None
    except Exception as exc:
        logger.warning("sdlc_progress: escalation read failed for %s: %s", key, exc)
        return None


def _escalation_set(slug: str, sha: str) -> bool:
    """``SET NX`` the escalation key. True = this caller may page the human.

    False means either the human was already told about this head sha, or Redis
    is unavailable — in both cases nothing is sent.
    """
    key = _ESCALATED_KEY.format(slug=slug, sha=sha)
    try:
        return bool(_get_redis().set(key, "1", nx=True, ex=_escalation_ttl_seconds()))
    except Exception as exc:
        logger.warning("sdlc_progress: escalation set failed for %s: %s", key, exc)
        return False


# --- Messages ---------------------------------------------------------------


def _steer_message(*, slug: str, pr_number: Any, issue_number: int, age_hours: int) -> str:
    """The instruction sent to an eng session (and used as a created session's goal).

    Contains ``issue #{N}`` by design: it is what makes the message actionable
    and what ``_derive_sdlc_metadata`` keys on when the create rung mints a
    session. Asks for ONE ``/sdlc`` stage per CLAUDE.md principle 9.
    """
    return (
        f"SDLC lane {slug} is stalled: PR #{pr_number} on issue #{issue_number}, "
        f"last commit {age_hours}h ago, no live run holds the issue lock. "
        f"Resume the pipeline: invoke /sdlc for issue #{issue_number}. "
        "Route one stage, then return."
    )


def _escalation_message(
    *,
    project: str,
    slug: str,
    pr_number: Any,
    issue_number: int,
    age_hours: int,
    attempts: int,
    reason: str,
) -> str:
    """Attempt-and-failure voice: the system tried and could not."""
    return (
        f"[{project}] SDLC lane {slug} (PR #{pr_number}, issue #{issue_number}) stalled "
        f"{age_hours}h and auto-resume failed after {attempts} attempt(s): {reason}. "
        "Needs a human."
    )


def _escalate_once(
    *,
    project: str,
    slug: str,
    sha: str,
    pr_number: Any,
    issue_number: int,
    age_hours: int,
    attempts: int,
    reason: str,
) -> str | None:
    """Page a human at most once per ``(slug, head-sha)``.

    Returns the message that was sent, or None when nothing was sent — either
    because the human was already told about this head sha, or because Redis
    was unavailable for the ``SET NX`` guard (under-alert during a flap beats
    spam during one).
    """
    if not _escalation_set(slug, sha):
        logger.info("sdlc_progress: escalation already recorded for %s@%s", slug, sha[:8])
        return None
    message = _escalation_message(
        project=project,
        slug=slug,
        pr_number=pr_number,
        issue_number=issue_number,
        age_hours=age_hours,
        attempts=attempts,
        reason=reason,
    )
    _send_alert(message)
    return message


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert. All failures swallowed and logged.

    Caller contract: fires ONLY from the escalation path, and only after
    ``_escalation_set`` returned True.
    """
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: valor-telegram not on PATH; skipping alert")
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: valor-telegram timed out")
    except Exception as exc:
        logger.warning("sdlc_progress: valor-telegram failed: %s", exc)


# --- The steer-target ladder ------------------------------------------------


def _pick_steer_target(project_key: str, lane_slug: str | None = None) -> tuple[str, Any]:
    """Return ``(kind, session)`` with kind in steer | resume | create | unknown.

    Rung 1 prefers a live (non-terminal, non-ledger) eng session for the
    project. Rung 2 falls back to a resumable eng session that carries a
    ``claude_session_uuid`` (required by ``resume_session``). Otherwise the
    caller creates one.

    Within BOTH buckets, a session whose ``slug`` matches ``lane_slug`` wins
    over a merely-more-recent one. This is not cosmetic: ``resume_session``
    transitions the row in place and the worker runs it in that row's own
    ``working_dir``, so resuming a session belonging to another lane makes work
    for issue A land as commits on lane B's branch. Only when no
    same-lane candidate exists does most-recently-updated decide.

    ``("unknown", None)`` means the session query itself failed — the caller
    declines to act rather than guessing.
    """
    try:
        from agent.session_health import _is_ledger
        from bridge.utc import to_unix_ts
        from models.agent_session import AgentSession
        from models.session_lifecycle import NON_TERMINAL_STATUSES, RESUMABLE_STATUSES

        rows = list(AgentSession.query.filter(project_key=project_key, session_type="eng"))
    except Exception as exc:
        logger.warning("sdlc_progress: target query failed for %s: %s", project_key, exc)
        return ("unknown", None)

    def _rank(row) -> tuple[int, float]:
        """Same-lane first, then most recently updated."""
        same_lane = 1 if (lane_slug and getattr(row, "slug", None) == lane_slug) else 0
        return (same_lane, to_unix_ts(getattr(row, "updated_at", None)) or 0.0)

    live: list[Any] = []
    resumable: list[Any] = []
    for row in rows:
        try:
            if _is_ledger(row):
                continue
            status = getattr(row, "status", None)
            if status in NON_TERMINAL_STATUSES:
                live.append(row)
            elif status in RESUMABLE_STATUSES and getattr(row, "claude_session_uuid", None):
                resumable.append(row)
        except Exception as exc:  # pragma: no cover — defensive per-row guard
            logger.debug("sdlc_progress: target selection skipped a row: %r", exc)

    if live:
        return ("steer", max(live, key=_rank))
    if resumable:
        return ("resume", max(resumable, key=_rank))
    return ("create", None)


@dataclass
class ActionOutcome:
    """Result of one ladder action.

    ``kind`` is carried through so telemetry can tell the rungs apart —
    the create rung is the one to watch.
    """

    kind: str
    success: bool = False
    benign: bool = False
    dispatched: bool = False
    charged: bool = False
    charge_failed: bool = False
    declined: bool = False
    error: str | None = None


def _row_is_nonterminal(session_id: str) -> bool:
    """Re-read a row and report whether it is (still) non-terminal.

    Mirrors ``steer_session``'s re-read-before-reject pattern. Used only for
    benign-race classification on the resume rung; a failed re-read is not
    evidence of another actor, so it reports False.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES

        fresh = AgentSession.query.filter(session_id=session_id).first()
        if fresh is None:
            return False
        return getattr(fresh, "status", None) not in TERMINAL_STATUSES
    except Exception as exc:
        logger.warning("sdlc_progress: benign-race re-read failed for %s: %s", session_id, exc)
        return False


def _attempt_action(
    kind: str,
    session: Any,
    *,
    slug: str,
    sha: str,
    issue_number: int,
    project_key: str,
    message: str,
) -> ActionOutcome:
    """Dispatch one rung, classify the outcome, and charge the attempt.

    An attempt is charged on success AND on failure — a steer that lands but
    does not move the pipeline is precisely the steer-storm the budget bounds —
    but NEVER on a benign race, where another actor has already done the work.
    """
    outcome = ActionOutcome(kind=kind)

    if kind == "steer":
        session_id = getattr(session, "session_id", None) or ""
        try:
            from agent.session_executor import steer_session

            result = steer_session(session_id, message)
            outcome.dispatched = True
            outcome.success = bool(result.get("success"))
            outcome.error = result.get("error")
        except Exception as exc:
            logger.warning("sdlc_progress: steer_session raised for %s: %s", session_id, exc)
            outcome.dispatched = True
            outcome.error = f"steer raised: {exc}"
        # No benign-race branch, deliberately: a live non-ledger target simply
        # succeeds, so every steer failure means the target is gone, terminal,
        # never steerable, or the push errored — all real dead ends.

    elif kind == "resume":
        session_id = getattr(session, "session_id", None) or ""
        try:
            from tools.valor_session import resume_session

            result = resume_session(session, message, source="sdlc-stall")
            outcome.dispatched = True
            outcome.success = bool(getattr(result, "success", False))
            outcome.error = getattr(result, "error", None)
        except Exception as exc:
            logger.warning("sdlc_progress: resume_session raised for %s: %s", session_id, exc)
            outcome.dispatched = True
            outcome.error = f"resume raised: {exc}"
        if not outcome.success and _row_is_nonterminal(session_id):
            # Another actor (typically crash_recovery) resumed it first.
            outcome.benign = True
            return outcome

    elif kind == "create":
        # Gate 5' ran earlier in the tick; the lock can be re-acquired in
        # between, and a duplicate lane on top of a live one is the worst
        # outcome in this change. Re-read immediately before creating.
        lock_live = _lock_says_live(issue_number)
        if lock_live is None:
            outcome.declined = True
            outcome.error = "gate-unknown: create-lock-reread"
            return outcome
        if lock_live:
            outcome.benign = True
            outcome.error = "lane re-took its issue lock"
            return outcome
        try:
            from tools.valor_session import create_session

            result = create_session(
                message=message,
                role="eng",
                slug=f"sdlc-{issue_number}",
                project_key=project_key,
                session_type="eng",
            )
            outcome.dispatched = True
            outcome.success = bool(getattr(result, "success", False))
            outcome.error = getattr(result, "error", None)
        except Exception as exc:
            logger.warning("sdlc_progress: create_session raised for %s: %s", slug, exc)
            outcome.dispatched = True
            outcome.error = f"create raised: {exc}"
        if not outcome.success and _lock_says_live(issue_number) is True:
            # The lane restarted itself between the guard and the refusal.
            outcome.benign = True
            return outcome

    else:  # pragma: no cover — unreachable via _pick_steer_target
        outcome.declined = True
        outcome.error = f"unknown rung: {kind}"
        return outcome

    if _bump_attempts(slug, sha):
        outcome.charged = True
    else:
        outcome.charge_failed = True
    return outcome


# --- Per-project body -------------------------------------------------------


def _check_project_stalls(project: dict) -> dict:
    """Per-project body called by ``run_per_project_audit``.

    Returns ``{status, findings, summary, duration}``.
    """
    t0 = time.time()
    wd = project.get("working_directory", "")
    project_key = project.get("slug", "?")
    findings: list[str] = []
    counts = {"steered": 0, "resumed": 0, "created": 0, "escalated": 0}
    # Per-call state, deliberately not in Redis: it brakes creations WITHIN one
    # project tick. It is therefore NOT atomic across overlapping ticks — two
    # ticks racing on different lanes of the same project can each mint one
    # session. Accepted at a 30-minute cadence; the per-(slug, sha) attempt
    # budget bounds the total regardless. See the plan's Risks table.
    creates_this_tick = 0

    if not wd or not Path(wd).is_dir():
        return {
            "status": "skipped",
            "findings": [],
            "summary": "sdlc-progress-check: no working_directory",
            "duration": time.time() - t0,
        }

    # Gate 6': single-machine ownership. Two machines sharing a checkout must
    # never both act on the same lane. An exception inside the helper is
    # already swallowed there and reported as not-owner.
    try:
        owns = machine_owns_project(project_key)
    except Exception as exc:  # pragma: no cover — helper is fail-soft already
        logger.warning("sdlc_progress: ownership check raised for %s: %s", project_key, exc)
        owns = False
    if not owns:
        return {
            "status": "skipped",
            "findings": [],
            "summary": f"sdlc-progress-check: {project_key} not owned by this machine",
            "duration": time.time() - t0,
        }

    threshold = _threshold_seconds()
    max_attempts = _max_attempts()
    create_budget = _create_max_per_tick()
    resume_enabled = _resume_enabled()
    now = int(time.time())

    prs = _list_open_sdlc_prs(wd)
    for pr in prs:
        branch = pr.get("headRefName") or ""
        slug = _slug_from_branch(branch)
        if not slug:
            continue
        issue_num = _issue_number_from_slug(slug)
        if issue_num is None:
            continue

        issue_open = _issue_is_open(wd, issue_num)
        if issue_open is False:
            continue
        if issue_open is None:
            findings.append(f"gate-unknown: issue-state {slug}")
            continue

        commit = _last_commit(wd, branch)
        if commit is None:
            # Branch not present locally — skip silently.
            continue
        sha, ts = commit
        age = now - ts
        if age < threshold:
            continue
        age_hours = age // 3600

        # Gate 5': liveness from the lock.
        live = _lane_is_live(issue_num)
        if live is None:
            findings.append(f"gate-unknown: lock-read {slug}")
            continue
        if live:
            continue

        # "Escalate once and stop acting", enforced literally. Without this
        # read the ladder keeps dispatching actions after the page (the
        # SET NX in _escalation_set silently swallows the extra escalations),
        # so the human sees one message while the system acts forever.
        escalated = _escalation_exists(slug, sha)
        if escalated is None:
            findings.append(f"gate-unknown: escalation-read {slug}")
            continue
        if escalated:
            findings.append(f"already-escalated: {slug}")
            continue

        def _escalate(reason: str, attempts: int) -> None:
            msg = _escalate_once(
                project=project_key,
                slug=slug,
                sha=sha,
                pr_number=pr.get("number"),
                issue_number=issue_num,
                age_hours=age_hours,
                attempts=attempts,
                reason=reason,
            )
            if msg:
                counts["escalated"] += 1
                findings.append(msg)

        if not resume_enabled:
            _escalate("auto-resume disabled (SDLC_STALL_RESUME_ENABLED=false)", 0)
            continue

        attempts = _attempts_count(slug, sha)
        if attempts is None:
            # Cannot prove we are under budget -> decline, and do NOT escalate.
            findings.append(f"gate-unknown: attempts-read {slug}")
            continue
        if attempts >= max_attempts:
            _escalate("attempt budget exhausted", attempts)
            continue

        # Claims the action window; also the overlapping-tick guard.
        if not _action_cooldown_set(slug, sha):
            logger.info("sdlc_progress: action cooldown live for %s@%s", slug, sha[:8])
            continue

        kind, target = _pick_steer_target(project_key, lane_slug=slug)
        if kind == "unknown":
            findings.append(f"gate-unknown: target-query {slug}")
            _action_cooldown_release(slug, sha)
            continue
        if kind == "create" and creates_this_tick >= create_budget:
            findings.append(f"create-brake: {slug} deferred to next tick")
            _action_cooldown_release(slug, sha)
            continue

        message = _steer_message(
            slug=slug,
            pr_number=pr.get("number"),
            issue_number=issue_num,
            age_hours=age_hours,
        )
        outcome = _attempt_action(
            kind,
            target,
            slug=slug,
            sha=sha,
            issue_number=issue_num,
            project_key=project_key,
            message=message,
        )
        if outcome.dispatched and kind == "create":
            creates_this_tick += 1
        if outcome.charge_failed:
            findings.append(f"charge-failed: attempts-incr {slug}")

        if outcome.benign:
            findings.append(f"benign-race: {kind} {slug}")
            if not outcome.dispatched:
                # A benign race caught BEFORE dispatch (the create rung's
                # pre-create lock re-read) sent nothing, so the lane owes no
                # cooldown. A benign race after a dispatch (the resume rung)
                # keeps the window: a message really did land.
                _action_cooldown_release(slug, sha)
            continue
        if outcome.declined:
            # Declined means the rung never dispatched — nothing happened, so
            # the action window is returned for the next tick.
            findings.append(f"{outcome.error} {slug}")
            _action_cooldown_release(slug, sha)
            continue

        if outcome.success:
            counts[{"steer": "steered", "resume": "resumed", "create": "created"}[kind]] += 1
            findings.append(f"auto-resume {kind}: {slug} (PR #{pr.get('number')})")
            continue

        _escalate(f"{kind} failed: {outcome.error or 'unknown error'}", attempts + 1)

    return {
        "status": "ok",
        "findings": findings,
        "summary": (
            f"sdlc-progress-check: {len(prs)} SDLC PR(s) inspected, "
            f"{counts['steered']} steered, {counts['resumed']} resumed, "
            f"{counts['created']} created, {counts['escalated']} escalated"
        ),
        "duration": time.time() - t0,
    }


def run_sdlc_progress_check() -> dict:
    """Reflection entrypoint. Iterates every local project."""
    return run_per_project_audit(_check_project_stalls, name="sdlc-progress-check")
