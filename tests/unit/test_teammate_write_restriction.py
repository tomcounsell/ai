"""Tests for teammate session write enforcement and the PreToolUse denial taps.

Covers ``_teammate_is_allowed_write`` and the teammate branch in
``pre_tool_use_hook`` for Write/Edit/MultiEdit:

- Allow cases (docs/, .claude/, .github/, wiki/, skills/, top-level meta
  files, ~/work-vault/).
- Deny cases (source code, positional promiscuity, path traversal, symlink
  escape, top-level non-allowlist file, nested non-allowlist, out-of-project,
  empty/invalid).
- MultiEdit parity with Write/Edit.

It also covers the denial-telemetry taps this hook's deny branches carry
(plan #3081 Risk 1): every sensitive-path and teammate-write deny mirrors a
``pre_tool_use_denial`` event onto the session telemetry stream, which is
where ``tools.belt_baseline`` gets its escalation-ceiling denominator.
Those taps are telemetry ONLY -- ``TestDenialTelemetryIsFailQuiet`` pins that
a broken recorder cannot soften a deny.

The cwd contract is established with ``monkeypatch.chdir(tmp_path)`` so the
project root used by ``_teammate_is_allowed_write`` is a predictable temp
directory. The vault prefix is patched to live under ``tmp_path`` so symlink
tests don't touch the user's real ~/work-vault/.
"""

from __future__ import annotations

import asyncio
import os
import re
from unittest.mock import MagicMock, patch

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
        # Block message must include the Eng-session redirect command.
        assert "valor-session create --role eng" in result.get("reason", "")

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
        assert "valor-session create --role eng" in result.get("reason", "")

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

    def test_teammate_sensitive_file_still_blocked(self, fake_project, mock_context, monkeypatch):
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


# --- PreToolUse denial telemetry (plan #3081 Risk 1) ---------------------------


def _make_bash_input(command: str) -> dict:
    return {
        "session_id": "sdk-session-teammate",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": "tu-denial-bash",
    }


@pytest.fixture()
def denial_stream(tmp_path, monkeypatch, request):
    """Redirect the telemetry stream to tmp_path and give the hook a session id.

    ``_record_denial`` resolves the session id from the environment with the
    same precedence ``agent/tool_budget.py`` uses for its own denial tap
    (``session_id`` first, then ``agent_session_id`` -- ``VALOR_SESSION_ID``
    then ``AGENT_SESSION_ID`` on the hook side), so setting the env var is the
    whole setup. Yields a reader that returns the events written for it.
    """
    from agent import session_telemetry as telemetry_mod

    # Slugged: the telemetry stream is one file per session id, so a '/' from a
    # parametrized test name would land the write in a nonexistent subdirectory.
    session_id = "denial-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    monkeypatch.setattr(telemetry_mod, "_TELEMETRY_DIR_RELATIVE", tmp_path / "session_telemetry")
    monkeypatch.setenv("VALOR_SESSION_ID", session_id)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    def _events() -> list[dict]:
        return telemetry_mod.read_session_timeline(session_id)

    yield _events

    # The module caches an open file handle per session; a handle left open on
    # a torn-down tmp_path would leak into the next test.
    fh = telemetry_mod._handles.pop(session_id, None)
    if fh:
        try:
            fh.close()
        except Exception:  # swallow-ok: fixture teardown, handle may already be closed
            pass
    telemetry_mod._locks.pop(session_id, None)
    telemetry_mod._last_event_monotonic.pop(session_id, None)
    telemetry_mod._event_counts.pop(session_id, None)
    telemetry_mod._truncated.discard(session_id)


class TestDenialTelemetryTaps:
    """Every deny branch this hook owns mirrors a ``pre_tool_use_denial`` event
    onto the session telemetry stream, tagged with the cause
    ``tools.belt_baseline`` splits on. Without these taps the two causes the
    baseline excludes are never emitted and its exclusion filter never runs
    against real data."""

    def test_sensitive_path_write_deny_is_recorded(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        result = asyncio.run(pre_tool_use_hook(_make_write_input(".env"), "tu-den-1", mock_context))

        assert result.get("decision") == "block"
        events = denial_stream()
        assert len(events) == 1
        assert events[0]["type"] == "pre_tool_use_denial"
        assert events[0]["cause"] == "sensitive_path"
        assert events[0]["tool_name"] == "Write"

    def test_sensitive_fragment_write_deny_is_recorded(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        """The SENSITIVE_FRAGMENTS half of the same block, not just the
        exact-basename half."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        result = asyncio.run(
            pre_tool_use_hook(
                _make_write_input("config/secrets/token.json", tool_name="Edit"),
                "tu-den-2",
                mock_context,
            )
        )

        assert result.get("decision") == "block"
        events = denial_stream()
        assert [e["cause"] for e in events] == ["sensitive_path"]
        assert events[0]["tool_name"] == "Edit"

    def test_teammate_write_deny_is_recorded(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        result = asyncio.run(
            pre_tool_use_hook(
                _make_write_input("agent/sdk_client.py", tool_name="MultiEdit"),
                "tu-den-3",
                mock_context,
            )
        )

        assert result.get("decision") == "block"
        events = denial_stream()
        assert [e["cause"] for e in events] == ["teammate_write"]
        assert events[0]["tool_name"] == "MultiEdit"

    @pytest.mark.parametrize(
        "command",
        ["echo secret > .env", "cp /tmp/x .env", "tee -a .env"],
    )
    def test_bash_sensitive_write_deny_is_recorded(
        self, command, fake_project, mock_context, monkeypatch, denial_stream
    ):
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        result = asyncio.run(pre_tool_use_hook(_make_bash_input(command), "tu-den-4", mock_context))

        assert result.get("decision") == "block"
        events = denial_stream()
        assert [e["cause"] for e in events] == ["sensitive_path"]
        assert events[0]["tool_name"] == "Bash"

    def test_allowed_write_records_nothing(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        """The tap fires on denies only -- an allowed write must not inflate
        the baseline's denominator."""
        monkeypatch.setenv("SESSION_TYPE", "teammate")

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        result = asyncio.run(
            pre_tool_use_hook(_make_write_input("docs/notes.md"), "tu-den-5", mock_context)
        )

        assert result.get("decision") != "block"
        assert denial_stream() == []

    def test_missing_session_id_skips_recording_without_blocking(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        """No resolvable session id means no recording at all -- never an
        invented placeholder id, and never a softened deny.

        The probe watches the recorder itself rather than the resulting files:
        an invented id writes to a DIFFERENT stream, so reading this test's own
        stream (or even globbing the directory, which the module's per-session
        handle cache makes order-dependent) would report absence either way.
        Asserting the call never happens is the claim stated exactly."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        with patch("agent.session_telemetry.record_pre_tool_use_denial") as recorder:
            result = asyncio.run(
                pre_tool_use_hook(_make_write_input(".env"), "tu-den-6", mock_context)
            )

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()
        assert recorder.call_args_list == []


class TestDenialTelemetryIsFailQuiet:
    """The taps are telemetry. A recorder that explodes, or an import that
    fails, must leave the deny decision, its message, and its shape untouched
    -- the hook must never become dependent on the telemetry stream."""

    def _expected_teammate_block(self, fake_project, mock_context, monkeypatch):
        """The deny this hook produces with telemetry working normally."""
        monkeypatch.setenv("SESSION_TYPE", "teammate")
        from agent.hooks.pre_tool_use import pre_tool_use_hook

        return asyncio.run(
            pre_tool_use_hook(_make_write_input("agent/sdk_client.py"), "tu-fq-base", mock_context)
        )

    def test_raising_recorder_still_denies_identically(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        baseline = self._expected_teammate_block(fake_project, mock_context, monkeypatch)
        assert baseline.get("decision") == "block"

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        with patch(
            "agent.session_telemetry.record_pre_tool_use_denial",
            side_effect=RuntimeError("telemetry exploded"),
        ):
            result = asyncio.run(
                pre_tool_use_hook(_make_write_input("agent/sdk_client.py"), "tu-fq-1", mock_context)
            )

        assert result == baseline

    def test_failed_import_still_denies_identically(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        """Simulates the lazy import itself failing."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        with patch(
            "agent.hooks.session_resolver.inflight_cooldown_key",
            side_effect=ImportError("no module"),
        ):
            result = asyncio.run(
                pre_tool_use_hook(_make_write_input(".env"), "tu-fq-2", mock_context)
            )

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()
        assert ".env" in result.get("reason", "")

    def test_raising_recorder_still_denies_bash_sensitive_write(
        self, fake_project, mock_context, monkeypatch, denial_stream
    ):
        monkeypatch.delenv("SESSION_TYPE", raising=False)

        from agent.hooks.pre_tool_use import pre_tool_use_hook

        with patch(
            "agent.session_telemetry.record_pre_tool_use_denial",
            side_effect=RuntimeError("telemetry exploded"),
        ):
            result = asyncio.run(
                pre_tool_use_hook(_make_bash_input("cp /tmp/x .env"), "tu-fq-3", mock_context)
            )

        assert result.get("decision") == "block"
        assert "sensitive" in result.get("reason", "").lower()
