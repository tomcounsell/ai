#!/usr/bin/env python3
"""
Reflections - Autonomous Daily Maintenance System

A long-running process that performs daily self-directed maintenance tasks:
1. Clean up legacy code
2. Review previous day's logs (per-project, with structured error extraction)
3. Check error logs via Sentry (skips gracefully if MCP unavailable)
4. Clean up task management (per-project, via gh CLI)
5. Update documentation
6. Session analysis (thrash ratio, user corrections) - Redis-backed via AgentSession
7. LLM reflection (Claude Haiku categorization)
8. File GitHub issues for high-confidence code bugs
9. Produce daily report (local markdown)
10. GitHub issue creation (per-project, via reflections_report module)
11. Skills audit (validate all SKILL.md files against template standards)
12. Redis TTL cleanup (all models including reflections models)
13. Redis data quality checks (unsummarized links, dead channels)
14. Branch and plan cleanup (stale branches, orphaned/completed/duplicate plans)
15. Feature docs audit (stale refs, README.md accuracy, plan-masquerading-as-feature)
16. Episode cycle-close (CyclicEpisode records from completed SDLC sessions)
17. Pattern crystallization (ProceduralPatterns from episode clusters)
18. Principal context staleness (PRINCIPAL.md age check)
19. Disk space check (project volume free space, finding if <10GB)

All persistence is Redis-backed via Popoto models (see models/ directory).
State: ReflectionRun | Ignore patterns: ReflectionIgnore

See docs/features/reflections.md for full documentation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
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

from config.models import HAIKU  # noqa: E402
from scripts.docs_auditor import DocsAuditor  # noqa: E402
from scripts.reflections_report import (  # noqa: E402
    create_reflections_issue,
    reset_dedup_guard,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("reflections")

# Paths — resolved at import time so they stay absolute even if cwd changes
PROJECT_ROOT = Path(__file__).parent.parent
AI_ROOT = PROJECT_ROOT  # Preserved alias; do not reassign in production code
LOGS_DIR = PROJECT_ROOT / "logs"
REFLECTIONS_DIR = LOGS_DIR / "reflections"
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = LOGS_DIR / "sessions"


# --- Redis-backed helpers ---


def load_ignore_log() -> list[dict]:
    """Load active (non-expired) ignore entries from Redis."""
    from models.reflections import ReflectionIgnore

    active = ReflectionIgnore.get_active()
    return [
        {
            "pattern": entry.pattern,
            "ignored_until": (
                datetime.fromtimestamp(entry.expires_at).date().isoformat()
                if entry.expires_at
                else ""
            ),
            "reason": entry.reason or "",
        }
        for entry in active
    ]


def prune_ignore_log() -> None:
    """Remove expired entries from the ignore log via Redis."""
    from models.reflections import ReflectionIgnore

    deleted = ReflectionIgnore.cleanup_expired()
    if deleted:
        logger.info(f"Pruned {deleted} expired ignore entries from Redis")


def is_ignored(pattern: str, ignore_entries: list[dict]) -> bool:
    """Check if a pattern matches any active ignore entry."""
    pattern_lower = pattern.lower()
    for entry in ignore_entries:
        entry_pattern = entry.get("pattern", "").lower()
        if entry_pattern and (entry_pattern in pattern_lower or pattern_lower in entry_pattern):
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=cwd)
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
    """Load projects from projects.json, filtered to those present on this machine.

    Loads from ~/Desktop/Valor/projects.json (iCloud-synced, private).
    Falls back to legacy config/projects.json if the Desktop path doesn't exist.

    Returns:
        List of project dicts, each including a 'slug' key derived from the
        projects.json key. Only projects whose working_directory exists on disk
        are returned.
    """
    config_path = Path(
        os.environ.get(
            "PROJECTS_CONFIG_PATH",
            str(Path.home() / "Desktop" / "Valor" / "projects.json"),
        )
    )
    if not config_path.exists():
        # Legacy fallback
        config_path = AI_ROOT / "config" / "projects.json"
    data = json.loads(config_path.read_text())
    projects = []
    for slug, cfg in data.get("projects", {}).items():
        wd = Path(cfg.get("working_directory", "")).expanduser()
        if wd.exists():
            projects.append({"slug": slug, **cfg, "working_directory": str(wd)})
    return projects


def analyze_sessions_from_redis(target_date: str) -> dict[str, Any]:
    """Analyze sessions using Redis AgentSession and BridgeEvent models.

    Queries AgentSession for sessions from the target date and BridgeEvent
    for error patterns, replacing the flat-file session directory scan.

    Args:
        target_date: Date string (YYYY-MM-DD) to filter sessions.

    Returns:
        Dict with sessions_analyzed, corrections, thrash_sessions, error_patterns.
    """
    result: dict[str, Any] = {
        "sessions_analyzed": 0,
        "corrections": [],
        "thrash_sessions": [],
        "error_patterns": [],
    }

    try:
        from models.agent_session import AgentSession
        from models.bridge_event import BridgeEvent

        # Query AgentSession for sessions from the target date
        all_sessions = AgentSession.query.all()
        target_sessions = []
        for session in all_sessions:
            if session.started_at:
                session_date = datetime.fromtimestamp(session.started_at).strftime("%Y-%m-%d")
                if session_date == target_date:
                    target_sessions.append(session)

        # Cap at 20 most interesting (sort by turn_count descending)
        target_sessions.sort(key=lambda s: s.turn_count or 0, reverse=True)
        target_sessions = target_sessions[:20]

        for session in target_sessions:
            result["sessions_analyzed"] += 1
            session_id = session.session_id or "unknown"

            # Check for thrashing: high tool_call_count relative to turn_count
            tool_calls = session.tool_call_count or 0
            turn_count = session.turn_count or 0
            if tool_calls > 5 and turn_count > 0:
                failure_ratio = max(0.0, 1.0 - (turn_count / tool_calls))
                if failure_ratio > THRASH_RATIO_THRESHOLD:
                    result["thrash_sessions"].append(
                        {
                            "session_id": session_id,
                            "tool_calls": tool_calls,
                            "successes": turn_count,
                            "failure_ratio": round(failure_ratio, 2),
                        }
                    )

            # Check transcript file for user corrections if available
            if session.log_path:
                log_path = Path(session.log_path)
                if log_path.exists():
                    try:
                        content = log_path.read_text()
                        for line in content.splitlines():
                            if "USER:" in line or "user:" in line:
                                for pattern in CORRECTION_PATTERNS:
                                    if pattern.search(line):
                                        result["corrections"].append(
                                            {
                                                "session_id": session_id,
                                                "message": line.strip()[:200],
                                                "pattern": pattern.pattern,
                                            }
                                        )
                                        break
                    except OSError:
                        pass

            # Check for failed sessions
            if session.status == "failed":
                summary_text = (session.summary or "")[:200]
                if not summary_text.strip():
                    # Skip failed sessions with empty summaries -- these produce
                    # vague, unactionable reflections. Log a warning so we can
                    # track if code paths still produce empty summaries.
                    logger.warning(
                        "Skipping failed session %s with empty summary",
                        session_id,
                    )
                    continue
                result["error_patterns"].append(
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "summary": summary_text,
                    }
                )

        # Also query BridgeEvent for error events from the target date
        try:
            all_events = BridgeEvent.query.filter(event_type="error")
            for event in all_events:
                if event.timestamp:
                    event_date = datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d")
                    if event_date == target_date:
                        result["error_patterns"].append(
                            {
                                "event_type": "bridge_error",
                                "data": event.data or {},
                                "timestamp": event_date,
                            }
                        )
        except Exception:
            pass  # BridgeEvent query is supplementary

    except Exception as e:
        logger.warning(f"Redis session analysis failed: {e}")

    return result


def run_llm_reflection(
    analysis: dict[str, Any],
) -> list[dict[str, str]]:
    """Run LLM reflection on session analysis using Claude Haiku.

    Args:
        analysis: Output from analyze_sessions_from_redis().

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
            model=HAIKU,
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


def extract_errors_from_redis(target_date: str) -> list[dict[str, str]]:
    """Extract error patterns from BridgeEvent Redis model.

    Supplements log file analysis with structured error data from Redis.

    Args:
        target_date: Date string (YYYY-MM-DD) to filter events.

    Returns:
        List of dicts with timestamp, level, message, and context.
    """
    errors: list[dict[str, str]] = []
    try:
        from models.bridge_event import BridgeEvent

        all_events = BridgeEvent.query.filter(event_type="error")
        for event in all_events:
            if event.timestamp:
                event_date = datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d")
                if event_date == target_date:
                    data = event.data or {}
                    errors.append(
                        {
                            "timestamp": datetime.fromtimestamp(event.timestamp).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "level": "ERROR",
                            "message": data.get("error", data.get("message", str(data))),
                            "context": f"project={event.project_key or 'unknown'} "
                            f"chat={event.chat_id or 'unknown'}",
                        }
                    )
    except Exception as e:
        logger.debug(f"Could not extract errors from Redis BridgeEvent: {e}")

    return errors


class ReflectionRunner:
    """Runs the reflections maintenance process.

    State is persisted in Redis via ReflectionRun model. Falls back to
    local JSON file if Redis is unavailable.
    """

    def __init__(self) -> None:
        self.state = self._load_state()
        self.projects = load_local_projects()
        self.steps = [
            (1, "Clean Up Legacy Code", self.step_clean_legacy),
            (2, "Review Previous Day's Logs", self.step_review_logs),
            (3, "Check Error Logs (Sentry)", self.step_check_sentry),
            (4, "Clean Up Task Management", self.step_clean_tasks),
            (5, "Audit Documentation", self.step_audit_docs),
            (6, "Session Analysis", self.step_session_analysis),
            (7, "LLM Reflection", self.step_llm_reflection),
            (8, "File Bug Issues", self.step_auto_fix_bugs),
            (9, "Produce Daily Report", self.step_produce_report),
            (10, "GitHub Issue Creation", self.step_create_github_issue),
            (11, "Skills Audit", self.step_skills_audit),
            (12, "Redis TTL Cleanup", self.step_redis_cleanup),
            (13, "Redis Data Quality", self.step_redis_data_quality),
            (14, "Branch and Plan Cleanup", self.step_branch_plan_cleanup),
            (15, "Feature Docs Audit", self.step_feature_docs_audit),
            (16, "Episode Cycle-Close", self.step_episode_cycle_close),
            (17, "Pattern Crystallization", self.step_pattern_crystallization),
            (18, "Principal Context Staleness", self.step_principal_staleness),
            (19, "Disk Space Check", self.step_disk_space_check),
        ]

    def _load_state(self) -> ReflectionsState:
        """Load state from Redis ReflectionRun model."""
        today = datetime.now().strftime("%Y-%m-%d")
        from models.reflections import ReflectionRun

        run = ReflectionRun.load_or_create(today)
        # Wrap in ReflectionsState for API compatibility
        state = ReflectionsState(
            current_step=run.current_step or 1,
            completed_steps=run.completed_steps or [],
            daily_report=run.daily_report or [],
            date=run.date or today,
            findings=run.findings or {},
            session_analysis=run.session_analysis or {},
            reflections=run.reflections or [],
            auto_fix_attempts=run.auto_fix_attempts or [],
            step_progress=run.step_progress or {},
        )
        return state

    async def run(self) -> None:
        """Run all reflections steps."""
        logger.info(f"Starting Reflections for {self.state.date}")
        logger.info(f"Resuming from step {self.state.current_step}")

        REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)

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
                continue

        logger.info("Reflections completed successfully")

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
            todo_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
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
                        f"Found {count} instances of deprecated typing import: {pattern}"
                    )
            except Exception:
                pass

        for finding in findings:
            self.state.add_finding("legacy_code", finding)

        self.state.step_progress["clean_legacy"] = {
            "findings": len(findings),
        }

    async def step_review_logs(self) -> None:
        """Step 2: Review previous day's logs per project.

        Uses both file-based log analysis and Redis BridgeEvent queries
        for a comprehensive view of errors and warnings.
        """
        total_files_analyzed = 0
        total_findings = 0
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        for project in self.projects:
            slug = project["slug"]
            project_dir = Path(project["working_directory"])
            logs_dir = project_dir / "logs"

            findings = []

            # Query Redis BridgeEvent for structured errors
            redis_errors = extract_errors_from_redis(yesterday)
            if redis_errors:
                findings.append(f"Redis BridgeEvent: {len(redis_errors)} error events yesterday")
                for error in redis_errors[-5:]:
                    msg = error["message"][:200]
                    findings.append(f"  [BridgeEvent] {error['timestamp']}: {msg}")

            if not logs_dir.exists():
                logger.info(f"No logs directory found for {slug}, skipping file scan")
                if findings:
                    for finding in findings:
                        self.state.add_finding(f"{slug}:log_review", finding)
                    total_findings += len(findings)
                continue

            log_files = list(logs_dir.glob("*.log"))

            for log_file in log_files:
                if not log_file.is_file():
                    continue

                try:
                    mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    if mtime < datetime.now() - timedelta(days=7):
                        findings.append(f"Log file {log_file.name} is older than 7 days")

                    size_mb = log_file.stat().st_size / (1024 * 1024)
                    if size_mb > 10:
                        findings.append(
                            f"Log file {log_file.name} is {size_mb:.1f}MB - consider rotation"
                        )

                    # Extract structured errors
                    errors = extract_structured_errors(log_file)
                    if errors:
                        findings.append(
                            f"{log_file.name}: {len(errors)} structured errors extracted"
                        )
                        # Include up to 5 most recent errors in findings
                        for error in errors[-5:]:
                            msg = error["message"][:200]
                            findings.append(f"  [{error['level']}] {error['timestamp']}: {msg}")

                    # Also count warnings
                    with open(log_file) as f:
                        lines = f.readlines()[-1000:]
                    warning_count = sum(1 for line in lines if "WARNING" in line)
                    if warning_count > 10:
                        findings.append(f"{log_file.name}: {warning_count} warnings in recent logs")

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
        todo_files = list(PROJECT_ROOT.glob("**/TODO.md")) + list(PROJECT_ROOT.glob("**/todo.md"))
        for todo_file in todo_files:
            try:
                content = todo_file.read_text()
                unchecked = content.count("[ ]")
                if unchecked > 0:
                    self.state.add_finding(
                        "tasks",
                        f"{todo_file.relative_to(PROJECT_ROOT)}: {unchecked} unchecked items",
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
            self.state.add_finding("documentation", f"Docs audit skipped: {summary.skip_reason}")
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
            if len(summary.kept) > 0 and len(summary.updated) == 0 and len(summary.deleted) == 0:
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
        """Step 9: Produce daily report to local markdown file.

        Includes principal context (mission + project priorities) at the top
        of the report so findings are contextualized against strategic goals.
        """
        total_steps = len(self.steps)
        report_lines = [
            f"# Reflections Report - {self.state.date}",
            "",
            "## Summary",
            f"- Steps completed: {len(self.state.completed_steps)}/{total_steps}",
            f"- Started: {self.state.step_started_at or 'N/A'}",
            "",
        ]

        # Include principal context for strategic alignment
        try:
            from agent.sdk_client import load_principal_context

            principal = load_principal_context(condensed=True)
            if principal:
                report_lines.extend(
                    [
                        "## Principal Priorities",
                        "",
                        principal,
                        "",
                    ]
                )
        except Exception as e:
            logger.debug(f"Could not load principal context for report: {e}")

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
            report_lines.append(f"- Sessions analyzed: {sa.get('sessions_analyzed', 0)}")
            corrections = sa.get("corrections", [])
            if corrections:
                report_lines.append(f"- User corrections detected: {len(corrections)}")
                for c in corrections[:5]:
                    report_lines.append(
                        f"  - [{c.get('session_id', '?')}] {c.get('message', '')[:100]}"
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

            # Include error patterns from Redis
            error_patterns = sa.get("error_patterns", [])
            if error_patterns:
                report_lines.append(f"- Error patterns: {len(error_patterns)}")
                for ep in error_patterns[:5]:
                    report_lines.append(
                        f"  - {ep.get('session_id', ep.get('event_type', '?'))}: "
                        f"{ep.get('summary', ep.get('data', ''))}"
                    )

            report_lines.append("")

        # Add reflections if available
        if self.state.reflections:
            report_lines.append("## LLM Reflections")
            for r in self.state.reflections:
                report_lines.append(f"- **{r.get('category', 'unknown')}**: {r.get('summary', '')}")
                report_lines.append(f"  - Pattern: {r.get('pattern', '')}")
                report_lines.append(f"  - Prevention: {r.get('prevention', '')}")
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
        REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        report_file = REFLECTIONS_DIR / f"report_{self.state.date}.md"
        report_file.write_text(report_content)

        logger.info(f"Report written to {report_file}")

        print("\n" + "=" * 60)
        print(report_content)
        print("=" * 60)

    async def step_session_analysis(self) -> None:
        """Step 6: Analyze yesterday's sessions.

        Tries Redis-backed analysis via AgentSession/BridgeEvent first,
        falls back to file-based analysis.
        """
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Try Redis-backed analysis first
        analysis = analyze_sessions_from_redis(yesterday)

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
        error_patterns = analysis.get("error_patterns", [])
        if error_patterns:
            self.state.add_finding(
                "session_analysis",
                f"Found {len(error_patterns)} error patterns in sessions/events",
            )

        self.state.step_progress["session_analysis"] = {
            "sessions_analyzed": analysis["sessions_analyzed"],
            "corrections": len(analysis["corrections"]),
            "thrash_sessions": len(analysis["thrash_sessions"]),
            "error_patterns": len(error_patterns),
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
        """Step 8: File GitHub issues for high-confidence code bugs."""
        enabled = os.environ.get("REFLECTIONS_AUTO_FIX_ENABLED", "true").lower()
        if enabled not in ("true", "1", "yes"):
            logger.info("REFLECTIONS_AUTO_FIX_ENABLED is false, skipping bug issues step")
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
            f"Bug issues: {len(candidates)} candidate(s) from {len(reflections)} reflection(s)"
        )

        attempts = []
        for r in candidates:
            pattern = r.get("pattern", "")
            summary = r.get("summary", "")
            prevention = r.get("prevention", "")

            if is_ignored(pattern, ignore_entries):
                logger.info(f"Bug issues: skipping ignored pattern: {pattern[:60]}")
                attempts.append({"pattern": pattern, "status": "ignored"})
                self.state.add_finding("auto_fix", f"Ignored (in ignore log): {summary[:80]}")
                continue

            # Check for existing GitHub work (use first project with github config)
            project_wd = None
            for project in self.projects:
                if project.get("github"):
                    project_wd = project["working_directory"]
                    break

            if project_wd and has_existing_github_work(pattern, project_wd):
                logger.info(f"Bug issues: duplicate found for pattern: {pattern[:60]}")
                attempts.append({"pattern": pattern, "status": "duplicate"})
                self.state.add_finding("auto_fix", f"Skipped (existing PR/issue): {summary[:80]}")
                continue

            if dry_run:
                logger.info(f"Bug issues: [DRY RUN] would create issue for: {summary[:80]}")
                attempts.append({"pattern": pattern, "status": "dry_run"})
                self.state.add_finding("auto_fix", f"[DRY RUN] Would file issue: {summary[:80]}")
                continue

            if not project_wd:
                logger.warning("Bug issues: no project with github config found, skipping")
                attempts.append(
                    {
                        "pattern": pattern,
                        "status": "skipped",
                        "reason": "no_github_project",
                    }
                )
                continue

            # Create GitHub issue instead of auto-fixing
            issue_body = (
                f"## Bug Pattern\n\n{pattern}\n\n"
                f"## Summary\n\n{summary}\n\n"
                f"## Suggested Prevention\n\n{prevention}\n\n"
                f"---\n*Filed automatically by the reflections system.*"
            )
            issue_title = f"Bug: {summary[:80]}"
            logger.info(f"Bug issues: creating issue for: {summary[:80]}")

            try:
                result = subprocess.run(
                    [
                        "gh",
                        "issue",
                        "create",
                        "--title",
                        issue_title,
                        "--body",
                        issue_body,
                        "--label",
                        "bug",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=project_wd,
                )
                if result.returncode == 0:
                    issue_url = result.stdout.strip()
                    attempts.append(
                        {
                            "pattern": pattern,
                            "status": "success",
                            "issue_url": issue_url,
                        }
                    )
                    self.state.add_finding("auto_fix", f"Issue created: {issue_url}")
                    logger.info(f"Bug issues: created {issue_url}")
                else:
                    output_snippet = (result.stderr or result.stdout or "")[:200]
                    attempts.append(
                        {
                            "pattern": pattern,
                            "status": "failed",
                            "output": output_snippet,
                        }
                    )
                    logger.warning(f"Bug issues: gh issue create failed: {output_snippet}")
                    self.state.add_finding("auto_fix", f"Issue creation failed: {summary[:80]}")
            except subprocess.TimeoutExpired:
                logger.warning(f"Bug issues: timed out for: {summary[:80]}")
                attempts.append({"pattern": pattern, "status": "timeout"})
                self.state.add_finding("auto_fix", f"Issue creation timed out: {summary[:80]}")
            except Exception as e:
                logger.warning(f"Bug issues: error: {e}")
                attempts.append({"pattern": pattern, "status": "error", "error": str(e)})

        self.state.auto_fix_attempts = attempts
        self.state.step_progress["auto_fix_bugs"] = {
            "candidates": len(candidates),
            "attempts": len(attempts),
            "dry_run": dry_run,
        }

    async def step_create_github_issue(self) -> None:
        """Step 10: Create GitHub issues per project with findings.

        For each local project with a github config, filters findings
        namespaced to that project and creates a GitHub issue if findings
        exist. Also calls step_post_to_telegram for each project.
        """
        # Reset the in-memory dedup guard at the start of each run to prevent
        # stale state from a previous run in the same process.
        reset_dedup_guard()

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
            issue_url_or_bool = create_reflections_issue(
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

    async def step_skills_audit(self) -> None:
        """Step 11: Run skills audit to validate all SKILL.md files."""
        audit_script = (
            PROJECT_ROOT / ".claude" / "skills" / "do-skills-audit" / "scripts" / "audit_skills.py"
        )
        if not audit_script.exists():
            logger.warning("Skills audit script not found, skipping")
            return

        try:
            result = subprocess.run(
                [sys.executable, str(audit_script), "--no-sync", "--json"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(PROJECT_ROOT),
            )
            audit_data = json.loads(result.stdout) if result.stdout else {}
            summary = audit_data.get("summary", {})
            fails = summary.get("fail", 0)
            warns = summary.get("warn", 0)
            total = summary.get("total_skills", 0)

            self.state.step_progress["skills_audit"] = {
                "total_skills": total,
                "fails": fails,
                "warns": warns,
            }

            if fails > 0:
                self.state.findings.setdefault("ai:skills_audit", []).append(
                    f"{fails} skill(s) have FAIL findings"
                )
                for f in audit_data.get("findings", []):
                    if f.get("severity") == "FAIL":
                        self.state.findings["ai:skills_audit"].append(
                            f"  {f.get('skill')}: {f.get('message')}"
                        )

            logger.info(f"Skills audit: {total} skills, {fails} fails, {warns} warns")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            logger.error(f"Skills audit failed: {e}")
            self.state.step_progress["skills_audit"] = {"error": str(e)}

    async def step_redis_cleanup(self) -> None:
        """Step 12: Run TTL cleanup on all Redis models.

        Cleans up: TelegramMessage, Link, Chat, AgentSession (90-day),
        BridgeEvent (7-day), ReflectionRun (30-day), ReflectionIgnore (expired).

        """
        try:
            from models.agent_session import AgentSession
            from models.bridge_event import BridgeEvent
            from models.chat import Chat
            from models.link import Link
            from models.reflections import (
                ReflectionIgnore,
                ReflectionRun,
            )
            from models.telegram import TelegramMessage

            msg_deleted = TelegramMessage.cleanup_expired(max_age_days=90)
            link_deleted = Link.cleanup_expired(max_age_days=90)
            chat_deleted = Chat.cleanup_expired(max_age_days=90)
            session_deleted = AgentSession.cleanup_expired(max_age_days=90)
            event_deleted = BridgeEvent.cleanup_old(max_age_seconds=7 * 86400)
            run_deleted = ReflectionRun.cleanup_expired(max_age_days=30)
            ignore_deleted = ReflectionIgnore.cleanup_expired()

            total = (
                msg_deleted
                + link_deleted
                + chat_deleted
                + session_deleted
                + event_deleted
                + run_deleted
                + ignore_deleted
            )
            logger.info(
                f"Redis TTL cleanup: {total} records deleted "
                f"(msgs={msg_deleted}, links={link_deleted}, "
                f"chats={chat_deleted}, sessions={session_deleted}, "
                f"events={event_deleted}, runs={run_deleted}, "
                f"ignores={ignore_deleted})"
            )
            self.state.daily_report.append(f"Redis cleanup: {total} expired records removed")
        except Exception as e:
            logger.warning(f"Redis TTL cleanup failed (non-fatal): {e}")

    async def step_redis_data_quality(self) -> None:
        """Step 13: Redis data quality checks.

        Queries Redis models to surface data quality issues:
        - Unsummarized links (shared but never summarized by AI)
        - Dead channels (chats with no recent message activity)
        - Sessions with transcripts containing recurring error patterns
        """
        findings: list[str] = []

        try:
            import time as _time

            from models.agent_session import AgentSession
            from models.chat import Chat
            from models.link import Link
            from models.telegram import TelegramMessage

            # 1. Unsummarized links: shared in last 7 days, no ai_summary

            week_ago = _time.time() - (7 * 86400)
            all_links = Link.query.all()
            unsummarized = [
                link
                for link in all_links
                if link.timestamp and link.timestamp > week_ago and not link.ai_summary
            ]
            if unsummarized:
                findings.append(
                    f"{len(unsummarized)} links shared in last 7 days have no AI summary"
                )
                for link in unsummarized[:5]:
                    findings.append(
                        f"  Unsummarized: {link.url} (chat={link.chat_id}, status={link.status})"
                    )

            # 2. Dead channels: chats not updated in 30+ days
            month_ago = _time.time() - (30 * 86400)
            all_chats = Chat.query.all()
            dead_chats = [
                chat for chat in all_chats if chat.updated_at and chat.updated_at < month_ago
            ]
            if dead_chats:
                findings.append(f"{len(dead_chats)} chat(s) with no activity in 30+ days")
                for chat in dead_chats[:5]:
                    days_inactive = int((_time.time() - chat.updated_at) / 86400)
                    findings.append(
                        f"  Inactive: {chat.chat_name} "
                        f"({days_inactive} days, type={chat.chat_type})"
                    )

            # 3. Transcript error pattern analysis: find common errors in recent sessions
            recent_cutoff = _time.time() - (7 * 86400)
            all_sessions = AgentSession.query.all()
            recent_sessions = [
                s for s in all_sessions if s.started_at and s.started_at > recent_cutoff
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
                    # Count common error patterns in transcripts
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

            # 4. Message volume per chat (identify high-traffic vs low-traffic)
            # Cap to last 10000 messages to bound memory usage.
            # Popoto lacks server-side filtering for SortedField range queries
            # on TelegramMessage, so we fetch and filter in Python (same pattern
            # as cleanup_expired in models/telegram.py -- bounded dataset).
            all_messages = TelegramMessage.query.all()[:10000]
            recent_messages = [m for m in all_messages if m.timestamp and m.timestamp > week_ago]
            chat_volumes: dict[str, int] = {}
            for msg in recent_messages:
                chat_id = msg.chat_id or "unknown"
                chat_volumes[chat_id] = chat_volumes.get(chat_id, 0) + 1

            if chat_volumes:
                sorted_chats = sorted(chat_volumes.items(), key=lambda x: x[1], reverse=True)
                findings.append(
                    f"Message volume (last 7 days): "
                    f"{len(recent_messages)} messages across "
                    f"{len(chat_volumes)} chats"
                )
                for chat_id, count in sorted_chats[:3]:
                    # Try to resolve chat name
                    chat_name = chat_id
                    chat_records = Chat.query.filter(chat_id=chat_id)
                    if chat_records:
                        chat_name = chat_records[0].chat_name or chat_id
                    findings.append(f"  {chat_name}: {count} messages")

        except Exception as e:
            logger.warning(f"Redis data quality check failed (non-fatal): {e}")
            findings.append(f"Data quality check error: {e}")

        for finding in findings:
            self.state.add_finding("redis_data_quality", finding)

        self.state.step_progress["redis_data_quality"] = {
            "findings": len(findings),
        }

    async def step_branch_plan_cleanup(self) -> None:
        """Step 14: Clean up stale branches and audit plan files.

        Branches:
        - Deletes local branches already merged into main

        Plans (docs/plans/):
        - COMPLETE: all checkboxes checked -> needs docs migration & deletion
        - ORPHANED: no matching open issue AND referenced issue is closed
        - CLOSED-ISSUE: plan references a closed issue -> should be cleaned up
        - DUPLICATE: multiple plan files for the same feature (underscore/hyphen)
        - ACTIVE: incomplete plan with matching open issue (healthy)

        Uses async subprocess calls for parallel GitHub issue state checks.
        """
        findings: list[str] = []

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
                        else:
                            logger.warning(
                                f"Branch cleanup: failed to delete {branch}: "
                                f"{del_result.stderr.strip()}"
                            )
        except Exception as e:
            logger.warning(f"Branch cleanup failed (non-fatal): {e}")

        # --- Plan file cleanup ---
        plans_dir = PROJECT_ROOT / "docs" / "plans"
        if not plans_dir.exists():
            self.state.step_progress["branch_plan_cleanup"] = {
                "findings": len(findings),
            }
            return

        plan_files = sorted(plans_dir.glob("*.md"))

        # Find project working directory for gh CLI
        project_wd = None
        for project in self.projects:
            if project.get("github"):
                project_wd = project["working_directory"]
                break

        # --- Detect duplicate plan files (underscore vs hyphen variants) ---
        normalized: dict[str, list[Path]] = {}
        for pf in plan_files:
            key = pf.stem.replace("-", "_").lower()
            normalized.setdefault(key, []).append(pf)
        for _key, dupes in normalized.items():
            if len(dupes) > 1:
                names = ", ".join(d.name for d in dupes)
                findings.append(f"Duplicate plans: {names}")
                self.state.add_finding(
                    "branch_plan_cleanup",
                    f"Duplicate plan files (consolidate): {names}",
                )

        # --- Extract issue references from each plan ---
        plan_issue_refs: dict[Path, list[int]] = {}
        for plan_file in plan_files:
            plan_text = plan_file.read_text(errors="replace")
            refs: set[int] = set()
            for m in re.finditer(r"#(\d+)", plan_text):
                refs.add(int(m.group(1)))
            for m in re.finditer(r"github\.com/[^/]+/[^/]+/issues/(\d+)", plan_text):
                refs.add(int(m.group(1)))
            plan_issue_refs[plan_file] = sorted(refs)

        # --- Check issue states in parallel via async subprocess ---
        async def check_issue_state(issue_num: int) -> tuple[int, str]:
            """Return (issue_number, 'open'|'closed'|'unknown')."""
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

        # Collect unique issue numbers, check in parallel batches
        all_issue_nums: set[int] = set()
        for refs in plan_issue_refs.values():
            all_issue_nums.update(refs)

        issue_states: dict[int, str] = {}
        if all_issue_nums:
            batch_size = 10
            issue_list = sorted(all_issue_nums)
            for i in range(0, len(issue_list), batch_size):
                batch = issue_list[i : i + batch_size]
                results = await asyncio.gather(
                    *[check_issue_state(n) for n in batch],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, tuple):
                        issue_states[r[0]] = r[1]

        # --- Evaluate each plan file ---
        stats = {"complete": 0, "orphaned": 0, "closed_issue": 0, "active": 0}

        for plan_file in plan_files:
            plan_name = plan_file.stem
            plan_text = plan_file.read_text(errors="replace")
            refs = plan_issue_refs.get(plan_file, [])

            # Check checkbox completion
            checkboxes = re.findall(r"- \[([ xX])\]", plan_text)
            checked = sum(1 for c in checkboxes if c.lower() == "x")
            is_complete = checkboxes and checked == len(checkboxes)

            if is_complete:
                stats["complete"] += 1
                findings.append(
                    f"Plan complete: {plan_name} -- "
                    f"run /do-docs then delete docs/plans/{plan_file.name}"
                )
                self.state.add_finding(
                    "branch_plan_cleanup",
                    f"Completed plan needs docs migration: {plan_file.name}",
                )
                continue

            # Check if all referenced issues are closed
            if refs:
                ref_states = [issue_states.get(r, "unknown") for r in refs]
                all_closed = all(s == "closed" for s in ref_states if s != "unknown")
                any_open = any(s == "open" for s in ref_states)

                if all_closed and not any_open:
                    stats["closed_issue"] += 1
                    closed_refs = ", ".join(f"#{r}" for r in refs)
                    findings.append(f"Plan with closed issue(s): {plan_file.name} ({closed_refs})")
                    self.state.add_finding(
                        "branch_plan_cleanup",
                        f"Stale plan (all issues closed): {plan_file.name} ({closed_refs})",
                    )
                    continue

                if any_open:
                    stats["active"] += 1
                    continue

            # No refs or unknown state -- fall back to name search
            if project_wd:
                try:
                    result = subprocess.run(
                        [
                            "gh",
                            "issue",
                            "list",
                            "--state",
                            "open",
                            "--search",
                            plan_name,
                        ],
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
            self.state.add_finding(
                "branch_plan_cleanup",
                f"Orphaned plan (no open issue): {plan_file.name}",
            )

        for finding in findings:
            logger.info(f"Branch/plan cleanup: {finding}")

        self.state.step_progress["branch_plan_cleanup"] = {
            "total_plans": len(plan_files),
            **stats,
            "findings": len(findings),
        }

    async def step_feature_docs_audit(self) -> None:
        """Step 15: Audit feature documentation for staleness and accuracy.

        Checks:
        - Stale references (SessionLog, RedisJob, old module paths)
        - README.md index vs actual files (missing entries, phantom entries)
        - Plan docs masquerading as feature docs (majority unchecked checkboxes)
        - Stub/empty docs (<5 content lines)
        - Docs referencing code files that no longer exist
        """
        findings: list[str] = []
        features_dir = PROJECT_ROOT / "docs" / "features"

        if not features_dir.exists():
            self.state.step_progress["feature_docs_audit"] = {"findings": 0}
            return

        feature_files = sorted(features_dir.glob("*.md"))
        readme_path = features_dir / "README.md"

        # Known stale terms to flag
        stale_terms = {
            "SessionLog": "AgentSession",
            "RedisJob": "AgentSession",
            "session_log": "agent_session",
            "redis_job": "agent_session",
        }

        stats = {
            "total_docs": len(feature_files),
            "current": 0,
            "stale_refs": 0,
            "stubs": 0,
            "plan_masquerade": 0,
            "dead_code_refs": 0,
        }

        for doc_file in feature_files:
            if doc_file.name == "README.md":
                continue

            text = doc_file.read_text(errors="replace")
            doc_findings: list[str] = []

            # Check for stale term references
            for old_term, new_term in stale_terms.items():
                if old_term in text:
                    # Don't flag if it's documenting the migration itself
                    migration_context = (
                        f"renamed to {new_term}" in text
                        or f"replaced by {new_term}" in text
                        or f"now {new_term}" in text
                        or f"formerly {old_term}" in text
                        or f"Replaces {old_term}" in text
                        or f"replaces {old_term}" in text
                    )
                    if not migration_context:
                        doc_findings.append(f"stale term '{old_term}' (now '{new_term}')")

            # Check for stub docs (very short, no real content)
            content_lines = [
                ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")
            ]
            if len(content_lines) < 5:
                stats["stubs"] += 1
                doc_findings.append("stub doc (<5 content lines)")

            # Check for plan-masquerading-as-feature (majority unchecked)
            unchecked = re.findall(r"- \[ \]", text)
            checked_boxes = re.findall(r"- \[[xX]\]", text)
            if unchecked and len(unchecked) > len(checked_boxes):
                stats["plan_masquerade"] += 1
                doc_findings.append(
                    f"looks like a plan ({len(unchecked)} unchecked, "
                    f"{len(checked_boxes)} checked checkboxes)"
                )

            # Check for references to code files that don't exist
            code_refs = re.findall(
                r"(?:`|\b)"
                r"((?:agent|bridge|models|tools|scripts|config)/\S+\.py)",
                text,
            )
            for ref in code_refs:
                ref_path = PROJECT_ROOT / ref
                if not ref_path.exists():
                    stats["dead_code_refs"] += 1
                    doc_findings.append(f"references non-existent file: {ref}")

            if doc_findings:
                stats["stale_refs"] += len([f for f in doc_findings if f.startswith("stale term")])
                for df in doc_findings:
                    finding = f"{doc_file.name}: {df}"
                    findings.append(finding)
                    self.state.add_finding("feature_docs_audit", finding)
            else:
                stats["current"] += 1

        # --- README.md index validation ---
        if readme_path.exists():
            readme_text = readme_path.read_text(errors="replace")
            actual_files = {f.name for f in feature_files if f.name != "README.md"}

            # Extract doc references from README table rows
            readme_refs = set(re.findall(r"\[.*?\]\(([^)]+\.md)\)", readme_text))
            readme_refs = {r.lstrip("./") for r in readme_refs}

            # Files in directory but not in README
            unlisted = actual_files - readme_refs
            for f in sorted(unlisted):
                finding = f"README.md missing entry for: {f}"
                findings.append(finding)
                self.state.add_finding("feature_docs_audit", finding)

            # Files in README but not in directory
            phantom = readme_refs - actual_files
            for f in sorted(phantom):
                finding = f"README.md references non-existent doc: {f}"
                findings.append(finding)
                self.state.add_finding("feature_docs_audit", finding)

            if unlisted or phantom:
                stats["readme_mismatches"] = len(unlisted) + len(phantom)

        for finding in findings:
            logger.info(f"Feature docs audit: {finding}")

        self.state.step_progress["feature_docs_audit"] = {
            **stats,
            "findings": len(findings),
        }

    async def step_episode_cycle_close(self) -> None:
        """Step 16: Create CyclicEpisode records for completed SDLC sessions.

        Reads completed SDLC sessions from the past 24 hours, classifies
        their fingerprints via the fingerprint classifier, and writes
        CyclicEpisode records. Skips non-SDLC sessions and sessions that
        already have linked episodes (idempotent).
        """
        import time as _time

        from models.cyclic_episode import CyclicEpisode

        from models.agent_session import AgentSession
        from scripts.fingerprint_classifier import classify_session

        cutoff = _time.time() - 86400  # past 24 hours
        episodes_created = 0
        sessions_skipped = 0

        try:
            all_sessions = AgentSession.query.all()
        except Exception as e:
            logger.warning(f"Episode cycle-close: failed to query sessions: {e}")
            self.state.step_progress["episode_cycle_close"] = {
                "error": str(e),
            }
            return

        for session in all_sessions:
            # Skip sessions not completed in the past 24h
            completed_at = session.completed_at or 0
            if completed_at < cutoff:
                continue

            # Skip non-SDLC sessions
            if not session.is_sdlc_job():
                sessions_skipped += 1
                continue

            # Skip if status is not completed
            if session.status != "completed":
                sessions_skipped += 1
                continue

            # Check if episode already exists for this session (idempotent)
            existing = CyclicEpisode.query.filter(raw_ref=session.job_id)
            if existing:
                sessions_skipped += 1
                continue

            # Classify fingerprint (needed for dedup check below)
            try:
                fingerprint = classify_session(session)
            except Exception as e:
                logger.warning(f"Fingerprint classification failed for {session.job_id}: {e}")
                fingerprint = {
                    "problem_topology": "ambiguous",
                    "affected_layer": "unknown",
                    "ambiguity_at_intake": 0.5,
                    "acceptance_criterion_defined": False,
                }

            # Determine vault from project_key
            vault = f"mem:{session.project_key}" if session.project_key else "mem:default"

            # Semantic dedup: skip if an episode with the same fingerprint + vault
            # already exists and was created within the same session_id scope.
            # This prevents structurally redundant episodes from auto-continues
            # or retries of the same work item.
            dedup_matches = [
                e
                for e in CyclicEpisode.query.filter(
                    problem_topology=fingerprint["problem_topology"],
                    affected_layer=fingerprint["affected_layer"],
                    vault=vault,
                )
                if e.branch_name and session.branch_name and e.branch_name == session.branch_name
            ]
            if dedup_matches:
                logger.info(
                    f"Semantic dedup: skipping episode for session {session.job_id}, "
                    f"existing episode with same fingerprint+branch: {dedup_matches[0].episode_id}"
                )
                sessions_skipped += 1
                continue

            # Compute stage durations from history
            stage_durations = {}

            # Create episode
            try:
                CyclicEpisode.create(
                    vault=vault,
                    raw_ref=session.job_id,
                    created_at=_time.time(),
                    problem_topology=fingerprint["problem_topology"],
                    affected_layer=fingerprint["affected_layer"],
                    ambiguity_at_intake=fingerprint["ambiguity_at_intake"],
                    acceptance_criterion_defined=fingerprint["acceptance_criterion_defined"],
                    tool_sequence=session.tool_sequence
                    if isinstance(session.tool_sequence, list)
                    else [],
                    friction_events=session.friction_events
                    if isinstance(session.friction_events, list)
                    else [],
                    stage_durations=stage_durations,
                    deviation_count=0,
                    resolution_type="clean_merge"
                    if not session.has_failed_stage()
                    else "patch_required",
                    intent_satisfied=session.status == "completed",
                    review_round_count=0,
                    surprise_delta=0.0,
                    issue_url=session.issue_url,
                    branch_name=session.branch_name,
                    session_summary=session.summary[:1000] if session.summary else None,
                )
                episodes_created += 1
                logger.info(
                    f"Created CyclicEpisode for session {session.job_id}: "
                    f"topology={fingerprint['problem_topology']}, "
                    f"layer={fingerprint['affected_layer']}"
                )
            except Exception as e:
                logger.warning(f"Failed to create episode for session {session.job_id}: {e}")

        self.state.step_progress["episode_cycle_close"] = {
            "episodes_created": episodes_created,
            "sessions_skipped": sessions_skipped,
        }
        if episodes_created:
            self.state.add_finding(
                "episode_cycle_close",
                f"Created {episodes_created} behavioral episodes from completed SDLC sessions",
            )
        logger.info(f"Episode cycle-close: created={episodes_created}, skipped={sessions_skipped}")

    async def step_pattern_crystallization(self) -> None:
        """Step 17: Crystallize ProceduralPatterns from episode clusters.

        Scans CyclicEpisodes for fingerprint clusters (same problem_topology +
        affected_layer) with 3+ episodes and consistent outcomes. Creates or
        reinforces ProceduralPatterns for qualifying clusters.

        Content is stripped from patterns before writing -- they contain only
        structural abstractions safe for cross-machine sync.
        """
        import time as _time
        from collections import Counter, defaultdict

        from models.cyclic_episode import CyclicEpisode
        from models.procedural_pattern import ProceduralPattern

        crystallization_threshold = 3  # minimum episodes to form a pattern

        try:
            all_episodes = CyclicEpisode.query.all()
        except Exception as e:
            logger.warning(f"Pattern crystallization: failed to query episodes: {e}")
            self.state.step_progress["pattern_crystallization"] = {
                "error": str(e),
            }
            return

        # Group episodes by fingerprint cluster
        clusters: dict[tuple[str, str], list] = defaultdict(list)
        for episode in all_episodes:
            key = (episode.problem_topology or "ambiguous", episode.affected_layer or "unknown")
            clusters[key].append(episode)

        patterns_created = 0
        patterns_reinforced = 0

        for (topology, layer), episodes in clusters.items():
            if len(episodes) < crystallization_threshold:
                continue

            # Compute cluster stats
            successes = sum(1 for e in episodes if e.intent_satisfied)
            success_rate = successes / len(episodes)

            # Skip clusters with 0% success rate -- no useful pattern
            if success_rate == 0.0:
                continue

            # Find canonical tool sequence (most common)
            tool_seqs = [
                tuple(e.tool_sequence)
                for e in episodes
                if isinstance(e.tool_sequence, list) and e.tool_sequence
            ]
            canonical = list(Counter(tool_seqs).most_common(1)[0][0]) if tool_seqs else []

            # Generate warnings from friction events
            warnings = []
            friction_counts: dict[str, int] = defaultdict(int)
            for episode in episodes:
                if isinstance(episode.friction_events, list):
                    for fe in episode.friction_events:
                        parts = fe.split("|") if isinstance(fe, str) else []
                        if len(parts) >= 2:
                            friction_counts[f"{parts[0]}:{parts[1]}"] += 1
            # Warn about friction that occurs in >50% of episodes
            for friction_key, count in friction_counts.items():
                if count > len(episodes) / 2:
                    warnings.append(
                        f"Frequent friction in {topology}/{layer}: {friction_key} "
                        f"({count}/{len(episodes)} episodes)"
                    )

            # Check if pattern already exists
            existing = ProceduralPattern.query.filter(
                problem_topology=topology,
                affected_layer=layer,
            )

            episode_ids = [e.episode_id for e in episodes if e.episode_id]

            if existing:
                pattern = existing[0]
                pattern.reinforce(success_rate > 0.5)
                # Update canonical tool sequence and warnings
                pattern.canonical_tool_sequence = canonical
                pattern.warnings = warnings
                pattern.source_episode_ids = episode_ids
                pattern.save()
                patterns_reinforced += 1
            else:
                # Create new pattern
                now = _time.time()
                ProceduralPattern.create(
                    vault="shared",
                    problem_topology=topology,
                    affected_layer=layer,
                    canonical_tool_sequence=canonical,
                    warnings=warnings,
                    shortcuts=[],
                    success_rate=success_rate,
                    sample_count=len(episodes),
                    success_count=successes,
                    confidence=success_rate * min(len(episodes) / 10.0, 1.0),
                    last_reinforced=now,
                    created_at=now,
                    source_episode_ids=episode_ids,
                )
                patterns_created += 1
                logger.info(
                    f"Crystallized pattern: {topology}/{layer} "
                    f"(success_rate={success_rate:.2f}, samples={len(episodes)})"
                )

        self.state.step_progress["pattern_crystallization"] = {
            "patterns_created": patterns_created,
            "patterns_reinforced": patterns_reinforced,
            "clusters_evaluated": len(clusters),
        }
        if patterns_created or patterns_reinforced:
            self.state.add_finding(
                "pattern_crystallization",
                f"Crystallized {patterns_created} new patterns, "
                f"reinforced {patterns_reinforced} existing patterns",
            )
        logger.info(
            f"Pattern crystallization: created={patterns_created}, "
            f"reinforced={patterns_reinforced}, clusters={len(clusters)}"
        )

    async def step_principal_staleness(self) -> None:
        """Step 18: Check if PRINCIPAL.md is stale (>90 days since last modification).

        PRINCIPAL.md encodes the supervisor's strategic context. If it hasn't
        been updated in 90+ days, flag it for review since priorities may
        have shifted.
        """
        principal_path = PROJECT_ROOT / "config" / "PRINCIPAL.md"

        if not principal_path.exists():
            self.state.add_finding(
                "principal_context",
                "config/PRINCIPAL.md does not exist — principal context is unavailable",
            )
            self.state.step_progress["principal_staleness"] = {"status": "missing"}
            return

        mod_time = datetime.fromtimestamp(principal_path.stat().st_mtime)
        age_days = (datetime.now() - mod_time).days
        staleness_threshold = 90

        if age_days > staleness_threshold:
            self.state.add_finding(
                "principal_context",
                f"config/PRINCIPAL.md is {age_days} days old (threshold: {staleness_threshold}). "
                "Consider reviewing and updating supervisor priorities.",
            )
            logger.warning(
                f"PRINCIPAL.md is stale: last modified {age_days} days ago "
                f"(threshold: {staleness_threshold} days)"
            )
        else:
            logger.info(
                f"PRINCIPAL.md is fresh: last modified {age_days} days ago "
                f"(threshold: {staleness_threshold} days)"
            )

        self.state.step_progress["principal_staleness"] = {
            "status": "stale" if age_days > staleness_threshold else "fresh",
            "age_days": age_days,
            "threshold": staleness_threshold,
        }

    async def step_disk_space_check(self) -> None:
        """Step 19: Check available disk space on the project volume.

        This is the canonical template step — use it as a reference when adding
        new reflection steps. It demonstrates:

        1. Method signature: ``async def step_<key>(self) -> None``
        2. Local ``findings: list[str]`` for collecting issues
        3. ``self.state.add_finding("<key>", text)`` to persist each finding
        4. ``self.state.step_progress["<key>"] = {...}`` for metrics
        5. Top-level try/except so a single step failure never halts the run

        The check uses :func:`shutil.disk_usage` on ``PROJECT_ROOT`` and records
        a finding when free space drops below 10 GB.
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
                self.state.add_finding("disk_space_check", finding)
                logger.warning(finding)
            else:
                logger.info(f"Disk space OK: {free_gb:.1f} GB free of {total_gb:.1f} GB total")
        except Exception:
            logger.exception("Failed to check disk space")

        self.state.step_progress["disk_space_check"] = {
            "findings": len(findings),
        }

    async def step_post_to_telegram(self, project: dict, issue_url: str = "") -> None:
        """Post reflections summary to project's Telegram chat.

        Args:
            project: Project dict from load_local_projects().
            issue_url: Optional GitHub issue URL to include in message.
        """
        groups = project.get("telegram", {}).get("groups", [])
        if not groups:
            logger.info(f"No telegram groups configured for {project['slug']}, skipping")
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
                len(v) for k, v in self.state.findings.items() if k.startswith(f"{slug}:")
            )
            msg_lines = [f"Reflections Report — {self.state.date}"]
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
                        logger.info(f"Posted reflections summary to {group_name}")
                    except Exception as e:
                        logger.warning(f"Could not post to {group_name}: {e}")
        except ImportError:
            logger.info("telethon not available, skipping Telegram post")
        except Exception as e:
            logger.warning(f"Telegram post failed for {project['slug']}: {e}")


# --- ReflectionsState: compatibility wrapper ---

from dataclasses import dataclass, field  # noqa: E402


@dataclass
class ReflectionsState:
    """Persisted state for resumability via Redis ReflectionRun model."""

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

    def save(self) -> None:
        """Save state to Redis ReflectionRun model."""
        import time as _time

        from models.reflections import ReflectionRun

        existing = ReflectionRun.query.filter(date=self.date)
        started_at = _time.time()
        if existing:
            started_at = existing[0].started_at or started_at
            existing[0].delete()

        ReflectionRun.create(
            date=self.date,
            current_step=self.current_step,
            completed_steps=self.completed_steps,
            daily_report=self.daily_report,
            findings=self.findings,
            session_analysis=self.session_analysis,
            reflections=self.reflections,
            auto_fix_attempts=self.auto_fix_attempts,
            step_progress=self.step_progress,
            started_at=started_at,
            dry_run=getattr(self, "_dry_run", False),
        )

    def add_finding(self, category: str, finding: str) -> None:
        """Add a finding to the report."""
        if category not in self.findings:
            self.findings[category] = []
        self.findings[category].append(finding)


async def run_reflections_async() -> None:
    """Run the full reflections pipeline. Called by the reflection scheduler."""
    runner = ReflectionRunner()
    await runner.run()


async def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Reflections autonomous maintenance")
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
        from models.reflections import ReflectionIgnore

        entry = ReflectionIgnore.add_ignore(pattern=args.ignore, reason=args.reason, days=14)
        ignored_until = datetime.fromtimestamp(entry.expires_at).date().isoformat()
        print(f"Added ignore entry: {args.ignore!r} (until {ignored_until})")
        return

    runner = ReflectionRunner()
    if args.dry_run:
        runner.state._dry_run = True
        logger.info("DRY RUN mode — no side effects will be triggered")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
