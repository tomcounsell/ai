"""Unit tests for PM-role refusal when no --slug and no issue reference (#1109).

When `valor-session create --role pm` is invoked WITHOUT `--slug` AND the
message does NOT contain an issue reference, the CLI must refuse with a
clear error and exit non-zero. This prevents PM sessions from silently
running on the worker's current branch (defect 1 in issue #1109).

Dev/teammate roles are unaffected — they may legitimately run without a
slug (e.g. ad-hoc conversations).
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import cmd_create  # noqa: E402


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        command="create",
        role="pm",
        message="",
        chat_id=None,
        parent=None,
        working_dir=None,
        project_key="valor",
        slug=None,
        model=None,
        json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPMRefuseWithoutIssue:
    def test_pm_role_no_slug_no_issue_reference_exits_nonzero(self, capsys):
        """PM create with no --slug and no issue ref must refuse."""
        args = _make_args(
            role="pm",
            message="Do something generic for me",
        )

        async def fake_push(**kwargs):
            raise AssertionError("should not be reached — CLI must refuse first")

        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc != 0
        captured = capsys.readouterr()
        # Error message must be explicit about the fix.
        err = captured.err.lower()
        assert "slug" in err or "issue" in err

    def test_pm_role_explicit_slug_bypasses_refusal(self, tmp_path):
        """Providing --slug is a valid way to bypass the issue-parse check."""
        args = _make_args(
            role="pm",
            slug="some-feature",
            message="Do something generic — no issue ref",
        )

        captured_kwargs: dict = {}

        async def fake_push(**kwargs):
            captured_kwargs.update(kwargs)

        wt_path = tmp_path / ".worktrees" / "some-feature"
        wt_path.mkdir(parents=True)

        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                return_value=wt_path,
            ),
            patch("agent.worktree_manager._validate_slug"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc == 0
        assert captured_kwargs.get("slug") == "some-feature"

    def test_dev_role_no_slug_no_issue_allowed(self):
        """Dev sessions may legitimately run without a slug (ad-hoc)."""
        args = _make_args(
            role="dev",
            message="fix a typo",
        )

        captured_kwargs: dict = {}

        async def fake_push(**kwargs):
            captured_kwargs.update(kwargs)

        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc == 0
        assert captured_kwargs.get("slug") is None

    def test_teammate_role_no_slug_no_issue_allowed(self):
        """Teammate sessions are conversational — no slug required."""
        args = _make_args(
            role="teammate",
            message="hey what's up",
        )

        captured_kwargs: dict = {}

        async def fake_push(**kwargs):
            captured_kwargs.update(kwargs)

        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc == 0
