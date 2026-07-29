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

import logging
import os
import signal
import site
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


class TestMemoryBridgeRealHandlerFires:
    """Closes a gap in the Failure Path Test Strategy: the drop-logged-not-
    swallowed criterion names ``memory_bridge.py:922``'s own
    ``except Exception as e: logger.warning(...)`` explicitly -- it is
    already a real handler (not a bare swallow), and spike-3's fix is to
    stop SIGKILL-truncating the process before that line can run. Every
    other test in this file drives failures through `stop_detach_worker`'s
    OWN wrapping `except` (e.g. `_run_memory_extraction`'s
    ``except Exception as e: log_hook_absolute(...)``), which never calls
    the real (unmocked) `memory_bridge.extract` far enough to hit its
    internal handler. These tests call the REAL `extract()` -- not a
    stand-in -- and assert on its own logger output via `caplog`, proving
    the exact cited line actually fires."""

    def test_real_extract_outer_except_logs_via_its_own_handler(
        self, tmp_path, monkeypatch, caplog
    ):
        """Forces the outer `try/except Exception as e: logger.warning(...)`
        in `memory_bridge.extract` (memory_bridge.py:~921) by making the
        Haiku round-trip raise, without mocking `extract` itself."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("x" * 100)

        import hook_utils.memory_bridge as memory_bridge

        monkeypatch.setattr(memory_bridge, "_get_project_key", lambda cwd=None: "test-project")

        with patch(
            "agent.memory_extraction.extract_observations_async",
            side_effect=RuntimeError("haiku boom"),
        ):
            with caplog.at_level(logging.WARNING, logger="hook_utils.memory_bridge"):
                memory_bridge.extract("sess-real-outer", str(transcript), cwd=str(tmp_path))

        assert any(
            "extract failed (non-fatal)" in r.message and "haiku boom" in r.message
            for r in caplog.records
        ), f"memory_bridge.py's own outer except never logged; records={caplog.records}"

    def test_real_extract_project_key_none_logs_via_its_own_handler(
        self, tmp_path, monkeypatch, caplog
    ):
        """Forces the project-key-None branch's `logger.warning(...)` inside
        the real (unmocked) `extract()` -- this is a graceful skip, not a
        raised exception, so it would never surface through any wrapping
        `except` at all; the ONLY place this line can be observed is
        `memory_bridge`'s own logger."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("x" * 100)
        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)

        import hook_utils.memory_bridge as memory_bridge

        monkeypatch.setattr(memory_bridge, "_get_project_key", lambda cwd=None: None)

        with caplog.at_level(logging.WARNING, logger="hook_utils.memory_bridge"):
            memory_bridge.extract("sess-real-none", str(transcript), cwd=str(tmp_path))

        assert any(
            "extract write skipped" in r.message
            and "resolve_project_key returned None" in r.message
            for r in caplog.records
        ), f"memory_bridge.py's project-key-None warning never logged; records={caplog.records}"


class TestRealSubprocessSpawnEndToEnd:
    """Failure Path Test Strategy calls for exercising the REAL
    `subprocess.Popen` spawn path at least once, not only the mocked-Popen
    shape every other test in this file uses. A pure-mock test can't catch
    integration-level bugs in: `sys.executable` argv resolution, the
    worker's own `sys.path`/PROJECT_ROOT setup running as a genuinely
    separate process, or -- the specific thing this plan is about --
    whether the PARENT's log-file redirection (`stdout=logf, stderr=logf`
    in `stop._spawn_detached_extraction`) actually captures output from a
    handler-less `logger.warning` call inside the child (which falls
    through to Python's `logging.lastResort`, a plain `StreamHandler(stderr)`).

    This is the one test in the file that does NOT patch
    `stop.subprocess.Popen` -- it lets a real detached child process spawn,
    run to completion, and land its output in the fake-HOME hooks log.
    """

    def test_real_popen_spawns_worker_and_its_stderr_lands_in_the_log(
        self, fake_home, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        # The real dependency set (popoto, anthropic, ...) lives in the
        # actual user's site-packages, which is keyed off the REAL HOME.
        # `fake_home` repoints HOME for log/state-dir isolation; without
        # this, the child process would fail on `import popoto` -- an
        # environment artifact, not the bug this test targets -- and mask
        # the real assertion.
        monkeypatch.setenv("PYTHONPATH", site.getusersitepackages())

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("z" * 100)
        foreign_cwd = tmp_path / "foreign-repo"
        foreign_cwd.mkdir()

        import stop

        assert not hasattr(stop.subprocess.Popen, "call_args"), (
            "sanity check: Popen must be the REAL subprocess.Popen in this test, not a mock"
        )

        stop._spawn_detached_extraction("sess-real-e2e", str(transcript), str(foreign_cwd))

        log_path = _hooks_log(fake_home)
        deadline = time.monotonic() + 25
        content = ""
        while time.monotonic() < deadline:
            if log_path.exists():
                content = log_path.read_text()
                if content.strip():
                    break
            time.sleep(0.2)

        assert content, (
            "real detached worker subprocess never wrote anything to the "
            "absolute hooks log within the deadline"
        )
        assert "resolve_project_key returned None" in content
