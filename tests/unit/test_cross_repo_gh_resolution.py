"""Behavioral tests for cross-repo GH_REPO env var injection (issue #375).

These tests verify that the GH_REPO environment variable is correctly set
in the subprocess environment for cross-repo SDLC requests, replacing the
unreliable approach of relying on LLM markdown instructions.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

# AI_REPO_ROOT as defined in sdk_client.py
AI_REPO_ROOT = str(Path(__file__).parent.parent.parent)


class TestValorAgentGhRepo:
    """Test GH_REPO env var injection in ValorAgent._create_options()."""

    def _make_agent(self, gh_repo=None):
        """Create a ValorAgent with minimal config for testing."""
        with patch("agent.sdk_client.load_system_prompt", return_value="test prompt"):
            from agent.sdk_client import ValorAgent

            return ValorAgent(
                working_dir=AI_REPO_ROOT,
                gh_repo=gh_repo,
            )

    def test_gh_repo_set_in_env_when_provided(self):
        """GH_REPO should appear in env dict when gh_repo is a valid org/repo string."""
        agent = self._make_agent(gh_repo="tomcounsell/popoto")
        options = agent._create_options()
        assert options.env.get("GH_REPO") == "tomcounsell/popoto"

    def test_gh_repo_not_set_when_none(self):
        """GH_REPO should NOT appear in env dict when gh_repo is None (default)."""
        agent = self._make_agent(gh_repo=None)
        options = agent._create_options()
        assert "GH_REPO" not in options.env

    def test_gh_repo_not_set_when_empty_string(self):
        """GH_REPO should NOT appear in env dict when gh_repo is an empty string."""
        agent = self._make_agent(gh_repo="")
        options = agent._create_options()
        assert "GH_REPO" not in options.env


class TestBuildHarnessTurnInputGhRepo:
    """Test that build_harness_turn_input() injects GITHUB header for cross-repo SDLC."""

    POPOTO_PROJECT = {
        "name": "Popoto",
        "_key": "popoto",
        "working_directory": str(Path.home() / "src/popoto"),
        "github": {"org": "tomcounsell", "repo": "popoto"},
    }

    AI_PROJECT = {
        "name": "Valor AI",
        "_key": "valor",
        "working_directory": AI_REPO_ROOT,
        "github": {"org": "tomcounsell", "repo": "ai"},
    }

    NO_GITHUB_PROJECT = {
        "name": "NoGithub",
        "_key": "nogithub",
        "working_directory": str(Path.home() / "src/nogithub"),
    }

    @pytest.fixture(autouse=True)
    def mock_context(self):
        """Patch build_context_prefix for all tests."""
        with patch("bridge.context.build_context_prefix", return_value="CONTEXT"):
            yield

    @pytest.mark.asyncio
    async def test_cross_repo_sdlc_sets_github_header(self):
        """When classification=sdlc and project is cross-repo, GITHUB header should be set."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="SDLC issue 193",
            session_id="test-session-1",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            task_list_id=None,
            session_type="dev",
            sender_id=123,
            classification="sdlc",
            is_cross_repo=True,
        )

        assert "GITHUB: tomcounsell/popoto" in result

    @pytest.mark.asyncio
    async def test_ai_repo_sdlc_does_not_set_github_header(self):
        """When project is the ai repo itself (not cross-repo), no GITHUB header."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="SDLC issue 42",
            session_id="test-session-2",
            sender_name="Valor",
            chat_title="Dev: Valor",
            project=self.AI_PROJECT,
            task_list_id=None,
            session_type="dev",
            sender_id=456,
            classification="sdlc",
            is_cross_repo=False,
        )

        assert "GITHUB:" not in result

    @pytest.mark.asyncio
    async def test_non_sdlc_classification_does_not_set_github_header(self):
        """When classification is not sdlc, no GITHUB header."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="What is popoto?",
            session_id="test-session-3",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            task_list_id=None,
            session_type="dev",
            sender_id=123,
            classification="question",
            is_cross_repo=True,
        )

        assert "GITHUB:" not in result

    @pytest.mark.asyncio
    async def test_pm_mode_does_not_set_github_header(self):
        """PM mode projects should never set GITHUB header."""
        pm_project = {
            **self.POPOTO_PROJECT,
            "mode": "pm",
        }

        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="SDLC issue 193",
            session_id="test-session-4",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=pm_project,
            task_list_id=None,
            session_type="dev",
            sender_id=123,
            classification="sdlc",
            is_cross_repo=True,
        )

        assert "GITHUB:" not in result

    @pytest.mark.asyncio
    async def test_project_without_github_config_does_not_crash(self):
        """Projects without a github config key should not crash."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="SDLC issue 1",
            session_id="test-session-5",
            sender_name="Tom",
            chat_title="Dev: NoGithub",
            project=self.NO_GITHUB_PROJECT,
            task_list_id=None,
            session_type="dev",
            sender_id=789,
            classification="sdlc",
            is_cross_repo=True,
        )

        # No github config means no GITHUB header, but should not crash
        assert "GITHUB:" not in result

    @pytest.mark.asyncio
    async def test_enriched_message_contains_github_line(self):
        """The enriched message should contain the GITHUB: line for cross-repo SDLC."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="SDLC issue 193",
            session_id="test-session-6",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            task_list_id=None,
            session_type="dev",
            sender_id=123,
            classification="sdlc",
            is_cross_repo=True,
        )

        assert "GITHUB: tomcounsell/popoto" in result


class TestSessionProjectConfig:
    """Test that AgentSession.project_config carries the full project config
    through the pipeline, replacing the old _project_configs parallel registry.
    """

    def test_session_carries_project_config(self):
        """AgentSession.project_config stores and returns the full config dict."""
        from models.agent_session import AgentSession

        full_config = {
            "name": "Popoto",
            "working_directory": str(Path.home() / "src/popoto"),
            "_key": "popoto",
            "github": {"org": "tomcounsell", "repo": "popoto"},
            "telegram": {"groups": ["Dev: Popoto"]},
        }
        session = AgentSession(
            project_key="popoto",
            working_dir=str(Path.home() / "src/popoto"),
            project_config=full_config,
        )
        assert session.project_config.get("github") == {"org": "tomcounsell", "repo": "popoto"}
        assert session.project_config.get("working_directory") == str(Path.home() / "src/popoto")

    def test_session_without_project_config_defaults_to_none(self):
        """Sessions created without project_config have None (backward compat)."""
        from models.agent_session import AgentSession

        session = AgentSession(
            project_key="test",
            working_dir="/tmp/test",
        )
        # DictField with null=True defaults to None
        assert session.project_config is None or session.project_config == {}


class TestBuildHarnessTurnInputSkipPrefix:
    """Test skip_prefix parameter returns raw message without context headers (#976)."""

    @pytest.mark.asyncio
    async def test_skip_prefix_returns_raw_message(self):
        """When skip_prefix=True, returns the raw message unchanged."""
        from agent.sdk_client import build_harness_turn_input

        raw_msg = "Just the new user message"
        result = await build_harness_turn_input(
            message=raw_msg,
            session_id="test-session",
            sender_name="Test User",
            chat_title="Test Chat",
            project={"name": "test", "mode": "dev"},
            task_list_id="task-123",
            session_type="dev",
            sender_id=12345,
            skip_prefix=True,
        )
        assert result == raw_msg

    @pytest.mark.asyncio
    async def test_skip_prefix_false_includes_headers(self):
        """When skip_prefix=False (default), returns enriched message with headers."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="test message",
            session_id="test-session",
            sender_name="Test User",
            chat_title="Test Chat",
            project={"name": "test", "mode": "dev"},
            task_list_id="task-123",
            session_type="dev",
            sender_id=12345,
        )
        assert "SESSION_ID:" in result
        assert "MESSAGE: test message" in result

    @pytest.mark.asyncio
    async def test_skip_prefix_with_empty_message(self):
        """skip_prefix=True with empty message returns empty string."""
        from agent.sdk_client import build_harness_turn_input

        result = await build_harness_turn_input(
            message="",
            session_id="test-session",
            sender_name=None,
            chat_title=None,
            project=None,
            task_list_id=None,
            session_type=None,
            sender_id=None,
            skip_prefix=True,
        )
        assert result == ""
