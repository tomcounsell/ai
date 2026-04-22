"""Unit tests for the shared AsyncAnthropic semaphore (#1111).

Every bridge/tools/agent call site must go through ``agent.anthropic_client``
so that an asyncio.Semaphore gates concurrent API calls. This prevents
fan-out from breaching Anthropic's per-minute request limits.

These tests prove:

1. The semaphore actually serializes concurrent acquisitions to the
   configured slot count (``anthropic_slot()`` is the public entry point).
2. The configured value is read from ``settings.features.anthropic_concurrency``
   (and therefore from the ``ANTHROPIC_CONCURRENCY`` env var by pydantic-settings
   resolution).
3. The context manager releases the slot on exception, not just on success.
4. No stray ``anthropic.AsyncAnthropic(`` instantiations exist outside the
   shared module (regression canary).
"""

from __future__ import annotations

import asyncio

import pytest


class TestSemaphoreSerialization:
    """Prove the shared semaphore actually gates concurrent slots."""

    @pytest.mark.asyncio
    async def test_only_n_slots_run_concurrently(self, monkeypatch):
        """With the default configured value, only N slots run at once.

        Spawn 10 tasks, hold each slot with a short sleep, count the
        maximum observed concurrency. Must equal the configured limit
        (not 10).
        """
        # Force a deterministic small limit for the test — re-build module-level
        # semaphore against the monkeypatched settings value.
        from agent import anthropic_client

        monkeypatch.setattr(anthropic_client, "_semaphore", asyncio.Semaphore(3))

        concurrent = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal concurrent, max_concurrent
            async with anthropic_client.anthropic_slot():
                async with lock:
                    concurrent += 1
                    max_concurrent = max(max_concurrent, concurrent)
                # Hold the slot briefly so other tasks have a chance to queue
                await asyncio.sleep(0.05)
                async with lock:
                    concurrent -= 1

        await asyncio.gather(*[worker() for _ in range(10)])

        assert max_concurrent == 3, (
            f"semaphore must serialize to 3 slots, observed peak concurrency of {max_concurrent}"
        )

    @pytest.mark.asyncio
    async def test_slot_released_on_exception(self, monkeypatch):
        """The context manager must release the slot even if the body raises.

        Acquire all slots with a failing body, then prove a follow-up
        acquisition still succeeds. If the exception leaked the slot,
        the follow-up would deadlock (we cap with asyncio.wait_for).
        """
        from agent import anthropic_client

        monkeypatch.setattr(anthropic_client, "_semaphore", asyncio.Semaphore(1))

        async def failing():
            async with anthropic_client.anthropic_slot():
                raise RuntimeError("simulated API error")

        # Drain the one slot via an exception
        with pytest.raises(RuntimeError, match="simulated API error"):
            await failing()

        # Second acquisition must complete quickly — if the slot leaked,
        # wait_for would TimeoutError.
        async def second():
            async with anthropic_client.anthropic_slot() as client:
                return client

        client = await asyncio.wait_for(second(), timeout=1.0)
        assert client is not None, "post-exception acquisition must yield a client"


class TestSemaphoreConfiguration:
    """Prove the semaphore reads its value from settings.features."""

    def test_default_value_is_configured(self):
        """settings.features.anthropic_concurrency must have a sane default.

        The default of 5 is conservative enough for a solo-dev Anthropic
        account even if all five migrated call sites fan out at once.
        """
        from config.settings import settings

        # Field exists and is a positive int
        assert hasattr(settings.features, "anthropic_concurrency")
        value = settings.features.anthropic_concurrency
        assert isinstance(value, int)
        assert value >= 1, "anthropic_concurrency must be at least 1"

    def test_module_semaphore_initialized_from_settings(self):
        """The module-level _semaphore must be built from the settings value.

        Read the semaphore's internal counter via asyncio.Semaphore._value
        (private attr; only used in tests) and confirm it matches
        settings.features.anthropic_concurrency at import time.
        """
        from agent import anthropic_client
        from config.settings import settings

        # _value is the remaining slot count on a freshly-imported semaphore
        # (no tasks holding slots at module load). It equals the configured limit.
        assert anthropic_client._semaphore._value == settings.features.anthropic_concurrency


class TestSharedModuleIsTheOnlyConstructor:
    """Regression canary: every AsyncAnthropic construction must be semaphore-gated.

    All call sites in ``bridge/``, ``tools/``, and ``agent/`` that construct
    ``anthropic.AsyncAnthropic(...)`` must either:

    * go through ``agent/anthropic_client.py::anthropic_slot()`` (the common
      case — the shared module both acquires the slot and constructs the
      client), OR
    * be the module ``agent/memory_extraction.py`` (which acquires the slot
      via ``semaphore_slot()`` then constructs its own client with hotfix
      #1055 invariants: ``async with AsyncAnthropic(timeout=...)`` +
      double-timeout for httpx cleanup).

    Any other direct instantiation bypasses the shared semaphore and
    regresses #1111.
    """

    # Modules allowed to construct AsyncAnthropic directly. Each must acquire
    # the shared semaphore through ``agent.anthropic_client`` in some form.
    _ALLOWED_DIRECT_CONSTRUCTORS = frozenset(
        {
            "agent/anthropic_client.py",  # the shared module itself
            "agent/memory_extraction.py",  # hotfix #1055 invariants
        }
    )

    def test_no_unguarded_async_anthropic_instantiation(self):
        """Every ``anthropic.AsyncAnthropic(`` must be in an approved module."""
        import subprocess

        result = subprocess.run(
            [
                "grep",
                "-rn",
                "anthropic.AsyncAnthropic(",
                "--include=*.py",
                "agent/",
                "bridge/",
                "tools/",
            ],
            capture_output=True,
            text=True,
        )

        hits = [
            line
            for line in result.stdout.splitlines()
            if line and not any(mod in line for mod in self._ALLOWED_DIRECT_CONSTRUCTORS)
        ]
        assert not hits, (
            "anthropic.AsyncAnthropic( outside approved modules "
            f"{sorted(self._ALLOWED_DIRECT_CONSTRUCTORS)}; found:\n" + "\n".join(hits)
        )

    def test_memory_extraction_acquires_shared_semaphore(self):
        """``agent/memory_extraction.py`` must route through ``semaphore_slot()``.

        Even though it constructs its own ``AsyncAnthropic`` for hotfix #1055
        reasons, the shared semaphore must still gate the call so memory
        extraction counts against the global concurrency budget (#1111).
        """
        from pathlib import Path

        source = Path("agent/memory_extraction.py").read_text()
        assert "semaphore_slot" in source, (
            "agent/memory_extraction.py must import and use "
            "agent.anthropic_client.semaphore_slot to gate the #1111 semaphore "
            "around its bespoke AsyncAnthropic construction."
        )
