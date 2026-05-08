"""Tests for `execute_function_reflection` capturing the callable's return
value and `run_reflection` forwarding `projects` to `mark_completed`.

Pre-#1187: `execute_function_reflection` was `-> None` and discarded any
return value. Per-project audits now return
``{"status": ..., "findings": [...], "summary": ..., "projects": [...]}``,
and ``run_reflection`` extracts the ``projects`` list and threads it through
``mark_completed(projects=...)`` so the modal can render the per-project
sub-table.

These tests pin two contracts that callers depend on:

1. `execute_function_reflection` returns the callable's return value verbatim
   (sync AND async callables).
2. `run_reflection` passes `projects=<list>` for dict returns with a
   `"projects"` key, and `projects=None` for None returns / non-dict returns
   / agent-type reflections — preserving backward compatibility with
   non-audit reflections.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.reflection_scheduler import (
    ReflectionEntry,
    execute_function_reflection,
    run_reflection,
)

# --------------------------- execute_function_reflection ---------------------------


@pytest.mark.asyncio
async def test_execute_function_reflection_returns_sync_callable_result():
    """Sync callable's return value is captured and returned by the wrapper."""
    sentinel = {"status": "ok", "findings": [], "summary": "s", "projects": []}

    def fake_callable():
        return sentinel

    entry = ReflectionEntry(
        name="fake-sync",
        description="fake",
        execution_type="function",
        callable="tests.unit.test_scheduler_result_forwarding._noop_target",
        schedule="every:86400s",
        priority="low",
    )
    with patch(
        "agent.reflection_scheduler._resolve_callable",
        return_value=fake_callable,
    ):
        result = await execute_function_reflection(entry)
    assert result is sentinel


@pytest.mark.asyncio
async def test_execute_function_reflection_returns_async_callable_result():
    """Async callable's awaited return value is captured."""
    sentinel = {"status": "ok", "projects": [{"slug": "ai", "status": "ok"}]}

    async def fake_async():
        return sentinel

    entry = ReflectionEntry(
        name="fake-async",
        description="fake",
        execution_type="function",
        callable="tests.unit.test_scheduler_result_forwarding._noop_target",
        schedule="every:86400s",
        priority="low",
    )
    with patch(
        "agent.reflection_scheduler._resolve_callable",
        return_value=fake_async,
    ):
        result = await execute_function_reflection(entry)
    assert result is sentinel


@pytest.mark.asyncio
async def test_execute_function_reflection_returns_none_for_legacy_callable():
    """Legacy callables that don't return anything propagate as None."""

    def legacy_void():
        # No return statement — implicit None
        return None

    entry = ReflectionEntry(
        name="fake-legacy",
        description="fake",
        execution_type="function",
        callable="tests.unit.test_scheduler_result_forwarding._noop_target",
        schedule="every:86400s",
        priority="low",
    )
    with patch(
        "agent.reflection_scheduler._resolve_callable",
        return_value=legacy_void,
    ):
        result = await execute_function_reflection(entry)
    assert result is None


# ------------------------------ run_reflection ------------------------------


def _make_entry(execution_type: str = "function") -> ReflectionEntry:
    return ReflectionEntry(
        name="fake",
        description="fake",
        execution_type=execution_type,
        callable=(
            "tests.unit.test_scheduler_result_forwarding._noop_target"
            if execution_type == "function"
            else None
        ),
        command="fake-agent" if execution_type == "agent" else None,
        schedule="every:86400s",
        priority="low",
    )


def _make_state() -> MagicMock:
    state = MagicMock()
    state.mark_started = MagicMock()
    state.mark_completed = MagicMock()
    return state


@pytest.mark.asyncio
async def test_run_reflection_forwards_projects_from_dict_result():
    """A function that returns {"projects": [...]} → mark_completed(projects=[...])."""
    projects_list = [
        {"slug": "ai", "status": "ok", "duration": 1.0, "findings_count": 0, "error": None},
        {
            "slug": "popoto",
            "status": "error",
            "duration": 0.5,
            "findings_count": 0,
            "error": "boom",
        },
    ]
    fake_result = {"status": "error", "findings": [], "summary": "s", "projects": projects_list}

    entry = _make_entry()
    state = _make_state()
    with patch(
        "agent.reflection_scheduler.execute_function_reflection",
        return_value=fake_result,
    ) as mock_exec:
        # Make the patched coroutine awaitable: wrap in an async-returning fn
        async def _awaitable_returning(_entry):
            return fake_result

        mock_exec.side_effect = _awaitable_returning

        await run_reflection(entry, state)

    # mark_completed should be called once with projects=projects_list
    state.mark_completed.assert_called_once()
    _args, kwargs = state.mark_completed.call_args
    assert kwargs.get("projects") == projects_list


@pytest.mark.asyncio
async def test_run_reflection_passes_none_for_legacy_none_result():
    """Legacy callable returning None → mark_completed(projects=None)."""
    entry = _make_entry()
    state = _make_state()
    with patch(
        "agent.reflection_scheduler.execute_function_reflection",
    ) as mock_exec:

        async def _none(_entry):
            return None

        mock_exec.side_effect = _none

        await run_reflection(entry, state)

    state.mark_completed.assert_called_once()
    _args, kwargs = state.mark_completed.call_args
    assert kwargs.get("projects") is None


@pytest.mark.asyncio
async def test_run_reflection_passes_none_for_dict_without_projects_key():
    """Dict result missing the `projects` key → mark_completed(projects=None)."""
    entry = _make_entry()
    state = _make_state()
    with patch(
        "agent.reflection_scheduler.execute_function_reflection",
    ) as mock_exec:

        async def _dict_no_projects(_entry):
            return {"status": "ok", "findings": [], "summary": "no per-project data"}

        mock_exec.side_effect = _dict_no_projects

        await run_reflection(entry, state)

    state.mark_completed.assert_called_once()
    _args, kwargs = state.mark_completed.call_args
    # dict.get("projects") on a dict without that key returns None
    assert kwargs.get("projects") is None


@pytest.mark.asyncio
async def test_run_reflection_passes_none_for_non_dict_return():
    """Non-dict return (string, int, list) → mark_completed(projects=None).

    Guards Risk 2 in the plan: any non-dict return value must safely degrade
    to projects=None and not crash the scheduler.
    """
    entry = _make_entry()
    state = _make_state()
    with patch(
        "agent.reflection_scheduler.execute_function_reflection",
    ) as mock_exec:

        async def _str_result(_entry):
            return "not a dict"

        mock_exec.side_effect = _str_result

        await run_reflection(entry, state)

    state.mark_completed.assert_called_once()
    _args, kwargs = state.mark_completed.call_args
    assert kwargs.get("projects") is None


# Module-level no-op target referenced by `_resolve_callable` mocking.
def _noop_target():
    return None
