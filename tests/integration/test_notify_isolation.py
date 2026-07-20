"""AC#1 gate: fixture notifies must land ONLY on the db-scoped channel (#2147).

Redis pub/sub is server-global, NOT db-scoped: ``PUBLISH``/``SUBSCRIBE`` operate
per Redis *server*, so before this fix a fixture enqueue on a test db (db>=1) still
delivered on the one global ``valor:sessions:new`` channel that the launchd live
worker (db=0) subscribes to. On 2026-07-17 that leak spun up production queue
loops for fixture sessions. The fix derives the channel name from the active db
(``notify_channel_for``): db=0 keeps ``valor:sessions:new``; any test db gets
``valor:sessions:new:db{N}``.

The gating assertion here is a **deterministic dual-channel probe**, NOT a log
scan. A negative log scan against an async live-worker writer is timing-fragile
(a real regression could pass vacuously if the scan fires before the log flush)
and skips in CI, so it cannot be the criterion that gates the PR. The probe both
proves landing and acts as its own happens-after barrier:

- Positive probe subscribes on the db-scoped channel and asserts exactly one
  message arrives — proves the notify DID fire on the isolated channel.
- Negative probe subscribes on the bare production channel and asserts zero
  messages — proves a live worker could never have received it.

Because pub/sub is synchronous within one Redis server, the positive-probe
receipt is a happens-after barrier making the negative-probe read deterministic,
not a race. The real publish path (``_push_agent_session``) is exercised — no
mocks of Redis pub/sub.

The ``logs/worker.log`` scan (``TestLiveWorkerLogSpotCheck``) is a demoted
live-machine spot check: it skips cleanly when there is no worker log, so it never
produces a false failure in CI. The dual-channel probe is the CI gate.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest

from agent.agent_session_queue import _push_agent_session, notify_channel_for

pytestmark = pytest.mark.integration

# The bare production channel a db=0 live worker subscribes to. A fixture notify
# reaching THIS channel is the leak (#2147) — the negative probe asserts zero.
PRODUCTION_CHANNEL = "valor:sessions:new"


def _raw_client(popoto_client):
    """Build a raw redis client on the same server as ``popoto_client``.

    Pub/sub is server-global, so the db number is irrelevant for delivery; we
    reuse the popoto client's host/port/auth so the probe listens on the exact
    Redis server the publish targets.
    """
    import redis

    kw = popoto_client.connection_pool.connection_kwargs
    return redis.Redis(
        host=kw.get("host", "localhost"),
        port=kw.get("port", 6379),
        db=int(kw.get("db", 0) or 0),
        username=kw.get("username"),
        password=kw.get("password"),
        decode_responses=False,
    )


def _subscribe(client, channel: str):
    """Subscribe a fresh pubsub to ``channel`` and drain the subscribe confirmation."""
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(channel)
    # Drain the subscribe-confirmation frame so the first data get_message() call
    # returns an actual published message (belt-and-suspenders with
    # ignore_subscribe_messages=True).
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        msg = pubsub.get_message(timeout=0.1)
        if msg is None:
            break
    return pubsub


def _enqueue_fixture(worker_key: str) -> None:
    """Enqueue a fixture session through the REAL publish path on the test db."""
    asyncio.run(
        _push_agent_session(
            project_key=worker_key,
            session_id=f"{worker_key}-sess",
            working_dir="/tmp",
            message_text="notify isolation probe",
            sender_name="NotifyIsolationTest",
            chat_id=f"{worker_key}-chat",
            telegram_message_id=1,
        )
    )


class TestNotifyChannelIsolation:
    """The deterministic dual-channel probe — the CI gate for AC#1."""

    def test_fixture_notify_lands_only_on_db_scoped_channel(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        # Under the autouse redis_test_db fixture, POPOTO_REDIS_DB points at a
        # per-process test db (db>=1) → the channel is db-scoped. If this fails,
        # the isolation fixture is not active and the whole premise is moot.
        db_channel = notify_channel_for(POPOTO_REDIS_DB)
        assert db_channel != PRODUCTION_CHANNEL, (
            f"Expected a db-scoped channel under redis_test_db, got {db_channel!r}. "
            "The autouse test-db fixture must repoint POPOTO_REDIS_DB to db>=1."
        )
        assert db_channel.startswith("valor:sessions:new:db")

        worker_key = f"test-notify-isolation-{uuid.uuid4().hex[:8]}"

        client = _raw_client(POPOTO_REDIS_DB)
        pos = _subscribe(client, db_channel)
        neg = _subscribe(client, PRODUCTION_CHANNEL)
        try:
            _enqueue_fixture(worker_key)

            # POSITIVE probe: the notify fired on the isolated db-scoped channel.
            # This receipt is also the happens-after barrier for the negative read.
            positive = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                msg = pos.get_message(timeout=0.2)
                if msg and msg.get("type") == "message":
                    positive = msg
                    break
            assert positive is not None, (
                f"No message on the db-scoped channel {db_channel!r} — the fixture "
                "notify did not fire on the isolated channel."
            )
            payload = json.loads(positive["data"])
            assert payload["worker_key"] == worker_key

            # Exactly one message — no duplicate delivery on the db-scoped channel.
            extra = pos.get_message(timeout=0.2)
            assert extra is None or extra.get("type") != "message", (
                "Expected exactly one message on the db-scoped channel, got a second."
            )

            # NEGATIVE probe: the bare production channel a live worker subscribes
            # to received ZERO messages. Deterministic because the positive receipt
            # above is a happens-after barrier (pub/sub is synchronous per server).
            negative = neg.get_message(timeout=1.0)
            assert negative is None or negative.get("type") != "message", (
                f"LEAK: a fixture notify reached the production channel "
                f"{PRODUCTION_CHANNEL!r} — a live worker (db=0) would have received "
                f"it. Got: {negative!r}"
            )
        finally:
            pos.close()
            neg.close()
            client.close()


class TestLiveWorkerLogSpotCheck:
    """Demoted live-machine spot check (NOT the CI gate).

    On a machine with a live ``python -m worker`` writing ``logs/worker.log``,
    confirm a fixture enqueue produces no new ``Received session notify`` /
    ``Started session queue worker`` lines for the fixture key. Skips cleanly
    where no worker log exists, so it never fails in CI.
    """

    def test_no_fixture_notify_in_live_worker_log(self):
        log_path = Path(__file__).resolve().parents[2] / "logs" / "worker.log"
        if not log_path.exists():
            pytest.skip("no logs/worker.log — live-machine spot check not applicable")

        worker_key = f"test-notify-isolation-{uuid.uuid4().hex[:8]}"

        # Capture the byte offset BEFORE enqueue so we only scan new output.
        offset = log_path.stat().st_size
        _enqueue_fixture(worker_key)

        # Bounded poll: give an async live-worker writer a moment to flush, then
        # assert no new fixture-key lines appear.
        deadline = time.monotonic() + 3.0
        leak_line = None
        while time.monotonic() < deadline:
            with log_path.open("rb") as fh:
                fh.seek(offset)
                new = fh.read().decode("utf-8", errors="replace")
            for line in new.splitlines():
                if worker_key in line and (
                    "Received session notify" in line or "Started session queue worker" in line
                ):
                    leak_line = line
                    break
            if leak_line:
                break
            time.sleep(0.25)

        assert leak_line is None, (
            f"LEAK: the live worker logged a fixture notify for {worker_key!r}: "
            f"{leak_line!r}. The db-scoped channel isolation failed."
        )
