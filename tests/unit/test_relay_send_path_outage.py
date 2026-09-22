"""A Redis outage on a relay send path must never look like an empty queue.

Both outbox relays report progress as "messages sent this cycle". Zero is the
normal outcome. Folding an infrastructure failure into that zero is how a send
path stays down while every health signal reads green -- this system lost 26
hours of Telegram replies to exactly that shape.

These tests pin the two halves of the fix:

* the key sweep is a bounded ``SCAN``, so a production-sized keyspace cannot
  blow the shared client's socket timeout the way a full-keyspace ``KEYS`` can;
* a ``RedisError`` from the sweep raises :class:`OutboxUnavailableError` and is
  Sentry-reported, rather than returning ``0``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import redis

from bridge.relay_errors import OutboxUnavailableError
from utils.redis_client import scan_keys


class FakeScanClient:
    """A client whose ``scan`` walks a fixed key list one COUNT-sized page at a time."""

    def __init__(self, keys):
        self.keys = list(keys)
        self.calls = 0

    def scan(self, cursor=0, match=None, count=10):
        self.calls += 1
        page = self.keys[cursor : cursor + count]
        nxt = cursor + count
        return (0 if nxt >= len(self.keys) else nxt), page


class OvershootScanClient:
    """Two rounds only: the first stays under ``limit``, the second's final
    page carries the total past it in the same round trip that closes the
    cursor -- the scenario the ``scan_keys`` docstring calls out as
    "possibly more than ``scan_key_limit``" while still reporting
    ``truncated=False``.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.calls = 0

    def scan(self, cursor=0, match=None, count=10):
        self.calls += 1
        if cursor == 0:
            return 1, [f"email:outbox:{i}" for i in range(self.limit - 1)]
        return 0, [f"email:outbox:overshoot:{i}" for i in range(50)]


class TimingOutClient:
    """A client whose key sweep times out, as a real one does on a huge keyspace."""

    def scan(self, *args, **kwargs):
        raise redis.exceptions.TimeoutError("Timeout reading from socket")


class EmptyOutboxClient:
    """A reachable Redis holding no outbox keys -- the ordinary idle case."""

    def scan(self, cursor=0, match=None, count=10):
        return 0, []

    def set(self, *args, **kwargs):
        return True


class TestScanKeysIsBounded:
    def test_it_returns_every_key_when_under_the_limit(self):
        client = FakeScanClient(f"email:outbox:{i}" for i in range(1200))
        keys, truncated = scan_keys(client, "email:outbox:*")
        assert len(keys) == 1200
        assert truncated is False

    def test_it_pages_rather_than_asking_for_the_whole_keyspace_at_once(self):
        """The point of SCAN over KEYS: bounded work per round trip."""
        client = FakeScanClient(f"email:outbox:{i}" for i in range(1200))
        scan_keys(client, "email:outbox:*")
        assert client.calls > 1, "a single unbounded call defeats the purpose of SCAN"

    def test_it_stops_and_reports_truncation_on_a_huge_keyspace(self):
        client = FakeScanClient(f"email:outbox:{i}" for i in range(25_000))
        keys, truncated = scan_keys(client, "email:outbox:*")
        assert truncated is True
        assert len(keys) < 25_000

    def test_a_sweep_that_completes_on_exactly_the_limit_is_not_truncated(self):
        """``truncated`` means "there is more", not "I returned a round number".

        A caller that reasons about the *absence* of a key is told to distrust a
        truncated result, so a false positive here costs real signal -- and the
        relays log a warning on it every poll.
        """
        from config.settings import settings

        limit = int(settings.redis.scan_key_limit)
        client = FakeScanClient(f"email:outbox:{i}" for i in range(limit))
        keys, truncated = scan_keys(client, "email:outbox:*")
        assert len(keys) == limit
        assert truncated is False

    def test_keys_repeated_across_scan_rounds_are_returned_once(self):
        """Redis SCAN guarantees at-least-once, not exactly-once, delivery.

        A key present for a whole iteration can still be handed back twice when
        the keyspace rehashes mid-sweep. A repeat LPOP is harmless, but
        ``list_dead_letters`` renders what it is given, and ``keys(pattern)``
        never did this.
        """
        client = FakeScanClient(["email:outbox:a", "email:outbox:b", "email:outbox:a"])
        keys, truncated = scan_keys(client, "email:outbox:*")
        assert keys == ["email:outbox:a", "email:outbox:b"]
        assert truncated is False

    def test_a_sweep_that_closes_the_cursor_while_crossing_the_limit_is_not_truncated(self):
        """The impossible-looking case the docstring documents four times over:

        ``truncated`` is decided by the cursor, not by the returned length, so
        a final page that both closes the cursor and pushes the running total
        past ``scan_key_limit`` still reports ``truncated=False`` with
        ``len(keys) > limit``. A refactor that checked the limit before the
        cursor would instead truncate here, silently contradicting the
        documented contract while every other test in this class kept passing.
        """
        from config.settings import settings

        limit = int(settings.redis.scan_key_limit)
        client = OvershootScanClient(limit)
        keys, truncated = scan_keys(client, "email:outbox:*")
        assert len(keys) > limit
        assert truncated is False


class TestTimeoutIsNotAnEmptyOutbox:
    """The blocker this file exists for: 0 must mean 'nothing to send'."""

    def test_email_relay_raises_rather_than_reporting_a_quiet_cycle(self):
        import bridge.email_relay as email_relay

        with patch.object(email_relay, "_get_redis_connection", lambda: TimingOutClient()):
            with pytest.raises(OutboxUnavailableError):
                asyncio.run(email_relay.process_outbox())

    def test_telegram_relay_raises_rather_than_reporting_a_quiet_cycle(self):
        import bridge.telegram_relay as telegram_relay

        with patch.object(telegram_relay, "_get_redis_connection", lambda: TimingOutClient()):
            with pytest.raises(OutboxUnavailableError):
                asyncio.run(telegram_relay.process_outbox(MagicMock()))

    def test_an_outage_is_reported_to_sentry(self):
        import bridge.email_relay as email_relay

        with patch.object(email_relay, "_get_redis_connection", lambda: TimingOutClient()):
            with patch("bridge.relay_errors.report_send_path_failure") as reported:
                # Patched at the definition site, so re-import the bound name.
                with patch.object(email_relay, "report_send_path_failure", reported):
                    with pytest.raises(OutboxUnavailableError):
                        asyncio.run(email_relay.process_outbox())
        assert reported.called, "an outbox outage must be reported, not only returned"
        transport, exc = reported.call_args[0]
        assert transport == "email"
        assert isinstance(exc, redis.exceptions.TimeoutError)

    def test_a_genuinely_empty_outbox_still_returns_zero(self):
        """The failure mode must not be traded for a relay that cries wolf."""
        import bridge.email_relay as email_relay

        with patch.object(email_relay, "_get_redis_connection", lambda: EmptyOutboxClient()):
            assert asyncio.run(email_relay.process_outbox()) == 0


_CLIENT_PRODUCERS = {
    "_get_redis",
    "_get_redis_connection",
    "text_redis",
    "bytes_redis",
    "derived_redis",
}


def _client_bound_names(tree) -> set[str]:
    """Local names holding a Redis client in ``tree``.

    Assignment targets are enough for the send paths: every client reaches a
    local through one, including the offloaded spelling
    ``r = await asyncio.to_thread(_get_redis_connection)`` -- so the producer is
    matched anywhere inside the assigned value, not only as its callee.
    """
    import ast

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        referenced = {
            sub.id if isinstance(sub, ast.Name) else sub.attr
            for sub in ast.walk(value)
            if isinstance(sub, (ast.Name, ast.Attribute))
        }
        if not referenced & _CLIENT_PRODUCERS:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _keys_offenders(tree, module_path: str) -> list[str]:
    """``KEYS`` and unbounded ``scan_iter`` sites in ``tree``."""
    import ast

    clients = _client_bound_names(tree)
    offenders = []
    for node in ast.walk(tree):
        # `.keys` is matched as a bare ATTRIBUTE, not as a call. Both relays
        # spelled it `asyncio.to_thread(r.keys, PATTERN)` -- a reference handed
        # to the threadpool, never an `ast.Call` here. A matcher that only
        # looked at calls reported both send paths clean while the full-keyspace
        # KEYS sat in plain sight. The base must resolve to a Redis client
        # though, or every `payload.keys()` in the module reads as an outage.
        if isinstance(node, ast.Attribute) and node.attr == "keys":
            base = node.value
            if isinstance(base, ast.Name) and base.id in clients:
                offenders.append(f"{module_path}:{node.lineno} KEYS")
            elif isinstance(base, ast.Attribute) and base.attr in _CLIENT_PRODUCERS:
                offenders.append(f"{module_path}:{node.lineno} KEYS")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "scan_iter"
            and not any(kw.arg == "count" for kw in node.keywords)
        ):
            offenders.append(f"{module_path}:{node.lineno} unbounded scan_iter")
    return offenders


class TestNoProductionKeysCall:
    """A full-keyspace KEYS must not come back to either send path."""

    @pytest.mark.parametrize(
        "module_path",
        ["bridge/email_relay.py", "bridge/telegram_relay.py", "bridge/email_dead_letter.py"],
    )
    def test_the_module_does_not_call_keys_or_an_unbounded_scan_iter(self, module_path):
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        tree = ast.parse((repo_root / module_path).read_text())

        offenders = _keys_offenders(tree, module_path)

        assert not offenders, (
            "Send-path modules must sweep keys via utils.redis_client.scan_keys. "
            f"Found: {offenders}"
        )

    def test_an_ordinary_dict_keys_is_not_a_full_keyspace_scan(self, tmp_path):
        """The guard must stay narrow enough to survive ordinary Python.

        Matching every ``.keys`` attribute caught the defect, but the first
        ``payload.keys()`` added to any of these modules would have turned it
        red with a "full-keyspace KEYS" message pointing at nothing.
        """
        import ast

        tree = ast.parse(
            "def f(payload):\n"
            "    r = _get_redis()\n"
            "    for name in payload.keys():\n"
            "        r.get(name)\n"
        )
        assert _keys_offenders(tree, "sample.py") == []

    def test_a_client_bound_keys_reference_is_still_caught(self):
        """...and narrow must not mean blind: the defect's own spelling."""
        import ast

        tree = ast.parse(
            "async def f():\n"
            "    r = await asyncio.to_thread(_get_redis_connection)\n"
            "    return await asyncio.to_thread(r.keys, PATTERN)\n"
        )
        assert _keys_offenders(tree, "sample.py") == ["sample.py:3 KEYS"]
