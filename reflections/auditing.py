"""Compatibility re-exports for auditing reflections relocated to one file each.

Each auditing reflection now lives in its own self-contained module under
``reflections/audits/`` (one file per reflection — see issue #1028). The
reflections registry (config/reflections.yaml, vault) references the historical
``reflections.auditing.<fn>`` dotted paths below, so each is re-exported here
under its original name and the scheduler's importlib resolution keeps working
with no registry edit.

New code should import the reflection's ``run`` from its per-reflection module.
"""

from reflections.audits.hooks_audit import run as run_hooks_audit
from reflections.audits.pr_review_audit import run as run_pr_review_audit
from reflections.audits.skills_audit import run as run_skills_audit

__all__ = [
    "run_skills_audit",
    "run_hooks_audit",
    "run_pr_review_audit",
]
