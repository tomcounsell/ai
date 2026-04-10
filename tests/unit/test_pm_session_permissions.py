"""Tests for PM session permission boundaries.

Verifies that:
- PM sessions get SESSION_TYPE=chat env var
- PM sessions get SENTRY_AUTH_TOKEN injected from ~/Desktop/Valor/.env
- PM sessions use bypassPermissions (not plan mode)
- PreToolUse hook blocks PM writes outside docs/
- PreToolUse hook allows PM writes inside docs/
- Non-PM sessions are not affected by PM write restrictions
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.hooks.pre_tool_use import pre_tool_use_hook

# --- PreToolUse hook: PM write restriction tests ---


class TestPMWriteRestriction:
    """PreToolUse hook should block PM writes outside docs/."""

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock()
        ctx.session_id = "test-pm-session"
        return ctx

    def _make_write_input(self, file_path, tool_name="Write"):
        return {
            "session_id": "sdk-session-1",
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path, "content": "test"},
            "tool_use_id": "tool-use-write",
        }

    def test_pm_blocked_from_writing_source_code(self, mock_context, monkeypatch):
        """PM session cannot write to source code files."""
        monkeypatch.setenv("SESSION_TYPE", "pm")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-1", mock_context))

        assert result.get("decision") == "block"
        assert "docs/" in result.get("reason", "")

    def test_pm_blocked_from_editing_source_code(self, mock_context, monkeypatch):
        """PM session cannot edit source code files."""
        monkeypatch.setenv("SESSION_TYPE", "pm")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input(
            "/Users/test/src/ai/bridge/telegram_bridge.py", tool_name="Edit"
        )
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-2", mock_context))

        assert result.get("decision") == "block"

    def test_pm_allowed_to_write_docs(self, mock_context, monkeypatch):
        """PM session can write to docs/ directory."""
        monkeypatch.setenv("SESSION_TYPE", "pm")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/docs/features/new-feature.md")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-3", mock_context))

        assert result.get("decision") != "block"

    def test_pm_allowed_to_write_nested_docs(self, mock_context, monkeypatch):
        """PM session can write to nested paths under docs/."""
        monkeypatch.setenv("SESSION_TYPE", "pm")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/docs/plans/my-plan.md")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-4", mock_context))

        assert result.get("decision") != "block"

    def test_non_pm_session_can_write_anywhere(self, mock_context, monkeypatch):
        """Non-PM sessions (no SESSION_TYPE) are not restricted."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-5", mock_context))

        assert result.get("decision") != "block"

    def test_dev_session_type_can_write_anywhere(self, mock_context, monkeypatch):
        """SESSION_TYPE=dev is not restricted by PM rules."""
        monkeypatch.setenv("SESSION_TYPE", "dev")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/agent/sdk_client.py")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-6", mock_context))

        assert result.get("decision") != "block"

    def test_pm_sensitive_file_still_blocked(self, mock_context, monkeypatch):
        """PM session writing to .env is blocked by sensitive path check (not PM check)."""
        monkeypatch.setenv("SESSION_TYPE", "pm")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = self._make_write_input("/Users/test/src/ai/.env")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-7", mock_context))

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()


# --- PreToolUse hook: PM Bash allowlist tests ---


class TestPMBashRestriction:
    """PreToolUse hook should restrict PM Bash calls to a read-only allowlist.

    Exercises the real ``pre_tool_use_hook`` -- no stubs. Environment is set via
    ``monkeypatch.setenv("SESSION_TYPE", "pm")`` exactly as the Write/Edit tests
    above. Each test calls ``asyncio.run(pre_tool_use_hook(...))``.
    """

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock()
        ctx.session_id = "test-pm-bash"
        return ctx

    def _make_bash_input(self, command: str):
        return {
            "session_id": "sdk-session-bash",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_use_id": "tool-use-bash",
        }

    def _run(self, command: str, mock_context):
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        return asyncio.run(
            pre_tool_use_hook(self._make_bash_input(command), "tu-bash", mock_context)
        )

    # -- Helper-level tests (no hook dispatch, just the pure function) ---------

    def test_helper_empty_string_returns_false(self):
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash("") is False

    def test_helper_whitespace_only_returns_false(self):
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash("   ") is False
        assert _is_pm_allowed_bash("\t\n ") is False

    def test_helper_none_returns_false_without_raising(self):
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash(None) is False

    def test_helper_git_status_allowed(self):
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash("git status") is True
        assert _is_pm_allowed_bash("git status --porcelain") is True

    def test_helper_git_dash_c_normalized(self):
        """``git -C <path> status`` normalizes to ``git status`` for allowlist."""
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash('git -C "/Users/v/src/ai" status') is True
        assert _is_pm_allowed_bash("git -C /Users/v/src/ai status") is True
        assert _is_pm_allowed_bash("git -C '/some/path' log --oneline -5") is True

    def test_helper_git_dash_c_does_not_bypass_mutation(self):
        """``git -C /x commit`` normalizes to ``git commit`` and stays blocked."""
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash('git -C "/x" commit -m "y"') is False
        assert _is_pm_allowed_bash("git -C /x push") is False

    def test_helper_metachar_injection_via_git_dash_c_blocked(self):
        """``git -C "$(rm -rf /)" status`` is blocked by the metacharacter
        guard BEFORE normalization strips the path argument.
        """
        from agent.hooks.pre_tool_use import _is_pm_allowed_bash

        assert _is_pm_allowed_bash('git -C "$(rm -rf /)" status') is False
        assert _is_pm_allowed_bash("git -C `pwd` status") is False

    # -- Blocked: incident commands --------------------------------------------

    def test_pm_blocked_from_rm_rf(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("rm -rf /Users/valorengels/src/ai"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"
        assert "PM session" in result.get("reason", "")
        assert "dev-session" in result.get("reason", "")

    def test_pm_blocked_from_git_clone(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git clone https://github.com/tomcounsell/ai.git /tmp/ai"),
                "tu-bash",
                mock_context,
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_git_commit(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input('git commit -m "wip"'), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_git_push(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git push origin main"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_git_reset_hard(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git reset --hard HEAD"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_git_checkout(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("git checkout main"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_pip_install(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("pip install requests"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_uv_sync(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("uv sync"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_from_rm_rf_venv(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("rm -rf .venv"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    # -- Blocked: metacharacter smuggling --------------------------------------

    def test_pm_blocked_metachar_pipe_smuggling(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git log | xargs rm -rf ."), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_command_substitution(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git status; rm -rf /tmp/x"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_redirection_and_chain(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git log > /tmp/x && rm -rf /tmp/x"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_dollar_parens(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("echo $(rm -rf /)"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_backtick_substitution(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("echo `rm -rf /`"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    # -- Blocked: gh api (deliberately excluded from allowlist) ----------------

    def test_pm_blocked_gh_api_get(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("gh api repos/tomcounsell/ai/issues/881"),
                "tu-bash",
                mock_context,
            )
        )
        assert result.get("decision") == "block"

    def test_pm_blocked_gh_api_post(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = self._run(
            "gh api repos/tomcounsell/ai/issues/881/comments --method POST --field body=hello",
            mock_context,
        )
        assert result.get("decision") == "block"

    # -- Allowed: read-only commands -------------------------------------------

    def test_pm_allowed_git_status(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("git status"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_git_log(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git log --oneline -10"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_git_diff(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("git diff main"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_git_show(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("git show HEAD"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_git_dash_c_status(self, mock_context, monkeypatch):
        """``git -C <token> status`` is allowed via the normalization step."""
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input('git -C "/Users/v/src/ai" status'), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_git_dash_c_log(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input('git -C "/repo" log --oneline -5'), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_gh_issue_view(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("gh issue view 881"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_gh_pr_list(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("gh pr list"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_gh_pr_view(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("gh pr view 123"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_tail_logs(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("tail logs/bridge.log"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_cat_docs_plans(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("cat docs/plans/pm-bash-discipline.md"),
                "tu-bash",
                mock_context,
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_valor_session_status(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("python -m tools.valor_session status --id abc"),
                "tu-bash",
                mock_context,
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_sdlc_stage_query(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("python -m tools.sdlc_stage_query"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_check_plan_freshness(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("python scripts/check_plan_freshness.py docs/plans/foo.md"),
                "tu-bash",
                mock_context,
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_grep_rl_docs_plans(self, mock_context, monkeypatch):
        """SDLC skill step 2a uses ``grep -rl #881 docs/plans/`` to locate plans."""
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("grep -rl #881 docs/plans/"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    def test_pm_allowed_grep_r_issue_in_plans(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input('grep -r "#881" docs/plans/'), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    # -- Non-PM sessions unrestricted ------------------------------------------

    def test_non_pm_session_bash_unrestricted(self, mock_context, monkeypatch):
        """``SESSION_TYPE=dev`` can still run mutating Bash commands."""
        monkeypatch.setenv("SESSION_TYPE", "dev")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("rm -rf /tmp/x"), "tu-bash", mock_context)
        )
        assert result.get("decision") != "block"

    def test_no_session_type_bash_unrestricted(self, mock_context, monkeypatch):
        """No ``SESSION_TYPE`` env var -> no PM restriction."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)
        result = asyncio.run(
            pre_tool_use_hook(
                self._make_bash_input("git push origin main"), "tu-bash", mock_context
            )
        )
        assert result.get("decision") != "block"

    # -- Edge-case input handling ----------------------------------------------

    def test_pm_empty_command_blocked(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(pre_tool_use_hook(self._make_bash_input(""), "tu-bash", mock_context))
        assert result.get("decision") == "block"

    def test_pm_whitespace_only_blocked(self, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("   "), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"

    def test_pm_missing_command_key_blocked(self, mock_context, monkeypatch):
        """A PM Bash call with no ``command`` key defaults to ``""`` and is blocked."""
        monkeypatch.setenv("SESSION_TYPE", "pm")
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = {
            "session_id": "sdk-missing",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {},
            "tool_use_id": "tu-missing",
        }
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-missing", mock_context))
        assert result.get("decision") == "block"

    # -- Ordering: sensitive-file check fires BEFORE PM allowlist --------------

    def test_pm_sensitive_file_check_runs_before_pm_allowlist(self, mock_context, monkeypatch):
        """For PM sessions, the sensitive-file check surfaces its specific
        error message instead of the generic PM-allowlist reason.

        We use ``cp x .env`` which is a plain command containing the sensitive
        path token -- it has no metacharacters, so it survives the metachar
        guard and reaches the sensitive-file check inside the Bash branch.
        The PM allowlist would ALSO block it (cp is not allowlisted), so the
        assertion distinguishes the two layers by inspecting the reason string.
        """
        monkeypatch.setenv("SESSION_TYPE", "pm")
        result = asyncio.run(
            pre_tool_use_hook(self._make_bash_input("cp /tmp/fake .env"), "tu-bash", mock_context)
        )
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "sensitive" in reason.lower(), (
            f"Expected sensitive-file reason first, got: {reason!r}"
        )
        assert "PM session" not in reason, (
            f"Sensitive-file check must fire BEFORE PM allowlist, got: {reason!r}"
        )


# --- SDK client: session_type and env var injection tests ---


class TestPMSessionEnvInjection:
    """ValorAgent should inject correct env vars for PM sessions."""

    def test_pm_session_gets_session_type_env(self):
        """PM session (session_type='pm') injects SESSION_TYPE=pm."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="pm")
        options = agent._create_options(session_id="test-session")

        assert options.env.get("SESSION_TYPE") == "pm"

    def test_non_pm_session_no_session_type_env(self):
        """Non-PM sessions without session_type don't inject SESSION_TYPE."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id="test-session")

        assert "SESSION_TYPE" not in options.env

    def test_sentry_token_injected_for_pm_session(self, tmp_path):
        """PM sessions get SENTRY_AUTH_TOKEN from ~/Desktop/Valor/.env."""
        # Create a fake ~/Desktop/Valor/.env
        valor_dir = tmp_path / "Desktop" / "Valor"
        valor_dir.mkdir(parents=True)
        (valor_dir / ".env").write_text("SENTRY_PERSONAL_TOKEN=test-sentry-token-abc\n")

        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="pm")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert options.env.get("SENTRY_AUTH_TOKEN") == "test-sentry-token-abc"

    def test_sentry_token_not_injected_for_non_pm(self):
        """Non-PM sessions don't get SENTRY_AUTH_TOKEN."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env

    def test_sentry_token_missing_file_no_error(self, tmp_path):
        """If ~/Desktop/Valor/.env doesn't exist, no error and no token."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env

    def test_sentry_token_missing_key_no_error(self, tmp_path):
        """If .env exists but has no SENTRY_PERSONAL_TOKEN, no token injected."""
        valor_dir = tmp_path / "Desktop" / "Valor"
        valor_dir.mkdir(parents=True)
        (valor_dir / ".env").write_text("SOME_OTHER_KEY=value\n")

        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")

        with patch("agent.sdk_client.Path.home", return_value=tmp_path):
            options = agent._create_options(session_id="test-session")

        assert "SENTRY_AUTH_TOKEN" not in options.env


class TestPMPermissionMode:
    """PM session should use bypassPermissions, not plan mode."""

    def test_pm_session_not_using_plan_mode(self):
        """Verify sdk_client does NOT set plan mode for PM sessions."""
        sdk_path = Path(__file__).parent.parent.parent / "agent" / "sdk_client.py"
        source = sdk_path.read_text()

        # Extract the PM session block (handles both enum and string forms)
        if "if _session_type == SessionType.PM:" in source:
            pm_block = source.split("if _session_type == SessionType.PM:")[1].split("elif")[0]
        else:
            pm_block = source.split('if _session_type == "pm":')[1].split("elif")[0]
        assert '"plan"' not in pm_block, (
            "PM session should not use plan permission mode. "
            "PM needs bypassPermissions with hook-based write restrictions."
        )

    def test_default_permission_mode_is_bypass(self):
        """Default permission mode should be bypassPermissions."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        assert agent.permission_mode == "bypassPermissions"

    def test_chat_session_inherits_default_bypass(self):
        """Chat session with no explicit permission_mode gets bypassPermissions."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(session_type="chat")
        assert agent.permission_mode == "bypassPermissions"
