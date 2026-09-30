"""Tests for tools.tts.cli."""

from __future__ import annotations

from unittest.mock import patch


class TestCli:
    def test_help_exits_zero(self, capsys):
        from tools.tts.cli import main

        try:
            main(["--help"])
        except SystemExit as e:
            assert e.code == 0
        out = capsys.readouterr().out
        assert "valor-tts" in out
        assert "--text" in out
        assert "--output" in out

    def test_missing_required_args(self, capsys):
        from tools.tts.cli import main

        try:
            main([])
        except SystemExit as e:
            assert e.code != 0

    def test_synthesize_success(self, capsys):
        from tools.tts.cli import main

        ok = {
            "path": "/tmp/x.ogg",
            "duration": 1.5,
            "backend": "cloud",
            "voice": "nova",
            "format": "opus",
            "error": None,
        }
        with patch("tools.tts.cli.synthesize", return_value=ok):
            rc = main(["--text", "hello", "--output", "/tmp/x.ogg"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" in out
        assert "backend=cloud" in out

    def test_synthesize_failure_exit_one(self, capsys):
        from tools.tts.cli import main

        with patch(
            "tools.tts.cli.synthesize",
            return_value={"error": "boom"},
        ):
            rc = main(["--text", "hello", "--output", "/tmp/x.ogg"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error" in err and "boom" in err

    def test_force_cloud_flag_threads_through(self):
        from tools.tts.cli import main

        ok = {
            "path": "/tmp/x.ogg",
            "duration": 1.5,
            "backend": "cloud",
            "voice": "nova",
            "format": "opus",
            "error": None,
        }
        with patch("tools.tts.cli.synthesize", return_value=ok) as mock_synth:
            main(
                [
                    "--text",
                    "hello",
                    "--output",
                    "/tmp/x.ogg",
                    "--force-cloud",
                ]
            )
        kwargs = mock_synth.call_args.kwargs
        assert kwargs["force_cloud"] is True
