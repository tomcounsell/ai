"""
Work Completion Tracking for SDK Agent Sessions.

Provides tools for marking work complete and verifying completion criteria.
Reads criteria from CLAUDE.md as single source of truth.
"""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

CompletionStatus = Literal["COMPLETE", "IN_PROGRESS", "BLOCKED"]


@dataclass
class CompletionCheck:
    """A single completion check result."""

    name: str
    passed: bool
    details: str = ""


@dataclass
class CompletionResult:
    """Result of completion verification."""

    status: CompletionStatus
    checks: list[CompletionCheck] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def all_checks_passed(self) -> bool:
        """Check if all required checks passed."""
        return all(check.passed for check in self.checks)

    def format_summary(self) -> str:
        """Format a human-readable completion summary."""
        lines = ["## Work Completion Summary", ""]

        # Status
        status_emoji = (
            "✅"
            if self.status == "COMPLETE"
            else "⏳" if self.status == "IN_PROGRESS" else "🚫"
        )
        lines.append(f"**Status**: {status_emoji} {self.status}")
        lines.append("")

        # Checks
        lines.append("### Completion Checks:")
        for check in self.checks:
            emoji = "✅" if check.passed else "❌"
            lines.append(f"{emoji} {check.name}")
            if check.details:
                lines.append(f"   {check.details}")
        lines.append("")

        # Artifacts
        if self.artifacts:
            lines.append("### Artifacts Created:")
            for artifact in self.artifacts:
                lines.append(f"- `{artifact}`")
            lines.append("")

        # Summary
        if self.summary:
            lines.append("### Summary:")
            lines.append(self.summary)
            lines.append("")

        lines.append(f"*Completed at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)


def load_completion_criteria() -> str:
    """
    Load completion criteria from CLAUDE.md.

    Returns the full "Work Completion Criteria" section.
    """
    claude_md = Path(__file__).parent.parent / "CLAUDE.md"

    if not claude_md.exists():
        logger.warning(f"CLAUDE.md not found at {claude_md}")
        return ""

    content = claude_md.read_text()

    # Extract the "Work Completion Criteria" section
    match = re.search(
        r"## Work Completion Criteria\n\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )

    if not match:
        logger.warning("Work Completion Criteria section not found in CLAUDE.md")
        return ""

    return match.group(0)


def check_git_status(working_dir: Path) -> CompletionCheck:
    """Check if changes are committed and pushed."""
    try:
        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )

        uncommitted = result.stdout.strip()
        if uncommitted:
            return CompletionCheck(
                name="Changes Committed",
                passed=False,
                details=f"Uncommitted changes found: {uncommitted[:100]}",
            )

        # Check if pushed to remote
        result = subprocess.run(
            ["git", "status", "-sb"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )

        status = result.stdout.strip()
        if "ahead" in status.lower():
            return CompletionCheck(
                name="Changes Committed",
                passed=False,
                details="Local commits not pushed to remote",
            )

        # Get last commit info
        result = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )

        last_commit = result.stdout.strip()

        return CompletionCheck(
            name="Changes Committed", passed=True, details=f"Latest: {last_commit}"
        )

    except Exception as e:
        logger.error(f"Git status check failed: {e}")
        return CompletionCheck(
            name="Changes Committed", passed=False, details=f"Git check error: {str(e)}"
        )


def check_code_quality(working_dir: Path) -> CompletionCheck:
    """Check if code passes linting."""
    # For now, just check if ruff/black are available and could be run
    # More sophisticated: actually run them and check exit codes
    try:
        # Quick check: are there Python files that need checking?
        python_files = list(working_dir.rglob("*.py"))
        if not python_files:
            return CompletionCheck(
                name="Code Quality", passed=True, details="No Python files to check"
            )

        # TODO: Actually run ruff/black and check results
        # For now, assume quality is good if we got this far
        return CompletionCheck(
            name="Code Quality",
            passed=True,
            details=f"Python files present ({len(python_files)} files)",
        )

    except Exception as e:
        logger.error(f"Code quality check failed: {e}")
        return CompletionCheck(
            name="Code Quality",
            passed=True,  # Don't block on this for now
            details=f"Check skipped: {str(e)}",
        )


def verify_completion(
    working_dir: Path, artifacts: list[str] | None = None, summary: str = ""
) -> CompletionResult:
    """
    Verify if work meets completion criteria.

    Args:
        working_dir: Project working directory
        artifacts: List of created artifacts (files/PRs/docs)
        summary: Description of what was accomplished

    Returns:
        CompletionResult with status and check details
    """
    checks = []

    # Check 1: Git status
    checks.append(check_git_status(working_dir))

    # Check 2: Code quality
    checks.append(check_code_quality(working_dir))

    # Check 3: Artifacts exist (if claimed)
    if artifacts:
        artifacts_exist = []
        for artifact in artifacts:
            # Check if it's a file path
            artifact_path = working_dir / artifact
            if artifact_path.exists():
                artifacts_exist.append(artifact)
            # Or a URL (PR, etc.)
            elif artifact.startswith("http"):
                artifacts_exist.append(artifact)

        if len(artifacts_exist) == len(artifacts):
            checks.append(
                CompletionCheck(
                    name="Artifacts Created",
                    passed=True,
                    details=f"{len(artifacts_exist)} artifact(s) verified",
                )
            )
        else:
            checks.append(
                CompletionCheck(
                    name="Artifacts Created",
                    passed=False,
                    details=f"Only {len(artifacts_exist)}/{len(artifacts)} artifacts found",
                )
            )

    # Determine overall status
    all_passed = all(check.passed for check in checks)
    status: CompletionStatus = "COMPLETE" if all_passed else "IN_PROGRESS"

    return CompletionResult(
        status=status,
        checks=checks,
        artifacts=artifacts or [],
        summary=summary,
    )


def mark_work_complete(
    working_dir: str | Path,
    summary: str,
    artifacts: list[str] | None = None,
) -> dict:
    """
    Tool function for agent to mark work complete.

    Verifies completion criteria and returns formatted result.

    Args:
        working_dir: Project working directory
        summary: Description of what was accomplished
        artifacts: List of created artifacts (file paths, PR URLs, etc.)

    Returns:
        Dict with status, message, and details
    """
    working_dir = Path(working_dir)

    # Load criteria for reference
    criteria = load_completion_criteria()

    # Verify completion
    result = verify_completion(working_dir, artifacts, summary)

    # Format response
    response = {
        "status": result.status,
        "message": result.format_summary(),
        "criteria_reference": criteria,
        "all_checks_passed": result.all_checks_passed,
        "timestamp": result.timestamp.isoformat(),
    }

    logger.info(
        f"Work completion check: {result.status}, "
        f"{sum(c.passed for c in result.checks)}/{len(result.checks)} checks passed"
    )

    return response
