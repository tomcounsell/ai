"""Audit skill SKILL.md files for dangerous hook patterns.

Prevents the Infinite Stop Hook Loop incident (Feb 2026):
- Stop hooks referencing non-existent scripts cause infinite loops
- uv run in hooks can corrupt the venv via dependency resolution
- Failed Stop hooks generate feedback that triggers new responses endlessly

This module runs on every /update to catch regressions.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HookIssue:
    """A single hook issue found during audit."""

    skill: str
    issue_type: str  # "missing_script", "uv_run"
    detail: str


@dataclass
class HookAuditResult:
    """Result of auditing all skill hooks."""

    success: bool = True
    issues: list[HookIssue] = field(default_factory=list)
    skills_scanned: int = 0


def audit_skill_hooks(project_dir: Path) -> HookAuditResult:
    """Audit all SKILL.md files for dangerous hook patterns.

    Scans both project-level and user-level skills directories.
    Deduplicates by inode to avoid double-reporting hardlinked files.
    """
    result = HookAuditResult()

    scan_dirs = [
        project_dir / ".claude" / "skills",
        Path.home() / ".claude" / "skills",
    ]

    seen_inodes: set[int] = set()

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue

        for skill_dir in sorted(scan_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue

            # Deduplicate hardlinked files
            inode = skill_file.stat().st_ino
            if inode in seen_inodes:
                continue
            seen_inodes.add(inode)

            result.skills_scanned += 1
            _audit_single_skill(skill_file, skill_dir.name, project_dir, result)

    return result


def _audit_single_skill(
    skill_file: Path,
    skill_name: str,
    project_dir: Path,
    result: HookAuditResult,
) -> None:
    """Audit a single SKILL.md for dangerous hook patterns."""
    content = skill_file.read_text()

    # Extract YAML frontmatter
    if not content.startswith("---"):
        return

    end = content.find("---", 3)
    if end == -1:
        return

    frontmatter = content[3:end]

    # Only care about files with Stop hooks
    if "Stop:" not in frontmatter:
        return

    # Check for uv run usage (venv corruption risk)
    if "uv run" in frontmatter:
        result.issues.append(
            HookIssue(
                skill=skill_name,
                issue_type="uv_run",
                detail="Hook uses 'uv run' which can corrupt venv or trigger slow dependency resolution",
            )
        )
        result.success = False

    # Check for missing scripts
    # Extract all script paths from command lines
    for match in re.finditer(r"(\$CLAUDE_PROJECT_DIR/\S+\.py)", frontmatter):
        ref = match.group(1)
        resolved = ref.replace("$CLAUDE_PROJECT_DIR", str(project_dir))
        script_path = Path(resolved)

        if not script_path.exists():
            result.issues.append(
                HookIssue(
                    skill=skill_name,
                    issue_type="missing_script",
                    detail=f"Hook references non-existent script: {ref}",
                )
            )
            result.success = False
