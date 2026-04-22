"""Shared AsyncAnthropic client with rate-limit semaphore (#1111).

Every call site across ``bridge/``, ``tools/``, and ``agent/`` goes through
this module instead of constructing its own ``anthropic.AsyncAnthropic``.
A process-wide ``asyncio.Semaphore`` gates concurrent API calls so fan-out
never breaches Anthropic's per-minute request limits.

Two entry points:

* ``anthropic_slot()`` — the ergonomic context manager. Acquires a slot,
  constructs a fresh ``AsyncAnthropic`` client, yields it, then releases
  the slot on exit. Use for the common case::

      async with anthropic_slot() as client:
          msg = await client.messages.create(...)

* ``semaphore_slot()`` — semaphore-only variant. Acquires a slot but does
  NOT construct a client. Use at sites with site-specific client
  invariants (e.g. ``agent/memory_extraction.py`` needs
  ``async with anthropic.AsyncAnthropic(timeout=...) as client:`` for
  hotfix #1055 httpx cleanup)::

      async with semaphore_slot():
          async with anthropic.AsyncAnthropic(timeout=...) as client:
              await client.messages.create(...)

Both acquire the same module-level semaphore, so mixing them is fine.

Configuration:
    ``settings.features.anthropic_concurrency`` (default 5, range 1-50).
    Override with the ``FEATURES__ANTHROPIC_CONCURRENCY`` env var.

Design notes:
    * Slot count is read once at module import time. Changing the setting
      at runtime requires a process restart (this matches the rest of
      ``FeatureSettings``, which is startup-config).
    * ``_semaphore`` is a module-level private for test monkeypatching.
"""

from __future__ import annotations

import asyncio

import anthropic

from config.settings import settings
from utils.api_keys import get_anthropic_api_key

# Module-level semaphore — slot count read once at import time from
# ``settings.features.anthropic_concurrency``. Tests monkeypatch this attr
# directly to simulate tight/loose limits.
_semaphore: asyncio.Semaphore = asyncio.Semaphore(settings.features.anthropic_concurrency)


class _AnthropicGuard:
    """Async context manager that acquires a semaphore slot then yields a client.

    On ``__aenter__``: acquire the shared semaphore, then construct and
    return a fresh ``anthropic.AsyncAnthropic`` client keyed with the
    resolved API key. On ``__aexit__``: release the slot (always, even
    on exception).

    The returned client is ephemeral — each ``anthropic_slot()`` call
    creates a new ``AsyncAnthropic`` instance so httpx connection pools
    are not shared across call sites. This matches the prior per-site
    behaviour; the only new thing is the gating semaphore.
    """

    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
        self._acquired = False

    async def __aenter__(self) -> anthropic.AsyncAnthropic:
        await _semaphore.acquire()
        self._acquired = True
        self._client = anthropic.AsyncAnthropic(api_key=get_anthropic_api_key())
        return self._client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._acquired:
            _semaphore.release()
            self._acquired = False
        # Do not swallow exceptions.
        return None


class _SemaphoreOnlyGuard:
    """Async context manager that only acquires/releases the semaphore slot.

    Used at call sites with bespoke client invariants (e.g.
    ``agent/memory_extraction.py`` which needs ``async with
    anthropic.AsyncAnthropic(timeout=...) as client:`` for hotfix #1055
    httpx cleanup). The caller constructs the client; this guard just
    gates concurrency.
    """

    def __init__(self) -> None:
        self._acquired = False

    async def __aenter__(self) -> None:
        await _semaphore.acquire()
        self._acquired = True
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._acquired:
            _semaphore.release()
            self._acquired = False
        return None


def anthropic_slot() -> _AnthropicGuard:
    """Return a fresh context manager that gates on the shared semaphore.

    Use as::

        async with anthropic_slot() as client:
            msg = await client.messages.create(...)
    """
    return _AnthropicGuard()


def semaphore_slot() -> _SemaphoreOnlyGuard:
    """Return a fresh context manager that acquires the slot without a client.

    Use when a call site needs its own ``AsyncAnthropic`` construction
    (e.g. with ``timeout=``, inside its own ``async with`` for httpx
    cleanup). The slot is released on exit so the caller can still
    create the client inside the slot's lifetime::

        async with semaphore_slot():
            async with anthropic.AsyncAnthropic(timeout=30.0) as client:
                msg = await client.messages.create(...)
    """
    return _SemaphoreOnlyGuard()
