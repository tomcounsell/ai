"""Seeded arm assignment, blinded arm ids, and identity-leak scanning.

The evaluation harness compares a candidate against an incumbent without
letting the judges know which arm is which. This module owns the three
pieces that make that claim checkable:

- :func:`assign_arms` deterministically derives a run order and a
  blinded-id mapping from the experiment id, so the assignment is
  reproducible and recorded rather than improvised per run.
- :func:`identity_tokens` derives the leak-detection token list from the
  experiment record itself (candidate surfaces, manifest branch/files,
  hypothesis text, operator-supplied tokens), so a new identity-bearing
  field is covered without an edit here.
- :func:`scan_for_identity` runs over the serialized judge envelope
  immediately before it is handed to a judge. A hit records
  ``blinded=False`` downstream; it never suppresses the evaluation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

CANDIDATE_ARM = "candidate"
INCUMBENT_ARM = "incumbent"

BLINDED_ARM_IDS = ("arm-a", "arm-b")

_CONTENT_FIELDS = (
    "candidate_surfaces",
    "manifest",
    "hypothesis",
    "mechanism",
    "falsifier",
)
_OPERATOR_TOKEN_FIELDS = ("identity_tokens", "extra_identity_tokens")


@dataclass
class ArmAssignment:
    """Seeded assignment of the two arms to run order and blinded ids."""

    run_order: tuple
    blinded_ids: dict
    digest: str


@dataclass(frozen=True)
class IdentityScan:
    """Result of scanning a serialized envelope for identity tokens."""

    leaked: bool
    hits: tuple = ()


def assign_arms(experiment_id: str) -> ArmAssignment:
    """Derive a deterministic arm assignment from the experiment id.

    One bit of ``sha256(experiment_id)`` decides both the run order and
    the real-arm to blinded-id mapping, so each experiment gets a stable
    assignment and the population of experiments splits across both
    possible orders.
    """
    seed = hashlib.sha256(experiment_id.encode("utf-8")).digest()
    flipped = bool(seed[0] & 1)
    if flipped:
        run_order = (INCUMBENT_ARM, CANDIDATE_ARM)
        blinded_ids = {
            CANDIDATE_ARM: BLINDED_ARM_IDS[1],
            INCUMBENT_ARM: BLINDED_ARM_IDS[0],
        }
    else:
        run_order = (CANDIDATE_ARM, INCUMBENT_ARM)
        blinded_ids = {
            CANDIDATE_ARM: BLINDED_ARM_IDS[0],
            INCUMBENT_ARM: BLINDED_ARM_IDS[1],
        }
    payload = json.dumps(
        {
            "experiment_id": experiment_id,
            "run_order": list(run_order),
            "blinded_ids": blinded_ids,
        },
        sort_keys=True,
    )
    digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ArmAssignment(run_order=run_order, blinded_ids=blinded_ids, digest=digest)


def _read_field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _as_tokens(value: Any) -> list:
    """Flatten one field value into raw token strings.

    Accepts plain strings, JSON-encoded strings, lists, and mappings.
    Returns the strings untouched (including whitespace-only ones, which
    :func:`scan_for_identity` rejects loudly rather than matching
    silently). Non-string scalars are ignored.
    """
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                return _as_tokens(json.loads(value))
            except (ValueError, TypeError):
                pass
        return [value]
    if isinstance(value, (list, tuple)):
        tokens: list = []
        for item in value:
            tokens.extend(_as_tokens(item))
        return tokens
    if isinstance(value, dict):
        tokens = []
        for item in value.values():
            tokens.extend(_as_tokens(item))
        return tokens
    return []


def identity_tokens(experiment: Any) -> list:
    """Derive identity tokens from the experiment record.

    Only content fields are read (surfaces, manifest, hypothesis text,
    operator-supplied lists), so envelope plumbing such as experiment
    ids and digests can never appear in the token list. Accepts mapping
    records, attribute records, and bare lists of strings.
    """
    if isinstance(experiment, (list, tuple)):
        return _as_tokens(list(experiment))
    tokens: list = []
    for field_name in _CONTENT_FIELDS + _OPERATOR_TOKEN_FIELDS:
        tokens.extend(_as_tokens(_read_field(experiment, field_name)))
    return tokens


def scan_for_identity(serialized_envelope: str, experiment: Any) -> IdentityScan:
    """Substring-scan the serialized envelope for experiment identity tokens.

    Empty tokens are skipped (they would match everything); a
    whitespace-only token raises ``ValueError`` for the same reason. An
    empty envelope scans clean.
    """
    envelope = serialized_envelope or ""
    hits: list = []
    for token in identity_tokens(experiment):
        if token is None or not isinstance(token, str):
            continue
        if token == "":
            continue
        if token.strip() == "":
            raise ValueError(f"identity token is whitespace-only: {token!r}")
        if token in envelope:
            hits.append(token)
    return IdentityScan(leaked=bool(hits), hits=tuple(hits))
