"""Regression tests pinning the missing-``OPENAI_API_KEY`` path.

The reopen of issue #1136 raised concern that the cloud fallback could
silently no-op when ``OPENAI_API_KEY`` is missing. Reading the code shows it
does not -- ``tools/tts/__init__.py`` returns an error dict and
``tools/tts/cli.py`` prints ``Error: ...`` to stderr and exits 1. These tests
pin that behavior so a future refactor cannot regress it.

Existing coverage at ``test_tts.py::TestOpenAIBackend::test_no_api_key_returns_error_dict``
already exercises this at the **helper layer** (``_synthesize_openai`` direct
call). This file adds the two layers above it that ``/do-debrief`` and the
agent's Bash tool actually hit:

1. The **dispatch** layer: ``synthesize(..., force_cloud=True)`` -- this is
   what every caller of the public TTS API hits, including the bridge relay
   and any future composite skill.
2. The **CLI** layer: ``cli.main([...])`` -- this is what the agent's Bash
   tool hits when it shells out to ``valor-tts``. The user-visible error
   message and exit code live here.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from tools.tts import cli as tts_cli
from tools.tts import synthesize

_EXPECTED_ERROR = "OPENAI_API_KEY environment variable not set"


class TestSynthesizeDispatchNoKey:
    """Dispatch-layer coverage: ``synthesize(force_cloud=True)`` with no key."""

    def test_force_cloud_no_key_returns_error_dict(self, tmp_path):
        out = tmp_path / "out.ogg"
        # Clear OPENAI_API_KEY for this call only; ``patch.dict(clear=True)``
        # also wipes other env vars that the synthesizer doesn't read, which
        # is fine -- we only care that OPENAI_API_KEY is unset.
        with patch.dict(os.environ, {}, clear=True):
            result = synthesize(
                text="hello world",
                output_path=str(out),
                force_cloud=True,
            )

        assert isinstance(result, dict)
        assert result.get("error") == _EXPECTED_ERROR
        # File must NOT be created on the missing-key path.
        assert not out.exists(), (
            "synthesize must not create the output file when OPENAI_API_KEY is missing"
        )

    def test_force_cloud_no_key_does_not_silently_succeed(self, tmp_path):
        """Guard: result must signal failure, not return a success-shaped dict."""
        out = tmp_path / "out.ogg"
        with patch.dict(os.environ, {}, clear=True):
            result = synthesize(
                text="ping",
                output_path=str(out),
                force_cloud=True,
            )

        # Success-path keys must be absent or paired with the error.
        # The contract on failure is {"error": "..."} -- nothing else.
        assert "path" not in result or result.get("error"), (
            "missing-key path must not produce a success-shaped result"
        )
        assert result.get("error"), "missing-key path must surface an error"


class TestCLINoKey:
    """CLI-layer coverage: ``cli.main`` with no key returns 1 and writes stderr."""

    def test_cli_force_cloud_no_key_exits_1_with_stderr_message(self, tmp_path, capsys):
        out = tmp_path / "out.ogg"
        with patch.dict(os.environ, {}, clear=True):
            exit_code = tts_cli.main(
                [
                    "--text",
                    "hello",
                    "--output",
                    str(out),
                    "--force-cloud",
                ]
            )

        captured = capsys.readouterr()
        assert exit_code == 1, (
            f"CLI must exit 1 on missing key, got {exit_code} (stderr={captured.err!r})"
        )
        assert "Error:" in captured.err
        assert _EXPECTED_ERROR in captured.err
        # On the failure path, stdout must be empty -- no "OK backend=..." line.
        assert captured.out == "", (
            f"CLI must not print success line on failure; stdout={captured.out!r}"
        )
        # No output file produced.
        assert not out.exists(), "CLI must not create output file on missing-key path"
