"""Tests for the Layer 1 foreground-only subagent hook (#2420).

``_enforce_foreground_subagents`` denies a backgrounded ``Task``/``Agent``
spawn inside a positively-resolved ``eng`` AgentSession via ``sys.exit(2)``
(the Claude Code block convention), and fails OPEN — observably — on an
unresolved session, a resolution infra error, an internal bug, a non-spawn
tool, a teammate session, or an explicit ``run_in_background: false``.

The pre_tool_use hook is a standalone script with non-standard imports, so we
add its directory to ``sys.path`` and import the function directly.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent.parent.parent / ".claude" / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from pre_tool_use import _enforce_foreground_subagents  # noqa: E402


class _Session:
    """Minimal positively-resolved AgentSession stand-in."""

    def __init__(self, session_type):
        self.session_type = session_type


class _RaisingSession:
    """Resolves, but reading ``session_type`` raises → internal-error path."""

    @property
    def session_type(self):
        raise RuntimeError("attribute boom")


def _hook_input(tool_name="Task", tool_input=None):
    hi = {"tool_name": tool_name, "session_id": "s-123"}
    if tool_input is not None:
        hi["tool_input"] = tool_input
    return hi


def _run(hook_input, session=..., resolve_error=None):
    """Invoke the guard with a patched ``_resolve_cli_session``.

    ``session=...`` (the default sentinel) means "don't patch resolution"
    (used for the non-spawn-tool early return). Otherwise patch it to return
    ``session`` or, when ``resolve_error`` is set, to raise it.
    """
    if resolve_error is not None:
        cm = patch("pre_tool_use._resolve_cli_session", side_effect=resolve_error)
    elif session is not ...:
        cm = patch("pre_tool_use._resolve_cli_session", return_value=session)
    else:
        cm = patch("pre_tool_use._resolve_cli_session")
    with cm:
        _enforce_foreground_subagents(hook_input)


class TestDeny:
    @pytest.mark.parametrize("tool_name", ["Task", "Agent"])
    def test_eng_background_true_denies(self, tool_name, capsys):
        with pytest.raises(SystemExit) as exc:
            _run(
                _hook_input(tool_name, {"run_in_background": True}),
                session=_Session("eng"),
            )
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "HOOK BLOCK" in err
        assert "run_in_background: false" in err
        assert "2420" in err

    @pytest.mark.parametrize("tool_name", ["Task", "Agent"])
    def test_eng_background_omitted_denies(self, tool_name, capsys):
        # Omission resolves to the background default → deny.
        with pytest.raises(SystemExit) as exc:
            _run(_hook_input(tool_name, {}), session=_Session("eng"))
        assert exc.value.code == 2
        assert "HOOK BLOCK" in capsys.readouterr().err

    def test_eng_no_tool_input_key_denies(self, capsys):
        # Missing tool_input entirely → treated as background → deny.
        with pytest.raises(SystemExit) as exc:
            _run(_hook_input("Task", None), session=_Session("eng"))
        assert exc.value.code == 2


class TestAllow:
    def test_eng_explicit_foreground_allows(self):
        # Explicit run_in_background: false → foreground → allow (no exit).
        _run(_hook_input("Task", {"run_in_background": False}), session=_Session("eng"))

    def test_teammate_allows(self):
        _run(_hook_input("Task", {"run_in_background": True}), session=_Session("teammate"))

    def test_non_spawn_tool_allows(self):
        # Not a subagent spawn → allow without even resolving the session.
        _run(_hook_input("Bash", {"run_in_background": True}))


class TestFailOpen:
    def test_unresolved_session_allows_and_logs(self, capsys):
        _run(_hook_input("Task", {"run_in_background": True}), session=None)
        err = capsys.readouterr().err
        assert "[foreground-guard] fail-open: unresolved-session" in err

    def test_resolution_error_allows_and_logs(self, capsys):
        _run(
            _hook_input("Task", {"run_in_background": True}),
            resolve_error=RuntimeError("redis down"),
        )
        err = capsys.readouterr().err
        assert "[foreground-guard] fail-open: resolution-error" in err

    def test_internal_error_allows_and_logs(self, capsys):
        # A bug reading session_type must fail OPEN with an observable line.
        _run(_hook_input("Task", {"run_in_background": True}), session=_RaisingSession())
        err = capsys.readouterr().err
        assert "[foreground-guard] fail-open: internal-error" in err
