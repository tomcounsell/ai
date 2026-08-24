"""Tests for agent/memory_extraction.py: event-loop safety.

Guards against nested ``asyncio.run()`` on the hook subprocess path.
Split out of the former ``tests/unit/test_memory_extraction.py`` monolith (#2879). The
``memory_extraction`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import pytest


class TestEventLoopSafety:
    """Verify memory_extraction never blocks the worker event loop (hotfix #1055).

    #1925: the AsyncAnthropic construction, ``async with`` httpx cleanup,
    outer ``asyncio.wait_for(hard_timeout)``, and shared semaphore slot all
    moved into ``agent.llm.run_typed`` -- ``agent/memory_extraction.py`` no
    longer touches ``anthropic.AsyncAnthropic`` directly, so there is no
    hung-socket mechanism left in *this* module to reproduce with a
    cooperative-hang stub. That mechanism is exercised directly against
    ``run_typed`` in ``tests/unit/test_llm_wrapper.py``
    (``TestHardTimeoutBound``). These tests instead verify the two things
    that are still this module's responsibility: (1) ``_llm_call``
    translates a hard-timeout ``LLMCallError`` back into ``TimeoutError`` so
    every call site's existing ``except TimeoutError:`` branch still fires,
    and (2) each call site's fail-safe default, logging, and analytics
    counter are preserved.
    """

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_extract_observations(self, caplog):
        """extract_observations_async returns [] and logs on a hard-timeout LLMCallError."""
        import logging
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_observations_async(
                "sess-test",
                "A" * 200,  # >50 chars to pass the short-circuit guard
                project_key="test-proj",
            )

        assert result == [], "extract_observations_async must return [] on timeout"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_detect_outcomes(self):
        """detect_outcomes_async (via _judge_outcomes_llm) falls back gracefully on timeout."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            # detect_outcomes_async first tries LLM; on LLM timeout, falls back
            # to bigram. We want to confirm: (a) the TimeoutError does NOT
            # propagate, (b) the counter is recorded, (c) the fallback still
            # returns something sensible.
            result = await ext.detect_outcomes_async(
                [("key1", "some thought content text goes here")],
                "some response text that mentions different topics entirely",
            )

        assert isinstance(result, dict), "detect_outcomes_async must never raise on timeout"
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_post_merge_learning(self, caplog):
        """extract_post_merge_learning returns None and logs on a hard-timeout LLMCallError."""
        import logging
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_post_merge_learning(
                "PR Title",
                "PR body content",
                "diff summary",
            )

        assert result is None, "extract_post_merge_learning must return None on timeout"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_llm_call_forwards_tightened_constants_to_run_typed(self):
        """_EXTRACTION_SDK_TIMEOUT / _EXTRACTION_HARD_TIMEOUT are read at call
        time (not captured) and forwarded to run_typed -- the double-timeout
        mechanism itself now lives in run_typed (see
        tests/unit/test_llm_wrapper.py::TestHardTimeoutBound); this test
        guards the forwarding, which is still _llm_call's responsibility."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.memory_extraction import ExtractionResult

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text="NONE"))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch.object(ext, "_EXTRACTION_SDK_TIMEOUT", 0.05),
            patch.object(ext, "_EXTRACTION_HARD_TIMEOUT", 0.1),
        ):
            await ext._llm_call(
                model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "x"}]
            )

        call_kwargs = mock_run_typed.call_args.kwargs
        assert call_kwargs["sdk_timeout"] == 0.05
        assert call_kwargs["hard_timeout"] == 0.1

    @pytest.mark.asyncio
    async def test_non_timeout_llm_call_error_caught_and_logged(self):
        """A non-timeout LLMCallError (provider error, exhausted schema retry) is
        caught by the outer except Exception and the counter is recorded.

        error_class is now "llmcallerror" rather than the raw SDK exception
        name (e.g. the old "apitimeouterror") -- an accepted analytics-only
        drift from routing every failure through the wrapper's translated
        exception type (see the plan's Rabbit Holes: per-site counters need
        not survive byte-for-byte)."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated provider error"))

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_observations_async(
                "sess-api-timeout",
                "A" * 200,
                project_key="test-proj",
            )

        assert result == [], "a provider error must not crash extract_observations_async"
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "llmcallerror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=llmcallerror (got {recorded})"

    def test_no_direct_anthropic_client_grep_canary(self):
        """Regression canary: no direct anthropic.Anthropic( or
        anthropic.AsyncAnthropic( construction in memory_extraction -- both
        the sync client (hotfix #1055) and, since #1925, the async client
        construction itself now live exclusively in agent.llm.run_typed."""
        import subprocess

        for pattern, label in (
            ("anthropic\\.Anthropic(", "sync anthropic.Anthropic("),
            ("anthropic\\.AsyncAnthropic(", "direct anthropic.AsyncAnthropic("),
        ):
            result = subprocess.run(
                ["grep", "-n", pattern, "agent/memory_extraction.py"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, (
                f"No {label} calls allowed in agent/memory_extraction.py — "
                "route through agent.llm.run_typed via _llm_call (see #1925). "
                f"Offending lines:\n{result.stdout}"
            )


def test_extract_post_merge_learning_runs_inside_asyncio_run(monkeypatch):
    """Guards the .claude hook subprocess call path (hotfix #1055, nit 3).

    Hook at .claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()
    calls asyncio.run(extract_post_merge_learning(...)) inside a short-lived
    subprocess. Routing the LLM call through agent.llm.run_typed (#1925)
    must NOT introduce a nested ``asyncio.run()`` (which raises RuntimeError:
    This event loop is already running). This test runs the function via
    asyncio.run with run_typed mocked and asserts no such error is raised.

    See docs/plans/agent_wiki.md:157 for the regression class this guards.
    """
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.memory_extraction import ExtractionResult, extract_post_merge_learning

    json_response = json.dumps(
        {
            "observation": "Use dependency injection for testability in hooks",
            "category": "pattern",
            "tags": ["testing", "hooks"],
            "file_paths": ["hooks/example.py"],
        }
    )

    # Also mock Memory.safe_save so we don't touch Redis from a subprocess-like test
    mock_memory_module = MagicMock()
    mock_memory_module.safe_save.return_value = MagicMock(memory_id="mock-mem-id")

    with (
        patch(
            "agent.memory_extraction.run_typed",
            AsyncMock(return_value=ExtractionResult(text=json_response)),
        ),
        patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        patch("models.memory.Memory", mock_memory_module),
        patch("models.memory.SOURCE_AGENT", "agent"),
    ):
        # asyncio.run is the entry point used by .claude/hooks/hook_utils/memory_bridge.py.
        # If run_typed's internal event-loop usage nested inside asyncio.run, this
        # would raise "RuntimeError: This event loop is already running".
        result = asyncio.run(
            extract_post_merge_learning(
                "PR title",
                "PR body content longer than twenty chars",
                "files_changed.py",
            )
        )

    # Result may be a dict (memory saved) or None — the critical assertion is that
    # asyncio.run did not raise. Accept either outcome.
    assert result is None or isinstance(result, dict)
