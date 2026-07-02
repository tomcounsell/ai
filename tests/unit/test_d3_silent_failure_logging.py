"""D3 (issue #1817): formerly-silent failure paths now emit log lines.

Covers the three D3 surfaces:
- agent/sdk_client.py circuit-breaker writes: awaited directly (no longer
  fire-and-forget via asyncio.ensure_future), guarded so a breaker write
  failure logs a warning instead of masking the original exception.
- bridge/telegram_bridge.py fire-and-forget tasks: held in _background_tasks
  with a done-callback (`_log_bg_task_exception`) that logs any exception.
- agent/memory_extraction.py: former `except Exception: pass` handlers now
  log with context (still non-fatal — memory must never crash the agent).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestCircuitBreakerWritesAwaited:
    """Static guards: the sdk_client circuit-breaker record calls must stay
    awaited (fire-and-forget ensure_future was the D3 bug: if the very Redis
    failure being recorded raised, the breaker silently never tripped)."""

    def test_no_fire_and_forget_circuit_records_remain(self):
        source = (REPO_ROOT / "agent" / "sdk_client.py").read_text()
        assert not re.search(r"ensure_future\(\s*circuit\.record", source), (
            "circuit.record_failure/record_success must be awaited, not "
            "wrapped in asyncio.ensure_future (D3, issue #1817)"
        )

    def test_record_calls_are_awaited_and_guarded(self):
        source = (REPO_ROOT / "agent" / "sdk_client.py").read_text()
        assert source.count("await circuit.record_failure") == 2
        assert source.count("await circuit.record_success") == 1
        # Each awaited call is guarded so a breaker write failure is logged,
        # never masking the original exception.
        assert source.count("[circuit-breaker]") >= 3


class TestBridgeBackgroundTaskDoneCallback:
    def _run_task_with_callback(self, coro_fn):
        from bridge.telegram_bridge import _log_bg_task_exception

        async def run():
            task = asyncio.create_task(coro_fn(), name="d3-test-task")
            task.add_done_callback(_log_bg_task_exception)
            try:
                await task
            except Exception:
                pass
            # Let the done-callback fire.
            await asyncio.sleep(0)

        asyncio.run(run())

    def test_task_exception_is_logged(self, caplog):
        async def boom():
            raise RuntimeError("simulated background failure")

        with caplog.at_level(logging.WARNING, logger="bridge.telegram_bridge"):
            self._run_task_with_callback(boom)

        assert any(
            "[bg-task]" in r.getMessage() and "simulated background failure" in r.getMessage()
            for r in caplog.records
        )

    def test_successful_task_logs_nothing(self, caplog):
        async def fine():
            return 42

        with caplog.at_level(logging.WARNING, logger="bridge.telegram_bridge"):
            self._run_task_with_callback(fine)

        assert not any("[bg-task]" in r.getMessage() for r in caplog.records)

    def test_cancelled_task_logs_nothing(self, caplog):
        from bridge.telegram_bridge import _log_bg_task_exception

        async def run():
            async def sleepy():
                await asyncio.sleep(10)

            task = asyncio.create_task(sleepy(), name="d3-cancelled")
            task.add_done_callback(_log_bg_task_exception)
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)

        with caplog.at_level(logging.WARNING, logger="bridge.telegram_bridge"):
            asyncio.run(run())

        assert not any("[bg-task]" in r.getMessage() for r in caplog.records)

    def test_emoji_and_classify_tasks_are_held(self):
        """The two D3 call sites must append to _background_tasks and attach
        the done-callback (source-level guard against regressing to bare
        create_task)."""
        source = (REPO_ROOT / "bridge" / "telegram_bridge.py").read_text()
        for name in ("select_and_set_emoji_reaction", "classify_work_type"):
            create_idx = source.find(f"asyncio.create_task(\n            {name}(")
            if create_idx == -1:
                create_idx = source.find(f"asyncio.create_task({name}(")
            assert create_idx != -1, f"create_task call for {name} not found"
            window = source[create_idx : create_idx + 600]
            assert "_background_tasks.append" in window, (
                f"{name} task must be appended to _background_tasks (D3)"
            )
            assert "_log_bg_task_exception" in window, (
                f"{name} task must have the exception-logging done-callback (D3)"
            )


class TestMemoryExtractionHandlersObservable:
    def test_record_extraction_error_analytics_failure_logged(self, caplog):
        """Handler 1 (former :328): record_metric raising inside
        _record_extraction_error logs at DEBUG instead of vanishing."""
        from agent.memory_extraction import _record_extraction_error

        with caplog.at_level(logging.DEBUG, logger="agent.memory_extraction"):
            with patch(
                "analytics.collector.record_metric",
                side_effect=RuntimeError("analytics down"),
            ):
                _record_extraction_error("RuntimeError", "sess-d3", "proj-d3")

        assert any(
            "record_metric(memory.extraction.error) failed" in r.getMessage()
            for r in caplog.records
        )

    def test_persist_outcome_metadata_save_failure_logged(self, caplog):
        """Handler 5 (former :1029): a per-record save() failure in
        _persist_outcome_metadata logs at DEBUG and continues the batch."""
        from agent.memory_extraction import _persist_outcome_metadata

        bad = MagicMock()
        bad.memory_id = "mem-bad"
        bad.metadata = {}
        bad.save.side_effect = RuntimeError("redis write failed")
        good = MagicMock()
        good.memory_id = "mem-good"
        good.metadata = {}

        with caplog.at_level(logging.DEBUG, logger="agent.memory_extraction"):
            _persist_outcome_metadata(
                [bad, good],
                {"mem-bad": "acted", "mem-good": "acted"},
            )

        assert any(
            "outcome update failed for memory mem-bad" in r.getMessage() for r in caplog.records
        )
        # Non-fatal: the batch continued and the good record was saved.
        good.save.assert_called_once()

    def test_no_bare_swallow_handlers_remain_at_d3_sites(self):
        """Source-level guard: the five D3 handlers must not regress to a
        bare `except Exception:` + `pass` with no logging. (One out-of-scope
        bare handler remains elsewhere in the module by design; this counts
        that the D3-scoped sites — title generation x2, both analytics
        record_metric wrappers, and the outcome persist — all log.)"""
        source = (REPO_ROOT / "agent" / "memory_extraction.py").read_text()
        assert source.count("generate_title_async failed") == 2
        assert "record_metric(memory.extraction.error) failed" in source
        assert "record_metric(memory.extraction) failed" in source
        assert "outcome update failed for memory" in source


class TestTitleGenerationFailureLogged:
    def test_post_merge_title_failure_logged_nonfatal(self, caplog):
        """Handler 4 (former :791): generate_title_async raising inside
        extract_post_merge_learning logs at DEBUG; the save still succeeds."""
        import agent.memory_extraction as mx
        from models.memory import Memory

        mock_memory = SimpleNamespace(memory_id="mem-pm")
        learning_text = "Always gate the reaper regex on the one-shot signature first."

        async def fake_llm_call(**kwargs):
            return learning_text

        with caplog.at_level(logging.DEBUG, logger="agent.memory_extraction"):
            with (
                patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
                patch.object(mx, "_llm_call", side_effect=fake_llm_call),
                patch.object(Memory, "safe_save", return_value=mock_memory),
                patch(
                    "tools.memory_search.title_generator.generate_title_async",
                    side_effect=RuntimeError("title model down"),
                ),
            ):
                result = asyncio.run(
                    mx.extract_post_merge_learning(
                        pr_title="Fix reaper",
                        pr_body="body",
                        diff_summary="agent/session_health.py",
                        project_key="proj-d3",
                    )
                )

        assert any(
            "generate_title_async failed for memory mem-pm" in r.getMessage()
            for r in caplog.records
        )
        assert result is not None
