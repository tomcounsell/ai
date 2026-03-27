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
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agent.reflection_scheduler import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_FUNCTION_TIMEOUT,
    MEMORY_DELTA_WARNING_BYTES,
    ReflectionEntry,
    ReflectionScheduler,
    _get_memory_rss,
    is_reflection_due,
    is_reflection_running,
    load_registry,
    run_reflection,
)

# === Registry Loading Tests ===


class TestRegistryLoading:
    """Tests for loading and validating config/reflections.yaml."""

    def test_load_registry_from_project(self):
        """Registry file exists and loads with valid entries."""
        entries = load_registry()
        assert len(entries) > 0, "Registry should have at least one entry"
        names = [e.name for e in entries]
        assert "health-check" in names
        assert "daily-maintenance" in names

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
        entry = ReflectionEntry(
            name="test",
            description="",
            interval=-1,
            priority="low",
            execution_type="function",
            callable="some.func",
        )
        errors = entry.validate()
        assert any("interval" in e for e in errors)

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
        state.last_run = None
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
        state.last_run = time.time() - 100  # Ran 100s ago, interval is 300s
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
        state.last_run = time.time() - 400  # Ran 400s ago, interval is 300s
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
        state.last_run = now - 300
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
        assert "last_run" in field_names
        assert "run_count" in field_names
        assert "last_status" in field_names
        assert "last_error" in field_names


# === Scheduler Tests ===


class TestReflectionScheduler:
    """Tests for the ReflectionScheduler class."""

    def test_scheduler_loads_registry(self):
        """Scheduler loads registry on load()."""
        scheduler = ReflectionScheduler()
        scheduler.load()
        assert len(scheduler._entries) > 0

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
        # Mock the Reflection.get_or_create to avoid Redis dependency
        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.last_run = time.time() - 100
            mock_state.last_status = "success"
            mock_state.last_error = None
            mock_state.last_duration = 1.5
            mock_state.run_count = 5
            mock_reflection.get_or_create.return_value = mock_state

            result = scheduler.format_status()
            assert "Reflections:" in result
            assert "health-check" in result

    @pytest.mark.asyncio
    async def test_scheduler_tick_skips_not_due(self):
        """Tick skips reflections that aren't due yet."""
        scheduler = ReflectionScheduler()
        scheduler.load()

        with patch("agent.reflection_scheduler.Reflection") as mock_reflection:
            mock_state = MagicMock()
            mock_state.last_run = time.time()  # Just ran
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
            mock_state.last_run = time.time() - 10  # Recently started
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
            mock_state.last_run = time.time() - 10
            mock_state.last_status = "running"
            mock_reflection.get_or_create.return_value = mock_state

            await scheduler.tick()
            # mark_skipped must NOT be called - it would overwrite "running" status
            mock_state.mark_skipped.assert_not_called()


# === Registry File Integrity Tests ===


class TestRegistryIntegrity:
    """Tests that config/reflections.yaml is well-formed and complete."""

    def test_registry_yaml_valid(self):
        """Registry file is valid YAML."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        assert registry_path.exists(), "config/reflections.yaml must exist"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        assert "reflections" in data

    def test_all_entries_have_required_fields(self):
        """All registry entries have name, interval, priority, execution_type."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        for entry in data["reflections"]:
            assert "name" in entry, f"Entry missing name: {entry}"
            assert "interval" in entry, f"Entry {entry.get('name')} missing interval"
            assert "priority" in entry, f"Entry {entry.get('name')} missing priority"
            assert "execution_type" in entry, f"Entry {entry.get('name')} missing execution_type"

    def test_health_check_is_high_priority(self):
        """Health check must be high priority."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        health_entries = [e for e in data["reflections"] if e["name"] == "health-check"]
        assert len(health_entries) == 1
        assert health_entries[0]["priority"] == "high"

    def test_health_check_interval_5_minutes(self):
        """Health check interval should be 300 seconds (5 minutes)."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        health_entries = [e for e in data["reflections"] if e["name"] == "health-check"]
        assert health_entries[0]["interval"] == 300

    def test_daily_maintenance_interval_daily(self):
        """Daily maintenance should run once per day (86400 seconds)."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        daily_entries = [e for e in data["reflections"] if e["name"] == "daily-maintenance"]
        assert len(daily_entries) == 1
        assert daily_entries[0]["interval"] == 86400

    def test_no_duplicate_names(self):
        """All reflection names should be unique."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        names = [e["name"] for e in data["reflections"]]
        assert len(names) == len(set(names)), f"Duplicate names found: {names}"

    def test_expected_reflections_present(self):
        """All expected reflections are declared in the registry."""
        registry_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        names = {e["name"] for e in data["reflections"]}
        expected = {"health-check", "orphan-recovery", "stale-branch-cleanup", "daily-maintenance"}
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
                    warning_calls = [
                        str(c) for c in mock_logger.warning.call_args_list
                    ]
                    assert any("HIGH MEMORY DELTA" in str(c) for c in warning_calls)
