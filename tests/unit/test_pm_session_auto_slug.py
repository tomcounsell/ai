"""Unit tests for PM-role auto-slug derivation in tools/valor_session.py (#1109).

When `valor-session create --role pm` is invoked WITHOUT `--slug` AND the
message contains an issue reference ("issue #N" or "issue N"), the CLI must:

1. Parse the issue number from the message.
2. Auto-set `slug = f"sdlc-{N}"`.
3. Provision a worktree for the slug via `agent.worktree_manager.get_or_create_worktree`.
4. Use the worktree path as the session's `working_dir`.

This prevents the PM session from inheriting the worker's branch state, which
was the root cause of the first-round SDLC session contamination (issue #1109).
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
    """Build a namespace mimicking argparse output for `create`."""
    defaults = dict(
        command="create",
        role="pm",
        message="",
        chat_id=None,
        parent=None,
        working_dir=None,
        project_key="valor",  # skip project resolution
        slug=None,
        model=None,
        json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestPMAutoSlugFromIssueReference:
    def test_pm_role_no_slug_with_hash_issue_reference_derives_slug(self, tmp_path):
        """PM create without --slug and 'issue #N' in message auto-derives sdlc-N."""
        args = _make_args(
            role="pm",
            message="Please run /sdlc on issue #1109 — it's a P0.",
        )

        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        wt_path = tmp_path / ".worktrees" / "sdlc-1109"
        wt_path.mkdir(parents=True)

        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                return_value=wt_path,
            ) as mock_wt,
            patch("agent.worktree_manager._validate_slug"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc == 0
        # The CLI must have auto-derived the slug.
        assert captured.get("slug") == "sdlc-1109"
        # Working dir should be the worktree, not the repo root.
        assert captured.get("working_dir") == str(wt_path)
        # The worktree manager must have been invoked.
        mock_wt.assert_called_once()

    def test_pm_role_no_slug_with_plain_issue_reference_derives_slug(self, tmp_path):
        """'issue 735' (no hash) also works."""
        args = _make_args(
            role="pm",
            message="Start the pipeline for issue 735",
        )

        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        wt_path = tmp_path / ".worktrees" / "sdlc-735"
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
        assert captured.get("slug") == "sdlc-735"

    def test_pm_role_explicit_slug_wins_over_issue_parse(self, tmp_path):
        """If --slug is explicit, the CLI must NOT override it with the issue parse."""
        args = _make_args(
            role="pm",
            slug="my-custom-slug",
            message="handle issue #1109",
        )

        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        wt_path = tmp_path / ".worktrees" / "my-custom-slug"
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
        assert captured.get("slug") == "my-custom-slug"

    def test_dev_role_no_slug_does_not_auto_derive(self, tmp_path):
        """Auto-derivation is PM-only. Dev sessions without --slug stay as-is."""
        args = _make_args(
            role="dev",
            message="work on issue #1109",
        )

        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        # Must NOT call get_or_create_worktree for dev without --slug.
        with (
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                side_effect=AssertionError("should not be called for dev"),
            ),
            patch("agent.worktree_manager._validate_slug"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 5)),
        ):
            rc = cmd_create(args)

        assert rc == 0
        # No slug assigned; dev session does not auto-derive.
        assert captured.get("slug") is None
