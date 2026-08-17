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
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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


# ── Shared subprocess runner ────────────────────────────────────────


def _run_migration_script(
    project_dir: Path,
    script_name: str,
    *,
    label: str,
    args: tuple[str, ...] = (),
    timeout: int = 300,
) -> str | None:
    """Run one migration script as a subprocess and LOG WHAT IT SAID.

    Discarding a migration's output is what made "did the strip actually do
    anything?" answerable only by forensics: a migration whose output is thrown
    away has a failure mode indistinguishable from its success mode. So the
    output is logged on the SUCCESS path too, not only on failure.

    Both streams are logged because a script's own record could move between
    them. The strip migrations log to stdout deliberately
    (``basicConfig(stream=sys.stdout)``), but that is one line away from being
    lost in a refactor, and Python's default is stderr -- a stdout-only capture
    would then faithfully record an empty string while looking healthy.

    For the same reason the error string carries BOTH tails: a stderr-only
    reason is empty for every failure of a script that logs to stdout, which is
    how an operator ends up reading ``exit code 2:`` with no explanation.

    Returns None on success, an error string on failure -- the ``MIGRATIONS``
    value contract is unchanged, because the output goes to the logger rather
    than through the return value.
    """
    script = project_dir / "scripts" / script_name
    if not script.exists():
        return "migration script not found"

    python = project_dir / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python), str(script), *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        for stream_name, stream in (("stdout", result.stdout), ("stderr", result.stderr)):
            for line in (stream or "").splitlines():
                if line.strip():
                    logger.info("[migration:%s] %s: %s", label, stream_name, line)
        if result.returncode != 0:
            return (
                f"exit code {result.returncode}: "
                f"stdout={result.stdout[-500:]!r} stderr={result.stderr[-500:]!r}"
            )
        return None
    except subprocess.TimeoutExpired:
        return f"migration timed out after {timeout}s"
    except Exception as e:  # swallow-ok: error returned as string to caller for logging
        return str(e)


# ── Migration registry ──────────────────────────────────────────────


def _migrate_agent_session_keyfield_rename(project_dir: Path) -> str | None:
    """Rename AgentSession job_id->id and parent_job_id->parent_agent_session_id in Redis.

    Returns None on success, error string on failure.
    """
    # Inherits the module's 300s default rather than opting down to 120s.
    # Since #2544 this script reconstructs indexes through repair_indexes(),
    # which runs the $IndexF stale-pointer scan ON TOP OF a rebuild measured at
    # 22.33s for 4006 rows (#2549) -- and that rebuild grows superlinearly:
    # 4x the rows from 1000 to 4006 cost 19x the wall clock. The two rename
    # migrations do the most work of any entry here, so they should not be the
    # ones on the tightest budget. A kill mid-rebuild leaves the indexes torn
    # down until the worker's startup repair, so the headroom is worth having.
    return _run_migration_script(
        project_dir,
        "migrate_agent_session_keyfield_rename.py",
        label="agent_session_keyfield_rename",
    )


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
    # Inherits the module's 300s default -- same reasoning as
    # _migrate_agent_session_keyfield_rename above (#2544 moved this onto
    # repair_indexes(), so the budget now has to span an $IndexF scan plus a
    # rebuild whose duration grows superlinearly with the row count).
    return _run_migration_script(
        project_dir,
        "migrate_unify_parent_session_field.py",
        label="unify_parent_session_field",
        args=("--apply",),
    )


def _migrate_steering_queue_drain(project_dir: Path) -> str | None:
    """Drain residual AgentSession.queued_steering_messages into the Redis steering list.

    Returns None on success, error string on failure.
    """
    return _run_migration_script(
        project_dir,
        "migrate_steering_queue_drain.py",
        label="steering_queue_drain",
        args=("--apply",),
        timeout=120,
    )


def _migrate_strip_pty_session_fields(project_dir: Path) -> str | None:
    """Strip removed PTY fields (+resume_handles) from existing AgentSession records.

    Plan #1924 task 5 removed dev_pid/pty_slot/last_pty_*/mid_run_*/
    role_transports/resume_handles from the model. This runs the ORM-safe
    strip script (atomic delete+recreate per terminal record; idempotent —
    see scripts/migrate_strip_pty_fields.py). Returns None on success,
    error string on failure.
    """
    return _run_migration_script(
        project_dir,
        "migrate_strip_pty_fields.py",
        label="strip_pty_session_fields",
        args=("--apply",),
    )


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
    return _run_migration_script(
        project_dir,
        "migrate_schema_diet_fields.py",
        label="schema_diet_fields",
        args=("--apply",),
    )


def _migrate_strip_pid_fields(project_dir: Path) -> str | None:
    """Strip removed pid fields (+expectations) from existing AgentSession records.

    The durability-room-job-agentrun plan (Milestone 1) replaced the raw pid
    fields with a fenced execution record and removed claude_pid/pm_pid/
    harness_pid/expectations from the model. This runs the ORM-safe strip
    script (atomic delete+recreate per TERMINAL record only; non-terminal rows
    are deferred, not aged out; idempotent -- see
    scripts/migrate_strip_pid_fields.py).

    Output capture is no longer special-cased here: every subprocess-shaped
    migration goes through ``_run_migration_script``, which logs both streams
    on the success path too (#2524). Widening the ``MIGRATIONS`` value contract
    turned out to be unnecessary -- the output goes to the module logger, so
    the ``None | error-string`` return is untouched.

    Returns None on success, error string on failure.
    """
    return _run_migration_script(
        project_dir,
        "migrate_strip_pid_fields.py",
        label="strip_pid_fields",
        args=("--apply",),
    )


def _migrate_strip_pty_session_fields_v2(project_dir: Path) -> str | None:
    """Re-run the PTY-field strip (#2524) under a distinguishable log label.

    Same script, same arguments as the v1 registration. It exists as its own
    function purely so the captured lines in ``logs/update.log`` say WHICH
    registration produced them. The whole point of the rename-and-rerun is
    auditability -- a log line that cannot be attributed to the re-run does not
    deliver it.

    ``strip_pid_fields_v2`` deliberately does NOT get this treatment: it is
    already recorded complete across the fleet and its captured output is the
    #2518 canary's gate artifact, so changing its label would move a string
    someone is reading.
    """
    return _run_migration_script(
        project_dir,
        "migrate_strip_pty_fields.py",
        label="strip_pty_session_fields_v2",
        args=("--apply",),
    )


def _migrate_schema_diet_fields_v2(project_dir: Path) -> str | None:
    """Re-run the schema-diet strip (#2524) under a distinguishable log label.

    See ``_migrate_strip_pty_session_fields_v2`` for why this is a separate
    function rather than a second registry entry pointing at the v1 helper.
    """
    return _run_migration_script(
        project_dir,
        "migrate_schema_diet_fields.py",
        label="schema_diet_fields_v2",
        args=("--apply",),
    )


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


def _migrate_confirm_pipeline_ledger_slug_readable(project_dir: Path) -> str | None:
    """Confirm PipelineLedger.slug (issues #2735/#2718) is readable on legacy rows.

    Purely additive, nullable field -- no backfill is required, and none is
    performed. A backfill would have to *guess* the identity of a lane with
    no branch and no PR, writing exactly the wrong-identity records the lane
    slug exists to prevent; those lanes heal on their next lane-start call
    instead. This is a read-only confirmation step: it loads a small sample
    of existing ledger records and accesses ``.slug`` on each one to prove
    Popoto's lazy-load descriptor healing resolves cleanly for rows written
    before the field existed. Mirrors
    ``_migrate_confirm_is_ledger_field_readable``. Writes nothing.
    Returns None on success (including "no ledgers to check"), error string
    on unexpected failure.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from agent.pipeline_ledger import PipelineLedger

        for ledger in list(PipelineLedger.query.all())[:5]:
            _ = ledger.slug  # noqa: B018 -- read-only healing probe
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
# The 3 legacy hook entries actually deployed to every machine's
# ~/.claude/settings.json by the old hardcoded `_SDLC_HOOK_DEFS` path (see
# docs/plans/hook-registration-manifest-dispatcher.md "Deployed-reality
# baseline"). Each tuple is (legacy script path relative to
# ~/.claude/hooks/, manifest_id of the new global-scope declaration that
# replaces it). The legacy script *names* are intentionally the OLD deployed
# names (e.g. "sdlc/validate_commit_message.py", not the renamed
# "sdlc/validate_commit_message_sdlc.py") -- that is what is actually on disk
# on every fleet machine today -- they are the *match* key only.
#
# The replacement command is always rebuilt from the manifest declaration
# (see _build_hook_command below), never copied from the legacy string:
# sync_claude_dirs (run.py Step 1.5) runs BEFORE run_pending_migrations
# (Step 3.6), so by the time this migration executes the rename has already
# landed and RENAMED_REMOVALS has deleted the old hardlink. Writing the
# legacy path back would point a blocking PreToolUse/Bash hook at a missing
# file and block every Bash call fleet-wide.
_LEGACY_FORK_HOOKS: tuple[tuple[str, str], ...] = (
    ("sdlc/validate_commit_message.py", "validate_commit_message_fork"),
    ("sdlc/sdlc_reminder.py", "sdlc_reminder_fork"),
    ("sdlc/validate_sdlc_on_stop.py", "validate_sdlc_on_stop_fork"),
)


def _legacy_fork_command_prefix(legacy_script: str) -> str:
    """Build the exact legacy (pre-manifest) command string for one fork script.

    The bare ``python`` here is a MATCH KEY against bytes already on disk in
    each machine's ``~/.claude/settings.json``, NOT generated output. It must
    never be updated to track the generator's interpreter token (issue #2503):
    the generator now emits the resolved global interpreter, but the legacy
    entries this matches were written with a literal ``python``, so changing
    this string would silently stop matching them and orphan the three fork
    entries on every machine that has not yet re-run ``/update``. This is the
    one place in the codebase where the string ``python `` must survive, and the
    bare-``python`` ban test exempts it explicitly.
    """
    hooks_root = Path.home() / ".claude" / "hooks"
    return f"python {hooks_root}/{legacy_script}"


# Pre-manifest global registration entries in ~/.claude/settings.json that must
# be swept out (issue #2503, spike-3). Each is a script path relative to
# ~/.claude/hooks/ that the old hardcoded `_SDLC_HOOK_DEFS` path registered as an
# unmarked global entry, but which the manifest now declares `scope = "project"`
# (so its logic already runs in-process via dispatch/pre_tool_use_bash.py). The
# stale global entry fires on every Bash call in every repo and crashes under the
# oldest system interpreter (PEP 604 at line 107).
#
# This is an EXPLICIT, PRECISE-MATCH allowlist -- mirroring _LEGACY_FORK_HOOKS's
# discipline -- deliberately NOT a `validators/`-prefix or `~/.claude/hooks/`-prefix
# test. A prefix gate would silently deregister a hand-added user hook that happens
# to live inside ~/.claude/hooks/; only the exact scripts named here are removed.
_LEGACY_UNMARKED_SWEEP_TARGETS: tuple[str, ...] = ("validators/validate_no_raw_redis_delete.py",)


def _migrate_sweep_legacy_unmarked_global_hooks(project_dir: Path) -> str | None:
    """Remove stale pre-manifest, unmarked global hook registrations (#2503).

    Spike-3 found the live ``~/.claude/settings.json`` carries an UNMARKED
    pre-manifest entry that the marker-keyed
    ``_migrate_hook_registration_manifest_ids`` path is structurally unable to
    touch (it indexes only entries whose command carries a ``# hook:<id>``
    marker). The entry --

        PreToolUse / matcher "Bash" / timeout 5
        python ~/.claude/hooks/validators/validate_no_raw_redis_delete.py

    -- fires on every Bash call in every repo and crashes under the oldest
    system interpreter (PEP 604 syntax at line 107). The manifest declares that
    validator ``scope = "project"`` and its logic already runs in-process via
    ``dispatch/pre_tool_use_bash.py``, so the correct fix is REMOVING THE STALE
    REGISTRATION.

    An entry is removed only when ALL THREE hold:
      (a) its command references the expanded ``~/.claude/hooks/`` directory,
      (b) it carries NO ``# hook:`` marker (unmarked -> pre-manifest; the marked
          path is already converged by the sibling migration), and
      (c) its resolved script path is one of ``_LEGACY_UNMARKED_SWEEP_TARGETS``.

    Every entry that does not reference ``~/.claude/hooks/`` is left completely
    untouched -- including ``bash .../calendar_prompt_hook.sh`` and any
    hand-added user hook. A marked entry, or one referencing a script NOT in the
    precise-match allowlist, is likewise left untouched.

    REGISTRATION-ONLY: this deletes NO files. Pruning the orphaned files under
    ``~/.claude/hooks/`` is deferred to issue #2521; once this registration is
    gone those files are inert.

    A timestamped ``.bak`` snapshot is written before any change and the result
    is asserted JSON-valid before it ever touches disk -- matching
    ``_migrate_hook_registration_manifest_ids``.

    Idempotent: a settings.json with no matching entry is a no-op. Handles an
    absent file, an empty ``{}``, or a missing ``hooks`` key as no-ops with no
    crash and no file written.

    Returns None on success (including "nothing to sweep"), an error string on
    failure.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return None

    try:
        raw = settings_path.read_text()
    except OSError as e:
        return f"failed to read {settings_path}: {e}"

    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"failed to parse {settings_path} as JSON: {e}"

    if not isinstance(settings, dict):
        return f"{settings_path} does not contain a JSON object at the top level"

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return None  # nothing registered at all -- nothing to sweep

    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from scripts.update.hardlinks import _extract_manifest_id
    except Exception as e:
        return f"failed to import hook manifest machinery: {e}"

    hooks_root = str(Path.home() / ".claude" / "hooks")
    target_commands = tuple(f"{hooks_root}/{t}" for t in _LEGACY_UNMARKED_SWEEP_TARGETS)

    def _should_remove(command: object) -> bool:
        # ~/.claude/settings.json is hand-editable, so a "command" arrives as
        # null, a number, or a list often enough to matter. The `""` default on
        # the caller's .get() never fires for a present `"command": null`, and
        # `run_pending_migrations` calls this with no try/except, so an
        # unguarded membership test raises TypeError out of /update. Anything
        # that is not a string matches no target and is left alone.
        if not isinstance(command, str):
            return False
        # (a) references the expanded ~/.claude/hooks/ directory
        if f"{hooks_root}/" not in command:
            return False
        # (b) carries no # hook: marker (marked entries are already converged)
        if _extract_manifest_id(command):
            return False
        # (c) resolved script path is in the precise-match allowlist
        return any(tc in command for tc in target_commands)

    changed = False
    for block in _iter_hook_blocks(hooks):
        entries = block.get("hooks")
        if not isinstance(entries, list):
            continue
        surviving = [e for e in entries if not _should_remove(e.get("command", ""))]
        if len(surviving) != len(entries):
            block["hooks"] = surviving
            changed = True

    # Prune any block/event emptied by the sweep.
    for event in list(hooks.keys()):
        if not isinstance(hooks[event], list):
            continue
        hooks[event] = [b for b in hooks[event] if b.get("hooks")]
        if not hooks[event]:
            del hooks[event]

    if not changed:
        return None

    try:
        serialized = json.dumps(settings, indent=2) + "\n"
        json.loads(serialized)  # validity assertion before ever touching disk
    except (TypeError, ValueError) as e:
        return f"post-sweep settings failed JSON validity check, aborting write: {e}"

    backup_path = settings_path.parent / (
        f"{settings_path.name}.bak.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    try:
        backup_path.write_text(raw)
    except OSError as e:
        return f"failed to write backup {backup_path}: {e}"

    try:
        settings_path.write_text(serialized)
    except OSError as e:
        return f"failed to write {settings_path}: {e}"

    return None


def _migrate_hook_registration_manifest_ids(project_dir: Path) -> str | None:
    """Rewrite the 3 deployed legacy SDLC-fork hook entries in place (Bug 1).

    Pre-requisite Bug 1 + the plan's "Legacy exact-command fallback": every
    machine's ``~/.claude/settings.json`` holds 3 hook entries written by the
    old hardcoded ``_SDLC_HOOK_DEFS`` path -- no ``manifest_id`` marker, the
    Stop entry missing its ``|| true`` guard, and ``sdlc_reminder.py``
    collapsed to a single ``Edit``-only block (a command-string dedupe quirk
    silently dropped its intended ``Write`` coverage). The new
    ``manifest_id``-keyed merge in
    ``scripts/update/hardlinks.py::_merge_hook_settings`` cannot recognize
    these legacy entries (they carry no marker) -- without this migration it
    would append fresh manifest-tagged copies alongside them, duplicating
    every SDLC hook fleet-wide.

    This migration finds each legacy entry by its literal (pre-manifest)
    command string -- keyed on the command **path**, ignoring the on-disk
    matcher -- and rewrites it in place:
      - embeds the correct ``# hook:<manifest_id>`` marker so future runs
        update-in-place instead of appending a duplicate,
      - applies the shell suffix the manifest's ``exit_policy`` calls for
        (restores the long-missing guard on ``validate_sdlc_on_stop.py``),
      - upgrades ``sdlc_reminder.py``'s matcher from the deployed ``Edit``-only
        to ``Write|Edit``, restoring the coverage the old dedupe silently
        dropped.
    Also collapses any accidental duplicate for one of these 3 manifest_ids
    (e.g. if a regular ``/update`` hardlink sync already ran once this run and
    appended a fresh marked copy before this migration got a chance to claim
    the legacy one) down to a single entry -- this keeps the "exactly one
    entry per event" guarantee regardless of step ordering within a single
    ``/update`` invocation.

    Never touches entries with no legacy-fork match: any pre-existing,
    non-SDLC user hook is left completely untouched.

    A timestamped ``.bak`` snapshot of ``~/.claude/settings.json`` is written
    before any change (Risk 1 rollback path). The rewrite is a
    parse -> modify -> serialize round-trip with a JSON-validity assertion
    before the write ever happens -- an invalid result is never written.

    Idempotent: a settings.json where every legacy entry already carries its
    manifest_id marker (and no duplicates exist) is a no-op.

    Returns None on success (including "nothing to migrate" -- e.g. a fresh
    machine with no ``~/.claude/settings.json`` yet, or the manifest no
    longer declaring one of the fork ids), an error string on failure.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return None

    try:
        raw = settings_path.read_text()
    except OSError as e:
        return f"failed to read {settings_path}: {e}"

    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"failed to parse {settings_path} as JSON: {e}"

    if not isinstance(settings, dict):
        return f"{settings_path} does not contain a JSON object at the top level"

    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from scripts.update.hardlinks import (
            _build_hook_command,
            _extract_manifest_id,
            resolve_global_interpreter,
        )
        from scripts.update.hook_manifest import load_hook_manifest
    except Exception as e:
        return f"failed to import hook manifest machinery: {e}"

    try:
        manifest = load_hook_manifest(project_dir / ".claude" / "hooks" / "manifest.toml")
    except Exception as e:
        return f"failed to load hook manifest: {e}"

    # These entries live in ~/.claude/settings.json (global scope), so they must
    # be rewritten with the same resolved absolute system python3 that
    # _merge_hook_settings emits -- NOT a bare `python` (issue #2503). Emitting a
    # command whose interpreter cannot run is the failure mode this fix exists to
    # end, so a total probe failure aborts rather than writing a dead command.
    interpreter = resolve_global_interpreter()
    if interpreter is None:
        return (
            "no working system python3 to rewrite global hook registrations "
            "(issue #2503); aborting rather than writing a dead command"
        )

    decl_by_id = {d.manifest_id: d for d in manifest if d.scope == "global"}

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return None  # nothing registered at all -- nothing to migrate

    changed = False

    # Pass 1: claim the legacy (unmarked) entry for each of the 3 fork hooks
    # and rewrite it in place with the correct marker/guard/matcher.
    for legacy_script, manifest_id in _LEGACY_FORK_HOOKS:
        decl = decl_by_id.get(manifest_id)
        if decl is None:
            continue  # manifest no longer declares this fork -- nothing to fix

        legacy_command = _legacy_fork_command_prefix(legacy_script)

        for block in _iter_hook_blocks(hooks):
            for hook_entry in block.get("hooks", []):
                command = hook_entry.get("command")
                # Same hand-editable-settings hazard as the sweep above: a
                # non-string command raises AttributeError on .endswith().
                # It can match no legacy fork command, so skip it.
                if not isinstance(command, str):
                    continue
                if _extract_manifest_id(command):
                    continue  # already migrated -- leave alone

                stripped = command
                if stripped.endswith("|| true"):
                    stripped = stripped[: -len("|| true")].rstrip()
                if stripped != legacy_command:
                    continue

                # Match on the legacy path (that is what is on disk today), but
                # write the *canonical* command built from the declaration. The
                # legacy string still names the pre-rename script, and Step 1.5
                # (sync_claude_dirs) has already removed that hardlink via
                # RENAMED_REMOVALS by the time this Step 3.6 migration runs --
                # preserving it would leave a dangling path in a blocking
                # PreToolUse/Bash hook and break Bash fleet-wide.
                new_command = _build_hook_command(
                    decl,
                    str(Path.home() / ".claude" / "hooks"),
                    interpreter=interpreter,
                    embed_marker=True,
                )

                if hook_entry.get("command") != new_command:
                    hook_entry["command"] = new_command
                    changed = True
                if block.get("matcher", "") != decl.matcher:
                    block["matcher"] = decl.matcher
                    changed = True

    # Pass 2: collapse any duplicate marked entry for the same manifest_id
    # (defends against a regular sync already having appended a fresh copy
    # before this migration ran within the same /update invocation).
    for _legacy_script, manifest_id in _LEGACY_FORK_HOOKS:
        seen = False
        for block in _iter_hook_blocks(hooks):
            surviving = []
            for hook_entry in block.get("hooks", []):
                if _extract_manifest_id(hook_entry.get("command", "")) != manifest_id:
                    surviving.append(hook_entry)
                    continue
                if seen:
                    changed = True  # drop the duplicate
                    continue
                seen = True
                surviving.append(hook_entry)
            if len(surviving) != len(block.get("hooks", [])):
                block["hooks"] = surviving

    # Prune any block/event emptied by the dedupe pass.
    for event in list(hooks.keys()):
        hooks[event] = [b for b in hooks[event] if b.get("hooks")]
        if not hooks[event]:
            del hooks[event]

    if not changed:
        return None

    try:
        serialized = json.dumps(settings, indent=2) + "\n"
        json.loads(serialized)  # validity assertion before ever touching disk
    except (TypeError, ValueError) as e:
        return f"post-migration settings failed JSON validity check, aborting write: {e}"

    backup_path = settings_path.parent / (
        f"{settings_path.name}.bak.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    try:
        backup_path.write_text(raw)
    except OSError as e:
        return f"failed to write backup {backup_path}: {e}"

    try:
        settings_path.write_text(serialized)
    except OSError as e:
        return f"failed to write {settings_path}: {e}"

    return None


def _iter_hook_blocks(hooks: dict) -> list[dict]:
    """Flatten every matcher block across every event in a settings ``hooks`` dict."""
    blocks: list[dict] = []
    for event_hooks in hooks.values():
        if isinstance(event_hooks, list):
            blocks.extend(b for b in event_hooks if isinstance(b, dict))
    return blocks


def _migrate_purge_phantom_agent_sessions(project_dir: Path) -> str | None:
    """Purge phantom AgentSession index-bookkeeping hashes from Redis (#2207).

    Runs scripts/purge_phantom_agent_sessions.py with a per-update time budget.
    Exit 0 = keyspace clean → migration recorded complete. Exit 3 = budget
    expired with phantoms remaining → returns an error string so the migration
    stays pending and resumes on the next update (the purge is idempotent and
    cursor-scan based, so partial progress is kept).

    KNOWING EXCEPTION to `_run_migration_script`: this is the one helper that
    still shells out on its own, because it needs the exit-3 branch and its own
    time budget, which the shared runner has no hook for. The cost is accepted
    and named here rather than left to be rediscovered: its output is discarded
    on the success path, and its failure string is stderr-only — so a script
    logging to stdout would report an empty reason. Give the shared runner an
    exit-code hook before adding a second helper in this shape.
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


def _migrate_confirm_corpus_baseline_readable(project_dir: Path) -> str | None:
    """Confirm the new CorpusSizeBaseline model (issue #2438) imports/queries cleanly.

    Unlike the AgentSession `confirm_*_field_readable` migrations (which
    probe a newly-added field on an existing model), this is a brand-new
    Popoto model with no legacy rows to heal. The equivalent safety check
    here is: import the model, call `get_or_create()` (idempotent —
    creates the singleton row on first call, returns the existing row on
    every subsequent call), and read back `.ring` and `.max_size()` to
    prove the model's read/write path works end-to-end on this machine.
    Writes nothing beyond the singleton bootstrap row itself (which the
    corpus-size anomaly detector would create on its own first run anyway).
    Returns None on success, error string on unexpected failure.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.memory_corpus_baseline import CorpusSizeBaseline

        baseline = CorpusSizeBaseline.get_or_create()
        _ = baseline.ring  # noqa: B018 -- read-only healing probe
        _ = baseline.max_size()  # noqa: B018 -- read-only healing probe
        return None
    except Exception as e:
        return str(e)


def _migrate_job_promises_to_expectations(project_dir: Path) -> str | None:
    """Rewrite every Job's goal JSON ``promises`` list as ``expectations`` (#2708).

    Expectations are now the single obligation primitive on ``Job.goal``, in
    both directions. The pre-cutover promise model recorded only the inbound
    one, so each legacy entry migrates to
    ``direction="inbound", holder="requester", owner="pm"`` — its id,
    timestamp, text (as ``what``) and ``removed_ts`` history carried across
    verbatim. NOTHING is deleted: a discharged promise stays a discharged
    expectation, and the full history remains reconstructable.

    The rewrite delegates to the model rather than reimplementing the
    mapping: ``Job._goal_data()`` already absorbs a lingering ``promises``
    key on every read (the merge-on-read self-heal that makes an old-code
    write landing mid-``/update`` safe), and ``_write_goal_data`` persists
    that merged form while restamping ``has_open_expectations`` and the
    ``status`` projection at the one chokepoint. So this migration is the
    offline sweep of the population, and the read-time merge is what covers
    the update window — one conversion implementation, two entry points,
    no way for them to disagree.

    Idempotent: a row whose stored goal carries no ``promises`` key is
    skipped without a write, so a second pass is reads only. Per-row
    fault-isolated: an unreadable or unparseable row is logged and the sweep
    continues — one bad row must never cost the rest of the population.
    ORM-only; the sweep closes by running ``Job.repair_indexes()``, the
    sanctioned guarded path, which clears the stale
    ``$IndexF:Job:has_open_promises`` sets left by the renamed IndexedField
    and re-derives the new flag.

    Returns None on success, an error string if the enumeration itself fails.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.job import Job

        rewritten = 0
        for job in Job.query.filter():
            try:
                fresh = Job.query.get(id=job.id, room_id=job.room_id)
                if fresh is None or not fresh.goal:
                    continue
                if "promises" not in json.loads(fresh.goal):
                    continue
                # _goal_data() returns the merged form; _write_goal_data()
                # persists it and re-derives the flag + status projection.
                fresh._write_goal_data(fresh._goal_data())
                rewritten += 1
            except Exception as e:  # swallow-ok: one bad row never stops the sweep
                logger.warning(
                    "[migration:job_promises_to_expectations] SKIP job=%s -- %s: %s",
                    getattr(job, "job_id", "?"),
                    type(e).__name__,
                    e,
                )

        if rewritten:
            logger.info(
                "[migration:job_promises_to_expectations] rewrote %d Job goal(s)", rewritten
            )

        # Clear the retired field's index sets and re-derive the new flag.
        # Failure here is not a migration failure: the same repair runs on
        # the daily maintenance cadence and on every worker start.
        try:
            Job.repair_indexes()
        except Exception as e:  # swallow-ok: index hygiene retries on its own cadence
            logger.warning(
                "[migration:job_promises_to_expectations] index repair failed (%s: %s); "
                "the daily guarded repair will converge it",
                type(e).__name__,
                e,
            )

        return None
    except Exception as e:
        return str(e)


def _migrate_backfill_job_last_active_scores(project_dir: Path) -> str | None:
    """Repair tz-skewed ``Job.last_active_at`` sorted-set scores (issue #2636).

    popoto 1.8.0 decodes stored datetimes without tzinfo, so before the
    ``Job.save()`` UTC-reattach override shipped, any re-save of a reloaded Job
    (``mark_at_rest()``, ``_write_goal_data()``) recomputed its ``$SortF``
    partition score as ``naive.timestamp()`` — local time, skewed from the
    correct UTC epoch by the writing host's UTC offset. The hash field stayed
    correct; only the score drifted. The bounded ``recent_for_room`` read
    trusts scores, so existing skew must be swept out once per machine.

    The sweep itself is ``Job.renormalize_last_active_scores()`` — the ONE
    shared implementation, also run by ``Job.repair_indexes()`` after every
    daily ``rebuild_indexes()`` (whose ``field.on_save`` re-scoring of
    naive-decoded instances bypasses the save() override and would otherwise
    re-skew every score on a non-UTC host). See that classmethod's docstring
    for the loop contract: derived partition key, 1-second tolerance,
    fresh re-read, structural clobber-proof
    ``fresh.save(update_fields=["last_active_at"])`` (ORM-only — the only
    write is ``instance.save()``), instant-preserving (never a constant
    stamp, spike-4), absent-member skip, per-row failure tolerance.

    Idempotent — a repaired score is inside tolerance on the next pass, so a
    second run repairs 0 and costs reads alone. Fleet-convergent: machines
    share this Redis, and re-running on every machine's ``/update`` is safe
    and expected; each pass only rewrites rows whose score still disagrees
    with their own hash (e.g. re-skewed by a peer still on pre-override code).
    Single unbounded pass, no cursor — DECIDED on the measured population
    (92 Jobs / 0.03s full hydrate, 2026-08-17); the total count is logged
    each run.

    Returns None on success; an error string only if the import/call path
    itself fails. An enumeration failure *inside* the sweep (e.g. Redis
    unreachable) is swallowed by ``renormalize_last_active_scores`` as
    ``(0, 0)``: this migration logs a 0-scanned pass, returns None, and is
    recorded applied. That is accepted — the ``Job.repair_indexes`` sweep,
    run at worker startup via ``scripts/popoto_index_cleanup.run_cleanup``,
    is the retry/backstop that eventually repairs the scores.
    """
    try:
        import sys

        sys.path.insert(0, str(project_dir))
        from models.job import Job

        scanned, repaired = Job.renormalize_last_active_scores()
        logger.info(
            "[migration:backfill_job_last_active_scores] "
            "scanned %d Job(s), repaired %d skewed score(s)",
            scanned,
            repaired,
        )
        return None
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
    "confirm_pipeline_ledger_slug_readable": (
        _migrate_confirm_pipeline_ledger_slug_readable,
        "Confirm PipelineLedger.slug (issues #2735/#2718) reads cleanly on legacy rows",
    ),
    "schema_diet_fields": (
        _migrate_schema_diet_fields,
        "Strip schema-diet (#1927) fields from existing AgentSession records",
    ),
    "strip_pid_fields": (
        _migrate_strip_pid_fields,
        "Strip removed pid fields (+expectations) from terminal AgentSession records",
    ),
    # Second registration of the SAME script under a new name. Migrations are
    # skipped by name, so a rename is the auditable way to re-run one that is
    # already recorded complete on every machine -- no hand-editing of
    # data/migrations_completed.json. The script is idempotent, so a clean
    # machine gets a fast no-op; the value of the re-run is the captured
    # output, which the first run discarded.
    "strip_pid_fields_v2": (
        _migrate_strip_pid_fields,
        "Re-run the pid-field strip with output captured (zero-record guard + "
        "guarded index repair)",
    ),
    # #2524: the same rename-to-re-run treatment for the two sibling strip
    # migrations, which until now had neither the zero-record guard nor the
    # production-safe index sweep. Both are recorded complete on every machine,
    # so editing the scripts in place would have been inert.
    #
    # Re-running is decided rather than obvious. The canary machine proved
    # "recorded complete" does not imply "actually did the work" -- its
    # strip_pid_fields was complete while 20 terminal records still carried all
    # four stale fields. The scripts are idempotent, so a clean machine pays one
    # scan and gets a captured log line proving it is clean; a machine in the
    # canary's state gets its fields reclaimed instead of keeping them forever
    # with no signal.
    #
    # Both must stay AHEAD of purge_phantom_agent_sessions: the purge deletes
    # index-bookkeeping hashes that the strip's trailing sweep expects to find
    # present. Same constraint test_migrations.py already pins for
    # strip_pid_fields_v2.
    #
    # On a FRESH install both the v1 and the v2 entry run in the same /update,
    # since neither is recorded complete yet. That is a wasted scan, not a bug:
    # the scripts are idempotent, and on an empty keyspace both hit the
    # zero-record guard anyway. On every existing machine the v1 is a skip.
    "strip_pty_session_fields_v2": (
        _migrate_strip_pty_session_fields_v2,
        "Re-run the PTY-field strip with the zero-record guard, the "
        "production-safe index sweep, and output captured",
    ),
    "schema_diet_fields_v2": (
        _migrate_schema_diet_fields_v2,
        "Re-run the schema-diet strip with the zero-record guard, the "
        "production-safe index sweep, and output captured",
    ),
    "purge_phantom_agent_sessions": (
        _migrate_purge_phantom_agent_sessions,
        "Purge phantom AgentSession index-bookkeeping hashes from Redis (#2207)",
    ),
    "confirm_corpus_baseline_readable": (
        _migrate_confirm_corpus_baseline_readable,
        "Confirm the new CorpusSizeBaseline model (issue #2438) reads/writes cleanly",
    ),
    "hook_registration_manifest_ids": (
        _migrate_hook_registration_manifest_ids,
        "Attach manifest_id markers to the 3 deployed legacy SDLC-fork hook "
        "entries in ~/.claude/settings.json (Pre-requisite Bug 1)",
    ),
    "sweep_legacy_unmarked_global_hooks": (
        _migrate_sweep_legacy_unmarked_global_hooks,
        "Remove the stale unmarked pre-manifest validate_no_raw_redis_delete.py "
        "global registration from ~/.claude/settings.json (issue #2503)",
    ),
    "job_promises_to_expectations": (
        _migrate_job_promises_to_expectations,
        "Rewrite Job goal promises as inbound expectations and clear the retired "
        "has_open_promises index sets (issue #2708)",
    ),
    "backfill_job_last_active_scores": (
        _migrate_backfill_job_last_active_scores,
        "Repair tz-skewed Job.last_active_at sorted-set scores via field-scoped "
        "ORM re-saves (issue #2636)",
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
