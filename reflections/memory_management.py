"""Compatibility re-exports for memory reflections relocated to one file each.

Each memory reflection now lives in its own self-contained module under
``reflections/memory/`` (one file per reflection — see issue #1028). The
reflections registry (config/reflections.yaml, vault) references the historical
``reflections.memory_management.<fn>`` dotted paths below, so each is re-exported
here under its original name and the scheduler's importlib resolution keeps
working with no registry edit.

New code should import the reflection's ``run`` from its per-reflection module.
"""

from reflections.memory.embedding_orphan_sweep import run as run_embedding_orphan_sweep
from reflections.memory.memory_decay_prune import run as run_memory_decay_prune
from reflections.memory.memory_embedding_backfill import run as run_memory_embedding_backfill
from reflections.memory.memory_outcome_resolve import run as run_memory_outcome_resolve
from reflections.memory.memory_quality_audit import run as run_memory_quality_audit

__all__ = [
    "run_memory_decay_prune",
    "run_memory_quality_audit",
    "run_embedding_orphan_sweep",
    "run_memory_embedding_backfill",
    "run_memory_outcome_resolve",
]
