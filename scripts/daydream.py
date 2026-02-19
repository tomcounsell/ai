#!/usr/bin/env python3
"""
Daydream - Autonomous Daily Maintenance System

A long-running process that performs daily maintenance tasks:
1. Clean up legacy code
2. Review previous day's logs (per-project, with structured error extraction)
3. Check error logs via Sentry (skips gracefully if MCP unavailable)
4. Clean up task management (per-project, via gh CLI)
5. Update documentation
6. Session analysis (thrash ratio, user corrections)
7. LLM reflection (Claude Haiku categorization)
8. Auto-fix high-confidence code bugs via plan-build-PR
9. Memory consolidation (lessons_learned.jsonl)
10. Produce daily report (local markdown)
11. GitHub issue creation (per-project, via daydream_report module)

This process is resumable - if interrupted, it picks up where it left off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path for standalone execution
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from scripts.daydream_report import create_daydream_issue  # noqa: E402
from scripts.docs_auditor import DocsAuditor  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("daydream")

# Paths — resolved at import time so they stay absolute even if cwd changes
PROJECT_ROOT = Path(__file__).parent.parent
AI_ROOT = PROJECT_ROOT  # Preserved alias; do not reassign in production code
LOGS_DIR = PROJECT_ROOT / "logs"
DAYDREAM_DIR = LOGS_DIR / "daydream"
STATE_FILE = DAYDREAM_DIR / "state.json"
DATA_DIR = PROJECT_ROOT / "data"
LESSONS_FILE = DATA_DIR / "lessons_learned.jsonl"
SESSIONS_DIR = LOGS_DIR / "sessions"
IGNORE_LOG_FILE = DATA_DIR / "daydream_ignore.jsonl"


def load_ignore_log() -> list[dict]:
    """Load active (non-expired) ignore log entries."""
    if not IGNORE_LOG_FILE.exists():
        return []
    today = datetime.now().date().isoformat()
    entries = []
    for line in IGNORE_LOG_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("ignored_until", "") >= today:
                entries.append(entry)
        except json.JSONDecodeError:
            pass
    return entries


def prune_ignore_log() -> None:
    """Remove expired entries from the ignore log."""
    if not IGNORE_LOG_FILE.exists():
        return
    today = datetime.now().date().isoformat()
    active = []
    for line in IGNORE_LOG_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("ignored_until", "") >= today:
                active.append(json.dumps(entry))
        except json.JSONDecodeError:
            pass
    IGNORE_LOG_FILE.write_text("\n".join(active) + ("\n" if active else ""))


def is_ignored(pattern: str, ignore_entries: list[dict]) -> bool:
    """Check if a pattern matches any active ignore entry."""
    pattern_lower = pattern.lower()
    for entry in ignore_entries:
        entry_pattern = entry.get("pattern", "").lower()
        if entry_pattern and (
            entry_pattern in pattern_lower or pattern_lower in entry_pattern
        ):
            return True
    return False


def is_high_confidence(reflection: dict) -> bool:
    """Check if a reflection meets the 2-of-3 confidence criteria for auto-fix."""
    criteria = [
        reflection.get("category") == "code_bug",
        bool(reflection.get("prevention", "").strip()),
        len(reflection.get("pattern", "")) >= 10,
    ]
    return sum(criteria) >= 2


def has_existing_github_work(pattern: str, cwd: str) -> bool:
    """Check if there's already an open issue or PR for this bug pattern."""
    search_term = pattern[:50]  # Truncate for search query
    for cmd in [
        ["gh", "issue", "list", "--state", "open", "--search", search_term],
        ["gh", "pr", "list", "--state", "open", "--search", search_term],
    ]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, cwd=cwd
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
    return False


# Correction patterns in user messages
CORRECTION_PATTERNS = [
    re.compile(r"\bno,?\s+i\s+meant\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+wrong\b", re.IGNORECASE),
    re.compile(r"\bactually,?\s+", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+i\s+(asked|wanted|meant)\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+(file|dir|path|approach)\b", re.IGNORECASE),
    re.compile(r"\bstop\b.*\binstead\b", re.IGNORECASE),
    re.compile(r"\bi\s+said\b", re.IGNORECASE),
    re.compile(r"\bplease\s+(don'?t|stop)\b", re.IGNORECASE),
]

# Thrash ratio threshold: above this is considered thrashing
THRASH_RATIO_THRESHOLD = 0.5


def load_local_projects() -> list[dict]:
    """Load projects from config/projects.json, filtered to those present on this machine.

    Returns:
        List of project dicts, each including a 'slug' key derived from the
        projects.json key. Only projects whose working_directory exists on disk
        are returned.
    """
    config_path = AI_ROOT / "config" / "projects.json"
    data = json.loads(config_path.read_text())
    projects = []
    for slug, cfg in data.get("projects", {}).items():
        wd = Path(cfg.get("working_directory", ""))
        if wd.exists():
            projects.append({"slug": slug, **cfg})
    return projects


def analyze_sessions(sessions_dir: Path, target_date: str) -> dict[str, Any]:
    """Analyze session snapshots for a given date.

    Reads chat.json and tool_use.jsonl from session directories,
    filters to the target date, and extracts:
    - User corrections (patterns like "No, I meant...")
    - Thrash ratio (tool calls / successful outcomes)

    Args:
        sessions_dir: Path to logs/sessions/ directory.
        target_date: Date string (YYYY-MM-DD) to filter sessions.

    Returns:
        Dict with sessions_analyzed, corrections, thrash_sessions.
    """
    result: dict[str, Any] = {
        "sessions_analyzed": 0,
        "corrections": [],
        "thrash_sessions": [],
    }

    if not sessions_dir.exists():
        return result

    # Collect all sessions for the target date
    matching_sessions = []
    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir():
            continue
        chat_file = session_dir / "chat.json"
        if not chat_file.exists():
            continue
        try:
            chat_data = json.loads(chat_file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Skipping malformed session: {session_dir.name}")
            continue

        started_at = chat_data.get("started_at", "")
        if not started_at.startswith(target_date):
            continue

        matching_sessions.append((session_dir, chat_data))

    # Cap at 10 most interesting (sort by message count descending for now)
    matching_sessions.sort(key=lambda x: len(x[1].get("messages", [])), reverse=True)
    matching_sessions = matching_sessions[:10]

    for session_dir, chat_data in matching_sessions:
        result["sessions_analyzed"] += 1
        session_id = chat_data.get("session_id", session_dir.name)
        messages = chat_data.get("messages", [])

        # Detect user corrections
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            for pattern in CORRECTION_PATTERNS:
                if pattern.search(content):
                    result["corrections"].append(
                        {
                            "session_id": session_id,
                            "message": content,
                            "pattern": pattern.pattern,
                        }
                    )
                    break  # One match per message is enough

        # Compute thrash ratio from tool_use.jsonl
        tool_file = session_dir / "tool_use.jsonl"
        if tool_file.exists():
            try:
                tool_calls = 0
                successes = 0
                for line in tool_file.read_text().strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        tool_calls += 1
                        if entry.get("success", False):
                            successes += 1
                    except json.JSONDecodeError:
                        continue

                if tool_calls > 0:
                    failure_ratio = 1.0 - (successes / tool_calls)
                    if failure_ratio > THRASH_RATIO_THRESHOLD:
                        result["thrash_sessions"].append(
                            {
                                "session_id": session_id,
                                "tool_calls": tool_calls,
                                "successes": successes,
                                "failure_ratio": round(failure_ratio, 2),
                            }
                        )
            except OSError:
                pass

    return result


def run_llm_reflection(
    analysis: dict[str, Any],
) -> list[dict[str, str]]:
    """Run LLM reflection on session analysis using Claude Haiku.

    Args:
        analysis: Output from analyze_sessions().

    Returns:
        List of reflection dicts with category, summary, pattern,
        prevention, source_session. Empty list on failure or skip.
    """
    # Skip if nothing interesting
    if (
        analysis.get("sessions_analyzed", 0) == 0
        and not analysis.get("corrections")
        and not analysis.get("thrash_sessions")
    ):
        logger.info("No session findings for reflection, skipping LLM call")
        return []

    # Skip if no API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("No ANTHROPIC_API_KEY set, skipping LLM reflection")
        return []

    if anthropic is None:
        logger.info("anthropic package not installed, skipping LLM reflection")
        return []

    prompt = f"""Analyze these session findings and categorize any mistakes or issues.

Session Analysis Data:
{json.dumps(analysis, indent=2)}

For each issue found, return a JSON array of objects with these fields:
- category: one of (misunderstanding, code_bug, poor_planning,
  tool_misuse, scope_creep, integration_failure)
- summary: brief description of what went wrong
- pattern: the recurring pattern that caused the issue
- prevention: specific rule to prevent this in the future
- source_session: the session_id where this was observed

Return ONLY the JSON array, no other text. If no issues found, return [].
"""

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-20250414",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text.strip()
        # Try to extract JSON from the response
        try:
            reflections = json.loads(response_text)
            if isinstance(reflections, list):
                return reflections
        except json.JSONDecodeError:
            # Try to find JSON array in the response
            match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if match:
                try:
                    reflections = json.loads(match.group())
                    if isinstance(reflections, list):
                        return reflections
                except json.JSONDecodeError:
                    pass
            logger.warning("LLM response was not valid JSON")
            return []
    except Exception as e:
        logger.error(f"LLM reflection failed: {e}")
        return []


def consolidate_memory(
    reflections: list[dict[str, str]],
    date: str,
    lessons_file: Path | None = None,
) -> None:
    """Append reflection output to lessons_learned.jsonl.

    Deduplicates by pattern similarity and prunes entries older than 90 days.

    Args:
        reflections: List of reflection dicts from run_llm_reflection().
        date: Date string (YYYY-MM-DD) for new entries.
        lessons_file: Path to lessons_learned.jsonl (defaults to DATA_DIR).
    """
    if lessons_file is None:
        lessons_file = LESSONS_FILE

    # Ensure parent directory exists
    lessons_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing entries
    existing_entries: list[dict[str, Any]] = []
    if lessons_file.exists():
        for line in lessons_file.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                existing_entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Prune entries older than 90 days
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    existing_entries = [e for e in existing_entries if e.get("date", "") >= cutoff]

    # Collect existing patterns for deduplication
    existing_patterns = {e.get("pattern", "") for e in existing_entries}

    # Add new entries (deduplicate by exact pattern match)
    for reflection in reflections:
        pattern = reflection.get("pattern", "")
        if pattern and pattern in existing_patterns:
            logger.info(f"Skipping duplicate pattern: {pattern}")
            continue

        entry = {
            "date": date,
            "category": reflection.get("category", "unknown"),
            "summary": reflection.get("summary", ""),
            "pattern": pattern,
            "prevention": reflection.get("prevention", ""),
            "source_session": reflection.get("source_session", ""),
            "validated": 0,
        }
        existing_entries.append(entry)
        existing_patterns.add(pattern)

    # Write back
    content = "\n".join(json.dumps(e) for e in existing_entries)
    if content:
        content += "\n"
    lessons_file.write_text(content)


def extract_structured_errors(log_file: Path) -> list[dict[str, str]]:
    """Extract structured error information from a log file.

    Args:
        log_file: Path to a log file (e.g., bridge.log).

    Returns:
        List of dicts with timestamp, level, message, and context.
    """
    errors: list[dict[str, str]] = []
    # Pattern: 2026-02-16 10:30:45,123 - module - ERROR - message
    log_pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})" r".*?(ERROR|CRITICAL)\s*[-:]\s*(.*)"
    )

    try:
        with open(log_file) as f:
            lines = f.readlines()[-1000:]

        for i, line in enumerate(lines):
            match = log_pattern.search(line)
            if match:
                timestamp, level, message = match.groups()
                # Grab some context (next 2 lines)
                context_lines = []
                for j in range(i + 1, min(i + 3, len(lines))):
                    stripped = lines[j].strip()
                    if stripped and not log_pattern.search(lines[j]):
                        context_lines.append(stripped)

                errors.append(
                    {
                        "timestamp": timestamp,
                        "level": level,
                        "message": message.strip(),
                        "context": " | ".join(context_lines),
                    }
                )
    except Exception as e:
        logger.warning(f"Could not extract errors from {log_file}: {e}")

    return errors


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
    session_analysis: dict[str, Any] = field(default_factory=dict)
    reflections: list[dict[str, str]] = field(default_factory=list)
    auto_fix_attempts: list[dict] = field(default_factory=list)

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

    def __init__(self) -> None:
        self.state = DaydreamState.load()
        self.projects = load_local_projects()
        self.steps = [
            (1, "Clean Up Legacy Code", self.step_clean_legacy),
            (2, "Review Previous Day's Logs", self.step_review_logs),
            (3, "Check Error Logs (Sentry)", self.step_check_sentry),
            (4, "Clean Up Task Management", self.step_clean_tasks),
            (5, "Audit Documentation", self.step_audit_docs),
            (6, "Session Analysis", self.step_session_analysis),
            (7, "LLM Reflection", self.step_llm_reflection),
            (8, "Auto-Fix Bugs", self.step_auto_fix_bugs),
            (9, "Memory Consolidation", self.step_memory_consolidation),
            (10, "Produce Daily Report", self.step_produce_report),
            (11, "GitHub Issue Creation", self.step_create_github_issue),
        ]

    async def run(self) -> None:
        """Run all daydream steps."""
        logger.info(f"Starting Daydream for {self.state.date}")
        logger.info(f"Resuming from step {self.state.current_step}")

        DAYDREAM_DIR.mkdir(parents=True, exist_ok=True)

        for step_num, step_name, step_func in self.steps:
            if step_num in self.state.completed_steps:
                logger.info(
                    f"Step {step_num} ({step_name}) already completed, skipping"
                )
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
                continue

        logger.info("Daydream completed successfully")

    async def step_clean_legacy(self) -> None:
        """Step 1: Clean up legacy code (ai-repo specific)."""
        findings = []

        # Look for TODO comments
        try:
            result = subprocess.run(
                ["grep", "-r", "TODO:", "--include=*.py", str(PROJECT_ROOT)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            todo_count = (
                len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
            )
            if todo_count > 0:
                findings.append(f"Found {todo_count} TODO comments to review")
        except Exception:
            pass

        # Check for deprecated imports
        deprecated_patterns = [
            "from typing import Optional",
            "from typing import List",
            "from typing import Dict",
        ]
        for pattern in deprecated_patterns:
            try:
                result = subprocess.run(
                    [
                        "grep",
                        "-r",
                        pattern,
                        "--include=*.py",
                        str(PROJECT_ROOT),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.stdout.strip():
                    count = len(result.stdout.strip().split("\n"))
                    findings.append(
                        f"Found {count} instances of deprecated typing "
                        f"import: {pattern}"
                    )
            except Exception:
                pass

        for finding in findings:
            self.state.add_finding("legacy_code", finding)

        self.state.step_progress["clean_legacy"] = {
            "findings": len(findings),
        }

    async def step_review_logs(self) -> None:
        """Step 2: Review previous day's logs per project with structured error extraction.

        Iterates over each local project from config/projects.json and reviews
        log files in <project>/logs/. Findings are namespaced as
        '{slug}:log_review' to distinguish per-project results.
        """
        total_files_analyzed = 0
        total_findings = 0

        for project in self.projects:
            slug = project["slug"]
            project_dir = Path(project["working_directory"])
            logs_dir = project_dir / "logs"

            if not logs_dir.exists():
                logger.info(f"No logs directory found for {slug}, skipping")
                continue

            log_files = list(logs_dir.glob("*.log"))
            findings = []

            for log_file in log_files:
                if not log_file.is_file():
                    continue

                try:
                    mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    if mtime < datetime.now() - timedelta(days=7):
                        findings.append(
                            f"Log file {log_file.name} is older than 7 days"
                        )

                    size_mb = log_file.stat().st_size / (1024 * 1024)
                    if size_mb > 10:
                        findings.append(
                            f"Log file {log_file.name} is {size_mb:.1f}MB "
                            f"- consider rotation"
                        )

                    # Extract structured errors
                    errors = extract_structured_errors(log_file)
                    if errors:
                        findings.append(
                            f"{log_file.name}: {len(errors)} structured errors "
                            f"extracted"
                        )
                        # Include up to 5 most recent errors in findings
                        for error in errors[-5:]:
                            msg = error["message"][:200]
                            findings.append(
                                f"  [{error['level']}] {error['timestamp']}: {msg}"
                            )

                    # Also count warnings
                    with open(log_file) as f:
                        lines = f.readlines()[-1000:]
                    warning_count = sum(1 for line in lines if "WARNING" in line)
                    if warning_count > 10:
                        findings.append(
                            f"{log_file.name}: {warning_count} warnings "
                            f"in recent logs"
                        )

                except Exception as e:
                    findings.append(f"Could not analyze {log_file.name}: {str(e)}")

            for finding in findings:
                self.state.add_finding(f"{slug}:log_review", finding)

            total_files_analyzed += len(log_files)
            total_findings += len(findings)

        self.state.step_progress["review_logs"] = {
            "files_analyzed": total_files_analyzed,
            "findings": total_findings,
        }

    async def step_check_sentry(self) -> None:
        """Step 3: Check error logs via Sentry.

        Sentry integration requires MCP server which is not available
        in standalone script mode. Skips gracefully with a log message.
        """
        self.state.add_finding(
            "sentry",
            "Sentry check skipped - MCP not available in standalone mode",
        )
        logger.info("Sentry check skipped - MCP not available in standalone mode")
        self.state.step_progress["check_sentry"] = {"skipped": True}

    async def step_clean_tasks(self) -> None:
        """Step 4: Clean up task management per project via gh CLI.

        For each local project with a github config, runs gh issue list to
        identify open bugs. Findings are namespaced as '{slug}:tasks'.
        Projects without github config are skipped.
        """
        total_findings = 0

        for project in self.projects:
            slug = project["slug"]

            # Skip projects without github config
            if not project.get("github") or not project["github"].get("org"):
                logger.info(f"No github config for {slug}, skipping task check")
                continue

            project_wd = project["working_directory"]
            findings = []

            try:
                result = subprocess.run(
                    [
                        "gh",
                        "issue",
                        "list",
                        "--state",
                        "open",
                        "--label",
                        "bug",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=project_wd,
                )
                if result.returncode == 0 and result.stdout.strip():
                    bug_lines = result.stdout.strip().split("\n")
                    findings.append(f"Found {len(bug_lines)} open bug issues on GitHub")
                    for line in bug_lines[:5]:
                        findings.append(f"  Bug: {line.strip()}")
                elif result.returncode == 0:
                    findings.append("No open bug issues on GitHub")
            except Exception as e:
                logger.warning(f"Could not check GitHub issues for {slug}: {e}")
                findings.append(f"GitHub issue check failed: {e}")

            for finding in findings:
                self.state.add_finding(f"{slug}:tasks", finding)

            total_findings += len(findings)

        # Also check local todo files in AI_ROOT
        todo_files = list(PROJECT_ROOT.glob("**/TODO.md")) + list(
            PROJECT_ROOT.glob("**/todo.md")
        )
        for todo_file in todo_files:
            try:
                content = todo_file.read_text()
                unchecked = content.count("[ ]")
                if unchecked > 0:
                    self.state.add_finding(
                        "tasks",
                        f"{todo_file.relative_to(PROJECT_ROOT)}: "
                        f"{unchecked} unchecked items",
                    )
                    total_findings += 1
            except Exception:
                pass

        self.state.step_progress["clean_tasks"] = {
            "todo_files": len(todo_files),
            "findings": total_findings,
        }

    async def step_audit_docs(self) -> None:
        """Step 5: Audit documentation against codebase (replaces naive timestamp check)."""
        auditor = DocsAuditor(repo_root=PROJECT_ROOT, dry_run=False)
        summary = await asyncio.to_thread(auditor.run)

        # Record findings
        if summary.skipped:
            self.state.add_finding(
                "documentation", f"Docs audit skipped: {summary.skip_reason}"
            )
        else:
            if len(summary.updated) > 0:
                self.state.add_finding(
                    "documentation",
                    f"Updated {len(summary.updated)} docs with corrections",
                )
            if len(summary.deleted) > 0:
                self.state.add_finding(
                    "documentation",
                    f"Deleted {len(summary.deleted)} stale/inaccurate docs",
                )
            if (
                len(summary.kept) > 0
                and len(summary.updated) == 0
                and len(summary.deleted) == 0
            ):
                self.state.add_finding(
                    "documentation", f"All {len(summary.kept)} docs verified accurate"
                )

        self.state.step_progress["audit_docs"] = {
            "kept": len(summary.kept),
            "updated": len(summary.updated),
            "deleted": len(summary.deleted),
            "renamed": len(summary.renamed),
            "relocated": len(summary.relocated),
            "skipped": summary.skipped,
        }

    async def step_produce_report(self) -> None:
        """Step 10: Produce daily report to local markdown file."""
        total_steps = len(self.steps)
        report_lines = [
            f"# Daydream Report - {self.state.date}",
            "",
            "## Summary",
            f"- Steps completed: {len(self.state.completed_steps)}/{total_steps}",
            f"- Started: {self.state.step_started_at or 'N/A'}",
            "",
        ]

        # Add findings by category
        for category, cat_findings in self.state.findings.items():
            if cat_findings:
                report_lines.append(f"## {category.replace('_', ' ').title()}")
                for finding in cat_findings:
                    report_lines.append(f"- {finding}")
                report_lines.append("")

        # Add session analysis if available
        if self.state.session_analysis:
            report_lines.append("## Session Analysis")
            sa = self.state.session_analysis
            report_lines.append(
                f"- Sessions analyzed: {sa.get('sessions_analyzed', 0)}"
            )
            corrections = sa.get("corrections", [])
            if corrections:
                report_lines.append(f"- User corrections detected: {len(corrections)}")
                for c in corrections[:5]:
                    report_lines.append(
                        f"  - [{c.get('session_id', '?')}] "
                        f"{c.get('message', '')[:100]}"
                    )
            thrash = sa.get("thrash_sessions", [])
            if thrash:
                report_lines.append(f"- Thrashing sessions: {len(thrash)}")
                for t in thrash:
                    report_lines.append(
                        f"  - [{t.get('session_id', '?')}] "
                        f"{t.get('tool_calls', 0)} calls, "
                        f"{t.get('successes', 0)} successes"
                    )
            report_lines.append("")

        # Add reflections if available
        if self.state.reflections:
            report_lines.append("## LLM Reflections")
            for r in self.state.reflections:
                report_lines.append(
                    f"- **{r.get('category', 'unknown')}**: " f"{r.get('summary', '')}"
                )
                report_lines.append(f"  - Pattern: {r.get('pattern', '')}")
                report_lines.append(f"  - Prevention: {r.get('prevention', '')}")
            report_lines.append("")

        # Add progress details
        report_lines.append("## Step Progress")
        for key, value in self.state.step_progress.items():
            report_lines.append(
                f"- **{key.replace('_', ' ').title()}**: {json.dumps(value)}"
            )

        report_lines.append("")
        report_lines.append("---")
        report_lines.append(f"*Generated at {datetime.now().isoformat()}*")

        # Write report
        report_content = "\n".join(report_lines)
        DAYDREAM_DIR.mkdir(parents=True, exist_ok=True)
        report_file = DAYDREAM_DIR / f"report_{self.state.date}.md"
        report_file.write_text(report_content)

        logger.info(f"Report written to {report_file}")

        print("\n" + "=" * 60)
        print(report_content)
        print("=" * 60)

    async def step_session_analysis(self) -> None:
        """Step 6: Analyze yesterday's sessions (ai-repo specific)."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        analysis = analyze_sessions(SESSIONS_DIR, yesterday)
        self.state.session_analysis = analysis

        # Add findings
        if analysis["corrections"]:
            self.state.add_finding(
                "session_analysis",
                f"Detected {len(analysis['corrections'])} user corrections "
                f"across {analysis['sessions_analyzed']} sessions",
            )
        if analysis["thrash_sessions"]:
            self.state.add_finding(
                "session_analysis",
                f"Detected {len(analysis['thrash_sessions'])} thrashing "
                f"sessions (high failure ratio)",
            )

        self.state.step_progress["session_analysis"] = {
            "sessions_analyzed": analysis["sessions_analyzed"],
            "corrections": len(analysis["corrections"]),
            "thrash_sessions": len(analysis["thrash_sessions"]),
        }

    async def step_llm_reflection(self) -> None:
        """Step 7: Run LLM reflection on session analysis."""
        reflections = run_llm_reflection(self.state.session_analysis)
        self.state.reflections = reflections

        if reflections:
            self.state.add_finding(
                "llm_reflection",
                f"Generated {len(reflections)} reflection entries",
            )
            for r in reflections:
                self.state.add_finding(
                    "llm_reflection",
                    f"[{r.get('category', '?')}] {r.get('summary', '')}",
                )

        self.state.step_progress["llm_reflection"] = {
            "reflections_generated": len(reflections),
        }

    async def step_auto_fix_bugs(self) -> None:
        """Step 8: Auto-fix high-confidence code bugs via plan-build-PR."""
        enabled = os.environ.get("DAYDREAM_AUTO_FIX_ENABLED", "true").lower()
        if enabled not in ("true", "1", "yes"):
            logger.info("DAYDREAM_AUTO_FIX_ENABLED is false, skipping auto-fix step")
            self.state.step_progress["auto_fix_bugs"] = {
                "skipped": True,
                "reason": "disabled",
            }
            return

        dry_run = getattr(self.state, "_dry_run", False)

        prune_ignore_log()
        ignore_entries = load_ignore_log()

        reflections = self.state.reflections
        candidates = [r for r in reflections if is_high_confidence(r)]

        logger.info(
            f"Auto-fix: {len(candidates)} candidate(s) from {len(reflections)} reflection(s)"
        )

        attempts = []
        for r in candidates:
            pattern = r.get("pattern", "")
            summary = r.get("summary", "")
            prevention = r.get("prevention", "")

            if is_ignored(pattern, ignore_entries):
                logger.info(f"Auto-fix: skipping ignored pattern: {pattern[:60]}")
                attempts.append({"pattern": pattern, "status": "ignored"})
                self.state.add_finding(
                    "auto_fix", f"Ignored (in ignore log): {summary[:80]}"
                )
                continue

            # Check for existing GitHub work (use first project with github config)
            project_wd = None
            for project in self.projects:
                if project.get("github"):
                    project_wd = project["working_directory"]
                    break

            if project_wd and has_existing_github_work(pattern, project_wd):
                logger.info(f"Auto-fix: duplicate found for pattern: {pattern[:60]}")
                attempts.append({"pattern": pattern, "status": "duplicate"})
                self.state.add_finding(
                    "auto_fix", f"Skipped (existing PR/issue): {summary[:80]}"
                )
                continue

            if dry_run:
                logger.info(f"Auto-fix: [DRY RUN] would trigger for: {summary[:80]}")
                attempts.append({"pattern": pattern, "status": "dry_run"})
                self.state.add_finding(
                    "auto_fix", f"[DRY RUN] Would auto-fix: {summary[:80]}"
                )
                continue

            # Trigger auto-fix
            prompt = (
                f"Fix the following bug in the codebase. "
                f"Use /do-plan to create a plan doc, then /do-build to implement it as a PR.\n\n"
                f"Bug summary: {summary}\n"
                f"Pattern: {pattern}\n"
                f"Prevention: {prevention}"
            )
            logger.info(f"Auto-fix: triggering for: {summary[:80]}")

            try:
                result = subprocess.run(
                    ["claude", "--print", "--dangerously-skip-permissions", prompt],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=str(PROJECT_ROOT),
                )
                status = "success" if result.returncode == 0 else "failed"
                output_snippet = (result.stdout or result.stderr or "")[:200]
                attempts.append(
                    {"pattern": pattern, "status": status, "output": output_snippet}
                )

                if status == "success":
                    self.state.add_finding(
                        "auto_fix", f"Auto-fix triggered: {summary[:80]}"
                    )
                    # Try to extract PR URL from output and post to Telegram
                    pr_match = re.search(
                        r"https://github\.com/\S+/pull/\d+", result.stdout
                    )
                    if pr_match and self.projects:
                        for project in self.projects:
                            if project.get("github"):
                                await self.step_post_to_telegram(
                                    project, pr_match.group()
                                )
                                break
                else:
                    logger.warning(f"Auto-fix subprocess failed: {output_snippet}")
                    self.state.add_finding(
                        "auto_fix", f"Auto-fix failed: {summary[:80]}"
                    )
            except subprocess.TimeoutExpired:
                logger.warning(f"Auto-fix timed out for: {summary[:80]}")
                attempts.append({"pattern": pattern, "status": "timeout"})
                self.state.add_finding(
                    "auto_fix", f"Auto-fix timed out: {summary[:80]}"
                )
            except Exception as e:
                logger.warning(f"Auto-fix error: {e}")
                attempts.append(
                    {"pattern": pattern, "status": "error", "error": str(e)}
                )

        self.state.auto_fix_attempts = attempts
        self.state.step_progress["auto_fix_bugs"] = {
            "candidates": len(candidates),
            "attempts": len(attempts),
            "dry_run": dry_run,
        }

    async def step_memory_consolidation(self) -> None:
        """Step 9: Consolidate lessons learned (was step 8)."""
        consolidate_memory(self.state.reflections, self.state.date, LESSONS_FILE)

        self.state.step_progress["memory_consolidation"] = {
            "lessons_written": len(self.state.reflections),
        }

    async def step_create_github_issue(self) -> None:
        """Step 11: Create GitHub issues per project with findings.

        For each local project with a github config, filters findings
        namespaced to that project and creates a GitHub issue if findings
        exist. Also calls step_post_to_telegram for each project.
        """
        projects_with_issues = 0

        for project in self.projects:
            slug = project["slug"]

            # Skip projects without github config
            if not project.get("github"):
                logger.info(f"No github config for {slug}, skipping issue creation")
                continue

            # Gather findings for this project (namespaced and generic)
            project_findings: dict[str, list[str]] = {}
            for key, values in self.state.findings.items():
                if key.startswith(f"{slug}:") and values:
                    # Strip the slug prefix for cleaner issue formatting
                    clean_key = key[len(f"{slug}:") :]
                    project_findings[clean_key] = values

            if not project_findings:
                logger.info(f"No findings for {slug}, skipping issue creation")
                continue

            project_wd = project["working_directory"]
            issue_url_or_bool = create_daydream_issue(
                project_findings,
                self.state.date,
                cwd=project_wd,
            )

            issue_url = ""
            if isinstance(issue_url_or_bool, str) and issue_url_or_bool:
                issue_url = issue_url_or_bool
                projects_with_issues += 1
            elif issue_url_or_bool is True:
                projects_with_issues += 1

            await self.step_post_to_telegram(project, issue_url)

        self.state.step_progress["github_issue"] = {
            "created": projects_with_issues > 0,
            "projects_with_issues": projects_with_issues,
        }

    async def step_post_to_telegram(self, project: dict, issue_url: str = "") -> None:
        """Post daydream summary to project's Telegram chat.

        Args:
            project: Project dict from load_local_projects().
            issue_url: Optional GitHub issue URL to include in message.
        """
        groups = project.get("telegram", {}).get("groups", [])
        if not groups:
            logger.info(
                f"No telegram groups configured for {project['slug']}, skipping"
            )
            return

        session_file = AI_ROOT / "data" / "valor.session"
        if not session_file.exists():
            logger.info("No valor.session file found, skipping Telegram post")
            return

        try:
            from telethon import TelegramClient  # type: ignore[import]

            api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
            api_hash = os.environ.get("TELEGRAM_API_HASH", "")

            if not api_id or not api_hash:
                logger.info("No Telegram credentials, skipping post")
                return

            # Build summary message
            slug = project["slug"]
            findings_count = sum(
                len(v)
                for k, v in self.state.findings.items()
                if k.startswith(f"{slug}:")
            )
            msg_lines = [f"Daydream Report — {self.state.date}"]
            msg_lines.append(f"Project: {project.get('name', slug)}")
            if findings_count:
                msg_lines.append(f"Findings: {findings_count} items")
            else:
                msg_lines.append("No significant findings today")
            if issue_url:
                msg_lines.append(f"GitHub: {issue_url}")
            message = "\n".join(msg_lines)

            async with TelegramClient(str(session_file), api_id, api_hash) as client:
                for group_name in groups[:1]:  # only post to first group
                    try:
                        await client.send_message(group_name, message)
                        logger.info(f"Posted daydream summary to {group_name}")
                    except Exception as e:
                        logger.warning(f"Could not post to {group_name}: {e}")
        except ImportError:
            logger.info("telethon not available, skipping Telegram post")
        except Exception as e:
            logger.warning(f"Telegram post failed for {project['slug']}: {e}")


async def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Daydream autonomous maintenance")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would trigger without acting",
    )
    parser.add_argument(
        "--ignore",
        metavar="PATTERN",
        help="Add a pattern to the ignore log for 14 days",
    )
    parser.add_argument(
        "--reason",
        metavar="REASON",
        default="",
        help="Reason for ignoring (used with --ignore)",
    )
    args = parser.parse_args()

    if args.ignore:
        ignored_until = (datetime.now() + timedelta(days=14)).date().isoformat()
        entry = {
            "pattern": args.ignore,
            "ignored_until": ignored_until,
            "reason": args.reason,
        }
        IGNORE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with IGNORE_LOG_FILE.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"Added ignore entry: {args.ignore!r} (until {ignored_until})")
        return

    runner = DaydreamRunner()
    if args.dry_run:
        runner.state._dry_run = True
        logger.info("DRY RUN mode — no side effects will be triggered")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
