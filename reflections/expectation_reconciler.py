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
       Shipped work is NEVER respawned; typed evidence (merged / open /
       closed-unmerged / branch-only, plus the issues the PR closes) goes to
       an agent for deliberate discharge (``tools/job_tool expectation-remove``).
    4  the ladder, keyed ``(job_id, expectation_id)``:
       handed-off sentinel already set -> stop acting entirely
       attempts >= max                 -> hand off once, stop
       action cooldown live            -> skip this tick
       live holder session             -> steer it via ``agent_handoff.hand_off``
       no holder, work unshipped       -> respawn the lane with the recorded
                                          ``what`` (create_session, lane slug)
       no slug / respawn failed        -> hand off once, stop

Nothing here writes to a human chat. Every finding goes through
``reflections.agent_handoff.hand_off``: an agent in the Job's Room judges it and
speaks to a human, in persona, only when a decision is needed. A handoff that
cannot reach an agent is an operator-surface finding and leaves the sentinel
unset, so a later tick retries under the cooldown.

Nothing here ever discharges an expectation, takes a lock, or writes
anything outside its own raw-Redis bookkeeping keys and the handoff — expectations are
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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config.settings import settings
from reflections import agent_handoff
from reflections.agent_handoff import Finding, HandoffResult, hand_off
from reflections.utilities import (
    _get_redis,
    machine_owns_project,
    run_per_project_audit,
)

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


def _annotate_attempts_exhausted(job_id: str, room_id: str, job_row_id: str, eid: str) -> None:
    """Re-fetch the Job and write the reconciler's own blocked annotation.

    Shared by Site A (crash-window repair) and Site B (fresh escalation) —
    both need an up-to-date snapshot before writing, and both write the same
    ``attempts_exhausted``/``reconciler`` pair. Fail-soft: an annotation
    failure must never interrupt the pass over other expectations (the
    caller's own ``try`` already covers this, but the write itself is best
    effort by construction — a missed annotation just means the row surfaces
    unannotated next tick, not a lost escalation).
    """
    from models.job import Job

    try:
        fresh = Job.query.get(id=job_row_id, room_id=room_id)
        if fresh is None:
            return
        fresh.block_expectation(eid, code="attempts_exhausted", by="reconciler")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "expectation_reconciler: attempts-exhausted annotation failed for %s/%s: %s",
            job_id,
            eid,
            exc,
        )


def _mark_handed_off(job_id: str, eid: str) -> None:
    """Write the once-only sentinel: an agent has this (job, expectation) at the exhausted rung.

    Written only after a delivered handoff (``steered`` / ``created``). An
    unreachable or rate-capped handoff leaves it unset and relies on the
    cooldown key, so a transient failure retries on a later tick instead of
    being suppressed for the sentinel's whole TTL.
    """
    key = _ESCALATED_KEY.format(job=job_id, eid=eid)
    try:
        _get_redis().set(key, "1", nx=True, ex=_escalation_ttl_seconds())
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: sentinel set failed for %s: %s", key, exc)


def _handoff(
    project: dict,
    job,
    entry: dict,
    *,
    rung: str,
    facts: list[str],
    evidence: dict,
    suggested_action: str,
) -> HandoffResult:
    """Hand one reconciler finding to an agent in the Job's Room."""
    eid = str(entry.get("id") or "")
    return hand_off(
        Finding(
            source="expectation_reconciler",
            project=project,
            room_id=job.room_id,
            facts=facts,
            evidence=evidence,
            suggested_action=suggested_action,
            job_id=job.job_id,
            expectation_id=eid,
            holder=str(entry.get("holder") or "") or None,
            dedup_key=f"{job.job_id}:{eid}:{rung}",
        )
    )


# --- Owner liveness (a session row is a claim, not proof — #2705) -----------


def _owner_rows(owner: str, project_key: str | None = None) -> list[Any]:
    """AgentSession rows matching the recorded owner (session id or slug)."""
    from models.agent_session import AgentSession

    rows: list[Any] = []
    slug = owner[len("session/") :] if owner.startswith("session/") else owner
    try:
        by_id = AgentSession.get_by_id(owner)
        if by_id is not None:
            rows.append(by_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: owner get_by_id failed: %s", exc)
    queries = [{"session_id": owner}, {"slug": slug}]
    if project_key:
        # dev_agent_id is a plain Field: Popoto's client-side filter needs an
        # indexed param (project_key) alongside it. An Agent-tool subagent has
        # no row of its own; its parent PM row stands in as the liveness claim.
        queries.append({"dev_agent_id": f"agent-{owner}", "project_key": project_key})
    for kwargs in queries:
        try:
            rows.extend(AgentSession.query.filter(**kwargs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("expectation_reconciler: owner query %s failed: %s", kwargs, exc)
    return rows


def _owner_is_gone(owner: str, project_key: str | None = None) -> bool | None:
    """True = no live claim for the owner, False = a row claims life, None = unknown.

    A non-terminal row is respawn-blocking only — never discharge evidence.
    The stale-``running`` tie-breaker stays with #2716; this keys on
    *absence*, not freshness arithmetic.
    """
    try:
        from models.session_lifecycle import TERMINAL_STATUSES

        rows = _owner_rows(owner, project_key)
        if not rows:
            return True
        return all(getattr(r, "status", None) in TERMINAL_STATUSES for r in rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: owner liveness failed for %s: %s", owner, exc)
        return None


# --- Shipped-work guard (Race 2's sole collision guard) ---------------------


def _record_shipped_work_evidence(
    project_key: str, job_id: str, eid: str, owner: str, evidence: str
) -> None:
    """Persist the shipped-work signal this run just computed (#3177).

    ``_shipped_evidence`` does a fresh git/GitHub read and the reconciler acts
    on it and then discards it, so the system has never been able to answer
    "how often did a lane ship without discharging its expectation?" — the
    exact shape of the intervention burden the improvement controller measures.
    One durable row per ``(job, expectation)``, deduped so re-running the
    reconciler does not inflate the count.

    Fail-soft by construction: the reconciler's own decision must not depend on
    an evidence write succeeding.
    """
    try:
        from models.improvement_evidence import ImprovementEvidence

        ImprovementEvidence.record_once(
            project_key,
            "shipped_work",
            source_ref=f"shipped:{job_id}:{eid}",
            text=f"lane {owner} shipped without discharging: {evidence}",
            detail=evidence,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("expectation_reconciler: shipped-work evidence write failed: %s", exc)


_RESERVED_OWNERS = frozenset({"dev", "pm"})


def _lane_slug(owner: str, project_key: str | None = None) -> str | None:
    """Best-effort branch slug for the recorded owner (None for a role placeholder)."""
    slug = owner[len("session/") :] if owner.startswith("session/") else owner
    if slug.strip().lower() in _RESERVED_OWNERS:
        return None
    # Session ids look like {chat}_{millis}; slugs are word-ish.
    if not slug or slug.replace("_", "").isdigit():
        rows = _owner_rows(owner, project_key)
        for row in rows:
            row_slug = getattr(row, "slug", None)
            if row_slug:
                return str(row_slug)
        return None
    return slug


@dataclass(frozen=True)
class ShippedEvidence:
    """What git/GitHub shows for a lane's branch. ``kind`` is picked merged > open >
    closed-unmerged > branch-only."""

    kind: str  # "merged" | "open" | "closed_unmerged" | "branch_only"
    branch: str
    pr_number: int | None = None
    closes_issues: list[int] = field(default_factory=list)
    sha: str | None = None

    @property
    def counts_as_shipped(self) -> bool:
        """A PR closed without merging is a deliberate decision, not shipped work."""
        return self.kind != "closed_unmerged"

    def describe(self) -> str:
        if self.kind == "branch_only":
            return f"pushed branch {self.branch} @ {self.sha}"
        state = {
            "merged": "merged",
            "open": "open",
            "closed_unmerged": "closed without merging",
        }[self.kind]
        text = f"PR #{self.pr_number} ({state}) on {self.branch}"
        if self.closes_issues:
            text += ", closes " + ", ".join(f"#{n}" for n in self.closes_issues)
        return text


_KIND_ORDER = ("merged", "open", "closed_unmerged")


def _pr_kind(state: str) -> str:
    state = (state or "").upper()
    return {"MERGED": "merged", "OPEN": "open"}.get(state, "closed_unmerged")


def _shipped_evidence(wd: str, slug: str | None) -> ShippedEvidence | None:
    """Fresh git/GitHub read: visible work for ``session/<slug>``, or None.

    Re-read immediately before any respawn decision (Race 2): when another
    actor's work is visible the reconciler declines to respawn and hands the
    typed evidence to an agent instead.
    """
    if not slug:
        return None
    branch = f"session/{slug}"
    timeout = int(settings.timeouts.git_subprocess_s)
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "number,state,closingIssuesReferences",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=wd,
        )
        if proc.returncode == 0:
            prs = json.loads(proc.stdout or "[]")
            if prs:
                best = min(prs, key=lambda pr: _KIND_ORDER.index(_pr_kind(pr.get("state", ""))))
                closes = [
                    int(ref["number"])
                    for ref in (best.get("closingIssuesReferences") or [])
                    if isinstance(ref, dict) and ref.get("number") is not None
                ]
                return ShippedEvidence(
                    kind=_pr_kind(best.get("state", "")),
                    branch=branch,
                    pr_number=best.get("number"),
                    closes_issues=closes,
                )
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
            return ShippedEvidence(
                kind="branch_only", branch=branch, sha=proc.stdout.split()[0][:8]
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("expectation_reconciler: git ls-remote failed for %s: %s", branch, exc)
    return None


# --- Respawn ----------------------------------------------------------------


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


def _note_handoff(
    result: HandoffResult, job, eid: str, findings: list[str], counts: dict, *, mark: bool
) -> None:
    """Record a handoff result on the operator surface and in the bookkeeping keys.

    Only a delivered handoff (``steered`` / ``created``) counts. ``mark=True``
    (the once-only rungs) also writes the sentinel; a steered handoff at a
    retryable rung bumps the attempts key instead.
    """
    if not result.delivered:
        counts["rate_capped" if result.kind == "rate-capped" else "unreachable"] += 1
        findings.append(result.finding_line(eid))
        return
    counts["steered" if result.kind == "steered" else "handed_off"] += 1
    findings.append(result.finding_line(eid))
    if mark:
        _mark_handed_off(job.job_id, eid)
    else:
        _bump_attempts(job.job_id, eid)


# --- Per-project body -------------------------------------------------------


def _reconcile_project(project: dict) -> dict:
    t0 = time.time()
    wd = project.get("working_directory", "")
    project_key = project.get("slug", "?")
    findings: list[str] = []
    counts = {"steered": 0, "respawned": 0, "handed_off": 0, "unreachable": 0, "rate_capped": 0}

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
        if job.goal_is_corrupt():
            # with_open_expectations() retains a flagged Job whose goal no
            # longer decodes: the flag is the last known truth and an empty
            # parse cannot disprove it. Nothing here can act on obligations
            # it cannot read, so the row surfaces as a finding for a human.
            findings.append(f"corrupt-goal: {job.job_id}")
            continue
        for entry in job.open_expectations(direction="outbound"):
            try:
                eid = str(entry.get("id") or "")
                owner = str(entry.get("owner") or "")
                if not eid or not owner:
                    continue
                if entry.get("blocked") is not None:
                    findings.append(f"blocked: {eid} {entry['blocked'].get('code')}")
                    continue
                age = _entry_age_seconds(entry, now)
                if age is None or age < min_age:
                    continue

                # Site A — crash-window repair: a prior tick escalated (the
                # recovery budget was already spent) but crashed before it
                # could write the annotation. This closes that window on the
                # very next tick, unconditionally on owner liveness — the
                # verdict was already earned, it just never got recorded.
                if (
                    entry.get("blocked") is None
                    and _escalation_exists(job.job_id, eid) is True
                    and (attempts_so_far := _attempts_count(job.job_id, eid)) is not None
                    and attempts_so_far >= _max_attempts()
                ):
                    _annotate_attempts_exhausted(job.job_id, job.room_id, job.id, eid)
                    findings.append(f"blocked: {eid} attempts_exhausted")
                    continue

                gone = _owner_is_gone(owner, project_key)
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
                slug = _lane_slug(owner, project_key)
                base_evidence = {"expected": what, "lane_owner": owner or "unrecorded"}
                if attempts >= _max_attempts():
                    result = _handoff(
                        project,
                        job,
                        entry,
                        rung="exhausted",
                        facts=[
                            f"The lane that owed {what!r} is gone and the recovery budget "
                            f"({attempts} attempt(s)) is spent."
                        ],
                        evidence={**base_evidence, "attempts": attempts},
                        suggested_action=(
                            "Decide whether the work still matters. Discharge the expectation if "
                            "it is moot, recover the lane yourself, or ask the human one plain "
                            "question if only they can decide."
                        ),
                    )
                    _note_handoff(result, job, eid, findings, counts, mark=True)
                    if result.delivered:
                        # An undelivered handoff must retry on a later tick, so
                        # only a delivered one parks the expectation.
                        _annotate_attempts_exhausted(job.job_id, job.room_id, job.id, eid)
                    continue
                if not _cooldown_claim(job.job_id, eid):
                    continue

                # Race 3: act only on a fresh, still-open snapshot.
                fresh = Job.query.get(id=job.id, room_id=job.room_id)
                if fresh is None or not any(
                    e.get("id") == eid for e in fresh.open_expectations(direction="outbound")
                ):
                    continue  # discharged (or gone) since the scan — the PM won

                shipped = _shipped_evidence(wd, slug)
                holder = str(entry.get("holder") or "")

                if shipped is not None and shipped.counts_as_shipped:
                    _record_shipped_work_evidence(
                        project_key, job.job_id, eid, owner, shipped.describe()
                    )

                if shipped is not None:
                    # Shipped work is never respawned: typed evidence goes to an
                    # agent, which verifies delivery and discharges. A PR closed
                    # without merging is evidence too, never an auto-respawn.
                    result = _handoff(
                        project,
                        job,
                        entry,
                        rung=f"shipped:{shipped.kind}:{shipped.pr_number or shipped.sha}",
                        facts=[
                            f"The lane that owed {what!r} is gone, but its work is visible: "
                            f"{shipped.describe()}."
                        ],
                        evidence={
                            **base_evidence,
                            "shipped_kind": shipped.kind,
                            "pr_number": shipped.pr_number,
                            "closes_issues": shipped.closes_issues,
                            "branch": shipped.branch,
                        },
                        suggested_action=(
                            "Verify the work was delivered, then discharge the expectation "
                            "(expectation-remove). A PR closed without merging was a deliberate "
                            "decision: do not respawn over it."
                            if shipped.kind == "closed_unmerged"
                            else "Verify the work was delivered, then discharge the expectation "
                            "(expectation-remove)."
                        ),
                    )
                    _note_handoff(result, job, eid, findings, counts, mark=False)
                    continue

                # Unshipped, owner gone: a live session that holds the Job re-owns
                # it; else respawn the lane from the recorded `what`.
                if agent_handoff._live_session_in_room(job.room_id, holder) is not None:
                    result = _handoff(
                        project,
                        job,
                        entry,
                        rung="orphan-live",
                        facts=[
                            f"The lane that owed {what!r} died with no visible work "
                            f"(no PR, no pushed branch)."
                        ],
                        evidence=base_evidence,
                        suggested_action=(
                            "Re-own it: respawn the lane, or discharge the expectation if it is "
                            "moot."
                        ),
                    )
                    if result.delivered:
                        _note_handoff(result, job, eid, findings, counts, mark=False)
                        continue
                if slug and _respawn_lane(project_key, slug, what, job.job_id):
                    counts["respawned"] += 1
                    findings.append(f"respawned: {slug} ({eid})")
                    _bump_attempts(job.job_id, eid)
                    continue
                _bump_attempts(job.job_id, eid)
                result = _handoff(
                    project,
                    job,
                    entry,
                    rung="no-respawn",
                    facts=[
                        f"The lane that owed {what!r} is gone. No live session holds this Job "
                        f"and there is no respawnable lane slug (owner: "
                        f"{owner if slug else 'unrecorded'})."
                    ],
                    evidence=base_evidence,
                    suggested_action=(
                        "Decide whether to start the work again under a new lane, discharge the "
                        "expectation if it is moot, or ask the human one plain question."
                    ),
                )
                _note_handoff(result, job, eid, findings, counts, mark=True)
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
            f"{counts['handed_off']} handed off, {counts['unreachable']} unreachable, "
            f"{counts['rate_capped']} rate-capped"
        ),
        "duration": time.time() - t0,
    }


def run_expectation_reconciliation() -> dict:
    """Reflection entrypoint. Iterates every local project."""
    return run_per_project_audit(_reconcile_project, name="expectation-reconciler")
