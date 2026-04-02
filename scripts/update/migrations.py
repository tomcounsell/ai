"""One-time data migrations for update system.

Each migration is a function that:
- Checks if it needs to run (idempotent)
- Runs the migration if needed
- Returns a MigrationResult

Migrations are keyed by name. Once a migration completes successfully,
its name is recorded in data/migrations_completed.json so it won't run again.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MigrationResult:
    """Result of running all pending migrations."""

    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _get_completed_path(project_dir: Path) -> Path:
    return project_dir / "data" / "migrations_completed.json"


def _load_completed(project_dir: Path) -> set[str]:
    path = _get_completed_path(project_dir)
    if path.exists():
        try:
            return set(json.loads(path.read_text()))
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def _save_completed(project_dir: Path, completed: set[str]) -> None:
    path = _get_completed_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(completed), indent=2) + "\n")


# ── Migration registry ──────────────────────────────────────────────


def _migrate_agent_session_keyfield_rename(project_dir: Path) -> str | None:
    """Rename AgentSession job_id->id and parent_job_id->parent_agent_session_id in Redis.

    Returns None on success, error string on failure.
    """
    script = project_dir / "scripts" / "migrate_agent_session_keyfield_rename.py"
    if not script.exists():
        return "migration script not found"

    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script)],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return f"exit code {result.returncode}: {result.stderr[-500:]}"
        return None
    except subprocess.TimeoutExpired:
        return "migration timed out after 120s"
    except Exception as e:
        return str(e)


# Migration name -> (function, description)
MIGRATIONS: dict[str, tuple[callable, str]] = {
    "agent_session_keyfield_rename": (
        _migrate_agent_session_keyfield_rename,
        "Rename AgentSession job_id/parent_job_id KeyFields in Redis",
    ),
}


# ── Public API ───────────────────────────────────────────────────────


def run_pending_migrations(project_dir: Path) -> MigrationResult:
    """Run all pending migrations in order."""
    result = MigrationResult()
    completed = _load_completed(project_dir)

    for name, (fn, description) in MIGRATIONS.items():
        if name in completed:
            result.skipped.append(name)
            continue

        error = fn(project_dir)
        if error is None:
            result.ran.append(name)
            completed.add(name)
            _save_completed(project_dir, completed)
        else:
            result.failed.append(name)
            result.errors.append(f"{name}: {error}")

    return result
