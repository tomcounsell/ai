"""reflections/audits/tech_debt_scan.py — Scan local projects for legacy code patterns.

What it does: Greps every local project's working_directory for TODO comments
    and deprecated typing imports (read-only; no writes).
Cadence: 86400s (daily) (tech debt accrues slowly; daily visibility suffices)
Failure modes:
    - grep returncode==2 (target removed mid-run) -> status="error" with stderr captured
    - missing working_directory -> per-project status="error"
Related reflections:
    - merged_branch_cleanup: also audits docs/plans for stale work items
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
import subprocess

from reflections.utilities import run_per_project_audit

logger = logging.getLogger("reflections.maintenance")


def _legacy_scan_for_project(project: dict) -> dict:
    """Per-project body for tech-debt-scan.

    Greps the project's working_directory for TODO comments and deprecated
    typing imports. ``grep -r`` returncode disambiguation:

    - 0  → matches found
    - 1  → no matches found (NOT an error)
    - 2  → error (target removed mid-run, permission denied, broken symlink)

    A returncode==2 surfaces as ``status="error"`` with stderr captured
    (first 200 chars).
    """
    import time as _time

    findings: list[str] = []
    wd = project.get("working_directory", "")
    if not wd:
        return {
            "status": "error",
            "findings": [],
            "summary": "missing working_directory",
            "duration": 0.0,
            "error": "missing working_directory",
        }

    t0 = _time.time()
    error_msg: str | None = None

    try:
        result = subprocess.run(
            ["grep", "-r", "TODO:", "--include=*.py", wd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 2:
            error_msg = (
                f"grep returned 2 (target may have been removed): stderr={result.stderr[:200]}"
            )
        elif result.returncode in (0, 1):
            todo_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
            if todo_count > 0:
                findings.append(f"Found {todo_count} TODO comments to review")
    except Exception as e:
        error_msg = f"TODO scan failed: {e}"
        logger.warning(error_msg)

    if error_msg is None:
        deprecated_patterns = [
            "from typing import Optional",
            "from typing import List",
            "from typing import Dict",
        ]
        for pattern in deprecated_patterns:
            try:
                result = subprocess.run(
                    ["grep", "-r", pattern, "--include=*.py", wd],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 2:
                    error_msg = (
                        f"grep returned 2 (target may have been removed): "
                        f"stderr={result.stderr[:200]}"
                    )
                    break
                if result.returncode in (0, 1) and result.stdout.strip():
                    count = len(result.stdout.strip().split("\n"))
                    findings.append(
                        f"Found {count} instances of deprecated typing import: {pattern}"
                    )
            except Exception as e:
                error_msg = f"Deprecated import scan failed for {pattern}: {e}"
                logger.warning(error_msg)
                break

    duration = _time.time() - t0
    if error_msg:
        return {
            "status": "error",
            "findings": findings,
            "summary": "tech-debt scan error",
            "duration": duration,
            "error": error_msg,
        }
    return {
        "status": "ok",
        "findings": findings,
        "summary": f"Legacy code scan: {len(findings)} finding(s)",
        "duration": duration,
    }


def run() -> dict:
    """Scan for legacy code patterns across all local projects.

    Iterates every project from ``load_local_projects()`` (no skip predicate
    — TODO/deprecated-typing checks apply to any Python repo) and aggregates
    findings with ``[slug]`` prefixes via :func:`run_per_project_audit`.
    """
    return run_per_project_audit(_legacy_scan_for_project, name="tech-debt-scan")
