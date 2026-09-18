"""Proof B — the mocked-seam ORDERING proof for ``prefer_type`` (#3091 Risk 1).

``tests/unit/session_lookup_mock.py::wire_session_lookup`` re-implements the
resolver's ordering on top of a ``MagicMock`` stand-in for ``AgentSession``.
Its ``rows(session_id, **filters)`` signature swallows ``prefer_type`` into
``**filters``, forwards it to ``query.filter``, and then orders by plain
newest-first. So an unextended helper **accepts the keyword, forwards it, and
silently ignores it** — verified by reproduction in the plan's Risk 1: the call
args come back as ``call(session_id='s', prefer_type='eng')`` while the returned
order is ``['pm', 'eng']``, the preference never applied.

That is why a test asserting the kwarg is *forwarded* does not count: it is
already green against the broken seam. The proof has to be on the ORDER.

Why the assertion is made at the seam rather than inside one of the five
production sites: today every one of those sites still hand-rolls the eng loop
over an unordered list, so a site-level test is GREEN against the broken seam
and pins nothing. Once the sites migrate they collapse to ``[0]`` of the ordered
list, which is exactly the two reads asserted below. This file exercises the
seam through both entry points a migrated site can use.

Why a new file: ``session_lookup_mock.py`` is a helper module, not a collected
test, and no existing test file owns it;
``tests/unit/test_agent_session_newest_wins.py`` is the real-Redis pin for the
resolver itself, and folding a ``MagicMock`` seam test into it would blur that
file's single purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from models.agent_session import AgentSession
from tests.unit.session_lookup_mock import wire_session_lookup

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _row(row_id: str, session_type: str | None, created_at: datetime) -> MagicMock:
    row = MagicMock(spec=AgentSession)
    row.id = row_id
    row.session_type = session_type
    row.created_at = created_at
    return row


def _wired_mock_class() -> MagicMock:
    """A mocked ``AgentSession`` whose class set holds a NEWER non-eng row and
    an OLDER eng row — preference and recency deliberately disagree."""
    newer_non_eng = _row("pm-row", "teammate", _T0 + timedelta(hours=1))
    older_eng = _row("eng-row", "eng", _T0)

    mock_cls = MagicMock()
    # Insertion order mirrors the class set: the newer non-eng row first, so a
    # seam that only sorts newest-first hands it back at [0].
    mock_cls.query.filter.return_value = [newer_non_eng, older_eng]
    return wire_session_lookup(mock_cls)


class TestWireSessionLookupPreferType:
    def test_newest_for_session_id_prefers_eng_over_newer_non_eng(self):
        """The ``[0]``-taking read: the OLDER eng row must come back."""
        mock_cls = _wired_mock_class()

        picked = mock_cls.newest_for_session_id("s", prefer_type="eng")

        assert picked is not None
        assert picked.id == "eng-row"

    def test_rows_for_session_id_orders_eng_first(self):
        """The scanning read: eng rows first, then the rest."""
        mock_cls = _wired_mock_class()

        rows = mock_cls.rows_for_session_id("s", prefer_type="eng")

        assert [r.id for r in rows] == ["eng-row", "pm-row"]
        assert rows[0].id == "eng-row"

    def test_default_order_is_still_plain_newest_first(self):
        """Omitting ``prefer_type`` must degrade to newest-first, not to the
        preference — the seam must not over-apply what the model does not."""
        mock_cls = _wired_mock_class()

        rows = mock_cls.rows_for_session_id("s")

        assert [r.id for r in rows] == ["pm-row", "eng-row"]
