"""Tests for agent/sdlc_liveness.py — watchdog-safe liveness token emission (issue #1394)."""

import sys

import pytest


class TestEmitLivenessToken:
    """Tests for emit_liveness_token output format and flush behavior."""

    def test_output_format(self, capsys):
        """Token contains stage and elapsed seconds in the expected format."""
        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=120, stage="TEST")
        captured = capsys.readouterr()
        assert "[sdlc-liveness]" in captured.out
        assert "TEST" in captured.out
        assert "120" in captured.out

    def test_output_format_deploy(self, capsys):
        """Token works for DEPLOY stage."""
        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=300, stage="DEPLOY")
        captured = capsys.readouterr()
        assert "[sdlc-liveness]" in captured.out
        assert "DEPLOY" in captured.out
        assert "300" in captured.out

    def test_output_ends_with_newline(self, capsys):
        """Token line ends with a newline so the watchdog sees a complete line."""
        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=60, stage="TEST")
        captured = capsys.readouterr()
        assert captured.out.endswith("\n")

    def test_elapsed_zero(self, capsys):
        """Zero elapsed seconds is a valid token (first heartbeat)."""
        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=0, stage="TEST")
        captured = capsys.readouterr()
        assert "0" in captured.out
        assert "[sdlc-liveness]" in captured.out

    def test_flush_called(self, monkeypatch):
        """Token is flushed immediately (no buffering that could delay watchdog reset)."""
        flushed = []
        original_stdout = sys.stdout

        class CapturingStream:
            def write(self, data):
                original_stdout.write(data)

            def flush(self):
                flushed.append(True)

        monkeypatch.setattr(sys, "stdout", CapturingStream())

        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=60, stage="TEST")
        assert len(flushed) > 0, "flush() was not called — token may not reach watchdog"

    @pytest.mark.parametrize("stage", ["TEST", "DEPLOY", "BUILD", "PLAN", "REVIEW"])
    def test_various_stages(self, capsys, stage):
        """Token emission works for any stage name."""
        from agent.sdlc_liveness import emit_liveness_token

        emit_liveness_token(elapsed_seconds=60, stage=stage)
        captured = capsys.readouterr()
        assert stage in captured.out
        assert "[sdlc-liveness]" in captured.out
