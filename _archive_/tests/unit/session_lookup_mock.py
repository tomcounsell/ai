"""Wire a ``MagicMock`` stand-in for ``AgentSession`` to answer the session_id resolver.

Production code reads a session by ``session_id`` through
``AgentSession.rows_for_session_id`` / ``newest_for_session_id`` (#3091). Tests
that replace the class with a ``MagicMock`` configure ``query.filter`` and
would otherwise get a bare ``MagicMock`` back from either resolver.
``wire_session_lookup`` derives both from the mock's own ``query.filter``, with
the real newest-first ordering, so a test's ``return_value`` / ``side_effect``
and its ``query.filter`` call assertions keep meaning what they meant.
"""

from __future__ import annotations

from models.agent_session import AgentSession as _RealAgentSession


def wire_session_lookup(mock_cls):
    """Route ``mock_cls``'s resolver methods through its ``query.filter``."""

    def rows(session_id, **filters):
        found = list(mock_cls.query.filter(session_id=session_id, **filters))
        found.sort(key=_RealAgentSession._newest_first_key, reverse=True)
        return found

    def newest(session_id, **filters):
        found = rows(session_id, **filters)
        return found[0] if found else None

    mock_cls.rows_for_session_id.side_effect = rows
    mock_cls.newest_for_session_id.side_effect = newest
    return mock_cls
