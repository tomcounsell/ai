"""Unit tests for bridge.routing.resolve_customer and related helpers.

Tests cover:
- Subprocess form dispatch (argv, not shell)
- Importlib callable form dispatch
- Redis cache hit/miss/cached-None
- Raise ResolverUnavailable (not return None) on infrastructure error (issue #1817 A2)
- Failure counter increments
- valor-retry label STORE attempted on failure
- Sender pre-validation
- Output sanitization (multi-line, garbage rejection) raises ResolverUnavailable
- Success clears both the failure counter and any armed resolver_unavailable alert
"""

from unittest.mock import MagicMock, patch

import pytest

from bridge.routing import (
    ResolverUnavailable,
    get_resolver_failure_count,
    invalidate_customer_cache,
    resolve_customer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(key="test-proj", resolver=None):
    """Build a minimal project config dict."""
    cfg = {"_key": key, "name": key}
    if resolver is not None:
        cfg["customer_resolver"] = resolver
    return cfg


# ---------------------------------------------------------------------------
# Sender pre-validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_rejects_invalid_sender():
    """Malformed sender email returns None without dispatching or incrementing failures."""
    project = _project(resolver={"type": "subprocess", "command": ["echo", "cust_42"]})
    with patch("bridge.routing._get_redis") as mock_redis:
        result = await resolve_customer("not-an-email", project)
    assert result is None
    # Should NOT have interacted with Redis (no failure increment for malformed input)
    mock_redis.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_customer_rejects_empty_sender():
    project = _project(resolver={"type": "subprocess", "command": ["echo", "cust_42"]})
    result = await resolve_customer("", project)
    assert result is None


# ---------------------------------------------------------------------------
# No resolver configured -> None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_no_resolver_returns_none():
    """Projects without customer_resolver return None immediately."""
    project = _project()  # no resolver
    result = await resolve_customer("user@example.com", project)
    assert result is None


# ---------------------------------------------------------------------------
# Redis cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_cache_hit():
    """A cached customer_id is returned without dispatching the resolver."""
    project = _project(resolver={"type": "subprocess", "command": ["echo", "new-id"]})
    mock_r = MagicMock()
    mock_r.get.return_value = "cached-cust-42"
    with patch("bridge.routing._get_redis", return_value=mock_r):
        result = await resolve_customer("user@example.com", project)
    assert result == "cached-cust-42"


@pytest.mark.asyncio
async def test_resolve_customer_cached_none():
    """A cached empty string means the resolver previously returned None."""
    project = _project(resolver={"type": "subprocess", "command": ["echo", "new-id"]})
    mock_r = MagicMock()
    mock_r.get.return_value = ""  # cached None
    with patch("bridge.routing._get_redis", return_value=mock_r):
        result = await resolve_customer("user@example.com", project)
    assert result is None


# ---------------------------------------------------------------------------
# Subprocess form
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_subprocess_success():
    """Subprocess returning a valid customer_id is cached and returned."""
    project = _project(
        resolver={
            "type": "subprocess",
            "command": ["echo", "cust_42"],
            "cache_ttl_seconds": 60,
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None  # cache miss

    with patch("bridge.routing._get_redis", return_value=mock_r):
        result = await resolve_customer("user@example.com", project)

    assert result == "cust_42"
    # Cache set called with customer_id (stored as bytes)
    mock_r.setex.assert_called_once()
    args = mock_r.setex.call_args[0]
    assert b"cust_42" in args
    # Success clears both the failure counter AND any armed resolver_unavailable
    # alert (issue #1817 A2) — two distinct keys, two delete calls.
    deleted_keys = [c.args[0] for c in mock_r.delete.call_args_list]
    assert "resolver:failures:test-proj" in deleted_keys
    assert "email:resolver_unavailable" in deleted_keys


@pytest.mark.asyncio
async def test_resolve_customer_subprocess_empty_stdout_is_none():
    """Subprocess that prints nothing is a DEFINITIVE non-customer result — the
    resolver ran successfully, so this returns None (not raise). Cached as
    empty string; failure counter and resolver_unavailable alert are still
    cleared since the dispatch itself succeeded (issue #1817 A2)."""
    project = _project(resolver={"type": "subprocess", "command": ["sh", "-c", "true"]})
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("bridge.routing._get_redis", return_value=mock_r):
        result = await resolve_customer("user@example.com", project)

    assert result is None
    mock_r.setex.assert_called_once()
    # Cached as empty bytes (represents None)
    call_args = mock_r.setex.call_args[0]
    assert b"" in call_args
    deleted_keys = [c.args[0] for c in mock_r.delete.call_args_list]
    assert "resolver:failures:test-proj" in deleted_keys
    assert "email:resolver_unavailable" in deleted_keys


@pytest.mark.asyncio
async def test_resolve_customer_subprocess_whitespace_is_none():
    """Subprocess printing only whitespace/newlines returns None."""
    project = _project(resolver={"type": "subprocess", "command": ["sh", "-c", "printf '  \\n'"]})
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("bridge.routing._get_redis", return_value=mock_r):
        result = await resolve_customer("user@example.com", project)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_customer_subprocess_multiline_is_garbage():
    """Subprocess printing multi-line output (e.g. warnings + id) is an
    infrastructure failure — raises ResolverUnavailable, NOT "not a
    customer" (issue #1817 A2). Failure counter is still incremented."""
    project = _project(
        resolver={
            "type": "subprocess",
            "command": ["sh", "-c", "printf 'warning: x\\ncust_1\\n'"],
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("bridge.routing._get_redis", return_value=mock_r):
        with pytest.raises(ResolverUnavailable):
            await resolve_customer("user@example.com", project)

    mock_r.incr.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_customer_subprocess_html_garbage_is_rejected():
    """HTML or other garbage output is an infrastructure failure — raises
    ResolverUnavailable (issue #1817 A2)."""
    project = _project(
        resolver={
            "type": "subprocess",
            "command": ["sh", "-c", "echo '<html>foo</html>'"],
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("bridge.routing._get_redis", return_value=mock_r):
        with pytest.raises(ResolverUnavailable):
            await resolve_customer("user@example.com", project)

    mock_r.incr.assert_called_once()


# ---------------------------------------------------------------------------
# Failure path: counter + valor-retry label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_timeout_increments_failure_counter():
    """Subprocess timeout increments failure counter and raises
    ResolverUnavailable (issue #1817 A2) — a timeout is not "not a customer"."""
    project = _project(
        resolver={
            "type": "subprocess",
            "command": ["sleep", "100"],
            "timeout_seconds": 0.01,
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with patch("bridge.routing._get_redis", return_value=mock_r):
        with pytest.raises(ResolverUnavailable):
            await resolve_customer("user@example.com", project)

    mock_r.incr.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_customer_failure_attempts_valor_retry_label():
    """On subprocess failure, valor-retry IMAP label is attempted when conn+uid
    provided, and ResolverUnavailable is raised (issue #1817 A2)."""
    project = _project(
        resolver={
            "type": "subprocess",
            "command": ["sh", "-c", "exit 1"],
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None
    mock_imap_conn = MagicMock()

    with patch("bridge.routing._get_redis", return_value=mock_r):
        with pytest.raises(ResolverUnavailable):
            await resolve_customer(
                "user@example.com", project, imap_conn=mock_imap_conn, imap_uid=b"42"
            )

    mock_imap_conn.uid.assert_called_once_with("store", b"42", "+X-GM-LABELS", '("valor-retry")')


@pytest.mark.asyncio
async def test_resolve_customer_valor_retry_label_failure_is_nonfatal():
    """If the IMAP STORE errors, the failure is logged but ResolverUnavailable
    is still raised (issue #1817 A2)."""
    project = _project(resolver={"type": "subprocess", "command": ["sh", "-c", "exit 1"]})
    mock_r = MagicMock()
    mock_r.get.return_value = None
    mock_imap_conn = MagicMock()
    mock_imap_conn.uid.side_effect = Exception("IMAP store error")

    with patch("bridge.routing._get_redis", return_value=mock_r):
        with pytest.raises(ResolverUnavailable):
            await resolve_customer(
                "user@example.com", project, imap_conn=mock_imap_conn, imap_uid=b"42"
            )

    mock_r.incr.assert_called_once()


# ---------------------------------------------------------------------------
# Importlib callable form
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_customer_callable_form_success():
    """Callable form invokes the Python callable and caches result."""

    def fake_resolver(email_from: str):
        return "callable-cust-99"

    project = _project(
        resolver={
            "type": "callable",
            "callable": "tests.unit.test_customer_resolver._stub_resolver",
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with (
        patch("bridge.routing._get_redis", return_value=mock_r),
        patch(
            "bridge.routing._resolve_resolver_callable",
            return_value=fake_resolver,
        ),
    ):
        result = await resolve_customer("user@example.com", project)

    assert result == "callable-cust-99"


@pytest.mark.asyncio
async def test_resolve_customer_callable_form_returns_none():
    """Callable returning None is cached as empty string."""

    def fake_resolver(email_from: str):
        return None

    project = _project(
        resolver={
            "type": "callable",
            "callable": "tests.unit.test_customer_resolver._stub_resolver",
        }
    )
    mock_r = MagicMock()
    mock_r.get.return_value = None

    with (
        patch("bridge.routing._get_redis", return_value=mock_r),
        patch(
            "bridge.routing._resolve_resolver_callable",
            return_value=fake_resolver,
        ),
    ):
        result = await resolve_customer("user@example.com", project)

    assert result is None
    call_args = mock_r.setex.call_args[0]
    assert b"" in call_args


# ---------------------------------------------------------------------------
# invalidate_customer_cache
# ---------------------------------------------------------------------------


def test_invalidate_customer_cache():
    """invalidate_customer_cache deletes the Redis key for a sender."""
    mock_r = MagicMock()
    with patch("bridge.routing._get_redis", return_value=mock_r):
        invalidate_customer_cache("my-project", "user@example.com")
    mock_r.delete.assert_called_once()
    key = mock_r.delete.call_args[0][0]
    assert "my-project" in key
    assert "user@example.com" in key


# ---------------------------------------------------------------------------
# get_resolver_failure_count (issue #1817 A2 — reused by the email bridge's
# alert-arming gate, not a parallel tally)
# ---------------------------------------------------------------------------


def test_get_resolver_failure_count_reads_counter():
    """Returns the current int value of resolver:failures:{project_key}."""
    mock_r = MagicMock()
    mock_r.get.return_value = b"5"
    with patch("bridge.routing._get_redis", return_value=mock_r):
        assert get_resolver_failure_count("my-project") == 5
    mock_r.get.assert_called_once_with("resolver:failures:my-project")


def test_get_resolver_failure_count_missing_key_is_zero():
    """No counter yet -> 0, not an error."""
    mock_r = MagicMock()
    mock_r.get.return_value = None
    with patch("bridge.routing._get_redis", return_value=mock_r):
        assert get_resolver_failure_count("my-project") == 0


def test_get_resolver_failure_count_fails_closed_on_redis_error():
    """A Redis error returns 0 rather than raising (best-effort alert gate)."""
    with patch("bridge.routing._get_redis", side_effect=Exception("redis down")):
        assert get_resolver_failure_count("my-project") == 0


# ---------------------------------------------------------------------------
# Stub resolver (used by importlib callable tests)
# ---------------------------------------------------------------------------


def _stub_resolver(email_from: str):
    """Stub resolver for importlib callable form tests."""
    return "callable-cust-99"
