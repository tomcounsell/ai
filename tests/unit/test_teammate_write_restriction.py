"""Tests for teammate session write enforcement.

Covers ``_teammate_is_allowed_write`` and the teammate branch in
``pre_tool_use_hook`` for Write/Edit/MultiEdit:

- Allow cases (docs/, .claude/, .github/, wiki/, skills/, top-level meta
  files, ~/work-vault/).
- Deny cases (source code, positional promiscuity, path traversal, symlink
  escape, top-level non-allowlist file, nested non-allowlist, out-of-project,
  empty/invalid).
- MultiEdit parity with Write/Edit.

The cwd contract is established with ``monkeypatch.chdir(tmp_path)`` so the
project root used by ``_teammate_is_allowed_write`` is a predictable temp
directory. The vault prefix is patched to live under ``tmp_path`` so symlink
tests don't touch the user's real ~/work-vault/.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_write_input(file_path: str, tool_name: str = "Write") -> dict:
    return {
        "session_id": "sdk-session-teammate",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path, "content": "x"},
        "tool_use_id": "tu-teammate",
    }


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.session_id = "test-teammate-session"
    return ctx


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Establish a fake project root + vault under tmp_path.

    - cwd is set to ``tmp_path/project`` so ``os.getcwd()`` returns it.
    - A fake vault is created at ``tmp_path/vault/`` and the
      ``TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES`` constant is replaced to point
      at it (so the symlink tests don't touch the real ~/work-vault/).
    """
    project = tmp_path / "project"
    project.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "agent.hooks.pre_tool_use.TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES",
        (str(vault) + os.sep,),
    )
    return {"project": project, "vault": vault}


# --- Unit tests on _teammate_is_allowed_write ---------------------------------


class TestTeammateAllowedWriteAllow:
    """Paths that MUST be allowed."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "docs/foo.md",
            "docs/features/x.md",
            "docs/plans/teammate-allowlist-enforce.md",
            ".claude/skills/y.md",
            ".claude/settings.local.json",
            ".github/workflows/z.yml",
            "wiki/Home.md",
            "skills/custom.md",
            "README.md",
            "CHANGELOG.md",
            "CLAUDE.md",
            "LICENSE",
            "NOTICE",
            "CNAME",
            ".gitignore",
            ".gitattributes",
            ".editorconfig",
            "PHASE_1.md",  # top-level *.md
            "MODERNIZATION_PLAN.md",
        ],
    )
    def test_allowed_relative_paths(self, fake_project, rel_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(rel_path) is True

    def test_vault_path_allowed(self, fake_project):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        vault_file = str(fake_project["vault"] / "notes" / "n.md")
        assert _teammate_is_allowed_write(vault_file) is True


class TestTeammateAllowedWriteDenyCode:
    """Source-code paths that MUST be denied."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "agent/sdk_client.py",
            "agent/hooks/pre_tool_use.py",
            "bridge/telegram_bridge.py",
            "worker/__main__.py",
            "tools/foo.py",
            "tests/unit/x.py",
            "apps/web/page.tsx",
            "packages/core/index.ts",
            "pyproject.toml",
            "package.json",
            "Makefile",
            "Dockerfile",
            "manage.py",
        ],
    )
    def test_code_paths_denied(self, fake_project, rel_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(rel_path) is False


class TestTeammateAllowedWritePositionalPromiscuity:
    """Substrings that look like allowed dir names must NOT match when
    they appear deeper in the path."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "agent/docs_handler/foo.py",
            "tools/wiki_scraper.py",
            "agent/skills_router.py",
            "apps/api/README.md",  # nested README not allowed
        ],
    )
    def test_substring_matches_denied(self, fake_project, rel_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(rel_path) is False


class TestTeammateAllowedWritePathTraversal:
    """Path-traversal via ``..`` must be normalized away."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "docs/../agent/foo.py",
            ".claude/../bridge/x.py",
            "docs/sub/../../agent/y.py",
        ],
    )
    def test_traversal_denied(self, fake_project, rel_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(rel_path) is False


class TestTeammateAllowedWriteSymlinkEscape:
    """Symlinks under an allowed dir that point at a code path must be
    rejected by the realpath pass."""

    def test_symlink_to_code_dir_denied(self, fake_project):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        project = fake_project["project"]
        (project / "docs").mkdir()
        (project / "agent").mkdir()
        # Create the symlink: docs/escape -> ../agent
        os.symlink(
            str(project / "agent"),
            str(project / "docs" / "escape"),
        )

        # The substring of the input path contains "docs/" so the
        # normpath pass would let it through. The realpath pass should
        # resolve docs/escape to ../agent and reject.
        assert _teammate_is_allowed_write("docs/escape/sdk_client.py") is False


class TestTeammateAllowedWriteBareDirNames:
    """A file literally named ``docs`` (no extension) at project root must
    NOT match the directory rule — covered by the ``len(parts) > 1`` guard."""

    @pytest.mark.parametrize(
        "rel_path",
        ["docs", ".claude", "wiki", "skills", ".github"],
    )
    def test_bare_top_level_dir_names_denied(self, fake_project, rel_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(rel_path) is False


class TestTeammateAllowedWriteOutOfProject:
    """Absolute paths outside the project root (and outside the vault)
    must be denied."""

    @pytest.mark.parametrize(
        "abs_path",
        ["/tmp/foo.md", "/etc/passwd", "/var/log/system.log"],
    )
    def test_outside_project_root_denied(self, fake_project, abs_path):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(abs_path) is False


class TestTeammateAllowedWriteEmpty:
    """Empty/invalid input default-denies."""

    def test_empty_string(self, fake_project):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write("") is False

    def test_none_input(self, fake_project):
        from agent.hooks.pre_tool_use import _teammate_is_allowed_write

        assert _teammate_is_allowed_write(None) is False  # type: ignore[arg-type]


# --- Integration tests: full hook with SESSION_TYPE=teammate -------------------


class TestTeammateHookBlocks:
    """The pre_tool_use_hook should block teammate Write/Edit/MultiEdit
    to disallowed paths and allow them to allowlisted paths."""

    def test_teammate_blocked_from_source_code_write(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("agent/sdk_client.py", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-1", mock_context))

        assert result.get("decision") == "block"
        # Block message must include the Dev-session redirect command.
        assert "valor-session create --role dev" in result.get("reason", "")

    def test_teammate_blocked_from_source_code_edit(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("bridge/telegram_bridge.py", tool_name="Edit")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-2", mock_context))

        assert result.get("decision") == "block"

    def test_teammate_blocked_via_multiedit(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("agent/sdk_client.py", tool_name="MultiEdit")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-3", mock_context))

        assert result.get("decision") == "block"
        assert "valor-session create --role dev" in result.get("reason", "")

    def test_teammate_allowed_docs_write(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("docs/features/x.md", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-4", mock_context))

        assert result.get("decision") != "block"

    def test_teammate_allowed_claude_write(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input(".claude/skills/foo.md", tool_name="Edit")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-5", mock_context))

        assert result.get("decision") != "block"

    def test_teammate_allowed_vault_write(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        vault_file = str(fake_project["vault"] / "notes" / "n.md")
        input_data = _make_write_input(vault_file, tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-6", mock_context))

        assert result.get("decision") != "block"

    def test_teammate_traversal_blocked(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("docs/../agent/sdk_client.py", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-7", mock_context))

        assert result.get("decision") == "block"

    def test_teammate_symlink_escape_blocked(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        project = fake_project["project"]
        (project / "docs").mkdir()
        (project / "agent").mkdir()
        os.symlink(str(project / "agent"), str(project / "docs" / "escape"))

        input_data = _make_write_input("docs/escape/sdk_client.py", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-8", mock_context))

        assert result.get("decision") == "block"

    def test_teammate_sensitive_file_still_blocked(
        self, fake_project, mock_context, monkeypatch
    ):
        """Sensitive file (.env) is blocked by the sensitive-path check,
        with the sensitive-path block message — not the teammate redirect."""
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input(".env", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-tm-9", mock_context))

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()


class TestTeammateBashAuditLog:
    """Bash is NOT blocked for teammate sessions, but every command is
    audit-logged with the ``[teammate-audit]`` tag."""

    def test_teammate_bash_not_blocked(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = {
            "session_id": "sdk-session-teammate",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "./scripts/valor-service.sh status"},
            "tool_use_id": "tu-bash-1",
        }
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-bash-1", mock_context))

        assert result.get("decision") != "block"

    def test_teammate_bash_audit_logged(self, fake_project, mock_context, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("SESSION_TYPE", "teammate")
        caplog.set_level(logging.INFO, logger="agent.hooks.pre_tool_use")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = {
            "session_id": "sdk-session-teammate",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_use_id": "tu-bash-2",
        }
        asyncio.run(pre_tool_use_hook(input_data, "tu-bash-2", mock_context))

        audit_lines = [r for r in caplog.records if "[teammate-audit]" in r.getMessage()]
        assert audit_lines, "expected at least one [teammate-audit] log line"
        assert any("echo hello" in r.getMessage() for r in audit_lines)

    def test_teammate_bash_sensitive_file_still_blocked(
        self, fake_project, mock_context, monkeypatch
    ):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = {
            "session_id": "sdk-session-teammate",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "cp x.txt .env"},
            "tool_use_id": "tu-bash-3",
        }
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-bash-3", mock_context))

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()


class TestNonTeammateUnaffected:
    """Sessions without SESSION_TYPE=teammate must not be restricted by
    teammate rules."""

    def test_no_session_type_can_write_code(self, fake_project, mock_context, monkeypatch):
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("agent/sdk_client.py", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-other-1", mock_context))

        assert result.get("decision") != "block"

    def test_dev_session_type_can_write_code(self, fake_project, mock_context, monkeypatch):
        monkeypatch.setenv("SESSION_TYPE", "dev")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        input_data = _make_write_input("agent/sdk_client.py", tool_name="Write")
        result = asyncio.run(pre_tool_use_hook(input_data, "tu-other-2", mock_context))

        assert result.get("decision") != "block"
