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
        "role": "eng",
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


class TestCreateDevRoleRequiresSlug:
    """Issue #1272: ``--role dev`` without a slug (or 'issue #N') must exit 1.

    Mirrors the pre-existing PM-slug-required check at
    ``tools/valor_session.py:cmd_create``. The CLI is the first line of
    defense — keeping slugless dev sessions out of the queue means the
    executor's synthetic-slug fallback (#1272) is only ever exercised
    by future programmatic spawn sites that bypass the CLI.
    """

    def test_create_dev_role_requires_slug(self, capsys):
        """``--role dev`` with no slug and no 'issue #N' must exit 1 with stderr error."""
        args = _make_args(
            "no slug here, no issue reference either",
            role="eng",
            slug=None,
        )
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()

        assert rc == 1, f"Expected exit code 1 for slugless --role dev, got {rc}"
        # Per the plan's "CLI rejection message must be grep-able" criterion:
        # the substring ``dev sessions must be created with --slug`` (case-
        # sensitive in our message) must appear in stderr.
        assert "Eng sessions must be created with --slug" in captured.err, (
            f"Expected slug-required error message in stderr, got: {captured.err!r}"
        )
        # Issue references for grep / reflections
        assert "#1272" in captured.err, (
            f"Expected issue #1272 reference in error message, got: {captured.err!r}"
        )
        # Must write to stderr, not stdout
        assert captured.out == "" or "Error" not in captured.out, (
            f"Error must go to stderr, not stdout: stdout={captured.out!r}"
        )

    def test_create_dev_role_with_issue_n_auto_derives_slug(self, capsys, monkeypatch, tmp_path):
        """``--role dev`` with 'issue #N' in the message auto-derives the slug.

        This is the same auto-derive path that PM uses; #1272 extends it
        to dev. We don't care about the downstream enqueue here — only
        that the slug-required guard does NOT fire (no exit 1, no error).
        """
        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(
            valor_session,
            "resolve_project_key",
            lambda cwd: "test-1272",
        )
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        args = _make_args("Fix issue #42 broken thing", role="eng", slug=None)
        rc = None
        try:
            rc = valor_session.cmd_create(args)
        except Exception:
            # Downstream enqueue may fail (no Redis) — we only care that the
            # slug-required guard didn't fire.
            pass
        captured = capsys.readouterr()

        assert "Eng sessions must be created with --slug" not in captured.err, (
            f"Auto-derive should have produced a slug, but rejection fired: {captured.err!r}"
        )
        # If the guard didn't fire, rc is either None (downstream raised)
        # or some non-1 value. Specifically must NOT be 1 from this guard.
        if rc is not None:
            assert rc != 1 or "must be created with --slug" not in captured.err

    def test_create_dev_role_with_explicit_slug_passes_guard(self, capsys, monkeypatch, tmp_path):
        """``--role dev --slug my-feat`` must not trigger the guard."""
        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(
            valor_session,
            "resolve_project_key",
            lambda cwd: "test-1272",
        )
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        args = _make_args("test message", role="eng", slug="my-feat")
        try:
            valor_session.cmd_create(args)
        except Exception:
            pass
        captured = capsys.readouterr()

        assert "Eng sessions must be created with --slug" not in captured.err, (
            f"Guard fired with explicit --slug present: {captured.err!r}"
        )


class TestCreateChildSessionGate:
    """Stopgap (#1633): cmd_create refuses NEW parent-attached session creation.

    The granite PTY container (PR #1612) owns the PM/Dev split from a bounded
    pool; parent-linked child sessions double-consume pool slots. The gate
    fires before any filesystem or Redis work and is bypassed only by
    VALOR_ALLOW_CHILD_SESSIONS=1 (loud stderr warning). Parentless creation
    and existing-child lifecycle commands are untouched.
    """

    def _stub_downstream(self, monkeypatch, tmp_path, push_calls):
        """Stub project resolution + enqueue so no Redis/filesystem is touched."""
        import agent.agent_session_queue as queue_mod

        monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1))
        monkeypatch.setattr(valor_session, "resolve_project_key", lambda cwd: "test-1633")
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: (tmp_path, {"working_directory": str(tmp_path)}),
        )

        async def _fake_push(**kwargs):
            push_calls.append(kwargs)
            return 1

        monkeypatch.setattr(queue_mod, "_push_agent_session", _fake_push)

    def test_blocked_parent_create_exits_2_with_no_enqueue(self, capsys, monkeypatch, tmp_path):
        """--parent without the bypass env: exit 2, nothing enqueued or resolved."""
        monkeypatch.delenv("VALOR_ALLOW_CHILD_SESSIONS", raising=False)
        push_calls: list[dict] = []
        self._stub_downstream(monkeypatch, tmp_path, push_calls)
        # The gate must fire BEFORE project/worktree resolution.
        monkeypatch.setattr(
            valor_session,
            "_resolve_project_working_directory",
            lambda key: pytest.fail("gate must fire before working-dir resolution"),
        )

        args = _make_args(
            "child task", role="teammate", slug=None, parent="agt_parent123", json=False
        )
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()

        assert rc == 2
        assert "temporarily disabled (#1633)" in captured.err
        assert "VALOR_ALLOW_CHILD_SESSIONS=1" in captured.err
        assert push_calls == [], "refused path must not enqueue anything"

    def test_blocked_parent_create_json_error_shape(self, capsys, monkeypatch, tmp_path):
        """--parent --json: structured error object on stdout, exit 2, no create."""
        import json as json_mod

        monkeypatch.delenv("VALOR_ALLOW_CHILD_SESSIONS", raising=False)
        push_calls: list[dict] = []
        self._stub_downstream(monkeypatch, tmp_path, push_calls)

        args = _make_args(
            "child task", role="teammate", slug=None, parent="agt_parent123", json=True
        )
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()

        assert rc == 2
        payload = json_mod.loads(captured.out)
        assert payload["error"] == "child_sessions_disabled"
        assert payload["issue"] == 1633
        assert payload["bypass"] == "VALOR_ALLOW_CHILD_SESSIONS=1"
        assert push_calls == []

    def test_escape_hatch_creates_with_loud_warning(self, capsys, monkeypatch, tmp_path):
        """VALOR_ALLOW_CHILD_SESSIONS=1: creation proceeds, warning on stderr."""
        monkeypatch.setenv("VALOR_ALLOW_CHILD_SESSIONS", "1")
        push_calls: list[dict] = []
        self._stub_downstream(monkeypatch, tmp_path, push_calls)
        # --parent inheritance looks up the parent session; stub it to avoid Redis.
        monkeypatch.setattr(valor_session, "_find_session", lambda _id: None)

        args = _make_args(
            "child task", role="teammate", slug=None, parent="agt_parent123", json=False
        )
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()

        assert rc == 0
        assert "WARNING: VALOR_ALLOW_CHILD_SESSIONS=1" in captured.err
        assert len(push_calls) == 1
        assert push_calls[0]["parent_agent_session_id"] == "agt_parent123"

    def test_parentless_create_unaffected(self, capsys, monkeypatch, tmp_path):
        """No --parent: creation proceeds normally with no gate output."""
        monkeypatch.delenv("VALOR_ALLOW_CHILD_SESSIONS", raising=False)
        push_calls: list[dict] = []
        self._stub_downstream(monkeypatch, tmp_path, push_calls)

        args = _make_args("plain task", role="teammate", slug=None, parent=None, json=False)
        rc = valor_session.cmd_create(args)
        captured = capsys.readouterr()

        assert rc == 0
        assert "temporarily disabled" not in captured.err
        assert "WARNING: VALOR_ALLOW_CHILD_SESSIONS" not in captured.err
        assert len(push_calls) == 1
        assert push_calls[0]["parent_agent_session_id"] is None
