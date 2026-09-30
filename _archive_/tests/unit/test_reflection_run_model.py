"""Unit tests for the new ReflectionRun Popoto model (issue #1273).

ReflectionRun replaces the embedded `Reflection.run_history` list with
unbounded per-run rows, gated by a 30-day TTL.

Tests cover:
- Round-trip persistence of all fields
- ``get_or_create_for(name, timestamp)`` composite-key idempotency
- ``class Meta.ttl`` is set to 86400 * 30
- Indexed query by reflection name
"""

from __future__ import annotations

import time

import pytest


class TestReflectionRunSchema:
    """Schema-level assertions on the model class itself."""

    def test_meta_ttl_is_30_days(self):
        from models.reflection_run import ReflectionRun

        # 30 days in seconds, matching tools/analytics.py --days default.
        # Popoto exposes Meta.ttl via the metaclass-built ``_meta.ttl`` attr;
        # the source-level ``class Meta`` declaration is consumed at class
        # construction time and not retained as a nested class.
        assert ReflectionRun._meta.ttl == 86400 * 30

    def test_required_fields_present(self):
        from models.reflection_run import ReflectionRun

        # The class must declare these descriptor names.
        for field in (
            "name",
            "timestamp",
            "status",
            "duration_ms",
            "cost_usd",
            "tokens_input",
            "tokens_output",
            "error",
            "output_summary",
            "delivery_error",
        ):
            assert hasattr(ReflectionRun, field), f"missing field: {field}"


class TestReflectionRunCreate:
    """Round-trip create + read."""

    def test_create_minimal(self):
        from models.reflection_run import ReflectionRun

        run = ReflectionRun.create(
            name="t-create-min",
            timestamp=time.time(),
            status="success",
        )
        assert run.name == "t-create-min"
        assert run.status == "success"
        run.delete()

    def test_create_with_all_fields(self):
        from models.reflection_run import ReflectionRun

        ts = time.time()
        run = ReflectionRun.create(
            name="t-create-all",
            timestamp=ts,
            status="success",
            duration_ms=1234.5,
            cost_usd=0.0123,
            tokens_input=100,
            tokens_output=50,
            error=None,
            output_summary="ok",
        )
        assert run.duration_ms == pytest.approx(1234.5)
        assert run.cost_usd == pytest.approx(0.0123)
        assert run.tokens_input == 100
        assert run.tokens_output == 50
        assert run.output_summary == "ok"
        run.delete()


class TestGetOrCreateFor:
    """Composite-key idempotency for migration backfill (Q3 cycle-4 fix)."""

    def test_get_or_create_for_creates_when_absent(self):
        from models.reflection_run import ReflectionRun

        ts = 1_700_000_001.0
        # Clean any prior row from a previous test
        for r in ReflectionRun.query.filter(name="t-goc-1", timestamp=ts):
            r.delete()

        run = ReflectionRun.get_or_create_for(name="t-goc-1", timestamp=ts)
        assert run.name == "t-goc-1"
        assert run.timestamp == pytest.approx(ts)
        run.delete()

    def test_get_or_create_for_returns_existing_on_second_call(self):
        from models.reflection_run import ReflectionRun

        ts = 1_700_000_002.0
        for r in ReflectionRun.query.filter(name="t-goc-2", timestamp=ts):
            r.delete()

        first = ReflectionRun.get_or_create_for(name="t-goc-2", timestamp=ts)
        second = ReflectionRun.get_or_create_for(name="t-goc-2", timestamp=ts)
        # Same record returned (Popoto autokey identity preserved)
        assert first.name == second.name
        assert first.timestamp == second.timestamp

        # Exactly one row, not two.
        rows = list(ReflectionRun.query.filter(name="t-goc-2", timestamp=ts))
        assert len(rows) == 1

        first.delete()


class TestReflectionRunQuery:
    """Indexed query patterns the dashboard reader will use."""

    def test_filter_by_name_returns_only_matching(self):
        from models.reflection_run import ReflectionRun

        # Setup
        for r in ReflectionRun.query.filter(name="t-q-A"):
            r.delete()
        for r in ReflectionRun.query.filter(name="t-q-B"):
            r.delete()

        ReflectionRun.create(name="t-q-A", timestamp=1.0, status="success")
        ReflectionRun.create(name="t-q-A", timestamp=2.0, status="success")
        ReflectionRun.create(name="t-q-B", timestamp=3.0, status="error")

        a_rows = list(ReflectionRun.query.filter(name="t-q-A"))
        b_rows = list(ReflectionRun.query.filter(name="t-q-B"))
        assert len(a_rows) == 2
        assert len(b_rows) == 1

        # Cleanup
        for r in a_rows + b_rows:
            r.delete()
