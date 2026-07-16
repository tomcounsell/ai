"""
Test Claude Agent SDK client integration.

Run with: pytest tests/test_sdk_client.py -v
"""

import os
import sys

import pytest

# Add repo root to sys.path so `from agent.* import ...` works when this module
# is imported standalone (pytest already provides the rootdir, but this keeps
# the file runnable via `python -m unittest`). The previous form pointed at the
# `tests/` directory, which broke transitive `from tools.* import ...` chains
# (e.g. agent.constants -> tools.emoji_embedding) at collection time.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.sdk_client import load_system_prompt


def test_load_system_prompt():
    """Test that system prompt can be loaded from persona segments."""
    prompt = load_system_prompt()
    assert prompt is not None
    assert len(prompt) > 100
    assert "Valor" in prompt


# ValorAgent (in-process ClaudeSDKClient wrapper) and its TELEGRAM_CHAT_ID /
# SESSION_TYPE env-injection tests were removed here (plan #2000 Task 2.2
# dead-SDK-path deletion): ValorAgent has no production caller after
# get_agent_response_sdk's deletion. The REAL, live env-injection mechanism
# for CLI-harness sessions lives in agent/session_executor.py and is covered
# by tests/integration/test_harness_env_pm_injection.py and
# tests/integration/test_session_spawning.py.


@pytest.mark.asyncio
async def test_build_harness_turn_input_basic():
    """Test build_harness_turn_input produces correct context headers."""
    from unittest.mock import patch

    with patch("bridge.context.build_context_prefix", return_value="PROJECT: test"):
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="Hello world",
            session_id="test-session-123",
            sender_name="Test User",
            chat_title="Test Chat",
            project={"name": "Test", "_key": "test"},
            task_list_id="task-list-1",
            session_type="eng",
            sender_id=12345,
        )

    assert "PROJECT: test" in result
    assert "FROM: Test User" in result
    assert "SESSION_ID: test-session-123" in result
    assert "TASK_SCOPE: task-list-1" in result
    assert "SCOPE:" in result
    assert "MESSAGE: Hello world" in result


@pytest.mark.asyncio
async def test_build_harness_turn_input_none_sender():
    """build_harness_turn_input with sender_name=None must not produce FROM: None."""
    from unittest.mock import patch

    with patch("bridge.context.build_context_prefix", return_value="CONTEXT"):
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="Hello",
            session_id="test-session",
            sender_name=None,
            chat_title=None,
            project=None,
            task_list_id=None,
            session_type="teammate",
            sender_id=None,
        )

    assert "FROM: None" not in result
    assert "FROM:" not in result


class TestApplyContextBudget:
    """Tests for _apply_context_budget() harness input trimming (issue #958)."""

    def test_noop_when_under_budget(self):
        """Messages under the budget are returned unchanged."""
        from agent.sdk_client import _apply_context_budget

        msg = "SHORT MESSAGE"
        assert _apply_context_budget(msg, max_chars=1000) == msg

    def test_trim_removes_oldest_prefix(self):
        """When over budget, oldest content (start of string) is trimmed."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 500 + "\nMESSAGE: keep this"
        result = _apply_context_budget(msg, max_chars=100)
        assert "keep this" in result
        assert len(result) <= 100 + len(
            "[CONTEXT TRIMMED — oldest context omitted to fit harness budget]\n"
        )

    def test_message_boundary_preserved(self):
        """Everything from the final MESSAGE: marker onward is preserved."""
        from agent.sdk_client import _apply_context_budget

        prefix = "X" * 1000
        tail = "\nMESSAGE: do the thing"
        msg = prefix + tail
        result = _apply_context_budget(msg, max_chars=100)
        assert result.endswith(tail)

    def test_trim_marker_injected(self):
        """Trimmed messages get a trim marker prepended."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 500 + "\nMESSAGE: keep"
        result = _apply_context_budget(msg, max_chars=100)
        assert result.startswith("[CONTEXT TRIMMED")

    def test_empty_input_passthrough(self):
        """Empty string returns empty string."""
        from agent.sdk_client import _apply_context_budget

        assert _apply_context_budget("", max_chars=100) == ""

    def test_steering_only_exceeds_budget_passthrough(self):
        """If MESSAGE: tail alone exceeds budget, pass through unchanged."""
        from agent.sdk_client import _apply_context_budget

        msg = "CTX\nMESSAGE: " + "B" * 200
        result = _apply_context_budget(msg, max_chars=50)
        # Should pass through unchanged because tail alone exceeds budget
        assert result == msg

    def test_no_marker_trim_from_start(self):
        """Without a MESSAGE: marker, trim from start of string."""
        from agent.sdk_client import _apply_context_budget

        msg = "A" * 200
        result = _apply_context_budget(msg, max_chars=50)
        assert result.startswith("[CONTEXT TRIMMED]")
        assert len(result) <= 50 + len("[CONTEXT TRIMMED]\n")


# -----------------------------------------------------------------------------
# _get_prior_session_uuid status filter — issue #1061
#
# The filter must include killed/failed so operator-initiated resume
# (`valor-session resume --id <killed-id>`) can hand the stored UUID to
# the Claude Code SDK for --resume replay.
# -----------------------------------------------------------------------------


class TestGetPriorSessionUuidStatusFilter:
    """killed and failed sessions must expose their claude_session_uuid.

    Prior to #1061 the filter excluded them, which meant resuming a killed
    session would silently start a fresh transcript instead of replaying the
    stored one.
    """

    def _make_session_row(self, status: str, uuid: str, created_at: int = 100):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.status = status
        s.claude_session_uuid = uuid
        s.created_at = created_at
        return s

    def _run_with_sessions(self, sessions):
        from unittest.mock import MagicMock, patch

        from agent.sdk_client import _get_prior_session_uuid

        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = sessions

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
            return _get_prior_session_uuid("sess-test")

    def test_killed_session_uuid_returned(self):
        sessions = [self._make_session_row("killed", "uuid-killed")]
        assert self._run_with_sessions(sessions) == "uuid-killed"

    def test_failed_session_uuid_returned(self):
        sessions = [self._make_session_row("failed", "uuid-failed")]
        assert self._run_with_sessions(sessions) == "uuid-failed"

    def test_completed_still_returned(self):
        sessions = [self._make_session_row("completed", "uuid-completed")]
        assert self._run_with_sessions(sessions) == "uuid-completed"

    def test_superseded_still_filtered_out(self):
        """Only the documented statuses are eligible; others are skipped."""
        sessions = [self._make_session_row("superseded", "uuid-old")]
        assert self._run_with_sessions(sessions) is None

    def test_newest_record_wins_when_multiple_status_eligible(self):
        """created_at desc sort picks the newest; an older killed cannot shadow newer completed."""
        old_killed = self._make_session_row("killed", "uuid-old-killed", created_at=100)
        new_completed = self._make_session_row("completed", "uuid-new-completed", created_at=500)
        # Pass them in non-sorted order to exercise the sort.
        assert self._run_with_sessions([old_killed, new_completed]) == "uuid-new-completed"


# -----------------------------------------------------------------------------
# get_response_via_harness: --append-system-prompt argv injection (issue #1148)
# -----------------------------------------------------------------------------


class TestGetResponseViaHarnessSystemPrompt:
    """Verify --append-system-prompt argv injection for the system_prompt kwarg.

    Issue #1148: PM harness sessions need to carry the project-manager persona
    via --append-system-prompt. Drafter sessions must NOT receive any persona
    so this is a strict opt-in via the system_prompt kwarg.
    """

    @pytest.mark.asyncio
    async def test_no_system_prompt_means_no_flag(self):
        """Default system_prompt=None must not add --append-system-prompt to argv."""
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
            )

        assert "--append-system-prompt" not in captured["cmd"]

    @pytest.mark.asyncio
    async def test_empty_system_prompt_means_no_flag(self):
        """system_prompt='' (falsy) must not add --append-system-prompt to argv."""
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt="",
            )

        assert "--append-system-prompt" not in captured["cmd"]

    @pytest.mark.asyncio
    async def test_truthy_system_prompt_appends_flag(self):
        """A non-empty system_prompt is injected as --append-system-prompt <text>.

        Verifies positional ordering: --append-system-prompt must appear after
        --model (model selection precedes any persona flag) but before the
        positional message at the tail of the argv.
        """
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}
        persona = "PM persona body — CRITIQUE is Mandatory After PLAN"

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="user-message-tail",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt=persona,
            )

        cmd = captured["cmd"]
        assert "--append-system-prompt" in cmd, cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == persona, "persona text must immediately follow the flag"
        # Position invariant: --model precedes --append-system-prompt
        assert "--model" in cmd
        assert cmd.index("--model") < idx, "--model must precede --append-system-prompt"
        # Position invariant: positional message is at the tail (after persona)
        assert cmd[-1] == "user-message-tail"
        assert idx < len(cmd) - 1

    @pytest.mark.asyncio
    async def test_oversized_system_prompt_logs_and_omits(self, caplog):
        """A 600KB system_prompt must be omitted with a warning, not injected."""
        import logging
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        oversize = "x" * 600_000
        caplog.set_level(logging.WARNING, logger="agent.sdk_client")

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt=oversize,
            )

        assert "--append-system-prompt" not in captured["cmd"]
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("exceeds 512KB soft cap" in m for m in warnings), warnings

    @pytest.mark.asyncio
    async def test_exclude_dynamic_sections_present_when_system_prompt(self):
        """PM sessions must inject --exclude-dynamic-system-prompt-sections for cache stability.

        Issue #1227: this flag stabilises the system-prompt prefix so that
        Anthropic's server-side prompt cache can reuse it across consecutive PM
        sessions with the same working_directory.  It must be present whenever
        --append-system-prompt is used.
        """
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}
        persona = "PM persona body — project-manager overlay"

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt=persona,
            )

        cmd = captured["cmd"]
        assert "--exclude-dynamic-system-prompt-sections" in cmd, (
            "--exclude-dynamic-system-prompt-sections must be in argv for PM sessions"
        )
        # Ordering: cache flag must precede --append-system-prompt
        exc_idx = cmd.index("--exclude-dynamic-system-prompt-sections")
        asp_idx = cmd.index("--append-system-prompt")
        assert exc_idx < asp_idx, (
            "--exclude-dynamic-system-prompt-sections must precede --append-system-prompt"
        )

    @pytest.mark.asyncio
    async def test_exclude_dynamic_sections_absent_without_system_prompt(self):
        """Non-PM sessions must NOT get --exclude-dynamic-system-prompt-sections.

        The flag only helps when --append-system-prompt is in play (PM sessions).
        Injecting it for dev/teammate sessions would needlessly change the
        default system-prompt composition for those session types.
        """
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                # system_prompt intentionally omitted — dev/teammate session
            )

        assert "--exclude-dynamic-system-prompt-sections" not in captured["cmd"]

    @pytest.mark.asyncio
    async def test_pm_persona_overlay_preserved_with_caching_flag(self):
        """Persona overlay from #1148 must survive the Direction-A changes (#1227).

        Verifies that the system-prompt text is still injected verbatim even
        after --exclude-dynamic-system-prompt-sections was added alongside it.
        """
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}
        persona = "PM persona body — CRITIQUE is Mandatory After PLAN\nSDLC rules here."

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt=persona,
            )

        cmd = captured["cmd"]
        # Persona must still be the value immediately after --append-system-prompt
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == persona, (
            "Persona content must be preserved verbatim (#1148 invariant)"
        )

    @pytest.mark.asyncio
    async def test_arg_max_guard_trips_with_oversized_prompt(self):
        """512KB ARG_MAX guard (agent/sdk_client.py:2118) must still trip after Direction-A changes.

        Issue #1227 must NOT remove or raise this guard.
        """
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = cmd
            return ("done", None, 0, None, None, None, 0, 0, None)

        oversize = "x" * 600_000
        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                model="opus",
                system_prompt=oversize,
            )

        # Neither --append-system-prompt nor the cache flag should appear when the
        # prompt exceeds the size cap — both are conditional on the else branch.
        assert "--append-system-prompt" not in captured["cmd"]
        assert "--exclude-dynamic-system-prompt-sections" not in captured["cmd"]


# ---------------------------------------------------------------------------
# cold_start_metrics: TTFT instrumentation (issue #1227)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Standalone verification-table test anchors (issue #1227)
# These thin wrappers are named exactly as the plan's verification table
# commands require so that `pytest tests/unit/test_sdk_client.py::test_pm_persona_overlay_present`
# and `::test_arg_max_guard_trips` resolve without failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_persona_overlay_present():
    """Persona overlay from #1148 must survive Direction-A changes (#1227).

    Verification anchor: pytest tests/unit/test_sdk_client.py::test_pm_persona_overlay_present
    """
    from unittest.mock import AsyncMock, patch

    from agent.sdk_client import get_response_via_harness

    captured = {}
    persona = "PM persona — CRITIQUE is Mandatory After PLAN. SDLC rules."

    async def fake_run(cmd, working_dir, proc_env, **_kw):
        captured["cmd"] = cmd
        return ("done", None, 0, None, None, None, 0, 0, None)

    with patch(
        "agent.session_runner.harness.claude._run_harness_subprocess",
        new=AsyncMock(side_effect=fake_run),
    ):
        await get_response_via_harness(
            message="hi",
            working_dir="/tmp",
            env={"AGENT_SESSION_ID": "x"},
            model="opus",
            system_prompt=persona,
        )

    cmd = captured["cmd"]
    assert "--append-system-prompt" in cmd
    idx = cmd.index("--append-system-prompt")
    assert cmd[idx + 1] == persona, "Persona content (#1148 invariant) must be verbatim"


@pytest.mark.asyncio
async def test_arg_max_guard_trips():
    """512KB ARG_MAX guard at agent/sdk_client.py must still trip after Direction-A (#1227).

    Verification anchor: pytest tests/unit/test_sdk_client.py::test_arg_max_guard_trips
    """
    from unittest.mock import AsyncMock, patch

    from agent.sdk_client import get_response_via_harness

    captured = {}

    async def fake_run(cmd, working_dir, proc_env, **_kw):
        captured["cmd"] = cmd
        return ("done", None, 0, None, None, None, 0, 0, None)

    oversize = "x" * 600_000
    with patch(
        "agent.session_runner.harness.claude._run_harness_subprocess",
        new=AsyncMock(side_effect=fake_run),
    ):
        await get_response_via_harness(
            message="hi",
            working_dir="/tmp",
            env={"AGENT_SESSION_ID": "x"},
            model="opus",
            system_prompt=oversize,
        )

    assert "--append-system-prompt" not in captured["cmd"], "Guard must drop oversized prompt"
    assert "--exclude-dynamic-system-prompt-sections" not in captured["cmd"]


class TestColdStartMetrics:
    """Verify the TTFT (time-to-first-token) measurement module."""

    def test_record_ttft_writes_jsonl(self, tmp_path, monkeypatch):
        """record_ttft() must append a valid JSON line to the metrics file."""
        import json

        import agent.cold_start_metrics as csm

        metrics_file = tmp_path / "cold_start_metrics.jsonl"
        monkeypatch.setattr(csm, "_METRICS_FILE", metrics_file)

        csm.record_ttft(
            ttft_seconds=12.345,
            session_id="test-session-1",
            session_type="eng",
            working_dir="/tmp/project",
            prompt_chars=74769,
            model="opus",
        )

        assert metrics_file.exists()
        lines = metrics_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["session_id"] == "test-session-1"
        assert entry["session_type"] == "eng"
        assert entry["ttft_seconds"] == 12.345
        assert entry["prompt_chars"] == 74769
        assert entry["model"] == "opus"
        assert "timestamp" in entry

    def test_record_ttft_appends_multiple_entries(self, tmp_path, monkeypatch):
        """Successive calls must append, not overwrite."""
        import json

        import agent.cold_start_metrics as csm

        metrics_file = tmp_path / "cold_start_metrics.jsonl"
        monkeypatch.setattr(csm, "_METRICS_FILE", metrics_file)

        csm.record_ttft(
            ttft_seconds=10.0,
            session_id="s1",
            session_type="eng",
            working_dir="/tmp",
            prompt_chars=1000,
            model="opus",
        )
        csm.record_ttft(
            ttft_seconds=5.0,
            session_id="s2",
            session_type="other",
            working_dir="/tmp",
            prompt_chars=0,
            model="sonnet",
        )

        lines = metrics_file.read_text().strip().splitlines()
        assert len(lines) == 2
        entries = [json.loads(line) for line in lines]
        assert entries[0]["session_id"] == "s1"
        assert entries[1]["session_id"] == "s2"

    def test_record_ttft_swallows_write_failure(self, tmp_path, monkeypatch):
        """record_ttft() must not raise even when the log directory is unwritable."""
        import agent.cold_start_metrics as csm

        # Point metrics file at a path whose parent cannot be created
        bad_file = tmp_path / "nonexistent_dir" / "subdir" / "metrics.jsonl"
        # Make tmp_path read-only so mkdir fails
        monkeypatch.setattr(csm, "_METRICS_FILE", bad_file)
        # Simulate permission error by monkeypatching open
        import builtins

        real_open = builtins.open

        def failing_open(path, *args, **kwargs):
            if "metrics" in str(path):
                raise PermissionError("disk full")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", failing_open)

        # Must NOT raise — instrumentation is best-effort
        csm.record_ttft(
            ttft_seconds=1.0,
            session_id="s",
            session_type="eng",
            working_dir="/tmp",
            prompt_chars=0,
            model="opus",
        )


class TestHarnessEnvStrip:
    """Issue #2100 AC7: the harness proc_env must never inherit any of the three
    ANTHROPIC_* auth vars, so a subscription-auth (OAuth) claude child cannot pick
    up an API-key base URL or auth token from the worker environment."""

    @pytest.mark.asyncio
    async def test_all_three_anthropic_vars_stripped_from_proc_env(self, monkeypatch):
        """ANTHROPIC_API_KEY / _BASE_URL / _AUTH_TOKEN are popped from proc_env,
        even when set in os.environ AND passed through the `env` kwarg."""
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        # Seed all three in the inherited environment.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak-key")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leak-token")

        captured = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["proc_env"] = proc_env
            return ("done", None, 0, None, None, None, 0, 0, None)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                # A caller-supplied ANTHROPIC_* must also be stripped (re-strip
                # after merge), not sneak back in.
                env={
                    "AGENT_SESSION_ID": "x",
                    "ANTHROPIC_API_KEY": "sk-caller-leak",
                    "ANTHROPIC_BASE_URL": "https://caller.example",
                    "ANTHROPIC_AUTH_TOKEN": "caller-token",
                },
                model="opus",
            )

        proc_env = captured["proc_env"]
        assert "ANTHROPIC_API_KEY" not in proc_env
        assert "ANTHROPIC_BASE_URL" not in proc_env
        assert "ANTHROPIC_AUTH_TOKEN" not in proc_env
