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
from dataclasses import dataclass, field, replace
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.update import (  # noqa: E402
    cal_integration,
    deps,
    env_sync,
    gh_auth,
    git,
    gws_auth,
    hardlinks,
    hooks,
    kokoro,
    log_cleanup,
    mcp_byob,
    mcp_memory,
    migrations,
    npm_tools,
    officecli,
    persona_drift,
    readme_check,
    redis_persistence,
    redis_replication,
    reflection_arm,
    reflection_register,
    reflections_yaml,
    rodney,
    sentry_cli,
    service,
    verify,
    zshenv_sync,
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
    do_log_cleanup: bool = True  # Deletes oversized log backups — off under --verify

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
            do_log_cleanup=True,
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
            do_log_cleanup=True,
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
            do_log_cleanup=False,  # --verify promises no changes; sweep deletes files
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
    projects_json_check: verify.ToolCheck | None = None
    sdlc_tool_check: verify.ToolCheck | None = None
    hardlink_result: hardlinks.HardlinkSyncResult | None = None
    env_sync_result: env_sync.EnvSyncResult | None = None
    reflections_sync_result: env_sync.ReflectionsSyncResult | None = None
    zshenv_sync_result: zshenv_sync.ZshenvSyncResult | None = None
    hook_audit: hooks.HookAuditResult | None = None
    migration_result: migrations.MigrationResult | None = None
    reflections_yaml_result: reflections_yaml.ReflectionsYamlMigrationResult | None = None
    reflection_arm_result: reflection_arm.ArmResult | None = None
    reflection_register_result: reflection_register.RegisterResult | None = None
    baseline_refresh_register_result: reflection_register.RegisterResult | None = None
    officecli_result: officecli.InstallResult | None = None
    rodney_result: rodney.InstallResult | None = None
    npm_tools_result: npm_tools.NpmToolsResult | None = None
    sentry_cli_result: sentry_cli.InstallResult | None = None
    kokoro_result: kokoro.DownloadResult | None = None
    ffmpeg_result: kokoro.FfmpegResult | None = None
    redis_persistence_result: redis_persistence.RedisPersistenceResult | None = None
    redis_replication_result: redis_replication.RedisReplicationResult | None = None
    readme_check_result: readme_check.ReadmeCheckResult | None = None
    log_cleanup_result: log_cleanup.LogCleanupResult | None = None
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

    from bridge.utc import to_unix_ts
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
            updated_ts = to_unix_ts(getattr(s, "updated_at", None))
            if updated_ts is not None:
                recency = now - updated_ts
                if recency < RECENT_ACTIVITY_WINDOW:
                    skipped_live += 1
                    continue  # recent heartbeat activity — session is live

            # Fallback liveness check: created_at age (for sessions without updated_at)
            created_ts = to_unix_ts(s.created_at)
            if created_ts is None:
                continue
            age = now - created_ts

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


def _cleanup_duplicate_sessions(project_dir: Path) -> int:
    """Kill pending sessions that re-process messages already handled by a completed session.

    A session is a re-run only if another session with the same
    (chat_id, telegram_message_id) has already reached ``completed`` — the sole
    status that means the message was actually handled. A prior ``killed`` /
    ``abandoned`` / ``failed`` attempt did NOT handle the message, so a legitimate
    ``pending`` retry after one of those must survive (issue #1877 defect #4).
    Pending duplicates of a completed message are killed before the worker picks
    them up.

    Returns the number of sessions killed.
    """
    from collections import defaultdict

    from models.agent_session import AgentSession
    from models.session_lifecycle import finalize_session

    # Collect pending sessions that have a telegram_message_id
    pending = list(AgentSession.query.filter(status="pending"))
    pending_by_key: dict[tuple[str, int], list] = defaultdict(list)
    for s in pending:
        msg_id = s.telegram_message_id
        chat_id = getattr(s, "chat_id", None)
        if msg_id and chat_id:
            pending_by_key[(str(chat_id), int(msg_id))].append(s)

    if not pending_by_key:
        return 0

    # Find sessions that actually HANDLED the same keys. Only `completed` counts:
    # a killed/abandoned/failed attempt left the message unhandled, so a pending
    # retry must not be suppressed by one (issue #1877 defect #4).
    terminal_keys: set[tuple[str, int]] = set()
    for status in ("completed",):
        for s in AgentSession.query.filter(status=status):
            msg_id = s.telegram_message_id
            chat_id = getattr(s, "chat_id", None)
            if msg_id and chat_id:
                terminal_keys.add((str(chat_id), int(msg_id)))

    killed = 0
    for key, sessions in pending_by_key.items():
        if key not in terminal_keys:
            continue
        for s in sessions:
            try:
                finalize_session(
                    s,
                    "killed",
                    reason="re-run of already-handled message",
                    skip_checkpoint=True,
                )
                killed += 1
            except Exception as exc:
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "[update] Failed to kill duplicate session %s: %s",
                    getattr(s, "agent_session_id", "?"),
                    exc,
                )

    return killed


# Tight per-invocation timeout for the best-effort valor-catchup final step.
# valor-catchup reads recent threads + runs an LLM judge per owned chat; the
# CLI itself already exits 0 on partial failure, but a hung Telethon connect or
# a stalled LLM call must NEVER stall /update. This ceiling bounds the worst
# case and is enforced via subprocess timeout (the subprocess is killed on
# expiry and the TimeoutExpired is swallowed).
CATCHUP_STEP_TIMEOUT_SECONDS = 90


def run_catchup_step(
    project_dir: Path,
    log_fn=log,
    timeout: int = CATCHUP_STEP_TIMEOUT_SECONDS,
) -> None:
    """Best-effort final ``/update`` step: invoke ``valor-catchup`` if healthy.

    Runs strictly LAST in ``run_update`` (after all service-management and
    health checks). Gated on BOTH the bridge AND the worker reporting
    ``running`` — if either is down, the step logs a skip and returns without
    invoking anything.

    When the gate passes, ``valor-catchup`` is invoked as a SUBPROCESS (clean
    isolation, killable on timeout) with a tight per-invocation ``timeout``.
    The invocation is wrapped in a best-effort try/except: any failure,
    non-zero exit, or timeout is logged and swallowed. ``/update`` completion
    is wholly independent of ``valor-catchup``'s outcome — this function never
    raises and returns ``None`` regardless of what happens.

    Args:
        project_dir: Project root (passed through to the status checks).
        log_fn: Logging callback (defaults to the module ``log``); injectable
            so unit tests can capture emitted lines.
        timeout: Per-invocation subprocess timeout in seconds.
    """
    import subprocess

    try:
        bridge_status = service.get_service_status(project_dir)
        worker_status = service.get_worker_status(project_dir)
    except Exception as exc:
        # Even the health gate must never raise out of this step.
        log_fn(f"catchup: skipped — health-gate check failed ({exc})")
        return

    if not (bridge_status.running and worker_status.running):
        which = []
        if not bridge_status.running:
            which.append("bridge")
        if not worker_status.running:
            which.append("worker")
        log_fn(
            f"catchup: skipped — {', '.join(which)} not running "
            "(agent-judgment catchup requires both bridge and worker)"
        )
        return

    log_fn("catchup: running valor-catchup (best-effort, agent-judgment recovery)...")
    try:
        proc = subprocess.run(
            ["valor-catchup"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            log_fn("catchup: valor-catchup completed")
        else:
            # valor-catchup exits 0 even on partial failure, so a non-zero exit
            # means the CLI itself failed to start/parse. Log and continue.
            log_fn(
                f"catchup: valor-catchup exited {proc.returncode} "
                f"(swallowed): {proc.stderr.strip() or 'no stderr'}"
            )
    except subprocess.TimeoutExpired:
        log_fn(
            f"catchup: valor-catchup timed out after {timeout}s (swallowed — "
            "/update completion is unaffected)"
        )
    except Exception as exc:
        log_fn(f"catchup: valor-catchup invocation failed (swallowed): {exc}")
    # Returns None unconditionally — outcome cannot influence UpdateResult.


def run_release_verify(
    project_dir: Path, machine_check: dict, result: UpdateResult, v: bool
) -> None:
    """Terminal release verify for the --full path (issue #1898).

    After Step 5's synchronous restart, confirm the bridge and worker
    actually run code at pulled HEAD — positive staleness against each
    process's OWN relevant path set (never raw HEAD equality). Any in-role
    ``stale`` → hard error naming both short-SHAs + ``result.success =
    False`` (non-zero exit), a ``data/update-release-failed`` sentinel on a
    bridge hard-fail (a stale bridge cannot be trusted to report its own
    failure — the watchdog reads it), and a Sentry capture as the durable
    off-machine record. ``unknown`` → warn only. A clean pass with the bridge
    positively ``matches`` clears any earlier sentinel (fleet recovered).
    Never raises.
    """
    try:
        head_short = git.get_short_sha(project_dir)
        release_check = service.verify_running_release(project_dir, head_short, machine_check)
        for name, info in release_check.items():
            if info.get("classification") == "unknown":
                log(f"WARN: {name} release could not be confirmed (unknown)", v, always=True)
                result.warnings.append(f"{name} release could not be confirmed")
        release_stale = {
            name: info
            for name, info in release_check.items()
            if info.get("classification") == "stale"
        }
        if not release_stale:
            if release_check.get("bridge", {}).get("classification") == "matches":
                # Fleet recovered — clear any earlier out-of-band sentinel so
                # the watchdog stops surfacing a resolved failure every 60s.
                # Positive `matches` only: an `unknown` pass must not erase a
                # genuine failure record.
                try:
                    (project_dir / "data" / "update-release-failed").unlink(missing_ok=True)
                except Exception as unlink_err:
                    log(f"WARN: could not clear update-release-failed sentinel: {unlink_err}", v)
            return
        details = "; ".join(
            f"{name} running {info.get('boot_sha') or '?'} but HEAD is {head_short}"
            for name, info in release_stale.items()
        )
        log(f"ERROR: release verify FAILED @ {head_short}: {details}", v, always=True)
        result.warnings.append(f"release verify FAILED: {details}")
        result.success = False
        if "bridge" in release_stale:
            try:
                import json as _json
                import time as _time

                sentinel = project_dir / "data" / "update-release-failed"
                sentinel.write_text(
                    _json.dumps(
                        {
                            "process": "bridge",
                            "boot_sha": release_stale["bridge"].get("boot_sha"),
                            "head_sha": head_short,
                            "ts": _time.time(),
                        }
                    )
                    + "\n"
                )
            except Exception as sentinel_err:
                log(
                    f"WARN: could not write update-release-failed sentinel: {sentinel_err}",
                    v,
                    always=True,
                )
        # Durable off-machine record of the hard-fail.
        try:
            import sentry_sdk

            from monitoring.sentry_config import configure_sentry

            if configure_sentry("update"):
                sentry_sdk.capture_message(
                    f"update release verify FAILED @ {head_short}: {details}",
                    level="error",
                )
        except Exception as sentry_err:
            log(f"WARN: Sentry capture failed: {sentry_err}", v)
    except Exception as verify_err:
        log(f"WARN: release verify errored (inconclusive): {verify_err}", v, always=True)


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

    # Step 1.55: Heal launchd plist PATH entries (ensure ~/.local/bin is present)
    healed_plists = service.heal_plist_paths(project_dir)
    if healed_plists:
        for label in healed_plists:
            log(f"Healed PATH in {label}.plist (added ~/.local/bin)", v, always=True)
        result.warnings.append(
            f"Healed {len(healed_plists)} plist(s) missing ~/.local/bin in PATH — "
            "services reloaded automatically"
        )

    # Step 1.56: Remove launchd jobs for features that have been fully deleted
    # from the codebase (see service.OBSOLETE_SERVICE_SUFFIXES). Without this,
    # a removed feature's plist keeps loading and failing on every machine that
    # was provisioned before the removal. Runs unconditionally (like Step 1.55)
    # since dead-job cleanup is self-healing hygiene, not a service mutation.
    obsolete_removed = service.remove_obsolete_services()
    for label in obsolete_removed:
        log(f"Removed obsolete launchd job {label} (feature deleted from codebase)", v, always=True)

    # Step 1.6: Verify .env symlink
    log("Verifying .env symlink...", v)
    result.env_sync_result = env_sync.sync_env_from_vault(project_dir)
    env_r = result.env_sync_result
    if env_r.created:
        log(".env symlink created → ~/Desktop/Valor/.env", v, always=True)
    if env_r.error:
        log(f"WARN: Env symlink: {env_r.error}", v)
        result.warnings.append(f"Env symlink: {env_r.error}")

    # Step 1.65: Ensure config/projects.json is a real file copy (never a symlink —
    # launchd TCC blocks open() on iCloud-synced ~/Desktop paths).
    log("Verifying config/projects.json...", v)
    projects_r = env_sync.sync_projects_json(project_dir)
    if projects_r.created:
        log("config/projects.json copied from vault (was symlink or stale)", v, always=True)
    elif projects_r.ok:
        log("config/projects.json OK (real file copy)", v)
    if projects_r.error:
        log(f"WARN: projects.json: {projects_r.error}", v, always=True)
        result.warnings.append(f"projects.json: {projects_r.error}")

    # Step 1.655: Ensure the crash-recovery reflection is registered in the
    # vault registry (issue #1917). Runs BEFORE Step 1.66's vault→config copy
    # (critique NIT) so the appended entry propagates into the per-machine
    # config/reflections.yaml on this same cycle. Guarded on vault presence +
    # 'valor' ownership; idempotent no-op once the entry exists.
    log("Ensuring crash-recovery reflection is registered...", v)
    result.reflection_register_result = reflection_register.register_crash_recovery(project_dir)
    rr = result.reflection_register_result
    if rr.action == "registered":
        log("crash-recovery reflection registered in vault reflections.yaml", v, always=True)
    elif rr.action == "noop":
        log("crash-recovery reflection already registered", v)
    elif rr.action == "skipped":
        log(f"crash-recovery registration skipped: {rr.detail}", v)
    if not rr.success:
        log(f"WARN: crash-recovery registration: {rr.detail}", v, always=True)
        result.warnings.append(f"crash-recovery registration: {rr.detail}")

    # Step 1.656: Ensure the weekly test-baseline-refresh reflection is
    # registered (#1933/#2004) via the same generalized register path. Same
    # ordering rationale as Step 1.655: runs BEFORE Step 1.66's vault→config
    # copy so the entry propagates on this same cycle.
    log("Ensuring test-baseline-refresh reflection is registered...", v)
    result.baseline_refresh_register_result = reflection_register.register_test_baseline_refresh(
        project_dir
    )
    br = result.baseline_refresh_register_result
    if br.action == "registered":
        log("test-baseline-refresh reflection registered in vault reflections.yaml", v, always=True)
    elif br.action == "noop":
        log("test-baseline-refresh reflection already registered", v)
    elif br.action == "skipped":
        log(f"test-baseline-refresh registration skipped: {br.detail}", v)
    if not br.success:
        log(f"WARN: test-baseline-refresh registration: {br.detail}", v, always=True)
        result.warnings.append(f"test-baseline-refresh registration: {br.detail}")

    # Step 1.66: Ensure config/reflections.yaml is a real file copy (never a
    # symlink — the launchd worker's reflection scheduler reads it, and a
    # symlink to ~/Desktop hangs the asyncio event loop under launchd TCC).
    log("Verifying config/reflections.yaml...", v)
    result.reflections_sync_result = env_sync.sync_reflections_yaml(project_dir)
    refl_r = result.reflections_sync_result
    if refl_r.created:
        log("config/reflections.yaml copied from vault (was symlink or stale)", v, always=True)
    elif refl_r.ok:
        log("config/reflections.yaml OK (real file copy)", v)
    elif refl_r.skipped:
        log("config/reflections.yaml: vault not found, using in-repo fallback", v)
    if refl_r.error:
        log(f"WARN: reflections.yaml: {refl_r.error}", v, always=True)
        result.warnings.append(f"reflections.yaml: {refl_r.error}")

    # Step 1.67: Bootstrap cross-machine zshenv loader.
    # Seeds ~/Desktop/Valor/zshenv.sh (vault) if missing and ensures ~/.zshenv
    # sources it. Idempotent — most runs are no-ops. Critical on fresh machines
    # so shared secrets (GITHUB_PAT_*, etc.) land in every shell.
    log("Verifying ~/.zshenv → vault loader...", v)
    result.zshenv_sync_result = zshenv_sync.sync_zshenv()
    zr = result.zshenv_sync_result
    if zr.vault_seeded:
        log("Seeded ~/Desktop/Valor/zshenv.sh (vault loader)", v, always=True)
    if zr.guard_added:
        log("Added Valor source guard to ~/.zshenv", v, always=True)
    if zr.error:
        log(f"WARN: zshenv sync: {zr.error}", v, always=True)
        result.warnings.append(f"zshenv sync: {zr.error}")

    # Step 1.68: Configure gh CLI with GITHUB_PAT_YUDAME.
    # Ensures all machines use the correct primary GitHub token consistently.
    # Idempotent — safe to run on every update tick.
    log("Configuring gh CLI auth...", v)
    gh_auth_result = gh_auth.configure_gh_auth(project_dir)
    if gh_auth_result.action == "configured":
        log("gh auth: configured with GITHUB_PAT_YUDAME", v, always=True)
    elif gh_auth_result.action == "skipped":
        log(f"gh auth: skipped — {gh_auth_result.detail}", v)
    elif not gh_auth_result.success:
        log(f"WARN: gh auth: {gh_auth_result.error}", v, always=True)
        result.warnings.append(f"gh auth: {gh_auth_result.error}")

    # Step 1.69: Check Google Workspace CLI (`gws`) auth state.
    # Detection only — the OAuth consent flow is human-gated and browser-based,
    # so we surface an actionable step rather than auto-running it (cron-safe).
    log("Checking gws auth...", v)
    gws_auth_result = gws_auth.configure_gws_auth(project_dir)
    if gws_auth_result.action == "already_ok":
        log(f"gws auth: {gws_auth_result.detail}", v)
    elif gws_auth_result.action == "skipped":
        log(f"gws auth: skipped — {gws_auth_result.detail}", v)
    elif gws_auth_result.action == "needs_auth":
        log(f"WARN: gws auth: {gws_auth_result.detail}", v, always=True)
        result.warnings.append(f"gws auth: {gws_auth_result.detail}")
    elif not gws_auth_result.success:
        log(f"WARN: gws auth: {gws_auth_result.error}", v, always=True)
        result.warnings.append(f"gws auth: {gws_auth_result.error}")

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

    # Step 2.6: Determine whether this machine is the lockfile maintainer.
    # `projects.json` assigns each project to exactly one machine via the
    # `machine` field (matched against `scutil --get ComputerName`). The
    # designated machine is the sole writer of `uv.lock` — every other machine
    # uses `uv sync --frozen` so the lockfile stays byte-stable across the
    # fleet. On followers, defensively reset a locally-modified `uv.lock` to
    # HEAD: it might be a leftover from the pre-frozen era, or a conflicted
    # stash-pop from a maintainer push that landed during this same run.
    machine_info = verify.check_machine_identity(project_dir)
    is_lockfile_maintainer = "valor" in machine_info.get("projects", [])
    if not is_lockfile_maintainer:
        lock_dirty = deps.run_cmd(
            ["git", "status", "--porcelain", "uv.lock"],
            cwd=project_dir,
            check=False,
        ).stdout.strip()
        if lock_dirty:
            log(
                f"Resetting locally-modified uv.lock (follower machine="
                f"{machine_info.get('hostname', '?')})",
                v,
                always=True,
            )
            deps.run_cmd(
                ["git", "checkout", "HEAD", "--", "uv.lock"],
                cwd=project_dir,
                check=False,
            )

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

    # Step 3.5: Auto-bump critical deps from PyPI.
    #
    # Only the lockfile-maintainer machine (see Step 2.6) runs auto-bump.
    # Without this gate, all four machines would race to bump the same package
    # and produce divergent lockfiles every cron tick.
    if config.do_auto_bump and not is_lockfile_maintainer:
        log(
            f"Skipping auto-bump (not lockfile maintainer; "
            f"this machine={machine_info.get('hostname', '?')})",
            v,
        )
    if config.do_auto_bump and is_lockfile_maintainer:
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
                    # Stage both pyproject.toml and uv.lock — the lockfile was
                    # regenerated by the unfrozen sync inside auto_bump_deps,
                    # and follower machines (`uv sync --frozen`) need it on
                    # origin to install the new pins.
                    deps.run_cmd(
                        ["git", "add", "pyproject.toml", "uv.lock"],
                        cwd=project_dir,
                    )
                    deps.run_cmd(
                        ["git", "commit", "-m", msg],
                        cwd=project_dir,
                    )
                    try:
                        deps.run_cmd(
                            ["git", "push"],
                            cwd=project_dir,
                        )
                        log(f"Committed and pushed: {msg}", v, always=True)
                    except Exception:
                        # Push rejected — another machine may have pushed the same bump.
                        # Pull rebase and re-push; if our changes are already present,
                        # reset to origin/main (no warning needed).
                        try:
                            deps.run_cmd(
                                ["git", "pull", "--rebase", "origin", "main"],
                                cwd=project_dir,
                            )
                            # Check if our commit is still ahead of origin
                            ahead = deps.run_cmd(
                                ["git", "rev-list", "--count", "origin/main..HEAD"],
                                cwd=project_dir,
                                check=False,
                            ).stdout.strip()
                            if ahead and int(ahead) > 0:
                                deps.run_cmd(
                                    ["git", "push"],
                                    cwd=project_dir,
                                )
                                log(f"Committed and pushed (after rebase): {msg}", v, always=True)
                            else:
                                # Remote already has the same bump — reset local commit
                                deps.run_cmd(
                                    ["git", "reset", "--hard", "origin/main"],
                                    cwd=project_dir,
                                )
                                log(
                                    f"Dep bump already on remote, skipping push: {msg}",
                                    v,
                                    always=True,
                                )
                        except Exception as e2:
                            log(f"WARN: Failed to push dep bump: {e2}", v)
                            result.warnings.append("Dep bump succeeded but commit/push failed")
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

    # Step 3.65: Migrate reflections.yaml (interval: -> every:) on every pull.
    # Idempotent — issue #1273 unified Reflection grammar. Runs after Step 3
    # `uv sync` so the migration's schema-validation phase can import croniter.
    log("Migrating reflections.yaml schedule grammar...", v)
    result.reflections_yaml_result = reflections_yaml.run_reflections_yaml_migration(project_dir)
    ry = result.reflections_yaml_result
    if ry.success:
        if ry.action == "rewrote":
            log(
                f"  reflections.yaml: rewrote {ry.rewrites_count} interval line(s) -> every:",
                v,
                always=True,
            )
        elif ry.action == "noop":
            log("  reflections.yaml: already migrated", v)
        elif ry.action == "skipped":
            log(
                f"  reflections.yaml: skipped ({ry.error or 'target missing'})",
                v,
            )
    else:
        log(f"  WARN: reflections.yaml migration failed: {ry.error}", v, always=True)
        result.warnings.append(f"reflections.yaml migration: {ry.error}")

    # Step 3.66: Arm the merged-branch-cleanup plan-migration backstop
    # (issue #1900, Tier 0). Runs after the reflections.yaml copy (Step 1.66)
    # and grammar migration (Step 3.65) so it flips the CURRENT vault + repo
    # copies. Guarded on the vault file existing and this machine owning the
    # 'valor' project -- a no-op everywhere else.
    log("Arming plan-migration backstop reflection...", v)
    result.reflection_arm_result = reflection_arm.arm_merged_branch_cleanup(project_dir)
    ar = result.reflection_arm_result
    if ar.action == "armed":
        log(f"  merged-branch-cleanup: {ar.detail}", v, always=True)
    elif ar.action == "noop":
        log(f"  merged-branch-cleanup: {ar.detail}", v)
    elif ar.action == "skipped":
        log(f"  merged-branch-cleanup: skipped ({ar.detail})", v)
    if not ar.success:
        log(f"  WARN: merged-branch-cleanup arm failed: {ar.detail}", v, always=True)
        result.warnings.append(f"merged-branch-cleanup arm: {ar.detail}")

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

    # Step 3.9: npm global tools (excalidraw-export, etc.)
    log("Checking npm tools...", v)
    result.npm_tools_result = npm_tools.install_or_update()
    for npm_r in result.npm_tools_result.results:
        if npm_r.success:
            if npm_r.action == "skipped":
                log(f"  {npm_r.name} {npm_r.version} (up to date)", v)
            else:
                log(f"  {npm_r.name} {npm_r.action}: {npm_r.version}", v, always=True)
        else:
            if npm_r.name == "npm":
                log("  WARN: npm not available — skipping npm tools", v)
            else:
                log(f"  WARN: {npm_r.name}: {npm_r.error}", v)
                result.warnings.append(f"npm:{npm_r.name}: {npm_r.error}")

    # Step 3.10: sentry-cli install/update
    log("Checking sentry-cli...", v)
    result.sentry_cli_result = sentry_cli.install_or_update()
    sr = result.sentry_cli_result
    if sr.success:
        if sr.action == "skipped":
            log(f"sentry-cli {sr.version} (up to date)", v)
        else:
            log(f"sentry-cli {sr.action}: {sr.version}", v, always=True)
    else:
        log(f"WARN: sentry-cli {sr.action}: {sr.error}", v)
        result.warnings.append(f"sentry-cli: {sr.error}")

    # Step 3.11: Kokoro TTS model + voices download.
    # Idempotent: skipped when both files are already present in the cache
    # directory ($KOKORO_MODELS_DIR or ~/.cache/kokoro-onnx/). The single
    # voices-v1.0.bin asset bundles every voice (am_michael default,
    # bf_alice female alternative, etc.), so there's no per-voice fetch.
    # Failures are non-fatal — the TTS layer falls back to OpenAI tts-1.
    log("Checking Kokoro TTS models...", v)
    result.kokoro_result = kokoro.ensure_models(project_dir)
    kr = result.kokoro_result
    if kr.success:
        if kr.action == "skipped":
            log(f"Kokoro models OK ({kr.models_dir})", v)
        else:
            log(f"Kokoro models downloaded ({kr.models_dir})", v, always=True)
    else:
        log(f"WARN: Kokoro download: {kr.error}", v)
        result.warnings.append(f"Kokoro: {kr.error}")

    # Step 3.12: ffmpeg — Kokoro encodes WAV -> OGG/Opus via ffmpeg. Without
    # it on PATH the local TTS backend reports unavailable and voice synthesis
    # silently falls back to the paid OpenAI tts-1 path. Non-fatal: a warning,
    # since cloud TTS still works.
    log("Checking ffmpeg (Kokoro encode dependency)...", v)
    result.ffmpeg_result = kokoro.ensure_ffmpeg()
    fr = result.ffmpeg_result
    if fr.success:
        if fr.action == "present":
            log(f"ffmpeg OK ({fr.path})", v)
        else:
            log(f"ffmpeg installed ({fr.path})", v, always=True)
    else:
        log(f"WARN: ffmpeg: {fr.error}", v)
        result.warnings.append(f"ffmpeg: {fr.error}")

    # Step 3.13: Redis durability configuration.
    # Pins AOF persistence (appendonly yes, appendfsync everysec) and eviction
    # policy (maxmemory-policy noeviction) on every machine. Idempotent: CONFIG SET
    # is a no-op if already set. CONFIG REWRITE persists directives into redis.conf;
    # if Redis was started without a config file, a stub redis.conf is written and a
    # loud WARNING is emitted. Non-fatal: if redis-cli is absent or Redis is down,
    # the result is logged and the update continues.
    log("Configuring Redis durability (AOF + eviction policy)...", v)
    try:
        result.redis_persistence_result = redis_persistence.apply_redis_persistence()
        rp = result.redis_persistence_result
        if rp.success:
            if rp.action == "applied":
                log("Redis durability: AOF enabled and persisted to redis.conf", v, always=True)
            else:
                log(
                    f"Redis durability: AOF enabled ({rp.action})",
                    v,
                    always=True,
                )
            if rp.warning:
                log(f"WARN: Redis durability: {rp.warning}", v, always=True)
                result.warnings.append(f"Redis durability: {rp.warning}")
        elif rp.action == "skipped":
            log(f"Redis durability: skipped — {rp.error}", v)
        else:
            log(f"WARN: Redis durability: {rp.error}", v, always=True)
            result.warnings.append(f"Redis durability: {rp.error}")
    except Exception as _rp_exc:
        log(f"WARN: Redis durability step failed unexpectedly: {_rp_exc}", v, always=True)
        result.warnings.append(f"Redis durability: unexpected error: {_rp_exc}")

    # Step 3.14: Redis replication + Sentinel seeding (availability; #1827).
    # Durability (3.13) before availability (3.14). BOOTSTRAP-ONLY / seed-once: this
    # step is a clean no-op on every client-only machine (no data/redis-replication-
    # enabled marker) and on any established cluster (presence-check early-exit). It
    # NEVER CONFIG SET replicaof on a role:master node — seeding a virgin opted-in
    # node is file-only. Non-fatal: failures are logged and the update continues.
    log("Seeding Redis replication/Sentinel config (if opted in)...", v)
    try:
        result.redis_replication_result = redis_replication.apply_redis_replication()
        rr = result.redis_replication_result
        if rr.success:
            if rr.action in ("applied", "applied_with_warning"):
                log("Redis replication: seeded replica/Sentinel config", v, always=True)
            else:
                log(f"Redis replication: {rr.action}", v)
            if rr.warning:
                log(f"WARN: Redis replication: {rr.warning}", v, always=True)
                result.warnings.append(f"Redis replication: {rr.warning}")
        elif rr.action == "skipped":
            log(f"Redis replication: skipped — {rr.error}", v)
        else:
            log(f"WARN: Redis replication: {rr.error}", v, always=True)
            result.warnings.append(f"Redis replication: {rr.error}")
    except Exception as _rr_exc:
        log(f"WARN: Redis replication step failed unexpectedly: {_rr_exc}", v, always=True)
        result.warnings.append(f"Redis replication: unexpected error: {_rr_exc}")

    # Step 4: Ollama generation model (full mode only).
    # Ensures the configured ollama_generation_model. For a :cloud tag this is a
    # near-no-op reachability/signin check (no heavy local pull); for an -mlx tag
    # it is the RAM-guarded probe→pull-once path inside ensure_generation_model().
    # The granite *classifier* stays for bridge routing (its removal is issue
    # #1923's scope). The superseded gemma4:e2b rm is gated on classifier
    # presence + the spike-1 parity marker (see Step 4.76).
    if config.do_ollama:
        from config.models import ensure_generation_model
        from config.settings import settings as _settings

        log("Checking Ollama generation model...", v)
        ollama_model = _settings.models.ollama_generation_model
        gen_ok, gen_detail = ensure_generation_model(ollama_model)
        if gen_ok:
            log(f"Generation model OK ({ollama_model}): {gen_detail}", v)
        else:
            log(f"WARN: generation model {ollama_model}: {gen_detail}", v, always=True)
            result.warnings.append(f"generation model {ollama_model}: {gen_detail}")
        # Cloud-signin precondition: a cloud tag needs the host signed in.
        # Ollama persists signin via SSH keypair at ~/.ollama/id_ed25519 —
        # there is no ":cloud" model entry in `ollama list`.
        from config.models import _is_cloud_tag

        if _is_cloud_tag(ollama_model):
            import pathlib as _pathlib

            _key = _pathlib.Path.home() / ".ollama" / "id_ed25519"
            if not _key.exists():
                msg = (
                    "Ollama Cloud not signed in (no ~/.ollama/id_ed25519) — "
                    f"generation model {ollama_model} will be unreachable. "
                    "Run: ollama signin"
                )
                log(f"WARN: {msg}", v, always=True)
                result.warnings.append(msg)

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

    # Step 4.5: Telegram auth check (warn only — bridge is optional, worker runs without it)
    # Skipped on machines with no Telegram-configured projects (no bridge to authorize).
    if config.do_service_restart and machine_check.get("bridge_projects"):
        log("Checking Telegram session...", v)
        telegram_check = verify.check_telegram_session(project_dir)
        if telegram_check.available:
            log(f"  Telegram: {telegram_check.version or 'OK'}", v)
        else:
            log(
                f"WARN: Telegram session not authorized: {telegram_check.error}",
                v,
                always=True,
            )
            result.warnings.append(f"Telegram auth: {telegram_check.error}")

    # Step 4.6: Validate projects.json — green-light gate for service restart.
    # If the iCloud-synced config maps any contact to multiple machines (or
    # is otherwise malformed), abort the restart so the running bridge keeps
    # serving on the old, validated config.
    if config.do_service_restart and machine_check.get("projects"):
        log("Validating projects.json...", v)
        result.projects_json_check = verify.check_projects_json(project_dir)
        if result.projects_json_check.available:
            log(f"  projects.json: {result.projects_json_check.version}", v)
        else:
            log(
                f"FAIL: projects.json validation failed — skipping service restart\n"
                f"  {result.projects_json_check.error}",
                v,
                always=True,
            )
            result.warnings.append(
                f"projects.json invalid; bridge restart skipped: {result.projects_json_check.error}"
            )
            # Suppress restart for the rest of this run. The existing bridge
            # process keeps running on the previously validated config.
            config = replace(config, do_service_restart=False)

    # Step 4.7: Validate sdlc-tool wrapper — green-light gate for service restart.
    # The wrapper resolves SDLC tool dispatch from any cwd; if it's missing or
    # broken, the bridge-spawned PM session can't record verdicts and the SDLC
    # router will oscillate. Same gate pattern as projects.json: skip restart,
    # leave the running bridge on the previously validated build.
    if config.do_service_restart:
        log("Validating sdlc-tool wrapper...", v)
        result.sdlc_tool_check = verify.check_sdlc_tool(project_dir)
        if result.sdlc_tool_check.available:
            log(f"  sdlc-tool: {result.sdlc_tool_check.version}", v)
        else:
            log(
                f"FAIL: sdlc-tool validation failed — skipping service restart\n"
                f"  {result.sdlc_tool_check.error}",
                v,
                always=True,
            )
            result.warnings.append(
                f"sdlc-tool invalid; bridge restart skipped: {result.sdlc_tool_check.error}"
            )
            config = replace(config, do_service_restart=False)

    # Step 4.75: Surface stale legacy GRANITE_* env keys (plan #1924). The
    # settings group renamed to SESSION_RUNNER__* when the PTY substrate was
    # deleted; pydantic ignores unknown keys silently, so a stale vault/plist
    # override would otherwise be a silent no-op on this machine forever.
    # Non-blocking: warn loudly here and let settings import warn at runtime.
    # (The former Step 4.75 granite-classifier green-light gate was deleted
    # with the PTY substrate — classifier gating, if bridge routing needs
    # one, is issue #1923's scope.)
    try:
        from config.settings import stale_granite_env_keys

        _stale_granite = stale_granite_env_keys(project_dir / ".env")
        if _stale_granite:
            _stale_msg = (
                "stale legacy GRANITE_* env keys (ignored since plan #1924's "
                f"PTY teardown): {', '.join(_stale_granite)} — rename to "
                "SESSION_RUNNER__* or delete from ~/Desktop/Valor/.env and "
                "the launchd plists"
            )
            log(f"WARN: {_stale_msg}", v, always=True)
            result.warnings.append(_stale_msg)
    except Exception as _stale_exc:
        log(f"WARN: stale GRANITE_* env-key scan failed: {_stale_exc}", v)

    # Step 4.76: Retire superseded Ollama models. The gemma4:e2b rm is
    # irreversible per-machine, so it is gated on BOTH (a) the granite
    # classifier model being PRESENT on this machine (presence check only —
    # the former Step 4.75 restart-blocking smoke gate died with the PTY
    # substrate, plan #1924; never delete gemma while its replacement
    # classifier is absent), AND (b) the spike-1 parity marker
    # `data/spike1_parity_ok` (shadow-mode, a valid poor-parity response,
    # needs gemma resident — never delete it out from under shadow-mode). If
    # either is missing, the machine keeps its superseded models until both
    # conditions hold.
    if config.do_ollama:
        from config.models import OLLAMA_CLASSIFIER_MODEL, OLLAMA_SUPERSEDED_MODELS

        classifier_present = verify.check_ollama(OLLAMA_CLASSIFIER_MODEL).available
        spike1_parity_ok = (project_dir / "data" / "spike1_parity_ok").exists()
        if classifier_present and spike1_parity_ok:
            log("Cleaning up superseded Ollama models...", v)
            for old_model in OLLAMA_SUPERSEDED_MODELS:
                try:
                    import subprocess as _sp_rm

                    rm_result = _sp_rm.run(
                        ["ollama", "rm", old_model],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if rm_result.returncode == 0:
                        log(f"  Removed {old_model}", v, always=True)
                    else:
                        stderr = rm_result.stderr.strip()
                        if "not found" in stderr.lower():
                            log(f"  {old_model} not present, skipping", v)
                        else:
                            log(f"  WARN: Failed to remove {old_model}: {stderr}", v)
                except Exception as e:
                    log(f"  WARN: Failed to remove {old_model}: {e}", v)
        else:
            reason = []
            if not classifier_present:
                reason.append("granite classifier model not present")
            if not spike1_parity_ok:
                reason.append("spike-1 parity marker absent")
            log(
                f"Skipping superseded-model cleanup ({'; '.join(reason)})",
                v,
            )

    # Step 4.8: Verify memory MCP registration in ~/.claude.json (idempotent).
    # Self-heals drift, fresh-machine setup, and manual edits. Runs in all
    # modes; --verify is read-only (LOCK_SH, no write), --full/--cron repair
    # under LOCK_EX. Failure is logged but non-fatal — memory MCP is a
    # convenience surface, not critical-path. Falls back gracefully when
    # Ollama is absent: stubs render as category-only, agent can still
    # call memory_get / memory_search via MCP tools.
    log("Verifying memory MCP registration...", v)
    _mcp_memory_write = config.do_service_restart  # full/cron only
    mcp_memory_result = mcp_memory.verify_memory_mcp(write=_mcp_memory_write)
    log(f"  {mcp_memory_result.message}", v)
    if not mcp_memory_result.ok:
        if _mcp_memory_write:
            result.warnings.append(f"memory MCP: {mcp_memory_result.message}")
        else:
            # --verify mode: report drift but do not warn aggressively
            result.warnings.append(f"memory MCP drift: {mcp_memory_result.message}")

    # Optional Ollama ping for the title-gen worker — non-fatal.
    if config.do_ollama:
        _ollama_ok, _ollama_msg = mcp_memory.check_ollama_for_titles()
        log(f"  {_ollama_msg}", v)
        if not _ollama_ok:
            # Title-gen falls back to category-only stubs — informational only.
            pass

    # Step 4.9: Verify BYOB MCP registration in ~/.claude.json (idempotent).
    # Same lock + atomic-write pattern as the memory MCP step above. The
    # registrar self-heals drift on every /update invocation regardless of
    # whether ~/.byob is being rebuilt this run. Failure is non-fatal at
    # the update level, but BYOB is the *only* browser surface (#1256), so
    # downstream skills that screenshot or drive the browser will surface
    # an explicit "BYOB bridge not running" error if it isn't registered.
    # macOS-only by ergonomics (BYOB ships a Chrome MV3 extension) but the
    # registrar itself is platform-agnostic; the BYOB binary install is
    # gated separately.
    log("Verifying BYOB MCP registration...", v)
    _mcp_byob_write = config.do_service_restart  # full/cron only
    mcp_byob_result = mcp_byob.verify_byob_mcp(write=_mcp_byob_write)
    log(f"  {mcp_byob_result.message}", v)
    if not mcp_byob_result.ok:
        if _mcp_byob_write:
            result.warnings.append(f"BYOB MCP: {mcp_byob_result.message}")
        else:
            # --verify mode: report drift but do not warn aggressively
            result.warnings.append(f"BYOB MCP drift: {mcp_byob_result.message}")

    # Step 4.10: Check PM persona overlay drift between in-repo template and private vault.
    # Surface only — never auto-merges. Fails gracefully if vault file absent (fresh machine).
    # All logic lives in scripts/update/persona_drift.py so unit tests exercise the real code.
    log("Checking PM persona overlay drift...", v)
    _persona_warnings = persona_drift.check_pm_persona_drift(project_dir)
    if _persona_warnings:
        for _w in _persona_warnings:
            log(f"  {_w}", v)
            result.warnings.append(_w)
    else:
        log("  PM persona overlay: in sync (or files absent)", v)

    # Step 4.95: Check that each active project repo has a '## Running' README section.
    # Warn only — never blocks the update. Guides devs to document startup commands
    # in their repo's README rather than relying on a generic skill to guess.
    log("Checking project READMEs for '## Running' section...", v)
    result.readme_check_result = readme_check.check_project_readmes(project_dir)
    rc = result.readme_check_result
    if rc.ok:
        log(f"  README check OK ({rc.checked} project(s))", v)
    else:
        for warn in rc.warnings:
            log(f"WARN: {warn}", v, always=True)
            result.warnings.extend(rc.warnings)

    # Step 4.96: Sweep oversized rotated log backups (*.log.N past a 100 MB
    # hard cap). Complements the 30-min log-rotate LaunchAgent, which only
    # ever re-checks the live file — a burst that lands a huge file into a
    # backup slot otherwise sits there until the live file grows enough to
    # cycle it out naturally, which may never happen. See scripts/log_rotate.py.
    # Gated on do_log_cleanup (off under --verify) since this deletes files —
    # --verify promises no changes.
    if config.do_log_cleanup:
        log("Sweeping oversized rotated log backups...", v)
        result.log_cleanup_result = log_cleanup.sweep_oversized_logs(project_dir)
        lc = result.log_cleanup_result
        if lc.warnings:
            for warn in lc.warnings:
                log(f"WARN: {warn}", v, always=True)
                result.warnings.append(warn)
        elif lc.removed:
            freed_mb = lc.freed_bytes / (1024 * 1024)
            log(
                f"  Removed {len(lc.removed)} oversized backup(s), freed {freed_mb:.1f} MB",
                v,
                always=True,
            )
        else:
            log("  No oversized log backups found", v)

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
        # Skipped on machines with no Telegram-configured projects
        # (valor-service.sh install gates bridge install on the same signal).
        has_bridge = bool(machine_check.get("bridge_projects"))
        if has_bridge:
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
                log(
                    "WARN: Bridge not running after restart (worker and web UI unaffected)",
                    v,
                    always=True,
                )
                result.warnings.append("Bridge not running after restart")
        else:
            log("Bridge: skipped (no projects assigned to this machine)", v)
            result.caffeinate_status = service.get_caffeinate_status()

        # Always force-restart the web UI on a service restart run so a stale
        # process (wrong Python, old code, missing routes) is replaced.
        if service.restart_webui(project_dir, force=True):
            log("Web UI restarted (port 8500)", v)
        else:
            log("WARN: Web UI failed to start", v, always=True)
            result.warnings.append("Web UI failed to start")

        # Check update cron
        if service.is_update_cron_installed():
            log("Update cron installed", v)
        else:
            result.warnings.append("Update cron not installed")

        # Install/reload standalone worker service
        if (project_dir / "com.valor.worker.plist").exists():
            worker_was_running = service.is_worker_running()
            if service.install_worker(project_dir):
                log("Worker service installed", v)
                # Verify worker starts and writes heartbeat.
                # Worker writes last_worker_connected on startup (before health loop),
                # so a fresh file confirms it's actually running and healthy.
                import time as _time

                heartbeat_file = project_dir / "data" / "last_worker_connected"
                install_ts = _time.time()
                # If the worker was already running before install (no-op plist),
                # its heartbeat predates install_ts — accept it as-is rather than
                # waiting for a fresh write that will never come.
                if worker_was_running and service.is_worker_running():
                    worker_pid = service.get_worker_pid()
                    log(f"Worker running (PID: {worker_pid})", v)
                    worker_healthy = True
                else:
                    worker_healthy = False
                    for _ in range(15):  # 30s window
                        _time.sleep(2)
                        if not service.is_worker_running():
                            continue
                        worker_pid = service.get_worker_pid()
                        # Check heartbeat was written after we started installing
                        try:
                            if (
                                heartbeat_file.exists()
                                and heartbeat_file.stat().st_mtime > install_ts
                            ):
                                log(f"Worker running (PID: {worker_pid})", v)
                                worker_healthy = True
                                break
                        except OSError:
                            pass
                    if not worker_healthy:
                        # Process present but heartbeat not yet written — warn but not an error
                        worker_pid = service.get_worker_pid()
                        if worker_pid:
                            log(
                                f"Worker running (PID: {worker_pid}) — heartbeat pending",
                                v,
                                always=True,
                            )
                            result.warnings.append(
                                "Worker started but heartbeat pending — "
                                "dashboard may show stale status briefly"
                            )
                        else:
                            # Kickstart fallback: force-start the service if launchd
                            # didn't auto-start after bootout+bootstrap.
                            import subprocess

                            uid = os.getuid()
                            try:
                                subprocess.run(
                                    ["launchctl", "kickstart", "-k", f"gui/{uid}/com.valor.worker"],
                                    capture_output=True,
                                )
                            except Exception as e:
                                log(f"launchctl kickstart failed: {e}", v, always=True)
                            # Re-poll up to 30s for worker heartbeat after kickstart.
                            # Worker startup (module imports, Redis connect, Popoto index
                            # rebuild, session recovery, orphan cleanup, claude binary
                            # smoke test) can take 10–20s on a loaded system; the previous
                            # 16s retry window would race and falsely report system
                            # degraded on every /update run. 15 iterations × 2s = 30s
                            # ceiling provides realistic headroom while keeping a 2s
                            # poll cadence for responsiveness when the worker comes up
                            # quickly. See issue #1098.
                            for _ in range(15):
                                _time.sleep(2)
                                if service.is_worker_running():
                                    worker_pid = service.get_worker_pid()
                                    try:
                                        if (
                                            heartbeat_file.exists()
                                            and heartbeat_file.stat().st_mtime > install_ts
                                        ):
                                            log(
                                                f"Worker running after kickstart"
                                                f" (PID: {worker_pid})",
                                                v,
                                                always=True,
                                            )
                                            worker_healthy = True
                                            break
                                    except OSError:
                                        pass
                            if not worker_healthy:
                                log(
                                    "ERROR: Worker not running after 30s kickstart retry window — "
                                    "system degraded",
                                    v,
                                    always=True,
                                )
                                result.warnings.append(
                                    "Worker not running after install and"
                                    " kickstart retry (30s window)"
                                )
                                result.success = False
            else:
                # #2089: install_worker() now returns False when the worker is
                # not running with a live PID after bootstrap + kickstart. A down
                # worker halts ALL session execution, so surface it as a loud
                # failure — never let the summary imply the worker is up.
                log(
                    "ERROR: Worker install failed — not running after bootstrap/kickstart; "
                    "queued sessions will not execute until the worker is restarted",
                    v,
                    always=True,
                )
                result.warnings.append(
                    "Worker install failed — worker not running (see update logs)"
                )
                result.success = False

        # Install/reload the reflection-scheduler subprocess (issue #1828).
        # UNCONDITIONAL (NOT under `if has_bridge:`) — the reflection subprocess must
        # install wherever the worker installs, and the shell script self-gates on
        # has_worker_role(). Placed AFTER the worker install/restart block is
        # load-bearing for cutover ordering: the new worker (no in-process scheduler)
        # comes up first, THEN this bootstraps the plist (RunAtLoad starts the
        # subprocess). Worker-first → at most a brief zero-scheduler window, never
        # two schedulers ticking at once.
        if (project_dir / "com.valor.reflection-worker.plist").exists():
            if service.install_reflection_worker(project_dir):
                log("Reflection-worker service installed/verified", v)
            else:
                log(
                    "WARN: Reflection-worker service install failed or not supported",
                    v,
                    always=True,
                )
                result.warnings.append("Reflection-worker service install failed")

        # Install nightly-tests launchd service on bridge machines.
        # The install script self-gates on has_bridge_role() — it skips
        # gracefully and removes stale plists on non-bridge machines.
        if has_bridge:
            if service.install_nightly_tests(project_dir):
                log("Nightly tests service installed/verified", v)
            else:
                log("WARN: Nightly tests service install failed or not supported", v, always=True)
                result.warnings.append("Nightly tests service install failed")
        else:
            log("Nightly tests: skipped (no projects assigned to this machine)", v)

        # Ensure email bridge is running if this machine has projects AND IMAP is configured.
        # If the machine has no projects, stop any stray email bridge process.
        has_projects = bool(machine_check.get("projects"))
        if has_projects and service.is_email_configured(project_dir):
            if service.is_email_running():
                log(f"Email bridge running (PID: {service.get_email_pid()})", v)
            else:
                log("Email bridge configured but stopped — starting...", v, always=True)
                if service.ensure_email_running(project_dir):
                    log(f"Email bridge started (PID: {service.get_email_pid()})", v, always=True)
                else:
                    log("WARN: Email bridge failed to start", v, always=True)
                    result.warnings.append("Email bridge configured but failed to start")
        else:
            if service.is_email_running():
                if not has_projects:
                    log(
                        "Email bridge running but no projects assigned to this machine — stopping",
                        v,
                        always=True,
                    )
                else:
                    log("Email bridge running but IMAP not configured — stopping", v, always=True)
                service.stop_email(project_dir)
                if not service.is_email_running():
                    log("Email bridge stopped", v, always=True)
                else:
                    log("WARN: Email bridge failed to stop", v, always=True)
                    result.warnings.append("Email bridge should not run here but failed to stop")
            elif not has_projects:
                log("Email bridge: skipped (no projects assigned to this machine)", v)
            else:
                log("Email bridge: skipped (IMAP_PASSWORD not configured)", v)

        # Install the user-space log-rotate LaunchAgent — replaces the prior
        # root-requiring newsyslog install. Runs every 30 minutes via launchd
        # under the user account, so `/update --full` never prompts for sudo.
        if service.install_log_rotate_agent(project_dir):
            log("Log-rotate LaunchAgent installed", v)
        else:
            log("WARN: Log-rotate LaunchAgent install failed", v, always=True)
            result.warnings.append("Log-rotate LaunchAgent install failed")

        # Best-effort cleanup of the stale /etc/newsyslog.d/valor.conf from
        # machines updated before this migration. Uses sudo -n so it never
        # prompts; a warning is logged if sudo isn't cached.
        if not service.remove_newsyslog_config():
            result.warnings.append(
                "Stale /etc/newsyslog.d/valor.conf still present — will cause "
                "double-rotation until manually removed"
            )

        # Terminal release verify (issue #1898): full-mode only — the
        # cron-path verify lives in remote-update.sh + handle_update_command.
        run_release_verify(project_dir, machine_check, result, v)

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

        # Returns dict {"corrupted": int, "orphans": int} as of issue #1271.
        cleanup_result = cleanup_corrupted_agent_sessions()
        if isinstance(cleanup_result, dict):
            corrupted = cleanup_result.get("corrupted", 0)
            orphans = cleanup_result.get("orphans", 0)
        else:
            corrupted = int(cleanup_result) if cleanup_result is not None else 0
            orphans = 0
        if corrupted > 0:
            log(f"Cleaned up {corrupted} corrupted session(s)", v)
        if orphans > 0:
            log(f"Reaped {orphans} orphan claude/MCP process(es)", v)
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

    try:
        dupe_killed = _cleanup_duplicate_sessions(project_dir)
        if dupe_killed > 0:
            log(
                f"Killed {dupe_killed} duplicate session(s) (already-handled messages)",
                v,
                always=True,
            )  # noqa: E501
    except Exception as e:
        log(f"WARN: Duplicate session cleanup failed: {e}", v)

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

        # Existence checks are pipelined in batches rather than issued as one
        # round trip per member — a bloated index (e.g. a status index that
        # leaked hundreds of thousands of stale pointers) turns a sequential
        # HGETALL-per-member scan into a multi-hour hang that starves every
        # other Redis client, including the worker's own startup cleanup.
        # EXISTS is equivalent to a non-empty HGETALL check here: Redis drops
        # a hash key automatically once its last field is removed, so a hash
        # can never exist-but-be-empty.
        stale_check_batch_size = 5000

        stale_by_index: dict[str, list[bytes]] = {}
        for index_key in index_keys:
            members = list(POPOTO_REDIS_DB.smembers(index_key))
            stale: list[bytes] = []
            for i in range(0, len(members), stale_check_batch_size):
                batch = members[i : i + stale_check_batch_size]
                pipe = POPOTO_REDIS_DB.pipeline(transaction=False)
                for m in batch:
                    pipe.exists(m)
                exists_results = pipe.execute()
                stale.extend(m for m, exists in zip(batch, exists_results) if not exists)
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

        # Report valor tool checks (env-completeness, etc.)
        for tool in result.verification.valor_tools:
            if not tool.available and tool.error:
                log(f"  WARN: {tool.name}: {tool.error}", v, always=True)
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

    # Step 9 (strictly last): best-effort agent-judgment catchup.
    #
    # Runs AFTER every service-management and health check above so the
    # bridge+worker health gate reflects this run's final state. Invokes
    # valor-catchup as a subprocess only when BOTH bridge and worker report
    # running; failure/timeout is logged and swallowed — /update completion is
    # wholly independent of valor-catchup's outcome (issue #1709). Gated on
    # do_service_restart so verify-only and follower-skip runs (which don't
    # bring services up) never trigger recovery enqueues.
    if config.do_service_restart:
        run_catchup_step(project_dir, log_fn=lambda m: log(m, v))

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
        "--no-pull",
        action="store_true",
        help="Skip git pull (caller already pulled before invoking this script)",
    )
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

    if args.no_pull:
        config.do_git_pull = False

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
        #
        # Short SHA of the live HEAD. get_short_sha() is a standalone
        # `git rev-parse --short HEAD` that returns the correct SHA regardless
        # of whether this orchestrator ran the pull itself — so it works on the
        # --no-pull / remote-update.sh path (the shell wrapper pulls, then calls
        # run.py with --no-pull, leaving result.git_result None). Only fall back
        # to "unknown" if the git call itself fails.
        try:
            sha = git.get_short_sha(args.project_dir)
        except Exception:
            sha = "unknown"

        # Commit count comes from the orchestrator's own pull result. On the
        # --no-pull path result.git_result is None because remote-update.sh did
        # the pull in the shell and never handed the pre-pull SHA to run.py, so
        # the count is genuinely unrecoverable here and stays 0 (which renders
        # the summary as "up to date at {sha}" rather than "updated to {sha}").
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

        # One-time valor-ingest backfill reminder, fired on the run that
        # actually installed the [knowledge] extra. Gated by a per-machine
        # flag file so cron updates don't re-nag. See plan C6 / Task 6.5.
        if result.dep_result and result.dep_result.backfill_reminder_needed:
            flag = Path.home() / ".cache" / "valor" / "markitdown-backfill-reminded"
            if not flag.exists():
                status += (
                    "\n\nTip: run 'valor-ingest --scan ~/work-vault/' to "
                    "backfill existing binary files into sidecars."
                )
                try:
                    flag.parent.mkdir(parents=True, exist_ok=True)
                    flag.touch()
                except OSError:
                    # Flag-file failure is not worth blocking the run.
                    pass

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
