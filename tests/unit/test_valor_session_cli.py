"""Tests for tools.valor_session CLI command handlers (issue #1148).

Focus: enrichment-header guard in cmd_create. Other valor_session subcommands
have their own dedicated test files; this module is reserved for guards and
input-validation logic that protect against the failure modes documented in
docs/plans/sdlc-1148.md.
"""

from __future__ import annotations

import argparse

import pytest

from tools import valor_session


def _make_args(message: str, **overrides) -> argparse.Namespace:
    """Build a minimal argparse.Namespace to feed cmd_create.

    Note: no ``working_dir`` attribute — the flag was removed in #1158. The
    derived working_dir now comes from ``_resolve_project_working_directory``
    which tests below monkeypatch to a ``tmp_path``-friendly stub.
    """
    base = {
        "role": "pm",
        "message": message,
        "chat_id": "999",
        "parent": None,
        "model": None,
        "slug": "sdlc-1148",  # Skip the auto-derive branch in cmd_create
        "project_key": "test-1148",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestCreateEnrichmentHeaderGuard:
    """cmd_create rejects --message values that look like forwarded enrichment headers.

    Failure scenario from issue #1148: PM session 8ff90a3c received a message,
    failed to load its persona, and forwarded its own enrichment payload
    ("PROJECT: Valor AI\\nFOCUS: ...") as the --message to a child dev session.
    The dev session stalled with "the message appears to have been truncated".

    This guard catches that pattern at the CLI boundary so the failure mode
    can never propagate even if the upstream persona-wiring regresses.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            "SESSION_ID: abc-123\nMESSAGE: do the thing",
            "PROJECT: Valor AI\nFROM: pm\nMESSAGE: build the feature",
            "FROM: pm\nMESSAGE: x",
            "TASK_SCOPE: bug-fix\nDescribe what to do here",
            "SCOPE: full-rewrite\nThis is the actual task",
        ],
    )
    def test_rejects_enrichment_header_prefixed_messages(self, msg, capsys):
        """Each enrichment-header prefix triggers exit code 2 + stderr error."""
        args = _make_args(msg)
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()
        assert rc == 2, f"Expected exit code 2, got {rc} for message: {msg!r}"
        assert "enrichment header" in captured.err.lower(), captured.err

    def test_accepts_lowercase_prose_with_scope_keyword(self, capsys, monkeypatch, tmp_path):
        """Lowercase 'scope: ...' is normal prose and must NOT fire the guard.

        We mock the downstream session-creation path so the test does not
        actually enqueue work; only the guard is exercised.
        """
        # Stub out the downstream create flow so cmd_create's later steps
        # don't blow up trying to talk to Redis. The guard fires (or doesn't)
        # before any of these are reached on the happy path.
        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(
            valor_session,
            "resolve_project_key",
            lambda cwd: "test-1148",
        )
        # #1158: cmd_create now calls _resolve_project_working_directory after
        # resolving project_key. The helper returns a 2-tuple (Path, dict).
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        args = _make_args("scope: database refactor\nLet's look at the connection pool config")
        # We don't care if the downstream code raises (no Redis in this test);
        # we only care that the GUARD itself does not trip.
        try:
            valor_session.cmd_create(args)
        except Exception:
            pass  # downstream failure is acceptable; guard didn't fire
        captured = capsys.readouterr()
        assert "enrichment header" not in captured.err.lower(), (
            f"Lowercase 'scope:' prose tripped the guard. Stderr: {captured.err!r}"
        )

    def test_accepts_buried_enrichment_after_first_line(self, capsys, monkeypatch, tmp_path):
        """Enrichment-header text buried after the first line must NOT fire.

        The regex anchors at ^ and only inspects the first 200 chars; this
        ensures normal task text that happens to mention SESSION_ID or
        PROJECT mid-message is not rejected.
        """
        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(
            valor_session,
            "resolve_project_key",
            lambda cwd: "test-1148",
        )
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        args = _make_args("Fix the bug where SESSION_ID: prefix sometimes appears in logs")
        try:
            valor_session.cmd_create(args)
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "enrichment header" not in captured.err.lower(), (
            f"Buried 'SESSION_ID:' tripped the guard. Stderr: {captured.err!r}"
        )

    def test_empty_message_does_not_fire(self, capsys, monkeypatch, tmp_path):
        """An empty --message bypasses this guard (other validation owns that case)."""
        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(
            valor_session,
            "resolve_project_key",
            lambda cwd: "test-1148",
        )
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        args = _make_args("")
        try:
            valor_session.cmd_create(args)
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "enrichment header" not in captured.err.lower()
