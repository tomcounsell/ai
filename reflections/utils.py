"""
reflections/utils.py — Shared helpers for all reflection callables.

All helpers are pure functions with no shared mutable state.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("reflections.utils")

PROJECT_ROOT = Path(__file__).parent.parent
AI_ROOT = PROJECT_ROOT

# Correction patterns in user messages (for session intelligence analysis)
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

THRASH_RATIO_THRESHOLD = 0.5


def load_local_projects() -> list[dict]:
    """Load projects from projects.json, filtered to those present on this machine.

    Loads from ~/Desktop/Valor/projects.json (iCloud-synced, private).
    Falls back to legacy in-repo config path if the Desktop path doesn't exist.

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
    ).expanduser()
    if not config_path.exists():
        config_path = AI_ROOT / "config" / "projects.json"
    if not config_path.exists():
        logger.warning(f"Project config not found at {config_path}, returning empty")
        return []
    data = json.loads(config_path.read_text())
    projects = []
    for slug, cfg in data.get("projects", {}).items():
        wd = Path(cfg.get("working_directory", "")).expanduser()
        if wd.exists():
            projects.append({"slug": slug, **cfg, "working_directory": str(wd)})
    return projects


def is_ignored(pattern: str, ignore_entries: list[dict]) -> bool:
    """Check if a pattern matches any active ignore entry.

    Args:
        pattern: The pattern to check (bug name, issue title, etc.).
        ignore_entries: List of dicts with 'pattern' key from ReflectionIgnore.

    Returns:
        True if the pattern matches any active ignore entry.
    """
    pattern_lower = pattern.lower()
    for entry in ignore_entries:
        entry_pattern = entry.get("pattern", "").lower()
        if entry_pattern and (entry_pattern in pattern_lower or pattern_lower in entry_pattern):
            return True
    return False


def load_ignore_entries() -> list[dict]:
    """Load active (non-expired) ignore entries from Redis.

    Returns:
        List of dicts with 'pattern', 'ignored_until', 'reason' keys.
        Returns empty list if Redis is unavailable.
    """
    try:
        from models.reflections import ReflectionIgnore

        active = ReflectionIgnore.get_active()
        return [
            {
                "pattern": entry.pattern,
                "ignored_until": (str(entry.expires_at) if entry.expires_at else ""),
                "reason": entry.reason or "",
            }
            for entry in active
        ]
    except Exception as e:
        logger.warning(f"Could not load ignore entries: {e}")
        return []


def has_existing_github_work(pattern: str, cwd: str) -> bool:
    """Check if there's already an open issue or PR for this bug pattern.

    Args:
        pattern: Search term to look for in existing issues/PRs.
        cwd: Working directory for gh CLI commands.

    Returns:
        True if an open issue or PR already exists for this pattern.
    """
    search_term = pattern[:50]
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


def run_llm_reflection(analysis: dict[str, Any]) -> list[dict[str, str]]:
    """Run LLM reflection on session analysis using Claude Haiku.

    Args:
        analysis: Output from analyze_sessions_from_redis().

    Returns:
        List of reflection dicts with category, summary, pattern,
        prevention, source_session. Empty list on failure or skip.
    """
    import json as _json

    try:
        import anthropic
    except ImportError:
        anthropic = None  # type: ignore[assignment]

    if (
        analysis.get("sessions_analyzed", 0) == 0
        and not analysis.get("corrections")
        and not analysis.get("thrash_sessions")
    ):
        logger.info("No session findings for reflection, skipping LLM call")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("No ANTHROPIC_API_KEY set, skipping LLM reflection")
        return []

    if anthropic is None:
        logger.info("anthropic package not installed, skipping LLM reflection")
        return []

    from config.models import HAIKU

    prompt = f"""Analyze these session findings and categorize any mistakes or issues.

Session Analysis Data:
{_json.dumps(analysis, indent=2)}

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
        try:
            reflections = _json.loads(response_text)
            if isinstance(reflections, list):
                return reflections
        except _json.JSONDecodeError:
            match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if match:
                try:
                    reflections = _json.loads(match.group())
                    if isinstance(reflections, list):
                        return reflections
                except _json.JSONDecodeError:
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
    log_pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})" r".*?(ERROR|CRITICAL)\s*[-:]\s*(.*)"
    )

    # Hotfix (sibling of PR #1056): guard against unbounded log files. If the
    # file is over 50 MB, seek to the last 1 MB instead of loading the whole
    # thing into memory just to discard all but the last 1000 lines.
    max_bytes = 50 * 1024 * 1024
    tail_bytes = 1 * 1024 * 1024

    try:
        try:
            size = os.path.getsize(log_file)
        except OSError:
            size = 0

        if size > max_bytes:
            with open(log_file, "rb") as f:
                f.seek(-tail_bytes, os.SEEK_END)
                chunk = f.read()
            lines = chunk.decode("utf-8", errors="replace").splitlines(keepends=True)[-1000:]
        else:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-1000:]

        for i, line in enumerate(lines):
            match = log_pattern.search(line)
            if match:
                timestamp, level, message = match.groups()
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


def is_high_confidence(reflection: dict) -> bool:
    """Check if a reflection meets the 2-of-3 confidence criteria for auto-fix.

    Args:
        reflection: A reflection dict with category, prevention, pattern keys.

    Returns:
        True if at least 2 of 3 criteria are met.
    """
    criteria = [
        reflection.get("category") == "code_bug",
        bool(reflection.get("prevention", "").strip()),
        len(reflection.get("pattern", "")) >= 10,
    ]
    return sum(criteria) >= 2
