#!/usr/bin/env python3
"""`valor` — the agent-session CLI.

Clean, single-purpose wrapper around `valor-session`. The interface is
positional-first: `valor agent-session "do this thing"` is the only required
shape for the common case. Everything else is an optional refinement.

Examples:
    valor agent-session "fix the typo in app.py"
    valor agent-session --role dev "build the feature"
    valor agent-session --id abc123 "follow up: also run tests"
    valor agent-session list
    valor agent-session list --status running
    valor agent-session status abc123
    valor agent-session kill abc123
    valor agent-session kill --all

Project key, model, slug, and other knobs are inherited from the cwd via
`projects.json` exactly as `valor-session create` does, so an agent in the
`ai` worktree transparently runs the session in this repo. Override with
`--project-key` or `--model` when needed.

This is a thin shell — every command delegates to `tools.valor_session` so
behavior stays in one place.
"""

from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path

# Bootstrap so this runs as a standalone script from any directory.
_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


@functools.cache
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valor",
        description="The agent-session CLI — create, steer, monitor, kill.",
    )
    sub = parser.add_subparsers(dest="command", metavar="agent-session")

    # Default form: `valor agent-session "prompt"` (positional, no sub-subcommand).
    p_create = sub.add_parser(
        "agent-session",
        help="Create a new session with a prompt (default; positional prompt).",
        # Allow `valor "do thing"` (without the `agent-session` token) by accepting
        # the prompt positionally on the top-level parser too — see `main()`.
    )
    p_create.add_argument(
        "prompt",
        nargs="?",
        help="What to do. e.g. 'fix the bug in app.py' or 'plan issue #1615'.",
    )
    p_create.add_argument(
        "--role",
        "-r",
        default="pm",
        choices=["pm", "dev", "teammate"],
        help="Session role (default: pm — orchestrator that may spawn dev children).",
    )
    p_create.add_argument(
        "--model",
        help="Claude model for the session (sonnet, opus, haiku). Default: env/CWD.",
    )
    p_create.add_argument(
        "--slug",
        help="Worktree slug. Auto-derived from 'issue #N' in the prompt when omitted.",
    )
    p_create.add_argument(
        "--project-key",
        help="Explicit project key (default: resolved from cwd or --parent).",
    )
    p_create.add_argument(
        "--parent",
        help="Parent session ID (child session inherits project_key from parent).",
    )
    p_create.add_argument(
        "--chat-id",
        help="Telegram chat ID for delivery (default: 0 = dashboard only).",
    )
    p_create.add_argument(
        "--needs-real-chrome",
        action="store_true",
        help="Mark session as requiring real Chrome (BYOB) — serialized.",
    )
    p_create.add_argument("--json", action="store_true", help="Emit JSON output.")

    # sub-subcommands for the rest of the lifecycle.
    p_list = sub.add_parser("list", help="List sessions (default: most recent 20).")
    p_list.add_argument("--status", help="Filter by status (comma-separated).")
    p_list.add_argument("--role", help="Filter by session type.")
    p_list.add_argument("--limit", type=int, default=20, help="Max results (default 20).")
    p_list.add_argument("--json", action="store_true")

    p_status = sub.add_parser("status", help="Show session status.")
    p_status.add_argument("id", help="Session ID (agent_session_id or session_id).")
    p_status.add_argument(
        "--full-message",
        dest="full_message",
        action="store_true",
        help="Print full initial message (no truncation).",
    )
    p_status.add_argument("--json", action="store_true")

    p_steer = sub.add_parser("steer", help="Inject a steering message into a running session.")
    p_steer.add_argument("id", help="Session ID to steer.")
    p_steer.add_argument("message", help="Steering message text.")
    p_steer.add_argument("--json", action="store_true")

    p_kill = sub.add_parser("kill", help="Kill a session.")
    p_kill.add_argument("id", nargs="?", help="Session ID (omit if using --all).")
    p_kill.add_argument("--all", action="store_true", help="Kill all running sessions.")
    p_kill.add_argument("--json", action="store_true")

    p_resume = sub.add_parser("resume", help="Resume a completed BUILD session.")
    p_resume.add_argument("id", help="Session ID of the completed session.")
    p_resume.add_argument("message", help="New message to inject.")
    p_resume.add_argument("--json", action="store_true")

    p_inspect = sub.add_parser("inspect", help="Dump all raw session fields (debug).")
    p_inspect.add_argument("id", help="Session ID.")
    p_inspect.add_argument("--json", action="store_true")

    p_children = sub.add_parser("children", help="List child sessions of a parent.")
    p_children.add_argument("id", help="Parent session ID.")
    p_children.add_argument("--json", action="store_true")

    p_release = sub.add_parser("release", help="Release retain_for_resume after PR merge/close.")
    p_release.add_argument("--pr", type=int, required=True, help="PR number.")
    p_release.add_argument("--json", action="store_true")

    return parser


def _derive_known_subcommands() -> set[str]:
    """Read the registered subparser names directly off the parser.

    Single source of truth: the subcommand allowlist is derived from the
    `_SubParsersAction.choices` keys rather than hand-maintained, so adding
    a subparser to `_build_parser` automatically extends the allowlist.
    `_build_parser` is `lru_cache`d, so this shares the one parser build
    with import-time derivation and every runtime `main()` call.
    """
    parser = _build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    raise AssertionError("no subparsers found on the valor parser")


# Subcommand names recognized by `_build_parser`, DERIVED from the
# registered subparsers (not a hand-maintained literal). The positional
# shortcut in `main()` rewrites `valor "prompt"` to
# `valor agent-session "prompt"` only when the first token is NOT in this
# set. Defined AFTER `_build_parser` so the derivation can introspect it.
KNOWN_SUBCOMMANDS = _derive_known_subcommands()


def _run(args: argparse.Namespace) -> int:
    """Dispatch to the underlying `valor-session` subcommand."""
    # Lazy import so `valor --help` doesn't pay the bootstrap cost.
    from tools import valor_session

    if args.command == "agent-session":
        if not args.prompt:
            print(
                'error: missing prompt. Usage: valor agent-session "do this thing"',
                file=sys.stderr,
            )
            return 2
        return valor_session.cmd_create(_to_create_namespace(args))

    if args.command == "list":
        return valor_session.cmd_list(_to_list_namespace(args))
    if args.command == "status":
        return valor_session.cmd_status(_to_status_namespace(args))
    if args.command == "steer":
        return valor_session.cmd_steer(_to_steer_namespace(args))
    if args.command == "kill":
        return valor_session.cmd_kill(_to_kill_namespace(args))
    if args.command == "resume":
        return valor_session.cmd_resume(_to_resume_namespace(args))
    if args.command == "inspect":
        return valor_session.cmd_inspect(_to_inspect_namespace(args))
    if args.command == "children":
        return valor_session.cmd_children(_to_children_namespace(args))
    if args.command == "release":
        return valor_session.cmd_release(_to_release_namespace(args))

    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _to_create_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(
        role=args.role,
        message=args.prompt,
        chat_id=args.chat_id,
        parent=args.parent,
        project_key=args.project_key,
        slug=args.slug,
        model=args.model,
        needs_real_chrome=args.needs_real_chrome,
        json=args.json,
    )


def _to_list_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(status=args.status, role=args.role, limit=args.limit, json=args.json)


def _to_status_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, full_message=args.full_message, json=args.json)


def _to_steer_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, message=args.message, json=args.json)


def _to_kill_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, all=args.all, json=args.json)


def _to_resume_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, message=args.message, json=args.json)


def _to_inspect_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, json=args.json)


def _to_children_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(id=args.id, json=args.json)


def _to_release_namespace(args: argparse.Namespace) -> argparse.Namespace:
    return _ns(pr=args.pr, json=args.json)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Accepts two equivalent forms:

    valor agent-session "prompt"   (explicit subcommand)
    valor "prompt"                 (positional shortcut)
    """
    if argv is None:
        argv = sys.argv[1:]

    # Help short-circuit on the PRE-REWRITE argv. When the first token is a
    # bare prompt (not a flag, not a known subcommand) AND a standalone
    # `-h`/`--help` token appears anywhere in argv, print top-level help and
    # exit. Without this, the shortcut rewrite below would prepend
    # `agent-session` and argparse would emit the create sub-help instead of
    # the top-level help the user almost certainly wanted.
    #
    # "Standalone token" means an exact argv element equal to `-h` or
    # `--help` — NOT a substring. `valor "document the --help flag"` is a
    # single argv element containing the substring, so it does NOT fire (the
    # prompt is delivered verbatim); `valor "prompt" --help` has `--help` as
    # its own element and DOES fire.
    #
    # `valor list --help` is excluded here because `list` IS a known
    # subcommand — it falls through to argparse, which prints the `list`
    # sub-help. SystemExit(0) (not `return 0`) keeps semantics uniform with
    # argparse's own `--help` exit.
    if (
        argv
        and not argv[0].startswith("-")
        and argv[0] not in KNOWN_SUBCOMMANDS
        and any(token in ("-h", "--help") for token in argv)
    ):
        parser = _build_parser()
        parser.print_help()
        raise SystemExit(0)

    # If the first token doesn't match a known subcommand and isn't a flag,
    # inject the `agent-session` subcommand so the user can omit it.
    # (`--help`/`-h` start with `-`, so the flag check already excludes them.)
    if argv and not argv[0].startswith("-") and argv[0] not in KNOWN_SUBCOMMANDS:
        argv = ["agent-session", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
