"""Unit tests for per-session model routing (plan docs/plans/session-model-routing-fallback.md).

Covers:
- `_resolve_session_model()` D1 precedence cascade (3 levels + empty-settings edge).
- `get_response_via_harness(model=...)` argv injection.
- Argv ordering: `--model` precedes positional `message` AND `--resume <uuid>`.
- INFO log line fires with the resolved value.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors fixtures used in test_harness_streaming.py)
# ---------------------------------------------------------------------------


async def _async_lines_empty():
    """Return an async iterator that yields nothing (empty stdout)."""
    if False:
        yield  # pragma: no cover


async def _async_lines(payload: str):
    """Yield each line of ``payload`` as bytes, as the harness reader expects."""
    for line in payload.splitlines(keepends=True):
        yield line.encode("utf-8")


def _stub_subprocess(mock_exec, result_text: str = "ok", session_id: str = "sess_abc"):
    """Wire ``asyncio.create_subprocess_exec`` to return a fake claude -p process."""
    stdout_data = (
        json.dumps(
            {
                "type": "result",
                "result": result_text,
                "session_id": session_id,
            }
        )
        + "\n"
    )
    mock_proc = AsyncMock()
    mock_proc.stdout = _async_lines(stdout_data)
    mock_proc.stderr = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_exec.return_value = mock_proc
    return mock_proc


# ---------------------------------------------------------------------------
# _resolve_session_model() cascade
# ---------------------------------------------------------------------------


class TestResolveSessionModelCascade:
    """D1 precedence: session.model > settings > codebase default 'opus'."""

    def test_explicit_session_model_wins(self):
        from agent.session_executor import _resolve_session_model

        session = MagicMock()
        session.model = "sonnet"
        # Even if settings has a different default, session.model wins.
        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="haiku")),
        ):
            assert _resolve_session_model(session) == "sonnet"

    def test_session_model_none_falls_through_to_settings(self):
        from agent.session_executor import _resolve_session_model

        session = MagicMock()
        session.model = None
        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="haiku")),
        ):
            assert _resolve_session_model(session) == "haiku"

    def test_session_model_empty_string_falls_through_to_settings(self):
        from agent.session_executor import _resolve_session_model

        session = MagicMock()
        session.model = ""
        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="sonnet")),
        ):
            assert _resolve_session_model(session) == "sonnet"

    def test_settings_default_is_opus(self):
        """Codebase default (settings default) is 'opus' when operator doesn't override."""
        from agent.session_executor import _resolve_session_model
        from config.settings import settings

        session = MagicMock()
        session.model = None
        # Use live settings (pydantic default is 'opus').
        assert settings.models.session_default_model == "opus"
        assert _resolve_session_model(session) == "opus"

    def test_empty_settings_default_returns_none(self):
        """Operator misconfiguration (empty string) → cascade returns None gracefully."""
        from agent.session_executor import _resolve_session_model

        session = MagicMock()
        session.model = None
        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="")),
        ):
            assert _resolve_session_model(session) is None

    def test_none_session_returns_settings_default(self):
        """O2 guard: _resolve_session_model(None) doesn't crash."""
        from agent.session_executor import _resolve_session_model

        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="opus")),
        ):
            assert _resolve_session_model(None) == "opus"

    def test_session_without_model_attr(self):
        """Session object missing .model attr (edge case)."""
        from agent.session_executor import _resolve_session_model

        class Bare:
            pass

        with patch(
            "agent.session_executor.settings",
            MagicMock(models=MagicMock(session_default_model="opus")),
        ):
            assert _resolve_session_model(Bare()) == "opus"


# ---------------------------------------------------------------------------
# get_response_via_harness(model=...) argv injection
# ---------------------------------------------------------------------------


class TestHarnessModelArgvInjection:
    """Confirms --model flows from kwarg to subprocess argv."""

    @pytest.mark.asyncio
    async def test_model_injected_into_argv(self):
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/test",
                model="opus",
            )

            # Positional argv is the first positional call arg.
            argv = mock_exec.call_args.args
            assert "--model" in argv, f"--model missing from argv: {argv}"
            idx = argv.index("--model")
            assert argv[idx + 1] == "opus"

    @pytest.mark.asyncio
    async def test_model_none_omits_flag(self):
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/test",
                model=None,
            )

            argv = mock_exec.call_args.args
            assert "--model" not in argv, f"--model should not be in argv when None: {argv}"

    @pytest.mark.asyncio
    async def test_empty_model_string_omits_flag(self):
        """model='' is falsy → no --model injected (graceful degradation)."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/test",
                model="",
            )

            argv = mock_exec.call_args.args
            assert "--model" not in argv

    @pytest.mark.asyncio
    async def test_model_precedes_positional_message(self):
        """--model must live in harness_cmd, before the positional message."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="THE_MESSAGE_TOKEN",
                working_dir="/tmp/test",
                model="sonnet",
            )

            argv = list(mock_exec.call_args.args)
            model_idx = argv.index("--model")
            msg_idx = argv.index("THE_MESSAGE_TOKEN")
            assert model_idx < msg_idx, f"--model must precede message: {argv}"

    @pytest.mark.asyncio
    async def test_model_precedes_resume(self):
        """On resume, --model must still precede --resume <uuid>."""
        from agent.sdk_client import get_response_via_harness

        valid_uuid = "12345678-1234-1234-1234-123456789abc"
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("agent.sdk_client._store_claude_session_uuid"),
        ):
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/test",
                prior_uuid=valid_uuid,
                model="opus",
            )

            argv = list(mock_exec.call_args.args)
            model_idx = argv.index("--model")
            resume_idx = argv.index("--resume")
            assert model_idx < resume_idx, (
                f"--model must precede --resume: {argv}"
            )

    @pytest.mark.asyncio
    async def test_defensive_copy_of_caller_harness_cmd(self):
        """Caller-supplied harness_cmd must not be mutated (S1 fix)."""
        from agent.sdk_client import get_response_via_harness

        caller_cmd = ["fake-claude", "-p", "--flag"]
        caller_cmd_before = list(caller_cmd)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            await get_response_via_harness(
                message="hello",
                working_dir="/tmp/test",
                harness_cmd=caller_cmd,
                model="opus",
            )

            assert caller_cmd == caller_cmd_before, (
                f"Caller harness_cmd was mutated: {caller_cmd} != {caller_cmd_before}"
            )

    @pytest.mark.asyncio
    async def test_info_log_emitted_with_model(self, caplog):
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)

            with caplog.at_level(logging.INFO, logger="agent.sdk_client"):
                await get_response_via_harness(
                    message="hi",
                    working_dir="/tmp/test",
                    model="sonnet",
                )

            matching = [r for r in caplog.records if "Using --model sonnet" in r.getMessage()]
            assert matching, (
                f"Expected INFO log with 'Using --model sonnet'; got: "
                f"{[r.getMessage() for r in caplog.records]}"
            )
