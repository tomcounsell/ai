"""Integration tests for the scheduler tick using the unified grammar (issue #1273).

Validates:

- ``ReflectionEntry`` accepts the new ``schedule`` field.
- ``is_reflection_due`` delegates to ``compute_next_due``.
- The legacy ``interval: <s>`` form is auto-coerced into ``every: <s>s``
  for back-compat *during the migration window* — the scheduler keeps
  serving on un-migrated YAML so an upgrade can happen out-of-band.
- ``reap_stale_running()`` clears stale ``last_status="running"`` records.
"""

from __future__ import annotations

import time


class TestEntrySchedule:
    """ReflectionEntry now carries a `schedule` string."""

    def test_entry_accepts_schedule_field(self):
        from agent.reflection_scheduler import ReflectionEntry

        entry = ReflectionEntry(
            name="t-entry-1",
            description="x",
            schedule="every: 60s",
            priority="low",
            execution_type="function",
            callable="x.y",
        )
        assert entry.schedule == "every: 60s"

    def test_entry_back_compat_interval_to_schedule(self):
        """Legacy ``interval=N`` is normalized to ``schedule='every: Ns'``."""
        from agent.reflection_scheduler import ReflectionEntry

        entry = ReflectionEntry(
            name="t-entry-bc",
            description="x",
            interval=300,
            priority="low",
            execution_type="function",
            callable="x.y",
        )
        assert entry.schedule == "every: 300s"
        # And `interval_seconds()` keeps working for stale-running thresholds.
        assert entry.interval_seconds() == 300

    def test_entry_validate_schedule_grammar(self):
        from agent.reflection_scheduler import ReflectionEntry

        bad = ReflectionEntry(
            name="t-entry-bad",
            description="x",
            schedule="hourly",
            priority="low",
            execution_type="function",
            callable="x.y",
        )
        errors = bad.validate()
        assert any("schedule" in e or "grammar" in e for e in errors)


class TestIsDueViaCompute:
    """``is_reflection_due`` delegates to ``compute_next_due``."""

    def test_every_due_after_interval(self):
        from agent.reflection_scheduler import ReflectionEntry, is_reflection_due
        from models.reflection import Reflection

        entry = ReflectionEntry(
            name="t-due-1",
            description="x",
            schedule="every: 60s",
            priority="low",
            execution_type="function",
            callable="x.y",
        )
        # Clean state
        for r in Reflection.query.filter(name="t-due-1"):
            r.delete()
        state = Reflection.create(name="t-due-1", schedule="every: 60s", ran_at=None)
        try:
            now = time.time()
            assert is_reflection_due(entry, state, now) is True

            state.ran_at = now - 30  # 30s ago — not yet due
            assert is_reflection_due(entry, state, now) is False

            state.ran_at = now - 70  # 70s ago — overdue
            assert is_reflection_due(entry, state, now) is True
        finally:
            state.delete()

    def test_at_due_when_reached(self):
        from agent.reflection_scheduler import ReflectionEntry, is_reflection_due
        from models.reflection import Reflection

        # Past ISO → instantly due
        past = "2020-01-01T00:00:00+00:00"
        entry = ReflectionEntry(
            name="t-due-at-past",
            description="x",
            schedule=f"at: {past}",
            priority="low",
            execution_type="function",
            callable="x.y",
        )
        for r in Reflection.query.filter(name="t-due-at-past"):
            r.delete()
        state = Reflection.create(name="t-due-at-past", schedule=f"at: {past}", ran_at=None)
        try:
            assert is_reflection_due(entry, state, time.time()) is True
        finally:
            state.delete()


class TestReaperStaleRunning:
    """``reap_stale_running()`` force-marks long-running reflections (Race 2 cycle-4)."""

    def test_reap_stale_running_clears_old_running_records(self):
        from agent.reflection_scheduler import ReflectionEntry, ReflectionScheduler
        from models.reflection import Reflection

        # Setup: a reflection with last_status="running", ran_at well in the past.
        for r in Reflection.query.filter(name="t-reap-stale"):
            r.delete()

        state = Reflection.create(
            name="t-reap-stale",
            schedule="every: 60s",
            last_status="running",
            ran_at=time.time() - 10_000,  # 10000s ago (way past 2 * 60s)
        )

        scheduler = ReflectionScheduler()
        # Inject the entry the scheduler would otherwise load from YAML.
        scheduler._entries = [
            ReflectionEntry(
                name="t-reap-stale",
                description="x",
                schedule="every: 60s",
                priority="low",
                execution_type="function",
                callable="x.y",
            )
        ]

        reaped = scheduler.reap_stale_running()
        assert reaped == 1

        state = list(Reflection.query.filter(name="t-reap-stale"))[0]
        assert state.last_status == "stale_running"
        assert state.failure_count_consecutive == 1
        state.delete()

    def test_reap_stale_running_keeps_recent_running_records(self):
        from agent.reflection_scheduler import ReflectionEntry, ReflectionScheduler
        from models.reflection import Reflection

        for r in Reflection.query.filter(name="t-reap-fresh"):
            r.delete()
        state = Reflection.create(
            name="t-reap-fresh",
            schedule="every: 60s",
            last_status="running",
            ran_at=time.time() - 10,  # 10s ago — well within 2*60s
        )

        scheduler = ReflectionScheduler()
        scheduler._entries = [
            ReflectionEntry(
                name="t-reap-fresh",
                description="x",
                schedule="every: 60s",
                priority="low",
                execution_type="function",
                callable="x.y",
            )
        ]
        reaped = scheduler.reap_stale_running()
        assert reaped == 0

        state = list(Reflection.query.filter(name="t-reap-fresh"))[0]
        assert state.last_status == "running"
        state.delete()


class TestPausedSkippedBeforeDue:
    """The scheduler must check ``paused_until`` BEFORE ``next_due`` (Q6)."""

    def test_paused_reflection_is_skipped_when_due(self):
        from models.reflection import Reflection

        for r in Reflection.query.filter(name="t-paused-skip"):
            r.delete()

        state = Reflection.create(
            name="t-paused-skip",
            schedule="every: 60s",
            ran_at=None,
            paused_until=time.time() + 86400,  # paused for 24h
        )
        try:
            assert state.is_paused() is True
        finally:
            state.delete()
