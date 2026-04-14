"""
reflections/maintenance.py — System maintenance reflection callables.

Extracted from scripts/reflections.py steps:
  - step_clean_legacy        → run_legacy_code_scan
  - step_redis_cleanup       → run_redis_ttl_cleanup
  - step_redis_data_quality  → run_redis_data_quality
  - step_branch_plan_cleanup → run_branch_plan_cleanup
  - step_disk_space_check    → run_disk_space_check
  - step_analytics_rollup    → run_analytics_rollup

All functions accept no arguments and return:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from reflections.utils import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.maintenance")


async def run_legacy_code_scan() -> dict:
    """Scan for legacy code patterns: TODO comments, deprecated typing imports.

    Maps to monolith step: step_clean_legacy
    """
    findings = []

    try:
        result = subprocess.run(
            ["grep", "-r", "TODO:", "--include=*.py", str(PROJECT_ROOT)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        todo_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        if todo_count > 0:
            findings.append(f"Found {todo_count} TODO comments to review")
    except Exception as e:
        logger.warning(f"TODO scan failed: {e}")

    deprecated_patterns = [
        "from typing import Optional",
        "from typing import List",
        "from typing import Dict",
    ]
    for pattern in deprecated_patterns:
        try:
            result = subprocess.run(
                ["grep", "-r", pattern, "--include=*.py", str(PROJECT_ROOT)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.stdout.strip():
                count = len(result.stdout.strip().split("\n"))
                findings.append(f"Found {count} instances of deprecated typing import: {pattern}")
        except Exception as e:
            logger.warning(f"Deprecated import scan failed for {pattern}: {e}")

    summary = f"Legacy code scan: {len(findings)} finding(s)"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_redis_ttl_cleanup() -> dict:
    """Run TTL cleanup on all Redis models to remove expired records.

    Maps to monolith step: step_redis_cleanup
    Cleans up: TelegramMessage, Link, Chat, AgentSession (90-day),
    BridgeEvent (7-day), ReflectionIgnore (expired).
    """
    findings = []

    try:
        from models.agent_session import AgentSession
        from models.bridge_event import BridgeEvent
        from models.chat import Chat
        from models.link import Link
        from models.reflections import ReflectionIgnore
        from models.telegram import TelegramMessage

        msg_deleted = TelegramMessage.cleanup_expired(max_age_days=90)
        link_deleted = Link.cleanup_expired(max_age_days=90)
        chat_deleted = Chat.cleanup_expired(max_age_days=90)
        session_deleted = AgentSession.cleanup_expired(max_age_days=90)
        event_deleted = BridgeEvent.cleanup_old(max_age_seconds=7 * 86400)
        ignore_deleted = ReflectionIgnore.cleanup_expired()

        total = (
            msg_deleted
            + link_deleted
            + chat_deleted
            + session_deleted
            + event_deleted
            + ignore_deleted
        )
        summary = (
            f"Redis cleanup: {total} expired records removed "
            f"(msgs={msg_deleted}, links={link_deleted}, "
            f"chats={chat_deleted}, sessions={session_deleted}, "
            f"events={event_deleted}, "
            f"ignores={ignore_deleted})"
        )
        logger.info(summary)
        findings.append(summary)

    except Exception as e:
        logger.warning(f"Redis TTL cleanup failed (non-fatal): {e}")
        return {"status": "error", "findings": [], "summary": f"Redis cleanup error: {e}"}

    return {"status": "ok", "findings": findings, "summary": findings[0] if findings else "ok"}


async def run_redis_data_quality() -> dict:
    """Run Redis data quality checks: unsummarized links, dead channels, error patterns.

    Maps to monolith step: step_redis_data_quality
    """
    findings: list[str] = []

    try:
        import time as _time

        from models.agent_session import AgentSession
        from models.chat import Chat
        from models.link import Link
        from models.telegram import TelegramMessage

        week_ago = _time.time() - (7 * 86400)
        month_ago = _time.time() - (30 * 86400)

        # 1. Unsummarized links
        all_links = Link.query.all()
        unsummarized = [
            link
            for link in all_links
            if link.timestamp and link.timestamp > week_ago and not link.ai_summary
        ]
        if unsummarized:
            findings.append(f"{len(unsummarized)} links shared in last 7 days have no AI summary")
            for link in unsummarized[:5]:
                findings.append(
                    f"  Unsummarized: {link.url} (chat={link.chat_id}, status={link.status})"
                )

        # 2. Dead channels
        all_chats = Chat.query.all()
        dead_chats = [chat for chat in all_chats if chat.updated_at and chat.updated_at < month_ago]
        if dead_chats:
            findings.append(f"{len(dead_chats)} chat(s) with no activity in 30+ days")
            for chat in dead_chats[:5]:
                _ua = chat.updated_at
                if isinstance(_ua, datetime):
                    _ua = _ua.timestamp() if _ua.tzinfo else _ua.replace(tzinfo=UTC).timestamp()
                days_inactive = int((_time.time() - (_ua or 0)) / 86400)
                findings.append(
                    f"  Inactive: {chat.chat_name} ({days_inactive} days, type={chat.chat_type})"
                )

        # 3. Error patterns in recent session transcripts
        recent_cutoff = _time.time() - (7 * 86400)
        all_sessions = AgentSession.query.all()
        recent_sessions = [
            s
            for s in all_sessions
            if (
                lambda sa: (
                    sa is not None
                    and (sa.timestamp() if isinstance(sa, datetime) else float(sa)) > recent_cutoff
                )
            )(s.started_at)
        ]

        error_keywords: dict[str, int] = {}
        for session in recent_sessions:
            if not session.log_path:
                continue
            log_path = Path(session.log_path)
            if not log_path.exists():
                continue
            try:
                content = log_path.read_text(errors="replace")
                for keyword in [
                    "ImportError",
                    "ModuleNotFoundError",
                    "ConnectionError",
                    "TimeoutError",
                    "PermissionError",
                    "FileNotFoundError",
                    "KeyError",
                    "AttributeError",
                ]:
                    count = content.count(keyword)
                    if count > 0:
                        error_keywords[keyword] = error_keywords.get(keyword, 0) + count
            except OSError:
                continue

        if error_keywords:
            sorted_errors = sorted(error_keywords.items(), key=lambda x: x[1], reverse=True)
            findings.append(
                f"Error patterns across {len(recent_sessions)} recent session transcripts:"
            )
            for keyword, count in sorted_errors[:5]:
                findings.append(f"  {keyword}: {count} occurrences")

        # 4. Message volume per chat
        all_messages = TelegramMessage.query.all()[:10000]
        recent_messages = [m for m in all_messages if m.timestamp and m.timestamp > week_ago]
        chat_volumes: dict[str, int] = {}
        for msg in recent_messages:
            chat_id = msg.chat_id or "unknown"
            chat_volumes[chat_id] = chat_volumes.get(chat_id, 0) + 1

        if chat_volumes:
            sorted_chats = sorted(chat_volumes.items(), key=lambda x: x[1], reverse=True)
            findings.append(
                f"Message volume (last 7 days): {len(recent_messages)} messages "
                f"across {len(chat_volumes)} chats"
            )
            for chat_id, count in sorted_chats[:3]:
                chat_name = chat_id
                chat_records = Chat.query.filter(chat_id=chat_id)
                if chat_records:
                    chat_name = chat_records[0].chat_name or chat_id
                findings.append(f"  {chat_name}: {count} messages")

    except Exception as e:
        logger.warning(f"Redis data quality check failed (non-fatal): {e}")
        findings.append(f"Data quality check error: {e}")

    summary = f"Data quality: {len(findings)} finding(s)"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_branch_plan_cleanup() -> dict:
    """Clean up stale git branches and audit plan files.

    Maps to monolith step: step_branch_plan_cleanup
    - Deletes local branches merged into main
    - Audits docs/plans/ for complete/orphaned/stale-issue plans
    """
    findings: list[str] = []
    projects = load_local_projects()

    # --- Stale branch cleanup ---
    try:
        result = subprocess.run(
            ["git", "branch", "--merged", "main"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                branch = line.strip().lstrip("* ")
                if branch and branch not in ("main", "master"):
                    del_result = subprocess.run(
                        ["git", "branch", "-d", branch],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        cwd=str(PROJECT_ROOT),
                    )
                    if del_result.returncode == 0:
                        findings.append(f"Deleted merged branch: {branch}")
                        logger.info(f"Branch cleanup: deleted merged branch {branch}")
    except Exception as e:
        logger.warning(f"Branch cleanup failed (non-fatal): {e}")

    # --- Plan file cleanup ---
    plans_dir = PROJECT_ROOT / "docs" / "plans"
    if not plans_dir.exists():
        return {
            "status": "ok",
            "findings": findings,
            "summary": f"Branch cleanup: {len(findings)} finding(s)",
        }

    plan_files = sorted(plans_dir.glob("*.md"))

    project_wd = None
    for project in projects:
        if project.get("github"):
            project_wd = project["working_directory"]
            break

    # Detect duplicates
    normalized: dict[str, list[Path]] = {}
    for pf in plan_files:
        key = pf.stem.replace("-", "_").lower()
        normalized.setdefault(key, []).append(pf)
    for _key, dupes in normalized.items():
        if len(dupes) > 1:
            names = ", ".join(d.name for d in dupes)
            findings.append(f"Duplicate plans: {names}")

    # Extract issue refs
    plan_issue_refs: dict[Path, list[int]] = {}
    for plan_file in plan_files:
        plan_text = plan_file.read_text(errors="replace")
        refs: set[int] = set()
        for m in re.finditer(r"#(\d+)", plan_text):
            refs.add(int(m.group(1)))
        for m in re.finditer(r"github\.com/[^/]+/[^/]+/issues/(\d+)", plan_text):
            refs.add(int(m.group(1)))
        plan_issue_refs[plan_file] = sorted(refs)

    # Check issue states
    async def check_issue_state(issue_num: int) -> tuple[int, str]:
        if not project_wd:
            return issue_num, "unknown"
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "issue",
                "view",
                str(issue_num),
                "--json",
                "state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_wd,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                return issue_num, data.get("state", "unknown").lower()
        except Exception as e:
            logger.warning(f"Could not check issue #{issue_num}: {e}")
        return issue_num, "unknown"

    all_issue_nums: set[int] = set()
    for refs in plan_issue_refs.values():
        all_issue_nums.update(refs)

    issue_states: dict[int, str] = {}
    if all_issue_nums:
        issue_list = sorted(all_issue_nums)
        for i in range(0, len(issue_list), 10):
            batch = issue_list[i : i + 10]
            results = await asyncio.gather(
                *[check_issue_state(n) for n in batch], return_exceptions=True
            )
            for r in results:
                if isinstance(r, tuple):
                    issue_states[r[0]] = r[1]

    stats = {"complete": 0, "orphaned": 0, "closed_issue": 0, "active": 0}

    for plan_file in plan_files:
        plan_name = plan_file.stem
        plan_text = plan_file.read_text(errors="replace")
        refs = plan_issue_refs.get(plan_file, [])

        checkboxes = re.findall(r"- \[([ xX])\]", plan_text)
        checked = sum(1 for c in checkboxes if c.lower() == "x")
        is_complete = checkboxes and checked == len(checkboxes)

        if is_complete:
            stats["complete"] += 1
            findings.append(
                f"Plan complete: {plan_name} -- "
                f"run /do-docs then delete docs/plans/{plan_file.name}"
            )
            continue

        if refs:
            ref_states = [issue_states.get(r, "unknown") for r in refs]
            all_closed = all(s == "closed" for s in ref_states if s != "unknown")
            any_open = any(s == "open" for s in ref_states)

            if all_closed and not any_open:
                stats["closed_issue"] += 1
                closed_refs = ", ".join(f"#{r}" for r in refs)
                findings.append(f"Plan with closed issue(s): {plan_file.name} ({closed_refs})")
                continue

            if any_open:
                stats["active"] += 1
                continue

        if project_wd:
            try:
                result = subprocess.run(
                    ["gh", "issue", "list", "--state", "open", "--search", plan_name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=project_wd,
                )
                if result.returncode == 0 and result.stdout.strip():
                    stats["active"] += 1
                    continue
            except Exception as e:
                logger.warning(f"Could not search issues for plan {plan_name}: {e}")

        stats["orphaned"] += 1
        findings.append(f"Orphaned plan (no open issue): {plan_file.name}")

    summary = (
        f"Branch/plan cleanup: {len(findings)} finding(s), "
        f"{stats['active']} active plans, {stats['complete']} complete, "
        f"{stats['orphaned']} orphaned"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_disk_space_check() -> dict:
    """Check available disk space on the project volume.

    Maps to monolith step: step_disk_space_check
    Records a finding when free space drops below 10 GB.
    """
    findings: list[str] = []

    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)

        if free_gb < 10:
            finding = (
                f"Low disk space: {free_gb:.1f} GB free "
                f"of {total_gb:.1f} GB total on project volume"
            )
            findings.append(finding)
            logger.warning(finding)
        else:
            logger.info(f"Disk space OK: {free_gb:.1f} GB free of {total_gb:.1f} GB total")
    except Exception as e:
        logger.exception(f"Failed to check disk space: {e}")
        return {"status": "error", "findings": [], "summary": f"Disk space check error: {e}"}

    summary = findings[0] if findings else "Disk space OK"
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_analytics_rollup() -> dict:
    """Run analytics daily rollup: aggregate metrics and purge old data.

    Maps to monolith step: step_analytics_rollup
    """
    try:
        from analytics.rollup import rollup_daily

        result = rollup_daily()
        summary = (
            f"Analytics rollup: aggregated {result['aggregated_days']} days, "
            f"purged {result['purged_rows']} rows"
        )
        logger.info(summary)
        return {"status": "ok", "findings": [summary], "summary": summary}
    except Exception as e:
        logger.warning(f"Analytics rollup failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Analytics rollup error: {e}"}
