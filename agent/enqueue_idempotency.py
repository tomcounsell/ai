"""Single-winner binding for session enqueue.

A caller that can be retried — a reflection tick that fires twice inside one
due window, a crash-retry of the same tick, the improvement control plane
re-issuing a dispatch intent — needs "enqueue this once" rather than "enqueue
this". It passes an ``idempotency_key`` to ``_push_agent_session``, and this
module binds that key to exactly one ``agent_session_id``:

* The winner of ``SET enqueue:idem:{key} {id} NX EX 86400`` creates the row
  under the id it just bound.
* Every loser reads the bound id back and returns it, having created nothing.

The id is preallocated rather than read back off a created row, because the
key must be bound *before* the row exists: otherwise two callers both find no
key, both create, and the binding arrives too late to prevent anything.

**The preallocated id must be passed to ``AgentSession`` as ``id=``, never as
``agent_session_id=``.** ``AgentSession.__init__`` pops ``agent_session_id``
and ignores it (it is the AutoKeyField's read name), so a binding passed
under that name is dropped *without raising* and the row gets a freshly
generated id — leaving the key pointing at a session that does not exist.

The id is also minted with Popoto's own generator shape and no other.
``AutoFieldMixin`` pins ``STRATEGY_LENGTHS = {"uuid4": 32, ...}`` and raises
``ModelException`` from ``__init__`` on a key that fails its length check, so
a readable id such as ``f"refl-{name}-{epoch}"`` raises rather than quietly
falling back to a generated one.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

# One day. Long enough that a crash-retry of a reflection tick still finds the
# binding, short enough that keys do not accumulate.
IDEMPOTENCY_TTL_SECONDS = 86400


def key_for(idempotency_key: str) -> str:
    """Redis key holding the binding for ``idempotency_key``."""
    return f"enqueue:idem:{idempotency_key}"


def mint_agent_session_id() -> str:
    """A key in Popoto's own AutoKeyField shape (uuid4 hex, 32 characters)."""
    return uuid.uuid4().hex


def bind(idempotency_key: str) -> tuple[str, bool]:
    """Bind ``idempotency_key`` to an ``agent_session_id``.

    Returns ``(agent_session_id, won)``. ``won`` is True for the caller that
    must go on to create the row, and False for a caller that lost the race
    and should return the bound id without creating anything.

    A caller that wins the key and then dies before the row exists leaves the
    key bound to a session nobody created; the next caller loses the race,
    finds no row, and creates it under the same preallocated id. That is why
    the id travels with the answer rather than being read off a row.
    """
    from utils.redis_client import text_redis

    client = text_redis()
    key = key_for(idempotency_key)
    candidate = mint_agent_session_id()

    if client.set(key, candidate, nx=True, ex=IDEMPOTENCY_TTL_SECONDS):
        return candidate, True

    bound = client.get(key)
    if bound:
        logger.info("Enqueue lost the idempotency race for %s; bound to %s", idempotency_key, bound)
        return str(bound), False

    # The key expired between the SET NX and the read. Take it.
    client.set(key, candidate, ex=IDEMPOTENCY_TTL_SECONDS)
    return candidate, True


def release(idempotency_key: str) -> None:
    """Drop a binding so the same key can enqueue again. Best-effort."""
    try:
        from utils.redis_client import text_redis

        text_redis().delete(key_for(idempotency_key))
    except Exception as e:  # noqa: BLE001 -- the key ages out on its own
        logger.debug("Enqueue idempotency release failed for %s: %s", idempotency_key, e)
