"""Every improvement reflection entry point runs for the owning project.

``valor-improve`` is bound to ``"valor"`` (``tools/improvement.py::PROJECT_KEY``),
and the reflection side resolves the owning project through
``reflections.redis_access.get_project_key`` (``VALOR_PROJECT_KEY``, falling
back to ``"valor"``). The first real cycle (#3217, build task 9) found the five
reflection entry points reading ``config.memory_defaults.DEFAULT_PROJECT_KEY``
instead, which is the literal ``"default"``: the evidence tick wrote its rows
under ``default`` and the planner would have opened the case where the CLI
could never find it. These tests pin the resolution at the entry points by
capturing the project key each one hands to its inner call.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from reflections import (
    improvement_assumption_digest,
    improvement_collect,
    improvement_controller_tick,
    improvement_intent_reconcile,
    improvement_plan,
)

pytestmark = pytest.mark.reflections

PK = "test-3217-project-key"


def _enabled():
    from config.settings import settings

    return patch.object(settings.improvement, "enabled", True)


def _run_collect() -> str:
    seen: list[str] = []

    def human_memories(project_key):
        seen.append(project_key)
        return []

    with (
        _enabled(),
        patch.object(improvement_collect, "human_memories", human_memories),
        patch.object(improvement_collect, "_recent_sessions", return_value=[]),
        patch.object(improvement_collect, "collect_expectation_coverage", return_value=0),
        patch.object(improvement_collect, "collect_lessons", return_value=0),
        patch.object(improvement_collect, "collect_promises", return_value=0),
    ):
        improvement_collect.run_improvement_collect()
    return seen[0]


def _run_planner() -> str:
    seen: list[str] = []

    def plan_tick(project_key, **kwargs):
        seen.append(project_key)
        return improvement_plan.TickResult(status="success")

    with _enabled(), patch.object(improvement_plan, "plan_tick", plan_tick):
        improvement_plan.run_improvement_planner()
    return seen[0]


def _run_digest() -> str:
    seen: list[str] = []

    def collect(project_key, *, since):
        seen.append(project_key)
        raise RuntimeError("stop here")

    with _enabled(), patch.object(improvement_assumption_digest, "collect", collect):
        improvement_assumption_digest.run_improvement_assumption_digest(sender=lambda *a, **k: True)
    return seen[0]


def _run_controller_tick() -> str:
    seen: list[str] = []

    def tick(project_key, **kwargs):
        seen.append(project_key)
        from tools.improvement_control.scheduler_adapter import TickResult

        return TickResult()

    with _enabled(), patch("tools.improvement_control.scheduler_adapter.tick", tick):
        improvement_controller_tick.run_improvement_controller_tick()
    return seen[0]


def _run_intent_reconcile() -> str:
    seen: list[str] = []

    def reconcile(project_key, **kwargs):
        seen.append(project_key)
        from tools.improvement_control.recovery import ReconcileResult

        return ReconcileResult()

    with patch("tools.improvement_control.recovery.reconcile", reconcile):
        improvement_intent_reconcile.run_improvement_intent_reconcile()
    return seen[0]


ENTRY_POINTS = {
    "collect": _run_collect,
    "planner": _run_planner,
    "assumption_digest": _run_digest,
    "controller_tick": _run_controller_tick,
    "intent_reconcile": _run_intent_reconcile,
}


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_unset_env_resolves_the_valor_project_never_default(name):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VALOR_PROJECT_KEY", None)
        assert ENTRY_POINTS[name]() == "valor"


@pytest.mark.parametrize("name", sorted(ENTRY_POINTS))
def test_env_names_the_owning_project(name):
    with patch.dict(os.environ, {"VALOR_PROJECT_KEY": PK}):
        assert ENTRY_POINTS[name]() == PK
