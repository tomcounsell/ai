"""Research process digest (lane 6, #3218).

A research process is the thing the comparison compares: how opportunities
are selected, how investigation effort is split across kinds, how often the
planner revises, and which prompt and skill bodies it runs. The digest is a
canonical ``sha256:<hex>`` of that spec, and it is the one hashing routine
in the system. Lane 5 stores the same canonical bytes on every
``ImprovementModelRevision`` as ``research_process_spec`` and sets
``research_process_digest`` only by importing :func:`research_process_digest`
from here, so the byte form is a cross-lane contract:

    json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")).encode("utf-8")

Key order never changes the digest. This module touches no Redis.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from models.improvement_investigation import INVESTIGATION_KINDS

#: A non-empty ``investigation_budget_split`` must sum to 1 within this band.
SPLIT_SUM_MIN = 0.99
SPLIT_SUM_MAX = 1.01


@dataclass(frozen=True)
class ResearchProcessSpec:
    """Everything that identifies one research process.

    Fields:
        selection_rule: How opportunities are chosen from the ranked cases.
        investigation_budget_split: Share of investigation effort per kind,
            keyed by :data:`INVESTIGATION_KINDS`. An empty split is a
            legitimate "no split declared" (lane 5 writes every revision
            that way); a non-empty split must sum to 1 within
            ``[SPLIT_SUM_MIN, SPLIT_SUM_MAX]``.
        revision_cadence_seconds: How often the planner revises the model.
        planner_prompt_digest: ``sha256:`` of the planner prompt body.
        skill_digest: ``sha256:`` of the skill body the process runs.
        extra: Any further identifying material (a ranking module digest,
            say). Hashed with the rest; keys are sorted like every other.
    """

    selection_rule: str
    investigation_budget_split: dict[str, float]
    revision_cadence_seconds: int
    planner_prompt_digest: str
    skill_digest: str
    extra: dict = field(default_factory=dict)


def validate_spec(spec: ResearchProcessSpec) -> None:
    """Raise ``ValueError`` when the split names an unknown kind or sums off 1.

    The sum check applies only to a non-empty split.
    """
    split = spec.investigation_budget_split
    unknown = set(split) - set(INVESTIGATION_KINDS)
    if unknown:
        raise ValueError(
            f"investigation_budget_split names unknown kinds {sorted(unknown)}; "
            f"known kinds are {list(INVESTIGATION_KINDS)}"
        )
    if split:
        total = sum(float(v) for v in split.values())
        if not (SPLIT_SUM_MIN <= total <= SPLIT_SUM_MAX):
            raise ValueError(
                f"investigation_budget_split must sum to 1 "
                f"(within [{SPLIT_SUM_MIN}, {SPLIT_SUM_MAX}]); got {total}"
            )


def canonical_bytes(spec: ResearchProcessSpec) -> bytes:
    """The exact bytes the digest hashes. Lane 5 stores these on revisions."""
    return json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")).encode("utf-8")


def research_process_digest(spec: ResearchProcessSpec) -> str:
    """``"sha256:<hex>"`` of the spec's canonical JSON.

    Validates the split first: a spec that names an unknown investigation
    kind or splits effort to a total other than 1 raises ``ValueError``
    rather than digesting to a string no revision should carry.
    """
    validate_spec(spec)
    return "sha256:" + hashlib.sha256(canonical_bytes(spec)).hexdigest()
