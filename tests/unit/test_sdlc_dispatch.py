"""Unit tests for tools.sdlc_dispatch (issue #2012 task 2: re-pointed at the
issue-keyed PipelineLedger).

There is no session in the `record` path anymore -- authorization is
decided SOLELY by the run_id-keyed issue lease
(``models.session_lifecycle.touch_issue_lock``). `get`/`reset` read the
ledger first with a retained session fallback for pre-cutover records.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _lock_result(**kw):
    from models.session_lifecycle import IssueLockResult

    base = dict(acquired=True, owner_session_id="s", owner_run_id="run-test", target_repo="o/r")
    base.update(kw)
    return IssueLockResult(**base)


class TestDispatchRecordLease:
    """`record` is authorized solely by the run_id-keyed issue lease."""

    def _args(self, **kw):
        base = dict(
            session_id=None,
            issue_number=1671,
            skill="/do-build",
            pr_number=None,
            run_id="run-test",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_record_writes_via_ledger_with_valid_lease(self):
        from tools import sdlc_dispatch

        mock_touch = MagicMock(return_value=_lock_result())
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            result = sdlc_dispatch._cli_record(self._args())

        assert result == {"ok": True, "history_length": 1}
        # Two lock touches: the read-only peek, then the non-peek
        # revalidation immediately before the write (Risk 5 TOCTOU close).
        assert mock_touch.call_count == 2
        peek_calls = [c for c in mock_touch.call_args_list if c.kwargs.get("peek")]
        revalidate_calls = [c for c in mock_touch.call_args_list if not c.kwargs.get("peek")]
        assert len(peek_calls) == 1
        assert len(revalidate_calls) == 1
        assert revalidate_calls[0].kwargs.get("target_repo") == "o/r"

    def test_missing_run_id_or_issue_number_returns_lease_absent(self):
        from tools import sdlc_dispatch

        result = sdlc_dispatch._cli_record(self._args(run_id=None))
        assert result["ok"] is False
        assert result["reason"] == "LEASE_ABSENT"

    def test_unheld_lease_returns_lease_absent(self):
        """PRESENT_NO_SESSION's replacement: an unheld lock is now LOUD
        (surfaced in the result dict), not a quiet no-op."""
        from tools import sdlc_dispatch

        mock_touch = MagicMock(return_value=_lock_result(owner_run_id=None, target_repo=None))
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            result = sdlc_dispatch._cli_record(self._args())

        assert result["ok"] is False
        assert result["reason"] == "LEASE_ABSENT"

    def test_foreign_run_id_returns_issue_locked(self):
        from models.session_lifecycle import IssueLockResult
        from tools import sdlc_dispatch

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False, owner_session_id="other-session", owner_run_id="foreign-run"
            )
        )
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            result = sdlc_dispatch._cli_record(self._args(run_id="intruder-run"))

        assert result["ok"] is False
        assert result["reason"] == "ISSUE_LOCKED"
        assert result["owner_run_id"] == "foreign-run"
        assert result["owner_session_id"] == "other-session"
        # Only the read-only peek fires -- no write is ever attempted.
        for call in mock_touch.call_args_list:
            assert call.kwargs.get("peek") is True

    def test_target_repo_missing_returns_error_never_writes(self):
        """Risk 5 (writer side): a valid lease with no pinned target_repo
        must hard-fail and never construct a PipelineLedger key with a
        None component."""
        from tools import sdlc_dispatch

        mock_touch = MagicMock(return_value=_lock_result(target_repo=None))
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            result = sdlc_dispatch._cli_record(self._args())

        assert result["ok"] is False
        assert result["reason"] == "TARGET_REPO_MISSING"
        mock_get_or_create.assert_not_called()

    def test_lease_lost_between_peek_and_write_refuses(self):
        from tools import sdlc_dispatch

        peek_result = _lock_result()
        revalidate_result = _lock_result(acquired=False, owner_run_id="foreign-run")
        mock_touch = MagicMock(side_effect=[peek_result, revalidate_result])
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            result = sdlc_dispatch._cli_record(self._args())

        assert result["ok"] is False
        assert result["reason"] == "ISSUE_LOCKED"
        mock_get_or_create.assert_not_called()

    def test_run_id_annotated_onto_dispatch_history(self):
        from tools import sdlc_dispatch

        with patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()):
            sdlc_dispatch._cli_record(self._args())
            history = sdlc_dispatch._cli_get(SimpleNamespace(session_id=None, issue_number=1671))

        assert history
        assert history[-1]["skill"] == "/do-build"
        assert history[-1]["run_id"] == "run-test"


class TestRecordDispatchForLedger:
    """Unit tests for record_dispatch_for_ledger() and get_dispatch_history()
    operating directly on a PipelineLedger or (legacy) AgentSession-shaped
    record."""

    def test_writes_and_reads_back_via_ledger(self):
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_dispatch import get_dispatch_history, record_dispatch_for_ledger

        ledger = PipelineLedger.get_or_create("owner/record-dispatch-ledger", 555001)
        ok = record_dispatch_for_ledger(ledger, skill="/do-build", run_id="run-z")
        assert ok is True

        history = get_dispatch_history(ledger)
        assert history[-1]["skill"] == "/do-build"
        assert history[-1]["run_id"] == "run-z"

    def test_none_ledger_returns_false(self):
        from tools.sdlc_dispatch import record_dispatch_for_ledger

        assert record_dispatch_for_ledger(None, skill="/do-build") is False

    def test_get_dispatch_history_works_on_legacy_session_shape(self):
        """get_dispatch_history() field-detects: a plain object with a
        `stage_states` attribute (no `ledger_key`) is read as a legacy
        session-shaped record."""
        from tools.sdlc_dispatch import get_dispatch_history

        class _FakeSession:
            stage_states = '{"_sdlc_dispatches": [{"skill": "/do-plan"}]}'

        history = get_dispatch_history(_FakeSession())
        assert history == [{"skill": "/do-plan"}]

    def test_get_dispatch_history_none_returns_empty(self):
        from tools.sdlc_dispatch import get_dispatch_history

        assert get_dispatch_history(None) == []


class TestDispatchGetResetReader:
    """`get`/`reset` are readers: issue-keyed ledger first, with a retained
    session fallback for pre-cutover records (issue #2012 task 2)."""

    def test_get_reads_ledger_when_target_repo_resolves(self):
        from agent.pipeline_ledger import PipelineLedger
        from tools import sdlc_dispatch

        ledger = PipelineLedger.get_or_create("owner/get-reads-ledger", 555002)
        sdlc_dispatch.record_dispatch_for_ledger(ledger, skill="/do-plan")

        with patch(
            "tools.sdlc_stage_query._resolve_issue_record",
            return_value=ledger,
        ):
            result = sdlc_dispatch._cli_get(SimpleNamespace(session_id=None, issue_number=555002))

        assert result[-1]["skill"] == "/do-plan"

    def test_get_falls_back_to_session_when_ledger_empty(self):
        """The ledger resolves (target_repo present) but carries no
        dispatch history yet -- retained cold-path session fallback,
        delegated to ``tools.sdlc_stage_query._resolve_issue_record`` (the
        SOLE place performing that resolution -- issue #2012 task 2).

        Uses a plain object (not MagicMock) for the legacy session double:
        MagicMock auto-vivifies ANY attribute access (including
        `ledger_key`), which would make get_dispatch_history()'s
        isinstance(PipelineLedger) check misclassify it as a ledger.
        """
        from tools import sdlc_dispatch

        class _FakeSession:
            stage_states = '{"_sdlc_dispatches": [{"skill": "/do-critique"}]}'

        session = _FakeSession()

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=session):
            result = sdlc_dispatch._cli_get(SimpleNamespace(session_id=None, issue_number=555003))

        assert result == [{"skill": "/do-critique"}]

    def test_get_returns_empty_when_target_repo_unresolved(self):
        """Risk 5 (reader side): target_repo cannot be resolved at all ->
        the defined empty outcome, never a phantom PipelineLedger[(None, N)]
        read."""
        from tools import sdlc_dispatch

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            result = sdlc_dispatch._cli_get(SimpleNamespace(session_id=None, issue_number=555004))

        assert result == []

    def test_get_without_issue_number_stays_plain_session_lookup(self):
        from tools import sdlc_dispatch

        find_mock = MagicMock(return_value=None)
        args = SimpleNamespace(session_id="some-session", issue_number=None)

        with patch.object(sdlc_dispatch, "_find_session", find_mock):
            result = sdlc_dispatch._cli_get(args)

        find_mock.assert_called_once_with(session_id="some-session", issue_number=None)
        assert result == []

    def test_reset_clears_ledger_history(self):
        from agent.pipeline_ledger import PipelineLedger
        from tools import sdlc_dispatch

        ledger = PipelineLedger.get_or_create("owner/reset-ledger", 555005)
        sdlc_dispatch.record_dispatch_for_ledger(ledger, skill="/do-build")

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=ledger):
            result = sdlc_dispatch._cli_reset(SimpleNamespace(session_id=None, issue_number=555005))

        assert result == {"ok": True, "history_length": 0}

    def test_reset_returns_empty_shape_when_target_repo_unresolved(self):
        from tools import sdlc_dispatch

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            result = sdlc_dispatch._cli_reset(SimpleNamespace(session_id=None, issue_number=555006))

        assert result == {"ok": False, "history_length": 0}


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
