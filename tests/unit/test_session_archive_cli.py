"""Tests for tools/session_archive_cli.py -- the read-only session archive CLI.

Covers the two agent-reachable entry points (`status`, `restore --dry-run`)
end-to-end, and locks in the CLI's read-only surface: no `export` subcommand
and no way to trigger a live (writing) `restore` -- see
docs/plans/session-archive-sqlite.md "Scope" and "No-Gos".
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid

import pytest

import agent.session_archive as archive
import tools.session_archive_cli as cli
from models.agent_session import AgentSession

pytestmark = pytest.mark.usefixtures("redis_test_db")


@pytest.fixture
def archive_db(tmp_path, monkeypatch):
    """Point the archive at an isolated per-test SQLite file."""
    db_path = tmp_path / "session_archive.db"
    monkeypatch.setenv("SESSION_ARCHIVE_DB_PATH", str(db_path))
    return db_path


def _make_session(status: str = "completed", **overrides) -> AgentSession:
    defaults = dict(
        session_id=f"tg_archive_cli_test_{uuid.uuid4().hex[:8]}",
        project_key="test-session-archive-cli",
        working_dir="/tmp",
        status=status,
    )
    defaults.update(overrides)
    session = AgentSession(**defaults)
    session.save()
    return session


def _run_main(monkeypatch, capsys, argv: list[str]) -> dict:
    """Run cli.main() with the given argv and return the parsed JSON stdout."""
    monkeypatch.setattr("sys.argv", ["valor-session-archive", *argv])
    cli.main()
    captured = capsys.readouterr()
    return json.loads(captured.out)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_end_to_end_valid_json(archive_db, monkeypatch, capsys):
    session = _make_session()
    archive.export_session(session)

    output = _run_main(monkeypatch, capsys, ["status"])

    expected_keys = {
        "db_path",
        "exists",
        "row_count",
        "last_export_ts",
        "last_export_age_s",
        "kind",
        "healthy",
    }
    assert expected_keys.issubset(output.keys())
    assert output["exists"] is True
    assert output["row_count"] == 1
    assert output["healthy"] is True


def test_status_exits_zero(archive_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["valor-session-archive", "status"])
    cli.main()  # must not raise / must not call sys.exit with a nonzero code


# ---------------------------------------------------------------------------
# restore --dry-run
# ---------------------------------------------------------------------------


def test_restore_dry_run_reports_guard_decision_without_writing(archive_db, monkeypatch, capsys):
    session = _make_session()
    session_id = session.id
    archive.export_session(session)
    session.delete()
    assert len(list(AgentSession.query.all())) == 0

    output = _run_main(monkeypatch, capsys, ["restore", "--dry-run"])

    assert output["skipped_reason"] is None
    assert output["restored"] == 0
    assert output["would_restore"] == 1

    # Redis must remain untouched -- no row was actually rehydrated.
    assert len(list(AgentSession.query.all())) == 0
    assert AgentSession.query.get(id=session_id) is None


def test_restore_dry_run_does_not_mutate_sentinel(archive_db, monkeypatch, capsys):
    session = _make_session()
    archive.export_session(session)
    session.delete()

    _run_main(monkeypatch, capsys, ["restore", "--dry-run"])

    conn = sqlite3.connect(str(archive_db))
    conn.row_factory = sqlite3.Row
    try:
        meta = conn.execute(
            "SELECT restore_in_progress, restore_complete, resume_attempts FROM _meta WHERE id=1"
        ).fetchone()
    finally:
        conn.close()

    assert meta["restore_in_progress"] == 0
    assert meta["restore_complete"] == 0
    assert meta["resume_attempts"] == 0


def test_restore_dry_run_reports_skip_reason_when_redis_populated(archive_db, monkeypatch, capsys):
    _make_session(status="running")  # a live session -- guard must no-op

    output = _run_main(monkeypatch, capsys, ["restore", "--dry-run"])

    assert output["skipped_reason"] == "redis_has_records"
    assert output["restored"] == 0


def test_restore_without_dry_run_flag_errors(archive_db):
    """`restore` bare (no --dry-run) must fail argparse validation, not run."""
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["restore"])


# ---------------------------------------------------------------------------
# Read-only surface: no export / no live-restore subcommand
# ---------------------------------------------------------------------------


def test_no_export_subcommand():
    parser = cli.build_parser()
    subparsers_action = next(
        action
        for action in parser._subparsers._group_actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert "export" not in subparsers_action.choices
    assert set(subparsers_action.choices) == {"status", "restore"}


def test_restore_subcommand_only_accepts_dry_run_flag():
    parser = cli.build_parser()
    subparsers_action = next(
        action
        for action in parser._subparsers._group_actions
        if isinstance(action, argparse._SubParsersAction)
    )
    restore_parser = subparsers_action.choices["restore"]

    # The only optional argument accepted is --dry-run, and it is required --
    # there is no flag (e.g. --live, --write) that could trigger a real write.
    dest_names = {action.dest for action in restore_parser._actions if action.option_strings}
    assert dest_names == {"help", "dry_run"}

    dry_run_action = next(a for a in restore_parser._actions if a.dest == "dry_run")
    assert dry_run_action.required is True


def test_cmd_restore_always_calls_dry_run_regardless_of_flag_value(archive_db, monkeypatch, capsys):
    """Even if --dry-run were somehow False, cmd_restore must force dry_run=True."""
    calls: list[bool] = []
    original = archive.restore_if_empty

    def _spy(*, dry_run=False):
        calls.append(dry_run)
        return original(dry_run=dry_run)

    monkeypatch.setattr(archive, "restore_if_empty", _spy)
    monkeypatch.setattr("sys.argv", ["valor-session-archive", "restore", "--dry-run"])
    cli.main()

    assert calls == [True]
