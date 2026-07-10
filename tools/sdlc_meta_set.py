"""CLI tool for writing SDLC pipeline metadata keys to a PM session's stage_states.

Invoked by SDLC skills (do-plan-critique, do-plan, do-build) to set or clear
plan-level metadata flags without depending on bridge hooks.

Whitelisted keys and their types:
  plan_revising          bool   — set by critique on NEEDS REVISION / MAJOR REWORK /
                                  READY TO BUILD (with concerns); cleared by plan after
                                  revision commit. Consumed by guard G7 in sdlc_router.
  plan_hash_at_build_start  str — git commit hash of the plan doc at build start.
                                  Recorded by do-build Step 7; verified at Step 21.
  pr_number               int  — PR number for an out-of-band PR the issue body
                                  never referenced. Consumed by _compute_meta as
                                  the primary pr_number resolution source so the
                                  router can route the PR to REVIEW/MERGE without
                                  a manual `/sdlc PR <n>`. Must be a positive int;
                                  non-positive/non-numeric values exit 2.

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
adopt. A foreign run_id refuses the write with an ``ISSUE_LOCKED``
diagnostic (exit 1).

Environment variables (checked in order if --session-id not provided):
    VALOR_SESSION_ID   — bridge-injected PM session ID
    AGENT_SESSION_ID   — alternative session ID env var

When no session ID is available (local Claude Code sessions), use --issue-number
to resolve the session by GitHub issue number.

Exit codes:
    0 — success, or fail-soft error (no session found, Redis down, etc.)
    1 — issue lock held by a foreign run (ISSUE_LOCKED; write refused)
    2 — invalid arguments (unknown key, missing required args, missing --run-id)

Output:
    {} on error (no session found, write failed, etc.)
    {"key": "plan_revising", "value": true} on success
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from tools._sdlc_utils import check_run_ownership, find_session

logger = logging.getLogger(__name__)

# Whitelisted keys and their storage/coercion rules.
# Maps key name -> ("_<internal_key>", coerce_fn)
_KEY_REGISTRY: dict[str, tuple[str, type]] = {
    "plan_revising": ("_plan_revising", bool),
    "plan_hash_at_build_start": ("_plan_hash_at_build_start", str),
    "pr_number": ("_pr_number", int),
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
    """Write a metadata key to stage_states["_<key>"] via update_stage_states().

    Run identity (issue #2003): when the resolved session has an issue
    context, the issue lock is peek-compared against ``run_id`` — a foreign
    live holder refuses the write and returns the ``ISSUE_LOCKED`` shape.
    Peek-only: meta-set never renews the lock (#1954 scope-narrowing
    preserved).

    Args:
        key: Whitelisted key name (e.g., "plan_revising").
        value: Raw string value — will be coerced to the key's type.
        session_id: Optional explicit session ID (falls back to env vars).
        issue_number: Optional issue number for local session resolution.
        run_id: The caller's run identity (the CLI's ``--run-id``).

    Returns:
        Dict with key/value on success, an ``ISSUE_LOCKED``-shaped dict when
        a foreign run holds the issue lock, empty dict on any other failure.
    """
    if key not in _KEY_REGISTRY:
        logger.debug(f"sdlc_meta_set: unknown key {key!r}")
        return {}

    try:
        coerced = _coerce_value(key, value)
    except ValueError as e:
        logger.debug(f"sdlc_meta_set: value coercion failed for key {key!r}: {e}")
        return {}

    session = find_session(session_id, issue_number=issue_number, ensure=True)
    if not session:
        return {}

    # Run-identity gate (issue #2003): refuse the write when a FOREIGN run
    # holds the issue lock.
    conflict = check_run_ownership(session, run_id, issue_number=issue_number)
    if conflict is not None:
        logger.debug(
            "sdlc_meta_set: issue lock held by a foreign run (run_id=%s, session=%s) "
            "-- refusing meta write",
            conflict.get("owner_run_id"),
            conflict.get("owner_session_id"),
        )
        return dict(conflict)

    internal_key, _ = _KEY_REGISTRY[key]

    def _apply_update(states: dict) -> dict:
        states[internal_key] = coerced
        return states

    try:
        from tools.stage_states_helpers import update_stage_states

        success = update_stage_states(session, _apply_update)
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

    # Run-identity gate (issue #2003): a state-mutating call without --run-id
    # exits non-zero with a NAMED error -- no mint, no adopt.
    if not args.run_id:
        print(
            "sdlc_meta_set: RUN_ID_REQUIRED — state-mutating calls must pass "
            "--run-id (emitted by `sdlc-tool session-ensure`).",
            file=sys.stderr,
        )
        print(json.dumps({"error": "RUN_ID_REQUIRED"}))
        sys.exit(2)

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
        run_id=args.run_id,
    )
    print(json.dumps(result))
    if result.get("reason") == "ISSUE_LOCKED":
        # Foreign run holds the issue lock — loud so the caller sees the
        # refused write instead of a silent {} no-op.
        print(
            f"sdlc_meta_set: ISSUE_LOCKED — issue lock held by a foreign run "
            f"(run_id={result.get('owner_run_id')}, "
            f"session={result.get('owner_session_id')}); write refused.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Otherwise exit 0 (fail-soft: runtime errors return {} but don't crash the skill)
    sys.exit(0)


if __name__ == "__main__":
    main()
