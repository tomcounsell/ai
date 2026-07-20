"""CLI tool for writing SDLC pipeline metadata to the issue-keyed PipelineLedger.

Invoked by SDLC skills (do-plan-critique, do-plan, do-build) to set or clear
plan-level metadata flags without depending on bridge hooks. Most keys land
in the ledger's ``stage_states_json`` dict; field-backed keys write a
``PipelineLedger`` attribute directly (issue #2012 task 2 -- there is no
session left in this path).

Whitelisted keys and their types:
  plan_revising          bool   — set by critique on NEEDS REVISION / MAJOR REWORK /
                                  READY TO BUILD (with concerns); cleared by plan after
                                  revision commit. Consumed by guard G7 in sdlc_router.
  plan_hash_at_build_start  str — git commit hash of the plan doc at build start.
                                  Recorded by do-build Step 7; verified at Step 21.
  pr_number               int  — FIELD-backed (#2003 T1.7): writes
                                  ``PipelineLedger.pr_number`` via ``ledger.save()``.
                                  This command is the SINGLE writer of that field —
                                  /do-build invokes it at PR creation, and it is the
                                  out-of-band operator recovery path. ``_compute_meta``
                                  (stage-query) reads the field first, then falls back
                                  to read-only gh recovery rungs. Must be a positive
                                  int; non-positive/non-numeric values exit 2.

Unknown keys are rejected with exit 2 — the whitelist is intentional and must be
explicit so stale meta keys don't accumulate silently.

Usage:
    python -m tools.sdlc_meta_set --key plan_revising --value true \
        --issue-number 1302 --run-id <hex>
    python -m tools.sdlc_meta_set --key plan_revising --value false \
        --issue-number 1302 --run-id <hex>
    python -m tools.sdlc_meta_set --key plan_hash_at_build_start --value abc123 \
        --issue-number 1302 --run-id <hex>
    python -m tools.sdlc_meta_set --help

Run identity (issue #2003): this tool is state-mutating and REQUIRES
``--run-id`` (the run identity emitted by ``sdlc-tool session-ensure``).
Missing flag is a named non-zero error (``RUN_ID_REQUIRED``) — no mint, no
adopt. ``--issue-number`` is likewise REQUIRED for a real write: the ledger
key is ``(target_repo, issue_number)`` and there is no session left to
derive an issue number from.

Degradation contract (issue #2012 task 2 — rebuilt around the lease, not a
session): a missing/foreign/repo-less lease is now LOUD (exit 1) — there is
no session to fail to resolve to anymore, so silence is never correct.

Exit codes:
    0 — success
    1 — lease absent, foreign (ISSUE_LOCKED), or repo-less (TARGET_REPO_MISSING);
        write refused
    2 — invalid arguments (unknown key, missing required args, missing --run-id)

Output:
    {} on a non-lease write failure (e.g. Redis hiccup on the actual write)
    {"key": "plan_revising", "value": true} on success
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from tools._sdlc_run_identity import heal_missing_run_id, maybe_heal_after_write
from tools._sdlc_utils import resolve_ledger_lease, revalidate_ledger_lease

logger = logging.getLogger(__name__)

# Whitelisted keys and their storage/coercion rules.
# Maps key name -> (storage_target, coerce_fn).
# Targets with a leading underscore are stage_states meta keys (written via
# update_stage_states); targets without one are AgentSession FIELDS (written
# via session.save()). `pr_number` is field-backed (#2003 T1.7): this tool is
# the single writer of AgentSession.pr_number — used by /do-build at PR
# creation and by out-of-band operator recovery alike.
_KEY_REGISTRY: dict[str, tuple[str, type]] = {
    "plan_revising": ("_plan_revising", bool),
    "plan_hash_at_build_start": ("_plan_hash_at_build_start", str),
    "pr_number": ("pr_number", int),
}

_BOOL_TRUE_VALUES = frozenset(["true", "1", "yes", "on"])
_BOOL_FALSE_VALUES = frozenset(["false", "0", "no", "off"])


def _coerce_bool(value: str) -> bool:
    """Coerce a string value to bool. Raises ValueError on unrecognized input."""
    if value.lower() in _BOOL_TRUE_VALUES:
        return True
    if value.lower() in _BOOL_FALSE_VALUES:
        return False
    raise ValueError(
        f"Cannot coerce {value!r} to bool. "
        f"Accepted true values: {sorted(_BOOL_TRUE_VALUES)}. "
        f"Accepted false values: {sorted(_BOOL_FALSE_VALUES)}."
    )


def _coerce_int(value: str) -> int:
    """Coerce a string to a positive int. Raises ValueError on non-numeric / non-positive."""
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Cannot coerce {value!r} to int.")
    if coerced <= 0:
        raise ValueError(f"Value {coerced!r} must be a positive integer.")
    return coerced


def _coerce_value(key: str, raw_value: str):
    """Coerce a raw string value to the expected type for the given key.

    Returns the coerced value, or raises ValueError on type mismatch.
    """
    _, target_type = _KEY_REGISTRY[key]
    if target_type is bool:
        return _coerce_bool(raw_value)
    if target_type is int:
        return _coerce_int(raw_value)
    if target_type is str:
        return str(raw_value)
    # Future types would go here
    return raw_value


def write_meta(
    key: str,
    value: str,
    session_id: str | None = None,
    issue_number: int | None = None,
    run_id: str | None = None,
) -> dict:
    """Write a whitelisted metadata key to the issue-keyed PipelineLedger.

    Meta keys go to ``stage_states_json["_<key>"]`` via
    ``update_stage_states()``; field-backed keys (``pr_number``) write the
    ``PipelineLedger`` attribute directly and ``ledger.save()`` (#2003 T1.7
    single-writer, re-pointed at the ledger by issue #2012 task 2).

    There is no session in this path: authorization is decided SOLELY by
    the run_id-keyed issue lease (``models.session_lifecycle.touch_issue_lock``).
    A missing/foreign/repo-less lease all hard-fail loudly (returned as an
    error-shaped dict; ``main()`` surfaces it via a non-zero exit) — there
    is nothing left to silently no-op to.

    Args:
        key: Whitelisted key name (e.g., "plan_revising").
        value: Raw string value — will be coerced to the key's type.
        session_id: Unused — accepted only for CLI-flag backward compat.
        issue_number: The GitHub issue number. Required for a real write
            (the ledger key is ``(target_repo, issue_number)``).
        run_id: The caller's run identity (the CLI's ``--run-id``).

    Returns:
        Dict with key/value on success, an error-shaped dict
        (``{"reason": "LEASE_ABSENT"|"ISSUE_LOCKED"|"TARGET_REPO_MISSING", ...}``)
        when the lease is missing/foreign/repo-less, empty dict on any other
        (non-lease) write failure.
    """
    del session_id  # unused -- CLI-flag backward compat only

    if key not in _KEY_REGISTRY:
        logger.debug(f"sdlc_meta_set: unknown key {key!r}")
        return {}

    try:
        coerced = _coerce_value(key, value)
    except ValueError as e:
        logger.debug(f"sdlc_meta_set: value coercion failed for key {key!r}: {e}")
        return {}

    target_repo, lease_error = resolve_ledger_lease(issue_number, run_id)
    if lease_error is not None:
        logger.debug(
            "sdlc_meta_set: lease invalid for issue #%s (reason=%s) -- refusing meta write",
            issue_number,
            lease_error.get("reason"),
        )
        return dict(lease_error)
    if not target_repo:
        logger.debug(
            "sdlc_meta_set: issue #%s lease has no pinned target_repo -- refusing meta write",
            issue_number,
        )
        return {"reason": "TARGET_REPO_MISSING"}

    from agent.pipeline_ledger import PipelineLedger

    ledger = PipelineLedger.get_or_create(target_repo, issue_number)
    storage_target, _ = _KEY_REGISTRY[key]

    # TOCTOU close (Risk 5): re-validate the lease non-peek immediately
    # before the actual write, never trusting the earlier peek across the
    # gap between resolve and write.
    if not revalidate_ledger_lease(issue_number, run_id, target_repo):
        logger.debug(
            "sdlc_meta_set: lease for issue #%s was taken by a foreign run between "
            "resolve and write -- refusing meta write",
            issue_number,
        )
        return {"reason": "ISSUE_LOCKED"}

    # Field-backed keys (#2003 T1.7): write the PipelineLedger attribute
    # directly — ONE writer code path for both /do-build's PR-creation write
    # and out-of-band operator recovery. No stage_states meta key is written.
    if not storage_target.startswith("_"):
        try:
            setattr(ledger, storage_target, coerced)
            ledger.save()
        except Exception as e:
            logger.debug(f"sdlc_meta_set: field write failed for key {key!r}: {e}")
            return {}
        return {"key": key, "value": coerced}

    def _apply_update(states: dict) -> dict:
        states[storage_target] = coerced
        return states

    try:
        from tools.stage_states_helpers import update_stage_states

        success = update_stage_states(ledger, _apply_update, field="stage_states_json")
        if not success:
            logger.debug(f"sdlc_meta_set: update_stage_states returned False for key {key!r}")
            return {}
    except Exception as e:
        logger.debug(f"sdlc_meta_set: write_meta failed: {e}")
        return {}

    return {"key": key, "value": coerced}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write SDLC pipeline metadata to a PM session's stage_states",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--key",
        required=True,
        help=(
            f"Metadata key to set. Whitelisted keys: {sorted(_KEY_REGISTRY.keys())}. "
            "Unknown keys exit with code 2."
        ),
    )
    parser.add_argument(
        "--value",
        required=True,
        help="Value to set. Booleans accept true/false/1/0/yes/no.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="PM session ID (falls back to VALOR_SESSION_ID / AGENT_SESSION_ID env vars)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number (for local sessions without VALOR_SESSION_ID)",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run identity emitted by `sdlc-tool session-ensure` (issue #2003). "
            "REQUIRED for this state-mutating tool; missing -> RUN_ID_REQUIRED."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    # Run-identity self-heal (issue #2144): a resumed pipeline turn loses the
    # run_id from context. Re-establish identity from the environment rather
    # than silently refusing; only a genuinely unhealable state (foreign live
    # lease, no issue-number) keeps the RUN_ID_REQUIRED refusal.
    run_id = args.run_id
    healed_at_gate = False
    if not run_id:
        run_id = heal_missing_run_id(args.issue_number, "meta_set")
        if not run_id:
            print(
                "sdlc_meta_set: RUN_ID_REQUIRED — state-mutating calls must pass "
                "--run-id (emitted by `sdlc-tool session-ensure`).",
                file=sys.stderr,
            )
            print(json.dumps({"error": "RUN_ID_REQUIRED"}))
            sys.exit(2)
        healed_at_gate = True

    # Unknown key → exit 2 (invalid argument, not a runtime error)
    if args.key not in _KEY_REGISTRY:
        print(
            f"sdlc_meta_set: unknown key {args.key!r}. "
            f"Whitelisted keys: {sorted(_KEY_REGISTRY.keys())}",
            file=sys.stderr,
        )
        print("{}")
        sys.exit(2)

    # Invalid value for a known key → exit 2 (invalid argument). This catches
    # e.g. a non-positive / non-numeric pr_number before any session lookup so
    # garbage is never written.
    try:
        _coerce_value(args.key, args.value)
    except ValueError as e:
        print(f"sdlc_meta_set: invalid value for key {args.key!r}: {e}", file=sys.stderr)
        print("{}")
        sys.exit(2)

    result = write_meta(
        key=args.key,
        value=args.value,
        session_id=args.session_id,
        issue_number=args.issue_number,
        run_id=run_id,
    )
    # A stale run_id whose lease lapsed refuses with LEASE_ABSENT; heal once and
    # retry under the re-established id (at-most-once).
    if result.get("reason") in ("LEASE_ABSENT", "ISSUE_LOCKED") and not healed_at_gate:
        healed = maybe_heal_after_write(result, run_id, args.issue_number, "meta_set")
        if healed:
            run_id = healed
            result = write_meta(
                key=args.key,
                value=args.value,
                session_id=args.session_id,
                issue_number=args.issue_number,
                run_id=run_id,
            )
    print(json.dumps(result))
    reason = result.get("reason")
    if reason == "ISSUE_LOCKED":
        # Foreign run holds the issue lock — loud so the caller sees the
        # refused write instead of a silent {} no-op.
        print(
            f"sdlc_meta_set: ISSUE_LOCKED — issue lock held by a foreign run "
            f"(run_id={result.get('owner_run_id')}, "
            f"session={result.get('owner_session_id')}); write refused.",
            file=sys.stderr,
        )
        sys.exit(1)
    if reason == "LEASE_ABSENT":
        # Issue #2012 task 2: this REPLACES the old fail-soft no-session
        # exit 0 -- there is no session to fail to resolve to anymore, so
        # silence is never correct.
        print(
            f"sdlc_meta_set: LEASE_ABSENT — no live issue lease for issue "
            f"#{args.issue_number} owned by run_id={args.run_id!r}; run "
            "`sdlc-tool session-ensure` first. Write refused.",
            file=sys.stderr,
        )
        sys.exit(1)
    if reason == "TARGET_REPO_MISSING":
        print(
            f"sdlc_meta_set: TARGET_REPO_MISSING — the issue lease for issue "
            f"#{args.issue_number} has no pinned target_repo; refusing to write "
            "a PipelineLedger record with a None key component.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Otherwise exit 0 (fail-soft: a genuine (non-lease) write failure returns
    # {} but doesn't crash the skill)
    sys.exit(0)


if __name__ == "__main__":
    main()
