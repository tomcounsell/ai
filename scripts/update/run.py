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
from datetime import UTC, datetime, timedelta
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
    reflections_callables,
    reflections_yaml,
    rodney,
    sentry_cli,
    service,
    verify,
    warn_state,
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

    # The authoritative "--verify promises no changes" flag (issues #2898,
    # #3026). Every step that mutates state outside this process must consult
    # it. The per-behavior booleans above are about WHICH steps a mode runs;
    # this one is about whether the run is allowed to leave a trace at all.
    #
    # It exists because the opt-out-per-behavior shape kept failing open: a
    # newly added mutating step defaults to running, so --verify quietly broke
    # its contract twice (warn_state persistence, then ~/.claude hardlinking
    # and migrations) before anyone noticed.
    read_only: bool = False

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
            read_only=True,
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
    reflection_removal_results: list[reflection_register.RegisterResult] = field(
        default_factory=list
    )
    memory_distill_backfill_register_result: reflection_register.RegisterResult | None = None
    sdlc_upvote_pickup_register_result: reflection_register.RegisterResult | None = None
    reflections_callables_result: reflections_callables.ReflectionsCallablesResult | None = None
    registry_probe_result: reflections_callables.RegistryProbeResult | None = None
    officecli_result: officecli.InstallResult | None = None
    rodney_result: rodney.InstallResult | None = None
    npm_tools_result: npm_tools.NpmToolsResult | None = None
    sentry_cli_result: sentry_cli.InstallResult | None = None
    kokoro_result: kokoro.DownloadResult | None = None
    ffmpeg_result: kokoro.FfmpegResult | None = None
    redis_persistence_result: redis_persistence.RedisPersistenceResult | None = None
    redis_replication_result: redis_replication.RedisReplicationResult | None = None
    # Untyped (`object`, not the concrete dataclass) so `run.py` never needs
    # a module-level import of `scripts.update.redis_flush_guard_pth` --
    # this step imports lazily so a missing module degrades to a warning,
    # never an ImportError at `/update` start (#2645).
    redis_flush_guard_install_results: object | None = None
    readme_check_result: readme_check.ReadmeCheckResult | None = None
    log_cleanup_result: log_cleanup.LogCleanupResult | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Keys whose warn_state.should_emit call returned True THIS run (#2845).
    # Subtracted from warn_state.active() when composing the `suppressed:`
    # trailer, so a key does not simultaneously warn AND get called
    # "unchanged since first warning" in the same run (Race 3's inverse
    # hazard — should_emit writes its signature the instant it returns True).
    warn_keys_emitted: set[str] = field(default_factory=set)


def _append_warning(result: UpdateResult, text: str) -> None:
    """Append a warning with embedded newlines collapsed to one physical line.

    The summary render block below renders one `⚠️` bullet per `result.warnings` entry
    (`status += f"\\n  ⚠️ {warn}"`) — a raw multi-line entry (an exception
    `str()`, a wrapped multi-line diagnostic) would render its sentinel on
    only the first physical line, dropping the rest. This is the exact
    truncation `extract_update_warnings` exists to prevent, reproduced
    through a different producer if any append site bypasses this helper
    (#2845).
    """
    result.warnings.append(" ".join(text.split("\n")))


def _append_error(result: UpdateResult, text: str) -> None:
    """Append an error with embedded newlines collapsed — the same hole
    exists on the failure path, and it is the path where the dropped tail
    is a stack trace."""
    result.errors.append(" ".join(text.split("\n")))


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


def apply_registry_probe_verdict(
    result: UpdateResult,
    probe_gate: reflections_callables.RegistryProbeResult,
    sentinel: Path,
    v: bool = False,
) -> bool:
    """Turn a Step 4.65 probe verdict into warnings/errors on ``result``.

    Returns ``True`` when the caller must suppress the service restart.

    Extracted from Step 4.65 so the verdict routing is reachable by a test.
    `run_update` is a two-thousand-line function that pulls git, installs
    launchd services, and writes an iCloud vault; nothing drives it end to end,
    which is why #3014 exists. That gap is tolerable for the restart-suppression
    half, which the shell independently re-derives from the sentinel — but under
    ``--verify`` there IS no sentinel and no shell, so the escalation below is
    the only channel the verdict has, and an inverted predicate here would be
    invisible. Hence a seam rather than a disclosed hole.

    Three shapes, distinguished by WHY the sentinel does or does not agree:

    1. Vacuous pass (``nothing_probed``). The gate proved nothing, so it must
       not render as a clean green: without a ``⚠️`` bullet
       ``extract_update_warnings`` finds nothing, no fix session is queued, and
       the cycle reports ``update OK`` on a machine whose registry is missing
       entirely. The restart is NOT suppressed — absence of a registry is no
       evidence a callable will not import, and blocking on it would wedge the
       cycle with no self-clearing path.
    2. Ordinary failure. Suppresses the restart and warns, deliberately NOT
       ``result.success = False``. Escalating here would destroy the mechanism
       it looks like it duplicates: ``remote-update.sh`` is ``set -euo
       pipefail`` and invokes ``run.py`` bare, so a non-zero exit aborts the
       shell at that line and everything after it becomes unreachable — the
       sentinel latch, the ``RESTART BLOCKED`` operator line, the
       ``RESTART_FAILED`` accounting, and the bridge's deliberate exemption from
       a registry fault. The shell already turns this fault into a non-zero
       terminal exit by the designed route, whenever the worker was actually due
       to restart: ``RESTART_FAILED=1`` is set inside ``if $NEED_RESTART && !
       $REGISTRY_PROBE_OK``, so a registry broken out-of-band with no
       worker-relevant commits still exits 0. Nothing is hidden — the
       ``always=True`` FAIL log prints and the warning produces a ``⚠️`` bullet.
    3. A failure the sentinel does not carry. The only in-process escalations,
       and they split by why it is absent:
       - ``sentinel_skipped`` (``--verify``): absent by design. There is no
         shell half on this path — ``remote-update.sh`` runs ``run.py --cron``,
         never ``--verify`` — so the "abort before the kickstart" reasoning does
         not apply. What remains is that a human or agent running ``/update
         --verify`` must not read a broken registry as a clean bill of health,
         and one warning among many is not enough for that. So this is a hard
         error and a non-zero exit: the loudest shape rather than the quietest.
       - ``not sentinel_recorded``: absent by accident, the write or unlink did
         not land. On the pass path a failed *clear* only over-blocks the next
         cycle, so it stays a warning. On the failure path the shell reads
         ``[ -f ]``, absence is its green light, and it restarts the worker
         after this process has exited. Losing the shell's own ``RESTART
         BLOCKED`` line to a ``set -e`` abort is the accepted cost: an abort
         with no kickstart beats a green gate.

    ``result.success`` is set directly rather than left to the ``if v:`` summary
    block at the end of ``run_update``: that block is skipped under
    ``--quiet``/``--json``, and these failures must exit non-zero on every
    invocation shape. Matches the other hard-error sites in this file.
    """
    suppress_restart = False

    if probe_gate.success and probe_gate.nothing_probed:
        log(f"WARN: registry probe proved nothing — {probe_gate.detail}", v, always=True)
        _append_warning(result, f"reflections registry probe proved nothing: {probe_gate.detail}")
    elif probe_gate.success:
        log(f"  registry probe: {probe_gate.detail}", v)
    else:
        log(
            f"FAIL: reflections-registry callables did not import — skipping service restart\n"
            f"  {probe_gate.detail}",
            v,
            always=True,
        )
        _append_warning(
            result,
            f"reflections registry callables unresolvable; service restart skipped: "
            f"{probe_gate.detail}",
        )
        suppress_restart = True

    if probe_gate.sentinel_skipped:
        if not probe_gate.success:
            # Two different trees are in play here, and the message keeps them
            # apart rather than using one path to stand for both. `sentinel` is
            # under `project_dir` — the tree the operator invoked `/update` in.
            # The registry copy that actually failed may be in a DIFFERENT one:
            # the probe falls back to the owning checkout when the current tree
            # carries no `config/reflections.yaml`, which is what a `--verify`
            # from a lane worktree hits. That path is not reconstructed here; it
            # arrives already named inside `probe_gate.detail`, which on the
            # probe's own resolve-failure path carries `FAIL: N of M registry
            # copy(ies) did not resolve: <paths>` from stderr. Other shapes
            # (timeout, missing script, bare exit code) name no copy, which is
            # why the clause below points at the fault rather than at a path.
            # Leading with `detail` is therefore what locates the fault; the
            # sentinel clause only explains why there is no other channel.
            _append_error(
                result,
                f"registry probe FAILED under --verify: {probe_gate.detail}. "
                f"No sentinel was written at {sentinel} (--verify makes no "
                f"changes, #3026), so this exit code is the whole verdict — the "
                f"reflection worker is not safe to restart until the fault above "
                f"is resolved.",
            )
            result.success = False
    elif not probe_gate.sentinel_recorded:
        if probe_gate.success:
            _append_warning(
                result,
                f"registry probe passed but could not clear {sentinel}; "
                f"the next update's restart will be blocked until it is removed",
            )
        else:
            _append_error(
                result,
                f"registry probe FAILED and could not stamp {sentinel} — "
                f"the shell half of /update would read the absent sentinel as a pass "
                f"and restart the worker onto an unresolvable registry. "
                f"Aborting instead: {probe_gate.detail}",
            )
            result.success = False

    return suppress_restart


def _cleanup_stale_sessions(
    project_dir: Path, age_minutes: int = 120
) -> tuple[int, int, int, int, int]:
    """Finalize ``running`` sessions that have no live execution process.

    Liveness ladder, evaluated in this order (every rung below the first is
    additive: it can only produce a skip, never a finalization):

    1. A live worker in ``_active_workers`` (in-process invocations only) → skip,
       terminal.
    2. ``is_ledger`` (#2042 CLI anchor rows, reusing ``agent.session_health._is_ledger``)
       → skip, terminal, counted as ``skipped_ledger``. Checked before the fence so
       no later rung can reach a ledger row: a ledger anchor has no subprocess by
       construction, so the process-liveness reaper below is structurally the
       wrong owner for it (see #2660, #2677 for the process-less reaper that is).
    3. The authoritative ``(exec_pid, pid_create_time)`` fence. When a session
       records both halves, :func:`agent.pid_fence.fence_is_live` answers "is the
       process we spawned still running?" directly. Fence live → skip, terminal.
       Fence dead → **does not** ``continue``; it only selects the finalization
       reason string and falls through to the rungs below. This fall-through is
       deliberate: a dead fence is strong evidence, but making it terminal would
       let a session be finalized seconds after spawn, bypassing the 120-minute
       ``created_at`` floor. The fence ADDS protection; it never subtracts it.
    4. ``last_heartbeat_at`` recency, within ``RECENT_ACTIVITY_WINDOW`` → skip,
       terminal, counted as ``skipped_heartbeat``. Written only by the executor's
       heartbeat loop (T+0 and every 60s tick via
       ``save(update_fields=["last_heartbeat_at"])``); no maintenance path ever
       sets it, so it cannot be forged by a sweep. Naive datetimes and raw floats
       are normalized by ``to_unix_ts`` and read as real heartbeats; only a
       missing or unparseable value falls through to the next rung.
    5. ``updated_at`` recency, within ``RECENT_ACTIVITY_WINDOW`` → skip, counted as
       ``skipped_recent``. Rows with no fence and no recent heartbeat — legacy
       records, and any row where ``pid_create_time`` was never recorded — keep the
       previous behavior. Per the legacy-row rule in ``agent/pid_fence.py``, an
       absent ``create_time`` means "unknown", so these finalizations do not claim
       that liveness was checked. This rung's live consumers are non-ledger rows:
       the local Claude Code CLI session kept fresh by the PostToolUse hook, and
       the enqueue window before a session's T+0 heartbeat lands.
    6. ``created_at`` age ≥ ``age_minutes`` → finalize. Last-resort check for
       sessions without an ``updated_at`` (created before the heartbeat feature
       existed).

    Terminal sessions (killed/abandoned/failed/completed) are preserved
    for reflections to analyze — reflections handles its own 90-day expiry.

    Returns:
        ``(killed_count, skipped_recent, skipped_fence_live, skipped_ledger,
        skipped_heartbeat)`` — sessions finalized, sessions skipped on
        ``updated_at`` recency alone, sessions skipped because their fence
        positively resolved to a live process, sessions skipped as non-executable
        ledger anchors, and sessions skipped on ``last_heartbeat_at`` recency. Each
        count is reported separately so an operator rolling ``/update`` across the
        fleet can see which signal drove each skip rather than inferring it from a
        total.
    """
    import time

    from agent.pid_fence import fence_is_live
    from agent.session_health import _is_ledger
    from models.agent_session import AgentSession
    from models.session_lifecycle import finalize_session
    from utils.utc import to_unix_ts

    # Attempt to import the live-worker registry; fails gracefully if the queue
    # module is not initialized in this process (standalone subprocess invocation).
    try:
        from agent.session_state import _active_workers as active_workers_registry
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "[update] Could not import _active_workers from agent.session_state — "
            "falling back to recency-threshold-only cleanup"
        )
        active_workers_registry = {}

    now = time.time()
    threshold = age_minutes * 60
    killed_count = 0
    skipped_recent = 0
    skipped_fence_live = 0
    skipped_ledger = 0
    skipped_heartbeat = 0

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

            # Ledger guard: a #2042 CLI anchor row has no subprocess by
            # construction, so this process-liveness reaper is structurally the
            # wrong owner for it. Checked before the fence so no later rung can
            # reach a ledger row (the #2660 write-authorization fix removed the
            # forged updated_at restamp that used to mask this gap). Scheduling
            # the correct issue-lock-based reaper for these rows is #2677.
            if _is_ledger(s):
                skipped_ledger += 1
                continue

            # Authoritative liveness check: the (exec_pid, pid_create_time) fence.
            # Checked ahead of recency so a fence-live session is skipped at any age,
            # and so the fence gate is visible in the run summary rather than hidden
            # behind a recency skip that would have covered the same session.
            fence = getattr(s, "live_fence", None)
            fence_pid = fence.get("pid") if isinstance(fence, dict) else None
            fence_ct = fence.get("create_time") if isinstance(fence, dict) else None
            fence_verified_dead = False
            if fence_pid is not None and fence_ct is not None:
                if fence_is_live(fence_pid, fence_ct):
                    skipped_fence_live += 1
                    continue  # the process we spawned is still running
                fence_verified_dead = True
                # Deliberately no `continue` here. A dead fence selects the
                # finalization reason below but still falls through the
                # remaining rungs — the fence ADDS protection, it never
                # subtracts it (making it terminal would bypass the
                # `age_minutes` floor for a session seconds after spawn).

            # last_heartbeat_at recency: written only by the executor's own
            # heartbeat loop (T+0 and every 60s tick), never by any maintenance
            # sweep, so it cannot be forged by a probe or a run-lock bind (#2660).
            # to_unix_ts normalizes both naive datetimes (popoto strips tzinfo,
            # so hydrated values are always naive) and raw floats into a unix
            # timestamp, so those are read as real heartbeats. It returns None
            # only for a missing or unparseable value, which falls through to
            # the next rung rather than being read as fresh.
            heartbeat_ts = to_unix_ts(getattr(s, "last_heartbeat_at", None))
            if heartbeat_ts is not None:
                heartbeat_recency = now - heartbeat_ts
                if heartbeat_recency < RECENT_ACTIVITY_WINDOW:
                    skipped_heartbeat += 1
                    continue  # recent heartbeat — treat as live

            # Fallback liveness check: updated_at recency
            updated_ts = to_unix_ts(getattr(s, "updated_at", None))
            if updated_ts is not None:
                recency = now - updated_ts
                if recency < RECENT_ACTIVITY_WINDOW:
                    skipped_recent += 1
                    continue  # recent heartbeat activity — treat as live

            # Last-resort liveness check: created_at age (for sessions without updated_at)
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
            #
            # The reason records which signal drove the decision. Only the fence
            # path can honestly assert that no live process remains; the age path
            # observed nothing about the process and says so.
            reason = (
                "stale cleanup (fence verified: recorded process not live)"
                if fence_verified_dead
                else "stale cleanup (stale heartbeat, liveness unverified)"
            )
            try:
                finalize_session(
                    s,
                    "killed",
                    reason=reason,
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

    return killed_count, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat


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
    import shutil

    # PATH resolution alone fails when /update is invoked as
    # `.venv/bin/python scripts/update/run.py` without activating the venv
    # (the documented invocation) — .venv/bin never lands on PATH in that
    # case, so a bare "valor-catchup" raises FileNotFoundError every time.
    # Prefer the sibling console-script next to the running interpreter.
    catchup_bin = Path(sys.executable).parent / "valor-catchup"
    if not catchup_bin.exists():
        catchup_bin = Path(shutil.which("valor-catchup") or "valor-catchup")
    try:
        proc = subprocess.run(
            [str(catchup_bin)],
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


# provisional/tunable — bounded poll for a fresh worker beacon after a
# self-healing kickstart (issues #2400/#2220). Mirrors verify_release.py's
# Race-1 beacon poll (POLL_ATTEMPTS x POLL_INTERVAL_SECONDS): 15 x 2s = 30s
# covers worker cold-start (module import, Redis connect, Popoto index
# rebuild, session recovery) with headroom. Env-overridable for slow hosts.
WORKER_SELF_HEAL_POLL_ATTEMPTS = int(os.environ.get("WORKER_SELF_HEAL_POLL_ATTEMPTS", "15"))
WORKER_SELF_HEAL_POLL_INTERVAL_S = float(os.environ.get("WORKER_SELF_HEAL_POLL_INTERVAL_S", "2"))


def _self_heal_stale_worker(project_dir: Path, since_ts: float, v: bool) -> str:
    """Restart a stale worker in place, then confirm it booted on new code.

    The ``/update --full`` Step 5 worker install is content-idempotent
    (``service.install_worker`` returns early when the plist is unchanged —
    the case for any code-only pull), so a manual ``/update`` run right after
    merging worker-relevant code does NOT restart the worker: it keeps serving
    the old SHA and the terminal verify correctly classifies it ``stale``.
    Unlike the cron path (``remote-update.sh`` kickstarts the worker on a
    worker-relevant diff BEFORE verifying), the full path had no restart
    trigger, so the alert could never self-heal and re-fired on every
    post-merge ``/update`` (issues #2400/#2220). This closes that gap: detect
    the staleness the verify already computed and fix it in the same run.

    Sequence (parity with ``install_worker``'s #2141 drain gate):
    1. Drain — wait for in-flight sessions to finish. If they don't drain in
       the window, DEFER (return ``"deferred"``): never kill a live PM turn;
       the 30-min cron will restart the worker on its next tick.
    2. ``launchctl kickstart -k`` the worker (atomic kill+restart).
    3. Poll (bounded) for a worker beacon fresher than ``since_ts`` — the same
       Race-1 mitigation as ``verify_release.py``. A fresh beacon proves the
       worker came up on new code.

    Returns one of ``"healed"`` | ``"deferred"`` | ``"failed"``. Never raises.
    """
    import time as _time

    # 1. Drain before restart (#2141). Drain-probe errors fail open (restart).
    try:
        from scripts.update.drain import DEFAULT_POLL_S, DEFAULT_TIMEOUT_S, wait_for_idle

        if not wait_for_idle(DEFAULT_TIMEOUT_S, DEFAULT_POLL_S, log=lambda m: log(m, v)):
            return "deferred"
    except Exception as drain_err:
        log(f"self-heal: drain probe failed ({drain_err}) — proceeding with restart", v)

    # 2. Kickstart the worker (atomic kill+restart via the service seam).
    if not service.kickstart_worker():
        return "failed"

    # 3. Poll for a worker beacon fresher than the restart moment.
    beacon_path = project_dir / "data" / "worker_boot_sha"
    for attempt in range(WORKER_SELF_HEAL_POLL_ATTEMPTS):
        beacon = service.read_boot_beacon(beacon_path)
        if beacon is not None and beacon[1] > since_ts:
            return "healed"
        if attempt < WORKER_SELF_HEAL_POLL_ATTEMPTS - 1:
            _time.sleep(WORKER_SELF_HEAL_POLL_INTERVAL_S)
    return "failed"


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

    Self-heal (issues #2400/#2220): a ``stale`` WORKER is restarted in place
    (drain → ``kickstart -k`` → beacon poll) BEFORE alerting — the full path's
    idempotent Step 5 install never restarts on a code-only pull, so without
    this the alert re-fired on every post-merge ``/update`` and could never
    recover. Only if the worker is STILL stale after the restart do we hard-
    fail + Sentry (now a genuine "worker won't come up on new code" signal);
    a busy-drain DEFER warns only (cron retries). The bridge is never self-
    restarted here (a bridge kickstart would SIGKILL this /update process).
    Never raises.
    """
    try:
        head_short = git.get_short_sha(project_dir)
        release_check = service.verify_running_release(project_dir, head_short, machine_check)

        # Self-heal a stale worker in place before alerting (issues #2400/#2220).
        worker_info = release_check.get("worker")
        if worker_info and worker_info.get("classification") == "stale":
            import time as _time

            since_ts = _time.time()
            log(
                f"worker stale on new code (running {worker_info.get('boot_sha') or '?'}, "
                f"HEAD {head_short}) — attempting self-healing restart",
                v,
                always=True,
            )
            outcome = _self_heal_stale_worker(project_dir, since_ts, v)
            if outcome == "healed":
                log("worker self-heal: restart succeeded, worker now on new code", v, always=True)
                # Re-verify so the refreshed worker classification feeds the
                # alert decision below (a healed worker must not still FAIL).
                release_check = service.verify_running_release(
                    project_dir, head_short, machine_check
                )
            elif outcome == "deferred":
                log(
                    "worker self-heal: restart DEFERRED (sessions in flight did not drain) — "
                    "the 30-min update cron will restart the worker next cycle",
                    v,
                    always=True,
                )
                _append_warning(
                    result,
                    "worker stale; self-heal restart deferred (sessions in flight) — "
                    "cron will retry next update cycle",
                )
                # A deferral is not a failure: drop the worker from alert
                # consideration so it neither hard-fails nor Sentry-alerts.
                release_check = {k: val for k, val in release_check.items() if k != "worker"}
            else:  # "failed" — restart ran but the worker never came up on new code
                log(
                    "worker self-heal: restart FAILED — worker did not come up on new code",
                    v,
                    always=True,
                )
                # Leave the worker in release_check → it falls through to the
                # hard-fail + Sentry path below (now a genuine failure signal).

        for name, info in release_check.items():
            if info.get("classification") == "unknown":
                log(f"WARN: {name} release could not be confirmed (unknown)", v, always=True)
                _append_warning(result, f"{name} release could not be confirmed")
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
        _append_warning(result, f"release verify FAILED: {details}")
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


_NIGHTLY_TESTS_STALE_AFTER = timedelta(days=2)


def _nightly_tests_staleness_warning(project_dir: Path) -> str | None:
    """Warn when the nightly detector is installed but has not run recently.

    This is the only check in the pipeline that observes the *absence* of a
    run. The clock is ``now - max(plist_mtime, run_at)``, never file-absence:
    the installer is idempotent and takes the "installed" leg on every
    ``/update``, so an absence-keyed check would warn on the very run that
    installs the detector and keep warning until a 03:00 night lands.

    Gated on the caller only invoking this on the ``"installed"`` leg — a
    plist booted out, a bootstrap that failed quietly, a machine asleep at
    03:00, or the detector's own run-lock collision (returns 0 silently) all
    look exactly like a green suite otherwise, and ``tools/doctor.py`` has no
    coverage for any of them.
    """
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.valor.nightly-tests.plist"
    anchor: datetime | None = None
    if plist_path.exists():
        try:
            anchor = datetime.fromtimestamp(plist_path.stat().st_mtime, UTC)
        except OSError:
            anchor = None

    run_at: datetime | None = None
    last_run_file = project_dir / "data" / "nightly_tests_last_run.json"
    try:
        state = json.loads(last_run_file.read_text())
        raw_run_at = state.get("run_at")
        run_at = datetime.fromisoformat(raw_run_at) if raw_run_at else None
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        run_at = None

    newest = max([d for d in (anchor, run_at) if d is not None], default=None)
    if newest is None or (datetime.now(UTC) - newest) >= _NIGHTLY_TESTS_STALE_AFTER:
        return "Nightly tests: service installed but last run is 2+ days old (or never ran)"
    return None


def _format_auto_bump_rollback_message(bump: deps.AutoBumpResult) -> tuple[str, str]:
    """Build the operator-facing log line and warning text for a rolled-back auto-bump.

    A gate that could not be evaluated on this machine (the ``llm`` phase's
    ``CompatResult.probe_skipped`` case: no Anthropic API key) is not an
    incompatible pair, and must not read like one to the operator -- even
    though both roll back. Split out of ``run_update`` so this text selection
    is unit-testable without driving the rest of that function's real
    system-touching steps.

    Returns ``(log_line, warning_text)``.
    """
    phase = bump.failed_phase or "gate"
    if bump.gate_unverifiable:
        log_line = f"WARN: Auto-bump rolled back ({phase} gate unverifiable, not incompatible)"
        warning_text = f"Auto-bump rolled back after {phase} phase was unverifiable (no API key)"
    else:
        log_line = f"WARN: Auto-bump rolled back ({phase} phase failed)"
        warning_text = f"Auto-bump rolled back after {phase} phase failure"
    return log_line, warning_text


def run_update(project_dir: Path, config: UpdateConfig) -> UpdateResult:
    """Run update with given configuration."""
    result = UpdateResult()
    v = config.verbose

    # Honor the read-only contract in the suppression-state module before any
    # step can call should_emit (#2898). Set unconditionally, so a non-verify
    # run in the same process re-enables persistence rather than inheriting a
    # previous verify run's switch.
    warn_state.set_read_only(config.read_only)

    # Step 1: Git pull
    if config.do_git_pull:
        log("Pulling latest changes...", v)
        result.git_result = git.git_pull(project_dir)

        if not result.git_result.success:
            log(f"FAIL: {result.git_result.error}", v)
            result.success = False
            _append_error(result, f"Git pull failed: {result.git_result.error}")
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
                _append_warning(result, "Local changes stashed but failed to restore")

    # Report which layout ~/.claude/hooks is in (issue #2567). Two machines in
    # different layouts produce different runtime behavior from identical code,
    # so every observation of that directory is untrustworthy until the layout
    # is named. Answer it here rather than leaving it to an ad-hoc `ls -ld`.
    #
    # Probed BEFORE the sync, which migrates the alias away: probing after would
    # only ever report the migrated layout, so the one run where the answer
    # carries information is the one run that could not report it.
    hooks_alias = hardlinks.user_hooks_root_is_repo_aliased(project_dir)
    if hooks_alias is not None:
        log(f"hooks: ~/.claude/hooks aliases the repo tree ({hooks_alias}) (see #2567)", v)
    elif (Path.home() / ".claude" / "hooks").is_symlink():
        log("hooks: ~/.claude/hooks is a symlink to a non-checkout path", v)
    else:
        log("hooks: ~/.claude/hooks is a real user directory", v)

    # Step 1.5: Sync .claude hardlinks (skills + commands to ~/.claude/)
    #
    # Writes into ~/.claude and can migrate the hooks dir-symlink, so it is a
    # real mutation of machine-global state and is off under --verify (#3026).
    # An empty result stands in so the reporting below still runs and honestly
    # reports zero rather than needing its own skip branch.
    if config.read_only:
        log("Skipping .claude hardlink sync — --verify makes no changes (#3026)", v, always=True)
        result.hardlink_result = hardlinks.HardlinkSyncResult()
    else:
        log("Syncing .claude hardlinks...", v)
        result.hardlink_result = hardlinks.sync_claude_dirs(project_dir)

    if hooks_alias is not None and not config.read_only:
        # The predicate lives beside its emitter in hardlinks so the two cannot
        # drift; matching a bare "dir-symlink" substring here once reported a
        # hooks migration that the skills migration had actually performed.
        if hardlinks.hooks_were_migrated(result.hardlink_result):
            log("hooks: migrated to a real user directory", v, always=True)
        else:
            # Left in place either because a parent carries the alias (nothing
            # at the hooks root to unlink) or because the sync did not reach the
            # migration. Re-probe rather than guess which.
            still = hardlinks.user_hooks_root_is_repo_aliased(project_dir)
            log(f"hooks: alias left in place ({still})", v, always=True)

    # Fleet-observable staleness signal (issue #2561). The global-scope hooks
    # import a shared sibling helper that registers no hook, so it has no
    # manifest declaration; if deployment ever re-narrows to declared files,
    # every global hook dies with ModuleNotFoundError in every foreign repo and
    # nothing else says so. This greppable line is how a machine reports
    # whether the sync actually took.
    if (Path.home() / ".claude/hooks/sdlc/sdlc_context.py").exists():
        log("hooks: sdlc_context deployed", v)
    else:
        log("hooks: MISSING sdlc_context (see #2561)", v, always=True)
        _append_warning(result, "hooks: MISSING sdlc_context (see #2561)")

    if result.hardlink_result.created > 0:
        log(f"Created {result.hardlink_result.created} new hardlink(s)", v, always=True)
        for action in result.hardlink_result.actions:
            if action.action == "created":
                log(f"  {action.dst}", v, always=True)
    if result.hardlink_result.removed > 0:
        # "removed" covers four unrelated events: a stale hardlink swept by
        # RENAMED_REMOVALS, the ~/.claude/{skills,hooks} dir-symlink migration,
        # a hook deregistered by the dead-script sweep, and one deregistered by
        # the marker-keyed removal pass. Naming them all "stale hardlink(s)"
        # told an operator the wrong thing about three of the four, so the
        # count is generic and each line carries its own detail. Every pass
        # that increments `removed` must emit a matching "removed" action, or
        # this prints a bare number with nothing under it.
        log(
            f"Removed {result.hardlink_result.removed} item(s) from ~/.claude/",
            v,
            always=True,
        )
        for action in result.hardlink_result.actions:
            if action.action == "removed":
                detail = f" ({action.error})" if action.error else ""
                log(f"  {action.dst}{detail}", v, always=True)
    if result.hardlink_result.errors > 0:
        for action in result.hardlink_result.actions:
            if action.action == "error":
                log(f"WARN: {action.error} ({action.dst})", v)
                _append_warning(result, f"Hardlink step failed: {action.dst}")

    # Step 1.55: Heal launchd plist PATH entries (ensure ~/.local/bin is present)
    healed_plists = service.heal_plist_paths(project_dir)
    if healed_plists:
        for label in healed_plists:
            log(f"Healed PATH in {label}.plist (added ~/.local/bin)", v, always=True)
        _append_warning(
            result,
            f"Healed {len(healed_plists)} plist(s) missing ~/.local/bin in PATH — "
            "services reloaded automatically",
        )

    # Step 1.56: Remove launchd jobs for features that have been fully deleted
    # from the codebase (see service.OBSOLETE_SERVICE_SUFFIXES). Without this,
    # a removed feature's plist keeps loading and failing on every machine that
    # was provisioned before the removal. Runs unconditionally (like Step 1.55)
    # since dead-job cleanup is self-healing hygiene, not a service mutation.
    obsolete_removed = service.remove_obsolete_services()
    for label in obsolete_removed:
        log(f"Removed obsolete launchd job {label} (feature deleted from codebase)", v, always=True)

    # Steps 1.6-1.68 mutate machine-global state outside this checkout: the
    # .env symlink, config/projects.json, three reflection registrations into
    # the shared iCloud vault, the vault->config reflections copy, ~/.zshenv,
    # and gh CLI auth. All are off under --verify (#3026).
    #
    # The reflection registrations are the sharpest case. Since #2855 the
    # write-side resolver targets the vault unconditionally, so leaving these
    # ungated would make --verify write ~/Desktop/Valor/reflections.yaml --
    # a shared, iCloud-synced file -- where it previously only dirtied a
    # repo-local copy. The read_only flag has to lead the fix, not trail it.
    if config.read_only:
        log(
            "Skipping machine-global sync steps 1.6-1.68 "
            "(.env, projects.json, reflection registration, zshenv, gh auth) "
            "— --verify makes no changes (#3026)",
            v,
            always=True,
        )
    else:
        # Step 1.6: Verify .env symlink
        log("Verifying .env symlink...", v)
        result.env_sync_result = env_sync.sync_env_from_vault(project_dir)
        env_r = result.env_sync_result
        if env_r.created:
            log(".env symlink created → ~/Desktop/Valor/.env", v, always=True)
        if env_r.error:
            log(f"WARN: Env symlink: {env_r.error}", v)
            _append_warning(result, f"Env symlink: {env_r.error}")

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
            _append_warning(result, f"projects.json: {projects_r.error}")

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
            _append_warning(result, f"crash-recovery registration: {rr.detail}")

        # Step 1.656: Remove reflections whose callables no longer ship in the
        # repo (reflection_register.REMOVED_REFLECTIONS) so no machine keeps
        # scheduling an entry that can no longer import (#2376). Same ordering
        # rationale as Step 1.655: runs BEFORE Step 1.66's vault→config copy so
        # the removal propagates on this same cycle.
        for removed_name in reflection_register.REMOVED_REFLECTIONS:
            log(f"Ensuring {removed_name} reflection is removed...", v)
            rm = reflection_register.remove_reflection(project_dir, name=removed_name)
            result.reflection_removal_results.append(rm)
            if rm.action == "removed":
                log(
                    f"{removed_name} reflection removed from vault reflections.yaml", v, always=True
                )
            elif rm.action == "noop":
                log(f"{removed_name} reflection already absent", v)
            elif rm.action == "skipped":
                log(f"{removed_name} removal skipped: {rm.detail}", v)
            if not rm.success:
                log(f"WARN: {removed_name} removal: {rm.detail}", v, always=True)
                _append_warning(result, f"{removed_name} removal: {rm.detail}")

        # Step 1.657: Ensure the memory-distill-backfill reflection is registered
        # (#2202) via the same generalized register path. Same ordering rationale
        # as Steps 1.655/1.656: runs BEFORE Step 1.66's vault→config copy so the
        # entry propagates into the per-machine config/reflections.yaml on this
        # same cycle.
        log("Ensuring memory-distill-backfill reflection is registered...", v)
        result.memory_distill_backfill_register_result = (
            reflection_register.register_memory_distill_backfill(project_dir)
        )
        mdr = result.memory_distill_backfill_register_result
        if mdr.action == "registered":
            log(
                "memory-distill-backfill reflection registered in vault reflections.yaml",
                v,
                always=True,
            )
        elif mdr.action == "noop":
            log("memory-distill-backfill reflection already registered", v)
        elif mdr.action == "skipped":
            log(f"memory-distill-backfill registration skipped: {mdr.detail}", v)
        if not mdr.success:
            log(f"WARN: memory-distill-backfill registration: {mdr.detail}", v, always=True)
            _append_warning(result, f"memory-distill-backfill registration: {mdr.detail}")

        # Step 1.658: Ensure the sdlc-upvote-pickup reflection is registered
        # (#2717) via the same generalized register path. Same ordering
        # rationale as Steps 1.655/1.656/1.657: runs BEFORE Step 1.66's
        # vault→config copy so the entry propagates into the per-machine
        # config/reflections.yaml on this same cycle.
        log("Ensuring sdlc-upvote-pickup reflection is registered...", v)
        result.sdlc_upvote_pickup_register_result = reflection_register.register_sdlc_upvote_pickup(
            project_dir
        )
        upr = result.sdlc_upvote_pickup_register_result
        if upr.action == "registered":
            log(
                "sdlc-upvote-pickup reflection registered in vault reflections.yaml",
                v,
                always=True,
            )
        elif upr.action == "noop":
            log("sdlc-upvote-pickup reflection already registered", v)
        elif upr.action == "skipped":
            log(f"sdlc-upvote-pickup registration skipped: {upr.detail}", v)
        if not upr.success:
            log(f"WARN: sdlc-upvote-pickup registration: {upr.detail}", v, always=True)
            _append_warning(result, f"sdlc-upvote-pickup registration: {upr.detail}")

        # Step 1.659: Repoint reflection callables onto the modules that own them.
        # Two migration families share one table: the `agent.sustainability.*` shim
        # -> `reflections.agents.*` (#2875), and the `agent.agent_session_queue.*`
        # re-export hub -> `agent.session_health` / `agent.session_revival` (#2876).
        # `agent/sustainability.py` is deleted, so the registry must never reacquire
        # those paths; this step rewrites any that it still carries, which is what
        # keeps it alive after its own migration is done.
        # Keep these strings family-agnostic: naming one family makes the log false
        # on a machine where the other fires, and an operator verifying #2876's
        # propagation gate reads exactly this output.
        # config/reflections.yaml is gitignored, so this registry edit can only
        # reach machines as tracked code that rewrites the file. Runs BEFORE Step
        # 1.66's vault->config copy (same ordering rationale as Steps 1.655-1.658)
        # so a vault rewrite propagates on this same cycle; the migration also
        # rewrites the config copy directly, because Step 1.66 skips the copy when
        # the config copy is not older than the vault. Idempotent no-op once done.
        log("Ensuring reflection callables name their owning modules...", v)
        result.reflections_callables_result = (
            reflections_callables.run_reflections_callables_migration(project_dir)
        )
        rcr = result.reflections_callables_result
        if rcr.action == "rewrote":
            log(
                f"reflection callables repointed onto their owning modules "
                f"({rcr.rewrites_count} line(s) across {len(rcr.targets or [])} file(s))",
                v,
                always=True,
            )
        elif rcr.action == "noop":
            log("reflection callables already name their owning modules", v)
        if not rcr.success:
            log(f"WARN: reflection callable migration: {rcr.error}", v, always=True)
            _append_warning(result, f"reflection callable migration: {rcr.error}")
            # Warning only HERE, but not fail-open overall: Step 4.65's probe
            # independently checks whether the registry actually imports, and it
            # is that probe — not this rewriter's exit status — that suppresses
            # the service restart. A rewrite failure over a registry that was
            # already clean is genuinely harmless and stays a warning; one that
            # leaves the deleted shim named is caught downstream.

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
            _append_warning(result, f"reflections.yaml: {refl_r.error}")

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
            _append_warning(result, f"zshenv sync: {zr.error}")

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
            _append_warning(result, f"gh auth: {gh_auth_result.error}")

    # Step 1.69: Check Google Workspace CLI (`gws`) auth state.
    # Detection only — the OAuth consent flow is human-gated and browser-based,
    # so we surface an actionable step rather than auto-running it (cron-safe).
    log("Checking gws auth...", v)
    gws_auth_result = gws_auth.configure_gws_auth(project_dir)
    if gws_auth_result.action == "already_ok":
        log(f"gws auth: {gws_auth_result.detail}", v)
        # Resolution: clear any stored signature and emit one resolved note.
        if warn_state.should_emit("gws-auth", "", project_dir):
            log("gws auth: resolved", v, always=True)
    elif gws_auth_result.action == "skipped":
        log(f"gws auth: skipped — {gws_auth_result.detail}", v)
    elif gws_auth_result.action == "needs_auth":
        # Human-gated (browser OAuth consent, #2329-shaped): one emission
        # per state transition rather than every 30-minute cycle (#2845).
        # The signature is the auth-method string, so a change in the
        # method re-warns.
        signature = f"needs_auth:{gws_auth_result.detail}"
        if warn_state.should_emit("gws-auth", signature, project_dir):
            log(f"WARN: gws auth: {gws_auth_result.detail}", v, always=True)
            _append_warning(result, f"gws auth: {gws_auth_result.detail}")
            result.warn_keys_emitted.add("gws-auth")
    elif not gws_auth_result.success:
        log(f"WARN: gws auth: {gws_auth_result.error}", v, always=True)
        _append_warning(result, f"gws auth: {gws_auth_result.error}")

    # Step 1.7: Audit skill hooks for dangerous patterns
    log("Auditing skill hooks...", v)
    result.hook_audit = hooks.audit_skill_hooks(project_dir)
    if result.hook_audit.issues:
        for issue in result.hook_audit.issues:
            log(f"WARN: [{issue.skill}] {issue.detail}", v, always=True)
            _append_warning(result, f"Hook issue in {issue.skill}: {issue.issue_type}")
    else:
        log(f"Skill hooks OK ({result.hook_audit.skills_scanned} skills scanned)", v)

    # Step 2: Check for pending critical upgrades
    pending = git.check_upgrade_pending(project_dir)
    if pending.pending:
        log(f"WARNING: Critical dependency upgrade pending since {pending.timestamp}", v)
        _append_warning(result, f"Critical upgrade pending: {pending.reason}")

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
                _append_warning(result, f"Dep sync failed: {result.dep_result.error}")

            # Verify critical versions
            result.version_info = deps.verify_critical_versions(project_dir)
            mismatches = [vi for vi in result.version_info if not vi.matches]
            if mismatches:
                for vi in mismatches:
                    log(
                        f"WARN: {vi.package} version mismatch: {vi.version} != {vi.expected}",
                        v,
                    )
                    _append_warning(result, f"{vi.package} version mismatch")
        else:
            log("No dependency changes, skipping sync", v)

    # Step 3.05: Install the ambient production-Redis flush guard's `.pth`
    # shim into every repo venv (#2645). Unconditional and deliberately
    # OUTSIDE the `if config.do_dep_sync:` block above: Step 3 is
    # CONDITIONAL (only runs when dep files changed or `--force-dep-sync`),
    # so gating this step on it would leave a venv `uv sync` just created
    # unguarded until the next `/update` that happens to touch a dependency
    # file. Placed AFTER dep sync (not before) precisely so a venv `uv sync`
    # created or recreated moments ago in THIS run is guarded within the
    # same run -- `uv sync` is what creates `.venv` when absent
    # (scripts/update/deps.py::sync_dependencies), so installing before it
    # would find "no venv yet" and skip, leaving the freshly created venv
    # unguarded until the next `/update` (Risk 1). Idempotent + non-fatal:
    # log, warn, continue -- same contract as Step 3.13/3.14.
    log("Installing startup .pth shims (Redis flush guard, checkout pin) into repo venvs...", v)
    try:
        from scripts.update import redis_flush_guard_pth

        result.redis_flush_guard_install_results = redis_flush_guard_pth.install_fleet(project_dir)
        for venv_result in result.redis_flush_guard_install_results:
            status = venv_result.get("status")
            venv_label = venv_result.get("venv")
            if status in ("installed", "unchanged"):
                log(f"Redis flush guard: {venv_label} — {status}", v)
            else:
                log(
                    f"WARN: Redis flush guard: {venv_label} — skipped "
                    f"({venv_result.get('reason')})",
                    v,
                    always=True,
                )
                _append_warning(
                    result,
                    f"Redis flush guard not installed in {venv_label}: {venv_result.get('reason')}",
                )
    except Exception as _rfg_exc:
        log(
            f"WARN: Redis flush guard install step failed unexpectedly: {_rfg_exc}",
            v,
            always=True,
        )
        _append_warning(result, f"Redis flush guard install: unexpected error: {_rfg_exc}")

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
                # `restore_failed` blocks the commit below regardless of
                # which set bumped cleanly (deps.py's set loop only stops
                # AFTER the failing set, so an earlier set's clean bump is
                # still reported here) -- say so inline, not just in the
                # adjacent restore-failed warning, so the two lines don't
                # read in tension.
                suffix = " (not committed)" if bump.restore_failed else ""
                log(f"  {b.package}: {b.old_version} -> {b.new_version}{suffix}", v, always=True)
            elif b.error:
                log(f"  {b.package}: skip ({b.error})", v)
            else:
                log(f"  {b.package}: {b.old_version} (up to date)", v)

        if bump.rolled_back:
            log_line, warning_text = _format_auto_bump_rollback_message(bump)
            log(log_line, v, always=True)
            log(f"  Detail: {bump.smoke_output or bump.sync_error}", v)
            _append_warning(result, warning_text)

        if bump.restore_failed:
            # The rollback's own re-sync failed, so the venv does NOT match
            # the restored pyproject.toml. Nothing may be committed this run.
            log("WARN: Auto-bump rollback could not restore dependencies", v, always=True)
            _append_warning(
                result,
                "pyproject.toml and uv.lock restored from the pre-bump snapshot; "
                "the venv re-sync failed, so nothing was committed this run",
            )

        # A member is `bumped` only once its whole coupled set survived every
        # gate, so this branch never names a pin that was rolled back.
        if bump.any_bumped and not bump.restore_failed:
            log("Smoke test passed after bump", v, always=True)
            # Commit the pyproject.toml change
            try:
                bumped_pkgs = [
                    f"{b.package} {b.old_version}->{b.new_version}" for b in bump.bumps if b.bumped
                ]
                msg = (
                    f"Bump deps: {', '.join(bumped_pkgs)}\n\n"
                    "No-issue: automated coupled-set dependency bump, gated on "
                    "llm/import/pytest checks in auto_bump_deps"
                )
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
                        # Fetch + rebase onto the NAMED ref, not FETCH_HEAD
                        # (#2650): `git pull --rebase` resolves its onto-
                        # target through .git/FETCH_HEAD, which every
                        # worktree of the repo shares, so a peer lane's
                        # concurrent fetch can retarget our rebase.
                        deps.run_cmd(
                            ["git", "fetch", "origin", "main"],
                            cwd=project_dir,
                        )
                        deps.run_cmd(
                            ["git", "rebase", "origin/main"],
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
                        _append_warning(result, "Dep bump succeeded but commit/push failed")
            except Exception as e:
                log(f"WARN: Failed to commit bump: {e}", v)
                _append_warning(result, "Dep bump succeeded but commit/push failed")

    # Step 3.6: Run pending data migrations (after git pull, before service restart)
    #
    # Migrations rewrite persistent data and are irreversible, which makes them
    # the single least appropriate thing for a mode advertised as safe to run
    # from a scratch worktree (#3026). Off under --verify.
    if config.read_only:
        log("Skipping pending migrations — --verify makes no changes (#3026)", v, always=True)
        result.migration_result = migrations.MigrationResult()
    else:
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
            _append_error(result, f"Migration failed: {err}")
    if not mig.ran and not mig.failed:
        log("No pending migrations", v)

    # Step 3.65: Migrate reflections.yaml (interval: -> every:) on every pull.
    # Idempotent — issue #1273 unified Reflection grammar. Runs after Step 3
    # `uv sync` so the migration's schema-validation phase can import croniter.
    # Rewrites the reflections.yaml in the iCloud vault — off under --verify
    # (#3026). "Idempotent" is not the same as "no changes": it still writes.
    if config.read_only:
        log(
            "Skipping reflections.yaml grammar migration — --verify makes no changes (#3026)",
            v,
            always=True,
        )
    else:
        log("Migrating reflections.yaml schedule grammar...", v)
        result.reflections_yaml_result = reflections_yaml.run_reflections_yaml_migration(
            project_dir
        )
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
            _append_warning(result, f"reflections.yaml migration: {ry.error}")

    # Step 3.66: Arm the merged-branch-cleanup plan-migration backstop
    # (issue #1900, Tier 0). Runs after the reflections.yaml copy (Step 1.66)
    # and grammar migration (Step 3.65) so it flips the CURRENT vault + repo
    # copies. Guarded on the vault file existing and this machine owning the
    # 'valor' project -- a no-op everywhere else.
    # Flips the current vault + repo reflections copies — off under --verify (#3026).
    if config.read_only:
        log(
            "Skipping plan-migration backstop arming — --verify makes no changes (#3026)",
            v,
            always=True,
        )
    else:
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
            _append_warning(result, f"merged-branch-cleanup arm: {ar.detail}")

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
        _append_warning(result, f"OfficeCLI: {oc.error}")

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
        _append_warning(result, f"Rodney: {rr.error}")

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
                _append_warning(result, f"npm:{npm_r.name}: {npm_r.error}")

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
        _append_warning(result, f"sentry-cli: {sr.error}")

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
        _append_warning(result, f"Kokoro: {kr.error}")

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
        _append_warning(result, f"ffmpeg: {fr.error}")

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
                _append_warning(result, f"Redis durability: {rp.warning}")
        elif rp.action == "skipped":
            log(f"Redis durability: skipped — {rp.error}", v)
        else:
            log(f"WARN: Redis durability: {rp.error}", v, always=True)
            _append_warning(result, f"Redis durability: {rp.error}")
    except Exception as _rp_exc:
        log(f"WARN: Redis durability step failed unexpectedly: {_rp_exc}", v, always=True)
        _append_warning(result, f"Redis durability: unexpected error: {_rp_exc}")

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
                _append_warning(result, f"Redis replication: {rr.warning}")
        elif rr.action == "skipped":
            log(f"Redis replication: skipped — {rr.error}", v)
        else:
            log(f"WARN: Redis replication: {rr.error}", v, always=True)
            _append_warning(result, f"Redis replication: {rr.error}")
    except Exception as _rr_exc:
        log(f"WARN: Redis replication step failed unexpectedly: {_rr_exc}", v, always=True)
        _append_warning(result, f"Redis replication: unexpected error: {_rr_exc}")

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
            _append_warning(result, f"generation model {ollama_model}: {gen_detail}")
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
                _append_warning(result, msg)

    # Step 4.5: Machine identity verification
    log("Verifying machine identity...", v)
    machine_check = verify.check_machine_identity(project_dir)
    if machine_check.get("error"):
        log(f"WARN: {machine_check['error']}", v, always=True)
        _append_warning(result, machine_check["error"])
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
        _append_warning(
            result,
            f"No projects in config for machine '{machine_check.get('hostname')}'. "
            "Check 'machine' field in ~/Desktop/Valor/projects.json",
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
            _append_warning(result, f"Telegram auth: {telegram_check.error}")

    # Step 4.6: Validate projects.json — green-light gate for service restart.
    # If the iCloud-synced config maps any contact to multiple machines (or
    # is otherwise malformed), abort the restart so the running bridge keeps
    # serving on the old, validated config.
    if config.do_service_restart and machine_check.get("projects"):
        log("Validating projects.json...", v)
        result.projects_json_check = verify.check_projects_json(project_dir)
        # Ownership is reported on every run, pass or fail (#2541). Three weeks
        # of silence is what let twenty projects stay pointed at a sold laptop.
        if result.projects_json_check.detail:
            for line in result.projects_json_check.detail.splitlines():
                log(f"  ownership: {line}", v, always=True)
        if result.projects_json_check.available:
            log(f"  projects.json: {result.projects_json_check.version}", v)
        else:
            log(
                f"FAIL: projects.json validation failed — skipping service restart\n"
                f"  {result.projects_json_check.error}",
                v,
                always=True,
            )
            _append_warning(
                result,
                "projects.json invalid; bridge restart skipped: "
                f"{result.projects_json_check.error}",
            )
            # Suppress restart for the rest of this run. The existing bridge
            # process keeps running on the previously validated config.
            config = replace(config, do_service_restart=False)

    # Step 4.65: Reflections-registry import probe — green-light gate for the
    # in-process service restart below and, via the sentinel, for
    # `remote-update.sh`'s worker kickstart. See the #3029 note further down for
    # the restart it does NOT reach.
    #
    # Why a gate at all: while `agent/sustainability.py` existed it was the
    # backstop, so a registry still naming the shim imported anyway. #2875
    # deleted it. Restarting the worker against a registry whose callables do
    # not import means five self-healing reflections raise ImportError inside
    # `reflection_scheduler.run_reflection`'s broad `except`, which records
    # `state.last_error` and keeps ticking with no alert.
    #
    # Why the probe and not `reflections_callables_result.success`: that flag
    # only means "the Step 1.659 rewriter did not error". It is True for
    # `action="noop"`, which also covers *no registry present* and covers a
    # registry whose shim reference the line-anchored rewrite regex never
    # matched (a flow-style `{callable: agent.sustainability.x}` entry returns
    # early, BEFORE the import verification, and reports a clean noop). The
    # probe checks the actual property the restart needs: every `callable:` in
    # every existing registry copy imports with `agent.sustainability` banned.
    # Under `--verify` the flag is not merely weak but absent: Step 1.659 sits
    # inside the `read_only` skip block, so `reflections_callables_result` is
    # None there and the probe is the only signal that exists at all.
    #
    # Why the probe still RUNS under `read_only` but writes no sentinel: the
    # import check itself is pure — it shells out to
    # `scripts/verify_registry_without_shim.py`, which only imports — so the
    # diagnostic costs --verify nothing. The sentinel is the mutation, and it is
    # passed `record_sentinel=not config.read_only` for two reasons. First,
    # `data/registry-probe-failed` is machine-global state and #3026 makes
    # `--verify` promise to leave none. Second and sharper: the sentinel's PASS
    # path is an `unlink`, and `--verify` runs outside `remote-update.sh`'s
    # lockfile, so a passing --verify from a scratch worktree could delete a
    # failing verdict that script had just stamped — and absence is its green
    # light. The shell's mtime freshness bound does not cover that direction; it
    # only discards sentinels that are too OLD. Suppressing the I/O closes it.
    # The cost is that a --verify probe failure has no sentinel to carry the
    # verdict, so it is escalated straight to `result.errors` below instead.
    #
    # Why NOT gated on `config.do_service_restart`: `UpdateConfig.cron()` sets
    # that False, yet the cron path restarts the worker anyway — in the SHELL,
    # after this process exits (`scripts/remote-update.sh` runs `run.py --cron`
    # and then `launchctl kickstart -k`, gated only on the diff touching
    # `agent/`). Gating the check itself on `do_service_restart` therefore made
    # it inert on the one path this change actually deploys through. So the
    # probe always runs; `run_registry_probe` stamps `data/registry-probe-
    # failed`, which `remote-update.sh` consults before its own kickstart, and
    # a failure additionally suppresses the in-process restart below (same
    # posture as Steps 4.6/4.7).
    #
    # What `do_service_restart=False` does and does not buy. The intended
    # effect: it skips Step 5's in-process `service.install_*` calls, including
    # `install_reflection_worker`, which is the one that reloads the registry.
    #
    # Collateral, because every remaining reader of the flag sits after this
    # step: Step 4.7 stops validating the sdlc-tool wrapper, Steps 4.8/4.9 pass
    # `write=False` to the memory and BYOB MCP registrars so they report drift
    # instead of self-healing it, and the terminal `run_catchup_step` is skipped.
    # All three are the same "we are not bringing services up on this run"
    # posture the flag already means, and Steps 4.6/4.7 flip it for their own
    # faults with the identical reach, so the fault does not widen — but the
    # reach is the flag's, not this step's, and is stated here rather than
    # implied.
    #
    # It does NOT universally mean "nothing restarts" — on `--full` with commits
    # pulled, Step 5's `elif` branch calls `git.set_restart_requested()` and the
    # worker self-exits into a launchd relaunch, ungated. That is harmless here
    # because `com.valor.worker` never loads the reflections registry; the
    # process that does is `com.valor.reflection-worker`, and its (re)install is
    # exactly what this flag suppresses.
    #
    # Known gap, tracked as #3029: `install_reflection_worker` is the ONLY site
    # that restarts `com.valor.reflection-worker`, and it sits under
    # `do_service_restart`, which `UpdateConfig.cron()` sets False. On the
    # routine cron path the registry is therefore migrated and probed green
    # while the live scheduler — which calls `load()` once at start and never
    # reloads — keeps whatever it read at process start. The probe gate is
    # correct about what it blocks; it just has no restart to gate there.
    log("Probing reflections-registry callables for importability...", v)
    result.registry_probe_result = reflections_callables.run_registry_probe(
        project_dir, record_sentinel=not config.read_only
    )
    probe_gate = result.registry_probe_result
    # Routing lives in `apply_registry_probe_verdict` (module level, tested
    # directly); see its docstring for the three shapes and each one's fail
    # direction. It reports whether the restart must be suppressed rather than
    # editing `config`, because `config` is a local rebind here that a helper
    # cannot perform.
    if apply_registry_probe_verdict(
        result,
        probe_gate,
        project_dir / reflections_callables.PROBE_SENTINEL,
        v,
    ):
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
            _append_warning(
                result, f"sdlc-tool invalid; bridge restart skipped: {result.sdlc_tool_check.error}"
            )
            config = replace(config, do_service_restart=False)

    # Step 4.76: Retire superseded Ollama models. The gemma4:e2b rm is
    # irreversible per-machine, so it is gated on BOTH (a) the granite
    # classifier model being PRESENT on this machine (presence check only —
    # the restart-blocking classifier smoke gate that used to run here died
    # with the PTY substrate, plan #1924; never delete gemma while its
    # replacement classifier is absent), AND (b) the spike-1 parity marker
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
            _append_warning(result, f"memory MCP: {mcp_memory_result.message}")
        else:
            # --verify mode: report drift but do not warn aggressively
            _append_warning(result, f"memory MCP drift: {mcp_memory_result.message}")

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
            _append_warning(result, f"BYOB MCP: {mcp_byob_result.message}")
        else:
            # --verify mode: report drift but do not warn aggressively
            _append_warning(result, f"BYOB MCP drift: {mcp_byob_result.message}")

    # Step 4.10: Check persona overlay drift between in-repo templates and private
    # vault overlays (engineer + teammate — see persona_drift.PERSONA_OVERLAY_PAIRS).
    # Surface only — never auto-merges. Fails gracefully if a vault file is absent
    # (fresh machine). All logic lives in scripts/update/persona_drift.py so unit
    # tests exercise the real code.
    log("Checking persona overlay drift...", v)
    _persona_warnings = persona_drift.check_all_persona_drift(project_dir)
    if _persona_warnings:
        # Human-gated (#2893): the vault overlay is a standing per-machine
        # customization that no /update cycle can reconcile, so this collapses
        # to one emission per state transition. The joined warning text is the
        # signature, so a changed diff size re-warns.
        signature = f"unresolved:{' | '.join(_persona_warnings)}"
        if warn_state.should_emit("persona-drift", signature, project_dir):
            for _w in _persona_warnings:
                log(f"  {_w}", v)
                _append_warning(result, _w)
            result.warn_keys_emitted.add("persona-drift")
    else:
        # Resolved — clear stored state (and emit one resolved note) so a
        # future regression warns again instead of staying silent.
        if warn_state.should_emit("persona-drift", "", project_dir):
            log("  Persona overlay drift: resolved", v, always=True)
            result.warn_keys_emitted.add("persona-drift")
        log("  Persona overlays: in sync (or files absent)", v)

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
            _append_warning(result, warn)

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
                _append_warning(result, warn)
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
                _append_warning(result, "Failed to install caffeinate")

        # Install main service (handles both bridge and update cron)
        if service.install_service(project_dir):
            log("Services installed/restarted", v)
        else:
            _append_warning(result, "Service install may have failed")

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
                _append_warning(result, "Bridge not running after restart")
        else:
            log("Bridge: skipped (no projects assigned to this machine)", v)
            result.caffeinate_status = service.get_caffeinate_status()

        # Always force-restart the web UI on a service restart run so a stale
        # process (wrong Python, old code, missing routes) is replaced.
        if service.restart_webui(project_dir, force=True):
            log("Web UI restarted (port 8500)", v)
        else:
            log("WARN: Web UI failed to start", v, always=True)
            _append_warning(result, "Web UI failed to start")

        # Check update cron
        if service.is_update_cron_installed():
            log("Update cron installed", v)
        else:
            _append_warning(result, "Update cron not installed")

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
                            _append_warning(
                                result,
                                "Worker started but heartbeat pending — "
                                "dashboard may show stale status briefly",
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
                                _append_warning(
                                    result,
                                    "Worker not running after install and"
                                    " kickstart retry (30s window)",
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
                _append_warning(
                    result, "Worker install failed — worker not running (see update logs)"
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
                _append_warning(result, "Reflection-worker service install failed")

        # Install nightly-tests launchd service. UNCONDITIONAL (NOT under
        # `if has_bridge:`) — running the test suite requires a checkout and a
        # worker, not a Telegram bridge (issue #2823). The install script
        # self-gates on a worktree refusal plus has_worker_role() and reports
        # its outcome as a three-way result rather than a bool, so a role-gate
        # skip is never conflated with an install failure.
        #
        # Warnings go through _append_warning (#2845/#2892), never
        # result.warnings.append: a raw multi-line entry renders its sentinel
        # on only the first physical line and silently drops the rest.
        nightly_outcome = service.install_nightly_tests(project_dir)
        if nightly_outcome == "installed":
            log("Nightly tests service installed/verified", v)
            staleness_warning = _nightly_tests_staleness_warning(project_dir)
            if staleness_warning:
                _append_warning(result, staleness_warning)
        elif nightly_outcome == "skipped":
            log("Nightly tests: no regression coverage on this machine (install skipped)", v)
            _append_warning(
                result, "Nightly tests: no regression coverage on this machine (install skipped)"
            )
        else:
            log("WARN: Nightly tests service install failed or not supported", v, always=True)
            _append_warning(result, "Nightly tests service install failed")

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
                    _append_warning(result, "Email bridge configured but failed to start")
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
                    _append_warning(result, "Email bridge should not run here but failed to stop")
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
            _append_warning(result, "Log-rotate LaunchAgent install failed")

        # Best-effort cleanup of the stale /etc/newsyslog.d/valor.conf from
        # machines updated before this migration. Uses sudo -n so it never
        # prompts; a warning is logged if sudo isn't cached.
        if not service.remove_newsyslog_config():
            _append_warning(
                result,
                "Stale /etc/newsyslog.d/valor.conf still present — will cause "
                "double-rotation until manually removed",
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
        from agent.session_health import cleanup_corrupted_agent_sessions

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
        stale_killed, skipped_recent, skipped_fence_live, skipped_ledger, skipped_heartbeat = (
            _cleanup_stale_sessions(project_dir)
        )
        if stale_killed > 0:
            log(f"Cleaned up {stale_killed} stale session(s)", v)
        if skipped_fence_live > 0:
            log(f"Skipped {skipped_fence_live} live session(s) (fence verified)", v)
        if skipped_ledger > 0:
            log(f"Skipped {skipped_ledger} ledger anchor session(s) (non-executable)", v)
        if skipped_heartbeat > 0:
            log(f"Skipped {skipped_heartbeat} live session(s) (recent heartbeat)", v)
        if skipped_recent > 0:
            log(f"Skipped {skipped_recent} live session(s) (recent updated_at)", v)
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

        # Report the LLM stack compat verdict every run, pass or fail (#2541
        # style), so a silently stale venv is visible. This is a lookup by
        # name in the `valor_tools` list `verify_environment` already
        # returned — the generic warning loop below never reads `detail`,
        # and re-calling `verify` would run the compat subprocess twice.
        compat = next(
            (t for t in result.verification.valor_tools if t.name == "llm-stack-compat"),
            None,
        )
        if compat is not None:
            log(f"  llm-stack-compat: {compat.detail}", v, always=True)

        # Report system tools
        # claude CLI is optional — bridge uses SDK directly
        optional_tools = {"claude"}
        for tool in result.verification.system_tools:
            status = "OK" if tool.available else "MISSING"
            log(f"  {tool.name}: {status}", v)
            if not tool.available and tool.error:
                log(f"    {tool.error}", v, always=True)
                if tool.name not in optional_tools:
                    _append_warning(result, f"{tool.name}: {tool.error}")

        # Report valor tool checks (env-completeness, etc.)
        #
        # google-token (#2329) and sms_reader FDA (#2328) can only be cleared by a
        # one-time INTERACTIVE HUMAN step (browser OAuth consent / System Settings
        # Full Disk Access grant) — no agent and no /update cycle can resolve them.
        # Re-warning every 30 min is pure spam, so these two are suppressed to a
        # single emission per state transition (warn_state, #2329/#2328). The
        # ToolCheck.error already carries the exact human steps (see verify.py).
        # env-completeness joins by set membership (#2845): it is already a
        # `valor_tools` member, so it needs no call-site wiring — the loop's
        # `signature = f"unresolved:{tool.error}"` already encodes the gap's
        # content (a newly-missing required key re-warns), and this loop
        # already records the key into `result.warn_keys_emitted`. Without
        # this, every interactive `/update` on a machine with an incomplete
        # vault (this checkout's own machine included) enqueues a fix
        # session for a condition this plan defines as permanent-and-correct.
        # NOTE: `should_emit` writes state under `--verify` too, so a
        # diagnostic run consumes the emission the next cron cycle would have
        # made. Pre-existing since #2329 for the two incumbents; tracked as
        # #2898 rather than widened or fixed here.
        human_gated_tools = {"google-token", "sms_reader", "env-completeness"}
        for tool in result.verification.valor_tools:
            if not tool.available and tool.error:
                if tool.name in human_gated_tools:
                    signature = f"unresolved:{tool.error}"
                    if warn_state.should_emit(tool.name, signature, project_dir):
                        log(f"  ACTION REQUIRED — {tool.name}: {tool.error}", v, always=True)
                        _append_warning(result, f"{tool.name}: {tool.error}")
                        result.warn_keys_emitted.add(tool.name)
                    # else: already warned for this exact state — stay silent.
                    continue
                log(f"  WARN: {tool.name}: {tool.error}", v, always=True)
                _append_warning(result, f"{tool.name}: {tool.error}")
            elif tool.name in human_gated_tools and not (tool.version or "").startswith("skipped"):
                # Resolved — clear stored state (and emit one resolved note) so a
                # future regression warns again instead of staying silent.
                #
                # Gated on the check having actually PASSED, not merely on
                # `available=True`: check_env_completeness reaches this
                # branch on its transient-vault-outage skip paths too
                # (`version="skipped (.env not found)"`), and clearing state
                # there would re-arm the whole missing-key respam on the
                # next healthy run — the .env symlink into the iCloud vault
                # genuinely goes missing transiently (#2845).
                if warn_state.should_emit(tool.name, "", project_dir):
                    log(f"  {tool.name}: resolved", v, always=True)
                    result.warn_keys_emitted.add(tool.name)

        # Migrate legacy Desktop/claude_code paths in settings.json.
        # Rewrites a settings.json outside this checkout — off under --verify (#3026).
        if config.read_only:
            log(
                "  Settings: skipped path migration — --verify makes no changes (#3026)",
                v,
                always=True,
            )
        else:
            log("Migrating settings.json paths...", v)
            settings_migration = verify.migrate_settings_json_paths()
            if settings_migration.get("migrated"):
                log(f"  Settings: {settings_migration.get('reason')}", v, always=True)
            else:
                log(f"  Settings: {settings_migration.get('reason')}", v)

            # Pin every machine to the 'latest' Claude Code channel — 'stable'
            # lags many releases behind, starving the fleet of silent-exit fixes
            # and blocking newer model ids (#project_claude_cli_silent_exit).
            channel = verify.ensure_claude_update_channel()
            if channel.get("changed"):
                log(f"  Settings: {channel.get('reason')}", v, always=True)
            else:
                log(f"  Settings: {channel.get('reason')}", v)

        # Sync Claude OAuth credentials.
        # Writes a credential file outside this checkout — off under --verify (#3026).
        if config.read_only:
            log(
                "  OAuth: skipped credential sync — --verify makes no changes (#3026)",
                v,
                always=True,
            )
        else:
            log("Syncing Claude OAuth credentials...", v)
            oauth_sync = verify.sync_claude_oauth(project_dir)
            if oauth_sync.get("synced"):
                if oauth_sync.get("refreshed_from_live"):
                    log("  OAuth: refreshed source from live token", v)
                else:
                    log(f"  OAuth: {oauth_sync.get('reason')}", v)
                # Resolved — clear stored state (and emit one resolved note) so a
                # future regression warns again instead of staying silent.
                if warn_state.should_emit("oauth-sync", "", project_dir):
                    log("  OAuth sync: resolved", v, always=True)
                    result.warn_keys_emitted.add("oauth-sync")
            else:
                # Human-gated (#2893): the source credential at
                # ~/Desktop/Valor/claude_oauth_config.json is per-machine and only a
                # human can provision it — structurally the same shape as
                # google-token. One emission per state transition; the reason string
                # is the signature, so a different failure mode re-warns. The
                # verbose log stays outside the gate: it is diagnostic detail, not
                # summary output, so it cannot respam the cron channel.
                log(f"  OAuth: {oauth_sync.get('reason')}", v)
                signature = f"unresolved:{oauth_sync.get('reason')}"
                if warn_state.should_emit("oauth-sync", signature, project_dir):
                    _append_warning(result, f"OAuth sync: {oauth_sync.get('reason')}")
                    result.warn_keys_emitted.add("oauth-sync")

        # Report SDK auth
        auth = result.verification.sdk_auth
        if auth.get("claude_desktop_running"):
            log("  SDK auth: Claude Desktop (subscription)", v)
        elif auth.get("api_key_configured"):
            log("  SDK auth: API key", v)
        else:
            log("  SDK auth: NOT CONFIGURED", v)
            _append_warning(result, "SDK auth not configured")

        # Report gitignore issues (un-gitignored embeddings, etc.)
        if result.verification.gitignore_issues:
            for issue in result.verification.gitignore_issues:
                msg = f"{issue.repo}: {issue.file_path} ({issue.size_mb}MB) not in .gitignore"
                log(f"  WARN: {msg}", v, always=True)
                _append_warning(result, msg)

    # Step 7: Calendar integration
    if config.do_calendar:
        log("Checking calendar integration...", v)

        # Verify all Anthropic models are still valid
        model_errors = verify.verify_models(project_dir)
        for model_error in model_errors:
            log(f"WARN: {model_error}", v, always=True)
            _append_warning(result, model_error)

        # Global hook
        result.calendar_hook = cal_integration.ensure_global_hook(project_dir)
        if result.calendar_hook.configured:
            if result.calendar_hook.created:
                log("Calendar hook installed", v)
            else:
                log("Calendar hook OK", v)
        else:
            log(f"WARN: Calendar hook issue: {result.calendar_hook.error}", v)
            _append_warning(result, f"Calendar hook: {result.calendar_hook.error}")

        # Calendar config
        result.calendar_config = cal_integration.generate_calendar_config(project_dir)
        if result.calendar_config.success:
            # clear on resolve — defensive no-op on `warn_keys_emitted` (an
            # empty signature always clears state, so `active()` cannot
            # hold this key afterward), instrumented anyway per the flat
            # "every should_emit site that returns True" rule (#2845).
            if warn_state.should_emit("calendar-config", "", project_dir):
                result.warn_keys_emitted.add("calendar-config")
            log(f"Calendar config: {len(result.calendar_config.mappings)} mappings", v)
            for mapping in result.calendar_config.mappings:
                status = "OK" if mapping.accessible else "INACCESSIBLE"
                cal_name = mapping.calendar_name or mapping.calendar_id
                log(f"  {mapping.slug} -> {cal_name} ({status})", v)
        else:
            # Most often the same missing Google OAuth token as google-token above
            # (#2329) — a human-gated step. Suppress the per-cycle spam to a single
            # emission per state transition.
            signature = f"unresolved:{result.calendar_config.error}"
            if warn_state.should_emit("calendar-config", signature, project_dir):
                log(f"WARN: Calendar config: {result.calendar_config.error}", v, always=True)
                _append_warning(result, f"Calendar config: {result.calendar_config.error}")
                result.warn_keys_emitted.add("calendar-config")

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
            # Three failure modes (release-verify FAILED, worker-not-running,
            # worker-install-failed) append ONLY to result.warnings and never
            # to result.errors — without this, the fix session queued by the
            # `failed` short-circuit gets an empty warning list on exactly
            # the runs that most need one (#2845).
            for warn in result.warnings:
                status += f"\n  ⚠️ {warn}"
        elif result.warnings:
            detail = f"updated to {sha}" if commits > 0 else f"up to date at {sha}"
            w_count = len(result.warnings)
            plural = "s" if w_count != 1 else ""
            status = f"{detail} ({w_count} warning{plural})"
            for warn in result.warnings:
                status += f"\n  ⚠️ {warn}"
        else:
            status = "update successful"

        # Suppressed-condition trailer (Risk 4): whatever warn_state.active()
        # holds, minus the keys that emitted THIS run (Race 3's inverse
        # hazard — should_emit writes its signature the instant it returns
        # True, so raw active() would call a key "unchanged since first
        # warning" on the very run it first warned). Composed here, after
        # every should_emit call for the run has completed and OUTSIDE the
        # if/elif/else above — the modal suppressed case is the `else`
        # branch (nothing else wrong), and nesting inside `elif
        # result.warnings:` would make the trailer silently disappear on
        # exactly the run Risk 4 exists to cover.
        suppressed = {
            k: v
            for k, v in warn_state.active(args.project_dir).items()
            if k not in result.warn_keys_emitted
        }
        if suppressed:
            names = ", ".join(sorted(suppressed))
            trailer = (
                f"{warn_state.SUPPRESSED_PREFIX} {names} — "
                "details: python -m scripts.update.warn_state"
            )
            log(trailer, always=True)  # -> _log_buffer -> data/update.txt
            status += "\n" + trailer  # -> stdout -> status_lines -> Telegram

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

        # Only attach log file if there were problems; clean success = simple message.
        # Widened to `suppressed` (the emitted-subtracted map) so the file
        # exists on a clean-except-suppressed run too — otherwise data/update.txt
        # is absent on exactly the run Risk 4 exists to cover, and the
        # second trailer emission (above) has nowhere to land.
        if not result.success or result.warnings or suppressed:
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
    # Configure the root logger at the entry point (issue #2678).
    #
    # This module's three logging.getLogger(__name__).warning() calls were
    # formatted only as an accidental import side effect: run.py imported
    # log_cleanup, which imported scripts.log_rotate, which called
    # basicConfig() at module scope. #2643 moved that call into log_rotate's
    # own __main__ guard, so without this run.py's warnings would fall through
    # to logging.lastResort and print bare, unprefixed, untimestamped.
    #
    # Mirrors scripts/log_rotate.py's format and stream deliberately: both are
    # /update entry points and their output interleaves in logs/update.log.
    # stderr keeps them off stdout, which --json mode reserves for its payload.
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
