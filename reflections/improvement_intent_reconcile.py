"""The 300s recovery pass over dispatch intents (#3215, Task 7).

Runs regardless of ``ImprovementSettings.enabled``: recovery must still run
against any intent a *previous* enabled window admitted, even after the
switch is turned back off. Mirrors ``run_improvement_collect``'s per-item
try/except shape (Failure Path Test Strategy): the sweep never raises to
the scheduler, and its counts are returned in the result dict.
"""

from __future__ import annotations

import time


def run_improvement_intent_reconcile() -> dict:
    """Reflection entrypoint: one :func:`tools.improvement_control.recovery.reconcile`
    for the owning project. Standard reflection result dict."""
    t0 = time.time()
    from reflections.redis_access import get_project_key
    from tools.improvement_control.recovery import reconcile

    project_key = get_project_key()
    result = reconcile(project_key)
    return {
        "status": "completed",
        "findings": [],
        "summary": (
            f"improvement-intent-reconcile: stale_sweeps={len(result.stale_sweeps)} "
            f"forced_reconciliation={len(result.forced_reconciliation)} "
            f"forced_terminal={len(result.forced_terminal)} "
            f"released_slots={len(result.released_slots)} "
            f"unit2_receipted_unknown={len(result.unit2_receipted_unknown)} "
            f"errors={len(result.errors)}"
        ),
        "duration_seconds": round(time.time() - t0, 3),
    }
