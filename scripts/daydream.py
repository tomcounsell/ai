#!/usr/bin/env python3
"""
Daydream - Autonomous Daily Maintenance System

A long-running process that performs daily maintenance tasks:
1. Clean up legacy code
2. Review previous day's logs
3. Check error logs via Sentry
4. Clean up task management
5. Update documentation
6. Produce daily report

This process is resumable - if interrupted, it picks up where it left off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("daydream")

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
DAYDREAM_DIR = LOGS_DIR / "daydream"
STATE_FILE = DAYDREAM_DIR / "state.json"


@dataclass
class DaydreamState:
    """Persisted state for resumability."""
    current_step: int = 1
    step_started_at: str | None = None
    step_progress: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[int] = field(default_factory=list)
    daily_report: list[str] = field(default_factory=list)
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    findings: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> DaydreamState:
        """Load state from file or create new."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                # Reset if it's a new day
                if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
                    logger.info("New day detected, starting fresh")
                    return cls()
                return cls(**data)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        return cls()

    def save(self) -> None:
        """Save state to file."""
        DAYDREAM_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def add_finding(self, category: str, finding: str) -> None:
        """Add a finding to the report."""
        if category not in self.findings:
            self.findings[category] = []
        self.findings[category].append(finding)


class DaydreamRunner:
    """Runs the daydream maintenance process."""

    def __init__(self):
        self.state = DaydreamState.load()
        self.steps = [
            (1, "Clean Up Legacy Code", self.step_clean_legacy),
            (2, "Review Previous Day's Logs", self.step_review_logs),
            (3, "Check Error Logs (Sentry)", self.step_check_sentry),
            (4, "Clean Up Task Management", self.step_clean_tasks),
            (5, "Update Documentation", self.step_update_docs),
            (6, "Produce Daily Report", self.step_produce_report),
        ]

    async def run(self) -> None:
        """Run all daydream steps."""
        logger.info(f"Starting Daydream for {self.state.date}")
        logger.info(f"Resuming from step {self.state.current_step}")

        for step_num, step_name, step_func in self.steps:
            if step_num in self.state.completed_steps:
                logger.info(f"Step {step_num} ({step_name}) already completed, skipping")
                continue

            if step_num < self.state.current_step:
                continue

            logger.info(f"Starting step {step_num}: {step_name}")
            self.state.current_step = step_num
            self.state.step_started_at = datetime.now().isoformat()
            self.state.save()

            try:
                await step_func()
                self.state.completed_steps.append(step_num)
                self.state.daily_report.append(f"Completed: {step_name}")
                self.state.save()
                logger.info(f"Completed step {step_num}: {step_name}")
            except Exception as e:
                logger.error(f"Step {step_num} failed: {e}")
                self.state.daily_report.append(f"Failed: {step_name} - {str(e)}")
                self.state.save()
                raise

        logger.info("Daydream completed successfully")

    async def step_clean_legacy(self) -> None:
        """Step 1: Clean up legacy code."""
        findings = []

        # Clean __pycache__ directories
        cache_dirs = list(PROJECT_ROOT.rglob("__pycache__"))
        for cache_dir in cache_dirs:
            if cache_dir.is_dir():
                # Count files
                file_count = len(list(cache_dir.glob("*")))
                if file_count > 0:
                    findings.append(f"Found {file_count} cached files in {cache_dir.relative_to(PROJECT_ROOT)}")

        # Clean .pyc files
        pyc_files = list(PROJECT_ROOT.rglob("*.pyc"))
        if pyc_files:
            findings.append(f"Found {len(pyc_files)} .pyc files")

        # Look for TODO comments that might indicate legacy code
        try:
            result = subprocess.run(
                ["grep", "-r", "TODO:", "--include=*.py", str(PROJECT_ROOT)],
                capture_output=True,
                text=True,
                timeout=30
            )
            todo_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
            if todo_count > 0:
                findings.append(f"Found {todo_count} TODO comments to review")
        except Exception:
            pass

        # Check for deprecated imports
        deprecated_patterns = ["from typing import Optional", "from typing import List", "from typing import Dict"]
        for pattern in deprecated_patterns:
            try:
                result = subprocess.run(
                    ["grep", "-r", pattern, "--include=*.py", str(PROJECT_ROOT)],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.stdout.strip():
                    count = len(result.stdout.strip().split("\n"))
                    findings.append(f"Found {count} instances of deprecated typing import: {pattern}")
            except Exception:
                pass

        for finding in findings:
            self.state.add_finding("legacy_code", finding)

        self.state.step_progress["clean_legacy"] = {
            "cache_dirs": len(cache_dirs),
            "pyc_files": len(pyc_files),
            "findings": len(findings),
        }

    async def step_review_logs(self) -> None:
        """Step 2: Review previous day's logs."""
        findings = []
        log_files = list(LOGS_DIR.glob("*.log"))

        for log_file in log_files:
            if not log_file.is_file():
                continue

            try:
                # Check file age
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < datetime.now() - timedelta(days=7):
                    findings.append(f"Log file {log_file.name} is older than 7 days")

                # Check file size
                size_mb = log_file.stat().st_size / (1024 * 1024)
                if size_mb > 10:
                    findings.append(f"Log file {log_file.name} is {size_mb:.1f}MB - consider rotation")

                # Look for errors in recent entries
                with open(log_file) as f:
                    lines = f.readlines()[-1000:]  # Last 1000 lines

                error_count = sum(1 for line in lines if "ERROR" in line or "CRITICAL" in line)
                warning_count = sum(1 for line in lines if "WARNING" in line)

                if error_count > 0:
                    findings.append(f"{log_file.name}: {error_count} errors in recent logs")
                if warning_count > 10:
                    findings.append(f"{log_file.name}: {warning_count} warnings in recent logs")

            except Exception as e:
                findings.append(f"Could not analyze {log_file.name}: {str(e)}")

        for finding in findings:
            self.state.add_finding("log_review", finding)

        self.state.step_progress["review_logs"] = {
            "files_analyzed": len(log_files),
            "findings": len(findings),
        }

    async def step_check_sentry(self) -> None:
        """Step 3: Check error logs via Sentry."""
        findings = []

        # Try to call clawdbot sentry skill
        try:
            result = subprocess.run(
                ["clawdbot", "skill", "sentry", "list_issues", "--status=unresolved", "--limit=10"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                findings.append(f"Sentry issues found: {result.stdout.strip()[:500]}")
            elif result.returncode != 0:
                findings.append("Could not query Sentry - check clawdbot configuration")
        except FileNotFoundError:
            findings.append("Clawdbot not available - skipping Sentry check")
        except subprocess.TimeoutExpired:
            findings.append("Sentry query timed out")
        except Exception as e:
            findings.append(f"Sentry check failed: {str(e)}")

        for finding in findings:
            self.state.add_finding("sentry", finding)

        self.state.step_progress["check_sentry"] = {
            "findings": len(findings),
        }

    async def step_clean_tasks(self) -> None:
        """Step 4: Clean up task management."""
        findings = []

        # Try to call clawdbot linear skill
        try:
            result = subprocess.run(
                ["clawdbot", "skill", "linear", "list_issues", "--status=backlog", "--limit=20"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                findings.append(f"Linear backlog items: {result.stdout.strip()[:500]}")
        except FileNotFoundError:
            findings.append("Clawdbot not available - skipping Linear check")
        except Exception as e:
            findings.append(f"Linear check failed: {str(e)}")

        # Check local todo files
        todo_files = list(PROJECT_ROOT.glob("**/TODO.md")) + list(PROJECT_ROOT.glob("**/todo.md"))
        for todo_file in todo_files:
            try:
                content = todo_file.read_text()
                unchecked = content.count("[ ]")
                checked = content.count("[x]")
                if unchecked > 0:
                    findings.append(f"{todo_file.relative_to(PROJECT_ROOT)}: {unchecked} unchecked items")
            except Exception:
                pass

        for finding in findings:
            self.state.add_finding("tasks", finding)

        self.state.step_progress["clean_tasks"] = {
            "todo_files": len(todo_files),
            "findings": len(findings),
        }

    async def step_update_docs(self) -> None:
        """Step 5: Update documentation."""
        findings = []

        # Check for outdated documentation
        docs_dir = PROJECT_ROOT / "docs"
        if docs_dir.exists():
            doc_files = list(docs_dir.rglob("*.md"))
            for doc_file in doc_files:
                try:
                    mtime = datetime.fromtimestamp(doc_file.stat().st_mtime)
                    age_days = (datetime.now() - mtime).days
                    if age_days > 30:
                        findings.append(f"{doc_file.relative_to(PROJECT_ROOT)} hasn't been updated in {age_days} days")
                except Exception:
                    pass

        # Check CLAUDE.md is present and recent
        claude_md = PROJECT_ROOT / "CLAUDE.md"
        if claude_md.exists():
            mtime = datetime.fromtimestamp(claude_md.stat().st_mtime)
            age_days = (datetime.now() - mtime).days
            if age_days > 7:
                findings.append(f"CLAUDE.md hasn't been updated in {age_days} days")
        else:
            findings.append("CLAUDE.md not found")

        # Check README.md
        readme = PROJECT_ROOT / "README.md"
        if not readme.exists():
            findings.append("README.md not found")

        for finding in findings:
            self.state.add_finding("documentation", finding)

        self.state.step_progress["update_docs"] = {
            "findings": len(findings),
        }

    async def step_produce_report(self) -> None:
        """Step 6: Produce daily report."""
        report_lines = [
            f"# Daydream Report - {self.state.date}",
            "",
            "## Summary",
            f"- Steps completed: {len(self.state.completed_steps)}/6",
            f"- Started: {self.state.step_started_at or 'N/A'}",
            "",
        ]

        # Add findings by category
        for category, findings in self.state.findings.items():
            if findings:
                report_lines.append(f"## {category.replace('_', ' ').title()}")
                for finding in findings:
                    report_lines.append(f"- {finding}")
                report_lines.append("")

        # Add progress details
        report_lines.append("## Step Progress")
        for key, value in self.state.step_progress.items():
            report_lines.append(f"- **{key.replace('_', ' ').title()}**: {json.dumps(value)}")

        report_lines.append("")
        report_lines.append("---")
        report_lines.append(f"*Generated at {datetime.now().isoformat()}*")

        # Write report
        report_content = "\n".join(report_lines)
        report_file = DAYDREAM_DIR / f"report_{self.state.date}.md"
        report_file.write_text(report_content)

        logger.info(f"Report written to {report_file}")

        # Also print to stdout for visibility
        print("\n" + "=" * 60)
        print(report_content)
        print("=" * 60)


async def main():
    """Main entry point."""
    runner = DaydreamRunner()
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
