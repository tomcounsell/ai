"""Opportunity freshness (lane 6, #3218).

A comparison must run both arms on opportunities neither process has already
worked, or the arm that saw them first inherits a head start. An opportunity
is fresh when no record shows it was worked: the ``ImprovementCase`` exists
and is open, no ``ImprovementExperiment`` or ``ImprovementInvestigation``
cites it as ``case_id``, and no prior comparison experiment lists it in its
manifest's ``opportunity_ids``. The decision is by lookup, never by
inference, and the comparison hashes the resulting id set into its contract.

Model imports are lazy so this module binds Redis at call time, the pattern
``tools/infrastructure_budget.py`` uses for the same reason.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: The ``candidate_surfaces`` value that marks an experiment as a comparison.
COMPARISON_SURFACES: tuple[str, ...] = ("research_process",)

#: Exclusion reasons, closed vocabulary.
NOT_FOUND = "NOT_FOUND"
NOT_OPEN = "NOT_OPEN"
HAS_EXPERIMENT = "HAS_EXPERIMENT"
HAS_INVESTIGATION = "HAS_INVESTIGATION"
IN_PRIOR_COMPARISON = "IN_PRIOR_COMPARISON"


def _as_list(value) -> list:
    """Decode a JSON-encoded list field; a list passes through, else empty."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def is_comparison_experiment(experiment) -> bool:
    """True when the experiment's candidate surface is the research process itself."""
    return tuple(_as_list(getattr(experiment, "candidate_surfaces", None))) == COMPARISON_SURFACES


def comparison_opportunity_ids(experiment) -> set[str]:
    """The ``opportunity_ids`` a comparison experiment's manifest names.

    The manifest is a ``ContentField`` on the verifying store, so it is read
    through lane 4's ``read_content`` and re-hashed on load. A manifest that
    is absent or fails to decode names nothing, with a warning.
    """
    from tools.improvement_eval.runner import read_content

    raw = read_content(experiment, "manifest")
    if raw is None:
        return set()
    if isinstance(raw, dict):
        manifest = raw
    else:
        try:
            manifest = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "freshness: comparison experiment %s carries an undecodable manifest",
                getattr(experiment, "id", "?"),
            )
            return set()
    ids = manifest.get("opportunity_ids") if isinstance(manifest, dict) else None
    return {str(i) for i in ids} if isinstance(ids, list) else set()


def fresh_opportunities(
    candidate_ids: list[str], *, project_key: str = "valor"
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split candidate case ids into fresh ones and excluded ones with reasons.

    Returns ``(fresh, excluded)``; ``excluded`` is a list of
    ``(case_id, reason)`` pairs in candidate order. Duplicated candidates are
    decided once. An empty candidate list returns ``([], [])`` without
    touching Redis.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for cid in candidate_ids:
        cid = str(cid)
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    if not ordered:
        return [], []

    from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
    from models.improvement_experiment import ImprovementExperiment
    from models.improvement_investigation import ImprovementInvestigation

    experimented: set[str] = set()
    compared: set[str] = set()
    for experiment in ImprovementExperiment.query.filter(project_key=project_key):
        if experiment.case_id:
            experimented.add(str(experiment.case_id))
        if is_comparison_experiment(experiment):
            compared |= comparison_opportunity_ids(experiment)
    investigated: set[str] = {
        str(row.case_id)
        for row in ImprovementInvestigation.query.filter(project_key=project_key)
        if row.case_id
    }

    fresh: list[str] = []
    excluded: list[tuple[str, str]] = []
    for cid in ordered:
        case = ImprovementCase.query.filter(project_key=project_key, id=cid).first()
        if case is None:
            excluded.append((cid, NOT_FOUND))
        elif case.state not in OPEN_CASE_STATES:
            excluded.append((cid, NOT_OPEN))
        elif cid in experimented:
            excluded.append((cid, HAS_EXPERIMENT))
        elif cid in investigated:
            excluded.append((cid, HAS_INVESTIGATION))
        elif cid in compared:
            excluded.append((cid, IN_PRIOR_COMPARISON))
        else:
            fresh.append(cid)
    return fresh, excluded
