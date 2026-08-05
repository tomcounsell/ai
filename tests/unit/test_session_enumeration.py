"""Tests for the shared AgentSession enumeration seam (issue #2519).

The seam exists because three callers enumerated sessions three different ways
and got three different answers against the same Redis. These tests lock in the
two properties that made the scan the sanctioned path: it returns records the
``status`` secondary index has lost, and it says so out loud.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.sessions]


def _mk(status: str, session_id: str, record_id: str | None = "rec"):
    m = MagicMock()
    m.status = status
    m.session_id = session_id
    m.id = record_id
    return m


class TestEnumerateSessions:
    def test_returns_every_scanned_session_when_no_status_filter(self):
        from models.session_enumeration import enumerate_sessions

        rows = [_mk("running", "a"), _mk("pending", "b"), _mk("completed", "c")]
        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.return_value = rows
            result = enumerate_sessions(check_divergence=False)

        assert [s.session_id for s in result] == ["a", "b", "c"]

    def test_filters_by_status_in_python(self):
        from models.session_enumeration import enumerate_sessions

        rows = [_mk("running", "a"), _mk("pending", "b"), _mk("completed", "c")]
        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.return_value = rows
            result = enumerate_sessions(("pending", "running"), check_divergence=False)

        assert sorted(s.session_id for s in result) == ["a", "b"]

    def test_returns_sessions_the_status_index_has_lost(self):
        """The #2519 hole: ``filter(status="pending")`` returns nothing while the
        records are intact and carry ``status="pending"``. The scan finds them."""
        from models.session_enumeration import enumerate_sessions

        stranded = [_mk("pending", f"stranded-{i}") for i in range(22)]
        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.return_value = stranded
            agent_session.query.filter.return_value = []  # the index knows nothing
            result = enumerate_sessions(("pending",), check_divergence=False)

        assert len(result) == 22

    def test_skips_records_left_id_less_by_a_partial_write(self):
        """A half-written record has no id. Every caller of the seam would act
        on it: `kill --all` calls finalize_session on it, `valor-session list`
        prints it, the dashboard renders it. The seam drops it once for all."""
        from models.session_enumeration import enumerate_sessions

        rows = [_mk("pending", "real"), _mk("pending", "partial", record_id=None)]
        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.return_value = rows
            result = enumerate_sessions(check_divergence=False)

        assert [s.session_id for s in result] == ["real"]

    def test_id_less_records_are_left_out_of_the_scan_count(self):
        """An id-less hash is the #2101 shape: it inflates the index without
        hydrating. Counting it as scanned would hide that disagreement."""
        from models.session_enumeration import enumerate_sessions

        rows = [_mk("pending", "real"), _mk("pending", "partial", record_id=None)]
        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.return_value = rows
            agent_session.query.count.return_value = 2
            with patch("models.session_enumeration.check_status_index_divergence") as check:
                enumerate_sessions(("pending",))

        assert check.call_args.args[0]["pending"] == 1

    def test_query_failure_returns_empty_list(self):
        from models.session_enumeration import enumerate_sessions

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.all.side_effect = RuntimeError("redis down")
            assert enumerate_sessions() == []


class TestStatusIndexDivergence:
    def test_logs_when_index_under_reports(self, caplog):
        from models.session_enumeration import check_status_index_divergence

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.return_value = 0
            with caplog.at_level(logging.WARNING):
                diverged = check_status_index_divergence({"pending": 22}, ("pending",), force=True)

        assert diverged == {"pending": (0, 22)}
        assert "index=0 scan=22" in caplog.text

    def test_logs_when_index_over_reports(self):
        """#2101 shape: phantom identity-less hashes inflate the index set."""
        from models.session_enumeration import check_status_index_divergence

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.return_value = 40
            diverged = check_status_index_divergence({"pending": 2}, ("pending",), force=True)

        assert diverged == {"pending": (40, 2)}

    def test_silent_when_counts_agree(self, caplog):
        from models.session_enumeration import check_status_index_divergence

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.return_value = 3
            with caplog.at_level(logging.WARNING):
                diverged = check_status_index_divergence({"running": 3}, ("running",), force=True)

        assert diverged == {}
        assert "disagrees" not in caplog.text

    def test_throttled_between_checks(self):
        """The 5s dashboard poll must not pay for a count per status every time."""
        import models.session_enumeration as mod

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.return_value = 0
            mod.check_status_index_divergence({"pending": 1}, ("pending",), force=True)
            calls_after_forced = agent_session.query.count.call_count
            # Immediately after a check, the throttle window is open.
            assert mod.check_status_index_divergence({"pending": 1}, ("pending",)) == {}
            assert agent_session.query.count.call_count == calls_after_forced

    def test_first_check_runs_even_soon_after_boot(self):
        """`time.monotonic()` counts from boot, not from process start. A
        process that opens within the first 5 minutes of uptime has to audit
        the index, so "never checked" is its own state rather than time zero."""
        import models.session_enumeration as mod

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.return_value = 0
            with (
                patch.object(mod, "_last_divergence_check_at", None),
                patch("time.monotonic", return_value=10.0),
            ):
                diverged = mod.check_status_index_divergence({"pending": 22}, ("pending",))

        assert diverged == {"pending": (0, 22)}

    def test_count_failure_skips_that_status(self):
        from models.session_enumeration import check_status_index_divergence

        with patch("models.agent_session.AgentSession") as agent_session:
            agent_session.query.count.side_effect = RuntimeError("index gone")
            assert check_status_index_divergence({"pending": 5}, ("pending",), force=True) == {}
