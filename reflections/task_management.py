"""
reflections/task_management.py — Task management reflection callables.

Extracted from scripts/reflections.py steps:
  - step_clean_tasks         → run_task_management
  - step_principal_staleness → run_principal_staleness

All functions accept no arguments and return:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from reflections.utils import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.task_management")


async def run_task_management() -> dict:
    """Clean up task management: check open bugs per project, local TODOs.

    Maps to monolith step: step_clean_tasks
    """
    projects = load_local_projects()
    findings: list[str] = []
    total_findings = 0

    for project in projects:
        slug = project["slug"]

        if not project.get("github") or not project["github"].get("org"):
            logger.info(f"No github config for {slug}, skipping task check")
            continue

        project_wd = project["working_directory"]
        project_findings = []

        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--label", "bug"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=project_wd,
            )
            if result.returncode == 0 and result.stdout.strip():
                bug_lines = result.stdout.strip().split("\n")
                project_findings.append(f"Found {len(bug_lines)} open bug issues on GitHub")
                for line in bug_lines[:5]:
                    project_findings.append(f"  Bug: {line.strip()}")
            elif result.returncode == 0:
                project_findings.append("No open bug issues on GitHub")
        except Exception as e:
            logger.warning(f"Could not check GitHub issues for {slug}: {e}")
            project_findings.append(f"GitHub issue check failed: {e}")

        for finding in project_findings:
            findings.append(f"[{slug}] {finding}")

        total_findings += len(project_findings)

    # Check local TODO files in project root
    todo_files = list(PROJECT_ROOT.glob("**/TODO.md")) + list(PROJECT_ROOT.glob("**/todo.md"))
    for todo_file in todo_files:
        try:
            content = todo_file.read_text()
            unchecked = content.count("[ ]")
            if unchecked > 0:
                findings.append(
                    f"{todo_file.relative_to(PROJECT_ROOT)}: {unchecked} unchecked items"
                )
                total_findings += 1
        except Exception:
            pass

    summary = f"Task management: {total_findings} finding(s) across {len(projects)} projects"
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_principal_staleness() -> dict:
    """Check if PRINCIPAL.md is stale (>90 days since last modification).

    Maps to monolith step: step_principal_staleness
    PRINCIPAL.md encodes the supervisor's strategic context. If it hasn't
    been updated in 90+ days, flag it for review since priorities may
    have shifted.
    """
    principal_path = PROJECT_ROOT / "config" / "PRINCIPAL.md"

    if not principal_path.exists():
        finding = "config/PRINCIPAL.md does not exist — principal context is unavailable"
        logger.warning(finding)
        return {"status": "ok", "findings": [finding], "summary": finding}

    mod_time = datetime.fromtimestamp(principal_path.stat().st_mtime, tz=UTC)
    from bridge.utc import utc_now

    age_days = (utc_now() - mod_time).days
    staleness_threshold = 90

    if age_days > staleness_threshold:
        finding = (
            f"config/PRINCIPAL.md is {age_days} days old (threshold: {staleness_threshold}). "
            "Consider reviewing and updating supervisor priorities."
        )
        logger.warning(
            f"PRINCIPAL.md is stale: last modified {age_days} days ago "
            f"(threshold: {staleness_threshold} days)"
        )
        return {"status": "ok", "findings": [finding], "summary": finding}
    else:
        msg = (
            f"PRINCIPAL.md is fresh: last modified {age_days} days ago "
            f"(threshold: {staleness_threshold} days)"
        )
        logger.info(msg)
        return {"status": "ok", "findings": [], "summary": msg}
