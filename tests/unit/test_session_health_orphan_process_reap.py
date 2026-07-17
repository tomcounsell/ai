"""Unit tests for the cross-process orphan reaper (issue #1271).

This complements the in-process orphan reap from PR #1236 (issue #1218).
Where #1218 scans the ``_active_sessions`` map, this reaper scans the
**OS process table** for processes whose PPID==1 and whose cmdline matches
``claude_agent_sdk/_bundled/claude`` or ``mcp_servers/*``.

Coverage matrix (9 scenarios + 3 invariants = 12 cases).
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil
import pytest

import agent.session_health as session_health


@pytest.fixture
def clean_state():
    """Reset module-level state and Redis registered-pid keys between tests."""
    saved = set(session_health._pending_sigkill_orphans)
    session_health._pending_sigkill_orphans.clear()
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        for k in list(_R.scan_iter("worker:registered_pid:test-*")):
            _R.delete(k)
    except Exception:
        pass
    yield
    session_health._pending_sigkill_orphans.clear()
    session_health._pending_sigkill_orphans.update(saved)
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        for k in list(_R.scan_iter("worker:registered_pid:test-*")):
            _R.delete(k)
    except Exception:
        pass


def _fake_proc(
    *,
    pid: int,
    ppid: int = 1,
    cmdline=None,
    create_time: float = 1000.0,
    children=None,
    parent_pid: int | None = None,
):
    """Build a fake psutil.Process-like object.

    The default cmdline mirrors the real `claude` CLI invocation (psutil's
    cmdline returns argv with the absolute path to the bundled binary).
    """
    cmd = cmdline or [
        "/usr/local/lib/node_modules/@anthropic-ai/claude-code/claude_agent_sdk/_bundled/claude",
        "-p",
    ]
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.info = {"pid": pid, "ppid": ppid, "cmdline": cmd, "create_time": create_time}
    proc.ppid.return_value = ppid
    proc.cmdline.return_value = cmd
    proc.create_time.return_value = create_time
    proc.children.return_value = children or []
    if parent_pid is not None:
        parent = MagicMock(spec=psutil.Process)
        parent.pid = parent_pid
        proc.parent.return_value = parent
    else:
        proc.parent.return_value = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = MagicMock()
    return proc


# -----------------------------------------------------------------------------
# Scenario tests
# -----------------------------------------------------------------------------


class TestOrphanProcessReap:
    def test_1_healthy_claude_ppid_not_1_not_killed(self, clean_state):
        """PPID != 1 means the parent is alive — never a candidate."""
        proc = _fake_proc(pid=2000, ppid=12345)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()

    def test_2_orphan_claude_no_owning_session_killed(self, clean_state):
        """Orphan claude (PPID==1) with no owning session → terminated; descendants captured."""
        child = _fake_proc(
            pid=2001,
            ppid=2000,
            cmdline=["python", "/path/to/mcp_servers/memory_server.py"],
        )
        proc = _fake_proc(pid=2000, ppid=1, children=[child])

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()
        child.terminate.assert_called_once()
        # Both PIDs (parent + descendant) staged for SIGKILL drain next tick
        staged_pids = {p for p, _ in session_health._pending_sigkill_orphans}
        assert 2000 in staged_pids
        assert 2001 in staged_pids

    def test_3_orphan_claude_with_fresh_heartbeat_skipped(self, clean_state):
        """Orphan claude whose owning session has a fresh heartbeat → skipped.

        Uses a recent create_time: the default `-p` cmdline plus an ancient
        create_time would legitimately trip the issue #1632 fast-kill
        signature, which intentionally bypasses the heartbeat gate. This test
        exercises the gate itself, so the process must be younger than
        ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS.
        """
        proc = _fake_proc(pid=2002, ppid=1, create_time=time.time() - 60)
        live_session = SimpleNamespace(
            project_key="proj-a",
            status="running",
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=30),
            claude_pid=2002,
        )

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession,
                "find_by_claude_pid",
                return_value=live_session,
            ):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()

    def test_4_live_registered_worker_never_reaped(self, clean_state):
        """A worker PID in the registered set is never reaped, even if PPID==1.

        Exercises positive-ID self-protection: even if a future code change
        adds the worker pattern to the regex set AND the worker has PPID==1
        (the design under launchd KeepAlive=true), the worker must survive.
        """
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        worker_pid = 99988
        _R.set(f"worker:registered_pid:test-{worker_pid}", worker_pid, ex=86400)

        worker_proc = _fake_proc(
            pid=worker_pid,
            ppid=1,
            cmdline=["python", "-m", "worker"],
        )
        orphan_proc = _fake_proc(pid=2003, ppid=1)

        with patch.object(psutil, "process_iter", return_value=[worker_proc, orphan_proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(
                    session_health, "_psutil_process_for_pid", return_value=orphan_proc
                ):
                    killed = session_health._reap_orphan_session_processes()

        worker_proc.terminate.assert_not_called()
        assert killed == 1
        orphan_proc.terminate.assert_called_once()

    def test_5_orphan_mcp_no_claude_pid_mapping_killed(self, clean_state):
        """Orphan MCP server with no direct mapping AND no live parent → killed."""
        mcp_proc = _fake_proc(
            pid=2004,
            ppid=1,
            cmdline=["python", "/path/to/mcp_servers/memory_server.py"],
            parent_pid=None,
        )

        with patch.object(psutil, "process_iter", return_value=[mcp_proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=mcp_proc):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        mcp_proc.terminate.assert_called_once()

    def test_6_orphan_mcp_with_live_parent_skipped(self, clean_state):
        """Orphan MCP whose parent.pid maps to a live session → skipped."""
        mcp_proc = _fake_proc(
            pid=2005,
            ppid=1,
            cmdline=["python", "/path/to/mcp_servers/memory_server.py"],
            parent_pid=3000,
        )
        live_session = SimpleNamespace(
            project_key="proj-b",
            status="running",
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
            claude_pid=3000,
        )

        def lookup(pid):
            return live_session if pid == 3000 else None

        with patch.object(psutil, "process_iter", return_value=[mcp_proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", side_effect=lookup
            ):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        mcp_proc.terminate.assert_not_called()

    def test_7_self_protection_current_pid_never_reaped(self, clean_state):
        """os.getpid() is always in skip_pids regardless of Redis state."""
        my_pid = os.getpid()
        my_proc = _fake_proc(pid=my_pid, ppid=1)

        with patch.object(psutil, "process_iter", return_value=[my_proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        my_proc.terminate.assert_not_called()

    def test_8_sigkill_drain_with_create_time_verification(self, clean_state):
        """PID was recycled by macOS — drain must NOT kill the new PID."""
        original_create_time = 1000.0
        recycled_create_time = 9999.0

        # Stage a (pid, create_time) tuple from a previous tick
        session_health._pending_sigkill_orphans.add((2006, original_create_time))

        # At drain, the new psutil.Process(2006) returns a DIFFERENT create_time
        recycled_proc = _fake_proc(pid=2006, ppid=12345, create_time=recycled_create_time)

        with patch.object(psutil, "process_iter", return_value=[]):
            with patch.object(
                session_health, "_psutil_process_for_pid", return_value=recycled_proc
            ):
                with patch.object(
                    session_health.AgentSession,
                    "find_by_claude_pid",
                    return_value=None,
                ):
                    session_health._reap_orphan_session_processes()

        # The recycled PID was NOT killed
        recycled_proc.kill.assert_not_called()
        # And the staged set is cleared regardless
        assert (2006, original_create_time) not in session_health._pending_sigkill_orphans

    def test_9_kill_switch_short_circuits(self, clean_state, monkeypatch):
        """DISABLE_ORPHAN_PROCESS_REAP=1 returns 0 without scanning."""
        monkeypatch.setenv("DISABLE_ORPHAN_PROCESS_REAP", "1")

        with patch.object(psutil, "process_iter") as mock_iter:
            killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        mock_iter.assert_not_called()


# -----------------------------------------------------------------------------
# Invariants (10–12)
# -----------------------------------------------------------------------------


class TestInvariants:
    def test_10_per_iteration_exception_continues_scan(self, clean_state, caplog):
        """psutil exceptions mid-iteration must be logged at DEBUG, not abort the loop."""
        good_proc = _fake_proc(pid=2007, ppid=1)

        def bad_iter(*_args, **_kwargs):
            yield good_proc
            raise psutil.NoSuchProcess(99999)

        with caplog.at_level(logging.DEBUG, logger="agent.session_health"):
            with patch.object(psutil, "process_iter", bad_iter):
                with patch.object(
                    session_health.AgentSession,
                    "find_by_claude_pid",
                    return_value=None,
                ):
                    with patch.object(
                        session_health,
                        "_psutil_process_for_pid",
                        return_value=good_proc,
                    ):
                        killed = session_health._reap_orphan_session_processes()

        # The good orphan was reaped, the bad iter raised but the function returned
        assert killed == 1

    def test_11_cleanup_corrupted_returns_dict_shape(self, clean_state):
        """cleanup_corrupted_agent_sessions returns {'corrupted': int, 'orphans': int}."""
        with patch.object(session_health, "_reap_orphan_session_processes", return_value=0):
            result = session_health.cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        assert "corrupted" in result
        assert "orphans" in result
        assert isinstance(result["corrupted"], int)
        assert isinstance(result["orphans"], int)
        assert result["orphans"] == 0

    def test_11b_reaper_exception_does_not_propagate(self, clean_state):
        """If the reaper raises, cleanup_corrupted_agent_sessions still returns dict."""

        def boom():
            raise RuntimeError("simulated reaper failure")

        with patch.object(session_health, "_reap_orphan_session_processes", side_effect=boom):
            result = session_health.cleanup_corrupted_agent_sessions()

        assert isinstance(result, dict)
        assert result["orphans"] == 0
        assert result["corrupted"] >= 0

    def test_12_counter_known_session_uses_project_scoped_key(self, clean_state):
        """Known owning session → {project_key}:session-health:orphan_process_reaped."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        proj_key = "test-counter-proj-known"
        counter_key = f"{proj_key}:session-health:orphan_process_reaped"
        _R.delete(counter_key)

        proc = _fake_proc(pid=2008, ppid=1)
        # Stale heartbeat → killed → counter increments
        stale_session = SimpleNamespace(
            project_key=proj_key,
            status="running",
            last_heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
            claude_pid=2008,
        )

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession,
                "find_by_claude_pid",
                return_value=stale_session,
            ):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    session_health._reap_orphan_session_processes()

        val = _R.get(counter_key)
        assert val is not None
        assert int(val) >= 1
        _R.delete(counter_key)

    def test_12b_counter_unknown_session_uses_hostname_scoped_key(self, clean_state):
        """Unknown owning session → session-health:orphan_process_reaped:{hostname}."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        hostname = socket.gethostname()
        counter_key = f"session-health:orphan_process_reaped:{hostname}"
        _R.delete(counter_key)

        proc = _fake_proc(pid=2009, ppid=1)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    session_health._reap_orphan_session_processes()

        val = _R.get(counter_key)
        assert val is not None
        assert int(val) >= 1
        _R.delete(counter_key)


# -----------------------------------------------------------------------------
# Issue #1632 mode 1(a): fast-kill signature for stale `claude --print` one-shots
# -----------------------------------------------------------------------------

# Mirrors the observed orphan swarm: bare `claude` on PATH (NOT the bundled
# path the legacy regex matches), one-shot `--print` mode, never exiting.
_BARE_ONESHOT_CMD = [
    "claude",
    "--permission-mode",
    "bypassPermissions",
    "--model",
    "sonnet",
    "--print",
    "ping",
]
_BARE_INTERACTIVE_CMD = ["claude", "--permission-mode", "bypassPermissions"]


def _stale_ct() -> float:
    """create_time older than the one-shot threshold."""
    return time.time() - (session_health.ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS + 300)


def _young_ct() -> float:
    """create_time well inside the one-shot threshold."""
    return time.time() - 60


def _session(
    *, status: str = "running", hb_age_seconds: float = 10, pid=None, project_key="proj-own"
):
    """Build a fake owning AgentSession-like object.

    ``hb_age_seconds`` sets how far in the past ``last_heartbeat_at`` is:
    <1800s + non-terminal status => ``_session_is_alive`` True.
    """
    return SimpleNamespace(
        project_key=project_key,
        status=status,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=hb_age_seconds),
        claude_pid=pid,
    )


class TestStalePrintOneshotFastKill:
    def test_stale_oneshot_live_owner_survives(self, clean_state):
        """A stale `--print` one-shot owned by a live session is NOT reaped.

        This is the 2026-07-17 regression fix: the old fast-kill branch killed
        any stale one-shot on age alone, ignoring whether the PID belonged to a
        genuinely running PM-turn harness. Under the ownership gate the stale
        one-shot now falls through to the pre-existing
        ``elif session is not None and _session_is_alive(session): continue``
        gate and is protected.
        """
        proc = _fake_proc(pid=2100, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        live_session = _session(status="running", hb_age_seconds=10, pid=2100)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=live_session
            ):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_stale_oneshot_orphan_still_reaped(self, clean_state):
        """A stale `--print` one-shot with NO owning session is still reaped.

        The ownership gate only protects live-owned PIDs — a genuine orphan
        (``find_by_claude_pid`` returns None) still gets terminated + staged.
        """
        proc = _fake_proc(pid=2110, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_stale_oneshot_terminal_owner_still_reaped(self, clean_state):
        """A stale one-shot whose owning session is terminal (killed) is reaped.

        ``_session_is_alive`` returns False for terminal status, so the
        ``elif ... _session_is_alive`` gate does not continue and the process
        falls through to termination.
        """
        proc = _fake_proc(pid=2111, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        dead_owner = _session(status="killed", hb_age_seconds=10, pid=2111)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=dead_owner
            ):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_stale_oneshot_stale_heartbeat_owner_still_reaped(self, clean_state):
        """A stale one-shot whose owning session has a >30min heartbeat is reaped."""
        proc = _fake_proc(pid=2112, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        stale_owner = _session(status="running", hb_age_seconds=2 * 3600, pid=2112)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=stale_owner
            ):
                with patch.object(session_health, "_psutil_process_for_pid", return_value=proc):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_young_bare_print_oneshot_not_reaped(self, clean_state):
        """A `--print` one-shot inside the age threshold is left alone."""
        proc = _fake_proc(pid=2101, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_young_ct())

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()

    def test_interactive_bare_claude_not_matched_by_oneshot_signature(self, clean_state):
        """Bare `claude` WITHOUT --print (interactive) never matches fast-kill.

        The `_is_stale_print_oneshot` helper itself stays narrow to --print/-p
        one-shots regardless of the broadened `_CLAUDE_CMDLINE_RE` (D2, issue
        #1817, below) — the two matchers are deliberately disjoint.
        """
        assert not session_health._is_stale_print_oneshot(_BARE_INTERACTIVE_CMD, _stale_ct())

    def test_interactive_bare_claude_with_live_session_not_reaped(self, clean_state):
        """A PTY TUI `claude` process with a fresh owning session is protected.

        D2 (issue #1817) broadened `_CLAUDE_CMDLINE_RE` to also match this
        native/PTY-TUI cmdline shape (previously only the SDK-bundled path
        matched, so an orphaned PTY child was invisible to the reaper). The
        broadened match now goes through the general is_claude branch, which
        (unlike the oneshot fast-kill path) is still heartbeat-gated — a
        live session protects the process exactly like any other claude
        signature match.
        """
        proc = _fake_proc(pid=2102, ppid=1, cmdline=_BARE_INTERACTIVE_CMD, create_time=_stale_ct())
        live_session = SimpleNamespace(
            project_key="proj-pty",
            status="running",
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
            claude_pid=2102,
        )

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=live_session
            ):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()

    def test_interactive_bare_claude_without_session_is_reaped(self, clean_state):
        """An orphaned PTY TUI `claude` process (no owning session) IS reaped.

        This is the D2 fix in action: before broadening, this cmdline shape
        never matched any signature, so a PTY child of a dead/crashed
        session ran forever, ungated. Now it matches the general is_claude
        signature and — with no live session found and PPID==1 — is reaped
        through the normal (heartbeat-gated, non-fast-kill) path.
        """
        proc = _fake_proc(pid=2103, ppid=1, cmdline=_BARE_INTERACTIVE_CMD, create_time=_stale_ct())

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_headless_oneshot_never_matched_by_broadened_claude_signature(self, clean_state):
        """A headless `claude -p` one-shot must NOT match the broadened
        PTY-TUI alternative of `_CLAUDE_CMDLINE_RE` — only the separate,
        age-gated `_is_stale_print_oneshot` matcher governs it (D2, issue
        #1817). Both shapes carry `--permission-mode bypassPermissions`;
        the only structural difference is `-p`/`--print`, which the
        broadened regex's negative lookaheads must exclude.
        """
        cmdline_str = " ".join(_BARE_ONESHOT_CMD)
        assert not session_health._CLAUDE_CMDLINE_RE.search(cmdline_str)

    def test_worker_cmdline_never_matches_oneshot_signature(self, clean_state):
        """`python -m worker --print-anything` can't match: argv[0] is not claude."""
        assert not session_health._is_stale_print_oneshot(
            ["python", "-m", "worker", "--print"], _stale_ct()
        )

    def test_zero_create_time_not_treated_as_stale(self, clean_state):
        """Unknown create_time (0.0) must not be mistaken for infinitely old."""
        assert not session_health._is_stale_print_oneshot(_BARE_ONESHOT_CMD, 0.0)


# -----------------------------------------------------------------------------
# Issue #1632 mode 1(b): dead-chain detection (orphaned-but-alive shell wrapper)
# -----------------------------------------------------------------------------


class TestDeadChainDetection:
    def _wrapper(self, *, pid: int, ppid: int, cmdline=None):
        return _fake_proc(
            pid=pid,
            ppid=ppid,
            cmdline=cmdline or ["/bin/zsh", "-c", "python -m pytest tests/unit/ -n0"],
        )

    def test_claude_under_orphaned_zsh_wrapper_reaped(self, clean_state):
        """claude whose parent is a PPID==1 `zsh -c` wrapper = dead chain → reaped."""
        wrapper = self._wrapper(pid=555, ppid=1)
        proc = _fake_proc(pid=2200, ppid=555)  # bundled-path claude default cmdline

        def by_pid(pid):
            return {555: wrapper, 2200: proc}.get(pid)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", side_effect=by_pid):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_claude_under_attached_zsh_wrapper_not_reaped(self, clean_state):
        """Wrapper shell whose own parent is alive (PPID!=1) shields the child."""
        wrapper = self._wrapper(pid=556, ppid=4242)
        proc = _fake_proc(pid=2201, ppid=556)

        def by_pid(pid):
            return {556: wrapper, 2201: proc}.get(pid)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", side_effect=by_pid):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()

    def test_claude_under_orphaned_non_shell_parent_not_reaped(self, clean_state):
        """A PPID==1 parent that is NOT a `sh -c` wrapper is not a dead chain."""
        parent = self._wrapper(pid=557, ppid=1, cmdline=["python", "-m", "worker"])
        proc = _fake_proc(pid=2202, ppid=557)

        def by_pid(pid):
            return {557: parent, 2202: proc}.get(pid)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                with patch.object(session_health, "_psutil_process_for_pid", side_effect=by_pid):
                    killed = session_health._reap_orphan_session_processes()

        assert killed == 0
        proc.terminate.assert_not_called()


# -----------------------------------------------------------------------------
# Issue #1632 mode 1(c): fast-cadence one-shot reaper wired into the health loop
# -----------------------------------------------------------------------------


class TestFastReapStalePrintOneshots:
    def test_reaps_stale_oneshot_and_stages_sigkill(self, clean_state):
        stale = _fake_proc(pid=2300, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        young = _fake_proc(pid=2301, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_young_ct())
        interactive = _fake_proc(
            pid=2302, ppid=1, cmdline=_BARE_INTERACTIVE_CMD, create_time=_stale_ct()
        )
        attached = _fake_proc(
            pid=2303, ppid=4242, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct()
        )

        # Under the ownership gate the fast reaper resolves each candidate PID
        # via find_by_claude_pid; None keeps `stale` on the orphan path.
        with patch.object(
            psutil, "process_iter", return_value=[stale, young, interactive, attached]
        ):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 1
        stale.terminate.assert_called_once()
        young.terminate.assert_not_called()
        interactive.terminate.assert_not_called()
        attached.terminate.assert_not_called()
        staged_pids = {p for p, _ in session_health._pending_sigkill_orphans}
        assert 2300 in staged_pids

    def test_escalates_to_sigkill_on_second_pass(self, clean_state):
        """A survivor staged on a previous pass gets SIGKILL, tuple-verified.

        The survivor is an orphan (``find_by_claude_pid`` -> None), so the
        ownership gate does not protect it and the staged tuple escalates.
        """
        ct = _stale_ct()
        survivor = _fake_proc(pid=2304, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=ct)
        session_health._pending_sigkill_orphans.add((2304, ct))

        with patch.object(psutil, "process_iter", return_value=[survivor]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 1
        survivor.kill.assert_called_once()
        survivor.terminate.assert_not_called()
        assert (2304, ct) not in session_health._pending_sigkill_orphans

    def test_self_pid_never_reaped(self, clean_state):
        me = _fake_proc(pid=os.getpid(), ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())

        # The self-PID skip precedes the ownership lookup; patch anyway so the
        # test is robust to any reordering of the guard checks.
        with patch.object(psutil, "process_iter", return_value=[me]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 0
        me.terminate.assert_not_called()

    def test_never_raises_on_process_iter_failure(self, clean_state):
        with patch.object(psutil, "process_iter", side_effect=RuntimeError("boom")):
            reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 0

    def test_kill_switch_short_circuits(self, clean_state, monkeypatch):
        monkeypatch.setenv("DISABLE_ORPHAN_PROCESS_REAP", "1")

        with patch.object(psutil, "process_iter") as mock_iter:
            reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 0
        mock_iter.assert_not_called()

    async def test_health_loop_invokes_fast_reaper(self, clean_state):
        """One loop iteration calls the fast one-shot reaper, fail-silent."""
        import asyncio

        with (
            patch.object(session_health, "_write_worker_heartbeat"),
            # patch.object auto-detects async defs and substitutes AsyncMock.
            patch.object(session_health, "_agent_session_health_check"),
            patch.object(session_health, "_agent_session_hierarchy_health_check"),
            patch.object(session_health, "_dependency_health_check"),
            patch.object(session_health, "_fast_reap_stale_print_oneshots", return_value=0) as fr,
            patch.object(asyncio, "sleep", side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await session_health._agent_session_health_loop()

        fr.assert_called_once()


# -----------------------------------------------------------------------------
# Fast-reaper ownership gate (issue #2149): don't SIGTERM/SIGKILL a stale
# one-shot whose PID is a genuinely live PM-turn harness.
# -----------------------------------------------------------------------------


class TestFastReapOwnershipGate:
    def test_live_owned_stale_oneshot_survives(self, clean_state):
        """A stale one-shot owned by a live session is neither TERM'd nor staged.

        This is the direct regression for the 2026-07-17 incident: the fast
        reaper killed a running session's `claude -p` on age alone. With the
        ownership gate, ``_oneshot_owner_is_live`` returns True and the reaper
        discards the candidate before any signal.
        """
        proc = _fake_proc(pid=2400, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        live_owner = _session(status="running", hb_age_seconds=10, pid=2400)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=live_owner
            ):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 0
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        staged_pids = {p for p, _ in session_health._pending_sigkill_orphans}
        assert 2400 not in staged_pids

    def test_orphaned_stale_oneshot_reaped(self, clean_state):
        """No owning session (find_by_claude_pid -> None) -> still reaped."""
        proc = _fake_proc(pid=2401, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 1
        proc.terminate.assert_called_once()
        staged_pids = {p for p, _ in session_health._pending_sigkill_orphans}
        assert 2401 in staged_pids

    def test_terminal_owner_stale_oneshot_reaped(self, clean_state):
        """Owner exists but is terminal (killed) -> not live -> reaped."""
        proc = _fake_proc(pid=2402, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        dead_owner = _session(status="killed", hb_age_seconds=10, pid=2402)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=dead_owner
            ):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 1
        proc.terminate.assert_called_once()

    def test_stale_heartbeat_owner_stale_oneshot_reaped(self, clean_state):
        """Owner is running but heartbeat is >30min old -> not live -> reaped."""
        proc = _fake_proc(pid=2403, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        stale_owner = _session(status="running", hb_age_seconds=2 * 3600, pid=2403)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=stale_owner
            ):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 1
        proc.terminate.assert_called_once()

    def test_staging_leak_discarded_when_owner_becomes_live(self, clean_state):
        """Concern-2 regression: a PID staged for SIGKILL that now resolves to a
        live owner must be discarded from ``_pending_sigkill_orphans`` and NOT
        killed.

        Scenario: a prior pass SIGTERM'd the PID and staged ``(pid, ct)``. On
        this pass ``find_by_claude_pid`` now resolves the PID to a genuinely
        running session (e.g. a recycled/handed-off harness). The ownership
        gate must fire before the staged-SIGKILL branch — no TERM, no KILL —
        and the leaked tuple must be dropped so it can't escalate later.
        """
        ct = _stale_ct()
        proc = _fake_proc(pid=2404, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=ct)
        session_health._pending_sigkill_orphans.add((2404, ct))
        live_owner = _session(status="running", hb_age_seconds=5, pid=2404)

        with patch.object(psutil, "process_iter", return_value=[proc]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", return_value=live_owner
            ):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 0
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        assert (2404, ct) not in session_health._pending_sigkill_orphans

    def test_raising_lookup_does_not_abort_pass(self, clean_state):
        """A find_by_claude_pid that raises for one PID must not abort the pass.

        ``_oneshot_owner_is_live`` swallows the exception and returns False, so
        the raising PID is treated as an orphan and reaped, and the *other*
        stale one-shot in the same pass is still reaped too.
        """
        raising = _fake_proc(pid=2405, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        normal = _fake_proc(pid=2406, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())

        def lookup(pid):
            if pid == 2405:
                raise RuntimeError("redis lookup blew up")
            return None

        with patch.object(psutil, "process_iter", return_value=[raising, normal]):
            with patch.object(
                session_health.AgentSession, "find_by_claude_pid", side_effect=lookup
            ):
                reaped = session_health._fast_reap_stale_print_oneshots()

        assert reaped == 2
        raising.terminate.assert_called_once()
        normal.terminate.assert_called_once()

    def test_protect_path_logs_decision(self, clean_state, caplog):
        """The protect path emits an auditable "protected live harness" log line
        so operators can distinguish protection from a kill in logs/worker.log.

        The exact substring is load-bearing: the post-deploy observability
        criterion (plan Success Criteria, issue #2149) greps worker.log for
        "protected live harness" as the counter-assertion to the 2026-07-17
        SIGTERM incident, and asserts NO SIGTERM line for the protected PID.
        """
        proc = _fake_proc(pid=2407, ppid=1, cmdline=_BARE_ONESHOT_CMD, create_time=_stale_ct())
        live_owner = _session(status="running", hb_age_seconds=10, pid=2407)

        with caplog.at_level(logging.DEBUG, logger="agent.session_health"):
            with patch.object(psutil, "process_iter", return_value=[proc]):
                with patch.object(
                    session_health.AgentSession, "find_by_claude_pid", return_value=live_owner
                ):
                    session_health._fast_reap_stale_print_oneshots()

        assert "protected live harness" in caplog.text
        assert "SIGTERM" not in caplog.text
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()


# -----------------------------------------------------------------------------
# _oneshot_owner_is_live direct unit tests (issue #2149)
# -----------------------------------------------------------------------------


class TestOneshotOwnerIsLive:
    def test_pid_none_returns_false(self):
        assert session_health._oneshot_owner_is_live(None) is False

    def test_live_owner_returns_true(self):
        live_owner = _session(status="running", hb_age_seconds=10, pid=3100)
        with patch.object(
            session_health.AgentSession, "find_by_claude_pid", return_value=live_owner
        ):
            assert session_health._oneshot_owner_is_live(3100) is True

    def test_orphan_returns_false(self):
        with patch.object(session_health.AgentSession, "find_by_claude_pid", return_value=None):
            assert session_health._oneshot_owner_is_live(3101) is False

    def test_terminal_owner_returns_false(self):
        dead_owner = _session(status="killed", hb_age_seconds=10, pid=3102)
        with patch.object(
            session_health.AgentSession, "find_by_claude_pid", return_value=dead_owner
        ):
            assert session_health._oneshot_owner_is_live(3102) is False

    def test_stale_heartbeat_owner_returns_false(self):
        stale_owner = _session(status="running", hb_age_seconds=2 * 3600, pid=3103)
        with patch.object(
            session_health.AgentSession, "find_by_claude_pid", return_value=stale_owner
        ):
            assert session_health._oneshot_owner_is_live(3103) is False

    def test_lookup_exception_returns_false(self):
        with patch.object(
            session_health.AgentSession,
            "find_by_claude_pid",
            side_effect=RuntimeError("redis down"),
        ):
            assert session_health._oneshot_owner_is_live(3104) is False

    def test_lookup_timeout_returns_false_within_bound(self, monkeypatch):
        """A find_by_claude_pid slower than the bound returns False, not a hang.

        The timeout constant is shrunk for the test; the fake lookup sleeps far
        longer, so a correct bounded implementation must return within the
        shortened budget rather than blocking on the full sleep.
        """
        monkeypatch.setattr(session_health, "ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS", 0.1)

        def slow_lookup(pid):
            time.sleep(0.6)
            return _session(status="running", hb_age_seconds=5, pid=pid)

        with patch.object(
            session_health.AgentSession, "find_by_claude_pid", side_effect=slow_lookup
        ):
            start = time.monotonic()
            result = session_health._oneshot_owner_is_live(3105)
            elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 1.5
