"""Calibration for the serves-charter judge against a frozen reference set (#3216).

``ImprovementEvidence`` rows expire on a 30-day rolling window
(``models/improvement_evidence.py``, ``ttl = 86400 * 30``), and
``reflections/improvement_collect.py::classify_correction`` defaults to
``unknown`` by design, so the ``architectural`` bucket is both small and
rotating. A judge calibrated against a set that rotates monthly is not
calibrated. This module therefore reads the retained architectural
corrections once, freezes them to the verifying artifact store, and every
calibration record cites that frozen artifact by digest. Recalibration
writes a new artifact rather than mutating one, so a kappa reported in
March remains checkable in September.

Two numbers are reported, because kappa alone produces a false sense of
having addressed judge reliability: Cohen's kappa (chance-corrected
agreement of the judge against the reference expectation) and a paired
position-swap consistency figure (the fraction of items whose verdict is
unchanged when the presentation order is swapped). Both are pure Python;
no statistics dependency is needed or used.

``MIN_REFERENCE_SET_SIZE`` is 20. Observed on this machine at build time:
0 architectural rows in the 30-day window across every project partition
(the collector had written nothing retained in that window). Below 20
items a single flipped verdict moves observed agreement by more than five
points, so kappa cannot discriminate a calibrated judge from noise; the
judge returns ``infra_failure`` until the set grows. That is the weak-but-
honest disposition: early calibrations are labelled by their size rather
than gated by an abstract kappa threshold, and no kappa gate is applied.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from tools.improvement_eval.errors import InfraFailure

logger = logging.getLogger(__name__)

#: Floor on the frozen reference-set size. Below this the judge returns
#: ``infra_failure`` rather than an uncalibrated opinion. Rationale: with
#: binary verdicts each item moves observed agreement by ``1/n``, so below
#: n = 20 a single disagreement swings the reported agreement by more than
#: five points and the chance-correction denominator is estimated from too
#: few items per class to discriminate signal from noise. Set from the
#: measured reality that the live ``architectural`` bucket holds 0 rows.
MIN_REFERENCE_SET_SIZE = 20

#: Reference verdict for a retained architectural correction: the human's
#: rescue identified work that missed the end-to-end journey, so a charter
#: judge shown the correction should flag it.
EXPECTED_VERDICT = "CHANGES REQUESTED"


@dataclass(frozen=True)
class FrozenReferenceSet:
    """A reference set frozen to the verifying artifact store."""

    artifact_ref: str
    digest: str
    size: int


@dataclass(frozen=True)
class CalibrationResult:
    """A measured calibration, checkable from the cited artifact alone."""

    project_key: str
    artifact_ref: str
    digest: str
    size: int
    kappa: float
    position_swap_consistency: float


def cohens_kappa(labels_a: list, labels_b: list) -> float:
    """Chance-corrected agreement between two labelings of one set.

    ``(po - pe) / (1 - pe)`` where ``po`` is observed agreement and ``pe``
    is the agreement expected from the two marginal distributions. When
    ``pe == 1`` (both labelings constant on one label) the ratio is
    undefined: perfect agreement scores 1.0, anything else 0.0.
    """
    if not labels_a or len(labels_a) != len(labels_b):
        raise ValueError(
            f"cohens_kappa needs two non-empty labelings of equal length, "
            f"got {len(labels_a)} and {len(labels_b)}"
        )
    n = len(labels_a)
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    keys = set(labels_a) | set(labels_b)
    pe = sum(
        (sum(1 for a in labels_a if a == k) / n) * (sum(1 for b in labels_b if b == k) / n)
        for k in keys
    )
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def position_swap_consistency(first_labels: list, second_labels: list) -> float:
    """Fraction of items with an identical verdict across a position swap.

    The judge scores each reference item twice with the presentation order
    swapped; a judge that follows position rather than content scores low
    here even when its kappa looks healthy.
    """
    if not first_labels or len(first_labels) != len(second_labels):
        raise ValueError(
            "position_swap_consistency needs two non-empty labelings of equal length, "
            f"got {len(first_labels)} and {len(second_labels)}"
        )
    return sum(1 for a, b in zip(first_labels, second_labels) if a == b) / len(first_labels)


def collect_architectural_reference(project_key: str, *, limit: int = 500) -> list[dict]:
    """Read this project's retained architectural corrections, newest first."""
    from models.improvement_evidence import ImprovementEvidence

    items = []
    for row in ImprovementEvidence.recent(project_key, limit=limit):
        if getattr(row, "classification", None) != "architectural":
            continue
        items.append(
            {
                "classification": "architectural",
                "kind": getattr(row, "kind", None),
                "source_ref": getattr(row, "source_ref", None),
                "text": getattr(row, "text", None),
            }
        )
    return items


def _canonical_reference_bytes(items: list[dict]) -> bytes:
    """Canonical JSON for a reference set: sorted keys, sorted items."""
    ordered = sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    return json.dumps(ordered, sort_keys=True).encode("utf-8")


def freeze_reference_set(items: list[dict], *, store=None) -> FrozenReferenceSet:
    """Write a reference set to the verifying artifact store, content-addressed.

    Recalibration calls this again rather than mutating: new content hashes
    to a new reference, so past calibrations keep citing what they measured.
    """
    from models.verifying_artifact_store import verifying_artifact_store

    target = store or verifying_artifact_store
    payload = _canonical_reference_bytes(items)
    digest = hashlib.sha256(payload).hexdigest()
    artifact_ref = target.save(
        payload,
        key=f"calibration-reference-{digest[:16]}",
        model_class_name="ImprovementCalibration",
    )
    return FrozenReferenceSet(artifact_ref=artifact_ref, digest=digest, size=len(items))


def calibrate(project_key: str, judge_fn, *, store=None, limit: int = 500) -> CalibrationResult:
    """Freeze the reference set and measure the judge against it.

    ``judge_fn`` is called as ``judge_fn(text, swapped=False)`` per item and
    returns a verdict string. Raises :class:`InfraFailure` when the retained
    set is below ``MIN_REFERENCE_SET_SIZE`` (including zero): an
    uncalibrated opinion is harness breakage, not evidence.
    """
    items = collect_architectural_reference(project_key, limit=limit)
    if len(items) < MIN_REFERENCE_SET_SIZE:
        raise InfraFailure(
            f"Calibration reference set holds {len(items)} architectural corrections, "
            f"below the floor of {MIN_REFERENCE_SET_SIZE}; the serves-charter judge "
            "is uncalibrated for this project."
        )
    frozen = freeze_reference_set(items, store=store)
    reference_labels = [EXPECTED_VERDICT] * len(items)
    try:
        first = [judge_fn(item["text"], swapped=False) for item in items]
        second = [judge_fn(item["text"], swapped=True) for item in items]
    except Exception as exc:
        raise InfraFailure(f"Calibration judge run failed: {type(exc).__name__}: {exc}") from exc
    kappa = cohens_kappa(first, reference_labels)
    consistency = position_swap_consistency(first, second)
    logger.info(
        "serves_charter calibration: project=%s size=%d digest=%s kappa=%.3f swap=%.3f",
        project_key,
        len(items),
        frozen.digest,
        kappa,
        consistency,
    )
    return CalibrationResult(
        project_key=project_key,
        artifact_ref=frozen.artifact_ref,
        digest=frozen.digest,
        size=len(items),
        kappa=kappa,
        position_swap_consistency=consistency,
    )
