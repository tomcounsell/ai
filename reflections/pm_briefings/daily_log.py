"""
reflections/pm_briefings/daily_log.py — Daily-log slot.

End-of-day per-project recap delivered as an audio brief plus written
follow-up. The dispatcher in ``__init__.py`` calls ``build(project,
slot_config)`` once per matching slot tick.

This slot is PURE — it does NOT enqueue Telegram payloads, does NOT release
the SETNX lock, does NOT mark the per-project Reflection record. The
dispatcher in ``__init__.py`` owns all side effects.

The vault file write (Markdown day log under ``~/work-vault/.../daily-logs/``)
is gated by ``slot_config.vault_writer: true`` to avoid iCloud conflict-copy
races across machines (see Risk 3 in the plan and the Implementation Notes).

This module owns the daily-activity aggregator, renderer, and vault writer
that used to live in ``reflections/daily_report.py``. The helpers were
inlined as part of issue #1292 (the legacy ``daily-report-and-notify``
registry entry was retired in the same cutover).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from reflections.utils import load_local_projects

logger = logging.getLogger("reflections.pm_briefings.daily_log")


SLOT_TYPE = "daily_log"

# Per-source subprocess timeout. Set deliberately tight so a single slow `gh`
# or `git log` call cannot starve the 600s reflection budget. On timeout the
# section is rendered with `[ERROR: timeout]` and the rest of the file lands.
PER_SOURCE_TIMEOUT_S = 30

# Decision-bearing classification types.
# A TelegramMessage qualifies for the "Telegram Decisions" section when its
# classification_type is in this set, message_type == "text", and
# classification_confidence >= 0.5.
DECISION_BEARING_CLASSIFICATIONS: set[str] = {
    "decision",
    "correction",
    "instruction",
    "plan-request",
}


# --- DayActivity dataclass ---------------------------------------------------


@dataclass
class DayActivity:
    """Aggregated activity for a single UTC day across the system.

    Categories are intentionally separate (rather than a single bag) so the
    renderer can produce a stable section order and the audio brief can pick
    a curated cut without re-classifying.

    All entries use full named entities (subject, slug, full title) so that
    text search across vault files surfaces the day file.
    """

    date_iso: str
    commits: list[dict] = field(default_factory=list)  # {project, sha, author, subject, is_merge}
    prs: list[dict] = field(
        default_factory=list
    )  # {project, number, title, state, url, merged_at, closed_at}
    issues: list[dict] = field(default_factory=list)  # {project, number, title, state, url, action}
    sessions: list[dict] = field(
        default_factory=list
    )  # {session_id, project_key, status, pr_url, issue_url, plan_url, turn_count, total_cost_usd}
    telegram_decisions: list[dict] = field(
        default_factory=list
    )  # {chat_id, sender, content, classification_type, ts}
    memories: list[dict] = field(
        default_factory=list
    )  # {memory_id, category, content, project_key, importance}
    crashes: list[dict] = field(default_factory=list)  # {timestamp, commit_sha, reason}
    reflection_runs: list[dict] = field(
        default_factory=list
    )  # {name, status, duration, error, projects}
    errors: dict[str, str] = field(default_factory=dict)  # source_name -> error_message

    def total_entries(self) -> int:
        return (
            len(self.commits)
            + len(self.prs)
            + len(self.issues)
            + len(self.sessions)
            + len(self.telegram_decisions)
            + len(self.memories)
            + len(self.crashes)
            + len(self.reflection_runs)
        )


# --- Vault path helper -------------------------------------------------------


def _resolve_vault_path() -> Path:
    """Return the daily-logs directory under the work vault.

    Vault root: ``~/work-vault/AI Valor Engels System/daily-logs/``.
    The `mkdir -p` is idempotent and creates a local-only path on machines
    without iCloud sync; that's expected and not an error.
    """
    vault_root = Path.home() / "work-vault" / "AI Valor Engels System" / "daily-logs"
    return vault_root


# --- Date utilities ----------------------------------------------------------


def _utc_day_bounds(target_date: datetime) -> tuple[datetime, datetime]:
    """Return (start, end_inclusive) UTC datetimes for the given UTC day.

    start = 00:00:00 UTC of target_date
    end   = 23:59:59.999999 UTC of target_date

    Both are tz-aware. Used by per-source filters to partition timestamps.
    """
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=UTC)
    end = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        23,
        59,
        59,
        999999,
        tzinfo=UTC,
    )
    return start, end


def _date_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


# --- Per-source collectors (each returns ([items], optional error_str)) -----


def _collect_git_for_project(project: dict, target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect commits authored on target_date in the project's repo.

    Returns (commits, error). `commits` items: {project, sha, author, subject,
    is_merge}.  `is_merge` is True when the commit is a merge commit.
    Uses `--since`/`--until` rather than the bare `--since=yesterday` so the
    boundary is unambiguous UTC.
    """
    cwd = project.get("working_directory")
    slug = project.get("slug", "?")
    if not cwd or not Path(cwd).exists():
        return ([], None)

    start, end = _utc_day_bounds(target_date)
    since_iso = start.strftime("%Y-%m-%dT%H:%M:%S+0000")
    until_iso = end.strftime("%Y-%m-%dT%H:%M:%S+0000")

    cmd = [
        "git",
        "log",
        f"--since={since_iso}",
        f"--until={until_iso}",
        "--pretty=format:%H|%P|%an|%s",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PER_SOURCE_TIMEOUT_S, cwd=cwd
        )
    except subprocess.TimeoutExpired:
        return ([], f"git log timeout for {slug}")
    except FileNotFoundError:
        return ([], "git binary not found")
    except Exception as e:
        return ([], f"git log error for {slug}: {e}")

    if result.returncode != 0:
        return ([], f"git log non-zero for {slug}: {result.stderr.strip()[:200]}")

    items: list[dict] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, parents, author, subject = parts
        is_merge = len(parents.split()) > 1
        items.append(
            {
                "project": slug,
                "sha": sha[:12],
                "author": author,
                "subject": subject,
                "is_merge": is_merge,
            }
        )
    return (items, None)


def _collect_github_for_project(
    project: dict, target_date: datetime
) -> tuple[list[dict], list[dict], str | None]:
    """Collect PRs and issues touched on target_date for the project's repo.

    Uses `gh pr list` and `gh issue list` with date-range search. Returns
    ``(prs, issues, error)``. Items are minimal dicts with `project`, `number`,
    `title`, `state`, `url`, plus `action` for issues ("opened"/"closed").
    """
    slug = project.get("slug", "?")
    gh = project.get("github") or {}
    org = (gh.get("org") or "").strip()
    repo = (gh.get("repo") or "").strip()
    if not org or not repo:
        return ([], [], None)
    repo_slug = f"{org}/{repo}"
    cwd = project.get("working_directory") or "."
    date_str = _date_str(target_date)

    prs: list[dict] = []
    issues: list[dict] = []
    last_err: str | None = None

    # PRs merged or closed on target_date
    pr_cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo_slug,
        "--state",
        "all",
        "--search",
        f"merged:{date_str} OR closed:{date_str}",
        "--json",
        "number,title,state,url,mergedAt,closedAt",
        "--limit",
        "100",
    ]
    try:
        result = subprocess.run(
            pr_cmd, capture_output=True, text=True, timeout=PER_SOURCE_TIMEOUT_S, cwd=cwd
        )
        if result.returncode == 0:
            try:
                parsed = json.loads(result.stdout or "[]")
                for p in parsed if isinstance(parsed, list) else []:
                    prs.append(
                        {
                            "project": slug,
                            "number": p.get("number"),
                            "title": p.get("title", ""),
                            "state": p.get("state", ""),
                            "url": p.get("url", ""),
                            "merged_at": p.get("mergedAt"),
                            "closed_at": p.get("closedAt"),
                        }
                    )
            except json.JSONDecodeError as e:
                last_err = f"gh pr list JSON for {slug}: {e}"
        else:
            last_err = f"gh pr list non-zero for {slug}: {result.stderr.strip()[:200]}"
    except subprocess.TimeoutExpired:
        last_err = f"gh pr list timeout for {slug}"
    except FileNotFoundError:
        last_err = "gh binary not found"
    except Exception as e:
        last_err = f"gh pr list error for {slug}: {e}"

    # Issues opened or closed on target_date
    issue_cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo_slug,
        "--state",
        "all",
        "--search",
        f"created:{date_str} OR closed:{date_str}",
        "--json",
        "number,title,state,url,createdAt,closedAt",
        "--limit",
        "100",
    ]
    try:
        result = subprocess.run(
            issue_cmd,
            capture_output=True,
            text=True,
            timeout=PER_SOURCE_TIMEOUT_S,
            cwd=cwd,
        )
        if result.returncode == 0:
            try:
                parsed = json.loads(result.stdout or "[]")
                start, end = _utc_day_bounds(target_date)
                for it in parsed if isinstance(parsed, list) else []:
                    created = it.get("createdAt") or ""
                    closed = it.get("closedAt") or ""
                    action = (
                        "opened"
                        if _iso_in_window(created, start, end)
                        else ("closed" if _iso_in_window(closed, start, end) else "touched")
                    )
                    issues.append(
                        {
                            "project": slug,
                            "number": it.get("number"),
                            "title": it.get("title", ""),
                            "state": it.get("state", ""),
                            "url": it.get("url", ""),
                            "action": action,
                        }
                    )
            except json.JSONDecodeError as e:
                last_err = f"gh issue list JSON for {slug}: {e}"
        else:
            err = f"gh issue list non-zero for {slug}: {result.stderr.strip()[:200]}"
            last_err = (last_err + " | " + err) if last_err else err
    except subprocess.TimeoutExpired:
        err = f"gh issue list timeout for {slug}"
        last_err = (last_err + " | " + err) if last_err else err
    except FileNotFoundError:
        last_err = (last_err or "") + " | gh binary not found"
    except Exception as e:
        err = f"gh issue list error for {slug}: {e}"
        last_err = (last_err + " | " + err) if last_err else err

    return (prs, issues, last_err)


def _iso_in_window(iso: str, start: datetime, end: datetime) -> bool:
    """True if ISO 8601 timestamp string falls within [start, end] (UTC)."""
    if not iso:
        return False
    try:
        # gh emits "2026-05-03T11:03:42Z"
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return start <= ts <= end


def _collect_sessions(target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect AgentSession records that completed on target_date.

    Filters via `Model.query.filter(status="completed")` (uses the IndexedField
    on status), then walks results comparing `completed_at`. Returns minimal
    dicts safe to serialize into Markdown.
    """
    try:
        from models.agent_session import AgentSession
    except Exception as e:
        return ([], f"AgentSession import failed: {e}")

    start, end = _utc_day_bounds(target_date)
    items: list[dict] = []
    try:
        completed = AgentSession.query.filter(status="completed")
    except Exception as e:
        return ([], f"AgentSession.query failed: {e}")

    for s in completed:
        ca = getattr(s, "completed_at", None)
        if ca is None:
            continue
        # `completed_at` is a DatetimeField — Popoto strips tzinfo on save
        if isinstance(ca, datetime):
            ts = ca if ca.tzinfo else ca.replace(tzinfo=UTC)
        else:
            try:
                ts = datetime.fromtimestamp(float(ca), tz=UTC)
            except (TypeError, ValueError):
                continue
        if not (start <= ts <= end):
            continue
        items.append(
            {
                "session_id": getattr(s, "agent_session_id", None)
                or getattr(s, "session_id", None),
                "session_type": getattr(s, "session_type", None),
                "project_key": getattr(s, "project_key", None),
                "status": getattr(s, "status", None),
                "pr_url": getattr(s, "pr_url", None),
                "issue_url": getattr(s, "issue_url", None),
                "plan_url": getattr(s, "plan_url", None),
                "turn_count": getattr(s, "turn_count", 0) or 0,
                "total_cost_usd": float(getattr(s, "total_cost_usd", 0.0) or 0.0),
            }
        )
    return (items, None)


def _collect_telegram_decisions(target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect decision-bearing TelegramMessage records on target_date.

    Heuristic: ``message_type == "text"`` AND ``classification_type`` is in
    DECISION_BEARING_CLASSIFICATIONS AND ``classification_confidence >= 0.5``.
    Project filtering is not applied here — the day log spans the whole
    system; per-project rendering happens in the renderer when a chat is
    associated with a project.
    """
    try:
        from models.telegram import TelegramMessage
    except Exception as e:
        return ([], f"TelegramMessage import failed: {e}")

    start, end = _utc_day_bounds(target_date)
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    items: list[dict] = []
    try:
        all_msgs = TelegramMessage.query.all()
    except Exception as e:
        return ([], f"TelegramMessage.query.all failed: {e}")

    for m in all_msgs:
        ts = getattr(m, "timestamp", None)
        if ts is None or not (start_ts <= float(ts) <= end_ts):
            continue
        if (getattr(m, "message_type", None) or "text") != "text":
            continue
        ctype = getattr(m, "classification_type", None) or ""
        if ctype.lower() not in DECISION_BEARING_CLASSIFICATIONS:
            continue
        conf = getattr(m, "classification_confidence", None)
        if conf is not None and float(conf) < 0.5:
            continue
        content = getattr(m, "content", "") or ""
        items.append(
            {
                "chat_id": getattr(m, "chat_id", None),
                "sender": getattr(m, "sender", None),
                "content": content[:300],
                "classification_type": ctype,
                "ts": float(ts),
                "project_key": getattr(m, "project_key", None),
            }
        )
    return (items, None)


def _collect_memories(target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect Memory records observed (created/strengthened) on target_date.

    Memory has no native `created_at` field. We infer the date from
    `metadata.outcome_history[0].ts` — the earliest outcome event. Memories
    with no outcome history are excluded (acceptable partial coverage).
    Filters to `metadata.category` in {"decision", "correction", "surprise"}.
    """
    try:
        from models.memory import Memory
    except Exception as e:
        return ([], f"Memory import failed: {e}")

    interesting = {"decision", "correction", "surprise"}
    start, end = _utc_day_bounds(target_date)
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    items: list[dict] = []

    try:
        all_mem = Memory.query.all()
    except Exception as e:
        return ([], f"Memory.query.all failed: {e}")

    for m in all_mem:
        md = getattr(m, "metadata", None) or {}
        cat = (md.get("category") or "").lower()
        if cat not in interesting:
            continue
        oh = md.get("outcome_history") or []
        if not oh or not isinstance(oh, list):
            continue
        first_ts = None
        for entry in oh:
            if isinstance(entry, dict) and entry.get("ts"):
                try:
                    first_ts = float(entry["ts"])
                    break
                except (TypeError, ValueError):
                    continue
        if first_ts is None or not (start_ts <= first_ts <= end_ts):
            continue
        items.append(
            {
                "memory_id": getattr(m, "memory_id", None),
                "category": cat,
                "content": (getattr(m, "content", "") or "")[:300],
                "project_key": getattr(m, "project_key", None),
                "importance": float(getattr(m, "importance", 1.0) or 1.0),
            }
        )
    return (items, None)


def _collect_crashes(target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect bridge crashes from monitoring/crash_tracker for target_date.

    Uses ``get_recent_events`` over a 48h window then filters to the target
    UTC day; ``get_recent_crashes(86400)`` only covers the trailing 24h from
    *now*, which would miss yesterday's crashes when the slot runs at
    00:30 UTC the next day.
    """
    try:
        from monitoring.crash_tracker import get_recent_events
    except Exception as e:
        return ([], f"crash_tracker import failed: {e}")

    start, end = _utc_day_bounds(target_date)
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    items: list[dict] = []
    try:
        events = get_recent_events(window_seconds=48 * 3600)
    except Exception as e:
        return ([], f"get_recent_events failed: {e}")
    for ev in events:
        if ev.event_type != "crash":
            continue
        if not (start_ts <= float(ev.timestamp) <= end_ts):
            continue
        items.append(
            {
                "timestamp": float(ev.timestamp),
                "commit_sha": ev.commit_sha,
                "reason": ev.reason or "",
            }
        )
    return (items, None)


def _collect_reflection_runs(target_date: datetime) -> tuple[list[dict], str | None]:
    """Collect Reflection.run_history entries that ran on target_date.

    Walks all Reflection records, scans `run_history`, and emits one item per
    matching entry with the reflection name and per-project breakdown.
    """
    try:
        from models.reflection import Reflection
    except Exception as e:
        return ([], f"Reflection import failed: {e}")

    start, end = _utc_day_bounds(target_date)
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    items: list[dict] = []

    try:
        all_refl = Reflection.query.all()
    except Exception as e:
        return ([], f"Reflection.query.all failed: {e}")

    for r in all_refl:
        history = getattr(r, "run_history", None) or []
        if not isinstance(history, list):
            continue
        for entry in history:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if ts is None:
                continue
            try:
                ts_f = float(ts)
            except (TypeError, ValueError):
                continue
            if not (start_ts <= ts_f <= end_ts):
                continue
            items.append(
                {
                    "name": r.name,
                    "status": entry.get("status", "unknown"),
                    "duration": float(entry.get("duration", 0.0) or 0.0),
                    "error": entry.get("error"),
                    "projects": entry.get("projects") or [],
                    "ts": ts_f,
                }
            )
    return (items, None)


# --- Aggregator entrypoint ---------------------------------------------------


async def _collect_day_activity(target_date: datetime) -> DayActivity:
    """Aggregate substantive activity for a single UTC day across the system.

    Data sources (7 total):
      1. Git commits per project (from `projects.json` with working_directory).
      2. GitHub PRs and issues per project (gh CLI).
      3. AgentSession records completed on target_date (Popoto query).
      4. TelegramMessage decisions on target_date (heuristic filter).
      5. Memory observations on target_date (metadata.outcome_history[0].ts).
      6. Bridge crashes on target_date (monitoring.crash_tracker).
      7. Reflection.run_history entries on target_date.

    Per-source failures (timeout, missing binary, query error) are recorded in
    `activity.errors` and the rest of the file still lands. Per-project git/gh
    queries are run concurrently via asyncio.gather to keep the wall-clock
    budget under the 600s reflection timeout.
    """
    activity = DayActivity(date_iso=_date_str(target_date))
    projects = load_local_projects()

    # 1+2. Git + GitHub per project, concurrent via thread pool.
    async def _git_one(p: dict) -> tuple[list[dict], str | None]:
        return await asyncio.to_thread(_collect_git_for_project, p, target_date)

    async def _gh_one(p: dict) -> tuple[list[dict], list[dict], str | None]:
        return await asyncio.to_thread(_collect_github_for_project, p, target_date)

    git_results = await asyncio.gather(*(_git_one(p) for p in projects), return_exceptions=True)
    gh_results = await asyncio.gather(*(_gh_one(p) for p in projects), return_exceptions=True)

    for p, res in zip(projects, git_results, strict=False):
        if isinstance(res, Exception):
            activity.errors[f"git:{p['slug']}"] = str(res)[:200]
            continue
        commits, err = res
        activity.commits.extend(commits)
        if err:
            activity.errors[f"git:{p['slug']}"] = err[:200]

    for p, res in zip(projects, gh_results, strict=False):
        if isinstance(res, Exception):
            activity.errors[f"gh:{p['slug']}"] = str(res)[:200]
            continue
        prs, issues, err = res
        activity.prs.extend(prs)
        activity.issues.extend(issues)
        if err:
            activity.errors[f"gh:{p['slug']}"] = err[:200]

    # 3. Sessions
    sessions, err = await asyncio.to_thread(_collect_sessions, target_date)
    activity.sessions.extend(sessions)
    if err:
        activity.errors["sessions"] = err[:200]

    # 4. Telegram decisions
    tdecisions, err = await asyncio.to_thread(_collect_telegram_decisions, target_date)
    activity.telegram_decisions.extend(tdecisions)
    if err:
        activity.errors["telegram_decisions"] = err[:200]

    # 5. Memories
    mems, err = await asyncio.to_thread(_collect_memories, target_date)
    activity.memories.extend(mems)
    if err:
        activity.errors["memories"] = err[:200]

    # 6. Crashes
    crashes, err = await asyncio.to_thread(_collect_crashes, target_date)
    activity.crashes.extend(crashes)
    if err:
        activity.errors["crashes"] = err[:200]

    # 7. Reflection runs
    refls, err = await asyncio.to_thread(_collect_reflection_runs, target_date)
    activity.reflection_runs.extend(refls)
    if err:
        activity.errors["reflection_runs"] = err[:200]

    return activity


# --- Markdown renderer -------------------------------------------------------


def _render_day_log(activity: DayActivity, target_date: datetime) -> str:
    """Produce the Markdown body of the day log file.

    Section order (priority): Commits & PRs → Issues → Sessions →
    Telegram Decisions → Memory Observations → Errors & Incidents →
    Reflection Findings (demoted, last). Errors collected during aggregation
    are surfaced as `[ERROR: ...]` lines so partial failures are visible.

    Every entry uses full named entities — repo slug, PR/issue title, commit
    subject, session id, classification type — so a `grep` over the vault
    finds the day file from any of those keywords.
    """
    date_iso = activity.date_iso
    lines: list[str] = [f"# Daily Log: {date_iso}", ""]

    if activity.total_entries() == 0 and not activity.errors:
        lines.append(f"## (No system activity recorded for {date_iso})")
        lines.append("")
        return "\n".join(lines)

    # Aggregator error banner
    if activity.errors:
        lines.append("## Aggregator Notes")
        for src, msg in sorted(activity.errors.items()):
            lines.append(f"- `[ERROR: {src}]` {msg}")
        lines.append("")

    # 1. Commits & PRs
    if activity.commits or activity.prs:
        lines.append("## Commits & PRs")
        # Group commits by project
        by_proj: dict[str, list[dict]] = {}
        for c in activity.commits:
            by_proj.setdefault(c["project"], []).append(c)
        for slug in sorted(by_proj.keys()):
            lines.append(f"### {slug}")
            for c in by_proj[slug]:
                marker = "[merge] " if c.get("is_merge") else ""
                lines.append(f"- `{c['sha']}` {marker}{c['subject']} — {c['author']}")
            lines.append("")
        if activity.prs:
            lines.append("### Pull Requests")
            for p in activity.prs:
                num = p.get("number")
                title = p.get("title", "")
                state = p.get("state", "")
                url = p.get("url", "")
                proj = p.get("project", "")
                lines.append(f"- [{proj}] PR #{num} ({state}): {title} — {url}")
            lines.append("")

    # 2. Issues
    if activity.issues:
        lines.append("## Issues")
        for it in activity.issues:
            num = it.get("number")
            title = it.get("title", "")
            state = it.get("state", "")
            url = it.get("url", "")
            proj = it.get("project", "")
            action = it.get("action", "touched")
            lines.append(f"- [{proj}] Issue #{num} ({action}, {state}): {title} — {url}")
        lines.append("")

    # 3. Sessions
    if activity.sessions:
        lines.append("## Agent Sessions")
        for s in activity.sessions:
            sid = s.get("session_id", "?")
            stype = s.get("session_type") or "?"
            pkey = s.get("project_key") or "?"
            turns = s.get("turn_count", 0)
            cost = s.get("total_cost_usd", 0.0) or 0.0
            line_parts = [
                f"- {stype} session `{sid}` (project `{pkey}`, turns {turns}, cost ${cost:.4f})"
            ]
            for k, label in (("pr_url", "PR"), ("issue_url", "issue"), ("plan_url", "plan")):
                v = s.get(k)
                if v:
                    line_parts.append(f"{label}: {v}")
            lines.append(" — ".join(line_parts))
        lines.append("")

    # 4. Telegram Decisions
    if activity.telegram_decisions:
        lines.append("## Telegram Decisions")
        for t in activity.telegram_decisions:
            ts = datetime.fromtimestamp(t["ts"], tz=UTC).strftime("%H:%M UTC")
            sender = t.get("sender") or "?"
            ctype = t.get("classification_type") or ""
            content = (t.get("content") or "").replace("\n", " ").strip()
            chat = t.get("chat_id")
            lines.append(f"- {ts} [{ctype}] {sender} (chat `{chat}`): {content}")
        lines.append("")

    # 5. Memory Observations
    if activity.memories:
        lines.append("## Memory Observations")
        for m in activity.memories:
            cat = m.get("category", "?")
            pkey = m.get("project_key") or "?"
            content = (m.get("content") or "").replace("\n", " ").strip()
            mid = m.get("memory_id", "?")
            lines.append(f"- [{cat}] (project `{pkey}`, id `{mid}`): {content}")
        lines.append("")

    # 6. Errors & Incidents
    if activity.crashes:
        lines.append("## Errors & Incidents")
        for c in activity.crashes:
            ts = datetime.fromtimestamp(c["timestamp"], tz=UTC).strftime("%H:%M UTC")
            sha = c.get("commit_sha", "?")
            reason = c.get("reason") or "(no reason recorded)"
            lines.append(f"- {ts} crash @ `{sha}`: {reason}")
        lines.append("")

    # 7. Reflection Findings (demoted)
    if activity.reflection_runs:
        lines.append("## Reflection Findings")
        for r in activity.reflection_runs:
            name = r.get("name", "?")
            status = r.get("status", "?")
            dur = r.get("duration", 0.0) or 0.0
            err = r.get("error")
            line = f"- `{name}` — {status} in {dur:.1f}s"
            if err:
                line += f" — error: {err[:200]}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --- Audio brief signal mapping ----------------------------------------------


def _activity_to_signals(activity: DayActivity) -> dict[str, list[dict]]:
    """Convert DayActivity into the raw_signals dict the builder consumes.

    Maps to ``builder._format_signals_for_prompt``'s expected shape — keys are
    category labels, values are lists of ``{subject, number}`` dicts.
    """
    signals: dict[str, list[dict]] = {}

    if activity.prs:
        signals["merges"] = [
            {
                "subject": p.get("title", ""),
                "pr_number": p.get("number"),
            }
            for p in activity.prs
        ]
    if activity.commits:
        # Non-merge commits as a separate "shipped" stream so the LLM sees them.
        non_merge = [c for c in activity.commits if not c.get("is_merge")]
        if non_merge:
            signals["commits"] = [{"subject": c.get("subject", "")} for c in non_merge[:30]]
    if activity.issues:
        signals["issues"] = [
            {
                "title": it.get("title", ""),
                "number": it.get("number"),
            }
            for it in activity.issues
        ]
    if activity.crashes:
        signals["incidents"] = [
            {"subject": (c.get("reason") or "bridge crash")} for c in activity.crashes
        ]
    if activity.telegram_decisions:
        signals["decisions"] = [
            {"subject": (t.get("content") or "")[:140]} for t in activity.telegram_decisions
        ]
    return signals


# --- Vault writer ------------------------------------------------------------


def _write_vault_log(activity: DayActivity, target_date: datetime) -> Path:
    """Atomic write of the rendered day log to the vault.

    Idempotent ``mkdir -p`` on the parent. Writes to a temp file in the same
    directory and renames into place so a crash mid-write doesn't leave a
    partial file on disk.
    """
    body = _render_day_log(activity, target_date)
    dest_dir = _resolve_vault_path()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{activity.date_iso}.md"

    # Atomic write: tempfile in the same dir + os.replace.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{activity.date_iso}.", suffix=".md.tmp", dir=str(dest_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_path, dest)
    except Exception:
        # Best-effort cleanup of tmp on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Daily log written to %s", dest)
    return dest


# --- Slot entrypoint ---------------------------------------------------------


def _to_signals_dict(activity: DayActivity) -> dict[str, list[dict]]:
    """Thin alias for ``_activity_to_signals`` kept as the slot's public API.

    Originally separate when ``daily_report._activity_to_signals`` was the
    canonical implementation. After the inlining (issue #1292) both names map
    to the same function; the alias remains so the slot's intent — "convert
    activity to builder-shaped signals" — reads at the call site.
    """
    return _activity_to_signals(activity)


def build(project: dict, slot_config: dict) -> tuple[str, str, dict[str, Any]]:
    """Build the per-project daily-log recap.

    Args:
        project: The full project dict.
        slot_config: The slot's config dict. Recognized keys:
            ``vault_writer`` (bool, default False) — if True, this slot
            writes the per-day vault Markdown file. Single-machine-ownership
            invariant ensures only one slot across all projects/machines has
            this set.

    Returns:
        ``(transcript, followup_markdown, raw_signals)``. On skip-when-empty
        (no activity for the target day), returns ``("", "", {})``.
    """
    from bridge.utc import utc_now
    from reflections.pm_briefings import builder

    target_date = utc_now() - timedelta(days=1)

    # Run the async collector. In production we are inside a running event
    # loop: the parent ``pm_briefings.run()`` is ``async def`` and the
    # reflection scheduler awaits it directly (see
    # ``agent/reflection_scheduler.py``), so the ``loop.is_running()`` branch
    # is the production path. ``asyncio.run`` cannot run inside an existing
    # loop, so we hand the coroutine to a dedicated thread that owns its own
    # event loop. The else branch handles direct sync invocations (CLI tools,
    # tests) where no loop is running yet.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                activity = pool.submit(asyncio.run, _collect_day_activity(target_date)).result()
        else:
            activity = asyncio.run(_collect_day_activity(target_date))
    except RuntimeError:
        activity = asyncio.run(_collect_day_activity(target_date))

    # Vault file write — gated by vault_writer flag (default False).
    if slot_config.get("vault_writer") is True:
        try:
            dest = _write_vault_log(activity, target_date)
            logger.info("Vault log written: %s", dest)
        except Exception as e:  # swallow-ok: vault write is best-effort
            logger.warning("Vault write failed for %s: %s", target_date.date().isoformat(), e)

    raw_signals = _to_signals_dict(activity)

    pm = project.get("pm_briefing") or {}
    fallback_message = (
        slot_config.get("fallback_message")
        or pm.get("fallback_message")
        or "Nothing shipped yesterday."
    )
    skip_when_empty = bool(slot_config.get("skip_when_empty", pm.get("skip_when_empty", True)))

    # Drive the brief through the same builder pipeline as the morning slot
    # so the no-numbers + word-count guards stay applied uniformly.
    transcript, followup = builder.build(
        raw_signals,
        fallback_message=fallback_message,
        skip_when_empty=skip_when_empty,
        project=project,
    )
    return transcript, followup, raw_signals
