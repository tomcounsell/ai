"""CLI tool for writing SDLC pipeline metadata keys to a PM session's stage_states.

Invoked by SDLC skills (do-plan-critique, do-plan, do-build) to set or clear
plan-level metadata flags without depending on bridge hooks.

Whitelisted keys and their types:
  plan_revising          bool   — set by critique on NEEDS REVISION / MAJOR REWORK /
                                  READY TO BUILD (with concerns); cleared by plan after
                                  revision commit. Consumed by guard G7 in sdlc_router.
  plan_hash_at_build_start  str — git commit hash of the plan doc at build start.
                                  Recorded by do-build Step 7; verified at Step 21.

Unknown keys are rejected with exit 2 — the whitelist is intentional and must be
explicit so stale meta keys don't accumulate silently.

Usage:
    python -m tools.sdlc_meta_set --key plan_revising --value true --issue-number 1302
    python -m tools.sdlc_meta_set --key plan_revising --value false --issue-number 1302
    python -m tools.sdlc_meta_set --key plan_hash_at_build_start --value abc123 --issue-number 1302
    python -m tools.sdlc_meta_set --help

Environment variables (checked in order if --session-id not provided):
    VALOR_SESSION_ID   — bridge-injected PM session ID
    AGENT_SESSION_ID   — alternative session ID env var

When no session ID is available (local Claude Code sessions), use --issue-number
to resolve the session by GitHub issue number.

Exit codes:
    0 — success, or fail-soft error (no session found, Redis down, etc.)
    2 — invalid arguments (unknown key, missing required args)

Output:
    {} on error (no session found, write failed, etc.)
    {"key": "plan_revising", "value": true} on success
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Whitelisted keys and their storage/coercion rules.
# Maps key name -> ("_<internal_key>", coerce_fn)
_KEY_REGISTRY: dict[str, tuple[str, type]] = {
    "plan_revising": ("_plan_revising", bool),
    "plan_hash_at_build_start": ("_plan_hash_at_build_start", str),
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


def _coerce_value(key: str, raw_value: str):
    """Coerce a raw string value to the expected type for the given key.

    Returns the coerced value, or raises ValueError on type mismatch.
    """
    _, target_type = _KEY_REGISTRY[key]
    if target_type is bool:
        return _coerce_bool(raw_value)
    if target_type is str:
        return str(raw_value)
    # Future types would go here
    return raw_value


def _find_session(session_id: str | None, issue_number: int | None = None):
    """Find a PM AgentSession by explicit ID, env vars, or issue number.

    Resolution order:
    1. --session-id argument (if provided)
    2. VALOR_SESSION_ID env var
    3. AGENT_SESSION_ID env var
    4. --issue-number argument (primary path for local Claude Code sessions)

    Returns the session object or None.
    """
    resolved_id = (
        session_id or os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
    )
    if not resolved_id:
        if issue_number is not None:
            try:
                from tools._sdlc_utils import find_session_by_issue

                session = find_session_by_issue(issue_number)
                if session:
                    return session
            except Exception as e:
                logger.debug(f"sdlc_meta_set: issue-number lookup failed: {e}")
        logger.debug("sdlc_meta_set: no session ID available (no arg, no env vars, no issue)")
        return None

    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=resolved_id))
        if not sessions:
            logger.debug(f"sdlc_meta_set: no session found for ID {resolved_id!r}")
            return None
        # Prefer PM sessions (they own stage_states)
        for s in sessions:
            if getattr(s, "session_type", None) == "pm":
                return s
        return sessions[0]
    except Exception as e:
        logger.debug(f"sdlc_meta_set: _find_session failed: {e}")
        return None


def write_meta(
    key: str,
    value: str,
    session_id: str | None = None,
    issue_number: int | None = None,
) -> dict:
    """Write a metadata key to stage_states["_<key>"] via update_stage_states().

    Args:
        key: Whitelisted key name (e.g., "plan_revising").
        value: Raw string value — will be coerced to the key's type.
        session_id: Optional explicit session ID (falls back to env vars).
        issue_number: Optional issue number for local session resolution.

    Returns:
        Dict with key/value on success, empty dict on any failure.
    """
    if key not in _KEY_REGISTRY:
        logger.debug(f"sdlc_meta_set: unknown key {key!r}")
        return {}

    try:
        coerced = _coerce_value(key, value)
    except ValueError as e:
        logger.debug(f"sdlc_meta_set: value coercion failed for key {key!r}: {e}")
        return {}

    session = _find_session(session_id, issue_number=issue_number)
    if not session:
        return {}

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
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    # Unknown key → exit 2 (invalid argument, not a runtime error)
    if args.key not in _KEY_REGISTRY:
        print(
            f"sdlc_meta_set: unknown key {args.key!r}. "
            f"Whitelisted keys: {sorted(_KEY_REGISTRY.keys())}",
            file=sys.stderr,
        )
        print("{}")
        sys.exit(2)

    result = write_meta(
        key=args.key,
        value=args.value,
        session_id=args.session_id,
        issue_number=args.issue_number,
    )
    print(json.dumps(result))
    # Always exit 0 (fail-soft: runtime errors return {} but don't crash the skill)
    sys.exit(0)


if __name__ == "__main__":
    main()
