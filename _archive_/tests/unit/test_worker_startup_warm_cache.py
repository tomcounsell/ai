"""Startup never waits on ``gh``, and the eligibility warm-up task is held (#3410).

Both long-lived processes warm the charter-§7 eligibility cache for their
project list at startup through ``tools.improvement_eligibility
.schedule_warm_cache``: ``worker/__main__.py::_run_worker`` with
``list(projects)`` before its worker loops start, and
``bridge/telegram_bridge.py::main`` with ``ACTIVE_PROJECTS`` before the
connect step. Neither awaits the task: ``is_open_source`` shells
``gh repo view`` with a 10 s timeout per key, and that must never sit ahead
of "Connected to Telegram" or the worker loops.

Two proofs, because neither ``_run_worker`` nor ``main`` can run in a unit
test (Redis, the ``claude`` binary, a Telethon client, an orphan reaper):

* Behavior, on the function both processes call: with ``is_open_source``
  faked to sleep 2 s per key, ``schedule_warm_cache`` returns within
  0.5 s with the task pending and held in ``_BACKGROUND_TASKS``; after it
  completes the set no longer holds it and ``caplog`` carries the
  ``eligibility warm-up done`` line with the right counts, one raising key
  included.
* Structure, on the two call sites by AST: each is a bare expression
  statement (never awaited, never wrapped in ``create_task``), carries the
  process's key list, and sits ahead of the loop start (the first
  ``_ensure_worker(`` call) and the connect step (``client.connect()``).
"""

from __future__ import annotations

import ast
import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest

from tools import improvement_eligibility

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_MAIN = REPO_ROOT / "worker" / "__main__.py"
BRIDGE_MAIN = REPO_ROOT / "bridge" / "telegram_bridge.py"


@pytest.fixture
def slow_is_open_source(monkeypatch):
    """``is_open_source`` blocks 2 s per key unless ``release`` is set first."""
    release = threading.Event()
    seen: list[str] = []

    def _slow(key: str) -> bool:
        seen.append(key)
        release.wait(2.0)
        if key == "broken":
            raise RuntimeError("gh exploded")
        return key in {"valor", "public-thing"}

    monkeypatch.setattr(improvement_eligibility, "is_open_source", _slow)
    return release, seen


class TestScheduleWarmCacheAtStartup:
    async def test_returns_at_once_with_the_task_held_then_releases_it(
        self, slow_is_open_source, caplog
    ):
        release, seen = slow_is_open_source
        keys = ["valor", "public-thing", "client-thing"]

        with caplog.at_level(logging.INFO, logger="tools.improvement_eligibility"):
            started = time.monotonic()
            task = improvement_eligibility.schedule_warm_cache(keys)
            elapsed = time.monotonic() - started

            assert elapsed < 0.5, f"startup waited {elapsed:.2f}s on the warm-up"
            assert not task.done(), "the warm-up must still be pending when startup moves on"
            assert task in improvement_eligibility._BACKGROUND_TASKS
            await asyncio.sleep(0)
            assert not task.done()

            release.set()
            await asyncio.wait_for(task, timeout=5.0)

        assert task not in improvement_eligibility._BACKGROUND_TASKS
        assert sorted(seen) == sorted(keys)
        done_lines = [
            r.getMessage() for r in caplog.records if r.getMessage().startswith("eligibility warm")
        ]
        assert done_lines == ["eligibility warm-up done keys=3 public=2 failed=0"]

    async def test_one_raising_key_still_warms_the_others(self, slow_is_open_source, caplog):
        release, seen = slow_is_open_source
        release.set()

        with caplog.at_level(logging.INFO, logger="tools.improvement_eligibility"):
            task = improvement_eligibility.schedule_warm_cache(["valor", "broken", "client-thing"])
            await asyncio.wait_for(task, timeout=5.0)

        assert sorted(seen) == ["broken", "client-thing", "valor"]
        done_lines = [
            r.getMessage() for r in caplog.records if r.getMessage().startswith("eligibility warm")
        ]
        assert done_lines == ["eligibility warm-up done keys=3 public=1 failed=1"]
        assert task not in improvement_eligibility._BACKGROUND_TASKS


def _function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async def {name} not found in {path}")


def _is_call_to(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == name)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
    )


def _warm_up_statements(fn: ast.AsyncFunctionDef) -> list[ast.stmt]:
    """Statements in ``fn`` whose value is a ``schedule_warm_cache(...)`` call."""
    return [
        stmt
        for stmt in ast.walk(fn)
        if isinstance(stmt, ast.Expr) and _is_call_to(stmt.value, "schedule_warm_cache")
    ]


def _first_line_of_call(fn: ast.AsyncFunctionDef, name: str) -> int:
    lines = [node.lineno for node in ast.walk(fn) if _is_call_to(node, name)]
    assert lines, f"no {name}( call inside {fn.name}"
    return min(lines)


def _assert_never_awaited_or_wrapped(fn: ast.AsyncFunctionDef) -> None:
    for node in ast.walk(fn):
        if isinstance(node, ast.Await) and _is_call_to(node.value, "schedule_warm_cache"):
            raise AssertionError(f"{fn.name} awaits schedule_warm_cache at line {node.lineno}")
        if _is_call_to(node, "create_task"):
            for arg in node.args:
                if _is_call_to(arg, "warm_cache") or _is_call_to(arg, "schedule_warm_cache"):
                    raise AssertionError(
                        f"{fn.name} wraps the warm-up in create_task at line {node.lineno}; "
                        "schedule_warm_cache is the one entry point (it holds the task)"
                    )


class TestWorkerCallSite:
    def test_run_worker_schedules_its_projects_before_the_loops_start(self):
        fn = _function(WORKER_MAIN, "_run_worker")
        statements = _warm_up_statements(fn)
        assert len(statements) == 1, "exactly one schedule_warm_cache call in _run_worker"
        call = statements[0].value
        assert ast.unparse(call) == "schedule_warm_cache(list(projects))"
        assert call.lineno < _first_line_of_call(fn, "_ensure_worker"), (
            "the warm-up must be scheduled before the worker loops start"
        )
        _assert_never_awaited_or_wrapped(fn)


class TestBridgeCallSite:
    def test_main_schedules_active_projects_before_the_connect_step(self):
        fn = _function(BRIDGE_MAIN, "main")
        statements = _warm_up_statements(fn)
        assert len(statements) == 1, "exactly one schedule_warm_cache call in main"
        call = statements[0].value
        assert ast.unparse(call) == "schedule_warm_cache(ACTIVE_PROJECTS)"
        assert call.lineno < _first_line_of_call(fn, "connect"), (
            "the warm-up must be scheduled before client.connect()"
        )
        _assert_never_awaited_or_wrapped(fn)


def test_no_bare_create_task_over_warm_cache_anywhere_in_bridge_or_worker():
    """The Verification grep: both processes go through ``schedule_warm_cache``."""
    hits: list[str] = []
    for root in ("bridge", "worker"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "create_task(" in line and "warm_cache" in line:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert hits == []
