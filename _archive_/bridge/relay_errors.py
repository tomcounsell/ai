"""The failure contract shared by the outbox relays' send paths.

Both ``bridge/telegram_relay.py`` and ``bridge/email_relay.py`` drain a Redis
queue in a ``while True`` poll loop and report progress as "messages sent this
cycle". Returning ``0`` from that sweep is the normal, overwhelmingly common
outcome: the queue was empty. That makes ``0`` a terrible way to report that
Redis was unreachable -- the relay keeps looping, keeps logging nothing
unusual, and every health signal stays green while nothing is delivered. This
system has already paid for that once: a swallowed ``process_outbox`` exception
dropped every Telegram reply for 26 hours.

So an infrastructure failure on a send path is never folded into the return
value. It raises :class:`OutboxUnavailableError` and is reported through
:func:`report_send_path_failure`, which is the loud signal of record.

The relays' own per-message handling is unchanged and still tolerant: a
malformed payload, a rejected recipient, or a failed SMTP send is a *message*
problem, gets dead-lettered or requeued, and must not take the loop down. Only
a failure to reach the queue at all -- a ``RedisError`` from the key sweep or
from ``LPOP`` -- is an outbox outage.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class OutboxUnavailableError(Exception):
    """The relay could not reach its Redis outbox.

    Distinct from "the outbox was empty" and from "a message failed to send".
    Callers must not treat this as a completed, zero-message cycle.
    """


def report_send_path_failure(transport: str, exc: BaseException) -> None:
    """Log and Sentry-report an outbox outage on ``transport``'s send path.

    Never raises: the ERROR log is the signal of record even when Sentry is
    unreachable, and a reporting failure must not become a second outage.
    """
    logger.error(
        "SEND PATH DOWN (%s): could not reach the Redis outbox -- %s: %s",
        transport,
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:
        logger.warning("Sentry capture failed for %s send-path outage", transport, exc_info=True)
