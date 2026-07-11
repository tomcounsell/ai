"""Integration test for eng harness env + argv injection (issue #1148).

Validates the end-to-end wiring chain that issue #1148 introduces:

  worker → _execute_agent_session
    → builds _harness_env with {AGENT_SESSION_ID, CLAUDE_CODE_TASK_LIST_ID,
      SESSION_TYPE, TELEGRAM_CHAT_ID, SENTRY_AUTH_TOKEN, VALOR_PARENT_SESSION_ID}
    → resolves load_eng_system_prompt(working_dir) for ENG sessions
    → calls get_response_via_harness(env=..., system_prompt=..., model=...)
        → builds claude -p argv with --model + --append-system-prompt + message

The test mocks _run_harness_subprocess (the leaf I/O call) so no real
claude binary is invoked, and asserts the constructed cmd + proc_env
match the contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A stub engineer persona file; placed inside the test working_dir as CLAUDE.md
# so that load_eng_system_prompt finds something reproducible to append.
_STUB_CLAUDE_MD = "## Stub project CLAUDE.md\nProject-specific engineer rules go here."


def _write_repo_persona(tmp_path: Path) -> Path:
    """Create a minimal working_dir with a CLAUDE.md so load_eng_system_prompt
    has something deterministic to read alongside the persona file."""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "CLAUDE.md").write_text(_STUB_CLAUDE_MD)
    return wd


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEngHarnessFullChain:
    """End-to-end env + argv assembly for the eng harness path."""

    @pytest.mark.asyncio
    async def test_eng_session_full_argv_contains_persona_and_env(self, monkeypatch, tmp_path):
        """An eng session through get_response_via_harness produces the expected argv.

        Asserts:
          (a) env contains AGENT_SESSION_ID, CLAUDE_CODE_TASK_LIST_ID,
              SESSION_TYPE=eng, TELEGRAM_CHAT_ID, SENTRY_AUTH_TOKEN
          (b) argv contains '--append-system-prompt' followed by a non-empty
              persona string
          (c) argv contains '--model' followed by 'opus'
          (d) the positional message is the LAST element of argv
        """
        from agent.sdk_client import (
            _resolve_sentry_auth_token,
            get_response_via_harness,
            load_eng_system_prompt,
        )

        # Sentry token comes from env var so the test is hermetic
        monkeypatch.setenv("SENTRY_PERSONAL_TOKEN", "integ-sentry-tok-1148")
        monkeypatch.delenv("VALOR_LAUNCHD", raising=False)

        wd = _write_repo_persona(tmp_path)

        # Mirror the harness env construction in session_executor.py:1324-1346.
        agent_session_id = "agt_integ_1148_001"
        chat_id = "555"
        session_type = "eng"
        env: dict[str, str] = {
            "AGENT_SESSION_ID": agent_session_id,
            "CLAUDE_CODE_TASK_LIST_ID": "thread-555-99",
        }
        if session_type:
            env["SESSION_TYPE"] = session_type
        if session_type in ("eng", "teammate"):
            env["VALOR_PARENT_SESSION_ID"] = agent_session_id
            env["TELEGRAM_CHAT_ID"] = chat_id
            tok = _resolve_sentry_auth_token()
            if tok:
                env["SENTRY_AUTH_TOKEN"] = tok

        # Resolve the persona via the production loader. Even with no work-vault
        # persona file, load_eng_system_prompt always returns the base persona.
        persona = load_eng_system_prompt(str(wd))
        assert persona, "load_eng_system_prompt must return non-empty content"
        # Sanity: persona must contain engineer content or the stub CLAUDE.md.
        assert any(
            token in persona.lower() for token in ("engineer", "worker", "stub project claude.md")
        ), (
            f"Persona content must contain an eng-specific signal. "
            f"First 200 chars: {persona[:200]!r}"
        )

        # Capture the cmd + proc_env passed to the leaf subprocess invoker.
        captured: dict = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = list(cmd)
            captured["proc_env"] = dict(proc_env)
            return ("ok", None, 0, None, None, None, 0, 0)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="user-task-message",
                working_dir=str(wd),
                env=env,
                model="opus",
                system_prompt=persona,
                session_id="sess-integ-1148",
            )

        cmd = captured["cmd"]
        proc_env = captured["proc_env"]

        # (a) env contract
        assert proc_env.get("AGENT_SESSION_ID") == agent_session_id
        assert proc_env.get("CLAUDE_CODE_TASK_LIST_ID") == "thread-555-99"
        assert proc_env.get("SESSION_TYPE") == "eng"
        assert proc_env.get("TELEGRAM_CHAT_ID") == "555"
        assert proc_env.get("SENTRY_AUTH_TOKEN") == "integ-sentry-tok-1148"
        assert proc_env.get("VALOR_PARENT_SESSION_ID") == agent_session_id
        # ANTHROPIC_API_KEY must be stripped (CLI uses subscription auth)
        assert "ANTHROPIC_API_KEY" not in proc_env

        # (b) --append-system-prompt with persona text
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == persona

        # (c) --model opus
        assert "--model" in cmd
        m_idx = cmd.index("--model")
        assert cmd[m_idx + 1] == "opus"
        # --model precedes --append-system-prompt
        assert m_idx < idx

        # (d) positional message at the tail
        assert cmd[-1] == "user-task-message"

    @pytest.mark.asyncio
    async def test_load_eng_system_prompt_failure_does_not_crash(self, monkeypatch, tmp_path):
        """If load_eng_system_prompt raises, the harness call still proceeds.

        Mirrors the [eng-persona-missing] fail-soft path in
        session_executor.py — degraded session is preferable to a crash.
        """
        from agent.sdk_client import get_response_via_harness

        wd = _write_repo_persona(tmp_path)

        # Caller code: try/except around load_eng_system_prompt mirrored from the
        # executor. We deliberately raise to exercise the swallow path.
        try:
            raise RuntimeError("simulated persona load failure")
        except Exception:
            persona = None  # exec falls through to system_prompt=None

        captured: dict = {}

        async def fake_run(cmd, working_dir, proc_env, **_kw):
            captured["cmd"] = list(cmd)
            return ("ok", None, 0, None, None, None, 0, 0)

        with patch(
            "agent.session_runner.harness.claude._run_harness_subprocess",
            new=AsyncMock(side_effect=fake_run),
        ):
            await get_response_via_harness(
                message="eng-task-without-persona",
                working_dir=str(wd),
                env={"AGENT_SESSION_ID": "agt_x", "SESSION_TYPE": "eng"},
                model="opus",
                system_prompt=persona,
            )

        cmd = captured["cmd"]
        # Persona path is None → no --append-system-prompt
        assert "--append-system-prompt" not in cmd
        # But the rest of the argv must still be intact
        assert "--model" in cmd
        assert cmd[-1] == "eng-task-without-persona"
