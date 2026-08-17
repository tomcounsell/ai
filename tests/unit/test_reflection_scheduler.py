"""Tests for the unified reflection scheduler (agent/reflection_scheduler.py).

Tests cover:
- Registry loading and validation
- Schedule evaluation logic
- Skip-if-running guard
- Reflection model state tracking
- Scheduler tick behavior
- Status formatting
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import agent.reflection_scheduler as reflection_scheduler
from agent.reflection_schedule import parse_every_duration
from agent.reflection_scheduler import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_FUNCTION_TIMEOUT,
    REFLECTION_STARTUP_MAX_CONCURRENT,
    ReflectionEntry,
    ReflectionScheduler,
    _get_memory_rss,
    _owning_checkout_root,
    _resolve_callable,
    _resolve_registry_path,
    execute_function_reflection,
    is_reflection_due,
    is_reflection_running,
    load_registry,
    run_reflection,
)


def _registry_path() -> Path:
    """Resolve the live reflections registry the way the scheduler does.

    The in-repo ``config/reflections.yaml`` is only present after install
    (install_worker.sh copies it from the iCloud-synced vault). Tests must use
    the same vault-first resolver the scheduler uses so they read the real file
    regardless of where it currently lives.
    """
    return _resolve_registry_path()


def _entry_interval_seconds(entry: dict) -> int:
    """Parse an entry's schedule into seconds.

    The registry schema declares schedules as ``every: 300s`` (unified grammar),
    not a bare ``interval: 300`` integer. This parses the ``every`` duration
    string into an integer number of seconds.
    """
    return parse_every_duration(str(entry["every"]).strip())


# The four schedule shapes the loader's normalizer accepts
# (agent/reflection_scheduler.py:266-283), in its own precedence order.
SCHEDULE_KEYS = ("schedule", "every", "cron", "at")


def required_field_violations(entry: dict) -> list[str]:
    """Return the registry-contract violations for one raw entry dict.

    Extracted as a module-level predicate so the contract can be exercised
    against synthetic entries without editing the live, gitignored registry.
    An empty list means the entry satisfies the contract.

    The schedule rule is **exactly one of** ``schedule`` / ``every`` / ``cron`` /
    ``at``. Rejecting zero keys matches the loader, which cannot schedule such an
    entry at all. Rejecting two-or-more is **stricter than the loader by policy**:
    the normalizer at ``agent/reflection_scheduler.py:266-283`` resolves a
    multi-key entry deterministically by precedence (schedule > every > cron > at)
    rather than refusing it, so a multi-key entry would load with one of its
    declarations silently discarded. This lint exists to stop that from reaching
    the registry. If a future loader change formalizes multi-key support, that is
    an intended divergence to re-decide here, not a bug in this test.
    """
    violations: list[str] = []
    name = entry.get("name")
    if "name" not in entry:
        violations.append(f"Entry missing name: {entry}")

    declared = [key for key in SCHEDULE_KEYS if key in entry]
    if not declared:
        violations.append(
            f"Entry {name} declares no schedule; exactly one of {list(SCHEDULE_KEYS)!r} "
            f"is required (agent/reflection_scheduler.py:266-283)."
        )
    elif len(declared) > 1:
        violations.append(
            f"Entry {name} declares {declared!r}; the loader "
            f"(agent/reflection_scheduler.py:266-283) would silently pick by precedence "
            f"schedule>every>cron>at. Declare exactly one."
        )

    if "cron_tz" in entry and "cron" not in entry:
        violations.append(
            f"Entry {name} declares cron_tz without cron; a timezone is meaningless "
            f"for a non-cron schedule."
        )

    for required in ("priority", "execution_type"):
        if required not in entry:
            violations.append(f"Entry {name} missing {required}")

    return violations


# === Registry Loading Tests ===


class TestRegistryLoading:
    """Tests for loading and validating config/reflections.yaml."""

    def test_load_registry_from_project(self):
        """Registry file exists and parses valid entries (some may be disabled)."""
        import yaml

        registry_path = _registry_path()
        assert registry_path.exists(), "Registry file should exist"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        all_names = [r["name"] for r in data["reflections"]]
        # session-liveness-check was intentionally removed (issue #2439,
        # spike-3): out-of-process actuation for it is unsafe by design.
        assert "circuit-health-gate" in all_names

    def test_load_registry_returns_only_enabled(self):
        """load_registry() filters out disabled entries."""
        entries = load_registry()
        assert entries, (
            "load_registry() returned no entries — the loop below would assert nothing. "
            "This means registry resolution broke (see _resolve_registry_path's "
            "exhausted-candidates error), not that every entry is disabled."
        )
        for entry in entries:
            assert entry.enabled, f"Disabled entry '{entry.name}' should not be returned"

    def test_load_registry_parses_pm_briefings(self):
        """The pm-briefings entry (issue #1197) parses with the expected fields."""
        import yaml

        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        entries = {r["name"]: r for r in data["reflections"]}
        assert "pm-briefings" in entries, (
            "pm-briefings entry missing from the reflections registry -- "
            "the feature is dead code without it (Blocker 1 from PR #1237 review)"
        )
        entry = entries["pm-briefings"]
        # Schema declares schedules as `every: 300s`, parsed to 300 seconds.
        assert _entry_interval_seconds(entry) == 300
        assert entry["timeout"] == 1500
        assert entry["execution_type"] == "function"
        assert entry["callable"] == "reflections.pm_briefings.run"

    def test_load_registry_validates_entries(self):
        """Invalid entries are skipped with warnings."""
        tmp = Path("/tmp/test_reflections_invalid.yaml")
        tmp.write_text(
            yaml.dump(
                {
                    "reflections": [
                        {
                            "name": "valid",
                            "interval": 300,
                            "priority": "high",
                            "execution_type": "function",
                            "callable": "some.func",
                        },
                        {
                            "name": "",
                            "interval": 300,
                            "priority": "high",
                            "execution_type": "function",
                            "callable": "some.func",
                        },
                        {
                            "name": "bad-priority",
                            "interval": 300,
                            "priority": "invalid",
                            "execution_type": "function",
                            "callable": "some.func",
                        },
                        {
                            "name": "missing-callable",
                            "interval": 300,
                            "priority": "low",
                            "execution_type": "function",
                        },
                    ]
                }
            )
        )
        entries = load_registry(tmp)
        assert len(entries) == 1
        assert entries[0].name == "valid"
        tmp.unlink()

    def test_load_registry_handles_missing_file(self):
        """Missing registry file returns empty list."""
        entries = load_registry(Path("/tmp/nonexistent_reflections.yaml"))
        assert entries == []

    def test_load_registry_handles_empty_file(self):
        """Empty registry file returns empty list."""
        tmp = Path("/tmp/test_reflections_empty.yaml")
        tmp.write_text("")
        entries = load_registry(tmp)
        assert entries == []
        tmp.unlink()

    def test_load_registry_skips_disabled(self):
        """Disabled entries are not included."""
        tmp = Path("/tmp/test_reflections_disabled.yaml")
        tmp.write_text(
            yaml.dump(
                {
                    "reflections": [
                        {
                            "name": "active",
                            "interval": 300,
                            "priority": "low",
                            "execution_type": "function",
                            "callable": "some.func",
                            "enabled": True,
                        },
                        {
                            "name": "disabled",
                            "interval": 300,
                            "priority": "low",
                            "execution_type": "function",
                            "callable": "some.func",
                            "enabled": False,
                        },
                    ]
                }
            )
        )
        entries = load_registry(tmp)
        assert len(entries) == 1
        assert entries[0].name == "active"
        tmp.unlink()


# === ReflectionEntry Validation Tests ===


class TestReflectionEntry:
    """Tests for ReflectionEntry validation."""

    def test_valid_function_entry(self):
        entry = ReflectionEntry(
            name="test",
            description="Test reflection",
            interval=300,
            priority="high",
            execution_type="function",
            callable="some.module.func",
        )
        assert entry.validate() == []

    def test_valid_agent_entry(self):
        entry = ReflectionEntry(
            name="test",
            description="Test reflection",
            interval=300,
            priority="low",
            execution_type="agent",
            command="python scripts/something.py",
        )
        assert entry.validate() == []

    def test_invalid_missing_name(self):
        entry = ReflectionEntry(
            name="",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        errors = entry.validate()
        assert any("name" in e for e in errors)

    def test_invalid_negative_interval(self):
        # Negative legacy interval cannot be normalized to a positive every:Ns
        # schedule, so the entry now fails the unified ``schedule`` requirement.
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=-1,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        errors = entry.validate()
        assert any(("schedule" in e) or ("interval" in e) for e in errors)

    def test_invalid_priority(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="mega-high",
            execution_type="function",
            callable="some.func",
        )
        errors = entry.validate()
        assert any("priority" in e for e in errors)

    def test_function_without_callable(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
        )
        errors = entry.validate()
        assert any("callable" in e for e in errors)

    def test_agent_without_command(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="agent",
        )
        errors = entry.validate()
        assert any("command" in e for e in errors)


# === Schedule Evaluation Tests ===


class TestScheduleEvaluation:
    """Tests for is_reflection_due() logic."""

    def test_never_run_is_due(self):
        """A reflection that has never run should be due."""
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="f",
        )
        state = MagicMock()
        state.ran_at = None
        assert is_reflection_due(entry, state, time.time()) is True

    def test_recently_run_not_due(self):
        """A reflection that ran recently should not be due."""
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="f",
        )
        state = MagicMock()
        state.ran_at = time.time() - 100  # Ran 100s ago, interval is 300s
        assert is_reflection_due(entry, state, time.time()) is False

    def test_past_interval_is_due(self):
        """A reflection past its interval should be due."""
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="f",
        )
        state = MagicMock()
        state.ran_at = time.time() - 400  # Ran 400s ago, interval is 300s
        assert is_reflection_due(entry, state, time.time()) is True

    def test_exactly_at_interval_is_due(self):
        """A reflection exactly at its interval should be due."""
        now = time.time()
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="f",
        )
        state = MagicMock()
        state.ran_at = now - 300
        assert is_reflection_due(entry, state, now) is True

    def test_blank_record_with_recent_history_not_due(self, monkeypatch):
        """Burst-fire guard: a blank every: record (ran_at lost during an
        index-rebuild race) must NOT re-fire when ReflectionRun history shows a
        recent run. Regression for the daily-digest burst-fire bug."""
        import agent.reflection_scheduler as sched

        now = time.time()
        entry = ReflectionEntry(
            name="system-health-digest",
            description="",
            schedule="every: 86400s",  # daily
            priority="low",
            execution_type="agent",
            command="send digest",
        )
        state = MagicMock()
        state.ran_at = None  # lost during the rebuild window
        # History says it actually ran 1h ago — well within the daily interval.
        monkeypatch.setattr(sched, "_latest_run_timestamp", lambda name: now - 3600)
        assert is_reflection_due(entry, state, now) is False

    def test_blank_record_without_history_is_due(self, monkeypatch):
        """A genuinely never-run every: record (no ran_at, no history) stays due —
        the guard must not suppress first-ever runs."""
        import agent.reflection_scheduler as sched

        now = time.time()
        entry = ReflectionEntry(
            name="system-health-digest",
            description="",
            schedule="every: 86400s",
            priority="low",
            execution_type="agent",
            command="send digest",
        )
        state = MagicMock()
        state.ran_at = None
        monkeypatch.setattr(sched, "_latest_run_timestamp", lambda name: None)
        assert is_reflection_due(entry, state, now) is True


# === Skip-if-Running Tests ===


class TestSkipIfRunning:
    """Tests for the skip-if-running guard."""

    def test_running_state_is_running(self):
        state = MagicMock()
        state.last_status = "running"
        assert is_reflection_running(state) is True

    def test_success_state_not_running(self):
        state = MagicMock()
        state.last_status = "success"
        assert is_reflection_running(state) is False

    def test_error_state_not_running(self):
        state = MagicMock()
        state.last_status = "error"
        assert is_reflection_running(state) is False

    def test_pending_state_not_running(self):
        state = MagicMock()
        state.last_status = "pending"
        assert is_reflection_running(state) is False


# === Reflection Model Tests ===


class TestReflectionModel:
    """Tests for the Reflection Popoto model."""

    def test_model_import(self):
        """Reflection model is importable."""
        from models.reflection import Reflection

        assert Reflection is not None

    def test_model_fields(self):
        """Reflection model has expected fields."""
        from models.reflection import Reflection

        field_names = [f for f in dir(Reflection) if not f.startswith("_")]
        assert "name" in field_names
        assert "ran_at" in field_names
        assert "run_count" in field_names
        assert "last_status" in field_names
        assert "last_error" in field_names


# === Scheduler Tests ===


class TestReflectionScheduler:
    """Tests for the ReflectionScheduler class."""

    def test_scheduler_loads_registry(self):
        """Scheduler loads registry on load() — returns only enabled entries."""
        scheduler = ReflectionScheduler()
        scheduler.load()
        # All reflections may be disabled; just verify load() doesn't crash
        # and _entries is a list
        assert isinstance(scheduler._entries, list)

    def test_scheduler_format_status_empty(self):
        """Format status with no entries."""
        scheduler = ReflectionScheduler(registry_path=Path("/tmp/nonexistent.yaml"))
        scheduler.load()
        result = scheduler.format_status()
        assert "No reflections" in result

    def test_scheduler_format_status_with_entries(self):
        """Format status shows reflection info."""
        scheduler = ReflectionScheduler()
        scheduler.load()
        # Inject a synthetic entry since all real ones may be disabled
        scheduler._entries = [
            ReflectionEntry(
                name="session-liveness-check",
                description="Test entry",
                interval=300,
                priority="high",
                execution_type="function",
                callable="some.func",
                enabled=True,
            )
        ]
        # Mock the Reflection.get_or_create to avoid Redis dependency
        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.ran_at = time.time() - 100
            mock_state.last_status = "success"
            mock_state.last_error = None
            mock_state.last_duration = 1.5
            mock_state.run_count = 5
            mock_reflection.get_or_create.return_value = mock_state

            result = scheduler.format_status()
            assert "Reflections:" in result
            assert "session-liveness-check" in result

    @pytest.mark.asyncio
    async def test_scheduler_tick_skips_not_due(self):
        """Tick skips reflections that aren't due yet."""
        scheduler = ReflectionScheduler()
        scheduler.load()

        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.ran_at = time.time()  # Just ran
            mock_state.last_status = "success"
            mock_reflection.get_or_create.return_value = mock_state

            enqueued = await scheduler.tick()
            assert enqueued == 0

    @pytest.mark.asyncio
    async def test_scheduler_tick_skips_running(self):
        """Tick skips reflections that are currently running."""
        scheduler = ReflectionScheduler()
        scheduler.load()

        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.ran_at = time.time() - 10  # Recently started
            mock_state.last_status = "running"
            mock_reflection.get_or_create.return_value = mock_state

            enqueued = await scheduler.tick()
            assert enqueued == 0

    @pytest.mark.asyncio
    async def test_skip_running_preserves_running_status(self):
        """Skipping a running reflection must NOT overwrite last_status.

        Regression test: mark_skipped() was changing last_status from 'running'
        to 'skipped', which defeated the skip-if-running guard on the next tick.
        """
        scheduler = ReflectionScheduler()
        scheduler.load()

        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.ran_at = time.time() - 10
            mock_state.last_status = "running"
            mock_reflection.get_or_create.return_value = mock_state

            await scheduler.tick()
            # mark_skipped must NOT be called - it would overwrite "running" status
            mock_state.mark_skipped.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_caps_function_dispatches_at_max_concurrent(self):
        """When more function-type reflections are due than the cap allows,
        tick() dispatches at most REFLECTION_STARTUP_MAX_CONCURRENT and defers
        the rest to the next tick.

        This prevents event-loop saturation at worker startup when ~30 reflections
        are simultaneously overdue (e.g. first boot after a long outage).
        """
        cap = REFLECTION_STARTUP_MAX_CONCURRENT
        # Build cap+2 synthetic function-type entries — all overdue
        entries = [
            ReflectionEntry(
                name=f"test-func-reflection-{i}",
                description=f"Test function reflection {i}",
                interval=300,
                priority="low",
                execution_type="function",
                callable="agent.reflection_scheduler._get_memory_rss",
                enabled=True,
            )
            for i in range(cap + 2)
        ]

        scheduler = ReflectionScheduler()
        scheduler._entries = entries

        dispatched_names: list[str] = []

        def fake_create_task(coro, *, name=None):
            # Record the task name but don't actually run the coroutine
            coro.close()  # prevent "coroutine was never awaited" warnings
            dispatched_names.append(name or "")
            task = MagicMock()
            task.add_done_callback = MagicMock()
            return task

        with (
            patch("agent.reflection_scheduler.Reflection") as mock_reflection,
            patch("agent.reflection_scheduler.asyncio.create_task", side_effect=fake_create_task),
            patch("agent.reflection_scheduler.run_reflection"),
        ):
            mock_state = MagicMock()
            mock_state.ran_at = None  # never run — always due
            mock_state.last_status = "success"
            mock_state.is_paused = MagicMock(return_value=False)
            mock_reflection.get_or_create.return_value = mock_state

            enqueued = await scheduler.tick()

        assert enqueued == cap, f"Expected exactly {cap} dispatched (cap), got {enqueued}"
        assert len(dispatched_names) == cap, (
            f"Expected {cap} asyncio.create_task calls, got {len(dispatched_names)}"
        )

    @pytest.mark.asyncio
    async def test_tick_small_batch_under_cap_unaffected(self):
        """When the number of due function reflections is under the cap,
        all of them are dispatched normally — throttle has no effect.
        """
        cap = REFLECTION_STARTUP_MAX_CONCURRENT
        count = max(1, cap - 1)  # strictly under the cap
        entries = [
            ReflectionEntry(
                name=f"test-small-func-{i}",
                description=f"Small batch reflection {i}",
                interval=300,
                priority="low",
                execution_type="function",
                callable="agent.reflection_scheduler._get_memory_rss",
                enabled=True,
            )
            for i in range(count)
        ]

        scheduler = ReflectionScheduler()
        scheduler._entries = entries

        dispatched_names: list[str] = []

        def fake_create_task(coro, *, name=None):
            coro.close()
            dispatched_names.append(name or "")
            task = MagicMock()
            task.add_done_callback = MagicMock()
            return task

        with (
            patch("agent.reflection_scheduler.Reflection") as mock_reflection,
            patch("agent.reflection_scheduler.asyncio.create_task", side_effect=fake_create_task),
            patch("agent.reflection_scheduler.run_reflection"),
        ):
            mock_state = MagicMock()
            mock_state.ran_at = None
            mock_state.last_status = "success"
            mock_state.is_paused = MagicMock(return_value=False)
            mock_reflection.get_or_create.return_value = mock_state

            enqueued = await scheduler.tick()

        assert enqueued == count, (
            f"Expected all {count} dispatched (under cap {cap}), got {enqueued}"
        )
        assert len(dispatched_names) == count


# === Registry File Integrity Tests ===


class TestRegistryIntegrity:
    """Tests that config/reflections.yaml is well-formed and complete."""

    def test_registry_yaml_valid(self):
        """Registry file is valid YAML."""
        registry_path = _registry_path()
        assert registry_path.exists(), "reflections registry must exist"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        assert "reflections" in data

    def test_all_entries_have_required_fields(self):
        """Every registry entry carries name, priority, execution_type and exactly
        one schedule key.

        The schedule rule accepts every shape the loader's normalizer accepts
        (``schedule`` / ``every`` / ``cron`` / ``at``) and additionally rejects
        multi-key entries — a lint **stricter than the loader by policy**, since
        the loader resolves those by precedence instead of refusing them. See
        ``required_field_violations`` for the full rationale.
        """
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        failures: list[str] = []
        for entry in data["reflections"]:
            failures.extend(required_field_violations(entry))
        assert not failures, "Registry entries violate the schedule contract:\n" + "\n".join(
            failures
        )

    def test_all_callables_resolve(self):
        """Every function-type entry's `callable:` dotted path must resolve.

        Guards the one-file-per-reflection refactor (#1028): the registry
        references historical dotted paths (e.g. ``reflections.maintenance.run_*``,
        ``agent.sustainability.*``) that now resolve through re-export shims to the
        relocated per-reflection modules. A typo in any shim re-export, or a moved
        module that forgot its shim, fails loudly here instead of silently halting
        a reflection in production. Covers disabled entries too — a disabled
        reflection's callable must still be importable.
        """
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        failures = []
        for entry in data["reflections"]:
            if entry.get("execution_type") != "function":
                continue
            dotted = entry.get("callable")
            assert dotted, f"function entry {entry.get('name')} missing callable"
            try:
                fn = _resolve_callable(dotted)
                assert callable(fn), f"{dotted} resolved to a non-callable"
            except Exception as exc:  # noqa: BLE001 — collect all, report together
                failures.append(f"{entry.get('name')}: {dotted} -> {exc!r}")
        assert not failures, "Unresolvable reflection callables:\n" + "\n".join(failures)

    # test_health_check_is_high_priority and test_health_check_interval_5_minutes
    # were DELETED (issue #2439): they guarded the session-liveness-check
    # reflection's priority/interval, but that reflection was intentionally
    # removed from the registry (spike-3: out-of-process actuation for it is
    # unsafe by design, per issues #2098/#2091). No successor reflection
    # inherited that exact "high priority, 5-minute interval" health-check
    # role — circuit-health-gate is high priority but runs every 60s, not
    # 300s — so there is nothing left for these tests to assert against.

    def test_no_duplicate_names(self):
        """All reflection names should be unique."""
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        names = [e["name"] for e in data["reflections"]]
        assert len(names) == len(set(names)), f"Duplicate names found: {names}"

    def test_expected_reflections_present(self):
        """All expected reflections are declared in the registry."""
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        names = {e["name"] for e in data["reflections"]}
        # session-liveness-check intentionally removed (issue #2439, spike-3).
        expected = {"agent-session-cleanup", "stale-branch-cleanup"}
        assert expected.issubset(names), f"Missing reflections: {expected - names}"


# === Timeout Field Tests ===


class TestTimeoutField:
    """Tests for the timeout field on ReflectionEntry."""

    def test_timeout_defaults_to_none(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        assert entry.timeout is None

    def test_effective_timeout_function_default(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        assert entry.effective_timeout() == DEFAULT_FUNCTION_TIMEOUT

    def test_effective_timeout_agent_default(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="agent",
            command="echo hi",
        )
        assert entry.effective_timeout() == DEFAULT_AGENT_TIMEOUT

    def test_explicit_timeout_overrides_default(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
            timeout=120,
        )
        assert entry.effective_timeout() == 120

    def test_negative_timeout_fails_validation(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
            timeout=-5,
        )
        errors = entry.validate()
        assert any("timeout" in e for e in errors)

    def test_zero_timeout_fails_validation(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
            timeout=0,
        )
        errors = entry.validate()
        assert any("timeout" in e for e in errors)

    def test_positive_timeout_passes_validation(self):
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
            timeout=600,
        )
        assert entry.validate() == []

    def test_load_registry_parses_timeout(self):
        """Timeout field is parsed from YAML."""
        tmp = Path("/tmp/test_reflections_timeout.yaml")
        tmp.write_text(
            yaml.dump(
                {
                    "reflections": [
                        {
                            "name": "with-timeout",
                            "interval": 300,
                            "priority": "low",
                            "execution_type": "function",
                            "callable": "some.func",
                            "timeout": 120,
                        },
                        {
                            "name": "without-timeout",
                            "interval": 300,
                            "priority": "low",
                            "execution_type": "function",
                            "callable": "some.func",
                        },
                    ]
                }
            )
        )
        entries = load_registry(tmp)
        assert len(entries) == 2
        assert entries[0].timeout == 120
        assert entries[1].timeout is None
        tmp.unlink()


# === Memory Instrumentation Tests ===


class TestMemoryInstrumentation:
    """Tests for psutil memory snapshots."""

    def test_get_memory_rss_returns_int(self):
        """_get_memory_rss returns an integer (bytes) when psutil is available."""
        result = _get_memory_rss()
        # psutil is in pyproject.toml so should be available
        assert result is not None
        assert isinstance(result, int)
        assert result > 0

    def test_get_memory_rss_handles_import_error(self):
        """_get_memory_rss returns None if psutil is unavailable."""
        with patch.dict("sys.modules", {"psutil": None}):
            with patch("builtins.__import__", side_effect=ImportError("no psutil")):
                result = _get_memory_rss()
                assert result is None


# === Timeout Enforcement Tests ===


class TestTimeoutEnforcement:
    """Tests for asyncio.wait_for timeout in run_reflection."""

    @pytest.mark.asyncio
    async def test_timeout_error_logged_as_error(self):
        """TimeoutError from wait_for is caught and logged as error status."""
        entry = ReflectionEntry(
            name="slow-reflection",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
            timeout=1,  # 1 second timeout
        )
        state = MagicMock()

        # Mock execute_function_reflection to be slow
        async def slow_func(e):
            await asyncio.sleep(10)

        with patch("agent.reflection_scheduler.execute_function_reflection", side_effect=slow_func):
            with patch("agent.reflection_scheduler._get_memory_rss", return_value=100_000_000):
                await run_reflection(entry, state)

        # Should have marked as completed with a timeout error
        state.mark_completed.assert_called_once()
        args, kwargs = state.mark_completed.call_args
        assert "error" in kwargs or (len(args) > 1 and "Timeout" in str(args[1]))
        # Check it was called with an error keyword
        if "error" in kwargs:
            assert "TimeoutError" in kwargs["error"] or "timeout" in kwargs["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_delta_warning_logged(self):
        """Memory delta > 100MB triggers a warning log."""
        entry = ReflectionEntry(
            name="memory-hog",
            description="",
            interval=300,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        state = MagicMock()

        # Simulate 200MB memory increase
        mem_before = 100 * 1024 * 1024  # 100MB
        mem_after = 350 * 1024 * 1024  # 350MB (delta = 250MB > 100MB threshold)

        with patch("agent.reflection_scheduler.execute_function_reflection", return_value=None):
            with patch(
                "agent.reflection_scheduler._get_memory_rss", side_effect=[mem_before, mem_after]
            ):
                with patch("agent.reflection_scheduler.logger") as mock_logger:
                    await run_reflection(entry, state)
                    # Check that warning was logged about high memory delta
                    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
                    assert any("HIGH MEMORY DELTA" in str(c) for c in warning_calls)


# === Typed-error fallback tests (#1158) ===


class TestEnqueueAgentReflectionTypedErrors:
    """Covers plan #1158 Failure Path: when resolve_project_key raises a typed
    error, _enqueue_agent_reflection must fall back to PROJECT_KEY env var and
    log a warning — not crash, not silently coerce.
    """

    @pytest.mark.asyncio
    async def test_project_key_resolution_error_falls_back_to_env(self, monkeypatch):
        """ProjectKeyResolutionError → logs warning, uses PROJECT_KEY env var."""
        from agent.reflection_scheduler import _enqueue_agent_reflection
        from tools.valor_session import ProjectKeyResolutionError

        entry = ReflectionEntry(
            name="agent-typed-err-test",
            description="Test reflection typed-error fallback",
            interval=3600,
            priority="low",
            execution_type="agent",
            command="Test agent reflection",
        )

        monkeypatch.setenv("PROJECT_KEY", "override-from-env")
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        with (
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectKeyResolutionError(
                    cwd="/tmp/unknown", available_keys=["valor", "ai"]
                ),
            ),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("agent.reflection_scheduler.logger") as mock_logger,
        ):
            await _enqueue_agent_reflection(entry)

        # Warning fired with the error message.
        warnings_logged = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("could not resolve project_key" in w for w in warnings_logged)
        # Enqueue used the env var fallback.
        assert captured["project_key"] == "override-from-env"

    @pytest.mark.asyncio
    async def test_projects_config_unavailable_error_falls_back_to_env(self, monkeypatch):
        """ProjectsConfigUnavailableError → logs warning, uses PROJECT_KEY env var."""
        from agent.reflection_scheduler import _enqueue_agent_reflection
        from tools.valor_session import ProjectsConfigUnavailableError

        entry = ReflectionEntry(
            name="agent-config-err-test",
            description="Test reflection typed-error fallback (config unavailable)",
            interval=3600,
            priority="low",
            execution_type="agent",
            command="Test agent reflection",
        )

        monkeypatch.setenv("PROJECT_KEY", "env-fallback-key")
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        with (
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectsConfigUnavailableError(
                    "could not load projects.json: permission denied"
                ),
            ),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("agent.reflection_scheduler.logger") as mock_logger,
        ):
            await _enqueue_agent_reflection(entry)

        warnings_logged = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("could not resolve project_key" in w for w in warnings_logged)
        assert captured["project_key"] == "env-fallback-key"


class TestExecuteFunctionReflectionParams:
    """Verify that params in ReflectionEntry are threaded through to the callable.

    This covers the dead-config fix: the `params:` block in reflections.yaml was
    parsed but never forwarded because ReflectionEntry had no params field and
    execute_function_reflection always called func() with no args.
    """

    def _make_entry(self, callable_path: str, params: dict | None = None) -> ReflectionEntry:
        return ReflectionEntry(
            name="test-reflection",
            description="test",
            priority="low",
            execution_type="function",
            schedule="every: 3600s",
            callable=callable_path,
            params=params or {},
        )

    def test_params_forwarded_to_callable_that_accepts_params(self):
        """Params are passed as kwargs when the callable declares `params`."""
        received: dict = {}

        def fake_func(params: dict | None = None) -> None:
            received["params"] = params

        entry = self._make_entry(
            "some.module.fake_func", params={"stall_advisory_telegram_enabled": True}
        )

        with patch("agent.reflection_scheduler._resolve_callable", return_value=fake_func):
            asyncio.run(execute_function_reflection(entry))

        assert received["params"] == {"stall_advisory_telegram_enabled": True}

    def test_zero_arg_callable_receives_no_params(self):
        """Zero-arg callables continue to be called without arguments (backward compat)."""
        call_count = {"n": 0}

        def zero_arg_func() -> None:
            call_count["n"] += 1

        entry = self._make_entry("some.module.zero_arg_func", params={"ignored": True})

        with patch("agent.reflection_scheduler._resolve_callable", return_value=zero_arg_func):
            asyncio.run(execute_function_reflection(entry))

        assert call_count["n"] == 1

    def test_params_field_default_is_empty_dict(self):
        """ReflectionEntry.params defaults to an empty dict when not supplied."""
        entry = ReflectionEntry(
            name="no-params",
            description="test",
            priority="low",
            execution_type="function",
            schedule="every: 3600s",
            callable="some.module.func",
        )
        assert entry.params == {}

    def test_load_registry_populates_params_from_yaml(self, tmp_path):
        """load_registry threads `params:` from YAML into ReflectionEntry.params."""
        yaml_content = """
reflections:
  - name: stall-advisory
    description: test
    priority: low
    execution_type: function
    every: 3600s
    callable: reflections.stall_advisory.run_stall_advisory
    enabled: true
    params:
      stall_advisory_telegram_enabled: true
"""
        registry_file = tmp_path / "reflections.yaml"
        registry_file.write_text(yaml_content)

        entries = load_registry(path=registry_file)

        assert len(entries) == 1
        assert entries[0].params == {"stall_advisory_telegram_enabled": True}


# === Required-fields Predicate Tests ===


class TestRequiredFieldsPredicate:
    """The schedule contract, exercised against synthetic entries.

    These assert against ``required_field_violations`` rather than the live
    registry: the registry has no invalid entry and must never gain one just to
    give a test something to fail on.
    """

    def test_entry_with_no_schedule_key_is_rejected(self):
        entry = {"name": "no-schedule", "priority": "low", "execution_type": "function"}
        violations = required_field_violations(entry)
        assert violations, "an entry with no schedule key must be rejected"
        assert "declares no schedule" in violations[0]

    def test_entry_with_every_and_cron_is_rejected_as_ambiguous(self):
        entry = {
            "name": "ambiguous",
            "priority": "low",
            "execution_type": "function",
            "every": "300s",
            "cron": "0 9 * * *",
        }
        violations = required_field_violations(entry)
        assert violations, "a multi-key entry must be rejected"
        # The message must name the loader's precedence so a reader learns what
        # would otherwise happen silently.
        assert "schedule>every>cron>at" in violations[0]

    def test_cron_only_entry_with_cron_tz_is_accepted(self):
        entry = {
            "name": "sdlc-upvote-pickup",
            "priority": "normal",
            "execution_type": "agent",
            "cron": "0 9 * * *",
            "cron_tz": "Asia/Bangkok",
        }
        assert required_field_violations(entry) == []

    def test_cron_tz_without_cron_is_rejected(self):
        entry = {
            "name": "stray-tz",
            "priority": "low",
            "execution_type": "function",
            "every": "300s",
            "cron_tz": "Asia/Bangkok",
        }
        violations = required_field_violations(entry)
        assert any("cron_tz without cron" in v for v in violations)


# === Registry Path Resolution Tests ===


@pytest.fixture(autouse=True)
def _clear_exhausted_warned():
    """Isolate the module-level exhausted-candidates dedup set.

    ``_exhausted_warned`` is process-global, so without this the dedup test is
    order-dependent: an earlier test that exhausts the same candidate set primes
    it and the dedup test sees zero caplog records (or passes vacuously in the
    reverse order). Cleared on entry *and* exit so this module neither inherits
    nor leaks the state.
    """
    reflection_scheduler._exhausted_warned.clear()
    yield
    reflection_scheduler._exhausted_warned.clear()


def _fake_module_file(root: Path) -> str:
    """The path ``agent/reflection_scheduler.py`` would have inside ``root``.

    Both ``_owning_checkout_root()`` and ``_resolve_registry_path()`` derive the
    repo root as ``Path(__file__).parent.parent``, so patching the module's
    ``__file__`` to this relocates both at once.
    """
    return str(root / "agent" / "reflection_scheduler.py")


def _synthesize_worktree(tmp_path: Path, *, commondir: bool = True) -> tuple[Path, Path]:
    """Build a linked-worktree layout under ``tmp_path``.

    Returns ``(primary_root, worktree_root)``. Only the git metadata is created —
    no ``config/reflections.yaml`` — because the locator performs no registry
    existence check of its own.
    """
    primary = tmp_path / "primary"
    admin = primary / ".git" / "worktrees" / "lane"
    admin.mkdir(parents=True)
    if commondir:
        (admin / "commondir").write_text("../..\n")
    worktree = tmp_path / "lane"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {admin}\n")
    return primary, worktree


class TestOwningCheckoutRoot:
    """Unit tests for the pure git-metadata locator."""

    def test_worktree_layout_returns_checkout_root_not_git_dir(self, tmp_path, monkeypatch):
        """The locator returns the checkout *root* — the directory containing
        ``.git`` — not the ``.git`` directory itself.

        Anti-regression for the two branches diverging on ``.parent``: applying a
        uniform ``.parent`` lands one directory above the checkout, whose
        ``config/reflections.yaml`` does not exist, so the resolver would fall
        through silently instead of finding the registry.
        """
        primary, worktree = _synthesize_worktree(tmp_path)
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree))

        root = _owning_checkout_root()

        assert root == primary
        assert (root / ".git").exists(), "locator must return the root that contains .git"

    def test_worktree_without_commondir_returns_checkout_root(self, tmp_path, monkeypatch):
        """With no ``commondir`` file the locator falls back to ``parents[2]``,
        which already *is* the checkout root for git's fixed layout."""
        primary, worktree = _synthesize_worktree(tmp_path, commondir=False)
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree))

        assert _owning_checkout_root() == primary

    def test_primary_checkout_returns_none(self, tmp_path, monkeypatch):
        """In the primary checkout ``.git`` is a directory: no owning checkout."""
        primary = tmp_path / "primary"
        (primary / ".git").mkdir(parents=True)
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(primary))

        assert _owning_checkout_root() is None

    def test_malformed_git_file_returns_none(self, tmp_path, monkeypatch):
        """Garbage with no ``gitdir:`` line degrades to None, never raises."""
        root = tmp_path / "weird"
        root.mkdir()
        (root / ".git").write_text("this is not a gitdir pointer\nnor is this\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _owning_checkout_root() is None

    def test_empty_git_file_returns_none(self, tmp_path, monkeypatch):
        root = tmp_path / "empty"
        root.mkdir()
        (root / ".git").write_text("")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _owning_checkout_root() is None

    def test_short_gitdir_returns_none_instead_of_index_error(self, tmp_path, monkeypatch):
        """``gitdir: /a`` has fewer than three parents.

        The explicit ``len(parents) > 2`` guard is what keeps this from raising
        IndexError into the broad except, where it would be indistinguishable
        from a genuinely unfamiliar layout.
        """
        root = tmp_path / "short"
        root.mkdir()
        (root / ".git").write_text("gitdir: /a\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _owning_checkout_root() is None

    def test_relative_gitdir_returns_none(self, tmp_path, monkeypatch):
        """``git worktree add --relative-paths`` writes a relative link.

        Resolving it would anchor to the *process CWD* rather than the worktree
        root and yield a silently wrong checkout, so the ``is_absolute()`` guard
        routes it down the unfamiliar-layout path instead.
        """
        root = tmp_path / "relative"
        root.mkdir()
        (root / ".git").write_text("gitdir: ../.git/worktrees/x\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _owning_checkout_root() is None

    def test_dangling_gitdir_still_returns_a_root(self, tmp_path, monkeypatch):
        """A ``gitdir:`` pointing at a nonexistent directory still yields a root.

        The locator does no ``exists()`` check by design — the resolver owns that,
        which is what keeps the fourth candidate nameable in the diagnostic. Paired
        with the resolver-side half in
        ``test_dangling_gitdir_names_absent_candidate_in_exhausted_message``.
        """
        root = tmp_path / "dangling"
        root.mkdir()
        missing = tmp_path / "gone" / ".git" / "worktrees" / "lane"
        (root / ".git").write_text(f"gitdir: {missing}\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _owning_checkout_root() == tmp_path / "gone"


class TestResolverFourthCandidate:
    """The resolver's owning-checkout level and its exhausted-candidates log."""

    @pytest.fixture(autouse=True)
    def _isolated_env(self, monkeypatch):
        """Force the resolver past the env-var and vault levels.

        VALOR_LAUNCHD=1 is the real nightly environment and deterministically
        skips the ~/Desktop vault, so these tests measure levels 3 and 4 on any
        machine regardless of whether a vault copy exists.
        """
        monkeypatch.delenv("REFLECTIONS_YAML", raising=False)
        monkeypatch.setenv("VALOR_LAUNCHD", "1")

    def test_worktree_hit_returns_owning_checkout_copy(self, tmp_path, monkeypatch):
        primary, worktree = _synthesize_worktree(tmp_path)
        (primary / "config").mkdir()
        registry = primary / "config" / "reflections.yaml"
        registry.write_text("reflections: []\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree))

        assert _resolve_registry_path() == registry

    def test_malformed_git_file_falls_back_to_local_path_without_raising(
        self, tmp_path, monkeypatch
    ):
        """The locator's broad ``except`` must show up as a fallback, not a crash."""
        root = tmp_path / "weird"
        root.mkdir()
        (root / ".git").write_text("not a gitdir pointer at all\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        assert _resolve_registry_path() == root / "config" / "reflections.yaml"

    def test_exhausted_message_names_all_four_candidates(self, tmp_path, monkeypatch, caplog):
        """An operator must be able to tell 'nothing anywhere' from 'wrong path'."""
        primary, worktree = _synthesize_worktree(tmp_path)
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree))

        with caplog.at_level(logging.ERROR, logger="agent.reflection_scheduler"):
            resolved = _resolve_registry_path()

        assert resolved == worktree / "config" / "reflections.yaml"
        records = [r for r in caplog.records if "every candidate exhausted" in r.getMessage()]
        assert len(records) == 1
        message = records[0].getMessage()
        assert "REFLECTIONS_YAML=<unset>" in message
        assert "<vault skipped: VALOR_LAUNCHD>" in message
        assert str(worktree / "config" / "reflections.yaml") in message
        assert str(primary / "config" / "reflections.yaml") in message

    def test_dangling_gitdir_names_absent_candidate_in_exhausted_message(
        self, tmp_path, monkeypatch, caplog
    ):
        """The resolver half of the locator/resolver division of labour.

        The locator hands back a root for a dangling ``gitdir:``; the resolver is
        what discovers the registry is absent there, falls back, and still names
        that concrete path.
        """
        root = tmp_path / "dangling"
        root.mkdir()
        missing_admin = tmp_path / "gone" / ".git" / "worktrees" / "lane"
        (root / ".git").write_text(f"gitdir: {missing_admin}\n")
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(root))

        with caplog.at_level(logging.ERROR, logger="agent.reflection_scheduler"):
            resolved = _resolve_registry_path()

        assert resolved == root / "config" / "reflections.yaml"
        message = next(
            r.getMessage() for r in caplog.records if "every candidate exhausted" in r.getMessage()
        )
        assert str(tmp_path / "gone" / "config" / "reflections.yaml") in message

    def test_primary_checkout_exhausted_message_marks_owner_unresolvable(
        self, tmp_path, monkeypatch, caplog
    ):
        """With ``.git`` a directory the locator returns None, and the fourth slot
        reads the literal placeholder — the contract that keeps the message at
        four slots rather than three."""
        primary = tmp_path / "primary"
        (primary / ".git").mkdir(parents=True)
        monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(primary))

        with caplog.at_level(logging.ERROR, logger="agent.reflection_scheduler"):
            resolved = _resolve_registry_path()

        assert resolved == primary / "config" / "reflections.yaml"
        message = next(
            r.getMessage() for r in caplog.records if "every candidate exhausted" in r.getMessage()
        )
        assert "<owning checkout not resolvable>" in message

    def test_exhausted_diagnostic_logs_once_per_candidate_set(self, tmp_path, monkeypatch, caplog):
        """Eight call sites re-resolving in one process must not print eight
        copies — but a *different* exhausted set must still get its own line."""
        _primary_a, worktree_a = _synthesize_worktree(tmp_path / "a")
        _primary_b, worktree_b = _synthesize_worktree(tmp_path / "b")

        with caplog.at_level(logging.ERROR, logger="agent.reflection_scheduler"):
            monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree_a))
            _resolve_registry_path()
            _resolve_registry_path()
            first_pass = [
                r for r in caplog.records if "every candidate exhausted" in r.getMessage()
            ]
            assert len(first_pass) == 1, "same candidate set must log exactly once"

            monkeypatch.setattr(reflection_scheduler, "__file__", _fake_module_file(worktree_b))
            _resolve_registry_path()

        all_records = [r for r in caplog.records if "every candidate exhausted" in r.getMessage()]
        assert len(all_records) == 2, "a different exhausted candidate set must log its own line"
