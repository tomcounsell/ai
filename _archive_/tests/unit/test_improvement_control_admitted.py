"""`admitted` is inert to every mechanism that isn't the improvement scheduler
adapter or its reconcile pass (Task 3, Decision 4).

Seeds one `admitted` row and proves it is invisible to: `RESUMABLE_STATUSES`,
the worker's `pending`-status query (the same exact-match `AgentSession.query.filter`
`worker/__main__.py` and `_agent_session_health_check` both use), and a
`running`-status query. Popoto's `IndexedField` filter is exact-match, so this
is largely true by construction -- the point of the test is to pin that
construction, not to re-derive it.
"""

from __future__ import annotations

import uuid

from models.agent_session import AgentSession, SessionType
from models.session_lifecycle import RESUMABLE_STATUSES, transition_status

PK = "test-3215-admitted-status"


def seed_admitted() -> AgentSession:
    session = AgentSession.create(
        project_key=PK,
        chat_id="0",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"test-admitted-{uuid.uuid4().hex[:8]}",
        working_dir=".",
    )
    transition_status(session, "admitted", reason="test seed")
    return session


class TestAdmittedIsNotResumable:
    def test_admitted_is_not_in_resumable_statuses(self):
        assert "admitted" not in RESUMABLE_STATUSES


class TestWorkerAndHealthCheckQueriesExcludeAdmitted:
    def test_pending_status_query_excludes_an_admitted_row(self):
        session = seed_admitted()
        pending_ids = {
            s.session_id for s in AgentSession.query.filter(status="pending", project_key=PK)
        }
        assert session.session_id not in pending_ids

    def test_running_status_query_excludes_an_admitted_row(self):
        session = seed_admitted()
        running_ids = {
            s.session_id for s in AgentSession.query.filter(status="running", project_key=PK)
        }
        assert session.session_id not in running_ids

    def test_admitted_status_query_finds_it(self):
        """Positive control: the row really is `admitted`, not silently
        something else -- a query that found it nowhere would pass the two
        exclusion tests above for the wrong reason."""
        session = seed_admitted()
        admitted_ids = {
            s.session_id for s in AgentSession.query.filter(status="admitted", project_key=PK)
        }
        assert session.session_id in admitted_ids
