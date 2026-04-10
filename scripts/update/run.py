#!/usr/bin/env python3
"""
Update orchestrator - main entry point for update system.

Usage:
    python scripts/update/run.py --full      # Full update (from /update skill)
    python scripts/update/run.py --cron      # Minimal update (from cron)
    python scripts/update/run.py --verify    # Just verify environment
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.update import (  # noqa: E402
    cal_integration,
    deps,
    env_sync,
    git,
    hardlinks,
    hooks,
    migrations,
    officecli,
    rodney,
    service,
    verify,
)


@dataclass
class UpdateConfig:
    """Configuration for update run."""

    # What to run
    do_git_pull: bool = True
    do_dep_sync: bool = True
    do_auto_bump: bool = True  # Auto-bump critical deps from PyPI
    do_service_restart: bool = True
    do_verify: bool = True
    do_calendar: bool = False  # Only in full mode
    do_ollama: bool = False  # Only in full mode
    do_mcp: bool = False  # Only in full mode

    # Options
    verbose: bool = False
    json_output: bool = False
    force_dep_sync: bool = False  # Sync even if no dep files changed

    @classmethod
    def full(cls) -> UpdateConfig:
        """Config for full update (from /update skill)."""
        return cls(
            do_git_pull=True,
            do_dep_sync=True,
            force_dep_sync=True,
            do_service_restart=True,
            do_verify=True,
            do_calendar=True,
            do_ollama=True,
            do_mcp=True,
            verbose=True,
        )

    @classmethod
    def cron(cls) -> UpdateConfig:
        """Config for cron update (user-triggered via Telegram /update)."""
        return cls(
            do_git_pull=True,
            do_dep_sync=True,
            force_dep_sync=True,
            do_service_restart=False,  # Use restart flag for graceful restart
            do_verify=True,
            do_calendar=True,
            do_ollama=True,
            do_mcp=True,
            verbose=True,
        )

    @classmethod
    def verify_only(cls) -> UpdateConfig:
        """Config for verification only."""
        return cls(
            do_git_pull=False,
            do_dep_sync=False,
            do_service_restart=False,
            do_verify=True,
            do_calendar=True,
            do_ollama=True,
            do_mcp=True,
            verbose=True,
        )


@dataclass
class UpdateResult:
    """Result of update run."""

    success: bool = True
    git_result: git.GitPullResult | None = None
    dep_result: deps.DepSyncResult | None = None
    auto_bump_result: deps.AutoBumpResult | None = None
    version_info: list[deps.VersionInfo] | None = None
    verification: verify.VerificationResult | None = None
    calendar_hook: cal_integration.CalendarHookResult | None = None
    calendar_config: cal_integration.CalendarConfigResult | None = None
    service_status: service.ServiceStatus | None = None
    caffeinate_status: service.CaffeinateStatus | None = None
    hardlink_result: hardlinks.HardlinkSyncResult | None = None
    env_sync_result: env_sync.EnvSyncResult | None = None
    hook_audit: hooks.HookAuditResult | None = None
    migration_result: migrations.MigrationResult | None = None
    officecli_result: officecli.InstallResult | None = None
    rodney_result: rodney.InstallResult | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Log buffer for telegram mode (writes to file instead of stdout)
_log_buffer: list[str] = []
_log_to_buffer: bool = False


def log(msg: str, verbose: bool = True, always: bool = False) -> None:
    """Print log message or capture to buffer for telegram mode."""
    if not (verbose or always):
        return
    line = f"[update] {msg}"
    if _log_to_buffer:
        _log_buffer.append(line)
    else:
        print(line)


RECENT_ACTIVITY_WINDOW = (
    30 * 60
)  # 30 minutes — session considered live if updated_at within this window


def _cleanup_stale_sessions(project_dir: Path, age_minutes: int = 120) -> tuple[int, int]:
    """Kill running sessions with no live process.

    Primary liveness check: ``updated_at`` recency. Sessions whose ``updated_at``
    timestamp is within ``RECENT_ACTIVITY_WINDOW`` (30 min) are skipped — they have
    recent heartbeat activity and are considered live regardless of ``created_at`` age.

    Fallback liveness check: ``created_at`` age. When ``updated_at`` is None the
    session falls back to the old 120-minute ``created_at`` threshold so that very
    old sessions created before the heartbeat feature existed are still cleaned up.

    Secondary defense: sessions whose ``chat_id`` has a live entry in
    ``_active_workers`` are always skipped (in-process invocations only; the registry
    is empty when the update script runs as a standalone subprocess).

    Terminal sessions (killed/abandoned/failed/completed) are preserved
    for reflections to analyze — reflections handles its own 90-day expiry.

    Returns:
        (killed_count, skipped_live) — number of sessions killed and number skipped
        because they had recent heartbeat activity.
    """
    import time
    from datetime import datetime

    from models.agent_session import AgentSession
    from models.session_lifecycle import finalize_session

    # Attempt to import the live-worker registry; fails gracefully if the queue
    # module is not initialized in this process (standalone subprocess invocation).
    try:
        from agent.agent_session_queue import _active_workers as active_workers_registry
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "[update] Could not import _active_workers from agent_session_queue — "
            "falling back to recency-threshold-only cleanup"
        )
        active_workers_registry = {}

    now = time.time()
    threshold = age_minutes * 60
    killed_count = 0
    skipped_live = 0

    # pending sessions are never stale — they were never started;
    # "pending" was added in PR #739 by mistake
    for status in ("running",):
        sessions = list(AgentSession.query.filter(status=status))
        for s in sessions:
            # Secondary defense: skip sessions with a live worker in the registry
            chat_id = getattr(s, "chat_id", None)
            if chat_id and chat_id in active_workers_registry:
                worker = active_workers_registry[chat_id]
                if worker is not None and not worker.done():
                    continue  # live worker exists — do not kill

            # Primary liveness check: updated_at recency
            updated = getattr(s, "updated_at", None)
            if updated is not None:
                if isinstance(updated, datetime):
                    recency = now - updated.timestamp()
                elif isinstance(updated, str):
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        recency = now - dt.timestamp()
                    except (ValueError, TypeError):
                        recency = None
                else:
                    try:
                        recency = now - float(updated)
                    except (TypeError, ValueError):
                        recency = None

                if recency is not None and recency < RECENT_ACTIVITY_WINDOW:
                    skipped_live += 1
                    continue  # recent heartbeat activity — session is live

            # Fallback liveness check: created_at age (for sessions without updated_at)
            created = s.created_at
            if not created:
                continue
            if isinstance(created, datetime):
                age = now - created.timestamp()
            elif isinstance(created, str):
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age = now - dt.timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                try:
                    age = now - float(created)
                except (TypeError, ValueError):
                    continue

            if age < threshold:
                continue

            # Route through lifecycle layer so hooks fire (log_lifecycle_transition,
            # auto_tag_session, parent finalization). skip_checkpoint=True because
            # stale cleanup runs outside the normal worker context and branch state
            # may be unavailable.
            try:
                finalize_session(
                    s,
                    "killed",
                    reason="stale cleanup (no live process)",
                    skip_checkpoint=True,
                )
                killed_count += 1
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "[update] Failed to finalize stale session %s: %s",
                    getattr(s, "agent_session_id", "?"),
                    exc,
                )

    return killed_count, skipped_live


def run_update(project_dir: Path, config: UpdateConfig) -> UpdateResult:
    """Run update with given configuration."""
    result = UpdateResult()
    v = config.verbose

    # Step 1: Git pull
    if config.do_git_pull:
        log("Pulling latest changes...", v)
        result.git_result = git.git_pull(project_dir)

        if not result.git_result.success:
            log(f"FAIL: {result.git_result.error}", v)
            result.success = False
            result.errors.append(f"Git pull failed: {result.git_result.error}")
            return result

        if result.git_result.commit_count == 0:
            log(f"Already up to date ({git.get_short_sha(project_dir)})", v, always=True)
        else:
            log(f"Pulled {result.git_result.commit_count} commit(s):", v, always=True)
            for commit in result.git_result.commits[:5]:
                log(f"  {commit}", v, always=True)

        if result.git_result.stashed:
            if result.git_result.stash_restored:
                log("Stashed and restored local changes", v)
            else:
                result.warnings.append("Local changes stashed but failed to restore")

    # Step 1.5: Sync .claude hardlinks (skills + commands to ~/.claude/)
    log("Syncing .claude hardlinks...", v)
    result.hardlink_result = hardlinks.sync_claude_dirs(project_dir)
    if result.hardlink_result.created > 0:
        log(f"Created {result.hardlink_result.created} new hardlink(s)", v, always=True)
        for action in result.hardlink_result.actions:
            if action.action == "created":
                log(f"  {action.dst}", v, always=True)
    if result.hardlink_result.removed > 0:
        log(
            f"Removed {result.hardlink_result.removed} stale hardlink(s)",
            v,
            always=True,
        )
        for action in result.hardlink_result.actions:
            if action.action == "removed":
                log(f"  {action.dst}", v, always=True)
    if result.hardlink_result.errors > 0:
        for action in result.hardlink_result.actions:
            if action.action == "error":
                log(f"WARN: Failed to link {action.dst}: {action.error}", v)
                result.warnings.append(f"Hardlink failed: {action.dst}")

    # Step 1.6: Sync env vars from vault
    log("Syncing env vars from vault...", v)
    result.env_sync_result = env_sync.sync_env_from_vault(project_dir)
    env_r = result.env_sync_result
    if env_r.added:
        log(f"Added env vars: {', '.join(env_r.added)}", v, always=True)
    if env_r.updated:
        log(f"Updated env vars: {', '.join(env_r.updated)}", v, always=True)
    if env_r.error:
        log(f"WARN: Env sync: {env_r.error}", v)
        result.warnings.append(f"Env sync: {env_r.error}")

    # Step 1.7: Audit skill hooks for dangerous patterns
    log("Auditing skill hooks...", v)
    result.hook_audit = hooks.audit_skill_hooks(project_dir)
    if result.hook_audit.issues:
        for issue in result.hook_audit.issues:
            log(f"WARN: [{issue.skill}] {issue.detail}", v, always=True)
            result.warnings.append(f"Hook issue in {issue.skill}: {issue.issue_type}")
    else:
        log(f"Skill hooks OK ({result.hook_audit.skills_scanned} skills scanned)", v)

    # Step 2: Check for pending critical upgrades
    pending = git.check_upgrade_pending(project_dir)
    if pending.pending:
        log(f"WARNING: Critical dependency upgrade pending since {pending.timestamp}", v)
        result.warnings.append(f"Critical upgrade pending: {pending.reason}")

    # Step 3: Dependency sync
    if config.do_dep_sync:
        should_sync = config.force_dep_sync

        # Check if dep files changed
        if result.git_result and result.git_result.commit_count > 0:
            changed_files = git.get_changed_files(
                project_dir,
                result.git_result.before_sha,
                result.git_result.after_sha,
            )

            if deps.check_dep_files_changed(changed_files):
                # Check for critical dep changes
                critical_changes = git.check_critical_dep_changes(
                    project_dir,
                    result.git_result.before_sha,
                    result.git_result.after_sha,
                )

                if critical_changes:
                    log("CRITICAL dependency changes detected:", v, always=True)
                    for change in critical_changes:
                        log(f"  {change}", v, always=True)
                    log(
                        "Skipping auto-sync. Run /update manually to apply.",
                        v,
                        always=True,
                    )
                    git.set_upgrade_pending(project_dir, "critical-dep-upgrade")
                else:
                    should_sync = True

        if should_sync:
            log("Syncing dependencies...", v, always=True)
            result.dep_result = deps.sync_dependencies(project_dir)

            if result.dep_result.success:
                log(
                    f"Dependencies synced via {result.dep_result.method}",
                    v,
                    always=True,
                )
            else:
                log(f"WARN: Dep sync failed: {result.dep_result.error}", v, always=True)
                result.warnings.append(f"Dep sync failed: {result.dep_result.error}")

            # Verify critical versions
            result.version_info = deps.verify_critical_versions(project_dir)
            mismatches = [vi for vi in result.version_info if not vi.matches]
            if mismatches:
                for vi in mismatches:
                    log(
                        f"WARN: {vi.package} version mismatch: {vi.version} != {vi.expected}",
                        v,
                    )
                    result.warnings.append(f"{vi.package} version mismatch")
        else:
            log("No dependency changes, skipping sync", v)

    # Step 3.5: Auto-bump critical deps from PyPI
    if config.do_auto_bump:
        log("Checking PyPI for newer critical deps...", v)
        result.auto_bump_result = deps.auto_bump_deps(project_dir)
        bump = result.auto_bump_result

        for b in bump.bumps:
            if b.bumped:
                log(f"  {b.package}: {b.old_version} -> {b.new_version}", v, always=True)
            elif b.error:
                log(f"  {b.package}: skip ({b.error})", v)
            else:
                log(f"  {b.package}: {b.old_version} (up to date)", v)

        if bump.any_bumped:
            if bump.rolled_back:
                log(
                    "WARN: Auto-bump rolled back (smoke test or sync failed)",
                    v,
                    always=True,
                )
                log(f"  Detail: {bump.smoke_output or bump.sync_error}", v)
                result.warnings.append("Auto-bump rolled back after test failure")
            elif bump.smoke_passed:
                log("Smoke test passed after bump", v, always=True)
                # Commit the pyproject.toml change
                try:
                    bumped_pkgs = [
                        f"{b.package} {b.old_version}->{b.new_version}"
                        for b in bump.bumps
                        if b.bumped
                    ]
                    msg = f"Bump deps: {', '.join(bumped_pkgs)}"
                    deps.run_cmd(
                        ["git", "add", "pyproject.toml"],
                        cwd=project_dir,
                    )
                    deps.run_cmd(
                        ["git", "commit", "-m", msg],
                        cwd=project_dir,
                    )
                    deps.run_cmd(
                        ["git", "push"],
                        cwd=project_dir,
                    )
                    log(f"Committed and pushed: {msg}", v, always=True)
                except Exception as e:
                    log(f"WARN: Failed to commit bump: {e}", v)
                    result.warnings.append("Dep bump succeeded but commit/push failed")

    # Step 3.6: Run pending data migrations (after git pull, before service restart)
    log("Checking pending migrations...", v)
    result.migration_result = migrations.run_pending_migrations(project_dir)
    mig = result.migration_result
    if mig.ran:
        for name in mig.ran:
            desc = migrations.MIGRATIONS.get(name, (None, name))[1]
            log(f"  Migrated: {desc}", v, always=True)
    if mig.failed:
        for err in mig.errors:
            log(f"  FAIL: {err}", v, always=True)
            result.errors.append(f"Migration failed: {err}")
    if not mig.ran and not mig.failed:
        log("No pending migrations", v)

    # Step 3.7: OfficeCLI binary install/update
    log("Checking OfficeCLI...", v)
    result.officecli_result = officecli.install_or_update()
    oc = result.officecli_result
    if oc.success:
        if oc.action == "skipped":
            log(f"OfficeCLI {oc.version} (up to date)", v)
        else:
            log(f"OfficeCLI {oc.action}: {oc.version}", v, always=True)
    else:
        log(f"WARN: OfficeCLI {oc.action}: {oc.error}", v)
        result.warnings.append(f"OfficeCLI: {oc.error}")

    # Step 3.8: Rodney binary install/update (happy path testing)
    log("Checking Rodney...", v)
    result.rodney_result = rodney.install_or_update()
    rr = result.rodney_result
    if rr.success:
        if rr.action == "skipped":
            log(f"Rodney {rr.version} (up to date)", v)
        else:
            log(f"Rodney {rr.action}: {rr.version}", v, always=True)
    else:
        log(f"WARN: Rodney {rr.action}: {rr.error}", v)
        result.warnings.append(f"Rodney: {rr.error}")

    # Step 4: Ollama model (full mode only)
    if config.do_ollama:
        from config.models import OLLAMA_LOCAL_MODEL, OLLAMA_SUPERSEDED_MODELS

        log("Checking Ollama model...", v)
        ollama_model = os.getenv("OLLAMA_SUMMARIZER_MODEL", OLLAMA_LOCAL_MODEL)
        ollama_check = verify.check_ollama(ollama_model)

        if not ollama_check.available:
            if ollama_check.error and "Not installed" not in ollama_check.error:
                log(f"Pulling Ollama model {ollama_model}...", v)
                if verify.pull_ollama_model(ollama_model):
                    log(f"Ollama model {ollama_model} pulled", v)
                else:
                    result.warnings.append(f"Failed to pull Ollama model {ollama_model}")
            else:
                log("Ollama not installed, skipping", v)

        # Smoke test: verify the model can generate a response
        if ollama_check.available or verify.check_ollama(ollama_model).available:
            log(f"Smoke testing {ollama_model}...", v)
            try:
                import subprocess

                smoke_result = subprocess.run(
                    ["ollama", "run", ollama_model, "hi"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if smoke_result.returncode == 0 and smoke_result.stdout.strip():
                    log(f"Smoke test passed for {ollama_model}", v)
                else:
                    result.warnings.append(
                        f"Smoke test failed for {ollama_model}: "
                        f"{smoke_result.stderr.strip() or 'empty response'}"
                    )
            except subprocess.TimeoutExpired:
                result.warnings.append(f"Smoke test timed out for {ollama_model}")
            except Exception as e:
                result.warnings.append(f"Smoke test error for {ollama_model}: {e}")

        # Cleanup superseded models (best-effort, never fail the update)
        if ollama_check.available or verify.check_ollama(ollama_model).available:
            log("Cleaning up superseded Ollama models...", v)
            for old_model in OLLAMA_SUPERSEDED_MODELS:
                try:
                    import subprocess

                    rm_result = subprocess.run(
                        ["ollama", "rm", old_model],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if rm_result.returncode == 0:
                        log(f"  Removed {old_model}", v, always=True)
                    else:
                        # Model may not exist, that is fine
                        stderr = rm_result.stderr.strip()
                        if "not found" in stderr.lower():
                            log(f"  {old_model} not present, skipping", v)
                        else:
                            log(f"  WARN: Failed to remove {old_model}: {stderr}", v)
                except Exception as e:
                    log(f"  WARN: Failed to remove {old_model}: {e}", v)

    # Step 4.5: Machine identity verification
    log("Verifying machine identity...", v)
    machine_check = verify.check_machine_identity(project_dir)
    if machine_check.get("error"):
        log(f"WARN: {machine_check['error']}", v, always=True)
        result.warnings.append(machine_check["error"])
    elif machine_check.get("projects"):
        log(
            f"Machine: {machine_check['hostname']} -> "
            f"projects: {', '.join(machine_check['projects'])}",
            v,
            always=True,
        )
    else:
        log(
            f"WARN: No projects assigned to machine '{machine_check.get('hostname', 'unknown')}'",
            v,
            always=True,
        )
        result.warnings.append(
            f"No projects in config for machine '{machine_check.get('hostname')}'. "
            "Check 'machine' field in ~/Desktop/Valor/projects.json"
        )

    # Step 4.5: Telegram auth check (critical — bridge is useless without it)
    if config.do_service_restart:
        log("Checking Telegram session...", v)
        telegram_check = verify.check_telegram_session(project_dir)
        if telegram_check.available:
            log(f"  Telegram: {telegram_check.version or 'OK'}", v)
        else:
            log(
                f"ERROR: Telegram session not authorized: {telegram_check.error}",
                v,
                always=True,
            )
            result.errors.append(f"Telegram auth: {telegram_check.error}")

    # Step 5: Service management
    if config.do_service_restart:
        log("Installing/restarting services...", v)

        # Install caffeinate first
        caff = service.get_caffeinate_status()
        if not caff.installed:
            log("Installing caffeinate service...", v)
            if service.install_caffeinate():
                log("Caffeinate installed", v)
            else:
                result.warnings.append("Failed to install caffeinate")

        # Install main service (handles both bridge and update cron)
        if service.install_service(project_dir):
            log("Services installed/restarted", v)
        else:
            result.warnings.append("Service install may have failed")

        # Wait for bridge to start after launchctl unload+load cycle.
        # Polling window: 10 x 2s = 20s covers ThrottleInterval (10s)
        # + bridge startup (~5s) + safety margin (~5s).
        import time

        for _ in range(10):
            time.sleep(2)
            result.service_status = service.get_service_status(project_dir)
            if result.service_status.running:
                break

        result.caffeinate_status = service.get_caffeinate_status()

        if result.service_status.running:
            log(f"Bridge running (PID: {result.service_status.pid})", v)
        else:
            log("ERROR: Bridge not running after restart", v, always=True)
            result.errors.append("Bridge not running after restart")

        # Always restart web UI to pick up code/dep changes
        if service.restart_webui(project_dir):
            log("Web UI restarted (port 8500)", v)
        else:
            log("ERROR: Web UI failed to start", v, always=True)
            result.errors.append("Web UI failed to start")

        # Check update cron
        if service.is_update_cron_installed():
            log("Update cron installed", v)
        else:
            result.warnings.append("Update cron not installed")

        # Install/reload reflections scheduler
        if service.install_reflections(project_dir):
            log("Reflections scheduler installed", v)
        elif (project_dir / "com.valor.reflections.plist").exists():
            result.warnings.append("Reflections plist install failed")

        # Install/reload standalone worker service
        if (project_dir / "com.valor.worker.plist").exists():
            if service.install_worker(project_dir):
                log("Worker service installed", v)
                # Verify worker starts
                import time as _time

                for _ in range(5):
                    _time.sleep(2)
                    if service.is_worker_running():
                        worker_pid = service.get_worker_pid()
                        log(f"Worker running (PID: {worker_pid})", v)
                        break
                else:
                    log("WARN: Worker not running after install", v, always=True)
                    result.warnings.append("Worker not running after install")
            else:
                result.warnings.append("Worker plist install failed")

    elif result.git_result and result.git_result.commit_count > 0:
        # Cron mode: set restart flag instead of restarting
        log("Setting restart flag for graceful restart...", v, always=True)
        git.set_restart_requested(project_dir, result.git_result.commit_count)

    # Step 5.5: Clean up corrupted + stale sessions
    # Corrupted sessions (invalid IDs) are deleted first to prevent error spam.
    # Then stale running/pending sessions are killed. Terminal sessions are
    # preserved for reflections to analyze.
    try:
        from agent.agent_session_queue import cleanup_corrupted_agent_sessions

        corrupted = cleanup_corrupted_agent_sessions()
        if corrupted > 0:
            log(f"Cleaned up {corrupted} corrupted session(s)", v)
    except Exception as e:
        log(f"WARN: Corrupted session cleanup failed: {e}", v)

    try:
        stale_killed, skipped_live = _cleanup_stale_sessions(project_dir)
        if stale_killed > 0:
            log(f"Cleaned up {stale_killed} stale session(s)", v)
        if skipped_live > 0:
            log(f"Skipped {skipped_live} live session(s) (recent heartbeat)", v)
    except Exception as e:
        log(f"WARN: Session cleanup failed: {e}", v)

    # Step 5.6: Repair Popoto field-index corruption.
    #
    # Popoto maintains secondary indexes (e.g. $IndexF:AgentSession:status:running)
    # that map field values to object keys. When a session is deleted without going
    # through the ORM (e.g. a crash mid-write), its object hash is gone but the
    # index entry remains. Every AgentSession.query.filter(status=...) then hits a
    # hgetall miss and logs "one or more redis keys points to missing objects".
    #
    # Detection: scan $IndexF:AgentSession:* keys and check each member's backing
    # hash (read-only). If stale entries found, use rebuild_indexes() to atomically
    # drop all indexes and reconstruct them from actual hashes — correct ORM path.
    try:
        from popoto.models.query import POPOTO_REDIS_DB

        from models.agent_session import AgentSession

        prefix = f"$IndexF:{AgentSession.__name__}:"
        index_keys = POPOTO_REDIS_DB.keys(f"{prefix}*")

        stale_by_index: dict[str, list[bytes]] = {}
        for index_key in index_keys:
            members = POPOTO_REDIS_DB.smembers(index_key)
            stale = [m for m in members if not POPOTO_REDIS_DB.hgetall(m)]
            if stale:
                label = index_key.decode().removeprefix(prefix)
                stale_by_index[label] = stale

        total_stale = sum(len(v) for v in stale_by_index.values())

        if total_stale:
            log(
                f"Popoto field index: {total_stale} stale pointer(s) across "
                f"{len(stale_by_index)} index(es) — rebuilding",
                v,
                always=True,
            )
            for label, stale_members in sorted(stale_by_index.items()):
                # Parse stale object keys for diagnostics.
                # Key format: AgentSession:{chat_id}:{session_id}:{parent_id}:{project}:{role}
                for raw in stale_members:
                    try:
                        parts = raw.decode().split(":")
                        chat_id = parts[1] if len(parts) > 1 else "?"
                        session_id = parts[2][:8] if len(parts) > 2 else "?"
                        role = parts[5] if len(parts) > 5 else "?"
                        log(
                            f"  [{label}] chat={chat_id} session={session_id}... role={role}",
                            v,
                            always=True,
                        )
                    except Exception:
                        log(f"  [{label}] {raw!r} (unparseable)", v, always=True)

            # Surface the root cause before repairing.
            if any("status" in k for k in stale_by_index):
                log(
                    "  ROOT CAUSE HINT: status index has stale entries — a session hash was "
                    "removed without going through the ORM (crash mid-write or finalize_session "
                    "failure). Check for unhandled exceptions in finalize_session().",
                    v,
                    always=True,
                )

            # repair_indexes() clears $IndexF: indexes (which rebuild_indexes()
            # misses) then calls rebuild_indexes() to reconstruct everything
            # from actual hashes — correct ORM path, no raw Redis writes.
            _, rebuilt = AgentSession.repair_indexes()
            log(f"Popoto field index rebuilt ({rebuilt} session(s) indexed)", v)
        else:
            log("Popoto field index: OK (no stale pointers)", v)
    except Exception as e:
        log(f"WARN: Popoto index repair failed: {e}", v)

    # Step 6: Environment verification
    if config.do_verify:
        log("Verifying environment...", v)
        result.verification = verify.verify_environment(
            project_dir,
            check_ollama_model=config.do_ollama,
        )

        # Report system tools
        # claude CLI is optional — bridge uses SDK directly
        optional_tools = {"claude"}
        for tool in result.verification.system_tools:
            status = "OK" if tool.available else "MISSING"
            log(f"  {tool.name}: {status}", v)
            if not tool.available and tool.error:
                log(f"    {tool.error}", v, always=True)
                if tool.name not in optional_tools:
                    result.warnings.append(f"{tool.name}: {tool.error}")

        # Migrate legacy Desktop/claude_code paths in settings.json
        log("Migrating settings.json paths...", v)
        settings_migration = verify.migrate_settings_json_paths()
        if settings_migration.get("migrated"):
            log(f"  Settings: {settings_migration.get('reason')}", v, always=True)
        else:
            log(f"  Settings: {settings_migration.get('reason')}", v)

        # Sync Claude OAuth credentials
        log("Syncing Claude OAuth credentials...", v)
        oauth_sync = verify.sync_claude_oauth(project_dir)
        if oauth_sync.get("synced"):
            if oauth_sync.get("refreshed_from_live"):
                log("  OAuth: refreshed source from live token", v)
            else:
                log(f"  OAuth: {oauth_sync.get('reason')}", v)
        else:
            log(f"  OAuth: {oauth_sync.get('reason')}", v)
            result.warnings.append(f"OAuth sync: {oauth_sync.get('reason')}")

        # Report SDK auth
        auth = result.verification.sdk_auth
        if auth.get("claude_desktop_running"):
            log("  SDK auth: Claude Desktop (subscription)", v)
        elif auth.get("api_key_configured"):
            log("  SDK auth: API key", v)
        else:
            log("  SDK auth: NOT CONFIGURED", v)
            result.warnings.append("SDK auth not configured")

        # Report gitignore issues (un-gitignored embeddings, etc.)
        if result.verification.gitignore_issues:
            for issue in result.verification.gitignore_issues:
                msg = f"{issue.repo}: {issue.file_path} ({issue.size_mb}MB) not in .gitignore"
                log(f"  WARN: {msg}", v, always=True)
                result.warnings.append(msg)

    # Step 7: Calendar integration
    if config.do_calendar:
        log("Checking calendar integration...", v)

        # Verify all Anthropic models are still valid
        model_errors = verify.verify_models(project_dir)
        for model_error in model_errors:
            log(f"WARN: {model_error}", v, always=True)
            result.warnings.append(model_error)

        # Global hook
        result.calendar_hook = cal_integration.ensure_global_hook(project_dir)
        if result.calendar_hook.configured:
            if result.calendar_hook.created:
                log("Calendar hook installed", v)
            else:
                log("Calendar hook OK", v)
        else:
            log(f"WARN: Calendar hook issue: {result.calendar_hook.error}", v)
            result.warnings.append(f"Calendar hook: {result.calendar_hook.error}")

        # Calendar config
        result.calendar_config = cal_integration.generate_calendar_config(project_dir)
        if result.calendar_config.success:
            log(f"Calendar config: {len(result.calendar_config.mappings)} mappings", v)
            for mapping in result.calendar_config.mappings:
                status = "OK" if mapping.accessible else "INACCESSIBLE"
                cal_name = mapping.calendar_name or mapping.calendar_id
                log(f"  {mapping.slug} -> {cal_name} ({status})", v)
        else:
            log(f"WARN: Calendar config: {result.calendar_config.error}", v)
            result.warnings.append(f"Calendar config: {result.calendar_config.error}")

    # Step 8: MCP servers
    if config.do_mcp:
        log("Checking MCP servers...", v)
        mcp_servers = verify.check_mcp_servers()
        if mcp_servers:
            log(f"MCP servers: {len(mcp_servers)}", v)
            for server in mcp_servers[:5]:
                log(f"  {server}", v)
        else:
            log("No MCP servers configured", v)

    # Final summary
    if v:
        log("", v)
        log("=" * 50, v)
        if result.errors:
            log(f"FAILED with {len(result.errors)} error(s)", v)
            result.success = False
        elif result.warnings:
            log(f"COMPLETED with {len(result.warnings)} warning(s)", v)
        else:
            log("COMPLETED successfully", v)

        if result.git_result:
            sha = git.get_short_sha(project_dir)
            log(f"HEAD: {sha}", v)

    return result


def main() -> int:
    """Main entry point."""
    global _log_to_buffer, _log_buffer

    parser = argparse.ArgumentParser(description="Valor update system")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="Full update (all checks)")
    mode.add_argument("--cron", action="store_true", help="Telegram /update (summary + log file)")
    mode.add_argument("--verify", action="store_true", help="Verify only (no changes)")

    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_ROOT,
        help="Project directory",
    )

    args = parser.parse_args()

    # Select config
    if args.full:
        config = UpdateConfig.full()
    elif args.cron:
        config = UpdateConfig.cron()
        # Telegram mode: capture logs to buffer, output summary + file
        _log_to_buffer = True
        _log_buffer = []
    else:
        config = UpdateConfig.verify_only()

    if args.quiet:
        config.verbose = False
    if args.json:
        config.json_output = True
        config.verbose = False

    # Run update
    result = run_update(args.project_dir, config)

    # Output for telegram mode: clean summary + log file
    if args.cron and _log_buffer:
        # Build summary
        sha = git.get_short_sha(args.project_dir) if result.git_result else "unknown"
        commits = result.git_result.commit_count if result.git_result else 0

        if not result.success:
            status = f"update failed at {sha}"
            for err in result.errors:
                status += f"\n  - {err}"
        elif result.warnings:
            detail = f"updated to {sha}" if commits > 0 else f"up to date at {sha}"
            w_count = len(result.warnings)
            plural = "s" if w_count != 1 else ""
            status = f"{detail} ({w_count} warning{plural})"
            for warn in result.warnings:
                status += f"\n  ⚠️ {warn}"
        else:
            status = "update successful"

        # Only attach log file if there were problems; clean success = simple message
        if not result.success or result.warnings:
            log_file = args.project_dir / "data" / "update.txt"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text("\n".join(_log_buffer) + "\n")
            print(status)
            print(f"<<FILE:{log_file}>>")
        else:
            print(status)

        return 0 if result.success else 1

    # Output for JSON mode
    if args.json:
        output = {
            "success": result.success,
            "errors": result.errors,
            "warnings": result.warnings,
        }

        if result.git_result:
            output["git"] = {
                "success": result.git_result.success,
                "commits": result.git_result.commit_count,
                "before": result.git_result.before_sha[:8],
                "after": result.git_result.after_sha[:8],
            }

        if result.service_status:
            output["service"] = {
                "running": result.service_status.running,
                "pid": result.service_status.pid,
            }

        print(json.dumps(output, indent=2))

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
