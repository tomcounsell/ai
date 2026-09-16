"""Task-rubric judges for agent-run evaluations (#3311).

A rubric judge is deterministic: it carries one frozen endpoint name and the
frozen expected verdict for the task it scores, reads the session outcome out
of the blinded envelope, and scores the ``VERDICT: <token>`` line 1.0/0.0.
A blank or malformed outcome scores zero with a named reason and never
throws, so a confused session is measured, never a harness error.

The ``judge_id`` equals the protocol endpoint it scores: that is the key
:func:`tools.improvement_eval.runner._score_agent_trials` matches on. The
serves-charter judge never matches a rubric endpoint and stays a recorded
leg without scoring.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_VERDICT_RE = re.compile(r"^\s*verdict\s*:\s*(\S.*?)\s*$", re.I | re.M)


def extract_verdict(output: str) -> str | None:
    """The last ``VERDICT: <token>`` line's token, upper-cased, or ``None``."""
    if not isinstance(output, str) or not output.strip():
        return None
    hits = _VERDICT_RE.findall(output)
    if not hits:
        return None
    return hits[-1].strip().upper()


def make_rubric_judge(endpoint: str, expected_verdict, rubric_text: str = ""):
    """Build the ``JudgeFn`` scoring ``endpoint`` against ``expected_verdict``.

    ``expected_verdict`` is one frozen token for every trial, or a mapping
    of trial id to token when tasks differ (each task's rubric names its own
    expected verdict). A trial outside the map scores 0.0 with the reason
    named. ``rubric_text`` is the frozen rubric prose carried for the record;
    the score itself compares the extracted verdict token to the expected one
    (case- and space-tolerant). The envelope is always ``ok``: even a
    malformed input scores 0.0 with the reason named.
    """
    if isinstance(expected_verdict, dict):
        expected_map = {str(k): (v or "").strip().upper() for k, v in expected_verdict.items()}
    else:
        expected_map = None
        expected_single = (expected_verdict or "").strip().upper()

    def _expected(trial_id: str) -> tuple[str | None, str]:
        if expected_map is None:
            return expected_single, ""
        if trial_id in expected_map:
            return expected_map[trial_id], ""
        return None, f"trial {trial_id!r} names no frozen expected verdict"

    def _judge(candidate_output: str, *, blinded_arm_id: str, trial_id: str) -> dict:
        reason = ""
        try:
            expected, missing = _expected(str(trial_id))
            if expected is None:
                reason = missing
                score = 0.0
            else:
                payload = json.loads(candidate_output)
                outcome = payload.get("outcome") if isinstance(payload, dict) else None
                output = outcome.get("output") if isinstance(outcome, dict) else None
                if not isinstance(output, str) or not output.strip():
                    reason = "blank session outcome; no VERDICT line to score"
                    score = 0.0
                else:
                    found = extract_verdict(output)
                    if found is None:
                        reason = "no VERDICT line in the session outcome"
                        score = 0.0
                    elif found == expected:
                        reason = f"verdict {found} matches the frozen expected {expected}"
                        score = 1.0
                    else:
                        reason = f"verdict {found} differs from the frozen expected {expected}"
                        score = 0.0
        except Exception as exc:  # noqa: BLE001 -- malformed input scores, never throws
            reason = f"unparseable judge input ({type(exc).__name__}); scored zero"
            score = 0.0
        judge: dict[str, Any] = {
            "judge_id": endpoint,
            "verdict": "APPROVED" if score >= 1.0 else "CHANGES REQUESTED",
            "blockers": 0 if score >= 1.0 else 1,
            "confidence": 1.0,
            "score": score,
            "reasoning_summary": reason,
        }
        if rubric_text:
            judge["rubric"] = rubric_text
        logger.info(
            "rubric judge %s scored trial %s: %.1f (%s)",
            endpoint,
            trial_id,
            score,
            reason,
        )
        return {"status": "ok", "judge": judge}

    return _judge
