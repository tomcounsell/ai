"""Blinded judge envelope for the improvement evaluation harness.

The inner judge dict keeps the established ``judge_id`` / ``verdict`` /
``blockers`` / ``confidence`` contract, so it stays consumable by
``agent.sdlc_review_consensus.compute_consensus`` unchanged. Wrapper
fields (experiment id, contract digest, charter digest, evaluator
version, trial id, blinded arm id, raw-response reference) go around
it, never inside it.

The charter digest is an opaque caller-supplied string here. This
module deliberately never imports the charter record; quoting a digest
must not require read access to the authority it quotes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .blinding import BLINDED_ARM_IDS

EVALUATOR_VERSION = "1"


def coerce_judge_dict(judge: Any) -> dict:
    """Coerce untrusted judge data into the consensus-compatible contract.

    Normalizes the verdict (any variant containing "approved" becomes
    ``APPROVED``, anything else becomes ``CHANGES REQUESTED``), coerces
    ``blockers`` to int, and clamps ``confidence`` to [0, 1]. Extra keys
    pass through untouched.
    """
    if not isinstance(judge, Mapping):
        raise ValueError(f"judge must be a mapping, got {type(judge).__name__}")
    data = dict(judge)

    judge_id = data.get("judge_id", "")
    if not isinstance(judge_id, str) or not judge_id.strip():
        raise ValueError(f"judge_id must be a non-empty string: {judge_id!r}")

    verdict = data.get("verdict", "")
    if not isinstance(verdict, str):
        verdict = str(verdict)
    if "APPROVED" in verdict.strip().upper():
        verdict = "APPROVED"
    else:
        verdict = "CHANGES REQUESTED"

    blockers = data.get("blockers", 0)
    if isinstance(blockers, bool):
        raise ValueError(f"blockers must be an int, got {blockers!r}")
    try:
        blockers = int(blockers)
    except (TypeError, ValueError):
        raise ValueError(f"blockers must be an int, got {data.get('blockers')!r}") from None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence != confidence:  # NaN carries no information; fall back
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    coerced = {
        "judge_id": judge_id.strip(),
        "verdict": verdict,
        "blockers": blockers,
        "confidence": confidence,
    }
    for key, value in data.items():
        if key not in coerced:
            coerced[key] = value
    return coerced


def _require_non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string: {value!r}")
    return value


def wrap_judge_envelope(
    judge: Any,
    experiment_id: str,
    contract_digest: str,
    charter_digest: str,
    trial_id: str,
    blinded_arm_id: str,
    raw_response_ref: str,
) -> dict:
    """Wrap a coerced inner judge dict with blinded-evaluation metadata.

    The blinded-arm slot only accepts a blinded id (``arm-a`` /
    ``arm-b``); a true arm identity there is rejected rather than
    carried.
    """
    coerced = coerce_judge_dict(judge)
    if blinded_arm_id not in BLINDED_ARM_IDS:
        raise ValueError(
            f"blinded_arm_id must be one of {list(BLINDED_ARM_IDS)}, got {blinded_arm_id!r}"
        )
    return {
        "experiment_id": _require_non_empty("experiment_id", experiment_id),
        "contract_digest": _require_non_empty("contract_digest", contract_digest),
        "charter_digest": _require_non_empty("charter_digest", charter_digest),
        "evaluator_version": EVALUATOR_VERSION,
        "trial_id": _require_non_empty("trial_id", trial_id),
        "blinded_arm_id": blinded_arm_id,
        "raw_response_ref": _require_non_empty("raw_response_ref", raw_response_ref),
        "judge": coerced,
    }


def serialize_envelope(envelope: dict) -> str:
    """Serialize an envelope to canonical JSON (sorted keys, compact)."""
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"))
