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
           --verdict "NEEDS REVISION" --issue-number 1040 --run-id <hex>
       python -m tools.sdlc_verdict record --stage REVIEW \\
           --verdict "CHANGES REQUESTED" --blockers 2 --issue-number 1040 --run-id <hex>
       python -m tools.sdlc_verdict get --stage CRITIQUE --issue-number 1040

   ``record`` is state-mutating and REQUIRES ``--run-id`` (issue #2003) — the
   run identity emitted by ``sdlc-tool session-ensure``. Missing flag is a
   named non-zero error (``RUN_ID_REQUIRED``); a foreign run_id refuses the
   write with an ``ISSUE_LOCKED`` diagnostic. ``get`` takes no run-id.

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
  - G5 comparisons use ``compute_plan_body_hash`` which strips ONLY the
    ``revision_applied:`` frontmatter line before hashing. This means that
    the SDLC router's cache guard is NOT busted when a critique skill writes
    ``revision_applied: true`` after applying its own NEEDS REVISION feedback.
    All other frontmatter keys (e.g. ``status:``, ``type:``) still bust the
    G5 cache; only ``revision_applied:`` is exempt.
  - ``compute_plan_hash`` (the full-bytes variant, including ``revision_applied:``)
    is retained for callers that explicitly want the complete file fingerprint.
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

from tools._sdlc_run_identity import heal_missing_run_id, maybe_heal_after_write
from tools._sdlc_utils import find_plan_path as _find_plan_path
from tools._sdlc_utils import find_session as _find_session
from tools._sdlc_utils import normalize_verdict
from tools.sdlc_review_finalize import _cli_finalize, _cli_selfcheck

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


def compute_plan_body_hash(plan_path: Path | str) -> str | None:
    """Compute the sha256 of a plan file with ``revision_applied:`` stripped.

    Returns ``"sha256:<hex>"`` on success, None on failure.

    This is the hash used by G5 (``guard_g5_artifact_hash_cache``) so that
    writing ``revision_applied: true`` after a NEEDS REVISION round-trip does
    NOT bust the critique-verdict cache. Only the ``revision_applied:`` key is
    stripped — all other frontmatter keys (e.g. ``status:``, ``type:``) still
    influence the hash and will bust the G5 cache.

    The hash covers:
      - The UTF-8 encoded bytes of the file after removing the
        ``revision_applied:`` line from the YAML frontmatter block.
      - After CRLF/CR -> LF normalization (same as ``compute_plan_hash``).

    Edge cases:
      - No frontmatter: hash the whole file unchanged.
      - Malformed/unterminated frontmatter (no closing ``---``): hash the
        whole file unchanged (conservative degradation).
      - ``revision_applied:`` absent: hash equals ``compute_plan_hash`` result.
      - ``revision_applied: false`` and ``revision_applied:`` absent produce
        the same hash (both hash a body without the key).
    """
    import re

    try:
        path = Path(plan_path)
        raw = path.read_bytes()
    except Exception as e:
        logger.debug(f"sdlc_verdict: compute_plan_body_hash read failed: {e}")
        return None

    # Normalize line endings: CRLF -> LF, then stray CR -> LF
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    text = normalized.decode("utf-8", errors="replace")

    # Attempt to strip revision_applied: from the frontmatter block.
    # Frontmatter is delimited by leading "---\n" ... "---\n" (or "---\n" at EOF).
    if text.startswith("---\n"):
        closing = text.find("\n---\n", 4)
        if closing == -1:
            # Unterminated frontmatter: degrade to hashing whole file.
            pass
        else:
            # Found closing delimiter. Strip the revision_applied: line only.
            fm_end = closing + len("\n---\n")
            frontmatter_block = text[4:closing]  # content between the two ---
            body = text[fm_end:]

            # Remove the revision_applied: line (any truthy/falsy value).
            stripped_fm = re.sub(
                r"^revision_applied:\s*\S+\s*$", "", frontmatter_block, flags=re.MULTILINE
            )
            # Rebuild; remove a stray blank line if the whole key was removed.
            stripped_fm = re.sub(r"\n{2,}", "\n", stripped_fm).strip("\n")

            if stripped_fm:
                text = f"---\n{stripped_fm}\n---\n{body}"
            else:
                # Frontmatter was only revision_applied: — drop the whole block.
                text = body

    normalized_bytes = text.encode("utf-8")
    digest = hashlib.sha256(normalized_bytes).hexdigest()
    return f"sha256:{digest}"


def _compute_artifact_hash(stage: str, issue_number: int | None) -> str | None:
    """Compute the artifact hash for a stage.

    CRITIQUE → sha256 of the plan file body (revision_applied-stripped).
    REVIEW   → None (REVIEW non-determinism is handled by G4, not G5).
    """
    if stage != "CRITIQUE":
        return None
    if issue_number is None:
        return None
    plan_path = _find_plan_path(issue_number)
    if plan_path is None:
        return None
    return compute_plan_body_hash(plan_path)


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
        from tools._sdlc_utils import is_pipeline_ledger
        from tools.stage_states_helpers import update_stage_states

        # ``session`` may be an AgentSession (field="stage_states", the
        # historical shape) or a PipelineLedger (field="stage_states_json"
        # -- issue #2012 task 2, the CLI's issue-keyed write path). Detected
        # via isinstance so this single writer function serves both backing
        # stores without misclassifying an unspecialized MagicMock() double.
        field = "stage_states_json" if is_pipeline_ledger(session) else "stage_states"
        ok = update_stage_states(session, _apply, field=field)
    except Exception as e:
        logger.debug(f"sdlc_verdict: update_stage_states invocation failed: {e}")
        return {}

    if not ok:
        return {}
    return dict(record)


def get_verdict(session, stage: str) -> dict:
    """Read the most recent verdict record for a stage.

    ``session`` may be an AgentSession or a PipelineLedger (issue #2012
    task 2) -- detected the same way as :func:`record_verdict`.

    Returns ``{}`` if no verdict is recorded or on any error.
    """
    if stage not in _VERDICT_STAGES:
        return {}
    if session is None:
        return {}

    try:
        from tools._sdlc_utils import is_pipeline_ledger

        field = "stage_states_json" if is_pipeline_ledger(session) else "stage_states"
        raw = getattr(session, field, None)
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
    """Record a verdict against the issue-keyed PipelineLedger (issue #2012 task 2).

    This is a WRITER: there is no session left to resolve, so the lease
    for ``args.issue_number`` under ``args.run_id`` is the sole source of
    authorization. Missing/foreign/repo-less leases all hard-fail loudly
    via ``OwnershipError`` (caught by ``main()``, which prints the message
    to stderr and exits 1) -- there is nothing left to silently no-op to.
    """
    from tools._sdlc_utils import resolve_ledger_lease, revalidate_ledger_lease

    target_repo, lease_error = resolve_ledger_lease(args.issue_number, args.run_id)
    if lease_error is not None:
        reason = lease_error.get("reason", "LEASE_ABSENT")
        if reason == "ISSUE_LOCKED":
            raise OwnershipError(
                f"ISSUE_LOCKED: issue lock held by a foreign run "
                f"(run_id={lease_error.get('owner_run_id')}, "
                f"session={lease_error.get('owner_session_id')}); refusing verdict write"
            )
        raise OwnershipError(
            f"LEASE_ABSENT: no live issue lease for issue #{args.issue_number} "
            f"owned by run_id={args.run_id!r}; run `sdlc-tool session-ensure` first."
        )
    if not target_repo:
        raise OwnershipError(
            f"TARGET_REPO_MISSING: issue lease for issue #{args.issue_number} has no "
            "pinned target_repo; refusing to write a PipelineLedger record with a "
            "None key component."
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

    # TOCTOU close (Risk 5): re-validate the lease non-peek immediately
    # before the actual write, never trusting the earlier peek across the
    # gap between resolve and write.
    if not revalidate_ledger_lease(args.issue_number, args.run_id, target_repo):
        raise OwnershipError(
            f"ISSUE_LOCKED: lease for issue #{args.issue_number} was taken by a "
            "foreign run between resolve and write; refusing verdict write"
        )

    from agent.pipeline_ledger import PipelineLedger

    ledger = PipelineLedger.get_or_create(target_repo, args.issue_number)
    return record_verdict(
        ledger,
        stage=args.stage.upper(),
        verdict=args.verdict,
        blockers=args.blockers,
        tech_debt=args.tech_debt,
        issue_number=args.issue_number,
        judges=judges,
        consensus=consensus,
    )


def _cli_get(args) -> dict:
    """Read a verdict — issue-keyed ledger first, with a retained session
    fallback for pre-cutover records (issue #2012 task 2).

    When ``--issue-number`` is given, delegates the resolution to
    ``tools.sdlc_stage_query._resolve_issue_record`` -- the SOLE place that
    performs the ledger-first/env-fallback/session-fallback dance (Risk 5,
    reader side), rather than duplicating it here. That function returns
    ``None`` when ``target_repo`` cannot be resolved at all -- the defined
    empty outcome ``{}``, never a phantom ``PipelineLedger[(None, issue)]``
    read.

    Without ``--issue-number``, this stays the plain session lookup
    (``--session-id`` / env-var resolution) -- unaffected by the ledger
    migration since there's no issue number to key a ledger read on.
    """
    if args.issue_number is not None:
        from tools.sdlc_stage_query import _resolve_issue_record

        record = _resolve_issue_record(args.issue_number)
        if record is None:
            return {}
        return get_verdict(record, args.stage.upper())

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
    rec.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run identity emitted by `sdlc-tool session-ensure` (issue #2003). "
            "REQUIRED for this state-mutating subcommand; missing -> RUN_ID_REQUIRED."
        ),
    )
    rec.set_defaults(func=_cli_record, requires_run_id=True)

    gt = subparsers.add_parser("get", help="Retrieve a verdict")
    gt.add_argument("--stage", required=True, help="CRITIQUE or REVIEW")
    gt.add_argument("--session-id", default=None)
    gt.add_argument("--issue-number", type=int, default=None)
    gt.set_defaults(func=_cli_get)

    fin = subparsers.add_parser(
        "finalize",
        help=(
            "Atomically record a REVIEW verdict + head_sha trailer + REVIEW "
            "completed marker, then verify all three persisted (#2193)"
        ),
    )
    fin.add_argument("--pr", type=int, required=True, help="PR number (head_sha trailer source)")
    fin.add_argument("--issue-number", type=int, required=True)
    fin.add_argument(
        "--verdict",
        required=True,
        help="Verdict string (free form); the head_sha trailer is appended if absent",
    )
    fin.add_argument("--blockers", type=int, default=None)
    fin.add_argument("--tech-debt", dest="tech_debt", type=int, default=None)
    fin.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run identity emitted by `sdlc-tool session-ensure` (issue #2003). "
            "REQUIRED for this state-mutating subcommand; missing -> RUN_ID_REQUIRED."
        ),
    )
    fin.set_defaults(func=_cli_finalize, requires_run_id=True)

    sc = subparsers.add_parser(
        "selfcheck",
        help=(
            "Read-only readback of REVIEW verdict/trailer/marker persistence "
            "(#2193). Always exits 0 -- branch on the JSON `ok` field."
        ),
    )
    sc.add_argument("--pr", type=int, required=True)
    sc.add_argument("--issue-number", type=int, required=True)
    sc.set_defaults(func=_cli_selfcheck)

    args = parser.parse_args()

    # Run-identity self-heal (issue #2144): a resumed pipeline turn loses the
    # run_id from context. Re-establish identity from the environment instead of
    # silently refusing; only a genuinely unhealable state (foreign live lease,
    # no issue-number) keeps the RUN_ID_REQUIRED refusal.
    requires_run_id = getattr(args, "requires_run_id", False)
    healed_at_gate = False
    if requires_run_id and not getattr(args, "run_id", None):
        healed = heal_missing_run_id(getattr(args, "issue_number", None), "verdict")
        if not healed:
            print(
                "sdlc_verdict: RUN_ID_REQUIRED — state-mutating calls must pass "
                "--run-id (emitted by `sdlc-tool session-ensure`).",
                file=sys.stderr,
            )
            print(json.dumps({"error": "RUN_ID_REQUIRED"}))
            sys.exit(2)
        args.run_id = healed
        healed_at_gate = True

    failed = False
    try:
        result = args.func(args)
    except OwnershipError as e:
        # _cli_record signals a run-identity refusal (LEASE_ABSENT / stale
        # ISSUE_LOCKED) by raising OwnershipError with the reason as the leading
        # message token. Heal once and retry under the re-established id.
        reason = str(e).split(":", 1)[0].strip()
        healed = None
        if requires_run_id and not healed_at_gate:
            healed = maybe_heal_after_write(
                {"reason": reason},
                getattr(args, "run_id", None),
                getattr(args, "issue_number", None),
                "verdict",
            )
        if healed:
            args.run_id = healed
            try:
                result = args.func(args)
            except Exception as e2:
                logger.debug(f"sdlc_verdict: CLI {args.command} failed after heal: {e2}")
                print(f"sdlc_verdict: CLI {args.command} failed: {e2}", file=sys.stderr)
                result = {}
                failed = True
        else:
            logger.debug(f"sdlc_verdict: CLI {args.command} failed: {e}")
            print(f"sdlc_verdict: CLI {args.command} failed: {e}", file=sys.stderr)
            result = {}
            failed = True
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
