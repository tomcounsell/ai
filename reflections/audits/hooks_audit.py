"""reflections/audits/hooks_audit.py — Per-project Claude Code hooks audit.

What it does: Scans each project's `logs/hooks.log` for recent (last 24h)
    errors and validates its `.claude/settings.json` hook configuration
    (Stop/SubagentStop must carry `|| true`; hook script paths must exist).
Cadence: 86400s (hook config and log volume change slowly; daily catches drift)
Failure modes:
    - neither hooks.log nor settings.json present -> project skipped via skip_if
    - hooks.log scan exception -> logged, error_count left at last value
    - settings.json parse error -> logged, counted as one settings issue
Related reflections:
    - skills-audit: sibling per-project audit over the same project list
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from reflections.utilities import (
    PROJECT_ROOT,
    extract_structured_errors,
    run_per_project_audit,
)

logger = logging.getLogger("reflections.auditing")


def _hooks_audit_for_project(project: dict) -> dict:
    """Per-project body for hooks-audit.

    Scans the project's ``logs/hooks.log`` for recent errors AND validates
    its ``.claude/settings.json`` hook configuration. Both file paths are
    rooted at the project's working_directory.
    """
    import time as _time

    from bridge.utc import utc_now

    wd = project.get("working_directory", "")
    repo_root = Path(wd) if wd else PROJECT_ROOT

    findings: list[str] = []
    error_count = 0
    settings_issues = 0
    t0 = _time.time()

    hooks_log = repo_root / "logs" / "hooks.log"
    if hooks_log.exists():
        try:
            errors = extract_structured_errors(hooks_log)
            cutoff = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")
            recent = [e for e in errors if e.get("timestamp", "") >= cutoff]
            error_count = len(recent)
            if recent:
                hook_names = set()
                for e in recent:
                    msg = e.get("message", "")
                    parts = msg.split(" - ")
                    if parts:
                        hook_names.add(parts[0].strip())
                names = ", ".join(sorted(hook_names)) or "unknown"
                findings.append(f"{error_count} hook error(s) in last 24h from: {names}")
        except Exception as e:
            logger.warning(f"Failed to scan hooks.log: {e}")

    settings_path = repo_root / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})

            for event_type, matchers in hooks.items():
                for matcher_block in matchers:
                    for hook in matcher_block.get("hooks", []):
                        cmd = hook.get("command", "")
                        has_or_true = "|| true" in cmd

                        if event_type in ("Stop", "SubagentStop") and not has_or_true:
                            findings.append(f"FAIL: {event_type} hook missing || true: {cmd[:60]}")
                            settings_issues += 1

                        for part in cmd.replace("|| true", "").split():
                            if part.endswith(".py") or part.endswith(".sh"):
                                script_path = part.replace('"$CLAUDE_PROJECT_DIR"/', "").replace(
                                    "$CLAUDE_PROJECT_DIR/", ""
                                )
                                full_path = repo_root / script_path
                                if not full_path.exists():
                                    findings.append(f"WARN: Hook script not found: {script_path}")
                                    settings_issues += 1
                                break
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse settings.json: {e}")
            settings_issues += 1

    return {
        "status": "ok",
        "findings": findings,
        "summary": f"Hooks audit: {error_count} log errors, {settings_issues} settings issues",
        "duration": _time.time() - t0,
    }


def run() -> dict:
    """Audit Claude Code hooks per project.

    Iterates every local project with EITHER ``logs/hooks.log`` OR
    ``.claude/settings.json`` present (skips projects with neither).

    Hotfix (sibling of PR #1056): the per-project body does only sync I/O
    (``extract_structured_errors``, ``json.loads``, ``Path.read_text``,
    ``Path.exists``) with no awaits — see ``test_event_loop_safe_callables_are_sync``.
    """

    def skip_if(repo_root: Path) -> bool:
        return not (
            (repo_root / "logs" / "hooks.log").exists()
            or (repo_root / ".claude" / "settings.json").exists()
        )

    return run_per_project_audit(_hooks_audit_for_project, skip_if=skip_if, name="hooks-audit")
