"""Unit tests for tools.sdlc_dispatch session resolution (#1671).

The #1671 skew explicitly named "dispatch-history entries to the wrong session"
as a symptom, but this module had zero coverage. These tests pin the corrected
behavior:

- ``record`` resolves with ``ensure=True`` so a cold-start
  ``dispatch record --issue-number N`` creates/uses ``sdlc-local-N`` rather than
  env-resolving to a divergent inherited session or silently no-opping.
- ``record`` under a divergent ``VALOR_SESSION_ID`` lands the dispatch entry on
  the issue-scoped session (the direct #1671 regression for the dispatch writer).
- ``get`` and ``reset`` stay non-ensuring — they must not fabricate a session.
- ``record_dispatch_for_session()`` calls ``touch_issue_lock()`` DIRECTLY,
  deriving ``issue_number`` from ``session.issue_url`` -- it must not assume
  ``ensure_session()`` ran first (#1954).
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDispatchRecordEnsures:
    """The record path passes ensure=True (B1, #1671)."""

    def test_record_resolves_with_ensure_true(self):
        """_cli_record calls find_session with ensure=True so the dispatch write
        has an issue-scoped home on a cold start."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        find_mock = MagicMock(return_value=session)

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None, run_id="run-1671"
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", return_value=True),
            patch.object(
                sdlc_dispatch, "get_dispatch_history", return_value=[{"skill": "/do-build"}]
            ),
        ):
            result = sdlc_dispatch._cli_record(args)

        # The critical assertion: record resolves with ensure=True.
        find_mock.assert_called_once_with(
            session_id=None, issue_number=1671, ensure=True, caller_run_id="run-1671"
        )
        assert result == {"ok": True, "history_length": 1}

    def test_record_lands_on_issue_session_under_divergent_env(self, monkeypatch):
        """#1671 regression: with VALOR_SESSION_ID pointing at a DIFFERENT
        session, a `dispatch record --issue-number N` resolves the issue-scoped
        session (sdlc-local-N), not the divergent env session.

        Exercises the real find_session resolver end-to-end (not mocked) to
        prove the precedence fix routes the dispatch write correctly.
        """
        from tools import sdlc_dispatch

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = MagicMock(name="issue_session")
        issue_session.session_type = "eng"
        issue_session.session_id = "sdlc-local-1671"

        captured = {}

        def _capture(session, skill, pr_number=None, run_id=None):
            captured["session"] = session
            captured["skill"] = skill
            return True

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None, run_id="run-1671"
        )

        with (
            # The real find_session is used; its issue-first pass hits this.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", side_effect=_capture),
            patch.object(sdlc_dispatch, "get_dispatch_history", return_value=[{}]),
        ):
            result = sdlc_dispatch._cli_record(args)

        # The dispatch write landed on the issue-scoped session, not the env one.
        assert captured["session"] is issue_session
        assert result == {"ok": True, "history_length": 1}

    def test_record_cold_start_with_run_id_refuses_without_ensure(self, monkeypatch):
        """Cold-state run-identity gate (#2003 cycle-3): a record call that
        carries a --run-id but resolves NO session must be refused WITHOUT
        auto-ensuring — ensuring would mint a fresh session + issue lock as a
        side effect of a write that is about to be refused, wedging the next
        legitimate session-ensure behind ISSUE_LOCKED for up to the TTL."""
        from tools import sdlc_dispatch

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None, run_id="run-1671"
        )

        with (
            # No existing issue session on the pure lookup.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("tools.sdlc_session_ensure.ensure_session") as ensure_mock,
            patch.object(sdlc_dispatch, "record_dispatch_for_session") as record_mock,
        ):
            result = sdlc_dispatch._cli_record(args)

        # No session minted, no write recorded, quiet no-op shape.
        ensure_mock.assert_not_called()
        record_mock.assert_not_called()
        assert result == {}

    def test_record_cold_start_identity_less_still_creates_via_ensure(self, monkeypatch):
        """Cold start with NO run_id (identity-less programmatic caller) keeps
        the #1671 auto-ensure behavior: ensure creates sdlc-local-N and the
        dispatch write lands there."""
        from tools import sdlc_dispatch

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        created = MagicMock(name="created_session")
        created.session_type = "eng"

        captured = {}

        def _capture(session, skill, pr_number=None, run_id=None):
            captured["session"] = session
            return True

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None, run_id=None
        )

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [created]

        with (
            # No existing issue session on the first lookup.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            # ensure_session creates sdlc-local-1671; the re-resolve returns it.
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"session_id": "sdlc-local-1671", "created": True},
            ) as ensure_mock,
            patch("tools._sdlc_utils.AgentSession", mock_as),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", side_effect=_capture),
            patch.object(sdlc_dispatch, "get_dispatch_history", return_value=[{}]),
        ):
            result = sdlc_dispatch._cli_record(args)

        ensure_mock.assert_called_once_with(1671)
        assert captured["session"] is created
        assert result == {"ok": True, "history_length": 1}


class TestDispatchGetResetNonEnsuring:
    """get/reset must not fabricate a session (no ensure=True)."""

    def test_get_does_not_ensure(self):
        from tools import sdlc_dispatch

        find_mock = MagicMock(return_value=None)
        args = SimpleNamespace(session_id=None, issue_number=1671)

        with patch.object(sdlc_dispatch, "_find_session", find_mock):
            result = sdlc_dispatch._cli_get(args)

        # No ensure kwarg → defaults to ensure=False (no session created).
        find_mock.assert_called_once_with(session_id=None, issue_number=1671)
        _, kwargs = find_mock.call_args
        assert "ensure" not in kwargs or kwargs["ensure"] is False
        assert result == []

    def test_reset_does_not_ensure(self):
        from tools import sdlc_dispatch

        find_mock = MagicMock(return_value=None)
        args = SimpleNamespace(session_id=None, issue_number=1671)

        with patch.object(sdlc_dispatch, "_find_session", find_mock):
            result = sdlc_dispatch._cli_reset(args)

        find_mock.assert_called_once_with(session_id=None, issue_number=1671)
        _, kwargs = find_mock.call_args
        assert "ensure" not in kwargs or kwargs["ensure"] is False
        assert result == {"ok": False, "history_length": 0}


class TestParseIssueNumberFromUrl:
    """_parse_issue_number_from_url mirrors find_session_by_issue's
    /issues/{N} suffix convention, in the reverse direction (url -> number)."""

    def test_extracts_issue_number(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url("https://github.com/tomcounsell/ai/issues/1954") == 1954

    def test_returns_none_for_missing_url(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url(None) is None
        assert _parse_issue_number_from_url("") is None

    def test_returns_none_for_url_without_issue_segment(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url("https://github.com/tomcounsell/ai/pull/42") is None


class TestRecordDispatchIssueLock:
    """Issues #1954/#2003: record_dispatch_for_session() calls
    touch_issue_lock() DIRECTLY (not via ensure_session()) before writing the
    dispatch event, deriving issue_number by parsing session.issue_url and
    comparing ownership by the caller's run_id. This must hold for the
    continuing-session path too, where find_session(ensure=True)'s Step-2
    short-circuit never calls ensure_session()."""

    def _lock_result(self, acquired: bool, owner_session_id=None, owner_run_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(
            acquired=acquired,
            owner_session_id=owner_session_id,
            owner_run_id=owner_run_id,
        )

    def test_refuses_and_returns_false_when_lock_held_by_foreign_run(self):
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3001"
        session.session_id = "sdlc-local-3001"

        lock_mock = MagicMock(
            return_value=self._lock_result(
                False, owner_session_id="other-live-session", owner_run_id="foreign-run"
            )
        )

        with patch("models.session_lifecycle.touch_issue_lock", lock_mock):
            ok = record_dispatch_for_session(session, skill="/do-build", run_id="run-mine")

        assert ok is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 3001
        assert args[1] == "run-mine"
        assert kwargs.get("session_id") == "sdlc-local-3001"

    def test_continuing_session_derives_issue_number_from_issue_url(self):
        """The continuing-session path: no prior ensure_session() call, session
        resolved via find_session_by_issue -- issue_number must be derived
        from session.issue_url and the lock still enforced (acquired)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3002"
        session.session_id = "sdlc-local-3002"

        lock_mock = MagicMock(
            return_value=self._lock_result(True, "sdlc-local-3002", owner_run_id="run-mine")
        )

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build", run_id="run-mine")

        assert ok is True
        lock_mock.assert_called_once()
        args, _ = lock_mock.call_args
        assert args[0] == 3002
        assert args[1] == "run-mine"

    def test_no_lock_call_when_session_has_no_issue_url(self):
        """A session with no parseable issue number must not attempt a lock
        check -- the write proceeds unguarded (unchanged no-issue-context
        behavior)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = None
        session.session_id = "some-session"

        lock_mock = MagicMock()

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is True
        lock_mock.assert_not_called()

    def test_refuses_without_any_run_identity(self):
        """An issue-scoped write with NO run identity (no explicit run_id, no
        active_run_id on the record) is refused without ever touching the
        lock -- an identity-less caller must never mutate (#2003)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3003"
        session.session_id = "sdlc-local-3003"
        session.active_run_id = None

        lock_mock = MagicMock()
        write_mock = MagicMock(return_value=True)

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", write_mock),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is False
        lock_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_falls_back_to_session_active_run_id_for_in_process_callers(self):
        """With no explicit run_id, the identity falls back to
        session.active_run_id -- the read-back of this process's own
        established identity, never foreign adoption."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3004"
        session.session_id = "sdlc-local-3004"
        session.active_run_id = "own-established-run"

        lock_mock = MagicMock(
            return_value=self._lock_result(True, "sdlc-local-3004", "own-established-run")
        )

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is True
        args, _ = lock_mock.call_args
        assert args[1] == "own-established-run"

    def test_dispatch_record_carries_run_id(self):
        """Dispatch history entries carry the run identity (#2003)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3005"
        session.session_id = "sdlc-local-3005"

        captured = {}

        def _fake_update(sess, apply_fn):
            captured["states"] = apply_fn({})
            return True

        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(True, "sdlc-local-3005", "run-z"),
            ),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=_fake_update),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build", run_id="run-z")

        assert ok is True
        history = captured["states"]["_sdlc_dispatches"]
        assert history[-1]["run_id"] == "run-z"
        assert history[-1]["skill"] == "/do-build"

    def test_cli_record_renews_lock_via_record_dispatch_for_session(self):
        """The `dispatch record` CLI subcommand's wiring
        (record_dispatch_for_session() calling touch_issue_lock() directly)
        satisfies "dispatch record renews the lock" -- keyed by the CLI's
        --run-id. Exercises the CLI entry point (_cli_record) end-to-end
        through the unmocked record_dispatch_for_session()."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        session.issue_url = "https://github.com/tomcounsell/ai/issues/1954"
        session.session_id = "sdlc-local-1954"

        find_mock = MagicMock(return_value=session)
        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-1954", "run-1954"))

        args = SimpleNamespace(
            session_id=None,
            issue_number=1954,
            skill="/do-build",
            pr_number=None,
            run_id="run-1954",
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            result = sdlc_dispatch._cli_record(args)

        assert result["ok"] is True
        lock_mock.assert_called_once()
        args_called, _ = lock_mock.call_args
        assert args_called[0] == 1954
        assert args_called[1] == "run-1954"

    def test_two_runs_same_issue_second_refused(self):
        """End-to-end (real Redis, no mocking of touch_issue_lock): two
        record_dispatch_for_session() calls for the same issue presenting
        DISTINCT run_ids -- the second is refused."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session_a = MagicMock()
        session_a.issue_url = "https://github.com/tomcounsell/ai/issues/3050"
        session_a.session_id = "sdlc-local-3050"

        session_b = MagicMock()
        session_b.issue_url = "https://github.com/tomcounsell/ai/issues/3050"
        session_b.session_id = "sdlc-local-3050"

        with patch("tools.stage_states_helpers.update_stage_states", return_value=True):
            ok_a = record_dispatch_for_session(session_a, skill="/do-build", run_id="proc-A-run")
        assert ok_a is True

        with patch("tools.stage_states_helpers.update_stage_states", return_value=True):
            ok_b = record_dispatch_for_session(session_b, skill="/do-build", run_id="proc-B-run")
        assert ok_b is False


class TestCliRecordIssueLockedShape:
    """Issue #1954 gap fix (extended by #2003): `_cli_record()` disambiguates
    a False result from ``record_dispatch_for_session()`` -- issue-lock
    contention vs. any other write failure -- via a read-only
    ``touch_issue_lock(peek=True)`` check keyed by the CLI's --run-id, so the
    CLI's returned dict matches the ISSUE_LOCKED shape SKILL.md documents."""

    def _lock_result(self, acquired: bool, owner_session_id=None, owner_run_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(
            acquired=acquired,
            owner_session_id=owner_session_id,
            owner_run_id=owner_run_id,
        )

    def test_lock_contention_surfaces_reason_and_owner(self):
        """record_dispatch_for_session() refuses because a foreign run holds
        the lock -- _cli_record's dict must carry reason=ISSUE_LOCKED with
        the owning run_id AND session_id, alongside the existing
        ok/history_length keys."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        session.issue_url = "https://github.com/tomcounsell/ai/issues/4001"
        session.session_id = "sdlc-local-4001"

        find_mock = MagicMock(return_value=session)
        # touch_issue_lock is called twice: once inside
        # record_dispatch_for_session() (mutating attempt), once inside the
        # CLI's post-failure peek. Both return the same "held elsewhere"
        # result for this contended-issue scenario.
        lock_mock = MagicMock(
            return_value=self._lock_result(
                False, owner_session_id="other-live-session", owner_run_id="foreign-run"
            )
        )

        args = SimpleNamespace(
            session_id=None,
            issue_number=4001,
            skill="/do-build",
            pr_number=None,
            run_id="run-4001",
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = sdlc_dispatch._cli_record(args)

        assert result["ok"] is False
        assert result["reason"] == "ISSUE_LOCKED"
        assert result["owner_run_id"] == "foreign-run"
        assert result["owner_session_id"] == "other-live-session"
        assert lock_mock.call_count == 2

    def test_non_lock_failure_keeps_old_shape(self):
        """record_dispatch_for_session() fails for a reason unrelated to the
        issue lock (e.g. update_stage_states write conflict) -- the lock
        peek reports acquired=True (free/owned by us), so _cli_record's dict
        must stay the pre-existing {"ok": False, "history_length": N} shape
        with no spurious "reason" key."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        session.issue_url = "https://github.com/tomcounsell/ai/issues/4002"
        session.session_id = "sdlc-local-4002"

        find_mock = MagicMock(return_value=session)
        # The lock itself is free/ours (acquired=True) both times; the write
        # fails for an unrelated reason (update_stage_states returns False).
        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-4002", "run-4002"))

        args = SimpleNamespace(
            session_id=None,
            issue_number=4002,
            skill="/do-build",
            pr_number=None,
            run_id="run-4002",
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=False),
        ):
            result = sdlc_dispatch._cli_record(args)

        assert result == {"ok": False, "history_length": 0}
        assert "reason" not in result
        assert "owner_session_id" not in result


def _isolated_subprocess_env():
    """Env for CLI subprocesses: Redis isolated to the per-worker test db.

    The autouse redis_test_db fixture patches the IN-PROCESS popoto client;
    a subprocess re-resolves REDIS_URL at import, so point it explicitly at
    the same test db -- unit tests must never touch production Redis.
    """
    import popoto.redis_db as rdb

    kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
    host = kwargs.get("host") or "localhost"
    port = kwargs.get("port") or 6379
    db = kwargs.get("db", 1)
    return {**os.environ, "REDIS_URL": f"redis://{host}:{port}/{db}"}


class TestRunIdRequiredFlag:
    """Issue #2003: every state-MUTATING sdlc-tool subcommand exits non-zero
    with the NAMED error RUN_ID_REQUIRED when --run-id is missing -- no mint,
    no adopt, no session resolution. Read-only subcommands take no run-id.
    One subprocess test per mutating subcommand."""

    @pytest.mark.parametrize(
        ("module", "argv"),
        [
            (
                "tools.sdlc_dispatch",
                ["record", "--skill", "/do-build", "--issue-number", "999999"],
            ),
            (
                "tools.sdlc_verdict",
                [
                    "record",
                    "--stage",
                    "CRITIQUE",
                    "--verdict",
                    "READY TO BUILD",
                    "--issue-number",
                    "999999",
                ],
            ),
            (
                "tools.sdlc_stage_marker",
                ["--stage", "DOCS", "--status", "completed", "--issue-number", "999999"],
            ),
            (
                "tools.sdlc_meta_set",
                ["--key", "plan_revising", "--value", "true", "--issue-number", "999999"],
            ),
        ],
        ids=["dispatch-record", "verdict-record", "stage-marker", "meta-set"],
    )
    def test_missing_run_id_is_named_nonzero_error(self, module, argv):
        proc = subprocess.run(
            [sys.executable, "-m", module, *argv],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=_isolated_subprocess_env(),
            timeout=60,
        )
        assert proc.returncode != 0, f"{module} must exit non-zero without --run-id"
        combined = proc.stdout + proc.stderr
        assert "RUN_ID_REQUIRED" in combined, (
            f"{module} must name the error RUN_ID_REQUIRED; got: {combined!r}"
        )

    @pytest.mark.parametrize(
        ("module", "argv"),
        [
            ("tools.sdlc_dispatch", ["get", "--issue-number", "999999"]),
            (
                "tools.sdlc_verdict",
                ["get", "--stage", "CRITIQUE", "--issue-number", "999999"],
            ),
        ],
        ids=["dispatch-get", "verdict-get"],
    )
    def test_read_only_subcommands_need_no_run_id(self, module, argv):
        proc = subprocess.run(
            [sys.executable, "-m", module, *argv],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=_isolated_subprocess_env(),
            timeout=60,
        )
        combined = proc.stdout + proc.stderr
        assert "RUN_ID_REQUIRED" not in combined
