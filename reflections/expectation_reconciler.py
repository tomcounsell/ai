"""
reflections/expectation_reconciler.py — orphaned-lane recovery from Job expectations (#2708).

The Job's open OUTBOUND expectations are the record of what spawned lanes
owe their PM. When a lane dies silently (the #2494 founding incident: a
SIGKILLed dev subagent, exit 0, worktree deleted, nothing detected it),
the expectation outlives the lane — this reflection is what looks.

Per open outbound expectation older than ``EXPECTATION_MIN_AGE_HOURS``:

    1  re-fetch the Job by KeyFields and re-check the expectation is still
       open (Race 3: a PM discharge between scan and act wins).
    2  owner liveness: resolve the recorded ``owner`` (lane session id or
       slug) against AgentSession rows. A row claiming a non-terminal
       status is **respawn-blocking only** — a session row is a claim, not
       proof of life (#2705), and liveness judgment for stale-``running``
       rows belongs to #2716. No row, or only terminal rows ⇒ gone.
    3  shipped-work guard (the sole cross-actor collision guard — Race 2):
       re-read git/GitHub for ``session/<slug>`` — an open/merged PR or a
       pushed branch means the work is visible outside the session model.
       Shipped work is NEVER respawned; the evidence goes to a PM for
       deliberate discharge (``tools/job_tool expectation-remove``).
    4  the ladder, keyed ``(job_id, expectation_id)``:
       escalation key already set     -> stop acting entirely
       attempts >= max                -> escalate once, stop
       action cooldown live           -> skip this tick
       live PM session                -> steer it with the evidence
       no live PM, work unshipped     -> respawn the lane with the recorded
                                         ``what`` (create_session, lane slug)
       action failed                  -> escalate once, stop

Nothing here ever discharges an expectation, takes a lock, or writes
anything outside its own raw-Redis bookkeeping keys — expectations are
readable ownership records; a second PM reads and decides (#2704).

The attempts TTL is floored at the escalation TTL
(``max(configured, escalation_ttl)`` exactly as
``reflections.sdlc_progress._attempts_ttl_seconds``): an attempts key that
expires while the escalation key still suppresses paging turns "escalate
once and stop" into "act forever, silently".

Every external boundary (ORM scan, per-expectation work, git/gh
subprocess, steer/create rungs) logs a warning and continues; the
reflection never raises.

Configuration (all optional, all provisional and tunable):
    EXPECTATION_RECONCILER_ENABLED         default true
    EXPECTATION_MIN_AGE_HOURS              default 1    minimum expectation age
    EXPECTATION_RECONCILE_MAX_ATTEMPTS     default 3    attempts per (job, expectation)
    EXPECTATION_RECONCILE_COOLDOWN_HOURS   default 1    action cooldown
    EXPECTATION_ATTEMPTS_TTL_DAYS          default 30   floored at escalation TTL
    EXPECTATION_ESCALATION_TTL_DAYS        default 30
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Any

from config.settings import settings
from reflections.utilities import _get_redis, machine_owns_project, run_per_project_audit

logger = logging.getLogger("reflections.expectation_reconciler")

# Raw Redis namespace — NOT Popoto-managed. Pure bookkeeping per CLAUDE.md's
# raw-Redis exception (precedent: reflections/sdlc_progress.py).
_COOLDOWN_KEY = "expectation:reconcile:cooldown:{job}:{eid}"
_ATTEMPTS_KEY = "expectation:reconcile:attempts:{job}:{eid}"
_ESCALATED_KEY = "expectation:reconcile:escalated:{job}:{eid}"

# GRAIN OF SALT: provisional/tunable via the paired env vars — chosen to
# mirror sdlc_progress, not from a measured distribution.
_DEFAULT_MIN_AGE_HOURS = 1
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_COOLDOWN_HOURS = 1
# INVARIANT: attempts TTL >= escalation TTL (see module docstring).
_DEFAULT_ATTEMPTS_TTL_DAYS = 30
_DEFAULT_ESCALATION_TTL_DAYS = 30


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _enabled() -> bool:
    raw = os.environ.get("EXPECTATION_RECONCILER_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _min_age_seconds() -> int:
    return int(_env_float("EXPECTATION_MIN_AGE_HOURS", _DEFAULT_MIN_AGE_HOURS) * 3600)


def _max_attempts() -> int:
    return int(_env_float("EXPECTATION_RECONCILE_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS))


def _cooldown_seconds() -> int:
    return int(_env_float("EXPECTATION_RECONCILE_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS) * 3600)


def _escalation_ttl_seconds() -> int:
    return int(_env_float("EXPECTATION_ESCALATION_TTL_DAYS", _DEFAULT_ESCALATION_TTL_DAYS) * 86400)


def _attempts_ttl_seconds() -> int:
    """Attempts TTL, floored at the escalation TTL (the load-bearing max())."""
    configured = int(
        _env_float("EXPECTATION_ATTEMPTS_TTL_DAYS", _DEFAULT_ATTEMPTS_TTL_DAYS) * 86400
    )
    return max(configured, _escalation_ttl_seconds())


# --- Redis bookkeeping (per (job_id, expectation_id)) -----------------------


def _cooldown_claim(job_id: str, eid: str) -> bool:
    key = _COOLDOWN_KEY.format(job=job_id, eid=eid)
    try:
        return bool(_get_redis().set(key, "1", nx=True, ex=_cooldown_seconds()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: cooldown set failed for %s: %s", key, exc)
        return False


def _attempts_count(job_id: str, eid: str) -> int | None:
    key = _ATTEMPTS_KEY.format(job=job_id, eid=eid)
    try:
        raw = _get_redis().get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: attempts read failed for %s: %s", key, exc)
        return None
    if raw is None:
        return 0
    try:
        return int(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return None


def _bump_attempts(job_id: str, eid: str) -> None:
    key = _ATTEMPTS_KEY.format(job=job_id, eid=eid)
    try:
        r = _get_redis()
        r.incr(key)
        r.expire(key, _attempts_ttl_seconds())
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: attempts bump failed for %s: %s", key, exc)


def _escalation_exists(job_id: str, eid: str) -> bool | None:
    key = _ESCALATED_KEY.format(job=job_id, eid=eid)
    try:
        return _get_redis().get(key) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: escalation read failed for %s: %s", key, exc)
        return None


def _escalate_once(job_id: str, eid: str, message: str) -> bool:
    """Page the operator at most once per (job, expectation). True = sent."""
    key = _ESCALATED_KEY.format(job=job_id, eid=eid)
    try:
        if not _get_redis().set(key, "1", nx=True, ex=_escalation_ttl_seconds()):
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: escalation set failed for %s: %s", key, exc)
        return False
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: alert send failed: %s", exc)
    return True


# --- Owner liveness (a session row is a claim, not proof — #2705) -----------


def _owner_rows(owner: str) -> list[Any]:
    """AgentSession rows matching the recorded owner (session id or slug)."""
    from models.agent_session import AgentSession

    rows: list[Any] = []
    slug = owner[len("session/") :] if owner.startswith("session/") else owner
    for kwargs in (
        {"session_id": owner},
        {"agent_session_id": owner},
        {"slug": slug},
    ):
        try:
            rows.extend(AgentSession.query.filter(**kwargs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("expectation_reconciler: owner query %s failed: %s", kwargs, exc)
    return rows


def _owner_is_gone(owner: str) -> bool | None:
    """True = no live claim for the owner, False = a row claims life, None = unknown.

    A non-terminal row is respawn-blocking only — never discharge evidence.
    The stale-``running`` tie-breaker stays with #2716; this keys on
    *absence*, not freshness arithmetic.
    """
    try:
        from models.session_lifecycle import TERMINAL_STATUSES

        rows = _owner_rows(owner)
        if not rows:
            return True
        return all(getattr(r, "status", None) in TERMINAL_STATUSES for r in rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: owner liveness failed for %s: %s", owner, exc)
        return None


# --- Shipped-work guard (Race 2's sole collision guard) ---------------------


def _lane_slug(owner: str) -> str | None:
    """Best-effort branch slug for the recorded owner."""
    slug = owner[len("session/") :] if owner.startswith("session/") else owner
    # Session ids look like {chat}_{millis}; slugs are word-ish.
    if not slug or slug.replace("_", "").isdigit():
        rows = _owner_rows(owner)
        for row in rows:
            row_slug = getattr(row, "slug", None)
            if row_slug:
                return str(row_slug)
        return None
    return slug


def _shipped_evidence(wd: str, slug: str | None) -> str | None:
    """Fresh git/GitHub read: visible work for ``session/<slug>``, or None.

    Re-read immediately before any respawn decision (Race 2): when another
    actor's work is visible — an open/merged PR or a pushed branch — the
    reconciler declines to respawn and routes the evidence to a PM instead.
    """
    if not slug:
        return None
    branch = f"session/{slug}"
    timeout = int(settings.timeouts.git_subprocess_s)
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,state"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=wd,
        )
        if proc.returncode == 0:
            prs = json.loads(proc.stdout or "[]")
            if prs:
                pr = prs[0]
                return f"PR #{pr.get('number')} ({pr.get('state', '?').lower()}) on {branch}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: gh pr list failed for %s: %s", branch, exc)
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=wd,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            sha = proc.stdout.split()[0][:8]
            return f"pushed branch {branch} @ {sha}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: git ls-remote failed for %s: %s", branch, exc)
    return None


# --- PM target + actions ----------------------------------------------------


def _live_pm_session(project_key: str, holder: str):
    """A live eng session to steer: the recorded holder first, else the most
    recently updated live non-ledger eng session in the project."""
    try:
        from agent.session_health import _is_ledger
        from models.agent_session import AgentSession
        from models.session_lifecycle import NON_TERMINAL_STATUSES
        from utils.utc import to_unix_ts

        rows = list(AgentSession.query.filter(project_key=project_key, session_type="eng"))
        live = [
            r
            for r in rows
            if getattr(r, "status", None) in NON_TERMINAL_STATUSES and not _is_ledger(r)
        ]
        if not live:
            return None
        for row in live:
            if holder in (getattr(row, "session_id", None), getattr(row, "agent_session_id", None)):
                return row
        return max(live, key=lambda r: to_unix_ts(getattr(r, "updated_at", None)) or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: PM target query failed: %s", exc)
        return None


def _steer(session, message: str) -> bool:
    try:
        from agent.session_executor import steer_session

        result = steer_session(getattr(session, "session_id", "") or "", message)
        return bool(result.get("success"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: steer failed: %s", exc)
        return False


def _respawn_lane(project_key: str, slug: str, what: str, job_id: str) -> bool:
    """Recreate a dead lane from its recorded ``what`` (unshipped work only)."""
    try:
        from tools.valor_session import create_session

        result = create_session(
            message=(
                f"Recovered orphaned lane {slug}: its session died with no visible "
                f"work. Deliver the recorded expectation: {what}"
            ),
            role="eng",
            slug=slug,
            project_key=project_key,
            session_type="eng",
            job_id=job_id,
            expect_what=what,
        )
        return bool(getattr(result, "success", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: respawn failed for %s: %s", slug, exc)
        return False


def _entry_age_seconds(entry: dict, now: float) -> float | None:
    try:
        ts = entry.get("ts")
        if not ts:
            return None
        return now - datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return None


# --- Per-project body -------------------------------------------------------


def _reconcile_project(project: dict) -> dict:
    t0 = time.time()
    wd = project.get("working_directory", "")
    project_key = project.get("slug", "?")
    findings: list[str] = []
    counts = {"steered": 0, "respawned": 0, "escalated": 0}

    if not _enabled():
        return {
            "status": "skipped",
            "findings": [],
            "summary": "expectation-reconciler: disabled",
            "duration": time.time() - t0,
        }
    try:
        owns = machine_owns_project(project_key)
    except Exception:  # noqa: BLE001
        owns = False
    if not owns or not wd:
        return {
            "status": "skipped",
            "findings": [],
            "summary": f"expectation-reconciler: {project_key} not owned / no working dir",
            "duration": time.time() - t0,
        }

    try:
        from models.job import Job

        jobs = [j for j in Job.with_open_expectations() if j.room_id.startswith(f"{project_key}|")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: job scan failed for %s: %s", project_key, exc)
        return {
            "status": "error",
            "findings": [f"scan-failed: {exc}"],
            "summary": "expectation-reconciler: job scan failed",
            "duration": time.time() - t0,
        }

    now = time.time()
    min_age = _min_age_seconds()
    for job in jobs:
        for entry in job.open_expectations(direction="outbound"):
            try:
                eid = str(entry.get("id") or "")
                owner = str(entry.get("owner") or "")
                if not eid or not owner:
                    continue
                age = _entry_age_seconds(entry, now)
                if age is None or age < min_age:
                    continue

                gone = _owner_is_gone(owner)
                if gone is None:
                    findings.append(f"gate-unknown: owner-liveness {owner}")
                    continue
                if not gone:
                    continue  # respawn-blocking row present; #2716 owns the tie-breaker

                if _escalation_exists(job.job_id, eid) is not False:
                    continue
                attempts = _attempts_count(job.job_id, eid)
                if attempts is None:
                    findings.append(f"gate-unknown: attempts-read {eid}")
                    continue
                what = str(entry.get("what") or "")
                slug = _lane_slug(owner)
                if attempts >= _max_attempts():
                    if _escalate_once(
                        job.job_id,
                        eid,
                        f"[{project_key}] Orphaned expectation on Job {job.job_id}: "
                        f"lane {owner} is gone, {attempts} recovery attempt(s) spent. "
                        f"Expected: {what!r}. Needs a human.",
                    ):
                        counts["escalated"] += 1
                        findings.append(f"escalated: {eid}")
                    continue
                if not _cooldown_claim(job.job_id, eid):
                    continue

                # Race 3: act only on a fresh, still-open snapshot.
                fresh = Job.query.get(id=job.id, room_id=job.room_id)
                if fresh is None or not any(
                    e.get("id") == eid for e in fresh.open_expectations(direction="outbound")
                ):
                    continue  # discharged (or gone) since the scan — the PM won

                evidence = _shipped_evidence(wd, slug)
                pm = _live_pm_session(project_key, str(entry.get("holder") or ""))

                if evidence is not None:
                    # Shipped work is never respawned: evidence goes to a PM
                    # for deliberate discharge.
                    message = (
                        f"Lane {owner} (Job {job.job_id}) is gone but its work is "
                        f"visible: {evidence}. Verify delivery of {what!r} and "
                        f"discharge the expectation: python -m tools.job_tool "
                        f"expectation-remove --job-id {job.job_id} --expectation-id {eid}"
                    )
                    if pm is not None and _steer(pm, message):
                        counts["steered"] += 1
                        findings.append(f"steered-evidence: {eid} ({evidence})")
                        _bump_attempts(job.job_id, eid)
                    elif _escalate_once(job.job_id, eid, f"[{project_key}] {message}"):
                        counts["escalated"] += 1
                        findings.append(f"escalated-evidence: {eid}")
                    continue

                # Unshipped, owner gone: steer a live PM to re-own; else
                # respawn the lane from the recorded `what`.
                if pm is not None:
                    message = (
                        f"Lane {owner} (Job {job.job_id}) died with no visible work. "
                        f"It owed: {what!r}. Re-own it: respawn the lane "
                        f"(valor-session create --job-id {job.job_id} "
                        f'--expect-what "{what}") or discharge the expectation '
                        f"(expectation-remove --expectation-id {eid}) if it is moot."
                    )
                    if _steer(pm, message):
                        counts["steered"] += 1
                        findings.append(f"steered-orphan: {eid}")
                        _bump_attempts(job.job_id, eid)
                        continue
                if slug and _respawn_lane(project_key, slug, what, job.job_id):
                    counts["respawned"] += 1
                    findings.append(f"respawned: {slug} ({eid})")
                    _bump_attempts(job.job_id, eid)
                    continue
                _bump_attempts(job.job_id, eid)
                if _escalate_once(
                    job.job_id,
                    eid,
                    f"[{project_key}] Orphaned expectation on Job {job.job_id}: lane "
                    f"{owner} gone, no live PM to steer and no respawnable slug. "
                    f"Expected: {what!r}. Needs a human.",
                ):
                    counts["escalated"] += 1
                    findings.append(f"escalated: {eid}")
            except Exception as exc:  # noqa: BLE001 — one expectation never stops the pass
                logger.warning(
                    "expectation_reconciler: per-expectation pass failed on job %s: %s",
                    getattr(job, "job_id", "?"),
                    exc,
                )

    return {
        "status": "ok",
        "findings": findings,
        "summary": (
            f"expectation-reconciler: {len(jobs)} job(s) with open expectations, "
            f"{counts['steered']} steered, {counts['respawned']} respawned, "
            f"{counts['escalated']} escalated"
        ),
        "duration": time.time() - t0,
    }


def run_expectation_reconciliation() -> dict:
    """Reflection entrypoint. Iterates every local project."""
    return run_per_project_audit(_reconcile_project, name="expectation-reconciler")
