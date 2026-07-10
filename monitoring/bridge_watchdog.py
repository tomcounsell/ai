#!/usr/bin/env python3
"""Bridge watchdog - external health monitor for the Telegram bridge.

This runs as a SEPARATE process from the bridge (via launchd) so it can
detect and recover from bridge crashes. It implements a 5-level recovery
escalation chain:

1. Simple restart (launchd handles this automatically)
2. Kill stale processes + restart
3. Clear lock files + restart
4. Revert recent commit + restart (if enabled)
5. Alert human with diagnostics

Usage:
    python monitoring/bridge_watchdog.py        # Run once (for launchd)
    python monitoring/bridge_watchdog.py --loop # Run continuously (testing)
"""

import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import redis

from bridge.utc import utc_iso, utc_now

# Add project root to path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from monitoring.crash_tracker import (  # noqa: E402
    detect_crash_pattern,
    get_recent_crashes,
    log_crash,
)

# Shared absolute process-start-time primitive (ps -o lstart). Moved from this
# module to scripts/update/service.py and generalized for any PID so the
# release verifier can classify both bridge and worker (issue #1898). The two
# TTL constants (Decision 26: BOTH = STARTUP_GRACE_SECONDS + one 60s watchdog
# cycle, so the suppression window can never expire before the boot window it
# protects) live in scripts.update.service so scripts.update.verify_release
# can share them without importing this module's side effects.
from scripts.update.service import (  # noqa: E402
    UPDATE_REPORT_TTL_SECONDS,
    UPDATE_RESTART_MARKER_TTL_SECONDS,
    get_process_start_ts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Add rotating file handler for watchdog log
LOGS_DIR = PROJECT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
_watchdog_file_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "watchdog.log",
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
)
_watchdog_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_watchdog_file_handler)

# Files and directories
LOG_FILE = PROJECT_DIR / "logs" / "bridge.log"
ERROR_LOG = PROJECT_DIR / "logs" / "bridge.error.log"
DATA_DIR = PROJECT_DIR / "data"
RECOVERY_LOCK = DATA_DIR / "recovery-in-progress"
AUTO_REVERT_ENABLED_FILE = DATA_DIR / "auto-revert-enabled"

# Update release-verify signals (issue #1898):
# - UPDATE_RESTART_MARKER: written by remote-update.sh just before its
#   deliberate bridge kickstart; suppresses crash-logging/recovery while fresh.
# - UPDATE_RELEASE_FAILED_SENTINEL: written by a fresh bridge that boots and
#   self-classifies stale (or by run.py --full on a bridge hard-fail).
# - UPDATE_PENDING_REPORT: staged /update reply; undrained past its TTL means
#   the fresh bridge never came up to flush it.
UPDATE_RESTART_MARKER = DATA_DIR / "update-restart-in-progress"
UPDATE_RELEASE_FAILED_SENTINEL = DATA_DIR / "update-release-failed"
UPDATE_PENDING_REPORT = DATA_DIR / "update-pending-report"

# Thresholds
LOG_STALENESS_THRESHOLD = 300  # 5 minutes - logs older than this are stale
WATCHDOG_INTERVAL = 60  # Check every 60 seconds (hardcoded per plan)
ZOMBIE_THRESHOLD_SECONDS = 7200  # 2 hours - processes older than this are zombies
SOFT_INSTANCE_LIMIT = 5  # Warn when more than this many active claude processes

# Update-flow / wedged-detector thresholds
UPDATE_STALENESS_CEILING = 4 * 3600  # 4 hours — primary ceiling for absolute staleness
UPDATE_STALENESS_WARN = 30 * 60  # 30 minutes — secondary accelerator for recently-active chats
STARTUP_GRACE_SECONDS = 5 * 60  # 5 minutes — grace window after bridge start
# How recent last_probe_ok must be to count as "API layer healthy"
PROBE_FRESHNESS_SECONDS = 3 * 3600  # 3 hours

# Process name patterns to scan for zombies.
# ZOMBIE_PROCESS_EXCLUDES filters out Claude Desktop app helper processes.
ZOMBIE_PROCESS_PATTERNS = ("claude ", "pyright")

# Patterns to exclude from process matching (Desktop app helpers)
ZOMBIE_PROCESS_EXCLUDES = ("Claude.app", "Claude Helper")


@dataclass
class HealthStatus:
    """Bridge health assessment."""

    healthy: bool
    process_running: bool
    logs_fresh: bool
    no_crash_pattern: bool
    issues: list[str]
    recovery_level: int  # 0 = healthy, 1-5 = escalation level needed
    zombie_count: int = 0
    zombie_pids: list[int] | None = None
    zombie_memory_mb: float = 0.0
    active_claude_count: int = 0
    update_flow_live: bool = True  # default True preserves existing tests
    update_flow_issue: str = ""

    def __post_init__(self):
        if self.zombie_pids is None:
            self.zombie_pids = []


def is_bridge_running() -> tuple[bool, int | None]:
    """Check if bridge process is running. Returns (running, pid)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "telegram_bridge.py"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = int(result.stdout.strip().split("\n")[0])
            return True, pid
        return False, None
    except Exception as e:
        logger.debug(f"Error checking bridge process: {e}")
        return False, None


def _get_watchdog_redis() -> redis.Redis:
    """Return a decode_responses Redis client for watchdog use."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def assess_update_flow(r: redis.Redis, bridge_pid: int | None) -> tuple[bool, str]:
    """Assess whether the bridge's Telethon update loop is live.

    Returns (is_live, issue_description).

    PRIMARY rule (always-on, no per-chat precondition):
      process_running AND recent last_probe_ok AND last_update_received older than
      UPDATE_STALENESS_CEILING AND bridge is past startup grace window.
      => verdict: NOT live (wedged), restart needed

    SECONDARY accelerator (optional, fires sooner):
      If a respond_to_unaddressed chat had bridge:last_event inside the window
      but has since gone quiet past UPDATE_STALENESS_WARN — and last_probe_ok
      is recent.
      => verdict: NOT live (early warning)

    On signal unreadable past grace window:
      => inconclusive (treated as live), emit WARNING "bridge_update_flow_signal_unreadable"

    On missing signal within grace window:
      => healthy (cold start)
    """
    from bridge.liveness import get_last_probe_ok, get_last_update_received

    now = time.time()

    # Determine if bridge is within startup grace window.
    within_grace = False
    if bridge_pid is not None:
        start_ts = get_process_start_ts(bridge_pid)
        if start_ts is None:
            # Cannot determine start time — fail-safe: treat as inconclusive for
            # grace-window purposes (do not authorise restart).
            logger.warning(
                "assess_update_flow: get_process_start_ts returned None for "
                "pid=%s — suppressing wedge verdict (fail-safe C3)",
                bridge_pid,
            )
            return True, ""
        within_grace = (now - start_ts) < STARTUP_GRACE_SECONDS

    try:
        last_update = get_last_update_received(r)
        last_probe = get_last_probe_ok(r)
    except Exception as e:
        logger.warning(
            "assess_update_flow: bridge_update_flow_signal_unreadable — Redis error: %s", e
        )
        # Inconclusive — treat as live regardless of grace window (fail-safe C3)
        return True, "bridge_update_flow_signal_unreadable — Redis error, treating as live"

    # Cold start: no signal yet
    if last_update is None and last_probe is None:
        if within_grace:
            return True, ""
        # Past grace window with no signal at all — inconclusive, not wedged
        logger.warning(
            "assess_update_flow: bridge_update_flow_signal_unreadable — "
            "no liveness keys found past grace window (cold start or keys expired)"
        )
        return True, ""

    # Within grace window and keys missing or stale — not yet a problem
    if within_grace:
        return True, ""

    # --- PRIMARY rule ---
    # Corroboration requirement: we only declare a wedge when last_probe_ok is
    # FRESH.  A stale probe means the API/TCP layer itself may be broken — the
    # bridge could be mid-reconnect.  Restarting mid-reconnect is counterproductive
    # and would flood Telegram's rate limiter.  This dual-signal design follows the
    # "silence != failure" principle from issue #1172: absence of updates alone is
    # NOT authoritative; a positive probe confirmation is required.
    #
    # Level cap: the wedge detector contributes at most level 2 (plain restart with
    # catch_up=True).  It must never push recovery_level to 4 (auto-revert).  A
    # wedged update loop is not evidence of a bad commit — it is a known Telethon
    # upstream bug (archived library, issue #1408).
    probe_is_fresh = last_probe is not None and (now - last_probe) < PROBE_FRESHNESS_SECONDS
    update_is_stale = last_update is None or (now - last_update) >= UPDATE_STALENESS_CEILING

    if probe_is_fresh and update_is_stale:
        update_age_h = "never" if last_update is None else f"{(now - last_update) / 3600:.1f}h ago"
        issue = (
            f"update loop wedged: last_update_received={update_age_h}, "
            f"last_probe_ok={((now - last_probe) / 60):.0f}m ago — "
            f"Telethon stopped delivering events while API layer is healthy"
        )
        return False, issue

    # --- SECONDARY accelerator ---
    # Fires only when a per-chat bridge:last_event key confirms a recently-active
    # chat has gone quiet — corroborating that the account-wide update silence is
    # anomalous, not just a quiet period.  Without this per-chat gate the accelerator
    # would fire for any 30-min lull (since last_probe_ok is refreshed every ~180s
    # by the reconciler, it is always "fresh" when connected) — re-inheriting the
    # silence=failure anti-pattern from issue #1172.
    #
    # Per the plan: "if a respond_to_unaddressed chat had a bridge:last_event inside
    # the window and has since gone quiet past UPDATE_STALENESS_WARN".
    if probe_is_fresh and last_update is not None:
        update_age = now - last_update
        if UPDATE_STALENESS_WARN <= update_age < UPDATE_STALENESS_CEILING:
            # Check per-chat corroboration: scan bridge:last_event:* for a chat
            # that was recently active but has gone quiet.
            try:
                per_chat_corroborated = False
                for key in r.scan_iter("bridge:last_event:*", count=100):
                    raw_ts = r.get(key)
                    if raw_ts is None:
                        continue
                    try:
                        chat_last_event = float(raw_ts)
                    except (ValueError, TypeError):
                        continue
                    chat_silence = now - chat_last_event
                    if UPDATE_STALENESS_WARN <= chat_silence < UPDATE_STALENESS_CEILING:
                        per_chat_corroborated = True
                        break
            except Exception as e:
                logger.warning(
                    "assess_update_flow: per-chat scan failed, skipping secondary accelerator: %s",
                    e,
                )
                per_chat_corroborated = False

            if per_chat_corroborated:
                issue = (
                    f"update loop possibly wedged (early warning): "
                    f"last_update_received={update_age / 60:.0f}m ago "
                    f"(>{UPDATE_STALENESS_WARN // 60}m), last_probe_ok fresh, "
                    f"per-chat corroboration confirms recently-active chat went quiet"
                )
                return False, issue

    return True, ""


def are_logs_fresh() -> bool:
    """Check if bridge logs have been written recently."""
    if not LOG_FILE.exists():
        return False

    try:
        mtime = LOG_FILE.stat().st_mtime
        age = time.time() - mtime
        return age < LOG_STALENESS_THRESHOLD
    except Exception:
        return False


def _parse_elapsed_time(etime_str: str) -> int:
    """Convert ps etime format to seconds.

    Handles formats:
    - MM:SS (e.g., "05:23")
    - HH:MM:SS (e.g., "01:05:23")
    - D-HH:MM:SS (e.g., "2-01:05:23")
    - DD-HH:MM:SS (e.g., "12-01:05:23")
    """
    etime_str = etime_str.strip()
    days = 0

    if "-" in etime_str:
        day_part, time_part = etime_str.split("-", 1)
        days = int(day_part)
    else:
        time_part = etime_str

    parts = time_part.split(":")
    if len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), int(parts[1])
    elif len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"Unexpected etime format: {etime_str}")

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _enumerate_claude_processes() -> list[dict]:
    """Enumerate all claude and pyright processes system-wide.

    Returns list of dicts with keys: pid, etime_seconds, rss_mb, command
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,etime,rss,command"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(f"ps command failed: {result.stderr}")
            return []
    except Exception as e:
        logger.warning(f"Failed to enumerate processes: {e}")
        return []

    processes = []
    for line in result.stdout.strip().split("\n")[1:]:  # Skip header
        line = line.strip()
        if not line:
            continue

        # Check if this line matches any of our target patterns
        matched = False
        for pattern in ZOMBIE_PROCESS_PATTERNS:
            if pattern in line:
                matched = True
                break
        if not matched:
            continue

        # Exclude Desktop app helper processes
        if any(excl in line for excl in ZOMBIE_PROCESS_EXCLUDES):
            continue

        # Parse: PID ETIME RSS COMMAND...
        parts = line.split(None, 3)
        if len(parts) < 4:
            logger.debug(f"Skipping malformed ps line: {line}")
            continue

        try:
            pid = int(parts[0])
            etime_seconds = _parse_elapsed_time(parts[1])
            rss_kb = int(parts[2])
            command = parts[3]

            # Skip the watchdog itself and grep processes
            if "bridge_watchdog" in command or "grep" in command:
                continue

            processes.append(
                {
                    "pid": pid,
                    "etime_seconds": etime_seconds,
                    "rss_mb": round(rss_kb / 1024, 1),
                    "command": command[:200],  # Truncate long commands
                }
            )
        except (ValueError, IndexError) as e:
            logger.debug(f"Skipping unparseable ps line: {line} ({e})")
            continue

    return processes


def classify_zombies(
    processes: list[dict],
    threshold_seconds: int = ZOMBIE_THRESHOLD_SECONDS,
) -> tuple[list[dict], list[dict]]:
    """Classify processes as zombies or active.

    Returns (zombies, active) where each is a list of process dicts.
    Zombies are processes with elapsed time exceeding the threshold.
    """
    zombies = []
    active = []

    for proc in processes:
        if proc["etime_seconds"] >= threshold_seconds:
            zombies.append(proc)
        else:
            active.append(proc)

    return zombies, active


def kill_zombie_processes(zombies: list[dict]) -> int:
    """Kill identified zombie processes with SIGTERM -> SIGKILL escalation.

    Returns count of processes successfully killed.
    """
    import signal

    killed = 0
    for proc in zombies:
        pid = proc["pid"]
        try:
            # First try SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)
            logger.info(
                f"Sent SIGTERM to zombie process {pid} "
                f"(age: {proc['etime_seconds']}s, mem: {proc['rss_mb']}MB)"
            )

            # Wait up to 3 seconds for process to exit
            for _ in range(6):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)  # Check if still alive
                except ProcessLookupError:
                    killed += 1
                    logger.info(f"Zombie process {pid} exited after SIGTERM")
                    break
            else:
                # Process still alive, escalate to SIGKILL
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info(f"Sent SIGKILL to zombie process {pid}")

        except ProcessLookupError:
            # Process already gone between detection and kill
            logger.debug(f"Zombie process {pid} already exited")
            killed += 1
        except PermissionError:
            logger.warning(f"Permission denied killing zombie process {pid}")
        except Exception as e:
            logger.error(f"Error killing zombie process {pid}: {e}")

    return killed


def check_bridge_health() -> HealthStatus:
    """Assess bridge health and determine recovery level needed."""
    issues = []
    recovery_level = 0

    # Check 1: Process running
    running, pid = is_bridge_running()
    if not running:
        issues.append("Bridge process not running")
        recovery_level = max(recovery_level, 1)
        # Record the crash event so crash_tracker has a record of bridge deaths
        # detected by the watchdog (e.g., SIGKILL, OOM kills leave no traceback)
        try:
            log_crash("bridge_dead_on_watchdog_check")
        except Exception as e:
            logger.debug(f"Failed to log crash event: {e}")

    # Check 2: Logs fresh (only if process is "running")
    logs_fresh = are_logs_fresh()
    if running and not logs_fresh:
        issues.append("Bridge logs stale (no activity in 5+ minutes)")
        recovery_level = max(recovery_level, 2)

    # Check 3: Crash pattern detection
    crash_pattern, suspect_commit = detect_crash_pattern()
    if crash_pattern:
        issues.append(f"Crash pattern detected (commit: {suspect_commit})")
        # Only escalate to level 4 if auto-revert is enabled
        if AUTO_REVERT_ENABLED_FILE.exists():
            recovery_level = max(recovery_level, 4)
        else:
            recovery_level = max(recovery_level, 3)

    # Check recent crash count
    recent_crashes = get_recent_crashes(1800)  # 30 min
    if len(recent_crashes) >= 5:
        issues.append(f"{len(recent_crashes)} crashes in last 30 minutes")
        recovery_level = max(recovery_level, 5)  # Alert human

    # Check 4: Zombie process detection
    all_processes = _enumerate_claude_processes()
    zombies, active = classify_zombies(all_processes)

    zombie_count = len(zombies)
    zombie_pids = [z["pid"] for z in zombies]
    zombie_memory_mb = round(sum(z["rss_mb"] for z in zombies), 1)
    active_claude_count = len(active)

    if zombie_count > 0:
        if running and logs_fresh:
            # Bridge is healthy — just kill zombies directly, don't restart
            killed = kill_zombie_processes(zombies)
            issues.append(
                f"{zombie_count} zombie process(es) cleaned up "
                f"({killed} killed, memory freed: {zombie_memory_mb}MB)"
            )
            # Do NOT escalate — bridge is fine, zombies are handled
        else:
            issues.append(
                f"{zombie_count} zombie process(es) detected "
                f"(PIDs: {zombie_pids}, memory: {zombie_memory_mb}MB)"
            )
            recovery_level = max(recovery_level, 2)

    if active_claude_count > SOFT_INSTANCE_LIMIT:
        logger.warning(
            f"High concurrent claude instance count: {active_claude_count} "
            f"(soft limit: {SOFT_INSTANCE_LIMIT})"
        )

    # Check 5: Update-flow / wedged detector (only meaningful when process is up)
    update_flow_live = True
    update_flow_issue = ""
    if running:
        try:
            r = _get_watchdog_redis()
            update_flow_live, update_flow_issue = assess_update_flow(r, pid)
        except Exception as e:
            logger.warning("check_bridge_health: assess_update_flow raised: %s", e)
            # Treat as inconclusive — do not trigger restart on our own errors
            update_flow_live = True
            update_flow_issue = ""

        if not update_flow_live:
            issues.append(update_flow_issue)
            # The wedge check itself contributes at most level 2 — must never push
            # to level 4 auto-revert (C4).  Other checks may independently set higher.
            recovery_level = max(recovery_level, 2)

    healthy = len(issues) == 0

    return HealthStatus(
        healthy=healthy,
        process_running=running,
        logs_fresh=logs_fresh,
        no_crash_pattern=not crash_pattern,
        issues=issues,
        recovery_level=recovery_level,
        zombie_count=zombie_count,
        zombie_pids=zombie_pids,
        zombie_memory_mb=zombie_memory_mb,
        active_claude_count=active_claude_count,
        update_flow_live=update_flow_live,
        update_flow_issue=update_flow_issue,
    )


def kill_stale_processes() -> int:
    """Kill any stale bridge processes. Returns count killed."""
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-f", "telegram_bridge.py"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().split("\n"):
                if pid_str:
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 9)
                        killed += 1
                        logger.info(f"Killed stale bridge process {pid}")
                    except (ValueError, ProcessLookupError, PermissionError):
                        pass
    except Exception as e:
        logger.error(f"Error killing stale processes: {e}")
    return killed


def clear_lock_files() -> int:
    """Clear session lock files. Returns count cleared."""
    cleared = 0
    for pattern in ["*.session-journal", "*.session-wal", "*.session-shm"]:
        for f in DATA_DIR.glob(pattern):
            try:
                f.unlink()
                cleared += 1
                logger.info(f"Cleared lock file: {f.name}")
            except Exception:  # noqa: S110 -- best-effort lock-file cleanup
                pass
    return cleared


def restart_bridge() -> bool:
    """Restart the bridge via launchctl kickstart. Returns success."""
    try:
        uid = os.getuid()
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.valor.bridge"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Failed to restart bridge: {e}")
        return False


def revert_last_commit() -> bool:
    """Revert HEAD commit and restart. Returns success."""
    try:
        # Create revert commit
        result = subprocess.run(
            ["git", "revert", "HEAD", "--no-edit"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"Git revert failed: {result.stderr}")
            return False

        logger.info("Created revert commit")
        return True
    except Exception as e:
        logger.error(f"Failed to revert commit: {e}")
        return False


def _hostname() -> str:
    """Return machine hostname for log identification."""
    import platform

    return platform.node()


def _kill_detected_zombies() -> int:
    """Detect and kill zombie claude/pyright processes. Returns count killed."""
    processes = _enumerate_claude_processes()
    zombies, _ = classify_zombies(processes)
    if zombies:
        logger.info(f"Found {len(zombies)} zombie process(es) to kill")
        return kill_zombie_processes(zombies)
    return 0


def execute_recovery(level: int, issues: list[str]) -> bool:
    """Execute recovery at the specified escalation level."""
    if level == 0:
        logger.warning(f"Recovery called with level 0 (no action needed): {issues}")
        return True

    logger.info(f"Executing recovery level {level}")

    # Create recovery lock
    RECOVERY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    RECOVERY_LOCK.write_text(
        json.dumps(
            {
                "level": level,
                "started": utc_iso(),
                "issues": issues,
            }
        )
    )

    try:
        if level == 1:
            # Simple restart (launchd should handle, but we can help)
            return restart_bridge()

        elif level == 2:
            # Kill stale + zombie processes + restart
            kill_stale_processes()
            _kill_detected_zombies()
            time.sleep(2)
            return restart_bridge()

        elif level == 3:
            # Clear locks + kill zombies + restart
            kill_stale_processes()
            _kill_detected_zombies()
            clear_lock_files()
            time.sleep(2)
            return restart_bridge()

        elif level == 4:
            # Revert commit + restart
            if not AUTO_REVERT_ENABLED_FILE.exists():
                logger.warning("Auto-revert not enabled, escalating to level 5")
                return execute_recovery(5, issues)

            kill_stale_processes()
            _kill_detected_zombies()
            clear_lock_files()

            if revert_last_commit():
                logger.warning(
                    "[%s] Auto-revert triggered. Issues: %s. HEAD reverted to previous commit.",
                    _hostname(),
                    ", ".join(issues),
                )
                time.sleep(2)
                return restart_bridge()
            else:
                return execute_recovery(5, issues)

        elif level == 5:
            # Log critical failure with hostname for multi-machine debugging
            logger.critical(
                "[%s] Bridge recovery failed — levels 1-4 exhausted."
                " Issues: %s. Manual intervention required.",
                _hostname(),
                ", ".join(issues),
            )
            log_crash(f"[{_hostname()}] Recovery exhausted")
            return False

    finally:
        # Remove recovery lock
        try:
            RECOVERY_LOCK.unlink()
        except Exception:  # noqa: S110 -- best-effort recovery-lock removal
            pass

    return False


def check_update_release_signals() -> list[str]:
    """Surface out-of-band update-release failure signals (issue #1898).

    Read-only: returns human-readable issue strings for (1) the
    ``data/update-release-failed`` sentinel a stale fresh bridge wrote at
    boot, and (2) a ``data/update-pending-report`` left undrained past
    ``UPDATE_REPORT_TTL_SECONDS`` measured against the report's OWN staged
    timestamp (the fresh bridge never came up to flush it). Richer
    alerting/escalation is deliberately out of scope (#1898 No-Gos).
    """
    issues: list[str] = []
    try:
        if UPDATE_RELEASE_FAILED_SENTINEL.exists():
            content = UPDATE_RELEASE_FAILED_SENTINEL.read_text().strip()[:200]
            issues.append(f"update-release-failed sentinel present: {content}")
    except Exception as e:
        logger.debug("Could not read update-release-failed sentinel: %s", e)
    try:
        if UPDATE_PENDING_REPORT.exists():
            staged_ts = 0.0
            try:
                staged_ts = float(json.loads(UPDATE_PENDING_REPORT.read_text()).get("staged_ts", 0))
            except Exception:  # noqa: S110 -- unreadable staged ts falls back to mtime
                pass
            if staged_ts <= 0:
                # Unreadable staged timestamp — fall back to file mtime.
                staged_ts = UPDATE_PENDING_REPORT.stat().st_mtime
            report_age = time.time() - staged_ts
            if report_age > UPDATE_REPORT_TTL_SECONDS:
                issues.append(
                    f"update pending report undrained for {int(report_age)}s "
                    f"(TTL {UPDATE_REPORT_TTL_SECONDS}s) — "
                    "the fresh bridge may never have come up"
                )
    except Exception as e:
        logger.debug("Could not read update-pending-report: %s", e)
    return issues


def run_health_check() -> bool:
    """Run a single health check cycle. Returns True if healthy."""
    # Check hibernation state before any recovery action.
    # When hibernating, the bridge is intentionally stopped awaiting human
    # re-authentication — suppress the restart loop entirely.
    try:
        from bridge.hibernation import is_hibernating

        if is_hibernating():
            logger.info(
                "[hibernation] Bridge hibernating: auth required. "
                "Run 'python scripts/telegram_login.py' then "
                "'./scripts/valor-service.sh restart' to resume."
            )
            return True  # Suppress all recovery actions
    except Exception as e:
        logger.debug("[hibernation] Could not check hibernation state: %s", e)

    # Skip if recovery already in progress
    if RECOVERY_LOCK.exists():
        try:
            lock_data = json.loads(RECOVERY_LOCK.read_text())
            lock_time = datetime.fromisoformat(lock_data.get("started", ""))
            # Ensure lock_time is tz-aware for comparison (legacy files may be naive)
            if lock_time.tzinfo is None:
                lock_time = lock_time.replace(tzinfo=UTC)
            age = (utc_now() - lock_time).total_seconds()
            if age < 300:  # 5 minute recovery timeout
                logger.info("Recovery in progress, skipping check")
                return True
            else:
                logger.warning("Stale recovery lock, removing")
                RECOVERY_LOCK.unlink()
        except Exception:  # noqa: S110 -- unreadable lock treated as absent
            pass

    # Planned-restart suppression (issue #1898, Decision 19): remote-update.sh
    # writes data/update-restart-in-progress just before its deliberate bridge
    # kickstart. While the marker is fresh, skip the health check entirely —
    # this suppresses BOTH log_crash("bridge_dead_on_watchdog_check") AND the
    # recovery_level bump → execute_recovery() → restart_bridge() that would
    # otherwise race the planned restart. The fresh bridge's boot self-check
    # clears the marker; an aged-out marker resumes normal health checking.
    try:
        if UPDATE_RESTART_MARKER.exists():
            marker_age = time.time() - UPDATE_RESTART_MARKER.stat().st_mtime
            if marker_age < UPDATE_RESTART_MARKER_TTL_SECONDS:
                logger.info(
                    "Planned update restart in progress (marker age %.0fs) — skipping health check",
                    marker_age,
                )
                return True
            logger.warning(
                "Planned-restart marker aged out (%.0fs) — removing and resuming health checks",
                marker_age,
            )
            UPDATE_RESTART_MARKER.unlink(missing_ok=True)
    except Exception as e:
        logger.debug("Planned-restart marker check failed: %s", e)

    # Out-of-band update-release failure signals (issue #1898): surfaced loudly
    # on every cycle so a stale/never-came-up fresh bridge cannot silence its
    # own alarm.
    for release_issue in check_update_release_signals():
        logger.critical("[update-release] %s", release_issue)

    status = check_bridge_health()

    if status.healthy:
        logger.debug("Bridge healthy")
        return True

    logger.warning(f"Bridge unhealthy: {', '.join(status.issues)}")
    logger.info(f"Recovery level needed: {status.recovery_level}")

    # Log specific crash event when wedge is the trigger
    if not status.update_flow_live:
        logger.warning(
            "bridge_update_loop_wedged: update loop stopped delivering events "
            "while process is running and API layer is healthy. "
            "Issue: %s",
            status.update_flow_issue,
        )
        try:
            log_crash("bridge_update_loop_wedged")
        except Exception as e:
            logger.debug("Failed to log wedge crash event: %s", e)

    # Execute recovery
    success = execute_recovery(status.recovery_level, status.issues)

    if success:
        logger.info("Recovery successful")
    else:
        logger.error("Recovery failed")

    return success


def main():
    parser = argparse.ArgumentParser(description="Bridge health watchdog")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (for testing)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check health only, don't execute recovery",
    )
    args = parser.parse_args()

    if args.check_only:
        # Check hibernation state first
        hibernating = False
        try:
            from bridge.hibernation import is_hibernating

            hibernating = is_hibernating()
        except Exception:  # noqa: S110 -- CLI diagnostic; defaults False
            pass
        print(f"Hibernating: {hibernating}")
        if hibernating:
            print(
                "Hibernation: auth required — run 'python scripts/telegram_login.py' then restart"
            )

        status = check_bridge_health()
        print(f"Healthy: {status.healthy}")
        print(f"Process running: {status.process_running}")
        print(f"Logs fresh: {status.logs_fresh}")
        print(f"No crash pattern: {status.no_crash_pattern}")
        if status.issues:
            print(f"Issues: {', '.join(status.issues)}")
        print(f"Recovery level: {status.recovery_level}")
        print(f"Zombie processes: {status.zombie_count}")
        if status.zombie_pids:
            print(f"Zombie PIDs: {status.zombie_pids}")
            print(f"Zombie memory: {status.zombie_memory_mb}MB")
        print(f"Active claude instances: {status.active_claude_count}")
        if status.active_claude_count > SOFT_INSTANCE_LIMIT:
            print(
                f"WARNING: Active instances exceed soft limit "
                f"({status.active_claude_count} > {SOFT_INSTANCE_LIMIT})"
            )
        print(f"Update flow live: {status.update_flow_live}")
        if not status.update_flow_live:
            print(f"Update flow issue: {status.update_flow_issue}")
        return 0 if status.healthy else 1

    if args.loop:
        logger.info(f"Starting watchdog loop (interval: {WATCHDOG_INTERVAL}s)")
        while True:
            try:
                run_health_check()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
            time.sleep(WATCHDOG_INTERVAL)
    else:
        # Single check (for launchd StartInterval)
        try:
            run_health_check()
        except Exception as e:
            logger.error(f"Watchdog error: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
