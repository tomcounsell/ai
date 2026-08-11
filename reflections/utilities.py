"""
reflections/utilities.py — Shared helpers for all reflection callables.

Genuinely shared logic only: ``load_local_projects`` (5+ callers),
``run_per_project_audit`` (the per-project audit harness), ``run_llm_reflection``
(wraps the Anthropic SDK + model config), and the ignore/confidence/log-parsing
helpers used across audit reflections. All helpers are pure functions with no
shared mutable state.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger("reflections.utilities")

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


def resolve_projects_config_path() -> Path:
    """Resolve the projects.json this machine should read.

    Precedence: ``PROJECTS_CONFIG_PATH`` env override →
    ``~/Desktop/Valor/projects.json`` (the iCloud-synced private vault copy) →
    the in-repo ``config/projects.json`` fallback.

    The returned path is not guaranteed to exist; callers decide how to treat a
    missing config. Single resolver so every reader agrees on which file is
    authoritative.
    """
    config_path = Path(
        os.environ.get(
            "PROJECTS_CONFIG_PATH",
            str(Path.home() / "Desktop" / "Valor" / "projects.json"),
        )
    ).expanduser()
    if not config_path.exists():
        config_path = AI_ROOT / "config" / "projects.json"
    return config_path


def machine_owns_project(project_key: str | None) -> bool:
    """Return True if THIS machine owns ``project_key`` per ``projects.json``.

    Single-machine invariant: an automated reflection acts on a project's
    sessions only when ``projects.<project_key>.machine == computer_name()``.
    Making ownership structural (rather than relying on the operator enabling a
    flag on exactly one box) means exactly one machine acts on a given lane even
    if the reflection is enabled fleet-wide.

    Fail-soft: an unresolvable / unknown / missing ``project_key`` → False
    (treated as not-owned, so the caller falls back to propose-only — the safe
    default). Any lookup error is swallowed and returns False.
    """
    if not project_key:
        return False
    try:
        from config.machine import get_machine_name
        from tools.reflection_machine_filter import _load_project_machines

        owners = _load_project_machines(resolve_projects_config_path())
        owner = owners.get(project_key)
        if not owner:
            return False
        return owner == get_machine_name().strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.debug("machine_owns_project swallowed exception: %r", exc)
        return False


def load_local_projects() -> list[dict]:
    """Load projects from projects.json, filtered to those present on this machine.

    Loads from ~/Desktop/Valor/projects.json (iCloud-synced, private).
    Falls back to legacy in-repo config path if the Desktop path doesn't exist.

    Returns:
        List of project dicts, each including a 'slug' key derived from the
        projects.json key. Only projects whose working_directory exists on disk
        are returned.
    """
    config_path = resolve_projects_config_path()
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


def run_per_project_audit(
    audit_one: Callable[[dict], dict],
    *,
    skip_if: Callable[[Path], bool] | None = None,
    name: str,
) -> dict:
    """Iterate `load_local_projects()` and run a per-project audit body.

    Aggregates findings across all qualifying projects, prefixing each with
    ``[slug] ``. Both the ``skip_if(repo_root)`` predicate AND the
    ``audit_one(project)`` call are wrapped in a single ``try/except`` per
    project, so a network-mount race or permission error on one project
    cannot abort the whole audit.

    Args:
        audit_one: sync callable taking the full project dict and returning
            ``{status, findings, summary, duration}``. Must NOT be a coroutine.
        skip_if: optional sync predicate taking the project's working dir as
            ``Path``; return ``True`` to silently skip.
        name: stable audit identifier (matches ``name:`` in the registry YAML).
            Used in the aggregate ``summary`` string and log lines — not decorative.

    Returns:
        ``{status, findings, summary, projects: [{slug, status, duration,
        findings_count, error}]}``. Per-project ``status`` is one of
        ``"ok" | "error" | "skipped" | "disabled"``.

        Aggregate ``status`` rules:
        - any ``error`` → ``"error"``
        - all ``disabled`` (and at least one project) → ``"disabled"``
        - otherwise → ``"ok"`` (covers all-ok, all-skipped, ok+skipped,
          ok+disabled, no-projects)
    """
    projects = load_local_projects()
    if not projects:
        return {
            "status": "ok",
            "findings": [],
            "summary": f"{name}: no qualifying projects",
            "projects": [],
        }

    findings: list[str] = []
    project_records: list[dict] = []
    scanned = 0
    skipped = 0
    errored = 0
    disabled = 0

    for project in projects:
        slug = project.get("slug", "?")
        wd = project.get("working_directory", "")
        repo_root = Path(wd) if wd else Path()

        try:
            if skip_if is not None and skip_if(repo_root):
                project_records.append(
                    {
                        "slug": slug,
                        "status": "skipped",
                        "duration": 0.0,
                        "findings_count": 0,
                        "error": None,
                    }
                )
                skipped += 1
                continue

            t0 = time.time()
            result = audit_one(project)
            elapsed = time.time() - t0

            if not isinstance(result, dict):
                raise TypeError(f"audit_one returned {type(result).__name__}, expected dict")

            per_status = result.get("status", "ok")
            per_findings = result.get("findings", []) or []
            per_duration = float(result.get("duration", elapsed))
            per_error = result.get("error")
            if per_error is not None:
                per_error = str(per_error)[:500]

            if per_status == "disabled":
                disabled += 1
            elif per_status == "error":
                errored += 1
            else:
                scanned += 1

            if per_status != "skipped":
                for f in per_findings:
                    findings.append(f"[{slug}] {f}")

            project_records.append(
                {
                    "slug": slug,
                    "status": per_status,
                    "duration": per_duration,
                    "findings_count": len(per_findings),
                    "error": per_error,
                }
            )
        except Exception as exc:
            err_str = f"{type(exc).__name__}: {exc}"
            logger.warning("[%s] per-project audit failed for %s: %s", name, slug, err_str)
            project_records.append(
                {
                    "slug": slug,
                    "status": "error",
                    "duration": 0.0,
                    "findings_count": 0,
                    "error": err_str[:500],
                }
            )
            errored += 1

    if errored > 0:
        agg_status = "error"
    elif disabled > 0 and scanned == 0 and skipped == 0:
        agg_status = "disabled"
    else:
        agg_status = "ok"

    summary = f"{name}: {scanned} project(s) scanned, {skipped} skipped, {errored} error(s)"
    if disabled:
        summary += f", {disabled} disabled"

    return {
        "status": agg_status,
        "findings": findings,
        "summary": summary,
        "projects": project_records,
    }


# --- Shared Redis client + issue-lock liveness ------------------------------
#
# Moved here from ``reflections/sdlc_progress.py`` (issue #2717) so a sibling
# reflection (``reflections/sdlc_upvote_lanes.py``) can read the same
# liveness signal without forking the rule. ``_lock_says_live``'s docstring
# states the invariant it protects: classification of the lock payload is
# delegated to ``models.session_lifecycle._lock_owner_is_live`` so this
# module never diverges from the canonical liveness rule. A copy in a
# sibling module would be exactly that fork. ``sdlc_progress`` re-exports
# both names (``from reflections.utilities import _LOCK_KEY,
# _lock_says_live``) so its own call sites and ``sdlc_progress._LOCK_KEY``
# keep resolving unchanged.


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import, error-tolerant)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


_LOCK_KEY = "session:issuelock:{issue_number}"


def _lock_says_live(issue_number: int) -> bool | None:
    """True if a live run owns this issue's lock, False if free, None if unknown.

    Deliberately a direct ``GET`` on the lock key rather than a call to
    ``models.session_lifecycle.touch_issue_lock``. ``touch_issue_lock`` fails
    OPEN — every Redis exception is swallowed and returned as
    ``IssueLockResult(acquired=True)``, which this gate would read as "lock
    unheld → not live → act". A flap would therefore push a caller INTO
    acting, exactly when it should be most reluctant. A bare ``GET`` also
    cannot claim or renew a lease, which is a stronger read-only guarantee than
    any argument pair.

    Classification of the payload is delegated to ``_lock_owner_is_live`` (the
    same helper ``touch_issue_lock`` uses) so this module never forks the
    liveness rule. A malformed payload lands in the ``except`` and reads as
    *unknown*, not as free.
    """
    try:
        from models.session_lifecycle import _lock_owner_is_live

        raw = _get_redis().get(_LOCK_KEY.format(issue_number=issue_number))
        if raw is None:
            return False  # no lock -> nobody owns the lane
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return bool(_lock_owner_is_live(json.loads(raw)))
    except Exception as exc:
        logger.warning("reflections.utilities: issue-lock read failed for #%s: %s", issue_number, exc)
        return None  # unknown -> caller declines to act


def resolve_eng_group(project: dict) -> tuple[str, int] | None:
    """Resolve a project's ``Eng: X`` Telegram group name and numeric chat id.

    Scans ``project["telegram"]["groups"]`` for keys carrying the literal
    ``Eng:`` prefix (matching ``bridge/routing.py``'s ``chat_title.startswith
    ("Eng:")`` fallback that grants the engineer persona) and returns
    ``(group_name, chat_id)`` for the first match. Returns ``None`` on any
    absence or malformation — missing ``telegram`` key, missing ``groups``
    key, a non-dict ``groups`` value, no key with the ``Eng:`` prefix, a
    group entry that is not a dict, a missing ``chat_id``, or a non-integer
    ``chat_id``. There is deliberately no substring fallback and no default
    chat: a project with no properly-configured ``Eng:`` group (e.g.
    ``royop`` at plan time) must be skipped by the caller, never routed
    somewhere plausible-looking.
    """
    try:
        groups = (project.get("telegram") or {}).get("groups")
        if not isinstance(groups, dict):
            return None
        for group_name, group_cfg in groups.items():
            if not isinstance(group_name, str) or not group_name.startswith("Eng:"):
                continue
            if not isinstance(group_cfg, dict):
                return None
            chat_id = group_cfg.get("chat_id")
            if not isinstance(chat_id, int) or isinstance(chat_id, bool):
                return None
            return group_name, chat_id
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolve_eng_group swallowed exception: %r", exc)
        return None


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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=cwd,
            )
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

    if analysis.get("sessions_analyzed", 0) == 0 and not analysis.get("corrections"):
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
    """Check if a reflection clears the high-confidence gate for auto-fix.

    The gate requires the reflection be a ``code_bug`` AND carry at least one
    supporting signal (non-empty prevention, or a pattern of >=10 chars). This
    deliberately excludes non-code-bug categories (e.g. ``poor_planning``,
    ``process_gap``) from the auto-fix path even when they have rich prevention
    and pattern text — those describe agent behaviour, not code defects, and
    must not ride into the auto-filer on length alone (see #1414).

    Args:
        reflection: A reflection dict with category, prevention, pattern keys.

    Returns:
        True only if category == "code_bug" AND (prevention OR pattern>=10).
    """
    if reflection.get("category") != "code_bug":
        return False
    return (
        bool(reflection.get("prevention", "").strip()) or len(reflection.get("pattern", "")) >= 10
    )
