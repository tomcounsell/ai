"""CLI and Python API for recording SDLC critique/review verdicts.

This module is the **single writer** for the ``_verdicts`` metadata subkey on
``AgentSession.stage_states``. The SDLC router (``agent/sdlc_router.py``)
reads from this subkey via ``sdlc_stage_query`` to drive Legal Dispatch
Guards G1, G3, and G5. Unifying the writer avoids the dual-source drift that
caused the original oscillation bug (see
``docs/plans/sdlc-router-oscillation-guard.md``).

Two entry points:

1. CLI (invoked by ``.claude/skills/do-plan-critique/SKILL.md`` and
   ``.claude/skills/do-pr-review/SKILL.md``)::

       python -m tools.sdlc_verdict record --stage CRITIQUE \\
           --verdict "NEEDS REVISION" --issue-number 1040
       python -m tools.sdlc_verdict record --stage REVIEW \\
           --verdict "CHANGES REQUESTED" --blockers 2 --issue-number 1040
       python -m tools.sdlc_verdict get --stage CRITIQUE --issue-number 1040

2. Python API (called from ``agent/pipeline_state.classify_outcome()``)::

       from tools.sdlc_verdict import record_verdict, get_verdict
       record_verdict(session, "CRITIQUE", "READY TO BUILD (no concerns)")
       record = get_verdict(session, "CRITIQUE")

Shape of ``_verdicts[stage]``::

    {
        "verdict": "NEEDS REVISION",
        "recorded_at": "2026-04-18T12:34:56+00:00",
        "artifact_hash": "sha256:...",  # CRITIQUE only; None for REVIEW
        "blockers": 0,                    # REVIEW only
        "tech_debt": 0,                   # REVIEW only
    }

Graceful failure: every function returns ``{}`` on error. Missing Redis, bad
input, malformed sessions — none of these crash the caller. Skills rely on
this: a verdict record failure must never block a critique/review from
finishing.

Artifact hash semantics (CRITIQUE only):
  - Normalize line endings to ``\\n`` (cross-platform safety).
  - Hash the FULL UTF-8-encoded plan file bytes, including YAML frontmatter.
    Frontmatter edits (e.g. ``revision_applied: true``) are meaningful plan
    changes that MUST bust the cache.
  - Do NOT normalize whitespace within prose. A reviewer who reflows a
    paragraph is editing the plan; the critique should re-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools._sdlc_utils import find_plan_path as _find_plan_path
from tools._sdlc_utils import find_session as _find_session

logger = logging.getLogger(__name__)

# Valid stages this module will write verdicts for.
_VERDICT_STAGES = frozenset(["CRITIQUE", "REVIEW"])


def compute_plan_hash(plan_path: Path | str) -> str | None:
    """Compute the sha256 of a plan file with normalized line endings.

    Returns ``"sha256:<hex>"`` on success, None on failure.

    The hash covers:
      - The full UTF-8 encoded bytes of the file.
      - Including YAML frontmatter.
      - After CRLF/CR -> LF normalization only.

    Whitespace inside prose and frontmatter values is NOT normalized — any
    such edit is assumed to be a meaningful plan change.
    """
    try:
        path = Path(plan_path)
        raw = path.read_bytes()
    except Exception as e:
        logger.debug(f"sdlc_verdict: compute_plan_hash read failed: {e}")
        return None

    # Normalize line endings: CRLF -> LF, then stray CR -> LF
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    digest = hashlib.sha256(normalized).hexdigest()
    return f"sha256:{digest}"


def _compute_artifact_hash(stage: str, issue_number: int | None) -> str | None:
    """Compute the artifact hash for a stage.

    CRITIQUE → sha256 of the plan file.
    REVIEW   → None (REVIEW non-determinism is handled by G4, not G5).
    """
    if stage != "CRITIQUE":
        return None
    if issue_number is None:
        return None
    plan_path = _find_plan_path(issue_number)
    if plan_path is None:
        return None
    return compute_plan_hash(plan_path)


def record_verdict(
    session,
    stage: str,
    verdict: str,
    blockers: int | None = None,
    tech_debt: int | None = None,
    issue_number: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Record a verdict for a stage on a session's stage_states.

    This is the sole writer of the ``_verdicts`` subkey. Uses
    ``tools.stage_states_helpers.update_stage_states`` for safe concurrent
    write semantics.

    Args:
        session: AgentSession to write to. Must have ``stage_states`` and
            ``save()``.
        stage: ``"CRITIQUE"`` or ``"REVIEW"``.
        verdict: Free-form verdict string from the skill output (e.g.,
            ``"NEEDS REVISION"``, ``"READY TO BUILD (no concerns)"``,
            ``"CHANGES REQUESTED"``, ``"APPROVED"``).
        blockers: Optional blocker count (REVIEW only).
        tech_debt: Optional tech-debt count (REVIEW only).
        issue_number: Optional issue number used to compute CRITIQUE's
            artifact_hash. Without it, ``artifact_hash`` is None.
        now: Optional timestamp for testability. Defaults to current UTC.

    Returns:
        The written verdict record on success, or ``{}`` on any failure.
    """
    if stage not in _VERDICT_STAGES:
        logger.debug(f"sdlc_verdict: unknown stage {stage!r}")
        return {}
    if not isinstance(verdict, str) or not verdict.strip():
        logger.debug("sdlc_verdict: empty or non-string verdict")
        return {}
    if session is None:
        return {}

    recorded_at = (now or datetime.now(UTC)).isoformat()
    artifact_hash = _compute_artifact_hash(stage, issue_number)

    record: dict = {
        "verdict": verdict,
        "recorded_at": recorded_at,
        "artifact_hash": artifact_hash,
    }
    if stage == "REVIEW":
        if blockers is not None:
            record["blockers"] = int(blockers)
        if tech_debt is not None:
            record["tech_debt"] = int(tech_debt)

    def _apply(states: dict) -> dict:
        verdicts = states.setdefault("_verdicts", {})
        if not isinstance(verdicts, dict):
            verdicts = {}
            states["_verdicts"] = verdicts
        verdicts[stage] = record
        return states

    try:
        from tools.stage_states_helpers import update_stage_states

        ok = update_stage_states(session, _apply)
    except Exception as e:
        logger.debug(f"sdlc_verdict: update_stage_states invocation failed: {e}")
        return {}

    if not ok:
        return {}
    return dict(record)


def get_verdict(session, stage: str) -> dict:
    """Read the most recent verdict record for a stage.

    Returns ``{}`` if no verdict is recorded or on any error.
    """
    if stage not in _VERDICT_STAGES:
        return {}
    if session is None:
        return {}

    try:
        raw = getattr(session, "stage_states", None)
        if not raw:
            return {}
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return {}

        verdicts = data.get("_verdicts") or {}
        record = verdicts.get(stage)
        if isinstance(record, dict):
            return dict(record)
        if isinstance(record, str):
            # Legacy shape — bare verdict string.
            return {"verdict": record}
        return {}
    except Exception as e:
        logger.debug(f"sdlc_verdict: get_verdict failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_record(args) -> dict:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        return {}
    return record_verdict(
        session,
        stage=args.stage.upper(),
        verdict=args.verdict,
        blockers=args.blockers,
        tech_debt=args.tech_debt,
        issue_number=args.issue_number,
    )


def _cli_get(args) -> dict:
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number)
    if session is None:
        return {}
    return get_verdict(session, args.stage.upper())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record or retrieve SDLC critique/review verdicts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    rec = subparsers.add_parser("record", help="Record a verdict")
    rec.add_argument("--stage", required=True, help="CRITIQUE or REVIEW")
    rec.add_argument("--verdict", required=True, help="Verdict string (free form)")
    rec.add_argument("--blockers", type=int, default=None)
    rec.add_argument("--tech-debt", dest="tech_debt", type=int, default=None)
    rec.add_argument("--session-id", default=None)
    rec.add_argument("--issue-number", type=int, default=None)
    rec.set_defaults(func=_cli_record)

    gt = subparsers.add_parser("get", help="Retrieve a verdict")
    gt.add_argument("--stage", required=True, help="CRITIQUE or REVIEW")
    gt.add_argument("--session-id", default=None)
    gt.add_argument("--issue-number", type=int, default=None)
    gt.set_defaults(func=_cli_get)

    args = parser.parse_args()

    try:
        result = args.func(args)
    except Exception as e:
        logger.debug(f"sdlc_verdict: CLI {args.command} failed: {e}")
        result = {}

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
