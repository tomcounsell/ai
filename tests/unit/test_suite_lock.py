"""Unit tests for the full-suite pytest advisory lock (issue #1967, F1).

The lock serializes concurrent full-suite ``-n auto`` runs so overlapping
invocations wait instead of oversubscribing CPU cores. These tests cover the
three pure/testable seams:

1. ``is_full_suite`` — which invocations the lock applies to.
2. ``evaluate_lock_state`` — the take/wait/steal policy.
3. ``acquire``/``release`` — real filesystem lock-dir behavior, including a
   second waiter backing off and a crashed owner's stale lock being reclaimed.
"""

import os

import pytest

from scripts import suite_lock


class TestIsFullSuite:
    @pytest.mark.parametrize(
        "args",
        [
            [],  # bare pytest collects the whole tree
            ["tests"],
            ["tests/"],
            ["-n", "auto", "--dist=loadfile"],
            ["-k", "test_thing"],  # -k value is not a path
            ["-m", "sdlc"],
            ["tests", "-v", "--tb=short"],
            ["-p", "no:postgresql"],  # value flag, not xdist-disabling
        ],
    )
    def test_full_suite_invocations(self, args):
        assert suite_lock.is_full_suite(args) is True

    @pytest.mark.parametrize(
        "args",
        [
            ["tests/unit/"],  # narrower path
            ["tests/unit/test_suite_lock.py"],
            ["tests/integration/test_foo.py::TestBar::test_baz"],
            ["-n0"],  # serial
            ["-n", "0"],
            ["--numprocesses=0"],
            ["-p", "no:xdist"],  # xdist disabled
            ["tests/unit/", "-k", "lock"],
        ],
    )
    def test_narrowed_or_serial_invocations(self, args):
        assert suite_lock.is_full_suite(args) is False

    def test_k_expression_containing_slash_is_not_a_path(self):
        # A value-taking flag's argument must never be read as a narrowing path.
        assert suite_lock.is_full_suite(["-k", "a/b or c"]) is True


class TestEvaluateLockState:
    def test_no_lock_takes(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=False, owner_pid=None, owner_alive=False, age_seconds=0, stale_after=3600
            )
            == "take"
        )

    def test_live_owner_fresh_waits(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=True, owner_pid=123, owner_alive=True, age_seconds=10, stale_after=3600
            )
            == "wait"
        )

    def test_dead_owner_steals(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=True, owner_pid=123, owner_alive=False, age_seconds=10, stale_after=3600
            )
            == "steal"
        )

    def test_live_owner_past_backstop_steals(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=True, owner_pid=123, owner_alive=True, age_seconds=7200, stale_after=3600
            )
            == "steal"
        )

    def test_unreadable_owner_fresh_waits(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=True, owner_pid=None, owner_alive=False, age_seconds=1, stale_after=3600
            )
            == "wait"
        )

    def test_unreadable_owner_aged_steals(self):
        assert (
            suite_lock.evaluate_lock_state(
                exists=True, owner_pid=None, owner_alive=False, age_seconds=9999, stale_after=3600
            )
            == "steal"
        )


class TestAcquireRelease:
    def test_lone_run_acquires_and_releases(self, tmp_path):
        lock = tmp_path / "full-suite-running.lock"
        assert suite_lock.acquire(lock, owner_pid=os.getpid()) == "ACQUIRED"
        assert lock.exists()
        assert (lock / "owner.pid").read_text().strip() == str(os.getpid())

        assert suite_lock.release(lock, owner_pid=os.getpid()) is True
        assert not lock.exists()

    def test_release_noop_when_not_held(self, tmp_path):
        lock = tmp_path / "full-suite-running.lock"
        assert suite_lock.release(lock, owner_pid=os.getpid()) is False

    def test_release_refuses_when_owner_differs(self, tmp_path):
        lock = tmp_path / "full-suite-running.lock"
        suite_lock.acquire(lock, owner_pid=os.getpid())
        # A run that proceeded unlocked (different owner) must not steal it.
        assert suite_lock.release(lock, owner_pid=os.getpid() + 1) is False
        assert lock.exists()
        assert suite_lock.release(lock, owner_pid=os.getpid()) is True

    def test_second_waiter_backs_off_when_owner_alive(self, tmp_path):
        """A live owner holds the lock; a second run waits, then proceeds unlocked."""
        lock = tmp_path / "full-suite-running.lock"
        # First run (this process) holds the lock.
        assert suite_lock.acquire(lock, owner_pid=os.getpid()) == "ACQUIRED"

        clock = {"t": 0.0}
        sleeps: list[float] = []

        def fake_now():
            return clock["t"]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        # Second waiter: owner (this process) is alive, so it must wait the
        # whole timeout and then proceed unlocked rather than double-run.
        status = suite_lock.acquire(
            lock,
            owner_pid=os.getpid() + 1,
            timeout=6.0,
            poll_interval=2.0,
            now_fn=fake_now,
            sleep_fn=fake_sleep,
            alive_fn=lambda pid: True,
            log=lambda msg: None,
        )
        assert status == "PROCEEDED_UNLOCKED"
        assert sleeps, "waiter should have polled at least once"
        # The original owner's lock is untouched.
        assert (lock / "owner.pid").read_text().strip() == str(os.getpid())

    def test_stale_lock_from_dead_owner_is_reclaimed(self, tmp_path):
        """A crashed owner's lock is reclaimed by the next run immediately."""
        lock = tmp_path / "full-suite-running.lock"
        dead_pid = 999_999_999  # not a live process
        suite_lock.acquire(lock, owner_pid=dead_pid, alive_fn=lambda pid: False)
        assert (lock / "owner.pid").read_text().strip() == str(dead_pid)

        # New run sees a dead owner and steals the lock without waiting.
        status = suite_lock.acquire(
            lock,
            owner_pid=os.getpid(),
            timeout=1.0,
            alive_fn=lambda pid: False,
            log=lambda msg: None,
        )
        assert status == "ACQUIRED"
        assert (lock / "owner.pid").read_text().strip() == str(os.getpid())


class TestCli:
    def test_acquire_skips_non_full_suite(self, tmp_path, capsys):
        rc = suite_lock.main(
            [
                "acquire",
                "--lock-dir",
                str(tmp_path / "l.lock"),
                "--",
                "tests/unit/test_suite_lock.py",
            ]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "SKIPPED_NOT_FULL_SUITE"

    def test_acquire_then_release_roundtrip_via_cli(self, tmp_path, capsys):
        lock = tmp_path / "l.lock"
        pid = os.getpid()
        rc = suite_lock.main(
            ["acquire", "--owner-pid", str(pid), "--lock-dir", str(lock), "--", "tests"]
        )
        assert rc == 0
        assert capsys.readouterr().out.strip() == "ACQUIRED"
        assert lock.exists()

        rc = suite_lock.main(["release", "--owner-pid", str(pid), "--lock-dir", str(lock)])
        assert rc == 0
        assert not lock.exists()

    def test_is_full_suite_exit_codes(self, tmp_path):
        assert suite_lock.main(["is-full-suite", "--", "tests"]) == 0
        assert suite_lock.main(["is-full-suite", "--", "tests/unit/"]) == 1


class TestDefaultLockDir:
    """The default lock dir is machine-global and shared across worktrees.

    Regression coverage for #2064: the pre-fix default was
    ``Path.cwd() / "data" / "full-suite-running.lock"`` — per-checkout, so
    concurrent worktree suites never contended on one lock.
    """

    def test_lock_dir_is_machine_global_not_repo_data(self, monkeypatch, tmp_path):
        # cwd is irrelevant to the resolved path: it must live under /tmp,
        # never under the checkout's data/ dir.
        monkeypatch.chdir(tmp_path)
        lock = suite_lock.default_lock_dir()
        assert lock.name == "full-suite-running.lock"
        assert str(lock).startswith("/tmp/valor-suite-lock-")
        assert "data/full-suite-running.lock" not in str(lock)
        assert str(tmp_path) not in str(lock)

    def test_default_lock_dir_delegates_to_public_helper(self):
        assert suite_lock._default_lock_dir() == suite_lock.default_lock_dir()

    def test_same_git_common_dir_yields_same_lock(self, monkeypatch):
        # Two invocations that see the same git-common-dir resolve to the same
        # lock — this is the worktree case (all worktrees share one common dir).
        monkeypatch.setattr(suite_lock, "_repo_lock_key", lambda: "deadbeefdeadbeef")
        first = suite_lock.default_lock_dir()
        second = suite_lock.default_lock_dir()
        assert first == second
        assert "deadbeefdeadbeef" in str(first)

    def test_different_keys_yield_different_locks(self, monkeypatch):
        monkeypatch.setattr(suite_lock, "_repo_lock_key", lambda: "1111111111111111")
        a = suite_lock.default_lock_dir()
        monkeypatch.setattr(suite_lock, "_repo_lock_key", lambda: "2222222222222222")
        b = suite_lock.default_lock_dir()
        assert a != b

    def test_lock_key_is_tmpdir_independent(self, monkeypatch, tmp_path):
        # A launchd worker (TMPDIR unset) and an interactive shell
        # (TMPDIR=/var/folders/...) must resolve the IDENTICAL lock dir, or two
        # lanes never serialize. The base is a fixed /tmp, so TMPDIR is ignored.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TMPDIR", raising=False)
        unset = suite_lock.default_lock_dir()
        monkeypatch.setenv("TMPDIR", "/var/folders/zz/interactive/T")
        interactive = suite_lock.default_lock_dir()
        assert unset == interactive

    def test_repo_lock_key_is_git_common_dir_hash(self, monkeypatch):
        # When git resolves, the key hashes the ABSOLUTE common dir so every
        # worktree (each of which shares one common dir) produces the same key.
        import hashlib
        import subprocess as sp

        class _Proc:
            returncode = 0
            stdout = "/Users/x/src/ai/.git\n"

        monkeypatch.setattr(sp, "run", lambda *a, **k: _Proc())
        expected = hashlib.sha1(b"/Users/x/src/ai/.git").hexdigest()[:16]
        assert suite_lock._repo_lock_key() == expected

    def test_repo_lock_key_falls_back_when_git_errors(self, monkeypatch, tmp_path):
        # git present-but-failing (non-zero) or absent -> hash cwd, never crash.
        import hashlib
        import subprocess as sp

        class _Proc:
            returncode = 128
            stdout = ""

        monkeypatch.setattr(sp, "run", lambda *a, **k: _Proc())
        monkeypatch.chdir(tmp_path)
        # Hash the resolved cwd (macOS canonicalizes /var -> /private/var, so
        # os.getcwd() may differ from str(tmp_path)).
        import os

        expected = hashlib.sha1(os.path.abspath(os.getcwd()).encode()).hexdigest()[:16]
        assert suite_lock._repo_lock_key() == expected

    def test_repo_lock_key_falls_back_when_git_missing(self, monkeypatch, tmp_path):
        import subprocess as sp

        def _boom(*a, **k):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(sp, "run", _boom)
        monkeypatch.chdir(tmp_path)
        # Must not raise; must produce a stable 16-hex key.
        key = suite_lock._repo_lock_key()
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)
