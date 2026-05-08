"""Unit tests for the Reflection Popoto model (models/reflection.py).

Covers the unified-reflections rebuild (issue #1273):
- New fields: schedule, output_sink, failure_count_consecutive, retry_policy,
  paused_until, cost_usd_total, tokens_input_total, tokens_output_total,
  created_by_session_id, auto_delete_after_run, dead_letter_escalated,
  last_run_summary.
- mark_completed signature: (duration, error, projects, cost_usd,
  tokens_input, tokens_output, output) writes a ReflectionRun row and
  triggers dead-letter Memory escalation on the <5 → >=5 transition.
"""

from __future__ import annotations

import time

from models.reflection import Reflection
from models.reflection_run import ReflectionRun


class TestReflectionFields:
    def test_new_field_defaults(self):
        r = Reflection.create(name=f"test-fields-{int(time.time() * 1e6)}")
        assert r.schedule == ""
        assert r.output_sink == "log_only"
        assert r.failure_count_consecutive == 0
        assert r.retry_policy == {} or r.retry_policy == {} or not r.retry_policy
        assert float(r.paused_until or 0.0) == 0.0
        assert float(r.cost_usd_total or 0.0) == 0.0
        assert int(r.tokens_input_total or 0) == 0
        assert int(r.tokens_output_total or 0) == 0
        assert r.created_by_session_id in (None, "")
        assert bool(r.auto_delete_after_run) is False
        assert bool(r.dead_letter_escalated) is False
        # last_run_summary should be a dict (or empty)
        lrs = r.last_run_summary
        assert lrs is None or isinstance(lrs, dict)


class TestReflectionMarkLifecycle:
    def _create(self, name_prefix="test-lifecycle"):
        return Reflection.create(name=f"{name_prefix}-{int(time.time() * 1e6)}")

    def test_mark_started(self):
        r = self._create()
        before = time.time()
        r.mark_started()
        assert r.last_status == "running"
        assert r.ran_at is not None
        assert float(r.ran_at) >= before

    def test_mark_completed_success_writes_run_summary(self):
        r = self._create()
        r.mark_completed(duration=1.5)
        assert r.last_status == "success"
        assert r.last_duration == 1.5
        assert r.last_error is None
        assert r.run_count == 1
        assert isinstance(r.last_run_summary, dict)
        assert r.last_run_summary["status"] == "success"
        assert r.last_run_summary["duration"] == 1.5

    def test_mark_completed_error(self):
        r = self._create()
        r.mark_completed(duration=0.3, error="boom")
        assert r.last_status == "error"
        assert r.last_error == "boom"
        assert r.run_count == 1
        assert r.last_run_summary["status"] == "error"
        assert r.last_run_summary["error"] == "boom"

    def test_mark_completed_writes_reflection_run(self):
        name = f"test-run-row-{int(time.time() * 1e6)}"
        r = Reflection.create(name=name)
        r.mark_completed(
            duration=2.0,
            cost_usd=0.05,
            tokens_input=100,
            tokens_output=50,
            output="some output",
            projects=[{"key": "p1"}],
        )
        runs = list(ReflectionRun.query.filter(name=name))
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "success"
        assert run.duration_ms == 2000
        assert float(run.cost_usd) == 0.05
        assert int(run.tokens_input) == 100
        assert int(run.tokens_output) == 50
        assert run.output_summary == "some output"

    def test_mark_completed_accumulates_cost_tokens(self):
        r = self._create()
        r.mark_completed(duration=1.0, cost_usd=0.01, tokens_input=5, tokens_output=3)
        r.mark_completed(duration=1.0, cost_usd=0.02, tokens_input=7, tokens_output=4)
        assert abs(float(r.cost_usd_total) - 0.03) < 1e-9
        assert int(r.tokens_input_total) == 12
        assert int(r.tokens_output_total) == 7

    def test_error_truncated_in_last_error(self):
        r = self._create()
        r.mark_completed(duration=0.1, error="x" * 2000)
        assert len(r.last_error) == 1000

    def test_error_truncated_in_run_summary(self):
        r = self._create()
        r.mark_completed(duration=0.1, error="x" * 1000)
        assert len(r.last_run_summary["error"]) == 500


class TestDeadLetterEscalation:
    def _create(self):
        return Reflection.create(name=f"test-dl-{int(time.time() * 1e6)}")

    def test_failure_count_increments_then_resets(self):
        r = self._create()
        r.mark_completed(duration=0.1, error="e1")
        assert r.failure_count_consecutive == 1
        r.mark_completed(duration=0.1, error="e2")
        assert r.failure_count_consecutive == 2
        r.mark_completed(duration=0.1)  # success
        assert r.failure_count_consecutive == 0
        assert r.dead_letter_escalated is False

    def test_dead_letter_memory_written_once_on_threshold_crossing(self):
        from models.memory import Memory

        # Capture memory writes
        captured: list = []
        original = Memory.create

        def spy(**kwargs):
            captured.append(kwargs)
            return original(**kwargs)

        Memory.create = classmethod(lambda cls, **kw: captured.append(kw) or original(**kw))
        try:
            r = self._create()
            for i in range(4):
                r.mark_completed(duration=0.1, error=f"err{i}")
            # 4 failures: not yet escalated
            assert r.failure_count_consecutive == 4
            assert r.dead_letter_escalated is False
            captured_at_4 = len(captured)

            # 5th failure crosses threshold → write Memory exactly once
            r.mark_completed(duration=0.1, error="err5")
            assert r.failure_count_consecutive == 5
            assert r.dead_letter_escalated is True
            after_5 = len(captured)
            new_writes = after_5 - captured_at_4
            # The threshold crossing should add at least one Memory write
            assert new_writes >= 1, "Expected Memory write on <5→>=5 transition"
            crossing_kwargs = captured[captured_at_4]
            assert crossing_kwargs.get("importance") == 7.0
            assert crossing_kwargs.get("category") == "correction"

            # 6th, 7th failures: NOT re-written
            r.mark_completed(duration=0.1, error="err6")
            r.mark_completed(duration=0.1, error="err7")
            assert len(captured) == after_5, "Memory must not re-write past threshold"

            # First success resets dead_letter_escalated to False
            r.mark_completed(duration=0.1)
            assert r.failure_count_consecutive == 0
            assert r.dead_letter_escalated is False
        finally:
            Memory.create = original

    def test_pause_extended_at_threshold(self):
        r = self._create()
        for _ in range(5):
            r.mark_completed(duration=0.1, error="boom")
        assert r.failure_count_consecutive == 5
        # paused_until should be ~24h from now
        pu = float(r.paused_until or 0.0)
        assert pu > time.time() + 80000  # ~23 hours minimum
