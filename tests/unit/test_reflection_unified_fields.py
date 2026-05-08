"""Unit tests for the new unified Reflection fields (issue #1273).

The unified Reflection schema adds:

- ``schedule`` — string in the unified grammar (every:/cron:/at:); replaces ``interval``.
- ``output_sink`` — declarative output destination (e.g. ``log_only``, ``telegram:Dev: Valor``).
- ``failure_count_consecutive`` — consecutive-error counter, dashboard-prominent.
- ``retry_policy`` — dict ``{max_retries, backoff_seconds, max_consecutive_failures_before_pause}``.
- ``paused_until`` — float; reflection is skipped while in the future.
- ``cost_usd_total`` — running total of API spend (rolled up by analytics).
- ``tokens_input_total`` / ``tokens_output_total`` — running token totals.
- ``created_by_session_id`` — set when the reflection is created via MCP;
  ``None`` for registry-loaded.
- ``auto_delete_after_run`` — True only for ``at:`` (one-shot) reflections
  (Q2 cycle-4 fix).
- ``dead_letter_escalated`` — escalation guard so a paused reflection only
  writes Memory once (Q6 cycle-4 fix).

The legacy embedded ``run_history`` list is removed; ``last_run_summary`` (a small dict)
takes its place for fast dashboard reads.
"""

from __future__ import annotations

import time

import pytest


def _create(name: str, **overrides):
    from models.reflection import Reflection

    base = dict(name=name)
    base.update(overrides)
    return Reflection.create(**base)


class TestNewFieldsExist:
    def test_all_new_fields_declared(self):
        from models.reflection import Reflection

        for field in (
            "schedule",
            "output_sink",
            "failure_count_consecutive",
            "retry_policy",
            "paused_until",
            "cost_usd_total",
            "tokens_input_total",
            "tokens_output_total",
            "created_by_session_id",
            "auto_delete_after_run",
            "dead_letter_escalated",
            "last_run_summary",
        ):
            assert hasattr(Reflection, field), f"missing field: {field}"

    def test_run_history_removed(self):
        """Legacy ``run_history`` is removed; per-run rows live in ReflectionRun."""
        from models.reflection import Reflection

        # Either the descriptor is gone, or it's a non-ListField (compat shim).
        # Plan mandates removal: assert it's truly gone.
        assert not hasattr(Reflection, "run_history") or "run_history" not in {
            f.name for f in Reflection._meta.fields
        }


class TestDefaults:
    def test_failure_count_starts_zero(self):
        r = _create("t-defaults-1", schedule="every: 60s")
        try:
            assert r.failure_count_consecutive == 0
        finally:
            r.delete()

    def test_paused_until_default(self):
        r = _create("t-defaults-2", schedule="every: 60s")
        try:
            # 0.0 (or None) — not in the future, so the reflection is not paused.
            assert (r.paused_until or 0.0) <= time.time()
        finally:
            r.delete()

    def test_dead_letter_escalated_default_false(self):
        r = _create("t-defaults-3", schedule="every: 60s")
        try:
            assert bool(r.dead_letter_escalated) is False
        finally:
            r.delete()

    def test_auto_delete_after_run_default_false(self):
        r = _create("t-defaults-4", schedule="every: 60s")
        try:
            assert bool(r.auto_delete_after_run) is False
        finally:
            r.delete()

    def test_output_sink_default_log_only(self):
        r = _create("t-defaults-5", schedule="every: 60s")
        try:
            assert r.output_sink == "log_only"
        finally:
            r.delete()

    def test_cost_totals_default_zero(self):
        r = _create("t-defaults-6", schedule="every: 60s")
        try:
            assert r.cost_usd_total == pytest.approx(0.0)
            assert r.tokens_input_total == 0
            assert r.tokens_output_total == 0
        finally:
            r.delete()


class TestRoundTrip:
    def test_save_load_round_trip_schedule_field(self):
        from models.reflection import Reflection

        r = _create("t-rt-1", schedule="cron: 0 9 * * *")
        try:
            loaded = list(Reflection.query.filter(name="t-rt-1"))
            assert loaded
            assert loaded[0].schedule == "cron: 0 9 * * *"
        finally:
            r.delete()

    def test_save_load_round_trip_output_sink(self):
        from models.reflection import Reflection

        r = _create(
            "t-rt-2",
            schedule="every: 60s",
            output_sink="telegram:Dev: Valor",
        )
        try:
            loaded = list(Reflection.query.filter(name="t-rt-2"))
            assert loaded[0].output_sink == "telegram:Dev: Valor"
        finally:
            r.delete()


class TestFailureTrackingHelpers:
    """Methods on Reflection that drive the dead-letter escalation."""

    def test_record_failure_increments_counter(self):
        r = _create("t-fail-1", schedule="every: 60s")
        try:
            r.record_failure(error="boom")
            assert r.failure_count_consecutive == 1
            r.record_failure(error="boom2")
            assert r.failure_count_consecutive == 2
        finally:
            r.delete()

    def test_record_success_resets_counter(self):
        r = _create("t-fail-2", schedule="every: 60s")
        try:
            r.record_failure(error="boom")
            r.record_failure(error="boom")
            assert r.failure_count_consecutive == 2
            r.record_success()
            assert r.failure_count_consecutive == 0
            assert bool(r.dead_letter_escalated) is False
        finally:
            r.delete()

    def test_dead_letter_escalation_threshold(self):
        """At the 5th consecutive failure, the escalation flag flips on."""
        r = _create("t-fail-3", schedule="every: 60s")
        try:
            for _ in range(4):
                r.record_failure(error="x")
            assert bool(r.dead_letter_escalated) is False
            r.record_failure(error="x")
            assert bool(r.dead_letter_escalated) is True
            assert r.paused_until > time.time()
        finally:
            r.delete()

    def test_subsequent_failures_do_not_re_escalate(self):
        r = _create("t-fail-4", schedule="every: 60s")
        try:
            for _ in range(7):
                r.record_failure(error="x")
            # Counter still increments
            assert r.failure_count_consecutive == 7
            # But escalation only flipped once
            assert bool(r.dead_letter_escalated) is True
        finally:
            r.delete()

    def test_first_success_after_escalation_resets_both(self):
        r = _create("t-fail-5", schedule="every: 60s")
        try:
            for _ in range(5):
                r.record_failure(error="x")
            assert bool(r.dead_letter_escalated) is True
            r.record_success()
            assert r.failure_count_consecutive == 0
            assert bool(r.dead_letter_escalated) is False
        finally:
            r.delete()

    def test_should_skip_when_paused(self):
        r = _create("t-fail-6", schedule="every: 60s")
        try:
            assert r.is_paused() is False
            r.paused_until = time.time() + 86400
            r.save()
            assert r.is_paused() is True
        finally:
            r.delete()


class TestUnifiedGetOrCreate:
    """``get_or_create`` keeps the schedule + output_sink fields filled in."""

    def test_get_or_create_with_schedule(self):
        from models.reflection import Reflection

        # Fresh name to avoid pre-existing rows.
        name = "t-goc-unified-A"
        for r in Reflection.query.filter(name=name):
            r.delete()

        r = Reflection.get_or_create(name=name, schedule="every: 60s")
        try:
            assert r.schedule == "every: 60s"
            assert r.output_sink == "log_only"
        finally:
            r.delete()

    def test_get_or_create_returns_existing_unchanged(self):
        from models.reflection import Reflection

        name = "t-goc-unified-B"
        for r in Reflection.query.filter(name=name):
            r.delete()

        first = Reflection.get_or_create(name=name, schedule="every: 60s")
        # Second call passing a different schedule does NOT mutate; it returns the existing.
        second = Reflection.get_or_create(name=name, schedule="every: 99s")
        try:
            assert first.name == second.name
            assert second.schedule == "every: 60s"  # unchanged
        finally:
            first.delete()
