"""The improvement scheduler's own tick: admit, materialize, activate (#3215).

Gated on ``ImprovementSettings.enabled`` exactly like
``reflections/improvement_collect.py`` -- the observer collects regardless,
the controller only dispatches once the switch is on, and the registration
in ``scripts/update/reflection_register.py`` is independent of both.
"""

from __future__ import annotations

import time


def run_improvement_controller_tick() -> dict:
    """Reflection entrypoint: one :func:`tools.improvement_control.scheduler_adapter.tick`
    for the owning project. Standard reflection result dict."""
    t0 = time.time()
    from config.settings import settings
    from reflections.redis_access import get_project_key

    project_key = get_project_key()

    if not settings.improvement.enabled:
        return {
            "status": "skipped",
            "findings": [],
            "summary": (
                "improvement-controller-tick: disabled "
                "(ImprovementSettings.enabled is False; set IMPROVEMENT__ENABLED=true "
                "to dispatch)"
            ),
            "duration_seconds": round(time.time() - t0, 3),
        }

    from tools.improvement_control.scheduler_adapter import tick

    result = tick(project_key)
    return {
        "status": "completed",
        "findings": [],
        "summary": (
            f"improvement-controller-tick: admitted={len(result.admitted)} "
            f"materialized={len(result.materialized)} activated={len(result.activated)} "
            f"skipped={len(result.skipped)} errors={len(result.errors)}"
        ),
        "duration_seconds": round(time.time() - t0, 3),
    }
