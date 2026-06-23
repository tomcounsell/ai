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

from agent.reflection_schedule import parse_every_duration
from agent.reflection_scheduler import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_FUNCTION_TIMEOUT,
    ReflectionEntry,
    ReflectionScheduler,
    _get_memory_rss,
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
        assert "session-liveness-check" in all_names

    def test_load_registry_returns_only_enabled(self):
        """load_registry() filters out disabled entries."""
        entries = load_registry()
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
        """All registry entries have name, every, priority, execution_type."""
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        for entry in data["reflections"]:
            assert "name" in entry, f"Entry missing name: {entry}"
            # Schema uses `every: Ns` (unified grammar), not a bare `interval`.
            assert "every" in entry, f"Entry {entry.get('name')} missing every"
            assert "priority" in entry, f"Entry {entry.get('name')} missing priority"
            assert "execution_type" in entry, f"Entry {entry.get('name')} missing execution_type"

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

    def test_health_check_is_high_priority(self):
        """Health check must be high priority."""
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        health_entries = [e for e in data["reflections"] if e["name"] == "session-liveness-check"]
        assert len(health_entries) == 1
        assert health_entries[0]["priority"] == "high"

    def test_health_check_interval_5_minutes(self):
        """Health check interval should be 300 seconds (5 minutes)."""
        registry_path = _registry_path()
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        health_entries = [e for e in data["reflections"] if e["name"] == "session-liveness-check"]
        assert _entry_interval_seconds(health_entries[0]) == 300

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
        expected = {"session-liveness-check", "agent-session-cleanup", "stale-branch-cleanup"}
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
