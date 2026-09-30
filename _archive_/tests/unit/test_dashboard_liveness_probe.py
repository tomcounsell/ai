"""The dashboard's fenced liveness probe (#1269, refenced by #2518).

``ui/data/sdlc._check_process_alive(pid, create_time)`` answers the question the
operator is actually asking — "is the process we spawned still running?" — not
the one the old implementation answered, "does this PID exist?". Those diverge
the moment the OS recycles a PID, and the old version rendered the recycled case
as a green live chip.

Three return values, and the boundaries between them are the whole point:

  * ``True``  — the fence matches: alive, and still our process.
  * ``False`` — not live. Either the PID is gone (ghost) or it is alive under a
                different ``create_time`` (recycled, and therefore someone
                else's).
  * ``None``  — unknown. ``pid`` is None or ``<= 0``; the PID exists but the row
                recorded no ``create_time`` to compare against (legacy row); or
                the PID's identity is unreadable (``PermissionError`` /
                ``OSError`` / no psutil).

The two load-bearing cases, both of which the pre-#2518 probe got wrong:

  * a **recycled** fence renders not-live, never a green live chip;
  * a **legacy** row whose PID is still in the process table renders
    **unknown**, never alive. The operator learns the dashboard cannot vouch for
    it rather than being told a comforting thing that is not checked.

The old module docstring codified the ``os.kill(pid, 0)`` contract and a
"recycled-PID caveat" that said the operator could infer recycling by pairing an
"alive" chip with a stale freshness chip. That mitigation is gone because the
probe no longer needs it.

Ghost-branch test pattern (POSIX-portable, zero flake risk):
    proc = subprocess.Popen(["true"])
    pid = proc.pid
    proc.wait()                # subprocess has terminated
    assert _check_process_alive(pid, ct) is False
Capture the PID *before* wait(), then assert after the OS reaps it. Do NOT
replace this with a sleep-based test.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from agent.pid_fence import CREATE_TIME_TOLERANCE_S, proc_create_time
from ui.data.sdlc import _check_process_alive


@pytest.fixture
def own_pid_and_ct():
    """This pytest process and its real psutil ``create_time`` — a true fence."""
    pid = os.getpid()
    ct = proc_create_time(pid)
    assert ct is not None, "cannot fence our own process — psutil is unavailable"
    return pid, ct


class TestNoneAndNonPositivePids:
    """The helper rejects None and non-positive PIDs without probing.

    ``kill(0, sig)`` and ``kill(-pid, sig)`` have process-group semantics on
    Linux/macOS, so refusing to probe beats risking a wrong answer.
    """

    def test_none_returns_none(self):
        assert _check_process_alive(None) is None
        assert _check_process_alive(None, 123.0) is None

    def test_zero_returns_none(self):
        assert _check_process_alive(0, 123.0) is None

    def test_negative_returns_none(self):
        assert _check_process_alive(-1, 123.0) is None
        assert _check_process_alive(-12345, 123.0) is None


class TestFenceMatches:
    """A matching fence is the only route to ``True``."""

    def test_own_pid_with_its_real_create_time_is_alive(self, own_pid_and_ct):
        pid, ct = own_pid_and_ct
        assert _check_process_alive(pid, ct) is True

    def test_sub_tolerance_skew_still_reads_alive(self, own_pid_and_ct):
        """A sub-millisecond re-read difference must not flip a live chip off."""
        pid, ct = own_pid_and_ct
        assert _check_process_alive(pid, ct + CREATE_TIME_TOLERANCE_S / 2) is True


class TestRecycledPid:
    """The case the pre-fence probe rendered as a green live chip.

    This is the operator-facing half of the whole change: a session whose
    ``exec_pid`` has been handed to an unrelated process must not read as live.
    """

    def test_recycled_pid_reads_not_live(self, own_pid_and_ct):
        pid, ct = own_pid_and_ct
        # Same live pid, but the recorded fence belongs to a process that booted
        # long before us — this pid is not ours.
        assert _check_process_alive(pid, ct - 5000.0) is False

    def test_recycled_pid_is_false_not_none(self, own_pid_and_ct):
        """``False``, not ``None`` — this is a positive finding, not an unknown.

        The PID was read successfully and demonstrably belongs to a different
        process. Collapsing that into "unknown" would lose the one thing the
        fence establishes.
        """
        pid, ct = own_pid_and_ct
        result = _check_process_alive(pid, ct + 5000.0)
        assert result is False
        assert result is not None

    def test_supra_tolerance_skew_reads_not_live(self, own_pid_and_ct):
        pid, ct = own_pid_and_ct
        assert _check_process_alive(pid, ct + CREATE_TIME_TOLERANCE_S * 100) is False


class TestGhostBranch:
    """A dead PID returns False, with or without a recorded ``create_time``.

    Absence needs no identity compare, so a legacy row whose PID is gone still
    reports a definite ``False`` rather than degrading to unknown.
    """

    def test_dead_pid_with_a_recorded_create_time_returns_false(self):
        proc = subprocess.Popen(["true"])
        pid = proc.pid
        proc.wait()  # OS reaps the subprocess
        assert _check_process_alive(pid, 1_700_000_000.0) is False

    def test_dead_pid_on_a_legacy_row_still_returns_false(self):
        proc = subprocess.Popen(["true"])
        pid = proc.pid
        proc.wait()
        assert _check_process_alive(pid, None) is False, (
            "a gone PID is a definite ghost — no identity compare is needed, so "
            "a legacy row must not degrade to unknown here"
        )


class TestLegacyRowRendersUnknown:
    """A live PID with nothing recorded to compare against is UNKNOWN, not alive.

    This is the second load-bearing case. Reporting ``True`` would mean the
    dashboard vouching for identity it never checked — precisely the claim the
    fence exists to stop making. The legacy-row rule in ``agent/pid_fence.py``
    says an absent ``create_time`` on either side means "unknown".
    """

    def test_live_pid_with_no_create_time_returns_none(self):
        assert _check_process_alive(os.getpid(), None) is None

    def test_live_pid_with_no_create_time_is_not_true(self):
        result = _check_process_alive(os.getpid(), None)
        assert result is not True, (
            "a legacy row whose PID is still in the process table must render "
            "unknown, never a green live chip"
        )

    def test_default_argument_preserves_the_legacy_reading(self):
        """Calling with one argument is the legacy shape and must read unknown."""
        assert _check_process_alive(os.getpid()) is None


class TestUnreadableIdentity:
    """When identity cannot be read at all, the answer is unknown — never a lie.

    Patch target note: ``ui/data/sdlc.py`` does
    ``from agent.pid_fence import fence_is_live, proc_create_time`` at module
    import, so ``_check_process_alive``'s own call resolves through the
    ``ui.data.sdlc`` binding. Patching ``agent.pid_fence.proc_create_time``
    would reach ``fence_is_live``'s internal (late-bound) call but NOT this one,
    and every test below would then quietly depend on whether the fabricated pid
    happens to exist on the machine.
    """

    _PROC_CT = "ui.data.sdlc.proc_create_time"

    def test_permission_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(self._PROC_CT, lambda _pid: None)

        def _raise_perm(_pid, _sig):
            raise PermissionError("not your process")

        monkeypatch.setattr(os, "kill", _raise_perm)
        assert _check_process_alive(12345, 1_700_000_000.0) is None

    def test_generic_os_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(self._PROC_CT, lambda _pid: None)

        def _raise_oserr(_pid, _sig):
            raise OSError("kernel weather")

        monkeypatch.setattr(os, "kill", _raise_oserr)
        assert _check_process_alive(12345, 1_700_000_000.0) is None

    def test_process_exists_but_create_time_unreadable_returns_none(self, monkeypatch):
        """psutil cannot read the identity, but ``os.kill(pid, 0)`` says it exists.

        Neither "alive" nor "ghost" is established: the process is there, and we
        cannot tell whose it is.
        """
        monkeypatch.setattr(self._PROC_CT, lambda _pid: None)
        monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
        assert _check_process_alive(12345, 1_700_000_000.0) is None

    def test_unreadable_identity_on_a_legacy_row_is_also_unknown(self, monkeypatch):
        monkeypatch.setattr(self._PROC_CT, lambda _pid: None)
        monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
        assert _check_process_alive(12345, None) is None

    def test_other_exceptions_propagate(self, monkeypatch):
        """Non-OSError exceptions are unexpected — they must not be swallowed."""
        monkeypatch.setattr(self._PROC_CT, lambda _pid: None)

        def _raise_runtime(_pid, _sig):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(os, "kill", _raise_runtime)
        with pytest.raises(RuntimeError):
            _check_process_alive(12345, 1_700_000_000.0)


class TestTruthTable:
    """The full contract in one place, so a future edit sees every boundary."""

    @pytest.mark.parametrize(
        "case,expected",
        [
            ("matching_fence", True),
            ("recycled_fence", False),
            ("dead_pid", False),
            ("legacy_live_pid", None),
            ("no_pid", None),
        ],
    )
    def test_truth_table(self, case, expected, own_pid_and_ct):
        pid, ct = own_pid_and_ct
        if case == "matching_fence":
            result = _check_process_alive(pid, ct)
        elif case == "recycled_fence":
            result = _check_process_alive(pid, ct - 5000.0)
        elif case == "dead_pid":
            proc = subprocess.Popen(["true"])
            dead_pid = proc.pid
            proc.wait()
            result = _check_process_alive(dead_pid, 1_700_000_000.0)
        elif case == "legacy_live_pid":
            result = _check_process_alive(pid, None)
        else:
            result = _check_process_alive(None, None)
        assert result is expected


class TestSignature:
    def test_create_time_is_an_optional_second_parameter(self):
        """Pins the interface so a one-argument revert fails loudly."""
        import inspect

        params = inspect.signature(_check_process_alive).parameters
        assert "create_time" in params
        assert params["create_time"].default is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
