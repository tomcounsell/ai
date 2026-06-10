"""Tests for the `valor` CLI wrapper (tools/valor_cli.py).

The wrapper is a pure delegation layer over `tools.valor_session`. These
tests cover the three things the feature doc calls out as uncovered risk:

1. The positional-shortcut rewrite (`valor "prompt"` → `valor agent-session
   "prompt"`), including the KNOWN_SUBCOMMANDS allowlist staying in sync
   with the subparser declarations.
2. The per-subcommand argparse-namespace translation — every attribute the
   underlying `cmd_*` function reads must be present on the namespace the
   wrapper builds (a missing attribute is a runtime AttributeError).
3. The error paths: missing prompt (exit 2), bare `valor` (help + exit 1),
   and flags-first argv (argparse exit 2, never mangled into a prompt).

All `cmd_*` calls are mocked — no Redis, no worker, no session creation.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from tools import valor_cli
from tools.valor_cli import KNOWN_SUBCOMMANDS, _build_parser, main


def _parser_subcommands() -> set[str]:
    """Extract the declared subparser names from the wrapper's parser."""
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    raise AssertionError("no subparsers found on the valor parser")


class TestKnownSubcommandsParity:
    def test_known_subcommands_matches_parser(self):
        """A new subparser without an allowlist entry gets silently
        rewritten into a prompt; an allowlist entry without a subparser is
        an unreachable name. Both are drift — fail loudly here."""
        assert KNOWN_SUBCOMMANDS == _parser_subcommands()


class TestPositionalShortcut:
    def test_bare_prompt_routes_to_create(self):
        with patch("tools.valor_session.cmd_create", return_value=0) as cmd:
            rc = main(["fix the bug in app.py"])
        assert rc == 0
        ns = cmd.call_args[0][0]
        assert ns.message == "fix the bug in app.py"
        assert ns.role == "pm"

    def test_prompt_with_trailing_flags_routes_to_create(self):
        with patch("tools.valor_session.cmd_create", return_value=0) as cmd:
            rc = main(["build it", "--role", "dev", "--slug", "feature-x"])
        assert rc == 0
        ns = cmd.call_args[0][0]
        assert ns.message == "build it"
        assert ns.role == "dev"
        assert ns.slug == "feature-x"

    def test_explicit_subcommand_equivalent_to_shortcut(self):
        with patch("tools.valor_session.cmd_create", return_value=0) as cmd:
            main(["agent-session", "do the thing"])
            shortcut_ns = cmd.call_args[0][0]
            main(["do the thing"])
            explicit_ns = cmd.call_args[0][0]
        assert vars(shortcut_ns) == vars(explicit_ns)

    def test_subcommand_name_is_never_treated_as_prompt(self):
        """`valor list` must dispatch to cmd_list, not create a session
        whose prompt is the word 'list'."""
        with (
            patch("tools.valor_session.cmd_list", return_value=0) as cmd_list,
            patch("tools.valor_session.cmd_create", return_value=0) as cmd_create,
        ):
            rc = main(["list"])
        assert rc == 0
        cmd_list.assert_called_once()
        cmd_create.assert_not_called()

    def test_flags_first_argv_is_not_mangled(self):
        """A leading flag must reach argparse untouched. The top-level
        parser has no --role, so argparse exits 2 — it must NOT become
        `agent-session --role ...`."""
        with pytest.raises(SystemExit) as exc:
            main(["--role", "dev", "msg"])
        assert exc.value.code == 2

    def test_help_flag_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0


class TestErrorPaths:
    def test_no_args_prints_help_and_returns_1(self, capsys):
        rc = main([])
        assert rc == 1
        assert "agent-session" in capsys.readouterr().out

    def test_agent_session_without_prompt_returns_2(self, capsys):
        rc = main(["agent-session"])
        assert rc == 2
        assert "missing prompt" in capsys.readouterr().err


class TestNamespaceTranslation:
    """Every attribute the underlying cmd_* reads must exist on the
    namespace the wrapper passes. The attribute sets below mirror the
    `args.<attr>` / `getattr(args, "<attr>")` usage in tools/valor_session.py."""

    def test_create_namespace_complete(self):
        with patch("tools.valor_session.cmd_create", return_value=0) as cmd:
            main(
                [
                    "agent-session",
                    "plan issue #1615",
                    "--role",
                    "teammate",
                    "--model",
                    "sonnet",
                    "--slug",
                    "sdlc-1615",
                    "--project-key",
                    "valor",
                    "--parent",
                    "abc123",
                    "--chat-id",
                    "42",
                    "--needs-real-chrome",
                    "--json",
                ]
            )
        ns = cmd.call_args[0][0]
        assert ns.message == "plan issue #1615"
        assert ns.role == "teammate"
        assert ns.model == "sonnet"
        assert ns.slug == "sdlc-1615"
        assert ns.project_key == "valor"
        assert ns.parent == "abc123"
        assert ns.chat_id == "42"
        assert ns.needs_real_chrome is True
        assert ns.json is True

    def test_create_namespace_defaults(self):
        with patch("tools.valor_session.cmd_create", return_value=0) as cmd:
            main(["agent-session", "hello"])
        ns = cmd.call_args[0][0]
        for attr in (
            "role",
            "message",
            "chat_id",
            "parent",
            "project_key",
            "slug",
            "model",
            "needs_real_chrome",
            "json",
        ):
            assert hasattr(ns, attr), f"cmd_create reads args.{attr}"
        assert ns.role == "pm"
        assert ns.chat_id is None
        assert ns.needs_real_chrome is False

    def test_list_namespace(self):
        with patch("tools.valor_session.cmd_list", return_value=0) as cmd:
            main(["list", "--status", "running,pending", "--role", "dev", "--limit", "5"])
        ns = cmd.call_args[0][0]
        assert ns.status == "running,pending"
        assert ns.role == "dev"
        assert ns.limit == 5
        assert ns.json is False

    def test_status_namespace(self):
        with patch("tools.valor_session.cmd_status", return_value=0) as cmd:
            main(["status", "abc123", "--full-message"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"
        assert ns.full_message is True
        assert ns.json is False

    def test_steer_namespace(self):
        with patch("tools.valor_session.cmd_steer", return_value=0) as cmd:
            main(["steer", "abc123", "stop after critique"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"
        assert ns.message == "stop after critique"

    def test_kill_namespace_by_id(self):
        with patch("tools.valor_session.cmd_kill", return_value=0) as cmd:
            main(["kill", "abc123"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"
        assert ns.all is False

    def test_kill_namespace_all(self):
        with patch("tools.valor_session.cmd_kill", return_value=0) as cmd:
            main(["kill", "--all"])
        ns = cmd.call_args[0][0]
        assert ns.id is None
        assert ns.all is True

    def test_resume_namespace(self):
        with patch("tools.valor_session.cmd_resume", return_value=0) as cmd:
            main(["resume", "abc123", "pick it back up"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"
        assert ns.message == "pick it back up"

    def test_inspect_namespace(self):
        with patch("tools.valor_session.cmd_inspect", return_value=0) as cmd:
            main(["inspect", "abc123", "--json"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"
        assert ns.json is True

    def test_children_namespace(self):
        with patch("tools.valor_session.cmd_children", return_value=0) as cmd:
            main(["children", "abc123"])
        ns = cmd.call_args[0][0]
        assert ns.id == "abc123"

    def test_release_namespace(self):
        with patch("tools.valor_session.cmd_release", return_value=0) as cmd:
            main(["release", "--pr", "1615"])
        ns = cmd.call_args[0][0]
        assert ns.pr == 1615

    def test_exit_code_propagates(self):
        with patch("tools.valor_session.cmd_kill", return_value=3):
            assert main(["kill", "abc123"]) == 3


class TestUnderlyingAttrContract:
    """Static guard: parse tools/valor_session.py and assert the wrapper's
    namespaces provide every `args.<attr>` each delegated cmd_* reads.
    Catches a flag added to valor-session that the wrapper forgot."""

    WRAPPER_PROVIDES = {
        "cmd_create": {
            "role",
            "message",
            "chat_id",
            "parent",
            "project_key",
            "slug",
            "model",
            "needs_real_chrome",
            "json",
        },
        "cmd_list": {"status", "role", "limit", "json"},
        "cmd_status": {"id", "full_message", "json"},
        "cmd_steer": {"id", "message", "json"},
        "cmd_kill": {"id", "all", "json"},
        "cmd_resume": {"id", "message", "json"},
        "cmd_inspect": {"id", "json"},
        "cmd_children": {"id", "json"},
        "cmd_release": {"pr", "json"},
    }

    def test_wrapper_covers_all_args_reads(self):
        import ast
        import inspect

        from tools import valor_session

        src = inspect.getsource(valor_session)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name in self.WRAPPER_PROVIDES):
                continue
            reads: set[str] = set()
            for n in ast.walk(node):
                if (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "args"
                ):
                    reads.add(n.attr)
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr"
                    and n.args
                    and isinstance(n.args[0], ast.Name)
                    and n.args[0].id == "args"
                    and isinstance(n.args[1], ast.Constant)
                ):
                    reads.add(n.args[1].value)
            missing = reads - self.WRAPPER_PROVIDES[node.name]
            assert not missing, (
                f"{node.name} reads args attributes the valor wrapper does not "
                f"provide: {sorted(missing)} — update tools/valor_cli.py "
                f"_to_*_namespace and this test"
            )

    def test_module_constant_exists(self):
        # The feature doc references the constant by name; keep it stable.
        assert isinstance(valor_cli.KNOWN_SUBCOMMANDS, set)
