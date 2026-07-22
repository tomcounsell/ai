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


def _migrate_schema_diet_fields(project_dir: Path) -> str | None:
    """Strip schema-diet (#1927) fields from existing AgentSession records.

    Plan #1927 pruned an accreted telemetry surface (self_report_sent_at,
    sdk_connection_torn_down_at, session_mode, the two transcript-path
    fields, the startup diagnostic pair, three write-only counters, and the
    four metered_* accounting fields) and applied one precision rename
    (watchdog_unhealthy -> unhealthy_reason). This runs the ORM-safe strip
    script (atomic delete+recreate per terminal record; idempotent — see
    scripts/migrate_schema_diet_fields.py). Returns None on success, error
    string on failure.
    """
    script = project_dir / "scripts" / "migrate_schema_diet_fields.py"
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


def _migrate_confirm_run_identity_fields_readable(project_dir: Path) -> str | None:
    """Confirm AgentSession.active_run_id + pr_number (issue #2003) read on legacy rows.

    One idempotent migration registering BOTH new nullable fields (Popoto
    rule: additive nullable fields need no backfill). Mirrors
    ``_migrate_confirm_issue_number_field_readable``: a read-only probe over a
    small sample of existing records proving Popoto's lazy-load descriptor
    healing resolves cleanly for rows written before the fields existed.
    Writes nothing. Returns None on success, error string on failure.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.agent_session import AgentSession

        for session in list(AgentSession.query.all())[:5]:
            _ = session.active_run_id  # noqa: B018 -- read-only healing probe
            _ = session.pr_number  # noqa: B018 -- read-only healing probe
        return None
    except Exception as e:
        return str(e)


def _migrate_confirm_is_ledger_field_readable(project_dir: Path) -> str | None:
    """Confirm AgentSession.is_ledger (issue #2042) is readable on legacy rows.

    Purely additive, defaulted (False) field -- no backfill is required.
    This is a read-only confirmation step: it loads a small sample of
    existing AgentSession records and accesses ``.is_ledger`` on each one to
    prove Popoto's lazy-load descriptor healing resolves cleanly for rows
    written before the field existed. Mirrors
    ``_migrate_confirm_issue_number_field_readable``. Writes nothing.
    Returns None on success (including "no sessions to check"), error
    string on unexpected failure.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.agent_session import AgentSession

        for session in list(AgentSession.query.all())[:5]:
            _ = session.is_ledger  # noqa: B018 -- read-only healing probe
        return None
    except Exception as e:
        return str(e)


def _migrate_backfill_pipeline_ledger(project_dir: Path) -> str | None:
    """Backfill non-terminal AgentSession.stage_states into the issue-keyed PipelineLedger.

    Issue #2012: the SDLC pipeline's stage/verdict/pr_number ledger moved
    from the ephemeral, executor-keyed ``AgentSession.stage_states`` to the
    durable, issue-keyed ``PipelineLedger`` (see ``agent/pipeline_ledger.py``
    and ``PipelineStateMachine.for_issue()`` in ``agent/pipeline_state.py``).
    This one-time backfill lifts any in-flight (non-terminal) session's
    ``stage_states`` blob into the ledger record for its issue, so a
    takeover after cutover reads the same progress the old session-keyed
    path would have shown.

    Scope is deliberately narrow (plan Open Question 2): only non-terminal
    sessions carrying a non-empty ``stage_states`` blob are considered. This
    is a live-issue lift, not a historical sweep -- terminal sessions'
    ledgers, if ever needed, are reconstructible from durable signals
    (verdicts/PR state), not from this migration.

    Keying (Risk 1 mitigation): ``target_repo`` is taken from the session's
    live issue lock (a ``peek`` of ``session:issuelock:{issue}``, which
    carries the lease-pinned value the rest of the pipeline already trusts)
    and, failing that, from the env-based ``_resolve_target_repo()`` ladder.
    A session for which BOTH resolution paths fail is skipped with a logged
    WARNING -- this migration never assembles a ``None:{issue}`` key.

    Idempotency (Race 2 mitigation): a target ledger is backfilled only when
    it is completely empty (``stage_states_json == "{}"`` and
    ``pr_number is None``). Any ledger that already carries content --
    whether from an earlier run of this same migration or from a live
    writer that got there first -- is left untouched. This also means a
    second run of this migration, or a run concurrent with a live writer,
    is a safe no-op for every session it has already touched.

    Uses ORM methods only (``AgentSession.query.all()``, ``PipelineLedger.
    get_or_create()``, ``.save()``) -- no raw Redis. Returns ``None`` on
    success (including "nothing to backfill"), an error string on
    unexpected failure. Never raises.
    """
    try:
        import logging
        import sys

        sys.path.insert(0, str(project_dir))
        from agent.pipeline_ledger import PipelineLedger
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES, touch_issue_lock
        from tools._sdlc_utils import _resolve_target_repo

        logger = logging.getLogger(__name__)

        for session in AgentSession.query.all():
            if getattr(session, "status", None) in TERMINAL_STATUSES:
                continue

            raw_stage_states = getattr(session, "stage_states", None)
            if not raw_stage_states:
                continue

            issue_number = getattr(session, "issue_number", None)
            if not issue_number:
                continue

            # Resolve target_repo: lease-pinned value first (authoritative,
            # matches what live writers/readers use), env-based fallback second.
            target_repo: str | None = None
            try:
                lock_result = touch_issue_lock(issue_number, run_id=None, peek=True)
                target_repo = lock_result.target_repo
            except Exception as e:  # swallow-ok: fall through to env resolution
                logger.warning(
                    "[migration:backfill_pipeline_ledger] issue lock peek failed for "
                    f"issue={issue_number} ({type(e).__name__}: {e}); "
                    "falling back to env resolution"
                )

            if not target_repo:
                target_repo = _resolve_target_repo()

            if not target_repo:
                logger.warning(
                    "[migration:backfill_pipeline_ledger] SKIP issue=%s session=%s -- "
                    "target_repo unresolvable (no live lease, env resolution failed); "
                    "never keying under None",
                    issue_number,
                    getattr(session, "session_id", "?"),
                )
                continue

            ledger = PipelineLedger.get_or_create(target_repo, issue_number)

            # Write-if-empty: read current ledger state and bail BEFORE any
            # mutation if it already carries content (Risk 1 -- never
            # overwrite a non-empty/newer ledger).
            ledger_is_empty = ledger.stage_states_json in (None, "{}") and ledger.pr_number is None
            if not ledger_is_empty:
                logger.info(
                    "[migration:backfill_pipeline_ledger] SKIP target_repo=%s issue=%s -- "
                    "ledger already has content, not overwriting",
                    target_repo,
                    issue_number,
                )
                continue

            try:
                parsed = (
                    json.loads(raw_stage_states)
                    if isinstance(raw_stage_states, str)
                    else raw_stage_states
                )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "[migration:backfill_pipeline_ledger] SKIP target_repo=%s issue=%s -- "
                    "unparseable stage_states (%s: %s)",
                    target_repo,
                    issue_number,
                    type(e).__name__,
                    e,
                )
                continue

            if not isinstance(parsed, dict):
                logger.warning(
                    "[migration:backfill_pipeline_ledger] SKIP target_repo=%s issue=%s -- "
                    "stage_states did not parse to a dict",
                    target_repo,
                    issue_number,
                )
                continue

            ledger.stage_states_json = json.dumps(parsed)
            session_pr_number = getattr(session, "pr_number", None)
            if session_pr_number:
                ledger.pr_number = session_pr_number
            ledger.save()

            logger.info(
                "[migration:backfill_pipeline_ledger] BACKFILLED target_repo=%s issue=%s "
                "from session=%s",
                target_repo,
                issue_number,
                getattr(session, "session_id", "?"),
            )

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
def _migrate_purge_phantom_agent_sessions(project_dir: Path) -> str | None:
    """Purge phantom AgentSession index-bookkeeping hashes from Redis (#2207).

    Runs scripts/purge_phantom_agent_sessions.py with a per-update time budget.
    Exit 0 = keyspace clean → migration recorded complete. Exit 3 = budget
    expired with phantoms remaining → returns an error string so the migration
    stays pending and resumes on the next update (the purge is idempotent and
    cursor-scan based, so partial progress is kept).
    """
    script = project_dir / "scripts" / "purge_phantom_agent_sessions.py"
    if not script.exists():
        return "purge script not found"

    budget_seconds = 900
    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script), "--max-seconds", str(budget_seconds), "--repair"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=budget_seconds + 120,
        )
        if result.returncode == 3:
            return "phantoms remain after time budget — will continue on next update"
        if result.returncode != 0:
            return f"exit code {result.returncode}: {result.stderr[-500:]}"
        return None
    except subprocess.TimeoutExpired:
        return f"purge timed out after {budget_seconds + 120}s"
    except Exception as e:
        return str(e)


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
    "confirm_run_identity_fields_readable": (
        _migrate_confirm_run_identity_fields_readable,
        "Confirm AgentSession.active_run_id + pr_number (issue #2003) read cleanly on legacy rows",
    ),
    "backfill_pipeline_ledger": (
        _migrate_backfill_pipeline_ledger,
        "Backfill non-terminal AgentSession.stage_states into the issue-keyed PipelineLedger",
    ),
    "confirm_is_ledger_field_readable": (
        _migrate_confirm_is_ledger_field_readable,
        "Confirm AgentSession.is_ledger (issue #2042) reads cleanly on legacy rows",
    ),
    "schema_diet_fields": (
        _migrate_schema_diet_fields,
        "Strip schema-diet (#1927) fields from existing AgentSession records",
    ),
    "purge_phantom_agent_sessions": (
        _migrate_purge_phantom_agent_sessions,
        "Purge phantom AgentSession index-bookkeeping hashes from Redis (#2207)",
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
