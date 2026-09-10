"""Cross-process dev-lane lease for the Codex dev tool (plan #2001, Phase 3).

One Codex thread admits exactly one live ``exec resume`` at a time: two
overlapping resumes race on the same rollout and return misordered work
(Race 3). The MCP handler runs in the Claude PM's subprocess tree — a
different process from the worker and from any stale overlapping worker —
so an in-process lock is insufficient. This lease is a Redis ``SET NX EX``
key per session, crash-releasing via TTL: a dead holder stops holding the
moment the key ages out, with no reaper required.

Contract:

- ``acquire_dev_lease(session_id, timeout_s)`` blocks up to ``timeout_s``
  for the key, then raises :class:`DevLaneBusy`. ``timeout_s=0`` is a
  pure try-lock (used by the operator downgrade probe).
- The lease is a context manager (sync ``with`` and async ``async with``);
  release in ``finally`` deletes only our own token (compare-and-delete
  via Lua), so a TTL expiry followed by another holder's acquire can never
  delete a lease we do not own.
- Re-read of persisted thread state happens AFTER acquisition (the caller
  does that); the lease only serializes, it does not snapshot.
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)

# Crash-releasing TTL: long enough that a healthy multi-minute Codex turn
# never loses the lease mid-turn, short enough that a dead holder frees
# the lane without operator action. The handler refreshes nothing — a
# turn that outlives the TTL is itself past the turn timeout and gets
# killed, so the lease outliving any live turn is the invariant, not a
# refresh loop.
DEV_LEASE_TTL_S = 900
DEV_LEASE_ACQUIRE_POLL_S = 0.2

_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


class DevLaneBusy(RuntimeError):
    """The session's dev lane is held by another live Codex turn."""


def key_for(session_id: str) -> str:
    """Redis key holding the dev-lane lease for ``session_id``."""
    return f"codex:devlane:{session_id}"


class DevLease:
    """A held dev-lane lease. Release via ``release()`` or context exit."""

    def __init__(self, session_id: str, token: str) -> None:
        self.session_id = session_id
        self.token = token
        self._released = False

    def release(self) -> None:
        """Release the lease if still ours. Idempotent, best-effort."""
        if self._released:
            return
        self._released = True
        try:
            from utils.redis_client import text_redis

            text_redis().eval(_RELEASE_LUA, 1, key_for(self.session_id), self.token)
        except Exception as exc:  # noqa: BLE001 -- TTL is the backstop
            logger.debug("[codex-lease] release failed for %s: %s", self.session_id, exc)

    def __enter__(self) -> DevLease:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    async def __aenter__(self) -> DevLease:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.release()


def acquire_dev_lease(session_id: str, timeout_s: float = 30.0) -> DevLease:
    """Acquire the dev-lane lease for ``session_id``, blocking up to ``timeout_s``.

    Raises :class:`DevLaneBusy` when another holder keeps the key for the
    whole wait. The returned :class:`DevLease` releases in ``finally`` —
    callers must use ``with``/``async with``.
    """
    from utils.redis_client import text_redis

    client = text_redis()
    key = key_for(session_id)
    token = uuid.uuid4().hex
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if client.set(key, token, nx=True, ex=DEV_LEASE_TTL_S):
            return DevLease(session_id, token)
        if time.monotonic() >= deadline:
            raise DevLaneBusy(
                f"Session {session_id!r} dev lane is busy "
                "(another Codex turn holds the lease) — retry when idle."
            )
        time.sleep(DEV_LEASE_ACQUIRE_POLL_S)
