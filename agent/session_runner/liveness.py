"""Single authoritative ``sdk_ever_output`` derivation (owner directive, 2026-07-07).

Owned by the runner package — which already owns subprocess spawn/kill
(``runner.py``) — per Tom's design directive: *"One authoritative liveness
signal makes the most sense. As much as we can strengthen a single module,
let's do that instead of manipulating the worker."*

``agent/session_health.py`` (the worker) imports :func:`derive_sdk_ever_output`
and calls it at all four of its recovery-path derivation sites rather than
inlining the OR expression itself. See
``docs/plans/headless-runner-zombie-liveness.md`` for the full root-cause
analysis and the four call sites this feeds.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any


def derive_sdk_ever_output(entry: Any) -> bool:
    """Return True iff the SDK has EVER produced recognized output.

    True when any of three per-turn/per-stream fields on the ``AgentSession``
    (or session-health entry proxy) ``entry`` is set:

    - ``last_tool_use_at`` — a tool boundary fired (PreToolUse/PostToolUse
      CLI hooks).
    - ``last_turn_at`` — a turn boundary completed (harness ``result`` event,
      via ``agent.hooks.liveness_writers.record_turn_boundary``).
    - ``last_stdout_at`` — the headless stream produced any output at all
      (``init`` event or subsequent stdout line, via
      ``SessionRunner._stamp_stdout_liveness``). This is the headless
      replacement for the PTY-era ``last_pty_read_loop_at`` liveness signal
      that the granite teardown dropped (#1843 Gap B).

    This is a presence check, not a freshness check — it answers "has the
    SDK EVER produced output," which is what the never-started gate and the
    reprieve-cap guard both need. Freshness-based mid-turn cadence checks
    (e.g. ``_has_progress`` sub-check A) are a separate, untouched concern.

    Never raises: missing attributes default to ``None`` via ``getattr``.
    """
    return bool(
        getattr(entry, "last_tool_use_at", None)
        or getattr(entry, "last_turn_at", None)
        or getattr(entry, "last_stdout_at", None)
    )


def _read_field(entry: Any, name: str) -> Any:
    """Read ``name`` from a dict-style or attribute-style entry.

    Both ``_has_demonstrable_progress`` forks historically read AgentSession
    objects via ``getattr(..., None)``; dict entries are accepted so the leaf
    is pure over either shape. Missing fields default to ``None``.
    """
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _as_unix_ts(val: Any) -> float | None:
    """Coerce a datetime / int / float / ISO-string to a Unix timestamp.

    Mirrors ``bridge.utc.to_unix_ts`` semantics (naive datetimes are treated
    as UTC — Popoto strips tzinfo on save) without importing it: this module
    stays stdlib-only so ``agent/crash_signature.py`` can import it where it
    deliberately cannot import ``agent/session_health.py``. Returns ``None``
    when the value cannot be coerced.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    return None


def has_demonstrable_activity(entry: Any, *, freshness_window: float | None = None) -> bool:
    """Return True iff the entry's own fields prove it has taken a turn or used a tool.

    The consolidated leaf for the two ``_has_demonstrable_progress`` forks
    (#2004 Task 2): ``agent/session_stall_classifier.py`` (freshness-windowed,
    passes ``IDLE_SUSPECT_SECS``) and ``agent/crash_signature.py``
    (presence-only, passes ``None``). Reads ONLY ``{turn_count,
    last_tool_use_at}`` — the exact subset both forks already used.

    **B1 guard:** ``log_path`` / ``claude_session_uuid`` / ``last_stdout_at``
    / ``last_turn_at`` are deliberately NOT presence signals here — an
    init-only/log-only session must read no-progress for the stall/crash
    paths. (``session_health`` has its own wider leaf,
    :func:`derive_sdk_ever_output`, for the started-vs-never-started axis.)

    Semantics:

    - ``turn_count > 0`` (int) → progress. A numeric-string ``turn_count``
      is coerced defensively (``int(turn_count) > 0``) — parity ported from
      the crash_signature fork for a real persisted shape.
    - ``last_tool_use_at`` with ``freshness_window=None`` → presence-only:
      any recorded tool use is progress. The crash extractor runs over
      already-terminal sessions inside a lookback reflection, so a wall-clock
      window would read stale/False for exactly the sessions it rescues.
    - ``last_tool_use_at`` with a numeric ``freshness_window`` (seconds) →
      progress iff ``now - ts < freshness_window``. The stall classifier runs
      live and gates on ``IDLE_SUSPECT_SECS`` to catch *currently* stalled
      sessions. The window is arithmetic on the caller-supplied value; this
      module holds no freshness policy of its own.

    Never raises: ``None`` / missing / malformed fields read as no-progress.
    """
    if entry is None:
        return False
    try:
        turn_count = _read_field(entry, "turn_count")
        if isinstance(turn_count, int) and turn_count > 0:
            return True
        if isinstance(turn_count, str):
            try:
                if int(turn_count) > 0:
                    return True
            except (TypeError, ValueError):
                pass

        last_tool_use_at = _read_field(entry, "last_tool_use_at")
        if freshness_window is None:
            return last_tool_use_at is not None
        ts = _as_unix_ts(last_tool_use_at)
        if ts is not None and (time.time() - ts) < freshness_window:
            return True
    except Exception:  # noqa: BLE001 — never-raises contract (fail-soft to no-progress)
        return False
    return False


# ---------------------------------------------------------------------------
# Short-term subprocess-hang probe (2026-07-13)
# ---------------------------------------------------------------------------
#
# Motivation. The never-started grace window was widened to ~20 min so that a
# genuinely-slow Opus cold start (15-20 min TTFT, MCP-fleet boot) is no longer
# killed at 150s. Output silence can no longer be trusted as a timely death
# signal inside that window, so we need a DIFFERENT, faster way to tell a
# working cold start from a real hang — one that does not wait for the model to
# emit its first token.
#
# The probe reads the subprocess tree directly (no model output required) and
# classifies each poll as:
#
#   "progressing" — POSITIVE liveness evidence: a live child process (tool /
#                   MCP subprocess), advancing CPU time, or an ESTABLISHED
#                   outbound HTTPS connection (a model call in flight — the CPU
#                   is legitimately idle while awaiting the first token). Any
#                   one reprieves the session for the full widened window.
#   "hung"        — POSITIVE hang evidence: the process is alive but has burned
#                   ZERO CPU, holds NO children, and — with its sockets
#                   readable — has NO established HTTPS connection, sustained
#                   past HANG_CONFIRM_SAMPLES flat polls (the sequence is
#                   baseline → grace → hung, so with the default of 2 the
#                   verdict lands on the third flat poll ≈ 90s at the 30s
#                   owned-task cadence) rather than waiting out the 20-min
#                   output window.
#   "unknown"     — no evidence either way (no pid, psutil unavailable, sockets
#                   unreadable while CPU is flat, or the first sample of a new
#                   session). Callers fall back to their existing behavior; we
#                   never declare "hung" on an inconclusive read.
#
# Philosophy (issue #1172): kill only on EVIDENCE of a hang, never on the mere
# absence of expected output. The socket check is what prevents a false hang
# during the legitimate first-token network wait — if sockets cannot be read,
# the verdict degrades to "unknown", not "hung".

# Consecutive flat-CPU polls (no children, no API socket) required before a
# live subprocess is declared hung. Because probe state is keyed by
# ``(session_key, caller)``, each caller's flat-count is a pure function of ITS
# OWN poll cadence — for the 30s owned-task loop this is ``baseline → grace →
# hung`` i.e. detection on the third flat poll (~90s). Env-tunable.
HANG_CONFIRM_SAMPLES: int = int(os.environ.get("HANG_CONFIRM_SAMPLES", "2"))

# CPU-seconds delta above which the process tree counts as "doing work" between
# two polls. Small but non-zero to absorb accounting jitter.
_CPU_PROGRESS_EPSILON: float = 0.05

# Remote ports that count as an outbound model/API call in flight. Env-tunable
# (comma-separated) so a fleet that routes cold-start traffic through a
# non-443 proxy / base-URL / local model can register its port and avoid a
# false hang during that endpoint's first-token wait.
_API_REMOTE_PORTS: frozenset[int] = frozenset(
    int(p)
    for p in os.environ.get("HANG_PROBE_API_PORTS", "443,8443").split(",")
    if p.strip().isdigit()
)

# (session_key, caller) -> (pid, last_tree_cpu_seconds, consecutive_flat_polls).
# Module-level so the CPU delta and flat-run survive across polls. Keyed by
# caller as well as session so the two probers (the 30s owned-task loop and the
# health-check reprieve gate, which both fire for a locally-owned session past
# 300s) keep independent baselines and flat-counts — otherwise one poller's
# increment would perturb the other's confirmation latency. The pid is stored
# so a session that recovers and respawns a NEW subprocess re-baselines instead
# of comparing CPU across two unrelated processes. Cleared on any progressing
# signal, on process exit, and by ``clear_hang_state``.
_hang_samples: dict[tuple[str, str], tuple[int, float, int]] = {}


def clear_hang_state(session_key: str) -> None:
    """Drop all accumulated hang-probe state for ``session_key`` (every caller).

    Callers invoke this when a session terminates or is recovered so a later
    session reusing the id never inherits a stale CPU baseline / flat-run, and
    so the long-lived worker process does not accumulate one entry per session
    forever. Clears every ``(session_key, caller)`` variant.
    """
    for key in [k for k in _hang_samples if k[0] == session_key]:
        _hang_samples.pop(key, None)


def _tree_cpu_seconds(proc: Any, psutil: Any) -> float | None:
    """Sum user+system CPU seconds across ``proc`` and its descendants.

    Returns ``None`` if the root process's own CPU times cannot be read (dead /
    access-denied) — an unreadable root makes the whole delta meaningless.
    Individually-unreadable children are skipped, not fatal.
    """
    try:
        total = float(sum(proc.cpu_times()[:2]))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    except Exception:  # noqa: BLE001 — fail-soft
        return None
    try:
        children = proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    except Exception:  # noqa: BLE001
        children = []
    for child in children:
        try:
            total += float(sum(child.cpu_times()[:2]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:  # noqa: BLE001, S112
            continue
    return total


def _tree_has_api_socket(proc: Any, psutil: Any) -> bool | None:
    """Whether ``proc`` or a descendant holds an ESTABLISHED outbound HTTPS conn.

    Returns ``True`` if such a connection exists, ``False`` if connections were
    readable but none qualify, and ``None`` if NO process in the tree exposed
    its connections (unreadable → inconclusive, must not feed a hang verdict).
    """
    procs = [proc]
    try:
        procs += proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except Exception:  # noqa: BLE001, S110
        pass
    readable = False
    for p in procs:
        conns_fn = getattr(p, "net_connections", None) or getattr(p, "connections", None)
        if conns_fn is None:
            continue
        try:
            conns = conns_fn(kind="inet")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:  # noqa: BLE001, S112
            continue
        readable = True
        for c in conns:
            raddr = getattr(c, "raddr", None)
            if (
                getattr(c, "status", None) == psutil.CONN_ESTABLISHED
                and raddr
                and getattr(raddr, "port", None) in _API_REMOTE_PORTS
            ):
                return True
    return False if readable else None


def subprocess_hang_verdict(
    pid: int | None, session_key: str, *, caller: str = ""
) -> tuple[str, str | None]:
    """Classify a subprocess as progressing / hung / unknown (see module notes).

    ``pid`` is the harness subprocess pid (``SessionHandle.pid``); ``session_key``
    plus ``caller`` key the per-session CPU baseline so distinct pollers (e.g.
    the owned-task loop vs. the health-check reprieve gate) accumulate
    independent flat-counts. Returns ``(verdict, gate)`` where ``verdict`` is
    one of ``"progressing"``, ``"hung"``, ``"unknown"`` and ``gate`` names the
    deciding signal for telemetry. Never raises.
    """
    state_key = (session_key, caller)
    if pid is None:
        return ("unknown", None)
    try:
        import psutil
    except ImportError:
        return ("unknown", None)
    try:
        proc = psutil.Process(pid)
        status = proc.status()
    except psutil.NoSuchProcess:
        # The subprocess is GONE (pid no longer exists). This is a death, not a
        # slow cold start — treat as hung so the caller recovers rather than
        # reprieves. Clear stale CPU state.
        clear_hang_state(session_key)
        return ("hung", "gone")
    except psutil.AccessDenied:
        # The process exists but is unreadable (perms) — inconclusive, never a
        # false hang; the caller falls back to its own bounded reprieve logic.
        return ("unknown", None)
    except Exception:  # noqa: BLE001
        return ("unknown", None)

    if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, psutil.STATUS_STOPPED):
        clear_hang_state(session_key)
        return ("hung", "dead")

    # A live child (tool / MCP subprocess) is the strongest progress signal.
    try:
        if proc.children():
            clear_hang_state(session_key)
            return ("progressing", "children")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except Exception:  # noqa: BLE001, S110
        pass

    cpu_now = _tree_cpu_seconds(proc, psutil)
    if cpu_now is None:
        return ("unknown", None)

    prev = _hang_samples.get(state_key)
    if prev is None or prev[0] != pid:
        # First observation for this (session, caller, pid) — establish a
        # baseline and give the benefit of the doubt. A pid change means the
        # session recovered and respawned, so the old CPU baseline is
        # meaningless and must be reset rather than compared across processes.
        _hang_samples[state_key] = (pid, cpu_now, 0)
        return ("progressing", "cpu_baseline")

    _prev_pid, prev_cpu, flat = prev
    if cpu_now - prev_cpu > _CPU_PROGRESS_EPSILON:
        _hang_samples[state_key] = (pid, cpu_now, 0)
        return ("progressing", "cpu")

    # CPU flat. Before declaring a hang, prove there is no model call in
    # flight — otherwise the legitimate first-token network wait looks hung.
    api = _tree_has_api_socket(proc, psutil)
    if api is True:
        _hang_samples[state_key] = (pid, cpu_now, 0)
        return ("progressing", "api")
    if api is None:
        # Sockets unreadable → cannot disprove a network wait → inconclusive.
        return ("unknown", None)

    flat += 1
    _hang_samples[state_key] = (pid, cpu_now, flat)
    if flat >= HANG_CONFIRM_SAMPLES:
        return ("hung", "flat_cpu_no_api")
    return ("progressing", "cpu_flat_grace")
