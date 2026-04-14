"""
reflections/daily_report.py — Daily report pipeline callable.

Extracted from scripts/reflections.py pipeline:
  step_produce_report → step_create_github_issue

This is a single callable that runs both sub-steps internally,
preserving ordering without depends_on complexity in the YAML scheduler.

The daily report aggregates findings from all other reflections that ran today
via their Redis Reflection state records, then posts to GitHub and Telegram.

Returns:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
from datetime import datetime

from reflections.utils import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.daily_report")

REFLECTIONS_DIR = PROJECT_ROOT / "logs" / "reflections"


def _collect_reflection_findings() -> dict[str, list[str]]:
    """Collect findings from all Reflection records that ran today.

    Reads from the Reflection model (used by the YAML scheduler) rather than
    ReflectionRun. Returns a dict of category → list[finding].
    """
    findings: dict[str, list[str]] = {}

    try:
        from bridge.utc import utc_now
        from models.reflection import Reflection

        today = utc_now().strftime("%Y-%m-%d")
        all_reflections = Reflection.query.all()

        for reflection in all_reflections:
            if not reflection.ran_at:
                continue
            ran_date = datetime.fromtimestamp(reflection.ran_at).strftime("%Y-%m-%d")
            if ran_date != today:
                continue

            name = reflection.name or "unknown"
            status = reflection.last_status or "unknown"

            if reflection.last_error:
                findings.setdefault(f"{name}", []).append(f"ERROR: {reflection.last_error[:200]}")
            elif status == "ok":
                duration = reflection.last_duration or 0
                findings.setdefault(name, []).append(f"Completed in {duration:.1f}s")

    except Exception as e:
        logger.warning(f"Could not collect reflection findings: {e}")

    return findings


async def run() -> dict:
    """Run the daily report pipeline.

    Pipeline: Produce Report → Create GitHub Issue

    Maps to monolith: step_daily_report_and_notify (which calls step_produce_report
    and step_create_github_issue in sequence)

    Note: The "must run after all other reflections" constraint cannot be
    mechanically enforced by the current scheduler (no DAG). This callable
    aggregates whatever findings exist in Redis at run time. A follow-up
    issue should track DAG scheduling support.

    Raises exceptions on sub-step failure (propagates to scheduler).
    """
    from bridge.utc import utc_now

    findings: list[str] = []
    today = utc_now().strftime("%Y-%m-%d")

    # Sub-step 1: Produce report
    reflection_findings = _collect_reflection_findings()

    report_lines = [
        f"# Reflections Report - {today}",
        "",
        "## Summary",
        f"- Reflections with data: {len(reflection_findings)}",
        "",
    ]

    # Include principal context
    try:
        from agent.sdk_client import load_principal_context

        principal = load_principal_context(condensed=True)
        if principal:
            report_lines.extend(["## Principal Priorities", "", principal, ""])
    except Exception as e:
        logger.debug(f"Could not load principal context: {e}")

    # Add findings by reflection name
    for name, items in reflection_findings.items():
        if items:
            report_lines.append(f"## {name.replace('-', ' ').replace('_', ' ').title()}")
            for item in items:
                report_lines.append(f"- {item}")
            report_lines.append("")

    report_lines.extend(["---", f"*Generated at {utc_now().isoformat()}*"])

    report_content = "\n".join(report_lines)
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REFLECTIONS_DIR / f"report_{today}.md"
    report_file.write_text(report_content)
    logger.info(f"Report written to {report_file}")

    findings.append(f"Report written: {report_file.name}")

    # Sub-step 2: Create GitHub issues and notify Telegram
    try:
        from scripts.reflections_report import create_reflections_issue, reset_dedup_guard

        reset_dedup_guard()
        projects = load_local_projects()
        projects_with_issues = 0

        for project in projects:
            slug = project["slug"]
            if not project.get("github"):
                continue

            project_findings = {k: v for k, v in reflection_findings.items() if v}
            if not project_findings:
                continue

            project_wd = project["working_directory"]
            issue_result = create_reflections_issue(project_findings, today, cwd=project_wd)

            issue_url = ""
            if isinstance(issue_result, str) and issue_result:
                issue_url = issue_result
                projects_with_issues += 1
                findings.append(f"GitHub issue created for {slug}: {issue_url}")
            elif issue_result is True:
                projects_with_issues += 1

            # Telegram notification
            await _post_to_telegram(project, issue_url, reflection_findings, today)

        if projects_with_issues:
            findings.append(f"GitHub issues created for {projects_with_issues} project(s)")

    except Exception as e:
        logger.warning(f"Daily report notification failed: {e}")
        findings.append(f"Notification error: {e}")

    summary = f"Daily report: generated for {today}, {len(reflection_findings)} reflections"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def _post_to_telegram(
    project: dict,
    issue_url: str,
    findings: dict[str, list[str]],
    date: str,
) -> None:
    """Post reflections summary to project's Telegram chat."""
    import os

    groups = project.get("telegram", {}).get("groups", [])
    if not groups:
        return

    session_file = PROJECT_ROOT / "data" / "valor.session"
    if not session_file.exists():
        return

    try:
        from telethon import TelegramClient  # type: ignore[import]

        try:
            api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        except ValueError:
            api_id = 0
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")

        if not api_id or not api_hash:
            return

        slug = project["slug"]
        findings_count = sum(len(v) for v in findings.values())
        msg_lines = [f"Reflections Report — {date}"]
        msg_lines.append(f"Project: {project.get('name', slug)}")
        if findings_count:
            msg_lines.append(f"Findings: {findings_count} items")
        else:
            msg_lines.append("No significant findings today")
        if issue_url:
            msg_lines.append(f"GitHub: {issue_url}")
        message = "\n".join(msg_lines)

        async with TelegramClient(str(session_file), api_id, api_hash) as client:
            for group_name in groups[:1]:
                try:
                    await client.send_message(group_name, message)
                    logger.info(f"Posted reflections summary to {group_name}")
                except Exception as e:
                    logger.warning(f"Could not post to {group_name}: {e}")
    except ImportError:
        logger.info("telethon not available, skipping Telegram post")
    except Exception as e:
        logger.warning(f"Telegram post failed for {project['slug']}: {e}")
