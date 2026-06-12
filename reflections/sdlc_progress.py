"""
reflections/sdlc_progress.py — Stalled SDLC pipeline detection (state-layer).

Companion to `agent.session_health` (process-layer). This reflection looks at
open SDLC PRs and asks: is there an open issue, no active session, an old last
commit, and a non-draft PR? If all five gates pass, fire a single Telegram
alert and dedupe by (slug, last-commit-sha) for ``SDLC_STALL_COOLDOWN_HOURS``.

Notification-only for v1 — never creates or resumes PM sessions. See
``docs/features/pm-session-liveness.md`` for the full state-layer rationale.

Configuration (all optional):
    SDLC_STALL_THRESHOLD_HOURS  default 4   — minimum age of last commit
    SDLC_STALL_COOLDOWN_HOURS   default 6   — dedup window per (slug, sha)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from reflections.utils import run_per_project_audit

logger = logging.getLogger("reflections.sdlc_progress")

# Raw Redis namespace — NOT Popoto-managed. Pure bookkeeping per
# CLAUDE.md "no raw Redis on Popoto-managed keys" exception (precedent:
# docs_auditor.py REDIS_ISSUE_DEDUP_PREFIX).
_DEDUP_PREFIX = "sdlc:stall:alert"

# Only branches matching session/sdlc-<N> are flagged. Excludes session/<other-slug>
# and ad-hoc branches — those aren't SDLC pipelines.
_SDLC_BRANCH_RE = re.compile(r"^session/sdlc-\d+$")

_DEFAULT_THRESHOLD_HOURS = 4
_DEFAULT_COOLDOWN_HOURS = 6


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import, error-tolerant)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _threshold_seconds() -> int:
    try:
        hours = float(os.environ.get("SDLC_STALL_THRESHOLD_HOURS", _DEFAULT_THRESHOLD_HOURS))
    except (TypeError, ValueError):
        hours = _DEFAULT_THRESHOLD_HOURS
    return int(hours * 3600)


def _cooldown_seconds() -> int:
    try:
        hours = float(os.environ.get("SDLC_STALL_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS))
    except (TypeError, ValueError):
        hours = _DEFAULT_COOLDOWN_HOURS
    return int(hours * 3600)


def _run_gh(args: list[str], *, cwd: str, timeout: int = 20) -> subprocess.CompletedProcess | None:
    """Run a gh CLI command. Returns CompletedProcess on success, None on failure."""
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
    proc = _run_gh(["issue", "view", str(number), "--json", "state"], cwd=cwd, timeout=15)
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
            timeout=15,
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


def _has_active_session(slug: str) -> bool | None:
    """True if any non-terminal AgentSession exists for this slug.

    Returns None on Redis/Popoto failure (caller must treat as 'unknown' and
    decline to alert, to avoid false positives during Redis flap).
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import NON_TERMINAL_STATUSES
    except Exception as exc:  # pragma: no cover — import errors are environmental
        logger.warning("sdlc_progress: AgentSession import failed: %s", exc)
        return None

    try:
        # Popoto KeyField on slug is indexed.
        sessions = list(AgentSession.query.filter(slug=slug).all())
    except Exception as exc:
        logger.warning("sdlc_progress: AgentSession.query failed for %s: %s", slug, exc)
        return None

    return any(s.status in NON_TERMINAL_STATUSES for s in sessions)


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert. All failures swallowed and logged."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: valor-telegram not on PATH; skipping alert")
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: valor-telegram timed out")
    except Exception as exc:
        logger.warning("sdlc_progress: valor-telegram failed: %s", exc)


def _dedup_set(slug: str, sha: str, ttl: int) -> bool:
    """Atomically set the dedup key. Returns True if this caller is the writer.

    Redis unavailable → returns False so we DO NOT send the alert (mirrors
    plan: "Redis unavailable for dedup write: skip Telegram send rather than
    potentially spamming").
    """
    key = f"{_DEDUP_PREFIX}:{slug}:{sha}"
    try:
        r = _get_redis()
        return bool(r.set(key, "1", nx=True, ex=ttl))
    except Exception as exc:
        logger.warning("sdlc_progress: dedup set failed for %s: %s", key, exc)
        return False


def _check_project_stalls(project: dict) -> dict:
    """Per-project body called by ``run_per_project_audit``.

    Returns ``{status, findings, summary, duration}``.
    """
    t0 = time.time()
    wd = project.get("working_directory", "")
    slug_proj = project.get("slug", "?")
    findings: list[str] = []
    alerts_fired = 0

    if not wd or not Path(wd).is_dir():
        return {
            "status": "skipped",
            "findings": [],
            "summary": "sdlc-progress-check: no working_directory",
            "duration": time.time() - t0,
        }

    threshold = _threshold_seconds()
    cooldown = _cooldown_seconds()
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
            # Closed issue — skip (gate fails).
            continue
        if issue_open is None:
            # Lookup failed — refuse to alert (avoid false positives).
            continue

        commit = _last_commit(wd, branch)
        if commit is None:
            # Branch not present locally — skip silently.
            continue
        sha, ts = commit
        age = now - ts
        if age < threshold:
            continue

        active = _has_active_session(slug)
        if active is None or active:
            # Either active or unknown — refuse to alert.
            continue

        message = (
            f"[{slug_proj}] SDLC stall: PR #{pr.get('number')} ({slug}) "
            f"last commit {age // 3600}h ago, no active session, issue #{issue_num} open."
        )
        if _dedup_set(slug, sha, cooldown):
            _send_alert(message)
            alerts_fired += 1
            findings.append(message)
        else:
            logger.info("sdlc_progress: alert deduped for slug=%s sha=%s", slug, sha[:8])

    return {
        "status": "ok",
        "findings": findings,
        "summary": (
            f"sdlc-progress-check: {len(prs)} SDLC PR(s) inspected, {alerts_fired} alert(s) fired"
        ),
        "duration": time.time() - t0,
    }


def run_sdlc_progress_check() -> dict:
    """Reflection entrypoint. Iterates every local project."""
    return run_per_project_audit(_check_project_stalls, name="sdlc-progress-check")
