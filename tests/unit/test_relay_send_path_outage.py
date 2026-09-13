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

        offenders = []
        for node in ast.walk(tree):
            # `.keys` is matched as a bare ATTRIBUTE, not as a call. Both relays
            # spelled it `asyncio.to_thread(r.keys, PATTERN)` -- a reference
            # handed to the threadpool, never an `ast.Call` here. A matcher that
            # only looked at calls reported both send paths clean while the
            # full-keyspace KEYS sat in plain sight.
            if isinstance(node, ast.Attribute) and node.attr == "keys":
                offenders.append(f"{module_path}:{node.lineno} KEYS")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "scan_iter"
                and not any(kw.arg == "count" for kw in node.keywords)
            ):
                offenders.append(f"{module_path}:{node.lineno} unbounded scan_iter")

        assert not offenders, (
            "Send-path modules must sweep keys via utils.redis_client.scan_keys. "
            f"Found: {offenders}"
        )
