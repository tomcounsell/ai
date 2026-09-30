"""Unit tests for subject-line coalescing helpers.

Tests cover:
- normalize_subject: stripping Re/Fwd/AW prefixes, bracket tags, whitespace
- find_coalescing_session_id: DB query, 48h bound, empty subject guard
"""

from unittest.mock import MagicMock, patch

import pytest

from bridge.email_bridge import find_coalescing_session_id, normalize_subject

# ---------------------------------------------------------------------------
# normalize_subject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Plain subject
        ("Hello world", "hello world"),
        # Re: prefix
        ("Re: Hello world", "hello world"),
        ("re: Hello world", "hello world"),
        ("RE: Hello world", "hello world"),
        # Fwd variants
        ("Fwd: Hello world", "hello world"),
        ("FWD: Hello world", "hello world"),
        ("Fw: Hello world", "hello world"),
        ("FW: Hello world", "hello world"),
        # German AW
        ("AW: Hello world", "hello world"),
        ("Aw: Hello world", "hello world"),
        ("Antw: Hello world", "hello world"),
        # Numbered Re
        ("Re[3]: Hello world", "hello world"),
        ("Re[10]: Hello world", "hello world"),
        # Bracket ticket tags
        ("[ticket-123] Hello world", "hello world"),
        ("[TICKET-42] Hello world", "hello world"),
        # Chained prefix
        ("Re: Fwd: Hello world", "hello world"),
        ("Re: Re: Hello world", "hello world"),
        # Extra whitespace
        ("  Hello   world  ", "hello world"),
        # Empty subject
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_subject(raw, expected):
    assert normalize_subject(raw) == expected


# ---------------------------------------------------------------------------
# find_coalescing_session_id
# ---------------------------------------------------------------------------


def _make_session(session_id, customer_id, subject, created_at):
    """Build a minimal fake AgentSession-like object."""
    s = MagicMock()
    s.session_id = session_id
    s.created_at = created_at
    s.extra_context = {
        "customer_id": customer_id,
        "email_subject": subject,
    }
    return s


def test_find_coalescing_session_id_empty_subject_returns_none():
    """Empty normalized subject never coalesces."""
    result = find_coalescing_session_id("proj", "cust-42", "")
    assert result is None


def test_find_coalescing_session_id_whitespace_subject_returns_none():
    result = find_coalescing_session_id("proj", "cust-42", "   ")
    assert result is None


def test_find_coalescing_session_id_match():
    """Returns session_id when a matching session exists within 48h."""
    import time

    now = time.time()
    session = _make_session(
        session_id="email_proj_cust42_111",
        customer_id="cust-42",
        subject="Hello world",
        created_at=now - 3600,  # 1 hour ago
    )

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[session]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result == "email_proj_cust42_111"


def test_find_coalescing_session_id_no_match_different_customer():
    """Different customer_id does not coalesce."""
    import time

    now = time.time()
    session = _make_session(
        session_id="email_proj_cust99_111",
        customer_id="cust-99",
        subject="Hello world",
        created_at=now - 3600,
    )

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[session]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result is None


def test_find_coalescing_session_id_no_match_different_subject():
    """Different normalized subject does not coalesce."""
    import time

    now = time.time()
    session = _make_session(
        session_id="email_proj_cust42_111",
        customer_id="cust-42",
        subject="Different topic",
        created_at=now - 3600,
    )

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[session]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result is None


def test_find_coalescing_session_id_too_old():
    """Sessions older than 48 hours do not coalesce."""
    import time

    now = time.time()
    old_session = _make_session(
        session_id="email_proj_cust42_old",
        customer_id="cust-42",
        subject="Hello world",
        created_at=now - (49 * 3600),  # 49 hours ago
    )

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[old_session]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result is None


def test_find_coalescing_session_id_picks_most_recent():
    """When multiple matches exist, returns the most recently created session_id."""
    import time

    now = time.time()
    old = _make_session("old-session", "cust-42", "Hello world", now - 7200)
    recent = _make_session("recent-session", "cust-42", "Hello world", now - 1800)

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[old, recent]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result == "recent-session"


def test_find_coalescing_session_id_normalizes_stored_subject():
    """Subject matching normalizes the stored email_subject from extra_context."""
    import time

    now = time.time()
    session = _make_session(
        session_id="email_proj_cust42_111",
        customer_id="cust-42",
        subject="Re: Hello world",  # stored with Re: prefix
        created_at=now - 3600,
    )

    with patch("bridge.email_bridge._query_non_terminal_sessions", return_value=[session]):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result == "email_proj_cust42_111"


def test_find_coalescing_session_id_db_error_returns_none():
    """DB query failure returns None gracefully."""
    with patch(
        "bridge.email_bridge._query_non_terminal_sessions",
        side_effect=Exception("Redis error"),
    ):
        result = find_coalescing_session_id("proj", "cust-42", "hello world")

    assert result is None
