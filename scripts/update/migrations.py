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


_SDLC_STUB_TEMPLATE = """\
# {name} addendum — this repo only
<!-- Do not duplicate content from the global skill.
     Only include what is unique to this repo. Max 300 lines. -->
"""

_SDLC_STUBS = [
    "do-plan",
    "do-plan-critique",
    "do-build",
    "do-test",
    "do-patch",
    "do-pr-review",
    "do-docs",
    "do-merge",
]


def _migrate_unify_parent_session_field(project_dir: Path) -> str | None:
    """Normalize parent_session_id Redis hash fields into parent_agent_session_id.

    Copies any leftover parent_session_id values into parent_agent_session_id where
    the latter is empty, then deletes the stale field. Idempotent — safe to re-run.
    Returns None on success, error string on failure.
    """
    script = project_dir / "scripts" / "migrate_unify_parent_session_field.py"
    if not script.exists():
        return "migration script not found"

    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script), "--apply"],
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
    except Exception as e:  # swallow-ok: error returned as string to caller for logging
        return str(e)


def _migrate_steering_queue_drain(project_dir: Path) -> str | None:
    """Drain residual AgentSession.queued_steering_messages into the Redis steering list.

    Returns None on success, error string on failure.
    """
    script = project_dir / "scripts" / "migrate_steering_queue_drain.py"
    if not script.exists():
        return "migration script not found"

    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script), "--apply"],
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


def _migrate_strip_pty_session_fields(project_dir: Path) -> str | None:
    """Strip removed PTY fields (+resume_handles) from existing AgentSession records.

    Plan #1924 task 5 removed dev_pid/pty_slot/last_pty_*/mid_run_*/
    role_transports/resume_handles from the model. This runs the ORM-safe
    strip script (atomic delete+recreate per terminal record; idempotent —
    see scripts/migrate_strip_pty_fields.py). Returns None on success,
    error string on failure.
    """
    script = project_dir / "scripts" / "migrate_strip_pty_fields.py"
    if not script.exists():
        return "migration script not found"

    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script), "--apply"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return f"exit code {result.returncode}: {result.stderr[-500:]}"
        return None
    except subprocess.TimeoutExpired:
        return "migration timed out after 300s"
    except Exception as e:  # swallow-ok: error returned as string to caller for logging
        return str(e)


def _migrate_confirm_issue_number_field_readable(project_dir: Path) -> str | None:
    """Confirm AgentSession.issue_number (issue #1954) is readable on legacy rows.

    Purely additive, nullable field -- no backfill is required. This is a
    read-only confirmation step: it loads a small sample of existing
    AgentSession records and accesses ``.issue_number`` on each one to prove
    Popoto's lazy-load descriptor healing resolves cleanly for rows written
    before the field existed. Writes nothing. Returns None on success
    (including "no sessions to check"), error string on unexpected failure.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.agent_session import AgentSession

        for session in list(AgentSession.query.all())[:5]:
            _ = session.issue_number  # noqa: B018 -- read-only healing probe
        return None
    except Exception as e:
        return str(e)


def _migrate_create_sdlc_stubs(project_dir: Path) -> str | None:
    """Create docs/sdlc/ stub files if missing.

    Idempotent — only creates files that do not yet exist.
    Returns None on success, error string on failure.
    """
    try:
        sdlc_dir = project_dir / "docs" / "sdlc"
        sdlc_dir.mkdir(parents=True, exist_ok=True)
        for name in _SDLC_STUBS:
            path = sdlc_dir / f"{name}.md"
            if not path.exists():
                path.write_text(_SDLC_STUB_TEMPLATE.format(name=name))
        return None
    except Exception as e:
        print(f"[migration] create_sdlc_stubs failed: {e}")
        return str(e)


# Migration name -> (function, description)
MIGRATIONS: dict[str, tuple[callable, str]] = {
    "agent_session_keyfield_rename": (
        _migrate_agent_session_keyfield_rename,
        "Rename AgentSession job_id/parent_job_id KeyFields in Redis",
    ),
    "create_sdlc_stubs": (
        _migrate_create_sdlc_stubs,
        "Create docs/sdlc/ per-stage addendum stub files",
    ),
    "unify_parent_session_field": (
        _migrate_unify_parent_session_field,
        "Normalize parent_session_id Redis fields into parent_agent_session_id",
    ),
    "steering_queue_drain": (
        _migrate_steering_queue_drain,
        "Drain residual AgentSession.queued_steering_messages into the Redis steering list",
    ),
    "strip_pty_session_fields": (
        _migrate_strip_pty_session_fields,
        "Strip removed PTY fields (+resume_handles) from existing AgentSession records",
    ),
    "confirm_issue_number_field_readable": (
        _migrate_confirm_issue_number_field_readable,
        "Confirm AgentSession.issue_number (issue #1954) reads cleanly on legacy rows",
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
