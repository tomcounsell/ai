"""Tests for the detached Stop-extraction fix (build-stop-detach, Phase A).

Covers docs/plans/hook-registration-manifest-dispatcher.md's Failure Path Test
Strategy for Phase A: `stop.py` no longer runs Haiku/`gh` extraction inline on
the Stop hook's 10s wall. It spawns a real detached subprocess
(`hook_utils/stop_detach_worker.py`) and exits immediately; the worker
enforces its own self-deadline and a concurrency cap, and logs every failure
to an absolute (never repo-relative) path.

`stop.py` and `hook_utils/*` are standalone scripts with non-standard
sys.path setup (see `.claude/hooks/stop.py` header) -- tests import them
directly after inserting the hooks dir onto sys.path, mirroring
`tests/unit/test_stop_hook.py`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / ".claude" / "hooks"

for _p in (str(HOOKS_DIR),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() at an isolated tmp dir for state-dir/log-path tests.

    detach_lock.py resolves `Path.home()` at call time (not import time), so
    setting HOME per-test is sufficient -- no module reload required.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _hooks_log(home: Path) -> Path:
    return home / ".claude" / "logs" / "hooks.log"


class TestSpawnDetachedExtraction:
    """stop.py must exit fast and never run extraction inline."""

    def test_spawns_real_subprocess_and_returns_immediately(self, fake_home):
        import stop

        fake_proc = type("P", (), {"pid": 424242})()

        with (
            patch("stop.subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("hook_utils.memory_bridge.extract") as mock_extract,
        ):
            start = time.monotonic()
            stop._spawn_detached_extraction("sess-fast", "/tmp/whatever.jsonl", "/tmp/cwd")
            elapsed = time.monotonic() - start

        # Fast: no inline Haiku/gh round-trip, just a Popen call.
        assert elapsed < 1.0
        mock_extract.assert_not_called()

        mock_popen.assert_called_once()
        _args, kwargs = mock_popen.call_args
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["close_fds"] is True

        argv = _args[0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("stop_detach_worker.py")
        assert "--session-id" in argv
        assert "sess-fast" in argv

    def test_worker_script_path_exists(self):
        worker = HOOKS_DIR / "hook_utils" / "stop_detach_worker.py"
        assert worker.exists()

    def test_spawn_failure_is_logged_not_swallowed(self, fake_home):
        """If Popen itself raises, stop.py logs it (no bare except: pass)."""
        import stop

        with patch("stop.subprocess.Popen", side_effect=OSError("fork failed")):
            stop._spawn_detached_extraction("sess-spawn-fail", "/tmp/t.jsonl", "/tmp")

        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "detach-spawn failed" in log_path.read_text()
        assert "fork failed" in log_path.read_text()

    def test_extraction_functions_no_longer_defined_inline_in_stop_py(self):
        """The three genuine bare `except Exception: pass` swallows this Phase
        A fix targets (formerly stop.py:225,242,262 -- each wrapping an inline
        Haiku/tui/gh extraction call) are gone: those three functions no
        longer exist in stop.py at all, since the calls they guarded moved
        into the detached worker. stop.py's own remaining bare-except sites
        (session lookups, Redis reads) are out of this fix's scope."""
        content = (HOOKS_DIR / "stop.py").read_text()
        assert "def _run_post_merge_extraction" not in content
        assert "def _run_memory_extraction" not in content
        assert "def _run_tui_interaction_capture" not in content


class TestConcurrencyCap:
    """Risk 3 concern 2: bounded concurrent detached workers."""

    def test_refuses_to_spawn_when_at_capacity(self, fake_home, monkeypatch):
        from hook_utils.detach_lock import get_absolute_log_path, get_absolute_state_dir

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "2")

        state_dir = get_absolute_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        my_pid = os.getpid()
        for i in range(2):
            (state_dir / f"slot-{i}.lock").write_text(str(my_pid))

        import stop

        with patch("stop.subprocess.Popen") as mock_popen:
            stop._spawn_detached_extraction("sess-cap", "/tmp/t.jsonl", "/tmp")

        mock_popen.assert_not_called()

        log_path = get_absolute_log_path()
        assert log_path.exists()
        assert "detach-skipped: at capacity" in log_path.read_text()

    def test_spawns_when_under_capacity(self, fake_home, monkeypatch):
        from hook_utils.detach_lock import get_absolute_state_dir

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "2")
        state_dir = get_absolute_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        # Only one of two slots occupied by a live PID (this test process).
        (state_dir / "slot-0.lock").write_text(str(os.getpid()))

        import stop

        fake_proc = type("P", (), {"pid": 555})()
        with patch("stop.subprocess.Popen", return_value=fake_proc) as mock_popen:
            stop._spawn_detached_extraction("sess-under-cap", "/tmp/t.jsonl", "/tmp")

        mock_popen.assert_called_once()


class TestStaleLockReaping:
    """A crashed/finished worker's dead-PID lock must not permanently burn a slot."""

    def test_dead_pid_lock_does_not_count_toward_cap(self, fake_home, monkeypatch):
        from hook_utils.detach_lock import get_absolute_state_dir, try_reserve_detach_slot

        # A definitely-dead PID: spawn a trivial subprocess and wait for it
        # to exit. Avoids os.fork() in a (possibly multi-threaded) test
        # process, which Python warns can deadlock.
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        dead_pid = proc.pid

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "1")
        state_dir = get_absolute_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "slot-0.lock").write_text(str(dead_pid))

        slot = try_reserve_detach_slot()

        assert slot is not None
        assert slot == state_dir / "slot-0.lock"

    def test_corrupt_lock_file_is_reaped(self, fake_home, monkeypatch):
        from hook_utils.detach_lock import get_absolute_state_dir, try_reserve_detach_slot

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "1")
        state_dir = get_absolute_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "slot-0.lock").write_text("not-a-pid")

        slot = try_reserve_detach_slot()

        assert slot is not None

    def test_reservation_uses_atomic_create_exclusive_flags(self, fake_home, monkeypatch):
        """Reservation must be O_CREAT|O_EXCL -- one atomic syscall, not a
        separate count-then-write pair (which would race)."""
        from hook_utils import detach_lock

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "3")
        real_open = os.open
        captured = []

        def spy_open(path, flags, *a, **kw):
            captured.append(flags)
            return real_open(path, flags, *a, **kw)

        monkeypatch.setattr(detach_lock.os, "open", spy_open)

        slot = detach_lock.try_reserve_detach_slot()

        assert slot is not None
        assert captured, "os.open was never called -- reservation did not use an open() syscall"
        assert all(f & os.O_EXCL for f in captured)
        assert all(f & os.O_CREAT for f in captured)

    def test_second_reservation_of_same_live_slot_fails_atomically(self, fake_home, monkeypatch):
        """Two attempts against a single, already-live slot: the second must
        fail (FileExistsError path), proving no double-booking is possible."""
        from hook_utils.detach_lock import get_absolute_state_dir, try_reserve_detach_slot

        monkeypatch.setenv("HOOK_DETACH_MAX_INFLIGHT", "1")

        first = try_reserve_detach_slot()
        assert first is not None
        # Immediately overwrite with our own (live) pid, as stop.py would.
        first.write_text(str(os.getpid()))

        second = try_reserve_detach_slot()
        assert second is None

        state_dir = get_absolute_state_dir()
        assert list(state_dir.glob("slot-*.lock")) == [first]


class TestSelfDeadline:
    """Risk 3 concern 1: the worker self-terminates rather than lingering."""

    def test_deadline_exception_is_not_a_plain_exception_subclass(self):
        from hook_utils.stop_detach_worker import DetachDeadlineExceeded

        assert issubclass(DetachDeadlineExceeded, BaseException)
        assert not issubclass(DetachDeadlineExceeded, Exception)

    def test_wrapping_except_exception_does_not_swallow_deadline(self, monkeypatch):
        """Proves the BaseException property is load-bearing: a broad
        `except Exception: pass` -- the exact shape of memory_bridge's own
        handler -- must NOT catch the deadline exception."""
        from hook_utils.stop_detach_worker import DetachDeadlineExceeded, _raise_deadline

        signal.signal(signal.SIGALRM, _raise_deadline)
        signal.alarm(1)
        caught_deadline = False
        try:
            try:
                time.sleep(5)
            except Exception:
                pass  # broad swallow -- must NOT catch DetachDeadlineExceeded
        except DetachDeadlineExceeded:
            caught_deadline = True
        finally:
            signal.alarm(0)

        assert caught_deadline, "except Exception: pass swallowed the deadline exception"

    def test_worker_main_self_terminates_and_logs_deadline_exceeded(self, fake_home, monkeypatch):
        monkeypatch.setenv("HOOK_DETACH_DEADLINE_SECONDS", "1")

        import hook_utils.stop_detach_worker as worker

        def hang(*_a, **_kw):
            time.sleep(5)

        with patch.object(worker, "run_extraction", side_effect=hang):
            rc = worker.main(
                ["--session-id", "sess-deadline", "--transcript-path", "", "--cwd", "/tmp"]
            )

        assert rc == 1
        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "deadline-exceeded" in log_path.read_text()
        assert "sess-deadline" in log_path.read_text()

    def test_run_memory_extraction_repropagates_deadline(self, monkeypatch):
        """The per-step try/except inside the worker must re-raise the
        deadline rather than treating it as a normal extraction failure."""
        from hook_utils.stop_detach_worker import DetachDeadlineExceeded, _run_memory_extraction

        def raise_deadline(*_a, **_kw):
            raise DetachDeadlineExceeded("boom")

        with patch("hook_utils.memory_bridge.extract", side_effect=raise_deadline):
            with pytest.raises(DetachDeadlineExceeded):
                _run_memory_extraction("sess", "/tmp/t.jsonl", "/tmp")


class TestSlotReleaseOnExit:
    """The worker must release its lock slot on success, failure, and deadline."""

    def _seed_slot(self, home: Path) -> Path:
        from hook_utils.detach_lock import get_absolute_state_dir

        state_dir = get_absolute_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        slot_path = state_dir / "slot-0.lock"
        slot_path.write_text(str(os.getpid()))
        return slot_path

    def test_slot_released_on_success(self, fake_home, monkeypatch):
        import hook_utils.stop_detach_worker as worker

        slot_path = self._seed_slot(fake_home)
        monkeypatch.setenv("HOOK_DETACH_SLOT_PATH", str(slot_path))

        with patch.object(worker, "run_extraction", return_value=None):
            rc = worker.main(["--session-id", "s", "--transcript-path", "", "--cwd", "/tmp"])

        assert rc == 0
        assert not slot_path.exists()

    def test_slot_released_on_failure(self, fake_home, monkeypatch):
        import hook_utils.stop_detach_worker as worker

        slot_path = self._seed_slot(fake_home)
        monkeypatch.setenv("HOOK_DETACH_SLOT_PATH", str(slot_path))

        def boom(*_a, **_kw):
            raise RuntimeError("boom")

        with patch.object(worker, "run_extraction", side_effect=boom):
            rc = worker.main(["--session-id", "s", "--transcript-path", "", "--cwd", "/tmp"])

        assert rc == 1
        assert not slot_path.exists()

    def test_slot_released_on_deadline(self, fake_home, monkeypatch):
        import hook_utils.stop_detach_worker as worker

        monkeypatch.setenv("HOOK_DETACH_DEADLINE_SECONDS", "1")
        slot_path = self._seed_slot(fake_home)
        monkeypatch.setenv("HOOK_DETACH_SLOT_PATH", str(slot_path))

        def hang(*_a, **_kw):
            time.sleep(5)

        with patch.object(worker, "run_extraction", side_effect=hang):
            rc = worker.main(["--session-id", "s", "--transcript-path", "", "--cwd", "/tmp"])

        assert rc == 1
        assert not slot_path.exists()


class TestExtractionFailureLogging:
    """Error State Rendering: a dropped extraction MUST appear in the log."""

    def test_memory_extraction_failure_is_logged_not_silent(self, fake_home):
        from hook_utils.stop_detach_worker import _run_memory_extraction

        with patch("hook_utils.memory_bridge.extract", side_effect=RuntimeError("boom")):
            _run_memory_extraction("sess-fail", "/tmp/t.jsonl", "/tmp")

        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        content = log_path.read_text()
        assert "memory extraction failed" in content
        assert "boom" in content

    def test_tui_capture_failure_is_logged_not_silent(self, fake_home):
        from hook_utils.stop_detach_worker import _run_tui_interaction_capture

        with patch(
            "agent.tui_interaction_capture.summarize_and_store",
            side_effect=RuntimeError("tui boom"),
        ):
            _run_tui_interaction_capture("sess-fail", "/tmp")

        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "tui interaction capture failed" in log_path.read_text()

    def test_post_merge_extraction_failure_is_logged_not_silent(self, fake_home):
        from hook_utils.stop_detach_worker import _run_post_merge_extraction

        with patch(
            "hook_utils.memory_bridge.load_agent_session_sidecar",
            side_effect=RuntimeError("sidecar boom"),
        ):
            _run_post_merge_extraction("sess-fail", "/tmp")

        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "post-merge extraction failed" in log_path.read_text()


class TestForeignRepoLogging:
    """Tech-debt 2: user-scope/foreign-repo sessions have no repo-relative logs/."""

    def test_log_lands_in_absolute_path_when_cwd_outside_any_repo(
        self, fake_home, tmp_path, monkeypatch
    ):
        foreign_dir = tmp_path / "foreign-repo"
        foreign_dir.mkdir()
        monkeypatch.chdir(foreign_dir)

        from hook_utils.stop_detach_worker import _run_post_merge_extraction

        with patch(
            "hook_utils.memory_bridge.load_agent_session_sidecar",
            side_effect=RuntimeError("sidecar read failed"),
        ):
            _run_post_merge_extraction("sess-foreign", str(foreign_dir))

        # No repo-relative logs/ directory should exist here -- the drop must
        # land in the absolute, home-relative path instead.
        assert not (foreign_dir / "logs").exists()

        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "post-merge extraction failed" in log_path.read_text()

    def test_worker_deadline_log_lands_in_absolute_path_from_foreign_cwd(
        self, fake_home, tmp_path, monkeypatch
    ):
        foreign_dir = tmp_path / "another-foreign-repo"
        foreign_dir.mkdir()
        monkeypatch.chdir(foreign_dir)
        monkeypatch.setenv("HOOK_DETACH_DEADLINE_SECONDS", "1")

        import hook_utils.stop_detach_worker as worker

        def hang(*_a, **_kw):
            time.sleep(5)

        with patch.object(worker, "run_extraction", side_effect=hang):
            rc = worker.main(
                [
                    "--session-id",
                    "sess-foreign-deadline",
                    "--transcript-path",
                    "",
                    "--cwd",
                    str(foreign_dir),
                ]
            )

        assert rc == 1
        assert not (foreign_dir / "logs").exists()
        log_path = _hooks_log(fake_home)
        assert log_path.exists()
        assert "deadline-exceeded" in log_path.read_text()
