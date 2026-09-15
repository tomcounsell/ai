"""The case lease: a fencing-generation source for control-namespace writers.

Decision 1 of the lane plan. #3220 (open, no branch) owns the production
session-execution lease, ``models/redis_lease.py``, and the issue explicitly
forbids forking a second general-purpose lease. This module ships the
narrowest thing that unblocks lanes 3-6 without forking anything: a
``typing.Protocol`` matching #3220's three declared calls, plus ``CaseLease``,
an interim implementation confined to the ``improve:`` prefix by an assertion
in every method.

**The accept rule lives in the journal, not here.** This module only mints,
renews, and releases generations. ``tools/improvement_control/journal.py``
and every intent script compare ``generation >= head.highest_accepted`` and
record the winner — that rule is independent of which lease minted the
generation, and it is what actually fences a stale controller (Race 4a).

**Retirement.** The moment ``models/redis_lease.py`` exists,
``test_interim_lease_retired_when_redis_lease_exists`` (in
``tests/unit/test_improvement_control_lease.py``) fails the suite. That is
the signal for #3220's builder to: delete ``CaseLease`` from this module,
repoint ``default_lease()`` at ``models.redis_lease``, delete the retirement
test, and delete the now-vacuous "no second general lease" row from this
lane's plan Verification table. One interface, one swap point, one deletion.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tools.improvement_control.keys import assert_control_key

#: One EVAL: mint a fresh generation and take the lease, unless a live
#: (unexpired) holder already exists. The generation counter itself lives
#: forever (never expires) so it stays monotonic across every acquire.
_LUA_ACQUIRE = """
local lease_key = KEYS[1]
local gen_key = KEYS[2]
local ttl_ms = tonumber(ARGV[1])
local now = redis.call("TIME")
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local expires_ms = redis.call("HGET", lease_key, "expires_ms")
if expires_ms and tonumber(expires_ms) > now_ms then
  return nil
end
local generation = redis.call("INCR", gen_key)
redis.call("HSET", lease_key, "generation", generation, "expires_ms", now_ms + ttl_ms)
return generation
"""

#: Compare-and-extend: only the presented generation's own holder may renew.
_LUA_RENEW = """
local lease_key = KEYS[1]
local generation = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local held = redis.call("HGET", lease_key, "generation")
if not held or tonumber(held) ~= generation then
  return 0
end
local now = redis.call("TIME")
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
redis.call("HSET", lease_key, "expires_ms", now_ms + ttl_ms)
return 1
"""

#: Compare-and-delete: only the presented generation's own holder may release.
#: A newer holder's lease (a different generation already written) is left
#: untouched — this is what keeps a stale releaser from clobbering a fresh
#: acquire.
_LUA_RELEASE = """
local lease_key = KEYS[1]
local generation = tonumber(ARGV[1])
local held = redis.call("HGET", lease_key, "generation")
if not held or tonumber(held) ~= generation then
  return 0
end
redis.call("DEL", lease_key)
return 1
"""


@runtime_checkable
class LeaseProtocol(Protocol):
    """The three calls #3220's declared lease interface matches.

    ``acquire`` returns a monotonically increasing generation, or ``None``
    when the key is already held by a live (unexpired) lease. ``renew`` and
    ``release`` both compare the presented generation against the stored one
    and return ``False`` (changing nothing) when they disagree.
    """

    def acquire(self, key: str, ttl: int) -> int | None: ...

    def renew(self, key: str, generation: int) -> bool: ...

    def release(self, key: str, generation: int) -> bool: ...


class CaseLease:
    """The interim :class:`LeaseProtocol` implementation, ``improve:*`` only.

    ``ttl`` is in seconds; expiry is computed from the Redis server's own
    ``TIME`` inside the acquire script, so a slow caller never manufactures a
    lease that outlives the server's own clock. The generation counter
    (``{key}:gen``) is a bare monotonic ``INCR`` and is never deleted, so a
    lapsed lease's next acquirer still gets a strictly higher number than any
    earlier holder.
    """

    def __init__(self, redis_client, *, default_ttl_seconds: int = 90):
        self._redis = redis_client
        # LeaseProtocol.renew(key, generation) carries no ttl argument (it
        # matches #3220's declared interface verbatim), so a renewal extends
        # by this lease's own configured TTL rather than one presented per
        # call.
        self._default_ttl_seconds = int(default_ttl_seconds)

    def acquire(self, key: str, ttl: int) -> int | None:
        assert_control_key(key)
        gen = self._redis.eval(_LUA_ACQUIRE, 2, key, f"{key}:gen", int(ttl) * 1000)
        return int(gen) if gen is not None else None

    def renew(self, key: str, generation: int) -> bool:
        assert_control_key(key)
        return bool(
            self._redis.eval(_LUA_RENEW, 1, key, int(generation), self._default_ttl_seconds * 1000)
        )

    def release(self, key: str, generation: int) -> bool:
        assert_control_key(key)
        return bool(self._redis.eval(_LUA_RELEASE, 1, key, int(generation)))


def default_lease() -> LeaseProtocol:
    """The single swap point. Returns :class:`CaseLease` until #3220 lands.

    #3220's builder repoints this factory at ``models.redis_lease`` and
    deletes :class:`CaseLease`; no other call site in this package names
    ``CaseLease`` directly, so that is the entire hand-off.
    """
    from config.settings import settings
    from utils.redis_client import text_redis

    return CaseLease(text_redis(), default_ttl_seconds=settings.improvement.lease_ttl_seconds)
