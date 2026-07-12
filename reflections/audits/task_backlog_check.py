"""reflections/audits/task_backlog_check.py — Open-bug and TODO backlog audit.

What it does: Per project, lists open GitHub issues labeled `bug` via gh CLI,
    then scans PROJECT_ROOT for `TODO.md`/`todo.md` files and counts unchecked
    items. Returns a findings list summarizing backlog pressure.
Cadence: 86400s (backlog changes slowly; daily snapshot is sufficient)
Failure modes:
    - no github config for a project -> skipped with info log
    - gh issue list failure -> per-project warning + finding noting the failure
    - TODO file read error -> silently skipped
Related reflections:
    - principal-staleness: sibling task-management reflection
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
import subprocess

from config.settings import settings
from reflections.utilities import PROJECT_ROOT, load_local_projects

logger = logging.getLogger("reflections.task_management")


def run() -> dict:
    """Clean up task management: check open bugs per project, local TODOs."""
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
                timeout=settings.timeouts.git_subprocess_s,
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
