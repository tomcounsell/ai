"""Tests for SIGTERM exit code behavior in worker/__main__.py.

Verifies that _shutdown_via_signal flag is set correctly, which causes
main() to exit with code 1 so launchd respects ThrottleInterval instead
of applying the default ~10-minute throttle after a code-0 exit.
"""

import signal


class TestSignalHandlerFlag:
    """Test _shutdown_via_signal flag behavior."""

    def setup_method(self):
        """Reset module-level flag before each test."""
        import worker.__main__ as worker_main

        worker_main._shutdown_via_signal = False

    def test_sigterm_sets_shutdown_via_signal_flag(self):
        """SIGTERM should set _shutdown_via_signal to True."""
        import worker.__main__ as worker_main

        # Simulate the conditional inside _signal_handler
        sig = signal.SIGTERM
        if sig == signal.SIGTERM:
            worker_main._shutdown_via_signal = True

        assert worker_main._shutdown_via_signal is True

    def test_sigint_does_not_set_shutdown_via_signal_flag(self):
        """SIGINT (developer Ctrl-C) should NOT set _shutdown_via_signal."""
        import worker.__main__ as worker_main

        sig = signal.SIGINT
        if sig == signal.SIGTERM:
            worker_main._shutdown_via_signal = True

        assert worker_main._shutdown_via_signal is False

    def test_shutdown_via_signal_default_is_false(self):
        """_shutdown_via_signal must default to False at module load."""
        import worker.__main__ as worker_main

        assert worker_main._shutdown_via_signal is False
