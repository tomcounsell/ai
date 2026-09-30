"""Read-only CLI for agent/session_archive.py.

Usage:
    python -m tools.session_archive_cli status
    python -m tools.session_archive_cli restore --dry-run

This CLI is deliberately **read-only**. Writes to the archive (`export_all`,
`export_session`) and live restores (`restore_if_empty()`) run automatically
via the worker's periodic daemon thread and the `finalize_session` terminal
hook / guarded startup step (see `agent/session_archive.py` and
`worker/__main__.py`). There is no `export` subcommand and no way to trigger
a live (writing) restore from this CLI -- `restore` only ever calls
`restore_if_empty(dry_run=True)`, regardless of the `--dry-run` flag's
presence, so this tool can never mutate Redis. `--dry-run` is required on
`restore` purely so an operator can never invoke it expecting a live effect.

See docs/plans/session-archive-sqlite.md "Scope" and "No-Gos" sections for
the rationale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root on sys.path (matches tools/analytics.py convention).
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def cmd_status(args: argparse.Namespace) -> None:
    """Print get_archive_status() as JSON."""
    from agent.session_archive import get_archive_status

    status = get_archive_status()
    json.dump(status, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def cmd_restore(args: argparse.Namespace) -> None:
    """Report the restore guard decision without writing anything.

    Always calls `restore_if_empty(dry_run=True)` -- the `--dry-run` flag is
    required on the subparser (not merely accepted) so this subcommand can
    never be invoked expecting a live write.
    """
    from agent.session_archive import restore_if_empty

    result = restore_if_empty(dry_run=True)
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valor-session-archive",
        description=(
            "Read-only inspection CLI for the session archive "
            "(agent/session_archive.py). Export and restore are automatic "
            "(worker daemon thread + finalize hook / guarded startup) -- "
            "this CLI never writes."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Print get_archive_status() as JSON")

    restore_parser = subparsers.add_parser(
        "restore",
        help="Report the restore guard decision without writing (dry-run only)",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required -- this CLI never performs a live (writing) restore",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "restore":
        cmd_restore(args)


if __name__ == "__main__":
    main()
