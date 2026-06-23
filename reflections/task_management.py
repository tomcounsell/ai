"""Compatibility re-exports for task-management reflections relocated to one file each.

Each task-management reflection now lives in its own self-contained module under
``reflections/audits/`` (one file per reflection — see issue #1028). The
reflections registry (config/reflections.yaml, vault) references the historical
``reflections.task_management.<fn>`` dotted paths below, so each is re-exported
here under its original name and the scheduler's importlib resolution keeps
working with no registry edit.

New code should import the reflection's ``run`` from its per-reflection module.
"""

from reflections.audits.principal_staleness import run as run_principal_staleness
from reflections.audits.task_backlog_check import run as run_task_management

__all__ = [
    "run_task_management",
    "run_principal_staleness",
]
