"""Wire a ``MagicMock`` stand-in for ``AgentSession`` to answer the session_id resolver.

Production code reads a session by ``session_id`` through
``AgentSession.rows_for_session_id`` / ``newest_for_session_id`` (#3091). Tests
that replace the class with a ``MagicMock`` configure ``query.filter`` and
would otherwise get a bare ``MagicMock`` back from either resolver.
``wire_session_lookup`` derives both from the mock's own ``query.filter``, with
the real newest-first ordering, so a test's ``return_value`` / ``side_effect``
and its ``query.filter`` call assertions keep meaning what they meant.

Both resolvers take the model's keyword-only ``prefer_type`` and apply the real
grouping rule: matching ``session_type`` first, then the rest, each group
ordered independently by ``_newest_first_key``. ``prefer_type`` is not a query
filter, so — exactly as in the model — it is never forwarded to
``query.filter``, and ``prefer_type=None`` / ``""`` short-circuit to plain
newest-first. A seam that accepted the keyword and ignored it would hand the
``[0]``-taking production sites a plausible but unpreferred row with no signal.
"""

from __future__ import annotations

from models.agent_session import AgentSession as _RealAgentSession


def wire_session_lookup(mock_cls):
    """Route ``mock_cls``'s resolver methods through its ``query.filter``."""

    def rows(session_id, *, prefer_type=None, **filters):
        found = list(mock_cls.query.filter(session_id=session_id, **filters))
        if not prefer_type:
            # Default path, mirroring the model: the short-circuit comes before
            # any partition, so a row with ``session_type=None`` is not floated
            # to the head by the ``None`` default.
            found.sort(key=_RealAgentSession._newest_first_key, reverse=True)
            return found

        matching = []
        others = []
        for row in found:
            if getattr(row, "session_type", None) == prefer_type:
                matching.append(row)
            else:
                others.append(row)
        matching.sort(key=_RealAgentSession._newest_first_key, reverse=True)
        others.sort(key=_RealAgentSession._newest_first_key, reverse=True)
        return matching + others

    def newest(session_id, *, prefer_type=None, **filters):
        found = rows(session_id, prefer_type=prefer_type, **filters)
        return found[0] if found else None

    mock_cls.rows_for_session_id.side_effect = rows
    mock_cls.newest_for_session_id.side_effect = newest
    return mock_cls
