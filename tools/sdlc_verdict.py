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
        # Optional REVIEW multi-judge side-fields (only present when caller
        # passed judges= and consensus= kwargs to record_verdict). Both are
        # written in the SAME single record_verdict call — single-writer
        # invariant preserved.
        "_judges": [
            {"judge_id": "code-quality", "verdict": "APPROVED", "blockers": 0,
             "tech_debt": 0, "confidence": 0.9, "reasoning_summary": "...",
             "review_url": "..."},
            ...
        ],
        "_consensus": {
            "rule": "any-blocker-wins", "k": 2, "n": 2,
            "mean_confidence": 0.85, "blocker_aggregation": "max",
            "tied": False, "decided_at": "2026-04-18T12:34:56+00:00",
        },
    }

Multi-judge consensus: ``record_verdict`` accepts optional ``judges`` and
``consensus`` kwargs at REVIEW. The Review skill computes consensus in the
parent (via ``agent.sdlc_review_consensus.compute_consensus``) and makes ONE
``record_verdict`` call. CRITIQUE rejects either kwarg — its internal critics
aggregate before recording. See
``docs/plans/multi-judge-consensus-gates.md``.

Ownership gate (issue #1735): when ``--issue-number N`` is explicitly passed to
the CLI ``record`` subcommand, the resolved session is verified to own issue N
via ``session_owns_issue()`` in ``tools._sdlc_utils``. If the check fails (the
resolved session belongs to a different issue — the artifact-divert residual
case), the CLI exits 1 with a stderr diagnostic and writes nothing. The gate
does not fire when ``--issue-number`` is omitted (bridge PM sessions using env-
var resolution are unaffected).

Graceful failure: every function returns ``{}`` on error. Missing Redis, bad
input, malformed sessions, malformed ``judges`` payload — none of these crash
the caller, and a malformed payload never produces a partial write. Skills
rely on this: a verdict record failure must never block a critique/review
from finishing.

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
from tools._sdlc_utils import normalize_verdict
from tools._sdlc_utils import session_owns_issue as _session_owns_issue

logger = logging.getLogger(__name__)

# Valid stages this module will write verdicts for.
_VERDICT_STAGES = frozenset(["CRITIQUE", "REVIEW"])


class OwnershipError(Exception):
    """Raised when --issue-number N is passed but the resolved session does not
    own issue N. Prevents a silent artifact divert to the wrong session.
    """


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


# Required keys on each per-judge dict passed via the ``judges`` kwarg.
_REQUIRED_JUDGE_KEYS = ("judge_id", "verdict", "blockers")


def _validate_judges_payload(judges: list) -> bool:
    """Validate every per-judge dict has the required keys + types.

    Returns True if all dicts are well-formed; False otherwise. Mirrors the
    graceful-failure contract: caller returns ``{}`` on a False without
    writing a partial record.
    """
    if not isinstance(judges, list):
        return False
    for j in judges:
        if not isinstance(j, dict):
            return False
        for key in _REQUIRED_JUDGE_KEYS:
            if key not in j:
                return False
        if not isinstance(j["judge_id"], str) or not j["judge_id"].strip():
            return False
        if not isinstance(j["verdict"], str):
            return False
        if not isinstance(j["blockers"], int):
            return False
    return True


def record_verdict(
    session,
    stage: str,
    verdict: str,
    blockers: int | None = None,
    tech_debt: int | None = None,
    issue_number: int | None = None,
    now: datetime | None = None,
    judges: list | None = None,
    consensus: dict | None = None,
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
        judges: Optional list of per-judge dicts (REVIEW multi-judge only).
            Each dict must have ``judge_id`` (str), ``verdict`` (str),
            ``blockers`` (int). Optional keys: ``tech_debt``, ``confidence``,
            ``reasoning_summary``, ``review_url``. Persisted as
            ``_verdicts[stage]._judges`` side-field.
        consensus: Optional consensus metadata dict (REVIEW multi-judge only).
            Persisted as ``_verdicts[stage]._consensus`` side-field. Caller
            (typically ``do-pr-review`` SKILL) computes via
            :func:`agent.sdlc_review_consensus.compute_consensus`.

    Returns:
        The written verdict record on success, or ``{}`` on any failure.

    Multi-judge semantics:
        - ``judges`` and ``consensus`` are only meaningful at stage ``REVIEW``.
          CRITIQUE rejects either kwarg (its internal critics aggregate
          before recording).
        - When provided, both kwargs are persisted in the same single
          ``update_stage_states`` call as the scalar — preserving the
          single-writer invariant.
        - Malformed ``judges`` payload (missing required key, wrong type) →
          return ``{}`` with no partial write.
    """
    if stage not in _VERDICT_STAGES:
        logger.debug(f"sdlc_verdict: unknown stage {stage!r}")
        return {}
    if not isinstance(verdict, str) or not verdict.strip():
        logger.debug("sdlc_verdict: empty or non-string verdict")
        return {}
    if session is None:
        return {}

    # Multi-judge side-fields are REVIEW-only. CRITIQUE has its own internal
    # aggregation pattern (do-plan-critique) and must not gain _judges.
    if stage != "REVIEW" and (judges is not None or consensus is not None):
        logger.debug(f"sdlc_verdict: judges/consensus only valid at REVIEW, got stage={stage!r}")
        return {}

    if judges is not None and not _validate_judges_payload(judges):
        logger.debug("sdlc_verdict: malformed judges payload — refusing partial write")
        return {}

    # Normalize the verdict to canonical space-separated uppercase form (#1638).
    # This is the single write boundary — all stored verdicts are canonical.
    raw_verdict = verdict
    verdict = normalize_verdict(verdict)
    if verdict != raw_verdict:
        logger.debug(f"sdlc_verdict: normalized verdict {raw_verdict!r} -> {verdict!r}")

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
        # Side-fields: only attached when caller passed them. Single-judge
        # callers (e.g. /do-plan-critique single-judge legacy path) pass
        # neither and the persisted shape is bit-identical to today.
        if judges is not None:
            record["_judges"] = list(judges)
        if consensus is not None:
            record["_consensus"] = dict(consensus)

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
    session = _find_session(session_id=args.session_id, issue_number=args.issue_number, ensure=True)
    if session is None:
        return {}
    # Ownership guard: when --issue-number N is passed, the resolved session must
    # own issue N or we refuse the write to prevent a silent artifact divert.
    if args.issue_number is not None and not _session_owns_issue(session, args.issue_number):
        session_id_val = getattr(session, "session_id", "<unknown>")
        raise OwnershipError(
            f"Recorder ownership guard: session '{session_id_val}' does not own"
            f" issue #{args.issue_number}; refusing write to prevent divert"
        )
    judges = None
    consensus = None
    if getattr(args, "judges_json", None):
        try:
            judges = json.loads(args.judges_json)
        except Exception as e:
            logger.debug(f"sdlc_verdict: --judges-json decode failed: {e}")
            return {}
    if getattr(args, "consensus_json", None):
        try:
            consensus = json.loads(args.consensus_json)
        except Exception as e:
            logger.debug(f"sdlc_verdict: --consensus-json decode failed: {e}")
            return {}
    return record_verdict(
        session,
        stage=args.stage.upper(),
        verdict=args.verdict,
        blockers=args.blockers,
        tech_debt=args.tech_debt,
        issue_number=args.issue_number,
        judges=judges,
        consensus=consensus,
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
    rec.add_argument(
        "--judges-json",
        dest="judges_json",
        default=None,
        help=(
            "JSON-encoded list of per-judge dicts (REVIEW multi-judge only). "
            "Each dict must have judge_id (str), verdict (str), blockers (int)."
        ),
    )
    rec.add_argument(
        "--consensus-json",
        dest="consensus_json",
        default=None,
        help=(
            "JSON-encoded consensus metadata dict (REVIEW multi-judge only). "
            "Typically the 'consensus' field returned by "
            "agent.sdlc_review_consensus.compute_consensus."
        ),
    )
    rec.set_defaults(func=_cli_record)

    gt = subparsers.add_parser("get", help="Retrieve a verdict")
    gt.add_argument("--stage", required=True, help="CRITIQUE or REVIEW")
    gt.add_argument("--session-id", default=None)
    gt.add_argument("--issue-number", type=int, default=None)
    gt.set_defaults(func=_cli_get)

    args = parser.parse_args()

    failed = False
    try:
        result = args.func(args)
    except Exception as e:
        # Load-bearing tool: failures must be loud so /sdlc operators see them.
        # Stdout still emits `{}` so existing callers parsing JSON don't break;
        # the non-zero exit is the loud signal.
        logger.debug(f"sdlc_verdict: CLI {args.command} failed: {e}")
        print(f"sdlc_verdict: CLI {args.command} failed: {e}", file=sys.stderr)
        result = {}
        failed = True

    print(json.dumps(result))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
