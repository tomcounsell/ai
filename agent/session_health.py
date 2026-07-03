"""Periodic health monitoring, evidence-only no-progress detection, orphan cleanup,
and startup recovery.

Detector philosophy (issue #1172): the detector kills only on **evidence** of
failure (worker_dead, OS-initiated OOM, response already delivered). It does
NOT kill on **inference** from absence of expected activity. Stdout silence,
heartbeat-stale-but-subprocess-alive, and wall-clock deadlines are not used as
kill signals. Cost monitoring (`AgentSession.total_cost_usd`) is the long-run
backstop for genuinely runaway sessions.

See ``docs/features/pm-session-liveness.md`` for the full model.
"""

import asyncio
import functools
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import agent.session_state as _session_state
from agent.session_stall_classifier import (
    NEVER_STARTED_CONFIRM_MARGIN_SECS,
    NEVER_STARTED_GRACE_SECS,
)
from agent.session_state import SessionHandle, _active_events, _active_sessions, _active_workers
from analytics.collector import record_metric
from models.agent_session import AgentSession, SessionType
from models.memory import Memory
from models.session_lifecycle import ALL_STATUSES, get_authoritative_session
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

# Re-exported for tests/monkeypatching: keeps the symbol resolvable as
# `agent.session_health.record_metric` even after ruff/F401 lint cycles.
__all__ = ["record_metric"]

logger = logging.getLogger(__name__)

# === Cross-process orphan reaper constants (issue #1271) ===
#
# Compiled cmdline regex patterns. Three signatures match:
#   - Claude CLI SDK bundle (``claude_agent_sdk/_bundled/claude``)
#   - The native/PTY granite TUI process (D2, issue #1817): a bare or
#     absolute-path ``claude`` invocation carrying ``--permission-mode
#     bypassPermissions`` (see ``agent/granite_container/pty_driver.py``'s
#     ``spawn()`` argv) that is NOT a one-shot. The two leading negative
#     lookaheads exclude any cmdline containing a standalone ``-p`` token or
#     ``--print`` — those are headless ``claude -p`` one-shot turns
#     (``agent/granite_container/role_driver.py``'s ``HeadlessRoleDriver``,
#     also carrying ``--permission-mode bypassPermissions``), which are
#     ALREADY governed by the separate, narrower, age-gated
#     ``_is_stale_print_oneshot`` matcher below. Overlapping the two would
#     let this broader regex fast-track-match (via ``is_claude``) an
#     in-flight headless turn before its own one-shot matcher's age gate
#     even applies — do not remove the lookaheads.
#   - Any MCP server module under ``mcp_servers/``
#
# The worker pattern (``python -m worker``) is INTENTIONALLY EXCLUDED:
# on macOS, every launchd-respawned worker has PPID==1 by design (launchd is
# PID 1 and ``com.valor.worker.plist`` sets ``KeepAlive=true``), so a worker
# signature + PPID==1 filter would match every live worker. As an additional
# defense-in-depth layer, the reaper builds a positive-ID skip-set from
# ``worker:registered_pid:*`` Redis keys.
_CLAUDE_CMDLINE_RE = re.compile(
    r"claude_agent_sdk/_bundled/claude\b"
    r"|^(?!.*(?:^|\s)-p(?:\s|$))(?!.*--print(?:\s|$)).*\bclaude\b.*--permission-mode\s+bypassPermissions"
)
_MCP_SERVER_CMDLINE_RE = re.compile(r"mcp_servers/[\w_]+\.py\b")

# Heartbeat-freshness threshold for the per-PID gate (30 minutes).
ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS = 1800

# === Fast-kill signature for stale `claude --print` one-shots (issue #1632) ===
#
# Observed 2026-06-11: rogue orphan subagents spawned bare `claude ... --print`
# one-shots (~250 MB each) that never exited; 21 accumulated at PPID==1. The
# bundled-path regex above missed them (argv[0] was bare `claude` on PATH) and
# the heartbeat gate is irrelevant — no legitimate `--print` one-shot lives
# longer than a few minutes. Any PPID==1 `claude` process in `--print`/`-p`
# mode older than this threshold is ALWAYS reapable. Conservative 10 minutes.
ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS = 600

# Shell executables whose `-c` invocations count as transient wrappers for
# dead-chain detection (issue #1632 mode 1b): a wrapper that is itself
# PPID==1 is alive only because it is blocked waiting on its child forever —
# the chain above the child is dead.
_SHELL_WRAPPER_NAMES = frozenset({"sh", "zsh", "bash", "dash"})

# Redis key prefix for positive-ID self-protection. Worker writes
# ``worker:registered_pid:{hostname}:{pid}`` at startup with TTL, refreshed on
# every heartbeat tick. The reaper reads all keys matching the prefix and adds
# the integer values to its skip-set.
WORKER_REGISTERED_PID_KEY_PREFIX = "worker:registered_pid:"
WORKER_REGISTERED_PID_TTL_SECONDS = 86400  # 24h

# === Fix #5 (#1821): worker→bridge Redis-mediated liveness/slot contract ===
#
# The bridge process cannot touch the worker's in-memory SlotLeaseRegistry (its
# asyncio.Semaphore is loop-affine) and cannot read last_loop_tick (a monotonic()
# value meaningless outside the worker process). Every cross-process signal
# therefore goes through Redis, published by the worker and read by the bridge.
# All keys are per-host and TTL'd so a dead worker's records expire and the
# bridge sees "no beacon" rather than a phantom-live registry.
#
# Config location (not a defect): read via raw os.environ.get() at module scope,
# matching the sibling WORKER_HEARTBEAT_INTERVAL / #1815 deadman constants — no
# config/settings.py entry is expected.
WORKER_HEARTBEAT_INTERVAL = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "30"))
WORKER_LOOP_BEACON_KEY_PREFIX = "worker:loop_beacon:"
WORKER_SLOT_LEASES_KEY_PREFIX = "worker:slot:leases:"
WORKER_SLOT_RECLAIM_REQUESTS_KEY_PREFIX = "worker:slot:reclaim_requests:"
WORKER_SLOT_LAST_RECLAIM_DRAIN_KEY_PREFIX = "worker:slot:last_reclaim_request_drain:"
WORKER_WATCHDOG_ACTIONS_KEY_PREFIX = "worker:watchdog:actions:"
# TTL for the loop beacon: 3× the heartbeat cadence, so a couple missed off-loop
# ticks still leave a fresh beacon; a dead worker's beacon expires within 90s.
WORKER_LOOP_BEACON_TTL_SECONDS = 3 * WORKER_HEARTBEAT_INTERVAL
# TTL for the lease snapshot + reclaim-request list: 3× the health-check tick
# (AGENT_SESSION_HEALTH_CHECK_INTERVAL == 300s, defined below). Literal here to
# avoid a forward-reference at module import.
WORKER_SLOT_KEY_TTL_SECONDS = 3 * 300
# Cap on the operator action log + reclaim-request list (Race 4: bound the list
# so a multi-owner leak burst cannot grow it unboundedly). Env-overridable.
WORKER_WATCHDOG_ACTIONS_MAX = 256
RECLAIM_REQUESTS_MAX = int(os.environ.get("RECLAIM_REQUESTS_MAX", "256"))
# Beacon-freshness / bridge-contract staleness threshold, REUSED for the
# bridge_contract_stale signal (concern #5 — no dedicated staleness var). Matches
# the #1815 deadman staleness threshold default (90s).
BRIDGE_WORKER_BEACON_STALE_S = int(os.environ.get("BRIDGE_WORKER_BEACON_STALE_S", "90"))

# --- B2-probe: observability-only duplicate-worker detection (issue #1817) --
#
# A SEPARATE, additive key namespace from WORKER_REGISTERED_PID_KEY_PREFIX
# above -- this probe never reads or writes the reaper's positive-ID
# self-protection keys, so it cannot regress that mechanism. One key per
# (host, role): ``worker:role_pid:{hostname}:{role}`` holds the pid of the
# most recently registered worker for that role, and
# ``worker:pid_heartbeat_ts:{hostname}:{pid}`` holds a per-pid unix timestamp
# refreshed alongside every registration write, used as the liveness
# freshness signal (reuses HEARTBEAT_FRESHNESS_WINDOW as the staleness
# threshold -- see ``_probe_duplicate_worker_registration``).
_WORKER_ROLE_PID_KEY_PREFIX = "worker:role_pid:"
_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX = "worker:pid_heartbeat_ts:"


def _current_worker_role() -> str:
    """Return this process's worker role for B2-probe scoping.

    Mirrors the ``VALOR_PROJECT_KEY`` fallback used elsewhere (e.g.
    ``agent/session_pickup.py``): empty/whitespace falls back to a stable
    default so writers and readers agree on the namespace.
    """
    v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return v or "default"


# SIGKILL escalation queue for cross-process orphans.
# Stages ``(pid, create_time)`` tuples. At drain time the reaper reconstructs
# ``psutil.Process(pid)`` and verifies ``proc.create_time() == staged`` BEFORE
# issuing SIGKILL — if the create_time differs, macOS recycled the PID to an
# unrelated process and the SIGKILL is skipped.
_pending_sigkill_orphans: set[tuple[int, float]] = set()

# Hostname captured at module load — used by the orphan-reap counter and the
# registered-PID key. Captured here (vs called per-tick) for amortization.
_ORPHAN_REAP_HOSTNAME = socket.gethostname()


def _filter_hydrated_sessions(sessions: Iterable) -> list[AgentSession]:
    """Return only AgentSession instances whose key identity fields are hydrated.

    A "phantom" session is one where attribute access falls through to the
    class-level Popoto Field descriptor instead of a hydrated string value.
    Phantoms are produced when ``AgentSession.query.*`` iterates an index set
    whose members point to hashes that no longer exist (orphan
    ``$IndexF:AgentSession:*`` members).

    Reading attributes of a phantom, or worse, calling ``.delete()`` on one,
    can collateral-damage real records whose indexed-field values happen to
    match. Every caller iterating ``AgentSession.query.*`` results must pass
    them through this filter BEFORE any attribute read for mutation decisions.

    The canonical hydration check is ``isinstance(s.agent_session_id, str)``.
    ``agent_session_id`` is Popoto's ``KeyField`` and is the first attribute
    populated on hydration; if it is still a ``Field`` descriptor, the
    instance is a phantom. This matches the established pattern at
    ``session_health.py`` in ``_agent_session_hierarchy_health_check`` and
    satisfies the acceptance criterion on issue #1069.

    Phantoms are dropped silently (DEBUG log) — they are NOT healed here.
    Source-level cleanup of orphan ``$IndexF`` members happens via
    ``AgentSession.repair_indexes()`` in ``cleanup_corrupted_agent_sessions``.

    Anomalous hydration states — where ``agent_session_id`` is absent but
    other fields (``status``, ``session_id``, ``created_at``) are populated —
    are logged at WARNING so operators notice if the hydration check itself
    becomes unreliable (e.g., a Popoto version bump that changes
    materialization semantics).

    Args:
        sessions: An iterable of AgentSession instances from ``query.*``.

    Returns:
        A list containing only hydrated instances.
    """
    hydrated: list[AgentSession] = []
    phantom_count = 0
    for s in sessions:
        try:
            aid = getattr(s, "agent_session_id", None)
        except Exception as exc:
            # Unexpected exception on attribute access — treat as phantom, warn
            # so operators notice.
            logger.warning(
                "[phantom-filter] Unexpected exception reading agent_session_id: %s", exc
            )
            phantom_count += 1
            continue
        # Hydration heuristic: a real record always has BOTH a string
        # ``agent_session_id`` AND a string ``session_id``. Popoto's
        # ``AutoKeyField`` auto-generates a fresh uuid when an instance is
        # constructed from a hash that lacks an ``id`` field (the phantom
        # case), so ``isinstance(aid, str)`` alone is no longer sufficient.
        # ``session_id`` is a plain ``Field()`` with no auto-generation and
        # is set by every legitimate caller (bridge, CLI, recovery), so its
        # absence reliably distinguishes phantoms from hydrated records.
        if isinstance(aid, str) and isinstance(getattr(s, "session_id", None), str):
            hydrated.append(s)
            continue
        # Phantom: either aid is non-string OR session_id is missing.
        # Surface anomalies where aid IS a string but session_id is absent —
        # that's the canonical phantom shape. If aid is non-string AND other
        # fields ARE populated, log at WARNING (hydration check may have
        # become unreliable, e.g., a Popoto version bump).
        # NOTE: ``status`` is NOT in the suspicious-fields list because it
        # has a class-level default ("pending") and is therefore populated on
        # every phantom — including pure phantoms that should log at DEBUG.
        # ``session_id`` and ``created_at`` are written by every legitimate
        # caller and are absent on phantoms.
        suspicious = False
        if not isinstance(aid, str):
            for f in ("session_id", "created_at"):
                try:
                    if isinstance(getattr(s, f, None), str):
                        suspicious = True
                        break
                except Exception:
                    pass
        if suspicious:
            logger.warning(
                "[phantom-filter] Suspicious phantom: agent_session_id not hydrated "
                "but other fields present (type(agent_session_id)=%s)",
                type(aid).__name__,
            )
        else:
            logger.debug(
                "[phantom-filter] Dropped phantom record (type(agent_session_id)=%s)",
                type(aid).__name__,
            )
        phantom_count += 1
    if phantom_count:
        logger.info(
            "[phantom-filter] Filtered %d phantom record(s) from query result", phantom_count
        )
    return hydrated


def _ts(val):
    """Convert datetime or float to Unix timestamp."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    return None


# Agent session health check constants
AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300  # 5 minutes
AGENT_SESSION_HEALTH_MIN_RUNNING = (
    300  # Don't recover sessions running less than 5 min (race condition guard)
)

# === Two-tier no-progress detector constants (issue #1036) ===
# Heartbeat write interval inside `_heartbeat_loop` for the queue-layer
# heartbeat field `last_heartbeat_at`. 60s matches the messenger watchdog
# tick so both Tier 1 heartbeats nominally refresh on the same cadence.
HEARTBEAT_WRITE_INTERVAL = 60
# Freshness window (seconds) for Tier 1 heartbeat fields. A heartbeat whose
# age is strictly less than this window is considered fresh. 90s provides a
# 30s grace margin over the 60s write cadence.
HEARTBEAT_FRESHNESS_WINDOW = 90
# Post-compaction grace period (issue #1099 Mode 3). After a successful
# compaction, the session often returns to idle briefly before the next turn
# picks up. During this window the Tier 2 gate reprieves the kill rather than
# treating the idle period as a stuck subprocess. The companion writer is
# ``agent/hooks/pre_compact.py::pre_compact_hook``, which updates
# ``AgentSession.last_compaction_ts`` on every successful backup. Env-tunable
# via ``COMPACT_REPRIEVE_WINDOW_SECS``.
COMPACT_REPRIEVE_WINDOW_SEC = int(os.environ.get("COMPACT_REPRIEVE_WINDOW_SECS", 600))
# Max health-check kills before a session is finalized as `failed` instead
# of being re-queued to `pending`. Ensures sessions always reach a terminal
# status within ~10 minutes of going non-progressing, avoiding the
# Meta.ttl=30d silent-delete backstop described in spike-2 of issue #1036.
MAX_RECOVERY_ATTEMPTS = 2
# Timeout for awaiting task cancellation during recovery. SDK client cleanup
# propagates near-instantly once CancelledError is raised; 0.25s keeps the
# health-check tick budget tight while still giving the cancellation a
# moment to complete.
TASK_CANCEL_TIMEOUT = 0.25
# Freshness window (seconds) for per-turn SDK progress signals
# (last_tool_use_at, last_turn_at) in Tier 1 sub-check A (issue #1226).
# 1800s (30 minutes) accommodates the longest observed extended-thinking turns.
# A session that produces a tool boundary or result event within this window is
# considered actively progressing. Env-tunable via SDK_PROGRESS_FRESHNESS_WINDOW_SECS
# for operators who observe edge cases in production.
SDK_PROGRESS_FRESHNESS_WINDOW = int(os.environ.get("SDK_PROGRESS_FRESHNESS_WINDOW_SECS", 1800))
# Max consecutive Tier 2 reprieves allowed for sessions that have NEVER produced
# any SDK output (sdk_ever_output=False). After this many reprieves the "alive"
# gate is suppressed and recovery proceeds. Derived from the SDK progress window
# divided by the heartbeat freshness window: 1800 // 90 = 20 ticks (~30 minutes).
# Sessions that have produced output (sdk_ever_output=True) are never subject to
# this cap — their recovery depends solely on per-turn freshness in sub-check A.
MAX_NO_OUTPUT_REPRIEVES = SDK_PROGRESS_FRESHNESS_WINDOW // HEARTBEAT_FRESHNESS_WINDOW  # 20
# Running-time threshold (seconds) below which a fresh queue-layer
# ``last_heartbeat_at`` alone is sufficient evidence of progress in
# ``_has_progress`` sub-check B (issue #1356).
#
# Aliased to ``AGENT_SESSION_HEALTH_MIN_RUNNING`` (300s). The
# ``_has_progress(entry)`` function is only called by the no_progress path
# when ``running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING`` (see the
# health-check loop's race-condition guard), so a tighter grace window
# (e.g. 90s) would be unreachable. Choosing 300s makes the gate meaningful
# from the very first tick where the gate could possibly fire.
#
# Env-tunable via ``STARTUP_GRACE_SECONDS`` for parity with other tunables
# in this file. Operators raising the grace must keep
# ``STARTUP_GRACE_SECONDS < NO_OUTPUT_BUDGET_SECONDS`` so the in-band region
# is non-empty.
STARTUP_GRACE_SECONDS = int(
    os.environ.get("STARTUP_GRACE_SECONDS", AGENT_SESSION_HEALTH_MIN_RUNNING)
)
# No-output running-time budget (seconds) for sub-check B (issue #1356).
#
# Defined as ``MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW``
# (= 20 * 90 = 1800s = 30 min). Mirrors how ``MAX_NO_OUTPUT_REPRIEVES`` is
# derived from ``SDK_PROGRESS_FRESHNESS_WINDOW // HEARTBEAT_FRESHNESS_WINDOW``,
# keeping the relationship symmetric.
#
# When ``sdk_ever_output`` is False AND ``running_seconds > NO_OUTPUT_BUDGET_SECONDS``,
# sub-check B's fresh-heartbeat fast-path is denied and the function falls
# through to the own-progress fields. Combined with Tier-2's existing reprieve
# cap (also gated on ``MAX_NO_OUTPUT_REPRIEVES``), this guarantees a session
# that never emits a first turn is recovered within ~30 minutes.
#
# Not env-tunable directly because the underlying constants are.
NO_OUTPUT_BUDGET_SECONDS = MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW  # 1800

# === Per-tool timeout sub-loop constants (issue #1270) ===
# A session whose ``current_tool_name`` is non-null and whose
# ``last_tool_use_at`` is older than the tier-specific budget is "tool-wedged":
# the PreToolUse hook fired (so we know which tool is in flight) but the
# PostToolUse hook never returned. Without this check, the session keeps
# passing Tier 1 sub-check A in ``_has_progress`` for up to
# ``SDK_PROGRESS_FRESHNESS_WINDOW`` (30 min) while making no real progress.
#
# The check runs in a dedicated 30-second sub-loop
# (``_agent_session_tool_timeout_loop``) parallel to the main 5-minute health
# loop so the 30s internal budget can fire within one tick of its expiry.
# On a hit, the per-tier counter on ``AgentSession`` is bumped, a project-
# scoped Redis counter is INCR'd, and the session is recovered via the same
# ``running -> pending`` transition used by the main loop's no_progress path.
#
# Kill switch: ``TOOL_TIMEOUT_TIERS_DISABLED=1`` short-circuits the sub-loop
# (parity with ``DISABLE_PROGRESS_KILL`` for the main loop).
TOOL_TIMEOUT_LOOP_INTERVAL = 30  # 30s — tightest tier (internal) budget
# Tier budgets — env-tunable; defaults from issue #1270 / Fazm reference.
TOOL_TIMEOUT_INTERNAL_SEC = int(os.environ.get("TOOL_TIMEOUT_INTERNAL_SEC", 30))
TOOL_TIMEOUT_MCP_SEC = int(os.environ.get("TOOL_TIMEOUT_MCP_SEC", 120))
TOOL_TIMEOUT_DEFAULT_SEC = int(os.environ.get("TOOL_TIMEOUT_DEFAULT_SEC", 300))

# === Path-B mid-run wedge detector constants (#1724) ===
# Stage-1 cheap quiescence gate: window of continuous PTY-screen quiescence
# (no new paint detected by the on_pty_read callback) before a running session
# with a tool in flight is marked a confirmed suspect for stage-2 dispatch.
# Provisional — tune via env. The value balances false-positive risk (a slow
# model turn that just happens to be silent) against detection latency. 180s
# (3 minutes) accommodates slow extended-thinking turns while still catching
# genuine wedges well within the existing TOOL_TIMEOUT_DEFAULT_SEC (300s) budget.
# Set to 0 to disable the quiescence gate without disabling the writers.
MID_RUN_QUIESCENCE_SECS: float = float(os.environ.get("MID_RUN_QUIESCENCE_SECS", "180"))

# Internal-tier tool name set: lightweight built-in tools that should never
# legitimately exceed 30s. Hard-coded; adding a tool is a one-line edit. Not
# env-overridable in v1 — drift risk is small and documented.
_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset(
    {"ToolSearch", "Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"}
)


def _classify_tool_tier(tool_name: str | None) -> str:
    """Return the timeout tier for ``tool_name``: ``"internal"``, ``"mcp"``, or ``"default"``.

    - ``mcp__`` prefix -> ``"mcp"`` (any Model Context Protocol tool).
    - Name in :data:`_INTERNAL_TOOL_NAMES` -> ``"internal"``.
    - Everything else -> ``"default"`` (Bash, Task, Skill, WebFetch, ...).
    - ``None`` or empty string -> ``"default"`` (defensive — a missing name is
      treated as the most permissive tier so a transient hook race does not
      mis-tier a real tool into the 30s bucket).
    """
    if not tool_name:
        return "default"
    if tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name in _INTERNAL_TOOL_NAMES:
        return "internal"
    return "default"


def _tool_tier_budget(tier: str) -> int:
    """Return the configured budget (seconds) for ``tier``."""
    if tier == "internal":
        return TOOL_TIMEOUT_INTERNAL_SEC
    if tier == "mcp":
        return TOOL_TIMEOUT_MCP_SEC
    return TOOL_TIMEOUT_DEFAULT_SEC


def _check_tool_timeout(entry: AgentSession) -> tuple[str, str] | None:
    """Return ``(tier, reason)`` if ``entry`` is tool-wedged, else ``None``.

    A session is tool-wedged when ``current_tool_name`` is non-null AND
    ``last_tool_use_at`` is older than the tier's budget. Pure function — no
    side effects, no Redis or DB writes. Safe to call from any tick.

    Returns ``None`` when:
      * ``current_tool_name`` is None / empty (no tool in flight).
      * ``last_tool_use_at`` is None (legacy session pre-Pillar A).
      * ``last_tool_use_at`` is fresher than the tier budget.
    """
    tool_name = getattr(entry, "current_tool_name", None)
    if not tool_name or not isinstance(tool_name, str):
        return None
    last_at = getattr(entry, "last_tool_use_at", None)
    if not isinstance(last_at, datetime):
        return None
    tier = _classify_tool_tier(tool_name)
    budget = _tool_tier_budget(tier)
    last_at_aware = last_at if last_at.tzinfo else last_at.replace(tzinfo=UTC)
    age = (datetime.now(tz=UTC) - last_at_aware).total_seconds()
    if age <= budget:
        return None
    reason = f"tool-wedge: {tool_name} ({tier} tier) older than {budget}s"
    return tier, reason


def _pty_quiescent_long_enough(entry: "AgentSession", now: datetime) -> bool:
    """Return True if a default-tier tool is wedge-eligible (OK to kill); False if alive.

    This is the PTY-liveness gate for the default-tier kill decision (issue #1784).
    Return value semantics: True = "wedge-eligible, proceed with kill"; False = "defer, PTY is
    live or liveness is unknown for a granite session."

    Branch order is load-bearing — the first matching branch wins:

    1. Kill-switch escape (FIRST, before any field reads): ``MID_RUN_QUIESCENCE_SECS <= 0``
       restores age-only default-tier kill for every session (granite and SDK alike).
       Must be first so the kill-switch is never silently defeated by a None-field
       short-circuit below it.

    2. Non-PTY / SDK escape: ``last_pty_read_loop_at is None`` — this session has no
       granite PTY read loop and never accumulates ``mid_run_quiescent_since``. The only
       liveness signal available is age, so return True to keep the existing 300s
       default-tier kill for the entire SDK path. Omitting this branch would cause SDK
       default tools to fall through to branch 3 (``mid_run_quiescent_since is None →
       False``) and never be killed — a regression blocker.

    2b. Staleness escape: ``last_pty_read_loop_at`` is stale (> ``HEARTBEAT_FRESHNESS_WINDOW``
       seconds old). When the PTY read loop has died, stage-1 ABSTAINs and
       ``mid_run_quiescent_since`` is unreliable — returning True here prevents a session
       whose read loop has crashed from escaping the kill via branch 3's "None → defer" path.

    3. Painting / freshly-active PTY: ``mid_run_quiescent_since is None`` — the granite PTY
       read loop exists and is fresh (branches 2/2b didn't fire) but the screen is currently
       painting (stage-1 cleared ``mid_run_quiescent_since``, or it was never quiescent).
       This is the "alive, defer the kill" case — the whole point of the fix.

    4. Quiescent long enough: tz-normalize ``mid_run_quiescent_since`` and return
       ``(now - it).total_seconds() >= MID_RUN_QUIESCENCE_SECS``. True once the granite
       PTY has been quiescent past the window (mirrors the exact predicate stage-1 uses, so
       there is a single definition of "quiescent long enough").
    """
    # Branch 1: kill-switch — age-only kill restored for all sessions.
    if MID_RUN_QUIESCENCE_SECS <= 0:
        return True

    # Branch 2: non-PTY (SDK) session — no granite PTY, age-only kill preserved.
    last_pty_read_loop_at = getattr(entry, "last_pty_read_loop_at", None)
    if last_pty_read_loop_at is None:
        return True

    # Branch 2b: staleness escape — if the granite PTY read loop marker is itself stale
    # (> HEARTBEAT_FRESHNESS_WINDOW seconds old), stage-1 ABSTAINs and
    # mid_run_quiescent_since is unreliable. Treat as wedge-eligible so a session whose
    # PTY read loop has died does not escape the kill via branch 3's "None → defer" path.
    # This is a safety net only; the main health loop's heartbeat checks own recovery
    # of granite sessions with a dead read loop (not this path).
    if isinstance(last_pty_read_loop_at, datetime):
        loop_at_aware = (
            last_pty_read_loop_at
            if last_pty_read_loop_at.tzinfo
            else last_pty_read_loop_at.replace(tzinfo=UTC)
        )
        if (now - loop_at_aware).total_seconds() > HEARTBEAT_FRESHNESS_WINDOW:
            return True

    # Branch 3: granite PTY present but painting (or freshly active) — alive, defer kill.
    mid_run_quiescent_since = getattr(entry, "mid_run_quiescent_since", None)
    if mid_run_quiescent_since is None:
        return False

    # Branch 4: quiescent long enough? Normalize timezone then compare.
    if not isinstance(mid_run_quiescent_since, datetime):
        # Defensive: unexpected type — treat as painting to avoid a spurious kill.
        return False
    quiescent_aware = (
        mid_run_quiescent_since
        if mid_run_quiescent_since.tzinfo
        else mid_run_quiescent_since.replace(tzinfo=UTC)
    )
    return (now - quiescent_aware).total_seconds() >= MID_RUN_QUIESCENCE_SECS


def _prime_pty_alive(entry: "AgentSession", now: datetime) -> bool:
    """Return True if the PTY is alive during priming (defer D0 kill); False if kill-eligible.

    **INVERTED POLARITY vs ``_pty_quiescent_long_enough``**: this helper returns
    ``True = session is alive, defer the kill`` and ``False = kill-eligible``.
    ``_pty_quiescent_long_enough`` is the exact opposite (True = proceed to kill).
    Do NOT copy its return values verbatim — a literal copy kills live sessions.

    This is the PTY-liveness gate for the D0 never-started kill path (issue #1792).
    Granite SDLC sessions can be killed during priming because the inner ``claude``
    TUI is still booting when the ``NEVER_STARTED_GRACE_SECS + CONFIRM_MARGIN`` window
    expires. If the PTY read loop is alive and the screen has shown recent activity,
    the session is still booting and the kill should be deferred.

    Branch order is load-bearing — first matching branch wins:

    1. **Kill-switch first** (before any field reads): ``NEVER_STARTED_PTY_LIVENESS_SECS <= 0``
       → return ``False`` (kill-eligible — disables deferral for all sessions).
       Must be first so the kill-switch is never defeated by None-field short-circuits below.

    2. **Non-PTY escape**: ``last_pty_read_loop_at is None`` → return ``False``
       (SDK/non-granite sessions have no PTY loop; age-only kill is preserved).

    3. **Stale read loop**: ``last_pty_read_loop_at`` older than ``HEARTBEAT_FRESHNESS_WINDOW``
       (90s) → return ``False`` (dead read loop cannot prove liveness).

    4. **Alive case**: read ``last_pty_activity_at``; if None or not a datetime → ``False``;
       otherwise tz-normalize and return ``True`` iff activity is within
       ``NEVER_STARTED_PTY_LIVENESS_SECS``.

    This helper NEVER raises — all unexpected exceptions return ``False`` (kill-eligible).
    """
    try:
        # Branch 1: kill-switch — disable PTY deferral, revert to age-only kill.
        from agent.session_stall_classifier import NEVER_STARTED_PTY_LIVENESS_SECS

        if NEVER_STARTED_PTY_LIVENESS_SECS <= 0:
            return False

        # Branch 2: non-PTY (SDK) session — no granite PTY, age-only kill preserved.
        last_pty_read_loop_at = getattr(entry, "last_pty_read_loop_at", None)
        if last_pty_read_loop_at is None:
            return False

        # Branch 3: stale read loop — a dead read loop cannot prove liveness.
        if isinstance(last_pty_read_loop_at, datetime):
            loop_at_aware = (
                last_pty_read_loop_at
                if last_pty_read_loop_at.tzinfo
                else last_pty_read_loop_at.replace(tzinfo=UTC)
            )
            if (now - loop_at_aware).total_seconds() > HEARTBEAT_FRESHNESS_WINDOW:
                return False

        # Branch 4: alive case — PTY has a fresh read loop; check screen activity.
        last_activity = getattr(entry, "last_pty_activity_at", None)
        if not isinstance(last_activity, datetime):
            return False
        activity_aware = (
            last_activity if last_activity.tzinfo else last_activity.replace(tzinfo=UTC)
        )
        return (now - activity_aware).total_seconds() <= NEVER_STARTED_PTY_LIVENESS_SECS
    except Exception:
        return False


# In-process cache for ``_is_memory_tight()`` (issue #1099 Mode 4). Tuple of
# ``(checked_at_monotonic, result)``. The cache amortizes psutil syscalls when
# many sessions enter the recovery branch within the same health-check tick.
_MEMORY_CACHE: tuple[float, bool] | None = None
_MEMORY_CACHE_TTL_SEC: float = 5.0


# Orphan-subprocess SIGKILL escalation set (issue #1218).
#
# Populated by the orphan-reap pass at the end of ``_agent_session_health_check``
# when a SIGTERM is sent to a subprocess whose owning ``AgentSession`` row is
# already terminal. Drained at the START of the next health tick: each PID is
# attempted as a SIGKILL, then unconditionally discarded — even if SIGKILL hit
# ``ProcessLookupError`` (already dead), ``PermissionError``, or any other
# exception. macOS recycles PIDs within ~5 minutes, so retaining a PID across
# more than one tick risks SIGKILLing an unrelated new process. One-shot drain,
# no retry, no accumulation.
#
# Grace window during reap (60s): a session that just transitioned to terminal
# is in its natural teardown path; we do not SIGTERM it. The
# ``_execute_agent_session`` ``finally`` block normally pops the handle from
# ``_active_sessions`` before the next health tick fires, so the grace window
# is purely defensive.
_pending_sigkill: set[int] = set()

# Grace window between a session's terminal transition and the orphan-reap pass.
# Sessions whose ``updated_at`` is within this window are skipped: the natural
# teardown in ``_execute_agent_session`` is given time to complete before the
# defensive reap fires. 60s is 10x the normal subprocess teardown duration.
ORPHAN_REAP_GRACE_SECONDS = 60


def _is_memory_tight() -> bool:
    """Return True if available system memory is below the OOM-backoff threshold.

    Used by the Mode 4 OOM-defer branch in the recovery path (issue #1099) to
    distinguish "OS killed under memory pressure" from "health check intentionally
    killed". Wraps ``psutil.virtual_memory().available`` in try/except so the
    health check never crashes from a psutil edge case (fail-open: on any error
    we return False, which means we do NOT defer — preserving today's behavior).

    A 5-second in-process cache amortizes the syscall when many sessions enter
    the recovery branch on the same tick (e.g. a worker restart that recovers a
    queue of stuck sessions). The cache is module-global; no cross-process
    coordination is needed because each health-check tick runs in one process.

    Threshold: 400 MB. Below this, the machine is genuinely tight and a 120s
    backoff is preferable to a thrash loop.
    """
    global _MEMORY_CACHE
    now_mono = time.monotonic()
    if _MEMORY_CACHE is not None and (now_mono - _MEMORY_CACHE[0]) < _MEMORY_CACHE_TTL_SEC:
        return _MEMORY_CACHE[1]
    try:
        import psutil  # noqa: PLC0415

        available_bytes = psutil.virtual_memory().available
        result = available_bytes < 400 * 1024 * 1024  # 400 MB
    except (
        Exception
    ):  # swallow-ok: fail-open — memory check failure must not stall session recovery
        result = False  # fail-open
    _MEMORY_CACHE = (now_mono, result)
    return result


def _recover_interrupted_agent_sessions_startup() -> int:
    """Reset stale running sessions to pending at startup.

    At startup, running sessions are likely orphaned from the previous process.
    However, sessions that started very recently (within AGENT_SESSION_HEALTH_MIN_RUNNING
    seconds) may have been picked up by a worker that started before this recovery
    function fired. These are skipped to avoid orphaning their SDK subprocesses.

    This uses the same timing guard as _agent_session_health_check() to avoid a race
    where a worker transitions a session to running, then startup recovery resets it
    back to pending -- orphaning the already-spawned SDK subprocess.

    Local CLI sessions (session_id starts with "local") are handled by session_type:
    - PM and Teammate local sessions are marked "abandoned". A live human CLI may hold
      the same claude_session_uuid, so resuming would spawn a second harness competing
      with the interactive CLI at that UUID (the #986 hijack rationale).
    - Dev local sessions are re-queued to "pending" like bridge sessions. Dev sessions
      are worker-owned (spawned via ``valor-session create --role eng`` by the parent
      session) with
      no human competitor — completion flows via _handle_dev_session_completion, which
      steers the PM and never uses a user-facing send callback (#1092).
    - Legacy records with ``session_type=None`` fall through to the safer abandon path.

    Note: The timing guard (AGENT_SESSION_HEALTH_MIN_RUNNING) is the primary defense
    against the hook-reactivation race. Hook reactivation transitions running→running
    (same status), so CAS via finalize_session(expected_status) does NOT protect against
    it — but truly stale sessions (>300s old) predate any active typing activity.

    Status is an IndexedField, so direct mutation and save is safe.
    Returns the combined count of recovered bridge + local-dev sessions.
    Abandoned local PM/teammate sessions are reported separately in the summary log
    line but are NOT included in the return value.
    """
    # Phantom guard: drop records whose fields are still Popoto Field descriptors
    # (orphan $IndexF members). Destructive path — filter MUST run before any
    # attribute read.
    running_sessions = _filter_hydrated_sessions(AgentSession.query.filter(status="running"))
    if not running_sessions:
        return 0

    now = time.time()
    cutoff = now - AGENT_SESSION_HEALTH_MIN_RUNNING

    # Filter out recently-started sessions (they are not orphans from a dead process)
    stale_sessions = []
    skipped = 0
    for entry in running_sessions:
        started_ts = _ts(getattr(entry, "started_at", None))
        if started_ts is not None and started_ts > cutoff:
            skipped += 1
            logger.info(
                "[startup-recovery] Skipping recent session %s (started %ds ago, guard=%ds)",
                entry.agent_session_id,
                int(now - started_ts),
                AGENT_SESSION_HEALTH_MIN_RUNNING,
            )
        else:
            stale_sessions.append(entry)

    if skipped:
        logger.info("[startup-recovery] Skipped %d recently-started session(s)", skipped)

    if not stale_sessions:
        return 0

    # Filter out terminal sessions that appear in the running index due to stale
    # IndexedField entries (#1006). These are zombie entries — the session hash
    # says killed/completed/failed but the index set still contains them.
    # Re-promoting these to pending creates an infinite resurrection cycle.
    non_terminal = []
    terminal_skipped = 0
    for entry in stale_sessions:
        actual_status = getattr(entry, "status", None)
        if actual_status in _TERMINAL_STATUSES:
            terminal_skipped += 1
            logger.warning(
                "[startup-recovery] Skipping terminal session %s "
                "(hash status=%s, stale running index entry — zombie #1006)",
                entry.agent_session_id,
                actual_status,
            )
        else:
            non_terminal.append(entry)
    if terminal_skipped:
        logger.warning(
            "[startup-recovery] Skipped %d terminal session(s) with stale running index entries",
            terminal_skipped,
        )
    stale_sessions = non_terminal

    if not stale_sessions:
        return 0

    logger.warning("[startup-recovery] Found %d stale session(s) to process", len(stale_sessions))

    bridge_count = 0
    local_dev_count = 0
    abandoned = 0
    for entry in stale_sessions:
        wk = entry.worker_key
        is_local = entry.session_id.startswith("local")  # session_id is the reliable discriminator
        session_type = getattr(entry, "session_type", None)

        # Gate the dev re-queue path on explicit equality with SessionType.ENG so that:
        # (a) legacy records with session_type=None fall through to the safer abandon path,
        # (b) any future SessionType member (e.g., REFLECTION, WORKFLOW) also falls through
        #     to abandon rather than being silently re-queued (#1092 Risk 2).
        if is_local and session_type == SessionType.ENG:
            # Local dev sessions are worker-owned — no human CLI is competing for the
            # claude_session_uuid. Re-queue like a bridge session so the worker resumes
            # the transcript on next pickup (#1092). CAS on expected_status="running"
            # protects against a concurrent health-check kill that already transitioned
            # the record away from running (same pattern as the bridge path below).
            logger.warning(
                "[startup-recovery] Recovering interrupted local dev session %s "
                "(session=%s, worker=%s, msg=%.80r...)",
                entry.agent_session_id,
                entry.session_id,
                wk,
                entry.message_text or "",
            )
            try:
                from models.session_lifecycle import update_session

                update_session(
                    entry.session_id,
                    new_status="pending",
                    # reprieve_count reset to 0: prevents escalation guard firing
                    # immediately after recovery if the session had accumulated
                    # reprieves before startup (issue #1226 Risk 4).
                    fields={"priority": "high", "started_at": None, "reprieve_count": 0},
                    expected_status="running",
                    reason="startup recovery: local dev session",
                )
                logger.info(
                    "[startup-recovery] Recovered local dev session %s",
                    entry.agent_session_id,
                )
                local_dev_count += 1
            except Exception as e:
                logger.warning(
                    "[startup-recovery] Failed to recover local dev session %s, deleting: %s",
                    entry.session_id,
                    e,
                )
                try:
                    entry.delete()
                except Exception:
                    pass
        elif is_local:
            # Local PM/teammate (or legacy session_type=None) session. A live human CLI
            # may hold the same claude_session_uuid — resuming would produce a second
            # harness competing at that UUID (the #986 hijack rationale).
            try:
                from models.session_lifecycle import StatusConflictError, finalize_session

                finalize_session(
                    entry,
                    "abandoned",
                    reason=(
                        "startup recovery: local PM/teammate session cannot be resumed by worker"
                    ),
                    skip_auto_tag=True,
                )
                abandoned += 1
                logger.info(
                    "[startup-recovery] Abandoned local %s session %s "
                    "(session_id=%s, worker_key=%s)",
                    session_type or "unknown-type",
                    entry.agent_session_id,
                    entry.session_id,
                    wk,
                )
            except StatusConflictError as e:
                # Another concurrent modification (not hook reactivation — timing guard handles
                # that race). Log at INFO and skip — session is being handled elsewhere.
                logger.info(
                    "[startup-recovery] Status conflict abandoning local session %s, skipping: %s",
                    entry.session_id,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "[startup-recovery] Failed to abandon local session %s, deleting: %s",
                    entry.session_id,
                    e,
                )
                try:
                    entry.delete()
                except Exception:
                    pass
        else:
            logger.warning(
                "[startup-recovery] Recovering interrupted session %s "
                "(session=%s, worker=%s, msg=%.80r...)",
                entry.agent_session_id,
                entry.session_id,
                wk,
                entry.message_text or "",
            )
            try:
                from models.session_lifecycle import update_session

                update_session(
                    entry.session_id,
                    new_status="pending",
                    # reprieve_count reset to 0: prevents escalation guard firing
                    # immediately after recovery if the session had accumulated
                    # reprieves before startup (issue #1226 Risk 4).
                    fields={"priority": "high", "started_at": None, "reprieve_count": 0},
                    expected_status="running",
                    reason="startup recovery",
                )
                logger.info("[startup-recovery] Recovered session %s", entry.agent_session_id)
                bridge_count += 1
            except Exception as e:
                logger.warning(
                    "[startup-recovery] Failed to recover session %s, deleting: %s",
                    entry.session_id,
                    e,
                )
                try:
                    entry.delete()
                except Exception:
                    pass

    logger.warning(
        "[startup-recovery] Recovered %d bridge session(s), %d local dev session(s), "
        "abandoned %d local PM/teammate session(s)",
        bridge_count,
        local_dev_count,
        abandoned,
    )
    return bridge_count + local_dev_count


def _sweep_dead_worker_sessions() -> int:
    """Sweep running sessions whose claude_pid is dead after a worker restart.

    Called during worker startup recovery (issue #1767). When a worker dies in
    U-state, sessions remain status='running' with a stale claude_pid. Without
    this sweep, those sessions are forever orphaned — the worker never picks them
    up (it only re-kicks 'pending') and the human's message is silently dropped.

    Guards against double-drop races:
    - Only sweeps sessions with dead claude_pid (os.kill(pid, 0) raises OSError)
    - Applies the AGENT_SESSION_HEALTH_MIN_RUNNING recency guard (300s)
      so brand-new sessions from the fresh worker cannot be touched
    - Relies on finalize_session's implicit status re-check: it re-reads the
      session status and raises StatusConflictError if the status has already
      changed, so a concurrent fresh-worker pickup wins and the session is skipped

    Returns the count of sessions swept to 'killed'.
    """
    running_sessions = _filter_hydrated_sessions(AgentSession.query.filter(status="running"))
    if not running_sessions:
        return 0

    now = time.time()
    cutoff = now - AGENT_SESSION_HEALTH_MIN_RUNNING

    swept = 0
    for entry in running_sessions:
        pid = getattr(entry, "claude_pid", None)

        # Skip sessions with no PID — they haven't been assigned a subprocess yet
        if not pid:
            continue

        # Skip recently-started sessions — they may belong to the freshly-started worker
        started_ts = _ts(getattr(entry, "started_at", None))
        if started_ts is not None and started_ts > cutoff:
            logger.debug(
                "[dead-worker-sweep] Skipping recent session %s (pid=%s, started %ds ago)",
                entry.agent_session_id,
                pid,
                int(now - started_ts),
            )
            continue

        # Check PID liveness: os.kill(pid, 0) raises OSError if dead/not accessible
        try:
            os.kill(int(pid), 0)
            # PID is alive — not a dead-worker orphan, skip it
            logger.debug(
                "[dead-worker-sweep] Session %s pid=%s is alive, skipping",
                entry.agent_session_id,
                pid,
            )
            continue
        except OSError:
            # PID is dead — this session is orphaned from the previous worker
            pass

        logger.warning(
            "[dead-worker-sweep] Session %s has dead claude_pid=%s — sweeping to killed",
            entry.agent_session_id,
            pid,
        )

        try:
            from models.session_lifecycle import StatusConflictError, finalize_session

            finalize_session(
                entry,
                "killed",
                reason=f"dead-worker-sweep: claude_pid={pid} not alive at worker restart (#1767)",
            )
            swept += 1
        except StatusConflictError as e:
            # Concurrent modification — another process already handled this session
            logger.info(
                "[dead-worker-sweep] Status conflict sweeping session %s (skipping): %s",
                entry.agent_session_id,
                e,
            )
        except Exception as e:
            logger.warning(
                "[dead-worker-sweep] Failed to sweep session %s: %s",
                entry.agent_session_id,
                e,
            )

    if swept > 0:
        logger.info("[dead-worker-sweep] Swept %d dead-worker session(s) to killed", swept)
        # Trigger catchup so unanswered human messages re-enqueue as fresh sessions
        try:
            subprocess.run(
                [sys.executable, "-m", "bridge.agent_catchup"],
                timeout=30,
                check=False,
            )
        except Exception as e:
            logger.warning("[dead-worker-sweep] Catchup trigger failed (non-fatal): %s", e)

    return swept


def _sweep_stranded_waiting_for_children_parents() -> int:
    """Re-finalize parents stranded in ``waiting_for_children`` after a crash (C1, #1817).

    Background: ``finalize_session()`` (``models/session_lifecycle.py:221``)
    finalizes the parent best-effort inside a non-fatal try/except
    (``_finalize_parent_sync``, :440-451) and ALWAYS saves the child (:474) --
    intentionally, so the child finalizes independently even if the parent
    lookup/save raises (parent deleted, Redis blip, stale index). This is a
    deliberate contract that must NOT be inverted by coupling the two writes:
    an all-or-nothing pipeline would strand the CHILD on every parent-finalize
    hiccup instead of the rarer case this sweep targets. See
    ``docs/plans/correctness-delivery-integrity.md`` C1.

    The gap this closes: a process crash AFTER the child save but BEFORE the
    parent transitions out of ``waiting_for_children`` leaves the parent
    stranded forever -- nothing else re-triggers ``_finalize_parent_sync`` for
    it once the crash window has passed.

    Why this is safe to run unconditionally on every worker startup:
    ``_finalize_parent_sync`` is itself idempotent -- it no-ops if the parent
    is already terminal or no longer exists (``session_lifecycle.py:719-732``),
    and it recomputes the parent's fate from the children's CURRENT statuses
    (not replayed/stale state) each time it runs. Calling it against a parent
    that already finalized normally, or one still legitimately waiting on a
    non-terminal child, is a no-op -- only genuinely-stranded parents (all
    children terminal, parent still ``waiting_for_children``) actually
    transition here. A concurrent finalize from another process is resolved
    by the generic CAS in ``transition_status()`` (:604-648, preserved
    untouched by this sweep); ``_transition_parent`` already swallows the
    resulting ``StatusConflictError`` at INFO level (:817-828), so this
    function never needs to special-case that race.

    Returns the count of parents actually re-finalized (confirmed by a
    post-call status check) -- NOT the count of stranded-looking parents
    scanned, most of which are expected to be legitimate no-ops.
    """
    from models.session_lifecycle import _finalize_parent_sync

    stranded = _filter_hydrated_sessions(AgentSession.query.filter(status="waiting_for_children"))
    if not stranded:
        return 0

    reswept = 0
    for parent in stranded:
        parent_id = getattr(parent, "id", None)
        if not parent_id:
            continue
        try:
            _finalize_parent_sync(parent_id)
        except Exception as e:
            logger.warning(
                "[waiting-for-children-sweep] Failed to re-finalize parent %s: %s",
                parent_id,
                e,
            )
            continue

        refreshed = AgentSession.get_by_id(parent_id)
        if refreshed is not None and refreshed.status != "waiting_for_children":
            reswept += 1
            logger.warning(
                "[waiting-for-children-sweep] Re-finalized stranded parent %s -> %s "
                "(crash-window recovery, #1817)",
                parent_id,
                refreshed.status,
            )

    return reswept


# === Agent Session Health Monitor ===


def _never_started_past_grace(
    entry: AgentSession,
    now: datetime | None = None,
) -> bool:
    """Return True when a session has NEVER produced output and has exceeded the
    combined never-started grace + confirmation margin window.

    A session is considered to have "never started" when BOTH conditions hold:
      - ``sdk_ever_output`` is False: neither ``last_tool_use_at`` nor
        ``last_turn_at`` has ever been written (no structured SDK output).
      - The session's wall-clock running time exceeds
        ``NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS``
        (default 120 + 30 = 150 seconds).

    The confirmation margin (``NEVER_STARTED_CONFIRM_MARGIN_SECS``) is stacked
    on top of the base grace to cover worst-case granite cold-start latency
    (container spin-up + TUI boot + priming). Both constants live in
    ``agent.session_stall_classifier`` and are env-overridable.

    Call sites that do NOT have ``now`` in scope (e.g. ``_has_progress`` and
    ``_tier2_reprieve_signal``) should omit the argument — this function
    derives it internally via ``datetime.now(tz=timezone.utc)``.

    Returns False (safe default) when:
      - ``sdk_ever_output`` is True (session has produced output).
      - ``started_at`` and ``created_at`` are both None (legacy / phantom
        record) — no running_seconds to compute.
      - ``running_seconds`` is below the combined threshold.
      - Any unexpected exception is encountered.

    This predicate NEVER raises.
    """
    try:
        # Derive sdk_ever_output the same way _has_progress and
        # _tier2_reprieve_signal do: True iff either per-turn field is set.
        sdk_ever_output = bool(
            getattr(entry, "last_tool_use_at", None) or getattr(entry, "last_turn_at", None)
        )
        if sdk_ever_output:
            return False

        if now is None:
            now = datetime.now(tz=UTC)

        # Use started_at or created_at as the origin timestamp, mirroring the
        # pattern established in _has_progress sub-check B (issue #1356).
        started_at = getattr(entry, "started_at", None)
        created_at = getattr(entry, "created_at", None)
        started_ref = started_at if isinstance(started_at, datetime) else created_at
        if not isinstance(started_ref, datetime):
            # Legacy / phantom record — no running_seconds, safe default.
            return False

        started_aware = started_ref if started_ref.tzinfo else started_ref.replace(tzinfo=UTC)
        running_seconds = (now - started_aware).total_seconds()

        threshold = NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS
        return running_seconds > threshold
    except Exception:
        return False


def _trusted_utc_now() -> datetime:
    """Return "now" from Redis's own clock (the ``TIME`` command) rather than
    this process's local wall-clock.

    C2 (#1817): freshness checks in ``_has_progress`` compare a session's
    last-write timestamp (``last_heartbeat_at``, ``last_tool_use_at``, ...)
    against "now" to decide staleness. Using each reader's own local
    wall-clock as "now" means a reader whose clock is skewed AHEAD of the
    writer's can flag a genuinely fresh session as stale — a spurious
    HEARTBEAT_FRESHNESS_WINDOW (90s) miss that triggers unnecessary
    recovery/kill. Sourcing "now" from Redis's ``TIME`` command instead gives
    every reader (across machines) the SAME shared reference clock, so an
    individual reader's local clock skew drops out of the age computation
    entirely — only the age relative to a single trusted source matters.

    Falls back to local wall-clock on any Redis error (connection blip):
    staleness evaluation must never hard-fail because the trusted-clock probe
    itself failed. A rare fallback to local time on a Redis hiccup is no
    worse than the pre-fix behavior it replaces.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        seconds, microseconds = POPOTO_REDIS_DB.time()
        return datetime.fromtimestamp(seconds + microseconds / 1_000_000, tz=UTC)
    except Exception as e:
        logger.debug(
            "[session-health] _trusted_utc_now: Redis TIME unavailable, using local clock: %s",
            e,
        )
        return datetime.now(tz=UTC)


def _has_progress(entry: AgentSession) -> bool:
    """Return True iff the session shows any signal that real work has begun.

    Evidence-only model (issue #1172): inference paths from #1046
    (``STDOUT_FRESHNESS_WINDOW``, ``FIRST_STDOUT_DEADLINE``) are gone. Stdout
    silence is no longer a kill signal — long-thinking turns and large tool
    outputs produce legitimate stdout silence.

    Tier 1 is evaluated in two sub-checks (A and B). Any one passing → return True.

    **Sub-check A: Per-turn SDK progress (#1226).**
    ``last_tool_use_at`` (written by PreToolUse/PostToolUse hooks) and
    ``last_turn_at`` (written by sdk_client on ``result`` event) are evidence
    of actual structured SDK output. Either field fresher than
    ``SDK_PROGRESS_FRESHNESS_WINDOW`` (1800s, 30 min) ⇒ progress.
    ``last_sdk_heartbeat_at`` is written by ``BackgroundTask._watchdog`` on
    subprocess existence — it is a watchdog-alive signal, NOT a progress signal.
    It is intentionally excluded from sub-check A (issue #1226).

    **Sub-check B: Startup-window executor-alive fallback (#1036, narrowed by #1226 / #1356).**
    When ``sdk_ever_output`` is False (neither per-turn field has ever been set),
    ``last_heartbeat_at`` (queue-layer, written by ``_heartbeat_loop``) fresher
    than ``HEARTBEAT_FRESHNESS_WINDOW`` (90s) ⇒ progress, **subject to the
    no-output running-time budget gate added by issue #1356**.

    The gate reads ``started_ref = entry.started_at or entry.created_at`` and
    computes ``running_seconds``:

    - Both ``started_at`` and ``created_at`` are None (truly legacy / phantom
      record predating the field) — the fresh-heartbeat fast-path is preserved.
    - ``running_seconds < STARTUP_GRACE_SECONDS`` (300s, aliased to
      ``AGENT_SESSION_HEALTH_MIN_RUNNING``) — the fast-path is preserved.
      The caller's race-condition guard already filters sessions whose
      running time is below this threshold; the explicit re-check defends
      against clock skew and future reuse paths.
    - ``STARTUP_GRACE_SECONDS <= running_seconds <= NO_OUTPUT_BUDGET_SECONDS``
      (where ``NO_OUTPUT_BUDGET_SECONDS = MAX_NO_OUTPUT_REPRIEVES *
      HEARTBEAT_FRESHNESS_WINDOW`` = 20 * 90 = 1800s = 30 min) — fresh
      heartbeat still passes (preserves backward compatibility for sessions
      in their normal startup-to-first-turn window).
    - ``running_seconds > NO_OUTPUT_BUDGET_SECONDS`` AND
      ``sdk_ever_output is False`` — sub-check B does NOT return True; it
      falls through to the own-progress fields. The Redis counter
      ``{project_key}:session-health:tier1_falloff:no_output_budget_exceeded``
      is INCR'd once per fall-through tick. With the per-turn signals also
      absent, ``_has_progress`` returns False and the Tier-2 reprieve cap
      escalates the session to recovery within
      ``MAX_NO_OUTPUT_REPRIEVES`` (20) ticks.

    The ``started_at or created_at`` fallback is load-bearing: the recovery
    path nulls ``started_at`` when re-queuing a session, so without the
    fallback a recovered session would silently re-enter the legacy fast-path
    and re-open the wedge.

    This preserves the pre-#1226 behavior for sessions in their normal
    startup window and for sessions predating PR #1177 (whose hooks did not
    write the per-turn fields), while bounding the previously-unbounded
    fresh-heartbeat fast-path that allowed cwd-disappearance and similar
    wedges to hold Tier 1 open indefinitely (issue #1246, parent of #1356).

    **Own-progress fields (#944 / #963, narrowed by #1226).**
    - ``turn_count > 0`` — at least one turn boundary observed.
    - ``log_path`` non-empty — first log entry written.
    - ``claude_session_uuid`` non-empty — SDK authenticated.
    These are sticky once set and cannot detect mid-run hangs. They are only
    evaluated when ``sdk_ever_output`` is False — once the SDK has produced a
    tool or turn event, sub-check A is the authoritative Tier 1 signal.

    **Child-progress check (#944, retained).**
    A PM session with at least one non-terminal child is not stuck.
    ``get_children()`` returns ``[]`` on failure with a WARNING log; no outer
    try/except needed.

    Returns ``False`` only when EVERY signal is absent. The caller
    (``_agent_session_health_check``) then evaluates ``_tier2_reprieve_signal``
    before deciding to recover.
    """
    # C2 (#1817): sourced from Redis TIME, not local wall-clock, so a reader
    # whose clock is skewed ahead of the writer's does not flag a fresh
    # session as stale. See _trusted_utc_now() docstring.
    now_utc = _trusted_utc_now()

    # Compute sdk_ever_output once — used by both sub-check A and the own-progress
    # field guard. True iff last_tool_use_at or last_turn_at has ever been written.
    sdk_ever_output = bool(
        getattr(entry, "last_tool_use_at", None) or getattr(entry, "last_turn_at", None)
    )

    # Sub-check A: per-turn SDK activity (issue #1226).
    # last_tool_use_at (PreToolUse/PostToolUse hooks) and last_turn_at (result event)
    # are evidence of actual structured SDK output. Fresh within
    # SDK_PROGRESS_FRESHNESS_WINDOW → real progress.
    for progress_attr in ("last_tool_use_at", "last_turn_at"):
        ts = getattr(entry, progress_attr, None)
        if isinstance(ts, datetime):
            ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            if (now_utc - ts_aware).total_seconds() < SDK_PROGRESS_FRESHNESS_WINDOW:
                return True

    # Sub-check B: startup-window executor-alive fallback (#1036 retained, narrowed
    # by #1226 / #1356).
    # Use last_heartbeat_at as a Tier 1 signal ONLY before the SDK has produced any
    # tool or turn output. Once sdk_ever_output is True, sub-check A is authoritative.
    # Backward-compatible: sessions from before PR #1177 (no tool/turn fields) fall
    # here and behave identically to the pre-#1226 behavior.
    #
    # The fresh-heartbeat fast-path is gated by the no-output running-time budget
    # added in issue #1356. See _has_progress docstring for the full rationale and
    # the four legs of the gate (legacy/grace/in-band/budget-exceeded).
    if not sdk_ever_output:
        hb = getattr(entry, "last_heartbeat_at", None)
        if isinstance(hb, datetime):
            hb_aware = hb if hb.tzinfo else hb.replace(tzinfo=UTC)
            if (now_utc - hb_aware).total_seconds() < HEARTBEAT_FRESHNESS_WINDOW:
                # Sub-check B D0 gate (issue #1724): deny the fresh-heartbeat
                # fast-path when the session is never-started past grace.
                # _never_started_past_grace uses now_utc internally (called
                # without a `now` arg here; it derives now itself). When True,
                # the session has been running longer than
                # NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS
                # without any SDK output — a fresh heartbeat must NOT falsely
                # signal "alive" for a session that has never started.
                if _never_started_past_grace(entry):
                    return False

                # Compute running_seconds and apply the no-output budget gate
                # (NO_OUTPUT_BUDGET_SECONDS = 1800s, 30 min).
                #
                # Use ``started_ref = entry.started_at or entry.created_at``.
                # The recovery path nulls ``started_at`` when re-queuing a
                # session to pending, so a recovered session would re-enter
                # sub-check B with ``started_at=None`` and silently take the
                # legacy fast-path — re-opening the wedge. The
                # ``started_at or created_at`` fallback mirrors the
                # established pattern at ``models/agent_session.py``.
                started_at = getattr(entry, "started_at", None)
                created_at = getattr(entry, "created_at", None)
                started_ref = started_at if isinstance(started_at, datetime) else created_at
                if not isinstance(started_ref, datetime):
                    # Truly legacy session (both fields None — pre-dates
                    # ``created_at`` introduction or phantom record) →
                    # preserve fast-path.
                    return True
                started_aware = (
                    started_ref if started_ref.tzinfo else started_ref.replace(tzinfo=UTC)
                )
                running_seconds = (now_utc - started_aware).total_seconds()
                if running_seconds < STARTUP_GRACE_SECONDS:
                    # Inside the startup grace window (or clock-skew negative
                    # running_seconds) → preserve fast-path. The
                    # ``running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING``
                    # caller-side guard already rules out genuinely fresh
                    # sessions, but we keep the explicit check for defense in
                    # depth and to handle clock skew.
                    return True
                if running_seconds <= NO_OUTPUT_BUDGET_SECONDS:
                    # In the band between startup grace (300s) and the
                    # no-output budget (1800s) → fresh heartbeat still passes.
                    # Preserves the normal startup-to-first-turn window for
                    # slow auth / large initial prompt digestion.
                    return True
                # Budget exceeded AND sdk_ever_output is False — DO NOT return
                # True from sub-check B. INCR the telemetry counter exactly
                # once on this fall-through path, then continue to the
                # own-progress fields below. If those are also absent,
                # _has_progress returns False and the existing Tier-2 reprieve
                # cap (also gated on sdk_ever_output / MAX_NO_OUTPUT_REPRIEVES)
                # escalates to recovery.
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB as _MR

                    _MR.incr(
                        f"{entry.project_key}:session-health:"
                        f"tier1_falloff:no_output_budget_exceeded"
                    )
                except Exception as _m_err:
                    logger.warning(
                        "[session-health] tier1_falloff counter increment failed (non-fatal): %s",
                        _m_err,
                    )

    # Own-progress fields (#944 / #963, narrowed by #1226, gated by #1614).
    # Only evaluated when sdk_ever_output is False — once the SDK has produced
    # a tool or turn event, per-turn freshness (sub-check A) is authoritative.
    #
    # Issue #1614 — confirmed verdict (Branch 2): ungated claude_session_uuid
    # returned True unconditionally when sdk_ever_output=False, blocking
    # recovery of zombie sessions whose heartbeat loop had silently exited.
    # Fix: gate the own-progress fields on heartbeat freshness — only honour
    # them if the session's last_heartbeat_at is within NO_OUTPUT_BUDGET_SECONDS
    # (1800s). A stale or absent heartbeat means the executor is likely dead;
    # own-progress fields must not keep the session alive indefinitely.
    # AC3 constraint: gate window MUST be >= NO_OUTPUT_BUDGET_SECONDS (1800s);
    # do NOT use the tighter HEARTBEAT_FRESHNESS_WINDOW (90s) here.
    if not sdk_ever_output:
        _hb_own = getattr(entry, "last_heartbeat_at", None)
        _own_progress_fresh = False
        if isinstance(_hb_own, datetime):
            _hb_own_aware = _hb_own if _hb_own.tzinfo else _hb_own.replace(tzinfo=UTC)
            _hb_age = (now_utc - _hb_own_aware).total_seconds()
            if _hb_age < NO_OUTPUT_BUDGET_SECONDS:
                _own_progress_fresh = True
        # If heartbeat is stale or absent, fall through — do NOT return True.
        if _own_progress_fresh:
            if (entry.turn_count or 0) > 0:
                return True
            if bool((entry.log_path or "").strip()):
                return True
            if bool(entry.claude_session_uuid):
                return True

    # Child-progress check: a PM session with active children is not stuck.
    # get_children() queries via Popoto parent_agent_session_id index and
    # returns [] on failure with a WARNING log — no outer try/except needed.
    children = entry.get_children()
    if any(c.status not in _TERMINAL_STATUSES for c in children):
        return True
    return False


def _tier2_reprieve_signal(
    handle: "SessionHandle | None",
    entry: AgentSession,
) -> str | None:
    """Evaluate Tier 2 activity-positive reprieve gates (issue #1036, #1099, #1172).

    Called by the health check after ``_has_progress`` has returned False for a
    session whose worker is alive (no_progress recovery branch). Any single
    positive signal reprieves the kill.

    Gates (order matters for telemetry — the first passing gate is returned):
      "compacting" — ``entry.last_compaction_ts`` is within
                     ``COMPACT_REPRIEVE_WINDOW_SEC``. Companion writer:
                     ``agent/hooks/pre_compact.py::pre_compact_hook`` updates
                     ``last_compaction_ts`` on every successful backup. Added
                     for issue #1099 Mode 3 — prevents false kills on
                     sessions that are legitimately idle post-compaction.
      "pty_alive"  — granite transport gate (issue #1820 note 2): the session
                     has no ``claude_pid`` (granite PTY, not SDK) and
                     ``_pty_quiescent_long_enough`` reports the PTY read loop is
                     alive/painting (not yet wedge-eligible). Evaluated before
                     the psutil gates, which are a no-op for a pid-less granite
                     session. SDK sessions never match this gate.
      "children"   — ``psutil.Process(pid).children()`` is non-empty.
                     Strongest signal: tool-subprocess execution is actively
                     happening right now. Returned in preference to "alive".
      "alive"      — ``psutil.Process(pid).status()`` is not one of
                     {zombie, dead, stopped}. Proves the SDK subprocess
                     still exists and is not a zombie.

    Returns the name of the first passing gate ("compacting", "children", or
    "alive"), or ``None`` if every gate fails.

    **Reprieve escalation guard (issue #1226):** When ``sdk_ever_output`` is
    False (the session has never produced a tool or turn event) AND
    ``reprieve_count >= MAX_NO_OUTPUT_REPRIEVES``, all gates are suppressed
    and ``None`` is returned immediately. This prevents indefinite alive-but-
    silent sessions from being reprieved forever. Sessions with
    ``sdk_ever_output=True`` (have produced output) are never subject to this
    cap — their recovery depends solely on per-turn freshness in
    ``_has_progress`` sub-check A.

    The previous "stdout" gate (and its ``STDOUT_FRESHNESS_WINDOW`` constant)
    was retired by issue #1172. Recent stdout is no longer evidence the
    subprocess is making progress — long-thinking turns and large tool
    outputs produce legitimate stdout silence.

    Failure handling:
      * ``last_compaction_ts`` is ``None`` / non-numeric → "compacting" skipped.
      * ``handle is None`` or ``handle.pid is None`` → psutil gates skipped.
      * ``psutil.NoSuchProcess`` / ``psutil.AccessDenied`` / ``ImportError``
        → psutil gates skipped silently.

    This helper NEVER raises. A genuinely dead session where every gate
    fails is preferable to crashing the health-check loop.
    """
    # Reprieve escalation guard (issue #1226): suppress all Tier 2 reprieves
    # for sessions that have NEVER produced any SDK output once reprieve_count
    # reaches MAX_NO_OUTPUT_REPRIEVES. This ensures sessions that hang from
    # the very first turn are eventually recovered rather than being reprieved
    # forever. Sessions with sdk_ever_output=True are NOT subject to this cap.
    sdk_ever_output = bool(
        getattr(entry, "last_tool_use_at", None) or getattr(entry, "last_turn_at", None)
    )
    reprieve_count = getattr(entry, "reprieve_count", 0) or 0
    if not sdk_ever_output and (
        reprieve_count >= MAX_NO_OUTPUT_REPRIEVES or _never_started_past_grace(entry)
    ):
        return None  # escalate: suppress all Tier 2 reprieves, allow recovery

    # "compacting" — reprieve when a compaction completed within
    # COMPACT_REPRIEVE_WINDOW_SEC seconds. Evaluated FIRST so the telemetry
    # counter (``tier2_reprieve_total:compacting``) distinguishes this case
    # from the psutil-based gates. See issue #1099 Mode 3.
    lct = getattr(entry, "last_compaction_ts", None)
    if lct is not None:
        try:
            if (time.time() - float(lct)) < COMPACT_REPRIEVE_WINDOW_SEC:
                return "compacting"
        except (TypeError, ValueError):
            # Defensive: malformed timestamp on the entry — skip this gate.
            pass

    # Granite transport-aware reprieve (issue #1820 note 2). A granite PTY
    # session records no ``claude_pid`` (SDK-path-only,
    # ``session_executor.py:1425``), so ``handle.pid`` is None and the psutil
    # "children"/"alive" gates below are a structural no-op for it. Without a
    # transport-specific gate, a legitimately-parked granite session (e.g. a
    # ``waiting_for_children`` PM) whose PTY read loop is alive but momentarily
    # quiet would be falsely killed at the progress deadline. Derive the
    # reprieve from the PTY-liveness signal instead: ``_pty_quiescent_long_enough``
    # returns False while the granite PTY read loop is alive and painting
    # (defer the kill) and True only once it has been quiescent past the
    # wedge-eligibility window (proceed to kill) — the exact "genuinely
    # quiescent granite IS killed" boundary the plan requires. For SDK sessions
    # (``last_pty_read_loop_at is None``) it returns True, so this gate never
    # reprieves an SDK session and cannot interfere with the psutil path below.
    if getattr(entry, "claude_pid", None) is None:
        try:
            if not _pty_quiescent_long_enough(entry, datetime.now(UTC)):
                return "pty_alive"
        except Exception as e:
            # Defensive: never crash the health check from a PTY-liveness edge case.
            logger.debug("[session-health] pty-liveness reprieve probe failed: %s", e)

    pid = handle.pid if handle is not None else None
    if pid is not None:
        try:
            import psutil

            proc = psutil.Process(pid)
            status = proc.status()
            if status not in (
                psutil.STATUS_ZOMBIE,
                psutil.STATUS_DEAD,
                psutil.STATUS_STOPPED,
            ):
                # Prefer "children" when present — stronger signal.
                if proc.children():
                    return "children"
                return "alive"
        except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError):
            pass
        except Exception as e:
            # Defensive: never crash the health check from a psutil edge case.
            logger.debug("[session-health] psutil probe failed for pid=%s: %s", pid, e)

    return None


def _should_kill_no_progress(
    entry: AgentSession,
    handle: "SessionHandle | None",
    *,
    emit_telemetry: bool,
) -> bool:
    """Shared Tier-2 reprieve gate for every ``no_progress``-shaped kill decision
    (issue #1820 OQ3 — exactly one place this decision lives; NO-LEGACY).

    Returns ``True`` if the session should be killed (no reprieve signal
    applies), ``False`` if a Tier-2 reprieve (active children / compaction /
    alive-but-quiet subprocess) applies and the caller must skip the kill.

    This is a straight extraction of the reprieve decision that used to be
    inlined in ``_apply_recovery_transition``'s ``reason_kind == "no_progress"``
    branch — behavior is unchanged, only the call site moved, so every
    ``no_progress``-shaped producer shares one reprieve policy instead of each
    carrying its own copy:

      * ``_apply_recovery_transition`` calls this for BOTH live ``no_progress``
        producers — the never-started-past-grace path
        (``session_health.py`` D0 branch) and the narrowed running-scan
        ``no_progress`` elif (the #944 shared-``worker_key`` orphan net) —
        passing ``emit_telemetry=True`` on its single per-recovery-decision
        call (the same cadence the inlined block used before extraction).
      * The progress-deadline cancel scope (issue #1820 Fix #3,
        ``agent_session_queue.py``) re-invokes this every ``PROGRESS_POLL_S``
        poll while a session is past its deadline, and passes
        ``emit_telemetry=True`` ONLY on the first deadline-exceeded poll (a
        loop-local latch) so a long-reprieved session's telemetry fires once,
        not once per poll.

    ``emit_telemetry=True`` increments the tier-1-flagged counter
    unconditionally (the gate was evaluated), and — only when a reprieve
    signal actually fires — increments ``tier2_reprieve_total:{reprieve}``,
    saves the bumped ``entry.reprieve_count``, and logs the reprieve.
    ``emit_telemetry=False`` still evaluates the gate (the kill/reprieve
    decision itself is never skipped) but performs none of those side
    effects — this predicate is
    therefore NOT pure, but the caller fully controls when its side effects
    fire (issue #1820 CONCERN r6).

    Never raises — counter/save failures are logged and swallowed, matching
    the pre-extraction inline block.
    """
    if emit_telemetry:
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _MR

            _MR.incr(f"{entry.project_key}:session-health:tier1_flagged_total")
        except Exception as _m_err:
            logger.debug("[session-health] tier1_flagged counter failed: %s", _m_err)

    reprieve = _tier2_reprieve_signal(handle, entry)
    if reprieve is not None:
        if emit_telemetry:
            try:
                from popoto.redis_db import POPOTO_REDIS_DB as _MR

                _MR.incr(f"{entry.project_key}:session-health:tier2_reprieve_total:{reprieve}")
            except Exception as _m_err:
                logger.debug("[session-health] tier2_reprieve counter failed: %s", _m_err)
            try:
                entry.reprieve_count = (entry.reprieve_count or 0) + 1
                entry.save(update_fields=["reprieve_count"])
            except Exception as _rc_err:
                logger.debug("[session-health] reprieve_count save failed: %s", _rc_err)
            log_fn = logger.warning if (entry.reprieve_count or 0) >= 3 else logger.info
            log_fn(
                "[session-health] Tier 2 reprieve (%s) for session %s — "
                "skipping kill (reprieve_count=%s)",
                reprieve,
                entry.agent_session_id,
                entry.reprieve_count,
            )
        return False
    return True


# Total wall-clock budget for the SIGTERM->SIGKILL escalation in
# ``_confirm_subprocess_dead``. Kept to single-digit seconds so the liveness
# loop is never stalled by a slow kill (issue #1537 No-Go: short grace only).
SUBPROCESS_KILL_TIMEOUT = 3.0
# Poll interval while waiting for a signalled PID to exit.
_SUBPROCESS_KILL_POLL_INTERVAL = 0.1


def _increment_subprocess_kill_counter(session, *, escalated: bool) -> None:
    """Best-effort Redis counter for the recovery subprocess-kill escalation (#1537).

    ``escalated=True``  -> ``{project_key}:session-health:subprocess_kill_escalated``
        (a kill signal — SIGTERM and/or SIGKILL — was actually delivered because
        ``task.cancel()`` left the subprocess alive).
    ``escalated=False`` -> ``{project_key}:session-health:subprocess_kill_failed``
        (the subprocess could not be confirmed dead; session escalates to ``failed``).

    The escalated counter intentionally does NOT fire on the *already-dead* path
    (``task.cancel()`` sufficed and no signal was sent): counting that as an
    escalation would inflate the metric and hide how often the SDK subprocess
    genuinely ignores cancellation. See ``_confirm_subprocess_dead`` →
    ``SubprocessKillResult.signal_sent``.

    A counter-backend failure must never propagate out of recovery.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        project_key = getattr(session, "project_key", None) or "unknown"
        suffix = "subprocess_kill_escalated" if escalated else "subprocess_kill_failed"
        _R.incr(f"{project_key}:session-health:{suffix}")
    except Exception as e:
        logger.debug("[session-health] subprocess_kill counter failed (non-fatal): %s", e)


class SubprocessKillResult(NamedTuple):
    """Outcome of ``_confirm_subprocess_dead`` (issue #1537).

    ``confirmed_dead``
        ``True`` when the PID is confirmed gone; ``False`` when it cannot be
        confirmed dead (still alive after SIGKILL, ``PermissionError``, or any
        unexpected error). Drives the caller's requeue-vs-``failed`` branch.
    ``signal_sent``
        ``True`` only when a kill signal (SIGTERM and/or SIGKILL) was actually
        delivered — i.e. the subprocess survived ``task.cancel()`` and had to be
        escalated. ``False`` on the *already-dead* path (cancel sufficed, no PID,
        or the very first liveness probe reports the process gone), so the caller
        does NOT over-count those as kill escalations.
    """

    confirmed_dead: bool
    signal_sent: bool


def _confirm_subprocess_dead(pid: "int | None", *, timeout: float) -> SubprocessKillResult:
    """Confirm a recovery target's ``claude -p`` subprocess is gone, escalating signals (#1537).

    ``task.cancel()`` does not guarantee the underlying SDK subprocess exited — a
    true hang ignores cancellation and orphans the PID. This helper closes that
    gap: it verifies liveness, then escalates SIGTERM -> SIGKILL, polling for exit
    within a short ``timeout`` so the liveness loop is never stalled.

    Returns a :class:`SubprocessKillResult` ``(confirmed_dead, signal_sent)``:

    * ``confirmed_dead=True`` only when the PID is confirmed gone
      (``os.kill(pid, 0)`` raises ``ProcessLookupError``); ``False`` when it cannot
      be confirmed dead (still alive after SIGKILL, ``PermissionError``, or any
      unexpected error). A non-confirmed result is the signal for the caller to
      escalate the session to ``failed`` so the orphan reaper owns cleanup, rather
      than requeuing an invisible orphan to ``pending``.
    * ``signal_sent=True`` only when SIGTERM and/or SIGKILL was actually delivered.
      It stays ``False`` on the already-dead path (no PID, or the process was gone
      at the first probe because ``task.cancel()`` terminated it) so the caller can
      distinguish "cancel sufficed" from "we had to kill it" and avoid inflating the
      escalated counter.

    NOTE: this helper is synchronous and uses ``time.sleep`` while polling. It must
    NOT be awaited directly on the worker event loop; ``_apply_recovery_transition``
    offloads it via ``run_in_executor`` so the kill grace period (up to ``timeout``
    seconds) never stalls other worker coroutines.

    PID-reuse caveat: a recorded ``claude_pid`` could in principle be recycled by an
    unrelated process before recovery runs. The window is the sub-second recovery
    path and this matches the existing PPID==1 reaper's assumptions (issue #1537
    Race Condition Analysis); we accept the residual risk rather than tracking PID
    generations.
    """
    if pid is None or pid <= 0:
        return SubprocessKillResult(confirmed_dead=True, signal_sent=False)

    deadline = time.monotonic() + max(timeout, 0.0)

    def _is_dead() -> bool:
        """``True`` iff signal 0 reports the PID is gone."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # Process exists but is owned by another user — cannot confirm death.
            return False
        except OSError:
            return False
        return False

    # Already gone (e.g. task.cancel() did terminate it)? No signal was sent.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return SubprocessKillResult(confirmed_dead=True, signal_sent=False)
    except PermissionError:
        return SubprocessKillResult(confirmed_dead=False, signal_sent=False)
    except OSError:
        return SubprocessKillResult(confirmed_dead=False, signal_sent=False)

    def _poll_until_dead() -> bool:
        while time.monotonic() < deadline:
            if _is_dead():
                return True
            time.sleep(_SUBPROCESS_KILL_POLL_INTERVAL)
        return _is_dead()

    # Escalation step 1: SIGTERM, then poll for graceful exit. From here on a
    # signal has been delivered, so signal_sent is True regardless of the outcome.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Raced to exit between the probe and SIGTERM — no signal landed.
        return SubprocessKillResult(confirmed_dead=True, signal_sent=False)
    except (PermissionError, OSError) as e:
        logger.debug("[session-health] SIGTERM failed for recovery pid=%s: %s", pid, e)
        return SubprocessKillResult(confirmed_dead=_is_dead(), signal_sent=False)

    if _poll_until_dead():
        return SubprocessKillResult(confirmed_dead=True, signal_sent=True)

    # Escalation step 2: SIGKILL only when SIGTERM failed to terminate it.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return SubprocessKillResult(confirmed_dead=True, signal_sent=True)
    except (PermissionError, OSError) as e:
        logger.debug("[session-health] SIGKILL failed for recovery pid=%s: %s", pid, e)
        return SubprocessKillResult(confirmed_dead=_is_dead(), signal_sent=True)

    return SubprocessKillResult(confirmed_dead=_poll_until_dead(), signal_sent=True)


# === MCP hang graceful degradation helpers (issue #1711) ===


def _compose_tool_timeout_steering(tool_name: str, original_request: str | None) -> str:
    """Compose the advisory steering message injected when a tool times out.

    Pure function — never raises. Returns a self-contained string the session
    can consume on the next turn.

    **Self-contained requirement:** The message must not rely on ``--resume``
    continuity. When the health-check kills the stuck subprocess, the prior
    ``claude_session_uuid`` becomes stale; the harness falls back to a fresh
    run (no ``--resume``) using ``full_context_message``. A fresh run has no
    prior conversation history, so the steering message must embed the original
    request verbatim — it is the only thread of context the re-queued turn has.

    Args:
        tool_name: The tool that timed out (e.g. ``mcp__foo__bar``).
        original_request: The user's original message text. Truncated to 1500
            chars. May be None or empty — still returns a valid string.
    """
    req = (original_request or "").strip()
    if req:
        req_truncated = req[:1500]
        req_part = f" Original request: {req_truncated}"
    else:
        req_part = ""
    return (
        f"The tool {tool_name} timed out and is temporarily unavailable — "
        f"do not call it again this turn. Answer the user's original request "
        f"as best you can without it, and note which information was "
        f"unavailable.{req_part}"
    )


async def _deliver_tool_timeout_degraded_notice(
    entry: "AgentSession",
    tool_name: str | None,
) -> None:
    """Send a one-shot degraded-service notice when a tool timeout leads to
    session failure.

    Idempotent: the first caller wins via Redis SETNX on
    ``tool_timeout:degraded_sent:{session_id}`` (1 h TTL). Subsequent calls
    return immediately.

    Transport is read from ``entry.extra_context["transport"]`` — AgentSession
    has no top-level ``transport`` field.  Falls back to FileOutputHandler when
    no registered callback is found.

    Never raises; failures are logged at WARNING and swallowed.
    """
    try:
        session_id = getattr(entry, "session_id", None) or getattr(entry, "agent_session_id", None)
        project_key = getattr(entry, "project_key", None) or "unknown"

        # Atomic dedup: only the first caller sends the notice (1 h window).
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

            lock_key = f"tool_timeout:degraded_sent:{session_id}"
            acquired = _R.set(lock_key, "1", nx=True, ex=3600)
            if not acquired:
                logger.debug(
                    "[session-health] degraded notice already sent for %s — skipping",
                    session_id,
                )
                return
        except Exception as _lock_err:
            logger.warning(
                "[session-health] degraded notice lock failed for %s: %s; proceeding anyway",
                session_id,
                _lock_err,
            )

        # Resolve transport from extra_context (never from a top-level field).
        transport = (getattr(entry, "extra_context", None) or {}).get("transport")

        # Resolve send callback — fall back to FileOutputHandler when none registered.
        from agent.agent_session_queue import _resolve_callbacks  # noqa: PLC0415

        send_cb, _react_cb = _resolve_callbacks(project_key, transport)
        if send_cb is None:
            from agent.output_handler import FileOutputHandler  # noqa: PLC0415

            _fallback = FileOutputHandler()
            send_cb = _fallback.send

        # Compose the user-facing degraded notice.
        tool_label = tool_name or "the requested service"
        message = (
            f"I couldn't finish that — the {tool_label} service didn't respond. "
            f"Please try again shortly; everything else is working."
        )

        chat_id = getattr(entry, "chat_id", None) or ""
        telegram_message_id = getattr(entry, "telegram_message_id", None) or 0

        await send_cb(chat_id, message, telegram_message_id, entry)

        # Best-effort telemetry counter.
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _R2  # noqa: PLC0415

            _R2.incr(f"{project_key}:session-health:tool_timeout_degraded_delivered")
        except Exception:
            pass

    except Exception as _err:
        logger.warning(
            "[session-health] _deliver_tool_timeout_degraded_notice failed for %s: %s",
            getattr(entry, "session_id", "?"),
            _err,
        )


def flush_deferred_self_draft_sync(session: "AgentSession", status: str | None = None) -> None:
    """Chokepoint flush for a never-redrafted deferred self-draft on terminal paths.

    This is the synchronous flush invoked from the ``finalize_session``
    chokepoint (``models/session_lifecycle.py``) so a held self-draft reply is
    delivered on **every** qualifying terminal status — closing the gap where a
    cleanly-``completed`` session that deferred a reply for self-draft and never
    redrafted silently swallowed it.

    Fully synchronous: it writes the payload directly to the outbox via ``rpush``
    with no event loop involvement (no ``await``, no ``asyncio.create_task``, no
    ``run_until_complete``). The ``completed`` path has no running event loop, so
    the async ``_deliver_deferred_self_draft_fallback`` cannot be used here.

    Transport / status gate (evaluated BEFORE the dedup SETNX so the key is not
    burned on ineligible paths):

    * **telegram** (or ``None``): proceeds for all terminal statuses — ``completed``,
      ``failed``, ``abandoned``.  The async helper early-returns for telegram, so
      this chokepoint owns telegram delivery exclusively.
    * **email** + ``status == "completed"``: proceeds and writes to
      ``email:outbox:{session_id}``.  The async helper handles email
      ``failed``/``abandoned`` paths.
    * **email** + any other status (``failed``, ``abandoned``, ``None``): early-returns.
      The async helper owns those paths so there is no double-send.

    Reads the deferral flag from a FRESH authoritative session via
    ``get_authoritative_session`` — never the caller's possibly-stale
    ``extra_context`` (the defer-time persist may post-date the caller's
    in-memory copy).

    Dedups on its OWN key ``self_draft_completed_flush_sent:{session_id}``
    (SETNX, 1 h TTL) — DISTINCT from the async helper's
    ``self_draft_fallback_sent:{session_id}``. Never raises; failures are logged
    at WARNING and swallowed.

    Args:
        session: AgentSession to flush.
        status: The terminal status being applied (e.g. ``"completed"``,
            ``"failed"``, ``"abandoned"``).  Forwarded from
            ``finalize_session`` so the email gate can restrict delivery to
            the ``completed`` path only.
    """
    try:
        session_id = getattr(session, "session_id", None)
        if not session_id:
            return

        # Authoritative read: the defer-time persist may post-date the caller's
        # in-memory copy, so read extra_context from a fresh re-read.
        fresh = get_authoritative_session(session_id)
        source = fresh if fresh is not None else session
        extra_ctx = getattr(source, "extra_context", None) or {}

        if not extra_ctx.get("deferred_self_draft_pending"):
            return

        # Transport / status gate — evaluated BEFORE the dedup SETNX so the key
        # is not burned on ineligible paths (e.g. email + failed).
        transport = extra_ctx.get("transport")
        if transport == "email":
            # Email: only proceed on the completed path.  The async fallback
            # helper (_deliver_deferred_self_draft_fallback) owns failed/abandoned.
            if status != "completed":
                return
        # telegram / None transport: proceed unconditionally (async helper
        # early-returns for telegram, so no double-send risk).

        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

        # Atomic dedup on the NEW completed-path key (distinct from the async
        # helper's dedup key). First caller wins.
        lock_key = f"self_draft_completed_flush_sent:{session_id}"
        acquired = _R.set(lock_key, "1", nx=True, ex=3600)
        if not acquired:
            logger.debug(
                "[session-health] self-draft completed flush already sent for %s — skipping",
                session_id,
            )
            return

        # Recover the deferred text; apply the narration gate and the empty-text
        # canned notice (parity with the async helper — kept inline, not shared).
        deferred_text = extra_ctx.get("deferred_self_draft_text") or ""
        if deferred_text and deferred_text.strip():
            try:
                from bridge.message_quality import (  # noqa: PLC0415
                    NARRATION_FALLBACK_MESSAGE,
                    is_narration_only,
                )

                if is_narration_only(deferred_text[:500]):
                    message = NARRATION_FALLBACK_MESSAGE
                else:
                    message = deferred_text
            except Exception:
                message = deferred_text
        else:
            message = "I couldn't finish responding to that — please try again."

        import json  # noqa: PLC0415

        from agent.output_handler import TelegramRelayOutputHandler  # noqa: PLC0415

        chat_id = getattr(source, "chat_id", None) or ""

        if transport == "email":
            # Email-completed branch: build the reply-all payload and push to
            # email:outbox:{session_id} for the SMTP relay.
            from agent.output_handler import build_email_outbox_payload  # noqa: PLC0415

            email_payload = build_email_outbox_payload(source, chat_id, message)
            queue_key = f"email:outbox:{session_id}"
            _R.rpush(queue_key, json.dumps(email_payload))
            _R.expire(queue_key, TelegramRelayOutputHandler.OUTBOX_TTL)
        else:
            # Telegram branch (unchanged): build the telegram outbox payload.
            reply_to = int(getattr(source, "telegram_message_id", None) or 0) or None
            payload = {
                "chat_id": chat_id,
                "reply_to": reply_to,
                "text": message,
                "session_id": session_id,
                "timestamp": time.time(),
            }
            queue_key = f"telegram:outbox:{session_id}"
            _R.rpush(queue_key, json.dumps(payload))
            _R.expire(queue_key, TelegramRelayOutputHandler.OUTBOX_TTL)

        logger.info(
            "[session-health] flushed deferred self-draft on terminal path for %s "
            "(%d chars, transport=%s)",
            session_id,
            len(message),
            transport or "telegram",
        )

        # Best-effort telemetry counter.
        try:
            project_key = getattr(source, "project_key", None) or "unknown"
            _R.incr(f"{project_key}:session-health:deferred_self_draft_completed_flush")
        except Exception:
            pass

    except Exception as _err:
        logger.warning(
            "[session-health] flush_deferred_self_draft_sync failed for %s: %s",
            getattr(session, "session_id", "?"),
            _err,
        )


async def _deliver_deferred_self_draft_fallback(
    entry: "AgentSession",
) -> None:
    """Deliver an EMAIL-transport fallback when a deferred self-draft was never completed.

    Handles the EMAIL transport specifically: it early-returns for telegram via
    ``if transport in (None, "telegram"): return`` (telegram is covered by the
    synchronous ``flush_deferred_self_draft_sync`` chokepoint flush invoked from
    ``finalize_session``). The deferral flag is written at defer time by
    ``TelegramRelayOutputHandler.send()`` — the steering queue cannot be used
    because the agent drains it at turn start, leaving it empty by finalization
    time.

    Precedence over the generic degraded notice: callers invoke this helper
    *before* ``_deliver_tool_timeout_degraded_notice``; if the deferred flag is
    set, only the self-draft fallback fires (not the generic notice).

    Idempotent: the first caller wins via Redis SETNX on
    ``self_draft_fallback_sent:{session_id}`` (1 h TTL) — DISTINCT from the sync
    flush's ``self_draft_completed_flush_sent:{session_id}`` key. The TTL is
    intentionally NOT per-run — for legitimate resume scenarios, scope it per-run
    by including ``started_at`` in the key if that becomes necessary.

    Never raises; failures are logged at WARNING and swallowed.
    """
    try:
        # Check the persisted detection signal — NOT the steering queue.
        extra_ctx = getattr(entry, "extra_context", None) or {}
        if not extra_ctx.get("deferred_self_draft_pending"):
            return

        session_id = getattr(entry, "session_id", None) or getattr(entry, "agent_session_id", None)
        project_key = getattr(entry, "project_key", None) or "unknown"

        # Atomic dedup: only the first caller sends the fallback (1 h window).
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

            lock_key = f"self_draft_fallback_sent:{session_id}"
            acquired = _R.set(lock_key, "1", nx=True, ex=3600)
            if not acquired:
                logger.debug(
                    "[session-health] self-draft fallback already sent for %s — skipping",
                    session_id,
                )
                return
        except Exception as _lock_err:
            logger.warning(
                "[session-health] self-draft fallback lock failed for %s: %s; proceeding anyway",
                session_id,
                _lock_err,
            )

        # Recover the deferred text and apply the narration gate.
        deferred_text = extra_ctx.get("deferred_self_draft_text") or ""
        if deferred_text and deferred_text.strip():
            try:
                from bridge.message_quality import (  # noqa: PLC0415
                    NARRATION_FALLBACK_MESSAGE,
                    is_narration_only,
                )

                if is_narration_only(deferred_text[:500]):
                    message = NARRATION_FALLBACK_MESSAGE
                else:
                    message = deferred_text
            except Exception:
                message = deferred_text
        else:
            message = "I couldn't finish responding to that — please try again."

        # Resolve transport from extra_context (no top-level transport field).
        transport = extra_ctx.get("transport")

        # EMAIL-only: telegram is covered by the synchronous chokepoint flush
        # (flush_deferred_self_draft_sync via finalize_session). Skip telegram here
        # to avoid a double-send; the sync flush owns telegram delivery.
        if transport in (None, "telegram"):
            return

        # Resolve send callback — fall back to FileOutputHandler when none registered.
        from agent.agent_session_queue import _resolve_callbacks  # noqa: PLC0415

        send_cb, _react_cb = _resolve_callbacks(project_key, transport)
        if send_cb is None:
            from agent.output_handler import FileOutputHandler  # noqa: PLC0415

            _fallback = FileOutputHandler()
            send_cb = _fallback.send

        chat_id = getattr(entry, "chat_id", None) or ""
        telegram_message_id = getattr(entry, "telegram_message_id", None) or 0

        await send_cb(chat_id, message, telegram_message_id, entry)

        # Best-effort telemetry counter.
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _R2  # noqa: PLC0415

            _R2.incr(f"{project_key}:session-health:deferred_self_draft_fallback_delivered")
        except Exception:
            pass

    except Exception as _err:
        logger.warning(
            "[session-health] _deliver_deferred_self_draft_fallback failed for %s: %s",
            getattr(entry, "session_id", "?"),
            _err,
        )


async def _apply_recovery_transition(
    entry: AgentSession,
    *,
    reason: str,
    reason_kind: str,
    handle: "SessionHandle | None",
    worker_key: str,
) -> bool:
    """Apply the standard ``running -> pending|abandoned|failed`` recovery transition.

    Shared between the main health-check loop (``_agent_session_health_check``)
    and the per-tool timeout sub-loop (``_agent_session_tool_timeout_loop``).
    Centralizing the transition prevents the "competing recovery functions
    racing" antipattern (see issue #1036) — both callers go through the same
    code path so MAX_RECOVERY_ATTEMPTS, the OOM defer, the response-delivered
    finalize-instead-of-recover guard, and the kill-switch all apply uniformly.

    ``reason_kind`` controls Tier 2 reprieve eligibility:
      * ``"no_progress"`` — full Tier 2 reprieve evaluation
        (compaction/children/alive gates).
      * ``"worker_dead"`` — skip Tier 2 reprieve; a dead worker cannot be
        reprieved by an "active children" signal.
      * ``"tool_timeout"`` — skip Tier 2 reprieve; the wedge condition itself
        is the evidence (issue #1270). A tool that has not returned within its
        tier budget is wedged regardless of whether the parent SDK subprocess
        is still alive.
      * ``"progress_deadline"`` — skip Tier 2 reprieve HERE; the caller (issue
        #1820 Fix #3's progress-deadline cancel scope in
        ``agent_session_queue.py``) already ran the shared reprieve gate
        (``_should_kill_no_progress``) itself before deciding to cancel and
        call this function — evaluating it a second time here would be a
        redundant, stale re-check (the caller's decision already stands).
        Always called with ``handle=None`` (Fix #3 owns its own cancel).

    Project-scoped Redis counter
    ``{project_key}:session-health:recoveries:{reason_kind}`` is incremented
    before the transition attempt. ``tool_timeout`` recoveries also increment
    ``{project_key}:session-health:tool_timeouts:{tier}`` from the caller, so
    the two namespaces stay distinct.

    Returns ``True`` if the transition fired (or finalize-instead-of-recover
    fired), ``False`` if a Tier 2 reprieve or kill-switch suppressed it.
    """
    # O1: observability counter — increment a project-scoped Redis counter
    # for dashboards. Failure must never block recovery.
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.incr(f"{entry.project_key}:session-health:recoveries:{reason_kind}")
    except Exception as _counter_err:
        logger.debug(
            "[session-health] recovery counter increment failed (non-fatal): %s",
            _counter_err,
        )

    def _reclaim_slot_lease() -> None:
        """Prompt slot reclaim (issue #1820, Fix #2) — called ONLY on the
        branches below that land ``entry``'s row TERMINAL (completed /
        abandoned / failed), never on the ``pending`` requeue branch.

        This is the wiring that makes acceptance criterion #1 (leaked-slot
        auto-recovery) fire on the health/tool-timeout cadence instead of
        waiting for the 300s reap-pass tick: an out-of-band kill (this
        function) may flip the DB row terminal while the owning worker
        loop's own ``finally`` release is stuck (e.g. a granite session
        whose PTY container thread never returns) — this reclaims the slot
        immediately. ``registry.reclaim()`` is idempotent, so it safely
        no-ops if the owning worker loop's normal release already fired
        first. Never raises into the caller — the reclaim is a self-heal,
        not a load-bearing part of the recovery transition.
        """
        try:
            registry = _session_state._slot_registry
            if registry is not None:
                registry.reclaim(entry.agent_session_id)
                from popoto.redis_db import POPOTO_REDIS_DB as _SR

                _SR.incr(f"{entry.project_key}:session-health:slot_reclaims")
        except Exception:
            logger.exception(
                "[session-health] slot lease reclaim failed for %s (non-fatal)",
                entry.agent_session_id,
            )

    # AC4 narrow telemetry counter (issue #1614): track recoveries that match
    # the zombie-uuid-no-output profile specifically (has claude_session_uuid,
    # but sdk_ever_output=False — the confirmed Branch 2 failure mode).
    # NOTE: sdk_ever_output is NOT a field on AgentSession; derive it from the
    # real fields last_tool_use_at and last_turn_at (same derivation as
    # _has_progress). Do NOT read the attribute directly from entry — use the
    # derived expression below instead.
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R2

        _sdk_ever_output = bool(
            getattr(entry, "last_tool_use_at", None) or getattr(entry, "last_turn_at", None)
        )
        if bool(getattr(entry, "claude_session_uuid", None)) and not _sdk_ever_output:
            project_key = getattr(entry, "project_key", "unknown")
            counter_key = f"{project_key}:session-health:recoveries:zombie_uuid_no_output"
            _R2.incr(counter_key)
            logger.info(
                "[session-health] zombie_uuid_no_output recovery: %s "
                "(claude_session_uuid set, sdk_ever_output=False)",
                getattr(entry, "agent_session_id", "?"),
            )
    except Exception:
        pass

    # Guard: if response was already delivered, finalize instead of recovering
    # to pending (prevents duplicate delivery, #918).
    if getattr(entry, "response_delivered_at", None) is not None:
        try:
            from models.session_lifecycle import (
                StatusConflictError,
                finalize_session,
            )

            logger.info(
                "[session-health] Session %s already delivered response at %s, "
                "finalizing instead of recovering",
                entry.agent_session_id,
                entry.response_delivered_at,
            )
            finalize_session(
                entry,
                "completed",
                reason="health check: already delivered",
            )
            _reclaim_slot_lease()  # row is now terminal (completed)
        except StatusConflictError as e:
            logger.info(
                "[session-health] Skipping finalize for already-delivered session %s: %s",
                entry.agent_session_id,
                e,
            )
        except Exception as e:
            logger.error(
                "[session-health] Failed to finalize already-delivered session %s: %s",
                entry.agent_session_id,
                e,
            )
        return True

    # === Tier 2 reprieve (no_progress only) ===
    if handle is None:
        logger.debug(
            "[session-health] No registry handle for %s; "
            "Tier 2 reprieve will only see compaction state",
            entry.agent_session_id,
        )
    if reason_kind == "no_progress":
        # Reprieve decision + telemetry live in exactly one place — the shared
        # predicate (issue #1820 OQ3, NO-LEGACY). This is the single
        # per-recovery-decision call, so emit_telemetry=True unconditionally
        # (same cadence as the pre-extraction inline block).
        if not _should_kill_no_progress(entry, handle, emit_telemetry=True):
            return False

    # All Tier 2 gates failed (or skipped). Respect kill-switch.
    if os.environ.get("DISABLE_PROGRESS_KILL") == "1":
        logger.warning(
            "[session-health] Would kill session %s (DISABLE_PROGRESS_KILL=1): %s",
            entry.agent_session_id,
            reason,
        )
        return False

    is_local = worker_key.startswith("local")
    # Capture tool_name once for use in advisory injection and degraded notice.
    tool_name = getattr(entry, "current_tool_name", None)
    logger.warning(
        "[session-health] Recovering session %s (chat=%s, session=%s, local=%s, kind=%s): %s",
        entry.agent_session_id,
        worker_key,
        entry.session_id,
        is_local,
        reason_kind,
        reason,
    )

    # Cancel-reason signal (#1877 defect #1). When THIS function owns the cancel
    # (handle present), predict the resume-ness of the outcome and write it BEFORE
    # cancelling, so the interrupt-message send (which fires during the cancel
    # await below) selects the right copy. `is_local` (abandoned) and the
    # exhausted-attempts ceiling (failed) are known here and are terminal ->
    # no_resume; otherwise the transition most likely re-queues to pending ->
    # resume. The subprocess-survived escalation to `failed` is only known after
    # the cancel; it re-stamps no_resume in its own branch below and degrades
    # safely to the resume copy (pre-#1877 behavior) if the send already fired.
    # When handle is None the caller (progress-deadline Fix #3) owns the cancel
    # and writes its own reason, so we skip here to avoid a wrong prediction.
    if handle is not None and handle.task is not None and not handle.task.done():
        from agent.cancel_reason import set_cancel_reason

        _predicted_terminal = (
            is_local or ((entry.recovery_attempts or 0) + 1) >= MAX_RECOVERY_ATTEMPTS
        )
        set_cancel_reason(entry.session_id, "no_resume" if _predicted_terminal else "resume")

    # Cancel the in-flight session task if we have a handle and the task
    # reference has been populated. Cancelling the populated task terminates
    # the SDK subprocess via CancelledError propagation, preventing orphan
    # heartbeats. (See plan spike-1, #1039 review.)
    if handle is not None and handle.task is not None and not handle.task.done():
        handle.task.cancel()
        try:
            await asyncio.wait_for(handle.task, timeout=TASK_CANCEL_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as _c_err:
            logger.debug(
                "[session-health] task cancel await raised %s for session %s",
                _c_err,
                entry.agent_session_id,
            )
        logger.info(
            "[session-health] Cancelled orphan task for session %s",
            entry.agent_session_id,
        )

    # Confirm the SDK subprocess actually exited (issue #1537). ``task.cancel()``
    # does not guarantee a hung ``claude -p`` exited; if it ignored cancellation
    # it becomes an orphan that no detector tracks once the session leaves
    # ``running``. Escalate SIGTERM -> SIGKILL against the recorded ``claude_pid``
    # and capture whether the process is confirmed gone. The requeue ``else``
    # branch below uses this to avoid silently parking an orphan at ``pending``.
    # ``_confirm_subprocess_dead`` is synchronous and may ``time.sleep`` for up to
    # ``SUBPROCESS_KILL_TIMEOUT`` while polling a signalled PID. Offload it to a
    # thread so the genuine-hang path never stalls the worker event loop (and every
    # other coroutine sharing it). The helper keeps its sync signature so its unit
    # tests stay unchanged.
    _kill_result = await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(
            _confirm_subprocess_dead,
            getattr(entry, "claude_pid", None),
            timeout=SUBPROCESS_KILL_TIMEOUT,
        ),
    )
    _subprocess_confirmed_dead = _kill_result.confirmed_dead
    if not _subprocess_confirmed_dead:
        _increment_subprocess_kill_counter(entry, escalated=False)
    elif _kill_result.signal_sent:
        # The subprocess survived task.cancel() and a SIGTERM/SIGKILL was actually
        # delivered to terminate it — a true escalation. The already-dead path
        # (cancel sufficed, signal_sent=False) is deliberately NOT counted.
        _increment_subprocess_kill_counter(entry, escalated=True)

    from models.session_lifecycle import (
        StatusConflictError,
        finalize_session,
        transition_status,
    )

    pre_bump_attempts = entry.recovery_attempts or 0
    entry.recovery_attempts = pre_bump_attempts + 1
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _MR

        _MR.incr(f"{entry.project_key}:session-health:kill_total")
    except Exception as _m_err:
        logger.debug("[session-health] kill counter failed: %s", _m_err)

    # Additive telemetry tap — no behavior change
    # Emit kill-enriched status_transition before the actual finalize/requeue.
    # Destination status is determined by the branches below; we emit one rich
    # event here so finalize_session() suppresses its plain duplicate via
    # emit_telemetry=False on every call in this recovery path.
    try:
        from agent.session_telemetry import record_telemetry_event as _rte

        _dest = (
            "abandoned"
            if is_local
            else (
                "failed"
                if entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS
                or not _subprocess_confirmed_dead
                else "pending"
            )
        )
        _rte(
            entry.session_id,
            {
                "type": "status_transition",
                "from": "running",
                "to": _dest,
                "reason": reason or "recovery",
                "kill": {
                    "confirmed_dead": _kill_result.confirmed_dead if _kill_result else False,
                    "signal_sent": _kill_result.signal_sent if _kill_result else False,
                    "pid": getattr(entry, "claude_pid", None),
                },
            },
        )
        # Reap the session's in-memory telemetry state when this recovery
        # transition is terminal. The lifecycle finalize call below runs with
        # emit_telemetry=False (the kill-enriched event above is the dedup
        # source), so the lifecycle reaper hook never fires on this path — reap
        # here instead. ``pending`` is a requeue (non-terminal): the session
        # keeps running, so its telemetry state must survive.
        if _dest in ("abandoned", "failed"):
            from agent.session_telemetry import finalize_session as _finalize_telemetry

            _finalize_telemetry(entry.session_id)
            # AC4 Seat B: reset the self-draft attempt counter on every
            # health-checker terminal finalize.  These callers pass
            # emit_telemetry=False, so the finalize_session Seat A reaper
            # (outside the emit_telemetry guard) would not fire here — this
            # seat closes that gap.  Best-effort: a Redis failure never blocks
            # the terminal transition.
            try:
                from agent.steering import reset_self_draft_attempts as _reset_attempts

                _reset_attempts(entry.session_id)
            except Exception:
                pass
    except Exception as _tel_err:
        logger.debug("[session-health] telemetry emit failed (non-fatal): %s", _tel_err)

    try:
        if is_local:
            # Deferred self-draft fallback: fire on abandoned path too (blocker 3).
            # The agent already stopped; deliver the deferred answer before closing.
            await _deliver_deferred_self_draft_fallback(entry)
            finalize_session(
                entry,
                "abandoned",
                reason=(
                    f"health check: local session showed no progress evidence "
                    f"(chat={worker_key}, attempts={entry.recovery_attempts}, kind={reason_kind})"
                ),
                skip_auto_tag=True,
                emit_telemetry=False,
            )
            _reclaim_slot_lease()  # row is now terminal (abandoned)
            logger.info(
                "[session-health] Marked local session %s as abandoned (chat=%s, attempts=%s)",
                entry.agent_session_id,
                worker_key,
                entry.recovery_attempts,
            )
        elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
            # Self-draft fallback takes precedence over the generic degraded notice.
            # Only send the generic notice when no deferred self-draft was pending.
            _has_deferred = (getattr(entry, "extra_context", None) or {}).get(
                "deferred_self_draft_pending"
            )
            await _deliver_deferred_self_draft_fallback(entry)
            if not _has_deferred and reason_kind == "tool_timeout":
                await _deliver_tool_timeout_degraded_notice(entry, tool_name)
            finalize_session(
                entry,
                "failed",
                reason=(
                    f"health check: {entry.recovery_attempts} recovery "
                    f"attempts, never progressed (kind={reason_kind})"
                ),
                emit_telemetry=False,
            )
            _reclaim_slot_lease()  # row is now terminal (failed, MAX_RECOVERY_ATTEMPTS)
            logger.warning(
                "[session-health] Finalized session %s as failed after %s recovery attempts",
                entry.agent_session_id,
                entry.recovery_attempts,
            )
        elif not _subprocess_confirmed_dead:
            # Issue #1537: the recorded subprocess survived cancel + SIGTERM +
            # SIGKILL (or could not be confirmed dead). Requeuing to ``pending``
            # would park a live orphan that no detector tracks — the exact defect
            # that wedged the worker for 25.5h on 2026-05-31. Escalate to the
            # ``failed`` terminal status so the in-process orphan reaper
            # (_TERMINAL_STATUSES) owns cleanup. Do NOT null ``started_at`` into
            # a pending record.
            # Cancel-reason re-stamp (#1877 defect #1): this escalation to the
            # terminal `failed` status was NOT predictable before the cancel
            # above (it depends on the post-cancel subprocess-confirmation), so
            # the pre-cancel prediction may have written `resume`. Correct it to
            # `no_resume`; if the interrupt send already fired it degrades safely
            # to the resume copy (documented acceptable degradation).
            from agent.cancel_reason import set_cancel_reason

            set_cancel_reason(entry.session_id, "no_resume")
            _has_deferred = (getattr(entry, "extra_context", None) or {}).get(
                "deferred_self_draft_pending"
            )
            await _deliver_deferred_self_draft_fallback(entry)
            if not _has_deferred and reason_kind == "tool_timeout":
                await _deliver_tool_timeout_degraded_notice(entry, tool_name)
            finalize_session(
                entry,
                "failed",
                reason=(
                    f"health check: subprocess {getattr(entry, 'claude_pid', None)} "
                    f"survived cancel+SIGTERM+SIGKILL; escalating to failed so the "
                    f"orphan reaper owns cleanup (chat={worker_key}, "
                    f"attempt {entry.recovery_attempts}, kind={reason_kind})"
                ),
                emit_telemetry=False,
            )
            _reclaim_slot_lease()  # row is now terminal (failed, subprocess not confirmed dead)
            logger.warning(
                "[session-health] Escalated session %s to failed — subprocess "
                "pid=%s not confirmed dead after cancel+SIGTERM+SIGKILL "
                "(chat=%s, attempt %s, kind=%s)",
                entry.agent_session_id,
                getattr(entry, "claude_pid", None),
                worker_key,
                entry.recovery_attempts,
                reason_kind,
            )
        else:
            entry.priority = "high"
            entry.started_at = None
            # Advisory injection (issue #1711): only on the requeue (``else``) branch,
            # only for ``tool_timeout`` reason kind. Explanation of each constraint:
            #   • Requeue-branch-only: the ``failed`` and ``abandoned`` branches
            #     finalize the session — there is no next turn to consume steering.
            #     Advisory steering is only useful when the session will run again.
            #   • tool_timeout-only: steering is narrowly targeted at the "model
            #     attempted a specific tool and it wedged" failure mode. Other reason
            #     kinds (no_progress, worker_dead) have different root causes and
            #     different remediation patterns; injecting a tool-skip message for
            #     them would be misleading and could mask the real issue.
            if reason_kind == "tool_timeout" and tool_name:
                try:
                    from agent.steering import push_steering_message as _push_steering_message

                    _push_steering_message(
                        entry.session_id,
                        _compose_tool_timeout_steering(
                            tool_name, getattr(entry, "message_text", None)
                        ),
                        "session-health",
                        front=True,
                    )
                    try:
                        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

                        _R.incr(
                            f"{entry.project_key}:session-health:tool_timeout_steering_injected"
                        )
                    except Exception:
                        pass
                except Exception as _steer_err:
                    logger.warning(
                        "[session-health] Failed to inject tool_timeout steering for %s: %s",
                        entry.agent_session_id,
                        _steer_err,
                    )
            # Clear durable wedge fields on tool_timeout requeue only (same gate
            # as the steering injection above) so the stale signal does not
            # re-trip _check_tool_timeout before the resumed session takes its
            # first new turn.  Each resume generates a fresh UUID/transcript
            # (bridge_adapter.py:993-995) so the diff-gated tailer has no
            # tool_use block to re-pin from; once cleared, _check_tool_timeout
            # returns None until a genuinely new tool_use arrives.
            # Both fields must be cleared together: a fresh tool name paired with
            # the frozen last_tool_use_at could still re-trip the budget check.
            # See issue #1762.
            if reason_kind == "tool_timeout":
                entry.current_tool_name = None
                entry.last_tool_use_at = None
            if (
                getattr(entry, "exit_returncode", None) == -9
                and pre_bump_attempts == 0
                and _is_memory_tight()
            ):
                entry.scheduled_at = datetime.now(tz=UTC) + timedelta(seconds=120)
                try:
                    _oom_fields = ["scheduled_at", "recovery_attempts"]
                    if reason_kind == "tool_timeout":
                        _oom_fields += ["current_tool_name", "last_tool_use_at"]
                    entry.save(update_fields=_oom_fields)
                except Exception as _sa_err:
                    logger.debug(
                        "[session-health] scheduled_at save failed: %s",
                        _sa_err,
                    )
                logger.warning(
                    "[session-health] OOM backoff: deferring %s for 120s "
                    "(exit_returncode=-9, recovery_attempts now=%d, "
                    "memory<400MB)",
                    entry.agent_session_id,
                    entry.recovery_attempts,
                )
            else:
                try:
                    _requeue_fields = ["recovery_attempts"]
                    if reason_kind == "tool_timeout":
                        _requeue_fields += ["current_tool_name", "last_tool_use_at"]
                    entry.save(update_fields=_requeue_fields)
                except Exception as _ra_err:
                    logger.debug(
                        "[session-health] recovery_attempts save failed: %s",
                        _ra_err,
                    )
            transition_status(
                entry,
                "pending",
                reason=(
                    f"health check: recovered session "
                    f"(chat={worker_key}, attempt {entry.recovery_attempts}, kind={reason_kind})"
                ),
                emit_telemetry=False,
            )
            logger.info(
                "[session-health] Recovered session %s (chat=%s, attempt %s, kind=%s)",
                entry.agent_session_id,
                worker_key,
                entry.recovery_attempts,
                reason_kind,
            )
            from agent.agent_session_queue import _ensure_worker  # noqa: PLC0415

            _ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)
            event = _active_events.get(worker_key)
            if event is not None:
                event.set()
    except StatusConflictError as _sc_err:
        # Expected: kill-is-terminal guard (#1208). Session was already terminal
        # when recovery tried to mark it abandoned/failed. Log at INFO.
        logger.info(
            "[session-health] Skipping recovery finalize for %s: %s",
            entry.agent_session_id,
            _sc_err,
        )
    return True


def _reap_slot_leases() -> None:
    """Top-of-tick slot-lease reap pass (issue #1820, Fix #2 — the reclaim
    half of the ownerless-semaphore leak fix).

    Hoisted OUT of the per-entry PENDING-session loop where the old
    logging-only leaked-slot fingerprint used to live (nested inside
    ``for entry in pending_sessions:``, gated on ``worker_alive``) into a
    SINGLE pass, called once per health-check tick from the TOP of
    ``_agent_session_health_check`` — independent of ``worker_alive``, of
    whether there is any pending session at all, and (for phase 1) of the
    kill-switch. A literal in-place edit of the old block would have run the
    reap N-times-per-tick and skipped it entirely on a drained queue; this
    hoisted single pass fires even with zero pending sessions, which is
    exactly the parked-worker starvation case Acceptance #1 targets.

    Two phases. Only phase 2 is gated on the kill-switch:

      Phase 1 (detection) — ALWAYS runs, even when
      ``SLOT_LEASE_REAP_DISABLED=1``. Computes and logs the leaked-slot
      fingerprint (WARNING iff ``permits_free==0 AND running_count<max``;
      INFO iff ``permits_free==0 AND running_count>=max`` — healthy
      backpressure) plus a heartbeat. This replaces the deleted block's
      detect-and-log role wholesale, so the kill-switch degrades to
      detect-only — never to no-visibility (Operator CONCERN).

      Phase 2 (reclaim) — gated on
      ``os.environ.get("SLOT_LEASE_REAP_DISABLED") != "1"``. For each lease
      in a SNAPSHOT of ``registry.leases()`` (mutation-during-iteration
      safe), re-reads the owner's DB status fresh (terminal-status-guarded,
      same pattern as the tool-timeout loop) and, iff the owner is terminal
      (or its record no longer exists), calls ``registry.reclaim(owner)``
      and increments the project-scoped ``slot_reclaims`` counter.
      **Terminal-owner only — there is deliberately no wall-clock elapsed-time
      reclaim arm** (see agent/slot_lease.py's "no reclaim deadline" note):
      reclaiming a still-running, progressing owner would
      strip its permit mid-execution, allowing semaphore over-admission
      (concurrently-running sessions > max) and re-imposing exactly the
      wall-clock duration cap issue #1172 removed.

    Never raises into the health check — a single bad lease is logged and
    the pass continues; the whole function is exception-wrapped.
    """
    try:
        registry = _session_state._slot_registry
        if registry is None:
            return  # No ceiling configured (pre-init / unlimited-mode tests).

        leases_snapshot = list(registry.leases())
        reap_disabled = os.environ.get("SLOT_LEASE_REAP_DISABLED") == "1"

        # === Phase 1: detection — ALWAYS runs (Operator CONCERN) ===
        try:
            _permits_free = registry.permits_free()
            _max_sessions = max(1, int(os.environ.get("MAX_CONCURRENT_SESSIONS", "8")))
            _running_count = len(list(AgentSession.query.filter(status="running")))
            if _permits_free == 0 and _running_count < _max_sessions:
                logger.warning(
                    "[session-health] SLOT-LEASE FINGERPRINT: leases_held=%d, "
                    "permits_free=0 AND running_count=%d < max_sessions=%d. "
                    "Slot(s) held by non-running session(s) (#1537 class). "
                    "reap_disabled=%s. See docs/features/slot-lease-ownership.md.",
                    len(leases_snapshot),
                    _running_count,
                    _max_sessions,
                    reap_disabled,
                )
            elif _permits_free == 0:
                logger.info(
                    "[session-health] leases_held=%d, permits_free=0, "
                    "running_count=%d >= max_sessions=%d (healthy backpressure; "
                    "no leak signal).",
                    len(leases_snapshot),
                    _running_count,
                    _max_sessions,
                )
            else:
                # Zero-reclaim heartbeat — proves the reap pass is alive even
                # when there is nothing to report.
                logger.debug(
                    "[session-health] slot-lease heartbeat: leases_held=%d, "
                    "permits_free=%d, running_count=%d, max_sessions=%d, "
                    "reap_disabled=%s",
                    len(leases_snapshot),
                    _permits_free,
                    _running_count,
                    _max_sessions,
                    reap_disabled,
                )
        except Exception:
            logger.exception("[session-health] slot-lease detection phase failed")

        # === Fix #5 (#1821): publish the lease snapshot (always-run region) ===
        # A single atomic SET of the complete JSON blob so the bridge always
        # reads a self-consistent snapshot (Race 1). Fail-quiet.
        _publish_slot_leases(registry, leases_snapshot)

        # === Fix #5 (#1821): drain bridge-pushed reclaim-requests ===
        # MUST sit in the ALWAYS-RUN region — after Phase 1 detection, BEFORE the
        # Phase-2 `if reap_disabled: return` gate below — so the drain still fires
        # under SLOT_LEASE_REAP_DISABLED=1, where it is the ONLY reclaim lever (the
        # autonomous Phase-2 reclaim is gated off). Placing it in/after the Phase-2
        # loop would silently defeat the feature's headline capability (concern #5).
        _drain_reclaim_requests(registry, leases_snapshot)

        # === Phase 2: reclaim — gated on SLOT_LEASE_REAP_DISABLED ===
        if reap_disabled:
            return

        for lease in leases_snapshot:
            try:
                fresh = AgentSession.get_by_id(lease.owner_session_id)
                # A record that no longer exists is at least as terminal as
                # one whose status field says so — reclaim either way.
                if fresh is None or getattr(fresh, "status", None) in _TERMINAL_STATUSES:
                    registry.reclaim(lease.owner_session_id)
                    _project_key = getattr(fresh, "project_key", None) or "unknown"
                    try:
                        from popoto.redis_db import POPOTO_REDIS_DB as _SR

                        _SR.incr(f"{_project_key}:session-health:slot_reclaims")
                    except Exception:
                        logger.debug(
                            "[session-health] slot_reclaims counter increment failed "
                            "(non-fatal) for owner=%s",
                            lease.owner_session_id,
                        )
            except Exception:
                logger.warning(
                    "[session-health] slot-lease reap failed for owner=%s (non-fatal)",
                    lease.owner_session_id,
                    exc_info=True,
                )
    except Exception:
        logger.exception("[session-health] _reap_slot_leases failed (non-fatal)")


def _publish_slot_leases(registry, leases_snapshot) -> None:
    """Publish the current lease snapshot to Redis (Fix #5, #1821).

    Written as ONE atomic ``SET`` of a complete JSON blob so the bridge always
    reads a self-consistent snapshot (Race 1 — never a partial, field-by-field
    write). ``acquired_at`` is a wall-clock ``time.time()`` value (agent/slot_lease.py
    Lease), so it maps straight onto ``acquired_at_wall_ts`` with no conversion.
    Fail-quiet: a Redis error must never raise into the reap pass.
    """
    try:
        try:
            permits_free = registry.permits_free()
        except Exception:
            permits_free = None
        held = len(leases_snapshot)
        payload = {
            "permits_free": permits_free,
            "held": held,
            "max": getattr(registry, "_max_concurrent", None),
            "ts": time.time(),
            "owners": [
                {
                    "owner_session_id": lease.owner_session_id,
                    "acquired_at_wall_ts": lease.acquired_at,
                }
                for lease in leases_snapshot
            ],
        }
        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

        _R.set(
            f"{WORKER_SLOT_LEASES_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}",
            json.dumps(payload),
            ex=WORKER_SLOT_KEY_TTL_SECONDS,
        )
    except Exception as e:
        logger.debug("[session-health] slot-lease snapshot publish failed (non-fatal): %s", e)


def _drain_reclaim_requests(registry, leases_snapshot) -> None:
    """Drain bridge-pushed reclaim-requests and reclaim genuinely-terminal owners.

    The out-of-domain half of Fix #5 (#1821): the bridge's
    ``check_worker_liveness_and_slots`` pushes owner ids onto
    ``worker:slot:reclaim_requests:{host}`` when it observes a terminal-owner
    lease under a live loop; this drain (running on the worker loop, where the
    semaphore actually lives) pops each request and performs the reclaim.

    This is a DISTINCT code path from #1820's autonomous Phase-2 reclaim, so it
    fires even when ``SLOT_LEASE_REAP_DISABLED=1`` gates the autonomous path off —
    the whole reason the reclaim-request lever exists.

    **#1868 trap (concern #2) — DELIBERATE divergence from the autonomous reaper.**
    The autonomous Phase-2 reclaim treats ``get_by_id → None`` as terminal. This
    request-driven drain MUST NOT: a transient Redis lookup failure returning
    ``None`` (or any lookup exception) is "unknown → SKIP, do not reclaim", because
    reclaiming on a blip would strip a LIVE session's permit (semaphore
    over-admission). Reclaim ONLY on an EXPLICIT terminal ``status``.

    Fail-quiet throughout; every swallow logs.
    """
    key = f"{WORKER_SLOT_RECLAIM_REQUESTS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}"
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415
    except Exception as e:
        logger.debug("[session-health] reclaim-request drain: redis unavailable: %s", e)
        return

    drained = 0
    try:
        # Atomic LPOP loop — bounded by RECLAIM_REQUESTS_MAX to avoid an
        # unbounded spin if the bridge is flooding the list faster than we drain.
        for _ in range(RECLAIM_REQUESTS_MAX + 1):
            owner = _R.lpop(key)
            if owner is None:
                break
            if isinstance(owner, bytes):
                owner = owner.decode("utf-8", "replace")
            drained += 1
            try:
                fresh = AgentSession.get_by_id(owner)
            except Exception:
                # Transient lookup failure → unknown → SKIP (concern #2, #1868).
                # Do NOT reclaim; a future tick re-evaluates if the bridge re-pushes.
                logger.debug(
                    "[session-health] reclaim-request drain: lookup failed for owner=%s "
                    "(unknown → skip, not reclaiming)",
                    owner,
                )
                continue
            if fresh is None:
                # #1868: None is "unknown", NOT terminal, for the request-driven
                # drain — a deliberate divergence from the autonomous reaper.
                logger.debug(
                    "[session-health] reclaim-request drain: owner=%s not found "
                    "(unknown → skip, not reclaiming)",
                    owner,
                )
                continue
            status = getattr(fresh, "status", None)
            if status in _TERMINAL_STATUSES:
                registry.reclaim(owner)
                project_key = getattr(fresh, "project_key", None) or "unknown"
                logger.warning(
                    "[session-health] bridge-requested reclaim: freed leaked slot for "
                    "terminal owner=%s (status=%s, project=%s)",
                    owner,
                    status,
                    project_key,
                )
                try:
                    _R.incr(f"{project_key}:session-health:bridge_reclaims")
                except Exception as e:
                    logger.debug(
                        "[session-health] bridge_reclaims counter increment failed "
                        "(non-fatal) for owner=%s: %s",
                        owner,
                        e,
                    )
            else:
                # Requested owner is still live (Risk 3) — never strip its permit.
                logger.debug(
                    "[session-health] reclaim-request drain: owner=%s status=%s not "
                    "terminal → skip (no reclaim)",
                    owner,
                    status,
                )
    except Exception as e:
        logger.warning(
            "[session-health] reclaim-request drain failed (non-fatal): %s", e, exc_info=True
        )

    _maybe_emit_bridge_contract_stale(drained, leases_snapshot)


def _maybe_emit_bridge_contract_stale(drained: int, leases_snapshot) -> None:
    """Emit ``bridge_contract_stale`` when a terminal-owner leak is observed but no
    reclaim-request has been drained for a sustained window (concern #5, #1821).

    Detects the new-worker / old-bridge direction: the worker keeps ONE Redis
    timestamp (``worker:slot:last_reclaim_request_drain:{host}``, set whenever the
    drain pops ≥1 request). On a tick where a terminal-owner leak IS present but
    ``now − last_drain_ts > BRIDGE_WORKER_BEACON_STALE_S`` (the REUSED beacon
    threshold — no new staleness var), emit ``bridge_contract_stale`` once (dedup
    ``SET NX EX``) so the contract gap is operator-visible rather than a silent drop.

    Fail-quiet — an observability signal must never raise into the reap pass.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

        host = _ORPHAN_REAP_HOSTNAME
        last_drain_key = f"{WORKER_SLOT_LAST_RECLAIM_DRAIN_KEY_PREFIX}{host}"
        now = time.time()

        if drained > 0:
            # We drained a request → the contract is live; record the timestamp.
            _R.set(last_drain_key, str(now), ex=WORKER_SLOT_KEY_TTL_SECONDS)
            return

        # No request drained this tick. Only interesting if a terminal-owner leak
        # actually exists (else there is nothing for the bridge to have requested).
        terminal_owner_present = False
        for lease in leases_snapshot:
            try:
                fresh = AgentSession.get_by_id(lease.owner_session_id)
            except Exception:
                continue
            if fresh is not None and getattr(fresh, "status", None) in _TERMINAL_STATUSES:
                terminal_owner_present = True
                break
        if not terminal_owner_present:
            return

        raw = _R.get(last_drain_key)
        last_drain_ts = None
        if raw is not None:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                last_drain_ts = float(raw)
            except (TypeError, ValueError):
                last_drain_ts = None

        stale = last_drain_ts is None or (now - last_drain_ts) > BRIDGE_WORKER_BEACON_STALE_S
        if not stale:
            return

        # Dedup so a persistent gap logs once per stale window, not every tick.
        dedup_key = f"worker:slot:bridge_contract_stale_applied:{host}"
        if not _R.set(dedup_key, "1", nx=True, ex=BRIDGE_WORKER_BEACON_STALE_S):
            return

        logger.warning(
            "[session-health] bridge_contract_stale: terminal-owner lease present but no "
            "reclaim-request drained for >%ss — bridge reclaim-request channel may be "
            "absent (new-worker/old-bridge). Autonomous reap still covers the leak.",
            BRIDGE_WORKER_BEACON_STALE_S,
        )
        _append_watchdog_action(
            _R,
            host,
            {"action": "bridge_contract_stale", "ts": now},
        )
        try:
            _R.incr(f"{host}:worker-watchdog:bridge_contract_stale")
        except Exception as e:
            logger.debug("[session-health] bridge_contract_stale counter increment failed: %s", e)
    except Exception as e:
        logger.debug("[session-health] bridge_contract_stale check failed (non-fatal): %s", e)


def _append_watchdog_action(redis_client, host: str, entry: dict) -> None:
    """Append a capped entry to the ``worker:watchdog:actions:{host}`` operator log.

    Capped LPUSH + LTRIM (newest first, bounded at WORKER_WATCHDOG_ACTIONS_MAX)
    + TTL, mirroring the existing action-log bound. Fail-quiet.
    """
    try:
        actions_key = f"{WORKER_WATCHDOG_ACTIONS_KEY_PREFIX}{host}"
        redis_client.lpush(actions_key, json.dumps(entry))
        redis_client.ltrim(actions_key, 0, WORKER_WATCHDOG_ACTIONS_MAX - 1)
        redis_client.expire(actions_key, WORKER_SLOT_KEY_TTL_SECONDS)
    except Exception as e:
        logger.debug("[session-health] watchdog-action append failed (non-fatal): %s", e)


async def _agent_session_health_check() -> None:
    """Health check for worker-managed sessions (running and pending).

    Other non-terminal statuses (active, dormant, paused, paused_circuit) are
    monitored by the bridge-hosted watchdog in monitoring/session_watchdog.py.
    See RECOVERY_OWNERSHIP in models/session_lifecycle.py for the full coverage map.

    Scans both 'running' and 'pending' sessions:

    For RUNNING sessions:
    1. If worker is dead/missing AND running > AGENT_SESSION_HEALTH_MIN_RUNNING: recover
       (``reason_kind="worker_dead"``).
    2. **NARROWED (issue #1820 Fix #3 + OQ3 + BLOCKER r6):** if worker appears alive
       but there is NO live in-scope handle for this session
       (``_active_sessions.get(entry.agent_session_id) is None``) AND running
       > AGENT_SESSION_HEALTH_MIN_RUNNING AND ``_has_progress(entry)`` is False:
       evaluate Tier 2 reprieve gates (via the shared ``_should_kill_no_progress``
       predicate) and recover only if every gate also fails
       (``reason_kind="no_progress"``). This is the #944 shared-``worker_key``
       orphan net — a row left ``running`` by a crashed worker whose
       ``worker_key`` was later reused by a respawned LIVE worker reads
       ``worker_alive=True`` even though no live task executes it. A
       worker-alive session WITH a live in-scope handle is instead owned by
       the progress-deadline cancel scope in ``agent_session_queue.py``
       (Fix #3) — the two are disjoint by construction on the in-scope-handle
       test, so no running session has two killers.
    3. Legacy sessions without started_at and no worker: recover.

    For PENDING sessions:
    4. If no live worker for session.chat_id AND pending > AGENT_SESSION_HEALTH_MIN_RUNNING:
       start a worker. This replaces the old _recover_stalled_pending mechanism.

    **Delivery guard (#918):** Before recovering a running session to pending,
    the health check inspects ``response_delivered_at``. If the field is set,
    the session already delivered its final response to Telegram — re-queuing
    would cause a duplicate reply. Instead, the session is finalized as
    ``completed`` via ``finalize_session()``. This prevents the crash-recover
    loop that previously produced 6+ duplicate messages per session.

    **No wall-clock timeout (#1172):** the per-session
    ``_get_agent_session_timeout`` cap was retired. A session writing fresh
    heartbeats is allowed to run as long as it needs. Cost monitoring
    (``AgentSession.total_cost_usd``) is the long-run backstop for genuinely
    runaway sessions; ``worker_dead`` and Mode 4 OOM defer (#1099) remain the
    evidence-based kill paths.

    Recovery resets status to 'pending' via direct mutation and save.
    Status is an IndexedField, so no delete-and-recreate is needed.
    Only sessions whose worker is confirmed dead are touched.

    **Orphan subprocess reap (#1218):** The two forward scans (RUNNING / PENDING)
    look at AgentSession rows and ask "is the worker still alive?". The orphan
    reap pass runs at the END of this function and asks the inverse question:
    for each subprocess in ``_active_sessions``, is the owning AgentSession row
    in ``_TERMINAL_STATUSES``? If yes (and outside the grace window), SIGTERM
    the PID, push it onto ``_pending_sigkill`` for next-tick SIGKILL escalation,
    and pop the handle. Drains ``_pending_sigkill`` first (single-shot clear)
    so PIDs never persist across more than one tick.
    """
    now = time.time()
    checked = 0
    recovered = 0
    workers_started = 0

    # === Slot-lease reap pass (issue #1820, Fix #2) ===
    # Single top-of-tick pass, independent of worker_alive and of whether
    # there are any pending sessions — replaces the deleted logging-only
    # fingerprint that used to be nested inside the PENDING-session loop
    # below (gated on worker_alive, re-run per pending entry). See
    # _reap_slot_leases()'s docstring for the two-phase (detect-always,
    # reclaim-gated) design.
    _reap_slot_leases()

    # === SIGKILL escalation drain (issue #1218) ===
    # Snapshot-then-clear: PIDs added to _pending_sigkill on the previous tick
    # are escalated to SIGKILL exactly once, then unconditionally discarded.
    # macOS recycles PIDs in ~5 minutes; persisting entries across multiple
    # ticks risks SIGKILLing an unrelated new process.
    _pending_sigkill_snapshot = list(_pending_sigkill)
    _pending_sigkill.clear()
    for _pid in _pending_sigkill_snapshot:
        try:
            os.kill(_pid, signal.SIGKILL)
            logger.warning(
                "[session-health] SIGKILL escalation for orphan subprocess pid=%s",
                _pid,
            )
        except ProcessLookupError:
            # Already dead between SIGTERM and this drain — expected, silent.
            pass
        except PermissionError as _perm_err:
            logger.warning(
                "[session-health] SIGKILL permission denied for pid=%s: %s",
                _pid,
                _perm_err,
            )
        except Exception as _kill_err:
            logger.debug("[session-health] SIGKILL failed for pid=%s: %s", _pid, _kill_err)

    # === Check RUNNING sessions_list ===
    # Phantom guard: drop records whose fields are still Popoto Field descriptors
    # (orphan $IndexF members). MUST run before the terminal-status guard below:
    # getattr(entry, "status", None) on a phantom returns a Field descriptor,
    # which would slip past `actual_status in _TERMINAL_STATUSES` (descriptors
    # are not in the terminal-status set) and reach the destructive recovery
    # path.
    running_sessions = _filter_hydrated_sessions(AgentSession.query.filter(status="running"))
    for entry in running_sessions:
        checked += 1

        # Terminal-status guard (#1006): skip sessions whose hash status is
        # terminal but still appear in the running index due to stale
        # IndexedField entries. Without this, killed/completed sessions get
        # re-promoted to pending in an infinite resurrection cycle.
        actual_status = getattr(entry, "status", None)
        if actual_status in _TERMINAL_STATUSES:
            logger.warning(
                "[session-health] Skipping terminal session %s "
                "(hash status=%s, stale running index entry — zombie #1006)",
                entry.agent_session_id,
                actual_status,
            )
            continue

        # Delivery guard: if response was already delivered, finalize immediately
        # without going through worker_alive/_has_progress evaluation. turn_count
        # and claude_session_uuid are sticky fields that block the no_progress
        # recovery path while the heartbeat is fresh (gated on
        # NO_OUTPUT_BUDGET_SECONDS since #1614 — no longer permanent), so
        # sessions that delivered but failed to finalize would otherwise stay
        # stuck as "running" until the heartbeat goes stale.
        if getattr(entry, "response_delivered_at", None) is not None:
            try:
                from models.session_lifecycle import StatusConflictError, finalize_session

                logger.info(
                    "[session-health] Session %s already delivered response at %s, "
                    "finalizing stuck running session",
                    entry.agent_session_id,
                    entry.response_delivered_at,
                )
                finalize_session(
                    entry, "completed", reason="health check: delivered but not finalized"
                )
                recovered += 1
            except StatusConflictError as e:
                logger.info(
                    "[session-health] Skipping finalize for already-delivered session %s: %s",
                    entry.agent_session_id,
                    e,
                )
            except Exception as e:
                logger.error(
                    "[session-health] Failed to finalize already-delivered session %s: %s",
                    entry.agent_session_id,
                    e,
                )
            continue

        try:
            worker_key = entry.worker_key
            worker = _active_workers.get(worker_key)
            worker_alive = worker is not None and not worker.done()
            # Computed once, alongside worker_alive (issue #1820 OQ3 + BLOCKER
            # r6): whether the CURRENT worker loop holds a live in-scope
            # handle for THIS session. The progress-deadline cancel scope
            # (Fix #3, agent_session_queue.py) is the authoritative
            # no-progress killer for exactly the sessions where this is
            # non-None — the narrowed elif below owns the disjoint residual
            # (a worker-alive row with NO live in-scope handle: the #944
            # shared-worker_key orphan, e.g. a crashed-then-respawned
            # worker_key).
            in_scope_handle = _active_sessions.get(entry.agent_session_id)

            started_ts = _ts(getattr(entry, "started_at", None))
            running_seconds = (now - started_ts) if started_ts else None

            should_recover = False
            reason = ""
            _reason_kind: str | None = None

            if not worker_alive:
                if started_ts is None:
                    should_recover = True
                    _reason_kind = "worker_dead"
                    reason = "worker dead/missing, no started_at (legacy session)"
                elif (
                    running_seconds is not None
                    and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING
                ):
                    should_recover = True
                    _reason_kind = "worker_dead"
                    reason = (
                        f"worker dead/missing, running for "
                        f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard)"
                    )
                else:
                    logger.debug(
                        "[session-health] Skipping session %s - worker dead but "
                        "running only %ss (under %ss guard)",
                        entry.agent_session_id,
                        int(running_seconds) if running_seconds else "?",
                        AGENT_SESSION_HEALTH_MIN_RUNNING,
                    )
            # NARROWED (issue #1820 OQ3 + BLOCKER r6): this elif is now
            # disjoint-by-construction from Fix #3's in-scope progress-deadline
            # watcher, which owns worker-alive sessions the current worker
            # loop is actively executing (in_scope_handle is not None). This
            # elif retains ONLY the #944 shared-worker_key orphan net: PM and
            # a project-keyed dev-without-slug share a worker_key, so a row
            # left `running` by a crashed worker whose worker_key was later
            # reused by a respawned LIVE worker reads worker_alive=True even
            # though no live task is executing it — no in-scope handle exists.
            # Fix #3 cannot reach this orphan (it only watches the session its
            # own owned task is executing), so this net must NOT be deleted —
            # only narrowed to the case Fix #3 provably cannot cover.
            elif (
                in_scope_handle is None
                and running_seconds is not None
                and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING
                and not _has_progress(entry)
            ):
                should_recover = True
                _reason_kind = "no_progress"
                reason = (
                    f"no progress signal, orphaned running row (no in-scope handle, #944), "
                    f"{int(running_seconds)}s "
                    f"(>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard, worker future not yet resolved, "
                    f"turn_count={entry.turn_count}, log_path={entry.log_path!r}, "
                    f"claude_session_uuid={entry.claude_session_uuid!r})"
                )

            if should_recover:
                # Delegate to shared recovery helper (issue #1270). Both this
                # loop and `_agent_session_tool_timeout_loop` go through the
                # same code path so MAX_RECOVERY_ATTEMPTS, the OOM defer, the
                # response-delivered finalize-instead-of-recover guard, and
                # the kill-switch all apply uniformly.
                if await _apply_recovery_transition(
                    entry,
                    reason=reason,
                    reason_kind=_reason_kind,
                    handle=in_scope_handle,
                    worker_key=worker_key,
                ):
                    recovered += 1
        except Exception:
            logger.exception(
                "[session-health] Error processing session %s",
                getattr(entry, "agent_session_id", "unknown"),
            )

    # === Check PENDING sessions_list ===
    pending_sessions = list(AgentSession.query.filter(status="pending"))
    for entry in pending_sessions:
        checked += 1
        try:
            worker_key = entry.worker_key
            worker = _active_workers.get(worker_key)
            worker_alive = worker is not None and not worker.done()

            if worker_alive:
                # The leaked-slot fingerprint that used to be logged HERE
                # (nested in this loop, gated on worker_alive, re-run once
                # per pending entry) is now a single top-of-tick pass —
                # see _reap_slot_leases(), called once at the start of
                # _agent_session_health_check (issue #1820, Fix #2). It
                # detects AND reclaims; this branch stays nudge-only.

                # Worker exists — nudge its event in case it missed the original
                # notify (e.g. startup-recovery race: session put to pending before
                # the worker loop subscribed to its event).
                event = _active_events.get(worker_key)
                if event is not None:
                    event.set()
                continue

            # No live worker — check age threshold before starting one
            created_ts = _ts(getattr(entry, "created_at", None))
            if created_ts is None:
                continue
            pending_seconds = now - created_ts
            if pending_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING:
                if worker_key.startswith("local"):
                    # Local CLI sessions can't be resumed by bridge workers
                    logger.info(
                        "[session-health] Marking orphaned local pending session %s "
                        "as abandoned (chat=%s, pending %.0fs)",
                        entry.agent_session_id,
                        worker_key,
                        pending_seconds,
                    )
                    from models.session_lifecycle import (
                        StatusConflictError,
                        finalize_session,
                    )

                    try:
                        finalize_session(
                            entry,
                            "abandoned",
                            reason=(
                                f"health check: orphaned local pending session (chat={worker_key})"
                            ),
                            skip_auto_tag=True,
                        )
                    except StatusConflictError as _sc_err:
                        # Session is already terminal (kill-is-terminal #1208).
                        # Skip silently at INFO; nothing more to do.
                        logger.info(
                            "[session-health] Skipping abandon for orphaned local "
                            "pending session %s: %s",
                            entry.agent_session_id,
                            _sc_err,
                        )
                else:
                    logger.info(
                        "[session-health] Starting worker for orphaned pending "
                        "session %s (chat=%s, pending %.0fs)",
                        entry.agent_session_id,
                        worker_key,
                        pending_seconds,
                    )
                    from agent.agent_session_queue import _ensure_worker  # noqa: PLC0415

                    _ensure_worker(worker_key, is_project_keyed=entry.is_project_keyed)
                workers_started += 1
        except Exception:
            logger.exception(
                "[session-health] Error processing pending session %s",
                getattr(entry, "agent_session_id", "unknown"),
            )

    if checked > 0:
        logger.info(
            "[session-health] Health check: %d checked, %d recovered, %d workers started",
            checked,
            recovered,
            workers_started,
        )

    # === Orphan subprocess reap pass (issue #1218) ===
    # Inverse-direction scan: iterate _active_sessions and reap any handle
    # whose corresponding AgentSession row is terminal. Snapshot via list()
    # to avoid mutation-during-iteration if another coroutine pops a handle.
    #
    # An optional kill-switch is available via the env flag DISABLE_ORPHAN_REAP=1
    # (parity with DISABLE_PROGRESS_KILL); enabled by default.
    if os.environ.get("DISABLE_ORPHAN_REAP") == "1":
        return

    for _session_id, _handle in list(_active_sessions.items()):
        try:
            # Use AgentSession.get_by_id (the canonical pattern in
            # agent_session_queue.py:591/626/664). Do NOT use
            # query.filter(agent_session_id=...) — agent_session_id is a
            # @property alias for id, not an indexed queryable field; the
            # filter would silently return nothing.
            entry = AgentSession.get_by_id(_session_id)
        except Exception as _lookup_err:
            logger.warning(
                "[session-health] Orphan reap: lookup failed for %s: %s",
                _session_id,
                _lookup_err,
            )
            continue

        if entry is None:
            # No DB row — handle is stale (record deleted). Pop the handle;
            # nothing else to do. No counter increment (no project_key to key on).
            _active_sessions.pop(_session_id, None)
            logger.debug(
                "[session-health] Orphan reap: popped handle for missing session %s",
                _session_id,
            )
            continue

        # Phantom guard: getattr on a phantom returns a Field descriptor,
        # which cannot be in _TERMINAL_STATUSES, so we won't act on it.
        actual_status = getattr(entry, "status", None)
        if actual_status not in _TERMINAL_STATUSES:
            # Healthy / running session — leave alone.
            continue

        # Grace window: skip if the session JUST transitioned (subprocess
        # may still be in natural teardown). A malformed/missing updated_at
        # is treated as "no grace" — proceed to reap.
        updated_ts = _ts(getattr(entry, "updated_at", None))
        if updated_ts is not None and (now - updated_ts) < ORPHAN_REAP_GRACE_SECONDS:
            logger.debug(
                "[session-health] Orphan reap: skipping %s within grace window "
                "(status=%s, age=%ss)",
                _session_id,
                actual_status,
                int(now - updated_ts),
            )
            continue

        pid = getattr(_handle, "pid", None)
        if pid is None:
            # Subprocess never started (on_sdk_started callback didn't fire)
            # OR handle was registered before the subprocess spawned. Pop the
            # handle — the task can never make progress on a terminal session.
            _active_sessions.pop(_session_id, None)
            logger.debug(
                "[session-health] Orphan reap: popped handle for %s (no pid, status=%s)",
                _session_id,
                actual_status,
            )
            continue

        # SIGTERM the orphan subprocess. ProcessLookupError = already dead
        # (pop handle, no counter); PermissionError = log WARN and pop anyway
        # (don't leave a stale entry); other exceptions = log DEBUG.
        sigterm_sent = False
        try:
            os.kill(pid, signal.SIGTERM)
            sigterm_sent = True
        except ProcessLookupError:
            # Subprocess already exited; handle pop below is sufficient.
            pass
        except PermissionError as _perm_err:
            logger.warning(
                "[session-health] Orphan reap: SIGTERM permission denied for session=%s pid=%s: %s",
                _session_id,
                pid,
                _perm_err,
            )
        except Exception as _kill_err:
            logger.debug(
                "[session-health] Orphan reap: SIGTERM failed for session=%s pid=%s: %s",
                _session_id,
                pid,
                _kill_err,
            )

        # Always pop the handle. The session row is terminal; the asyncio
        # task will never produce more progress regardless of whether SIGTERM
        # found a live PID.
        _active_sessions.pop(_session_id, None)

        if sigterm_sent:
            # Stage SIGKILL escalation for the next tick.
            _pending_sigkill.add(pid)

            # Observability counter — established prefix order
            # ``{project_key}:session-health:{metric}`` so dashboards
            # scanning ``{project_key}:session-health:*`` can see it.
            project_key = getattr(entry, "project_key", None) or "unknown"
            try:
                from popoto.redis_db import POPOTO_REDIS_DB as _R

                _R.incr(f"{project_key}:session-health:orphan_subprocess_reaped")
            except Exception as _counter_err:
                logger.debug(
                    "[session-health] orphan_subprocess_reaped counter failed (non-fatal): %s",
                    _counter_err,
                )

            logger.info(
                "[session-health] Orphan subprocess reaped: session=%s pid=%s status=%s",
                _session_id,
                pid,
                actual_status,
            )


async def _agent_session_hierarchy_health_check() -> None:
    """Check for orphaned children and stuck parents in session hierarchy.

    1. Orphaned children: child's parent_agent_session_id points to a non-existent session.
       Action: clear the parent_agent_session_id field (child completes normally).
    2. Stuck parents: status is waiting_for_children but all children are terminal.
       Action: finalize the parent (transition to completed/failed).

    Stale-index defense (kill-is-terminal, #1208): Before acting on any parent
    matched by ``query.filter(status="waiting_for_children")``, this function
    re-reads the parent's authoritative hash status. If the hash says terminal
    (killed/completed/failed/abandoned/cancelled), the index entry is stale and
    the parent is skipped. Without this guard, a killed parent whose
    ``waiting_for_children`` index entry was not srem'd at kill time will be
    picked up here, drive ``schedule_pipeline_completion``, and ship a final
    summary to Telegram even though the operator has already killed the session.
    See ``docs/features/session-lifecycle.md`` for the kill-is-terminal invariant
    and ``docs/features/bridge-self-healing.md`` for the broader defense pattern.
    """
    orphans_fixed = 0
    stuck_fixed = 0

    # Check for orphaned children
    try:
        all_sessions = list(AgentSession.query.all())
        # Guard against corrupt/phantom records whose fields are still Popoto Field
        # descriptors rather than hydrated values — those would crash set-building
        # and recreate.
        hydrated = [s for s in all_sessions if isinstance(s.agent_session_id, str)]
        children_with_parent = [s for s in hydrated if isinstance(s.parent_agent_session_id, str)]
        parent_ids = {s.agent_session_id for s in hydrated}

        for child in children_with_parent:
            if child.parent_agent_session_id not in parent_ids:
                try:
                    logger.warning(
                        "[session-health] Orphaned child %s: parent %s no longer exists — "
                        "clearing parent_agent_session_id",
                        child.agent_session_id,
                        child.parent_agent_session_id,
                    )
                    # Delete-and-recreate required: parent_agent_session_id is a KeyField,
                    # so mutating it directly would corrupt the index.
                    from agent.agent_session_queue import (
                        _extract_agent_session_fields,  # noqa: PLC0415
                    )

                    fields = _extract_agent_session_fields(child)
                    child.delete()
                    fields["parent_agent_session_id"] = None
                    AgentSession.create(**fields)
                    orphans_fixed += 1
                except Exception as inner:
                    logger.error(
                        "[session-health] Orphan repair failed for %s: %s",
                        getattr(child, "agent_session_id", "?"),
                        inner,
                        exc_info=True,
                    )
    except Exception as e:
        logger.error("[session-health] Orphan detection failed: %s", e, exc_info=True)

    # Check for stuck parents
    try:
        waiting_parents = list(AgentSession.query.filter(status="waiting_for_children"))
        for parent in waiting_parents:
            # Re-read the hash status: index entries can be stale (kill-is-terminal, #1208).
            # The waiting_for_children index entry may not have been srem'd when the parent
            # was killed; without this guard the killed parent would still be picked up,
            # ship a Telegram summary, and clobber the killed status. Re-reading the
            # authoritative hash is defense-in-depth analogous to the running-index fix
            # in #1006.
            fresh = get_authoritative_session(getattr(parent, "session_id", None))
            if fresh is not None and getattr(fresh, "status", None) in _TERMINAL_STATUSES:
                logger.info(
                    "[session-health] Skipping terminal parent %s (status=%s) — index entry stale",
                    getattr(parent, "agent_session_id", "?"),
                    fresh.status,
                )
                continue

            children = parent.get_children()
            if not children:
                # No children but waiting — auto-complete
                logger.warning(
                    "[session-health] Stuck parent %s has no children — auto-completing",
                    parent.agent_session_id,
                )
                from agent.session_completion import _transition_parent  # noqa: PLC0415

                _transition_parent(parent, "completed")
                stuck_fixed += 1
                continue

            terminal_statuses = _TERMINAL_STATUSES
            non_terminal = [c for c in children if c.status not in terminal_statuses]
            if not non_terminal:
                # All children terminal but parent still waiting
                any_failed = any(c.status == "failed" for c in children)
                new_status = "failed" if any_failed else "completed"
                logger.warning(
                    "[session-health] Stuck parent %s: all %d children terminal — "
                    "re-enqueuing for final summary (target=%s)",
                    parent.agent_session_id,
                    len(children),
                    new_status,
                )
                if new_status == "completed":
                    # Fan-out completion (issue #1058): invoke the PM final-delivery
                    # runner directly instead of pushing a steering message that
                    # relied on the deprecated [PIPELINE_COMPLETE] marker. The runner
                    # composes a summary via the harness and delivers through
                    # send_cb; the Redis CAS lock in _deliver_pipeline_completion
                    # deduplicates against any concurrent _handle_dev_session_completion
                    # path that may fire for the same parent.
                    n_ok = sum(1 for c in children if c.status == "completed")
                    n_fail = sum(1 for c in children if c.status == "failed")
                    child_lines = "\n".join(
                        f"  - {getattr(c, 'agent_session_id', '?')}: {c.status}" for c in children
                    )
                    fan_out_summary = (
                        f"All {len(children)} child pipeline sessions have completed "
                        f"({n_ok} succeeded, {n_fail} failed).\n"
                        f"Children:\n{child_lines}"
                    )

                    from agent.agent_session_queue import _resolve_callbacks  # noqa: PLC0415
                    from agent.session_completion import (  # noqa: PLC0415
                        schedule_pipeline_completion,
                    )

                    transport = getattr(parent, "transport", None) or None
                    send_cb, _react_cb = _resolve_callbacks(
                        getattr(parent, "project_key", None), transport
                    )
                    chat_id = getattr(parent, "chat_id", None)
                    telegram_message_id = getattr(parent, "telegram_message_id", None)

                    logger.info(
                        "[session-health] Fan-out complete for parent %s — "
                        "invoking completion-turn runner",
                        parent.agent_session_id,
                    )
                    schedule_pipeline_completion(
                        parent, fan_out_summary, send_cb, chat_id, telegram_message_id
                    )
                else:
                    # Failed parent: finalize immediately (no point composing a summary)
                    from agent.session_completion import _transition_parent  # noqa: PLC0415

                    _transition_parent(parent, new_status)
                stuck_fixed += 1
    except Exception as e:
        logger.error("[session-health] Stuck parent detection failed: %s", e, exc_info=True)

    if orphans_fixed or stuck_fixed:
        logger.info(
            "[session-health] Hierarchy check: %d orphan(s) fixed, %d stuck parent(s) fixed",
            orphans_fixed,
            stuck_fixed,
        )


async def _dependency_health_check() -> None:
    """No-op: dependency tracking was removed in issue #609."""
    pass


def register_worker_pid() -> None:
    """Write ``worker:registered_pid:{hostname}:{pid}`` to Redis with TTL.

    Issue #1271. Called from worker startup AND from ``_write_worker_heartbeat``
    on every heartbeat tick. The cross-process orphan reaper reads all keys
    matching the prefix and adds their integer values to its skip-set, so a
    live worker is never reaped even if a future code change re-adds the
    worker pattern to the cmdline regex set. Failure is non-fatal: the reaper
    still has ``os.getpid()`` in its skip-set.

    B2-probe (issue #1817, optional/observability-only): additionally runs a
    liveness-gated duplicate-worker check scoped to the same host + role and
    logs a WARNING when a genuinely live second worker is found. This probe
    is diagnostic-only: it always lets registration proceed, never exits,
    and never blocks -- see ``_probe_duplicate_worker_registration`` for the
    full rationale. The original additive ``_R.set`` write above is
    unchanged.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        pid = os.getpid()
        key = f"{WORKER_REGISTERED_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}"
        _R.set(key, pid, ex=WORKER_REGISTERED_PID_TTL_SECONDS)
    except Exception as e:
        logger.debug("[session-health] register_worker_pid write failed: %s", e)

    try:
        _probe_duplicate_worker_registration(os.getpid())
    except Exception as e:
        logger.debug("[session-health] duplicate-worker probe failed (non-fatal): %s", e)


def _probe_duplicate_worker_registration(pid: int) -> None:
    """Observability-only liveness-gated duplicate-worker probe (issue #1817 B2-probe).

    Scoped to the SAME host + role: a different machine, or a worker running
    a different role (``VALOR_PROJECT_KEY``), legitimately owns its own
    registration and is never compared against. ``pid`` is always
    ``os.getpid()`` of the caller -- a worker never flags itself, since the
    comparison only considers a *different* pid found under the role key.

    A competitor pid is only treated as a genuine conflict when it is
    CONFIRMED LIVE: it must pass ``os.kill(pid, 0)`` AND have a heartbeat
    timestamp fresher than ``HEARTBEAT_FRESHNESS_WINDOW``. A pid that fails
    either check is dead-worker residue (the exact launchd-respawn case) --
    it is silently superseded (this registration overwrites the role key)
    with no log line, since logging a "conflict" for routine respawn churn
    would be noise, not signal.

    Only when a competitor pid is CONFIRMED LIVE does this emit
    ``logger.warning`` and supersede the role key. Still never
    exits/blocks/refuses in either branch.

    Why this must NEVER refuse to start: under launchd ``KeepAlive``, an
    unclean worker exit leaves the dead pid's TTL'd key present until its
    TTL expires. A refuse-guard here would block the HEALTHY RESPAWNED
    worker for that entire window -- an availability outage. The atomic
    pending->running run-claim (``models.session_lifecycle.claim_pending_run``,
    issue #1817 B2) is what makes exactly-one-actor-per-session correctness
    hold; this probe is diagnostic only and always allows registration to
    proceed.
    """
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    role = _current_worker_role()
    role_key = f"{_WORKER_ROLE_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{role}"

    existing_raw = _R.get(role_key)
    if existing_raw is not None:
        try:
            existing_pid = int(existing_raw)
        except (TypeError, ValueError):
            existing_pid = None

        if existing_pid is not None and existing_pid != pid:
            is_live = _pid_is_live(existing_pid)
            heartbeat_fresh = is_live and _worker_pid_heartbeat_fresh(existing_pid)

            if is_live and heartbeat_fresh:
                logger.warning(
                    "[session-health] second live worker for host=%s role=%s "
                    "(existing pid=%d, new pid=%d) -- superseding pid registration",
                    _ORPHAN_REAP_HOSTNAME,
                    role,
                    existing_pid,
                    pid,
                )
            # else: dead/stale residue (liveness or heartbeat check failed) --
            # silently supersede, no log. This is the routine launchd-respawn
            # case, not a real conflict.

    # Additive: this registration always proceeds and always overwrites the
    # role key + this pid's heartbeat timestamp, regardless of the liveness
    # outcome above. Never refuses.
    now_ts = datetime.now(UTC).timestamp()
    _R.set(role_key, pid, ex=WORKER_REGISTERED_PID_TTL_SECONDS)
    _R.set(
        f"{_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}",
        now_ts,
        ex=WORKER_REGISTERED_PID_TTL_SECONDS,
    )


def _pid_is_live(pid: int) -> bool:
    """Return True if ``pid`` appears to be a live process on this host.

    ``os.kill(pid, 0)`` sends no signal, only checks existence/permission.
    A ``PermissionError`` means the process exists but is owned by another
    user -- treated conservatively as live (we cannot confirm death, so we
    do not silently supersede without evidence). Any other failure
    (``ProcessLookupError``, etc.) means the pid is dead-worker residue.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _worker_pid_heartbeat_fresh(pid: int) -> bool:
    """Return True if ``pid``'s last B2-probe heartbeat timestamp is fresh.

    Reuses ``HEARTBEAT_FRESHNESS_WINDOW`` as the staleness threshold. Missing
    or unparseable timestamps are treated as stale (fail toward "silently
    supersede", not toward "log a conflict").
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        raw = _R.get(f"{_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}")
        if raw is None:
            return False
        ts = float(raw)
        return (datetime.now(UTC).timestamp() - ts) < HEARTBEAT_FRESHNESS_WINDOW
    except Exception:
        return False


def _write_worker_heartbeat() -> None:
    """Write worker heartbeat file so the dashboard can show worker status.

    Also refreshes the worker's registered-PID key in Redis (issue #1271).
    """
    heartbeat_file = Path(__file__).parent.parent / "data" / "last_worker_connected"
    try:
        heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat_file.with_suffix(".tmp")
        tmp.write_text(datetime.now(UTC).isoformat())
        os.replace(tmp, heartbeat_file)
    except OSError:
        pass
    try:
        register_worker_pid()
    except Exception as e:
        logger.debug("[session-health] register_worker_pid refresh failed: %s", e)

    _publish_loop_beacon()


def _publish_loop_beacon() -> None:
    """Publish the wall-clock loop beacon to Redis (Fix #5, #1821).

    The bridge process cannot read ``last_loop_tick`` directly — it is a
    ``time.monotonic()`` value, meaningless in another process. So the off-loop
    heartbeat thread translates the per-process monotonic loop age into a
    **wall-clock** ``time.time()`` timestamp the bridge can key freshness on.

    CRITICAL (Risk 1 — the #1 design risk): the bridge keys freshness ONLY on
    ``wall_ts`` (a wall-clock ``time.time()`` value). ``loop_beacon_age_s`` (the
    monotonic ``now - last_loop_tick`` age) is carried as an ADVISORY field only
    and must NEVER be used for cross-process time math — mixing two unrelated
    clocks yields nonsense. ``armed=False`` / age ``None`` means the loop has not
    ticked yet (the beacon is unarmed) and is NEVER treated as wedged.

    Fail-quiet: a Redis error here must never break the heartbeat — the disk
    write already happened before this call, and the dead-man's-switch never
    aborts on a beacon-publish failure.
    """
    try:
        tick = _session_state.get_loop_tick()
        now_monotonic = time.monotonic()
        armed = tick is not None
        loop_beacon_age_s = (now_monotonic - tick) if tick is not None else None
        payload = {
            # wall_ts is the ONLY field the bridge keys freshness on (Risk 1).
            "wall_ts": time.time(),
            # Advisory only — a per-process monotonic age, never cross-process math.
            "loop_beacon_age_s": loop_beacon_age_s,
            "armed": armed,
        }
        from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

        _R.set(
            f"{WORKER_LOOP_BEACON_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}",
            json.dumps(payload),
            ex=WORKER_LOOP_BEACON_TTL_SECONDS,
        )
    except Exception as e:
        logger.debug("[session-health] loop-beacon publish failed (non-fatal): %s", e)


async def _agent_session_health_loop() -> None:
    """Periodically check running sessions for liveness and timeout."""
    logger.info(
        "[session-health] Agent session health monitor started (interval=%ds)",
        AGENT_SESSION_HEALTH_CHECK_INTERVAL,
    )
    while True:
        try:
            # Heartbeat write moved to dedicated daemon thread (issue #1767):
            # worker/__main__.py::_heartbeat_thread_main(). Keeping it here
            # meant PTY/thread-pool saturation could starve the write and
            # produce a false "hung worker" signal to the watchdog.
            await _agent_session_health_check()
            await _agent_session_hierarchy_health_check()
            await _dependency_health_check()
            # Issue #1632 mode 1c: stale `--print` one-shots accumulate at
            # ~4/min during an orphan cascade — the hourly cleanup reflection
            # is too slow. The fast reaper is itself fail-silent (never
            # raises); the outer try/except here is a second safety layer.
            _fast_reap_stale_print_oneshots()
        except Exception as e:
            logger.error("[session-health] Error in health check: %s", e, exc_info=True)
        await asyncio.sleep(AGENT_SESSION_HEALTH_CHECK_INTERVAL)


def _eval_mid_run_pty_stage1(entry: "AgentSession", now: datetime) -> None:
    """Stage-1 PTY quiescence evaluation for the path-B mid-run wedge detector (#1724).

    Evaluates PTY screen liveness for a single session on the 30s tool-timeout
    sub-loop tick. Updates durable cross-tick state on ``entry`` in place and
    persists via ``update_fields``. Does NOT recover — it only maintains the
    ``mid_run_quiescent_since`` timestamp and flags confirmed suspects for the
    future stage-2 dispatch (task 4). Fail-silent: never raises.

    Eligibility gate:
    - ``sdk_ever_output=True``: session has produced at least one PM turn.
    - ``current_tool_name`` is set: a tool is currently in flight.
    - ``last_pty_read_loop_at`` is not None: PTY writer has initialized.

    Three-state PTY freshness logic (compared against ``last_heartbeat_at``):
    1. ABSTAIN: ``last_pty_read_loop_at`` is None → writer not initialized yet.
    2. ABSTAIN: read-loop marker is stale (> HEARTBEAT_FRESHNESS_WINDOW behind
       ``last_heartbeat_at``) → the pexpect thread may have died; clear
       ``mid_run_quiescent_since`` and skip this tick.
    3. FRESH: read-loop marker is current.
       a. Activity fresh (``last_pty_activity_at`` recently changed): NOT a
          suspect; clear ``mid_run_quiescent_since``.
       b. Activity stale (screen not repainting): check the snapshot tuple
          ``(last_pty_activity_at, byte_offset)`` against the prior tick's
          ``mid_run_pty_snapshot``. If unchanged, increment the quiescence
          window (set ``mid_run_quiescent_since`` if not already set); if
          ``now - mid_run_quiescent_since >= MID_RUN_QUIESCENCE_SECS``,
          log the confirmed suspect for stage-2. If the snapshot changed,
          reset ``mid_run_quiescent_since``.
    """
    try:
        # Eligibility: must have produced output and have a tool in flight.
        sdk_ever_output = bool(
            getattr(entry, "last_turn_at", None) or getattr(entry, "last_tool_use_at", None)
        )
        if not sdk_ever_output:
            return
        tool_name = getattr(entry, "current_tool_name", None)
        if not tool_name:
            return

        # Eligibility: PTY writer must have initialized.
        last_loop_at = getattr(entry, "last_pty_read_loop_at", None)
        if not isinstance(last_loop_at, datetime):
            # ABSTAIN: writer never initialized (non-granite session or not yet started).
            return

        last_loop_aware = last_loop_at if last_loop_at.tzinfo else last_loop_at.replace(tzinfo=UTC)
        last_hb_at = getattr(entry, "last_heartbeat_at", None)

        # Use last_heartbeat_at as the reference clock for staleness checks.
        # If not set, fall back to now (conservative).
        if isinstance(last_hb_at, datetime):
            ref_time = last_hb_at if last_hb_at.tzinfo else last_hb_at.replace(tzinfo=UTC)
        else:
            ref_time = now

        loop_age = (ref_time - last_loop_aware).total_seconds()
        if loop_age > HEARTBEAT_FRESHNESS_WINDOW:
            # ABSTAIN: the pexpect read-loop writer has gone silent (thread may
            # have died or been throttled). Clear quiescent_since so we don't
            # accumulate stale quiescence time across a writer outage.
            mid_qs = getattr(entry, "mid_run_quiescent_since", None)
            if mid_qs is not None:
                try:
                    entry.mid_run_quiescent_since = None
                    save = getattr(entry, "save", None)
                    if callable(save):
                        save(update_fields=["mid_run_quiescent_since"])
                except Exception as _clr_err:
                    logger.debug(
                        "[session-health] stage-1 mid_run_quiescent_since clear failed: %s",
                        _clr_err,
                    )
            logger.debug(
                "[session-health] stage-1 ABSTAIN session=%s: "
                "last_pty_read_loop_at stale by %.0fs (> HEARTBEAT_FRESHNESS_WINDOW=%ds)",
                getattr(entry, "agent_session_id", "?"),
                loop_age,
                HEARTBEAT_FRESHNESS_WINDOW,
            )
            return

        # PTY read-loop is fresh. Check whether the screen is repainting.
        last_activity_at = getattr(entry, "last_pty_activity_at", None)
        activity_fresh = False
        if isinstance(last_activity_at, datetime):
            act_aware = (
                last_activity_at
                if last_activity_at.tzinfo
                else last_activity_at.replace(tzinfo=UTC)
            )
            activity_age = (now - act_aware).total_seconds()
            # Screen is "fresh" if activity within the same HEARTBEAT_FRESHNESS_WINDOW.
            activity_fresh = activity_age < HEARTBEAT_FRESHNESS_WINDOW

        # Build current snapshot tuple string for cross-tick comparison.
        # byte_offset is a corroborator (log but do not gate on it).
        act_iso = last_activity_at.isoformat() if isinstance(last_activity_at, datetime) else "None"
        # stage-1 proxy: total_input_tokens substitutes for the plan's byte_offset (raw
        # transcript bytes) because byte_offset is not persisted on AgentSession.
        # Safe for observe-only stage-1 (corroborator-only, never gates).
        # Must be replaced with a persisted byte_offset before stage-2 wires recovery.
        byte_offset = getattr(entry, "total_input_tokens", None)
        current_snapshot = f"({act_iso},{byte_offset})"

        prior_snapshot = getattr(entry, "mid_run_pty_snapshot", None)
        mid_qs = getattr(entry, "mid_run_quiescent_since", None)

        update_fields: list[str] = []

        if activity_fresh:
            # NOT a suspect: screen is repainting. Clear quiescent_since.
            if mid_qs is not None:
                entry.mid_run_quiescent_since = None
                update_fields.append("mid_run_quiescent_since")
        else:
            # Screen is quiescent. Compare snapshot to prior tick.
            if current_snapshot == prior_snapshot:
                # Same snapshot as last tick: screen has not repainted.
                if mid_qs is None:
                    # First quiescent tick: record the start time.
                    entry.mid_run_quiescent_since = now
                    update_fields.append("mid_run_quiescent_since")
                    mid_qs = now
                # Check whether the quiescence window has been exceeded.
                if isinstance(mid_qs, datetime):
                    qs_aware = mid_qs if mid_qs.tzinfo else mid_qs.replace(tzinfo=UTC)
                    quiescent_secs = (now - qs_aware).total_seconds()
                    if MID_RUN_QUIESCENCE_SECS > 0 and quiescent_secs >= MID_RUN_QUIESCENCE_SECS:
                        logger.warning(
                            "[session-health] stage-1 CONFIRMED SUSPECT session=%s "
                            "tool=%s quiescent_secs=%.0f byte_offset=%s "
                            "(awaiting stage-2 dispatch, task #1724)",
                            getattr(entry, "agent_session_id", "?"),
                            tool_name,
                            quiescent_secs,
                            byte_offset,
                        )
                    else:
                        logger.debug(
                            "[session-health] stage-1 quiescent session=%s tool=%s "
                            "quiescent_secs=%.0f / %.0f",
                            getattr(entry, "agent_session_id", "?"),
                            tool_name,
                            quiescent_secs,
                            MID_RUN_QUIESCENCE_SECS,
                        )
            else:
                # Snapshot changed between ticks (activity resumed): reset.
                if mid_qs is not None:
                    entry.mid_run_quiescent_since = None
                    update_fields.append("mid_run_quiescent_since")

        # Always rewrite snapshot to current (cross-tick state persistence).
        entry.mid_run_pty_snapshot = current_snapshot
        update_fields.append("mid_run_pty_snapshot")

        if update_fields:
            try:
                save = getattr(entry, "save", None)
                if callable(save):
                    save(update_fields=update_fields)
            except Exception as _save_err:
                logger.debug(
                    "[session-health] stage-1 snapshot save failed for %s: %s",
                    getattr(entry, "agent_session_id", "?"),
                    _save_err,
                )

    except Exception:
        logger.debug(
            "[session-health] stage-1 eval raised for session %s",
            getattr(entry, "agent_session_id", "?"),
            exc_info=True,
        )


async def _agent_session_tool_timeout_check() -> None:
    """Per-tool timeout sub-loop tick (issue #1270).

    Scans every ``running`` ``AgentSession`` row and recovers any whose
    ``current_tool_name`` is non-null AND ``last_tool_use_at`` exceeds the
    tier-specific budget (30s internal / 120s mcp / 300s default).

    On a hit:
      1. Re-read ``current_tool_name`` and ``last_tool_use_at`` from a fresh
         query (race mitigation — PostToolUse may have fired between the
         initial read and this point; if either is now fresh, abort the
         recovery for this tick).
      2. Bump the per-tier counter on the session row
         (``tool_timeout_count_{internal,mcp,default}``).
      3. INCR ``{project_key}:session-health:tool_timeouts:{tier}`` Redis counter.
      4. Delegate to ``_apply_recovery_transition`` with
         ``reason_kind="tool_timeout"`` — bypasses Tier 2 reprieve (the
         wedge condition itself is the evidence) but otherwise reuses the
         same MAX_RECOVERY_ATTEMPTS / OOM-defer / kill-switch path as the
         main loop. See helper docstring for the full transition behavior.

    The kill-switch ``TOOL_TIMEOUT_TIERS_DISABLED=1`` short-circuits the
    entire tick (parity with ``DISABLE_PROGRESS_KILL`` for the main loop).
    """
    if os.environ.get("TOOL_TIMEOUT_TIERS_DISABLED") == "1":
        return

    running_sessions = _filter_hydrated_sessions(AgentSession.query.filter(status="running"))
    for entry in running_sessions:
        # Terminal-status guard (#1006) — IndexedField may show stale running entries.
        actual_status = getattr(entry, "status", None)
        if actual_status in _TERMINAL_STATUSES:
            continue
        try:
            now = datetime.now(tz=UTC)

            # D0: dedicated never-started recovery branch (issue #1724).
            # Check BEFORE the tool-timeout path so never-started sessions are
            # caught promptly on the 30s loop without waiting for a tool wedge.
            # Uses the same race-mitigation pattern as the tool-timeout path:
            # re-read fresh, re-confirm predicate, then transition.
            if _never_started_past_grace(entry, now):
                try:
                    fresh_ns = AgentSession.get_by_id(entry.agent_session_id)
                except Exception as _ns_re_err:
                    logger.debug(
                        "[session-health] never-started re-read failed for %s: %s",
                        entry.agent_session_id,
                        _ns_re_err,
                    )
                    fresh_ns = None
                if fresh_ns is not None and (
                    getattr(fresh_ns, "status", None) not in _TERMINAL_STATUSES
                ):
                    if _never_started_past_grace(fresh_ns, now):
                        # Increment project-scoped telemetry counter.
                        try:
                            from popoto.redis_db import POPOTO_REDIS_DB as _R_NS

                            _R_NS.incr(
                                f"{fresh_ns.project_key}:session-health:"
                                f"tier1_falloff:never_started_grace_exceeded"
                            )
                        except Exception as _ns_ctr_err:
                            logger.debug(
                                "[session-health] never_started_grace_exceeded counter "
                                "increment failed: %s",
                                _ns_ctr_err,
                            )
                        # PTY-liveness gate (#1792): defer the D0 kill when the granite
                        # PTY is demonstrably alive (screen activity within the window).
                        # SDK/non-PTY sessions fall through (helper returns False).
                        if _prime_pty_alive(fresh_ns, now):
                            try:
                                from popoto.redis_db import POPOTO_REDIS_DB as _R_PTY_DEFER

                                _R_PTY_DEFER.incr(
                                    f"{fresh_ns.project_key}:session-health:"
                                    f"never_started_pty_deferred"
                                )
                            except Exception:
                                logger.debug("Failed to incr never_started_pty_deferred counter")
                            continue

                        handle_ns = _active_sessions.get(fresh_ns.agent_session_id)
                        await _apply_recovery_transition(
                            fresh_ns,
                            reason="no progress signal observed (never_started past grace)",
                            reason_kind="no_progress",
                            handle=handle_ns,
                            worker_key=fresh_ns.worker_key,
                        )
                        continue

            # === Stage-1 PTY quiescence gate (path B, #1724) ===
            # Cheap, stateless per-tick evaluation of PTY screen activity for
            # sessions that have produced output and have a tool in flight.
            # Updates durable state (mid_run_quiescent_since, mid_run_pty_snapshot)
            # on the entry object and persists via update_fields. Does NOT recover —
            # it only flags confirmed suspects for stage-2 (task 4). Fail-silent.
            _eval_mid_run_pty_stage1(entry, now)

            check = _check_tool_timeout(entry)
            if check is None:
                continue
            tier, reason = check

            # PTY-liveness gate for default-tier tools on granite PTY sessions (#1784).
            # Internal/mcp tiers keep the age-only predicate (they are short-lived tools
            # that must not run for minutes). For the default tier, suppress the kill if
            # the PTY is still painting — a screen still updating is never a wedge.
            # SDK/non-granite sessions (last_pty_read_loop_at=None) always go through
            # the age-only path (helper branch 2) so the 300s ceiling is preserved.
            if tier == "default" and not _pty_quiescent_long_enough(entry, now):
                logger.debug(
                    "[session-health] tool-timeout default-tier deferred for %s "
                    "(%s): PTY still active (mid_run_quiescent_since=%s, "
                    "MID_RUN_QUIESCENCE_SECS=%s)",
                    entry.agent_session_id,
                    getattr(entry, "current_tool_name", "?"),
                    getattr(entry, "mid_run_quiescent_since", None),
                    MID_RUN_QUIESCENCE_SECS,
                )
                # Deferred-kill observability counter (critique Concern 2).
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB as _R_DEFER

                    _R_DEFER.incr(
                        f"{entry.project_key}:session-health:tool_timeouts:default_deferred"
                    )
                except Exception as _def_err:
                    logger.debug(
                        "[session-health] tool_timeouts:default_deferred incr failed: %s",
                        _def_err,
                    )
                continue

            # Race mitigation (issue #1270 Risk 2): re-read both fields from a
            # fresh query before we transition. PostToolUse may have fired
            # between the iterator's read and this point. If the second read
            # shows a fresh (or cleared) state, abort the recovery for this tick.
            try:
                fresh = AgentSession.get_by_id(entry.agent_session_id)
            except Exception as _re_err:
                logger.debug(
                    "[session-health] tool-timeout re-read failed for %s: %s",
                    entry.agent_session_id,
                    _re_err,
                )
                continue
            if fresh is None:
                continue
            if getattr(fresh, "status", None) in _TERMINAL_STATUSES:
                continue
            recheck = _check_tool_timeout(fresh)
            if recheck is None:
                logger.debug(
                    "[session-health] tool-timeout race avoided for %s "
                    "(PostToolUse fired between read and transition)",
                    entry.agent_session_id,
                )
                continue
            tier, reason = recheck

            # Re-evaluate the PTY-liveness gate on the fresh row (#1784 race path):
            # if the tool resumed painting between the iterator read and the re-read,
            # abort the recovery — we must not kill a session that just resumed output.
            if tier == "default" and not _pty_quiescent_long_enough(fresh, now):
                logger.debug(
                    "[session-health] tool-timeout default-tier race abort for %s "
                    "(%s): PTY resumed painting between read and re-read",
                    fresh.agent_session_id,
                    getattr(fresh, "current_tool_name", "?"),
                )
                continue

            # Extend the default-tier reason string with quiescence context so logs
            # explain why a live-but-past-deadline default tool was killed.
            if tier == "default":
                reason = (
                    f"{reason}; pty quiescent >= {MID_RUN_QUIESCENCE_SECS}s"
                    if getattr(fresh, "last_pty_read_loop_at", None) is not None
                    else reason
                )

            # Bump per-tier counter on the session row. Best-effort; failure
            # must not block the recovery transition (matches the
            # observability-counter pattern at #1036:863).
            counter_field = f"tool_timeout_count_{tier}"
            try:
                current = getattr(fresh, counter_field, 0) or 0
                setattr(fresh, counter_field, current + 1)
                fresh.save(update_fields=[counter_field])
            except Exception as _cnt_err:
                logger.debug(
                    "[session-health] tool_timeout_count_%s save failed: %s",
                    tier,
                    _cnt_err,
                )

            # Project-tier Redis counter — mirrors `recoveries:{kind}` precedent.
            try:
                from popoto.redis_db import POPOTO_REDIS_DB as _R

                _R.incr(f"{fresh.project_key}:session-health:tool_timeouts:{tier}")
            except Exception as _rc_err:
                logger.debug(
                    "[session-health] tool_timeouts:%s incr failed: %s",
                    tier,
                    _rc_err,
                )

            handle = _active_sessions.get(fresh.agent_session_id)
            await _apply_recovery_transition(
                fresh,
                reason=reason,
                reason_kind="tool_timeout",
                handle=handle,
                worker_key=fresh.worker_key,
            )
        except Exception:
            logger.exception(
                "[session-health] tool-timeout check error for session %s",
                getattr(entry, "agent_session_id", "unknown"),
            )


async def _agent_session_tool_timeout_loop() -> None:
    """Dedicated 30-second sub-loop for per-tool timeout enforcement.

    Runs in parallel to ``_agent_session_health_loop`` so the 30s internal-tier
    budget can fire within one tick of expiry. The 5-minute main loop's other
    checks (psutil, OOM defer, orphan reap) stay on their original cadence —
    we deliberately avoid running them at 30s to keep load impact bounded.

    Kill switch: ``TOOL_TIMEOUT_TIERS_DISABLED=1`` short-circuits each tick
    (parity with ``DISABLE_PROGRESS_KILL`` for the main loop).
    """
    logger.info(
        "[session-health] Per-tool timeout sub-loop started (interval=%ds, "
        "internal=%ds, mcp=%ds, default=%ds)",
        TOOL_TIMEOUT_LOOP_INTERVAL,
        TOOL_TIMEOUT_INTERNAL_SEC,
        TOOL_TIMEOUT_MCP_SEC,
        TOOL_TIMEOUT_DEFAULT_SEC,
    )
    while True:
        try:
            await _agent_session_tool_timeout_check()
        except Exception as e:
            logger.error(
                "[session-health] Error in tool-timeout sub-loop: %s",
                e,
                exc_info=True,
            )
        await asyncio.sleep(TOOL_TIMEOUT_LOOP_INTERVAL)


def format_duration(seconds) -> str:
    """Format seconds into human-readable duration."""
    if seconds is None:
        return "N/A"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_mins = minutes % 60
    return f"{hours}h{remaining_mins}m"


def _delete_with_stale_key_lookup(session) -> bool:
    """Force-delete a session whose stored Redis key has drifted from the schema.

    When the model's ``KeyField`` composition order changes between releases,
    records stored before the change live at the OLD key path, while
    ``session.db_key.redis_key`` (and Popoto's ``delete()`` fallback)
    computes the NEW key path. ``HDEL`` against the new key returns 0
    and ``delete()`` reports False without doing anything.

    This helper resolves the actual stored key from the class set
    (``Model.query.keys()`` returns SMEMBERS of ``$Class:<name>``), matches
    by ``agent_session_id``, sets ``_redis_key``, and retries delete.
    Returns True if the second attempt succeeded.
    """
    aid = getattr(session, "agent_session_id", None)
    if not isinstance(aid, str):
        return False
    try:
        class_set_keys = AgentSession.query.keys()
    except Exception:
        return False
    target = None
    for k in class_set_keys:
        ks = k.decode() if isinstance(k, bytes) else k
        if aid in ks:
            target = ks
            break
    if not target:
        return False
    session._redis_key = target
    try:
        return bool(session.delete())
    except Exception as exc:
        logger.warning("[agent-session-cleanup] Forced delete with %s raised: %s", target, exc)
        return False


# === Per-status index drift pre-scan (issue #1361) ===
#
# `repair_indexes()` (models/agent_session.py) only counts a member as
# `stale_count` when `hgetall(member)` returns empty (i.e., the underlying
# hash is gone — a "phantom"). It does NOT count members whose hash exists
# but whose `status` field disagrees with the index segment ("drift").
#
# This pre-scan walks `$IndexF:AgentSession:status:*` keys and counts drift
# members per status. Phantoms are skipped here (still owned by repair_indexes).
# Counts feed the `agent_session.indexed_field.stale_members` metric.
#
# IMPORTANT: scope is intentionally `status` only. Other indexed fields
# (e.g. `claude_pid`, `claude_session_uuid`) have non-status segments;
# putting them into a `dimensions={"status": ...}` metric would produce an
# inverted cardinality bomb. `repair_indexes()` (called unconditionally
# below) handles drift on those fields generically — no per-field metric.
_STATUS_INDEX_PREFIX = "$IndexF:AgentSession:status:"


def _count_per_status_stale_index_members() -> dict[str, int]:
    """Walk status-index keys and count drift members per status segment.

    A "drift" member has a populated hash whose `status` field differs from
    the index-key segment. Phantoms (empty `hgetall`) are NOT counted —
    they are `repair_indexes()`'s responsibility (see #1361).

    Unknown status segments (not in `ALL_STATUSES`) are coalesced under
    the `"unknown"` key and a WARNING is logged with the actual segment
    value, so a future bug producing garbage segments cannot explode the
    metric's dimension cardinality.

    Returns:
        Mapping of status (or "unknown") -> count of drift members.
        Empty dict on a clean DB.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    drift: dict[str, int] = {}
    keys = POPOTO_REDIS_DB.keys(f"{_STATUS_INDEX_PREFIX}*")
    for raw_key in keys:
        index_key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        # Parse the segment after the literal prefix.
        if not index_key.startswith(_STATUS_INDEX_PREFIX):
            continue
        segment = index_key[len(_STATUS_INDEX_PREFIX) :]
        if segment in ALL_STATUSES:
            dim_status = segment
        else:
            dim_status = "unknown"
            logger.warning(
                "[agent-session-cleanup] Unknown status segment in index key %s "
                "(coalesced into dimension status='unknown')",
                index_key,
            )

        for raw_member in POPOTO_REDIS_DB.smembers(index_key):
            hash_data = POPOTO_REDIS_DB.hgetall(raw_member)
            if not hash_data:
                # Phantom — owned by repair_indexes(); skip here.
                continue
            # Status field is msgpack-encoded; decoding is best-effort.
            # If we can't tell the field's value, treat it as drift (safer
            # to count than to silently miss a real drift case).
            actual_status = _extract_status_field(hash_data)
            if actual_status != segment:
                drift[dim_status] = drift.get(dim_status, 0) + 1
    return drift


def _extract_status_field(hash_data: dict) -> str | None:
    """Decode the `status` field from a raw `HGETALL` payload.

    Popoto serializes field values via msgpack. Returns the decoded string
    on success, or None if the field is missing or undecodable.
    """
    import msgpack

    # Hash keys/values come back as bytes from redis-py by default.
    for k, v in hash_data.items():
        kstr = k.decode() if isinstance(k, bytes) else k
        if kstr != "status":
            continue
        try:
            return msgpack.unpackb(v) if isinstance(v, (bytes, bytearray)) else v
        except Exception:
            return None
    return None


def cleanup_corrupted_agent_sessions() -> dict[str, int]:
    """Delete AgentSession records with corrupted data that prevent .save().

    Detects sessions where the ID field has an invalid length (e.g., 60 chars
    instead of the expected 32 for uuid4), or where ``.save()`` raises a
    validation-type exception (``"invalid"`` or ``"validation"`` in the message).
    These records jam the health check and startup recovery loops with repeated
    errors because they can't be transitioned or finalized through normal ORM ops.

    Before any iteration, the result of ``AgentSession.query.all()`` is passed
    through ``_filter_hydrated_sessions`` to drop phantom instances — records
    whose fields are still Popoto ``Field`` descriptors, produced when
    orphan ``$IndexF:AgentSession:*`` members reference deleted hashes.
    Phantoms must never reach the mutation path: attribute access on a
    phantom returns a descriptor repr (~60 chars), the length check then
    mis-flags it as "corrupt", and ``.delete()`` damages real records whose
    indexed-field values happen to match.

    After the mutation pass, two independent index-hygiene steps run on
    EVERY tick (issue #1361 — gate removed):

    1. **Per-status drift pre-scan.** Walks ``$IndexF:AgentSession:status:*``
       and counts members whose hash exists but whose ``status`` field
       disagrees with the index segment ("drift"). Counts are emitted as
       ``agent_session.indexed_field.stale_members`` metrics with
       ``dimensions={"status": <status>}``. This is observability-only —
       the actual cleanup is done by step 2.
    2. **Unconditional ``AgentSession.repair_indexes()``.** Clears every
       ``$IndexF:AgentSession:*`` key and rebuilds from surviving hashes.
       Idempotent on a clean DB; per-tick cost is negligible.

    The pre-scan and ``repair_indexes()`` count DIFFERENT failure modes.
    ``repair_indexes()``'s ``phantoms_cleared`` is members whose hash is
    GONE; the pre-scan's drift counts are members whose hash is PRESENT
    but mis-classified. The two counters are independent and additive.

    Called by the reflection scheduler as the 'agent-session-cleanup' reflection.
    Also safe to call from startup recovery or the update script.

    Issue #1271: after the corrupted-record pass and ``repair_indexes()``, this
    function calls ``_reap_orphan_session_processes()`` to scan the OS process
    table for PPID==1 orphan claude/MCP subprocesses. Reaper failure is
    swallowed and reported as ``orphans=0`` in the return dict — never aborts
    the corrupted-record cleanup.

    Returns a dict ``{"corrupted": int, "orphans": int}``:
        - ``corrupted``: number of corrupted AgentSession records deleted.
        - ``orphans``: number of OS-process orphans reaped (parent kills only;
          descendants are bookkeeping for the staged SIGKILL drain).
    """
    cleaned = 0
    raw_sessions = list(AgentSession.query.all())
    all_sessions = _filter_hydrated_sessions(raw_sessions)
    phantoms_filtered = len(raw_sessions) - len(all_sessions)

    for session in all_sessions:
        session_id_str = str(getattr(session, "id", "") or "")
        is_corrupt = False

        # Check 1: ID length validation (uuid4 strategy expects 32 chars)
        if session_id_str and len(session_id_str) != 32:
            logger.warning(
                "[agent-session-cleanup] Corrupted session detected: id=%s "
                "(length %d, expected 32), session_id=%s",
                session_id_str[:20],
                len(session_id_str),
                getattr(session, "session_id", "?"),
            )
            is_corrupt = True

        # Check 2: Try a no-op save to detect other validation failures
        if not is_corrupt:
            try:
                session.save()
            except Exception as e:
                if "invalid" in str(e).lower() or "validation" in str(e).lower():
                    logger.warning(
                        "[agent-session-cleanup] Unsaveable session detected: "
                        "id=%s, session_id=%s, error=%s",
                        session_id_str[:20],
                        getattr(session, "session_id", "?"),
                        e,
                    )
                    is_corrupt = True

        if is_corrupt:
            try:
                deleted = session.delete()
                if deleted:
                    cleaned += 1
                elif _delete_with_stale_key_lookup(session):
                    # Hash key format drift: the stored key reflects an older
                    # KeyField composition order than the current model schema,
                    # so Popoto's computed db_key.redis_key points at a
                    # non-existent hash and HDEL returns 0. Resolve the actual
                    # key from the class set, set _redis_key, retry delete.
                    cleaned += 1
                else:
                    logger.warning(
                        "[agent-session-cleanup] ORM delete returned False for %s "
                        "and no class-set match found — record will reappear next tick",
                        session_id_str[:20],
                    )
            except Exception as del_err:
                # ORM-only policy: no raw-Redis fallback. If ORM delete fails,
                # log and move on — next reflection tick will retry.
                logger.warning(
                    "[agent-session-cleanup] ORM delete failed for %s: %s",
                    session_id_str[:20],
                    del_err,
                )

    # === Per-status drift pre-scan (issue #1361) ===
    # `repair_indexes()` only counts phantoms (members whose hash is gone).
    # Drift members — hash present, but `status` field disagrees with the
    # index segment — slip past it. Pre-scan counts those per status so the
    # `agent_session.indexed_field.stale_members` metric is observable.
    # Pre-scan failure is logged as WARNING and is non-fatal: the cleanup
    # function MUST continue to repair_indexes() regardless.
    per_status_drift: dict[str, int] = {}
    try:
        per_status_drift = _count_per_status_stale_index_members()
    except Exception as scan_err:
        logger.warning(
            "[agent-session-cleanup] Per-status stale index pre-scan failed (non-fatal): %s",
            scan_err,
            exc_info=True,
        )
        per_status_drift = {}

    # Emit per-status drift metrics. record_metric is best-effort internally
    # (analytics/collector.py wraps every backend write in try/except), but
    # we wrap again to defend against any future contract change.
    for status, count in per_status_drift.items():
        if count <= 0:
            continue
        try:
            record_metric(
                "agent_session.indexed_field.stale_members",
                count,
                {"status": status},
            )
        except Exception as metric_err:
            logger.warning(
                "[agent-session-cleanup] record_metric failed for status=%s "
                "count=%d (non-fatal): %s",
                status,
                count,
                metric_err,
            )

    # === Unconditional repair_indexes() (issue #1361) ===
    # PR #1078 introduced a `cleaned > 0 or phantoms_filtered > 0` gate here.
    # The gate prevented `repair_indexes()` from ever flushing genuine drift
    # members for which the underlying hash was fine. Issue #1361 removes
    # the gate permanently — `repair_indexes()` is idempotent on a clean DB,
    # the per-tick cost is negligible, and the durable safety covers any
    # future drift source (not just pre-`615eab9c` residue).
    try:
        phantoms_cleared, sessions_rebuilt = AgentSession.repair_indexes()
        if phantoms_cleared or sessions_rebuilt or cleaned or phantoms_filtered or per_status_drift:
            logger.info(
                "[agent-session-cleanup] repair_indexes: phantoms_cleared=%d "
                "(hash missing), sessions_rebuilt=%d, drift_per_status=%s "
                "(hash present, status mismatched), cleaned=%d corrupt, "
                "phantoms_filtered=%d. phantoms_cleared and drift_per_status "
                "are independent counters.",
                phantoms_cleared,
                sessions_rebuilt,
                per_status_drift or {},
                cleaned,
                phantoms_filtered,
            )
        else:
            logger.debug("[agent-session-cleanup] repair_indexes: no drift, no phantoms")
    except Exception as idx_err:
        logger.warning("[agent-session-cleanup] Index repair failed: %s", idx_err)

    # === Class-set orphan cleanup (#1459) ===
    # repair_indexes() covers $IndexF (field/status indexes) but never touches
    # the class set ($Idx:AgentSession). TTL expiry removes the hash but not
    # its class-set member, causing continuous Sentry noise. clean_indexes()
    # uses SSCAN (production-safe) to remove stale class-set entries.
    for model_cls, model_label in ((AgentSession, "AgentSession"), (Memory, "Memory")):
        try:
            orphans_removed = model_cls.clean_indexes()
            if orphans_removed:
                logger.info(
                    "[agent-session-cleanup] clean_indexes %s: removed %d orphan class-set entries",
                    model_label,
                    orphans_removed,
                )
        except Exception as ci_err:
            logger.warning(
                "[agent-session-cleanup] clean_indexes %s failed (non-fatal): %s",
                model_label,
                ci_err,
            )

    # === Cross-process orphan reap pass (#1271) ===
    # Wrapped in try/except — reaper failure must never abort corrupted-record
    # cleanup. On reaper exception, log WARNING and report orphans=0.
    orphans_reaped = 0
    try:
        orphans_reaped = _reap_orphan_session_processes()
    except Exception as reap_err:
        logger.warning(
            "[agent-session-cleanup] Orphan-process reaper raised (non-fatal): %s",
            reap_err,
            exc_info=True,
        )
        orphans_reaped = 0

    # === Telemetry retention sweep ===
    # Delete JSONL trace files older than 14 days.  Wrapped in try/except so
    # a filesystem error never aborts the main cleanup function.
    try:
        from agent.session_telemetry import _get_telemetry_dir

        retention_days = 14
        cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
        telemetry_dir = _get_telemetry_dir()
        stale_count = 0
        for jsonl_file in telemetry_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=UTC)
                if mtime < cutoff:
                    jsonl_file.unlink()
                    stale_count += 1
            except Exception:
                pass
        if stale_count:
            logger.info(
                "[session-health] Deleted %d stale telemetry traces (older than %d days)",
                stale_count,
                retention_days,
            )
    except Exception as sweep_err:
        logger.warning(
            "[session-health] Telemetry retention sweep failed (non-fatal): %s",
            sweep_err,
        )

    return {"corrupted": cleaned, "orphans": orphans_reaped}


def _psutil_process_for_pid(pid: int):
    """Construct a ``psutil.Process(pid)`` or return None on lookup failure.

    Wrapped in a function to make patching from tests trivial. Issue #1271.
    """
    try:
        import psutil

        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception:
        return None


def _is_stale_print_oneshot(cmdline: list, create_time: float) -> bool:
    """True if ``cmdline`` is a `claude --print` one-shot older than the threshold.

    Issue #1632 mode 1(a). Matches a `claude` executable (bare on PATH or any
    absolute path, including the bundled SDK path) running in one-shot mode
    (`--print` or its `-p` alias) whose age exceeds
    ``ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS``.

    Deliberately narrow:
      - argv[0] (or argv[1], for `node /path/claude` shapes) must have
        basename exactly ``claude`` — `python -m worker` can never match.
      - interactive `claude` (no `--print`/`-p` token) never matches.
      - ``create_time`` of 0/None (unknown) is treated as NOT stale.
    """
    if not cmdline:
        return False
    head = [str(x) for x in cmdline[:2]]
    if not any(tok.rsplit("/", 1)[-1] == "claude" for tok in head):
        return False
    args = [str(x) for x in cmdline[1:]]
    if "--print" not in args and "-p" not in args:
        return False
    if not create_time:
        return False
    try:
        age = time.time() - float(create_time)
    except (TypeError, ValueError):
        return False
    return age > ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS


def _parent_is_orphaned_shell_wrapper(ppid: int) -> bool:
    """True if PID ``ppid`` is a live `sh -c`-style wrapper whose own PPID==1.

    Issue #1632 mode 1(b): when a session process dies, its `zsh -c` Bash-tool
    wrappers reparent to launchd (PPID==1) but stay alive, blocked waiting on
    their child forever. A claude/MCP/pytest child under such a wrapper is an
    orphan even though its immediate parent is technically alive — the chain
    above it is dead.

    Only consults the single parent level; failure of any psutil call returns
    False (keep the child — conservative default).
    """
    if not ppid or ppid <= 1:
        return False
    parent = _psutil_process_for_pid(ppid)
    if parent is None:
        return False
    try:
        if parent.ppid() != 1:
            return False
        pcmd = [str(x) for x in (parent.cmdline() or [])]
    except Exception:
        return False
    if len(pcmd) < 2:
        return False
    exe = pcmd[0].rsplit("/", 1)[-1]
    return exe in _SHELL_WRAPPER_NAMES and "-c" in pcmd[1:3]


def _session_is_alive(session) -> bool:
    """Return True if the owning session's heartbeat is fresh enough to skip.

    Decision matrix (issue #1271):
      - status in TERMINAL_STATUSES → False (kill the orphan)
      - last_heartbeat_at is None → False (never heartbeated)
      - (now - last_heartbeat_at) < 30min → True (alive, skip)
      - else → False (stale, kill)
    """
    status = getattr(session, "status", None)
    if status in _TERMINAL_STATUSES:
        return False
    hb = getattr(session, "last_heartbeat_at", None)
    if hb is None:
        return False
    try:
        if isinstance(hb, datetime):
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - hb).total_seconds()
        else:
            age = time.time() - float(hb)
    except Exception:
        return False
    return age < ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS


def _increment_orphan_process_counter(session) -> None:
    """Increment the appropriate orphan-reap counter (issue #1271).

    Known owning session: ``{project_key}:session-health:orphan_process_reaped``.
    Unknown:              ``session-health:orphan_process_reaped:{hostname}``.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        if session is not None:
            project_key = getattr(session, "project_key", None) or "unknown"
            _R.incr(f"{project_key}:session-health:orphan_process_reaped")
        else:
            _R.incr(f"session-health:orphan_process_reaped:{_ORPHAN_REAP_HOSTNAME}")
    except Exception as e:
        logger.debug("[orphan-reap] counter increment failed (non-fatal): %s", e)


def _reap_orphan_session_processes() -> int:
    """Scan the OS process table for PPID==1 orphans matching Claude/MCP signatures.

    Issue #1271. This is the cross-process orphan reaper. It complements:
      - ``cleanup_corrupted_agent_sessions()`` — corrupted DB-row reaper
      - ``_pending_sigkill`` reap inside ``_agent_session_health_check`` (#1218)
        — in-process map scan

    Algorithm:
      0. Honor ``DISABLE_ORPHAN_PROCESS_REAP=1`` kill switch (early return 0).
      1. Drain ``_pending_sigkill_orphans``: for each ``(pid, staged_create_time)``,
         construct ``psutil.Process(pid)`` and verify ``proc.create_time() ==
         staged_create_time`` BEFORE issuing SIGKILL. Skip on mismatch.
      2. Build ``skip_pids`` from ``os.getpid()`` + all
         ``worker:registered_pid:*`` Redis values.
      3. Iterate ``psutil.process_iter`` with per-iteration try/except. For
         each process whose PPID==1 AND cmdline matches the Claude or MCP
         regex AND PID not in ``skip_pids``: per-PID heartbeat gate, then
         capture descendants, terminate parent + descendants, stage tuples.
      4. Counter scheme via ``_increment_orphan_process_counter``.

    Returns the number of *parent* kills (descendants are bookkeeping only).
    """
    if os.environ.get("DISABLE_ORPHAN_PROCESS_REAP") == "1":
        logger.debug("[orphan-reap] Disabled via DISABLE_ORPHAN_PROCESS_REAP=1")
        return 0

    try:
        import psutil
    except ImportError:
        logger.warning("[orphan-reap] psutil unavailable — skipping reap pass")
        return 0

    # === Step 1: Drain staged SIGKILL queue with create-time verification ===
    staged = list(_pending_sigkill_orphans)
    _pending_sigkill_orphans.clear()
    for pid, staged_create_time in staged:
        proc = _psutil_process_for_pid(pid)
        if proc is None:
            logger.debug("[orphan-reap] Drain: PID %d already gone, skip SIGKILL", pid)
            continue
        try:
            current_ct = proc.create_time()
        except Exception as e:
            logger.debug("[orphan-reap] Drain: create_time() failed for PID %d: %s", pid, e)
            continue
        if abs(current_ct - staged_create_time) > 1e-3:
            logger.debug(
                "[orphan-reap] Drain: PID %d recycled (create_time %f != %f), skip",
                pid,
                current_ct,
                staged_create_time,
            )
            continue
        try:
            proc.kill()
            logger.info("[orphan-reap] Drain: SIGKILL'd PID %d (escalation)", pid)
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            logger.debug("[orphan-reap] Drain: kill() failed for PID %d: %s", pid, e)

    # === Step 2: Build skip_pids (positive-ID self-protection) ===
    skip_pids: set[int] = {os.getpid()}
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        for k in _R.scan_iter(f"{WORKER_REGISTERED_PID_KEY_PREFIX}*"):
            try:
                v = _R.get(k)
                if v is None:
                    continue
                skip_pids.add(int(v))
            except (ValueError, TypeError):
                continue
    except Exception as e:
        logger.debug("[orphan-reap] skip_pids Redis scan failed (non-fatal): %s", e)

    # === Step 3: Iterate process table ===
    parent_kills = 0
    try:
        proc_iter_raw = psutil.process_iter(["pid", "ppid", "cmdline", "create_time"])
    except Exception as e:
        logger.warning("[orphan-reap] process_iter failed: %s", e)
        return 0

    # Coerce to a real iterator so ``next()`` works for both psutil's generator
    # and test mocks that return a list.
    proc_iter = iter(proc_iter_raw)
    while True:
        try:
            proc = next(proc_iter)
        except StopIteration:
            break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug("[orphan-reap] iter exception (continuing): %s", e)
            continue
        except Exception as e:
            logger.debug("[orphan-reap] iter unexpected exception: %s", e)
            break

        try:
            info = proc.info or {}
            pid = info.get("pid")
            ppid = info.get("ppid")
            cmdline = info.get("cmdline") or []
            create_time = info.get("create_time") or 0.0

            if pid is None or ppid is None:
                continue
            if pid in skip_pids:
                continue
            if not cmdline:
                continue

            cmdline_str = " ".join(str(x) for x in cmdline)
            is_claude = bool(_CLAUDE_CMDLINE_RE.search(cmdline_str))
            is_mcp = bool(_MCP_SERVER_CMDLINE_RE.search(cmdline_str))
            is_stale_oneshot = _is_stale_print_oneshot(cmdline, create_time)
            if not (is_claude or is_mcp or is_stale_oneshot):
                continue

            # Orphan gate: PPID==1, OR (issue #1632 mode 1b) the immediate
            # parent is itself an orphaned (PPID==1) `sh -c`/`zsh -c` wrapper —
            # alive only because it is blocked waiting on this child forever.
            # The wrapper lookup runs only for signature-matched processes, so
            # the per-tick psutil cost is a handful of parent reads at most.
            if ppid != 1 and not _parent_is_orphaned_shell_wrapper(ppid):
                continue

            # === Per-PID heartbeat gate ===
            session = AgentSession.find_by_claude_pid(pid)
            if session is None and is_mcp:
                # MCP servers don't have a direct claude_pid mapping. Try the
                # parent: if it resolves to a live session, inherit that decision.
                try:
                    parent = proc.parent()
                    if parent is not None:
                        session = AgentSession.find_by_claude_pid(parent.pid)
                except Exception as e:
                    logger.debug(
                        "[orphan-reap] proc.parent() lookup failed for PID %d: %s",
                        pid,
                        e,
                    )

            if is_stale_oneshot:
                # Fast-kill signature (issue #1632 mode 1a): no legitimate
                # `--print` one-shot lives this long. The heartbeat gate is
                # intentionally bypassed — an alive owning session does not
                # legitimize a stuck one-shot child.
                logger.info(
                    "[orphan-reap] Stale --print one-shot PID %d (age > %ds) — fast-kill",
                    pid,
                    ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS,
                )
            elif session is not None and _session_is_alive(session):
                logger.debug("[orphan-reap] Skip PID %d — owning session is alive", pid)
                continue

            # === Capture descendants BEFORE killing the parent ===
            descendants: list = []
            try:
                descendants = list(proc.children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                descendants = []
            except Exception as e:
                logger.debug("[orphan-reap] children() failed for PID %d: %s", pid, e)
                descendants = []

            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                continue
            except Exception as e:
                logger.debug("[orphan-reap] terminate() parent PID %d failed: %s", pid, e)
                continue

            _pending_sigkill_orphans.add((pid, create_time))
            for d in descendants:
                d_pid = None
                d_ct = 0.0
                try:
                    d_pid = d.pid
                    d_ct = d.create_time()
                    d.terminate()
                except psutil.NoSuchProcess:
                    continue
                except Exception as e:
                    logger.debug("[orphan-reap] terminate() descendant failed: %s", e)
                    continue
                if d_pid is not None:
                    _pending_sigkill_orphans.add((d_pid, d_ct))

            parent_kills += 1
            owning_id = getattr(session, "agent_session_id", None) if session else None
            logger.info(
                "[orphan-reap] Killed PID %d (cmd=%s, owning_session=%s, descendants=%d)",
                pid,
                cmdline_str[:100],
                owning_id or "<unknown>",
                len(descendants),
            )

            _increment_orphan_process_counter(session)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug("[orphan-reap] per-PID exception (continuing): %s", e)
            continue
        except Exception as e:
            logger.debug("[orphan-reap] per-PID unexpected exception: %s", e)
            continue

    return parent_kills


def _fast_reap_stale_print_oneshots() -> int:
    """Fast-cadence reaper for stale `claude --print` one-shots (issue #1632 mode 1c).

    A trimmed-down sibling of ``_reap_orphan_session_processes`` applying ONLY
    the fast-kill signature (``_is_stale_print_oneshot`` + PPID==1). It runs
    from the worker's ``_agent_session_health_loop`` every
    ``AGENT_SESSION_HEALTH_CHECK_INTERVAL`` seconds because the hourly
    `agent-session-cleanup` reflection is far too slow for a
    multiple-spawns-per-minute orphan cascade (observed: ~4/min, ~250 MB each).

    Deliberately minimal surface:
      - No heartbeat gate, no Redis skip-set scan, no descendant walk — the
        signature alone is decisive, and a `--print` one-shot has no useful
        descendants. ``os.getpid()`` is still skipped, and the signature
        cannot match `python -m worker` (argv[0] basename must be `claude`).
      - Escalation: first sighting → SIGTERM + stage ``(pid, create_time)``
        into ``_pending_sigkill_orphans``; if the same tuple is sighted again
        on a later pass → SIGKILL (the create_time match guards against PID
        recycling, same contract as the hourly drain).
      - Fail-silent: every failure path logs at DEBUG and the function never
        raises — the health loop must not be destabilized by a reap pass.

    Returns the number of processes acted on (TERM or KILL).
    """
    if os.environ.get("DISABLE_ORPHAN_PROCESS_REAP") == "1":
        return 0

    reaped = 0
    try:
        import psutil

        proc_iter = iter(psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]))
        self_pid = os.getpid()
        while True:
            try:
                proc = next(proc_iter)
            except StopIteration:
                break
            except Exception as e:
                logger.debug("[fast-oneshot-reap] iter exception (stopping): %s", e)
                break

            try:
                info = proc.info or {}
                pid = info.get("pid")
                ppid = info.get("ppid")
                cmdline = info.get("cmdline") or []
                create_time = info.get("create_time") or 0.0

                if pid is None or pid == self_pid or ppid != 1:
                    continue
                if not _is_stale_print_oneshot(cmdline, create_time):
                    continue

                staged = (pid, create_time)
                if staged in _pending_sigkill_orphans:
                    proc.kill()
                    _pending_sigkill_orphans.discard(staged)
                    logger.info(
                        "[fast-oneshot-reap] SIGKILL'd surviving stale one-shot PID %d", pid
                    )
                else:
                    proc.terminate()
                    _pending_sigkill_orphans.add(staged)
                    logger.info(
                        "[fast-oneshot-reap] SIGTERM'd stale --print one-shot PID %d (cmd=%s)",
                        pid,
                        " ".join(str(x) for x in cmdline)[:100],
                    )
                reaped += 1
                _increment_orphan_process_counter(None)
            except Exception as e:
                logger.debug("[fast-oneshot-reap] per-PID exception (continuing): %s", e)
                continue
    except Exception as e:
        logger.debug("[fast-oneshot-reap] pass failed (non-fatal): %s", e)
    return reaped


def _cleanup_orphaned_claude_processes() -> int:
    """Backward-compat shim for the cross-process orphan reaper (issue #1271).

    Originally this function used ``pgrep`` + ``ps`` to scan for orphan
    ``claude_agent_sdk/_bundled/claude`` processes. As of #1271 it is replaced
    by ``_reap_orphan_session_processes()`` which uses psutil, walks descendant
    trees, applies a per-PID heartbeat gate via
    ``AgentSession.find_by_claude_pid()``, and applies positive-ID
    self-protection from ``worker:registered_pid:*`` keys.

    Kept as a shim so the existing startup wiring in ``worker/__main__.py``
    (``orphans_killed = _cleanup_orphaned_claude_processes()``) continues to
    work without rewiring tests.
    """
    return _reap_orphan_session_processes()
