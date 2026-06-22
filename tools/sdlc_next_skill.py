"""CLI entry point for the SDLC next-skill dispatch decision.

Wraps ``agent.sdlc_router.decide_next_dispatch()`` with session resolution
and the enriched meta payload from ``tools.sdlc_stage_query.query_enriched``.

This is the **production runtime path** that replaces the SKILL.md Step 4
hand-edited dispatch table. The LLM calls this tool and dispatches whatever
skill it returns — the LLM no longer authors routing decisions.

Usage::

    sdlc-tool next-skill --issue-number 1040
    sdlc-tool next-skill --issue-number 1040 --proposed-skill /do-build
    sdlc-tool next-skill --issue-number 1040 --format pretty

Environment:
    No rollout flags -- this module is the sole routing source of truth.
    The legacy SKILL.md hand-authored dispatch table has been removed.
    Setting ``SDLC_ROUTER_SOURCE`` has no effect.

Exit codes:
    0 — decision produced (either ``dispatched`` or ``blocked``)
    1 — session lookup or dispatch calculation failed fatally
    2 — wrapper-level usage / configuration error

Output (JSON, stdout)::

    {
        "skill": "/do-build",
        "reason": "...",
        "row_id": "4a",
        "dispatched": true
    }

    # When the router blocks:
    {
        "blocked": true,
        "reason": "...",
        "guard_id": "G4"
    }

The ``dispatched`` key is always present in a non-blocked response. It is
``true`` when the router produced a ``Dispatch`` object, ``false`` otherwise
(should not happen in practice — the blocked path uses the ``blocked`` key).

Graceful failure: any exception in session lookup or dispatch is caught and
emitted as JSON on stdout with ``{"error": "...", "dispatched": false}``
followed by exit code 1. This prevents the LLM from seeing a raw traceback.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


def _resolve_enriched(issue_number: int | None, session_id: str | None) -> dict:
    """Return the enriched stage_states payload (stages + _meta)."""
    try:
        from tools.sdlc_stage_query import query_enriched

        return query_enriched(
            session_id=session_id,
            issue_number=issue_number,
        )
    except Exception as e:
        logger.debug(f"_resolve_enriched failed: {e}")
        return {"stages": {}, "_meta": {}}


def _build_context(proposed_skill: str | None, issue_number: int | None) -> dict:
    """Build the optional context dict for the dispatch function.

    The context dict carries caller-supplied hints that the guards may need
    but that are not present in stage_states or _meta:
    - ``proposed_skill``: the skill the LLM was about to invoke (used by G3
      to detect plan-family redirects when a PR is already open).
    - ``branch_exists``: whether the session branch already exists (Row 5).
    - ``current_plan_hash``: sha256 of the plan file (used by G5 to short-circuit
      re-critique on an unchanged plan; #1639). Without this, G5's loop bound on
      router row 2b is inert in the CLI path.
    """
    context: dict = {}
    if proposed_skill:
        context["proposed_skill"] = proposed_skill

    # G5 activation (#1639): supply the current plan-file hash so
    # guard_g5_artifact_hash_cache can compare it against the cached CRITIQUE
    # verdict's artifact_hash and bound the row-2b re-critique loop. None-safe:
    # no plan path or unreadable file leaves the key unset (G5 then no-ops).
    if issue_number:
        try:
            from tools._sdlc_utils import find_plan_path
            from tools.sdlc_verdict import compute_plan_body_hash

            plan_path = find_plan_path(issue_number)
            if plan_path is not None:
                plan_hash = compute_plan_body_hash(plan_path)
                if plan_hash is not None:
                    context["current_plan_hash"] = plan_hash
                    context["issue_number"] = issue_number
        except Exception:
            pass

    # Check whether the issue-specific session branch already exists (informs Row 5).
    # Uses `session/sdlc-{issue_number}` — the canonical branch name for SDLC work.
    # Checking just "session/" would always be True in this repo due to many active
    # session/ branches; we must check for the issue-specific pattern.
    if issue_number:
        try:
            import subprocess

            proc2 = subprocess.run(
                ["git", "branch", "-a"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch_names = proc2.stdout if proc2.returncode == 0 else ""
            context["branch_exists"] = f"session/sdlc-{issue_number}" in branch_names
        except Exception:
            context["branch_exists"] = False

    return context


def decide(
    issue_number: int | None = None,
    session_id: str | None = None,
    proposed_skill: str | None = None,
) -> dict:
    """Run the dispatch algorithm and return a JSON-serialisable result dict.

    This is the programmatic interface; CLI consumers go through ``main()``.

    Returns:
        On ``Dispatch``: ``{"skill": "/do-X", "reason": "...", "row_id": "...",
        "dispatched": True}``
        On ``Blocked``: ``{"blocked": True, "reason": "...", "guard_id": "..."}``
        On error: ``{"error": "...", "dispatched": False}``
    """
    try:
        from agent.sdlc_router import (
            Blocked,
            Dispatch,
            decide_next_dispatch,
        )

        enriched = _resolve_enriched(issue_number, session_id)
        stage_states = enriched.get("stages") or {}
        meta = enriched.get("_meta") or {}
        context = _build_context(proposed_skill, issue_number)

        result = decide_next_dispatch(stage_states, meta, context)

        if isinstance(result, Dispatch):
            return {
                "skill": result.skill,
                "reason": result.reason,
                "row_id": result.row_id,
                "dispatched": True,
            }
        elif isinstance(result, Blocked):
            return {
                "blocked": True,
                "reason": result.reason,
                "guard_id": result.guard_id,
            }
        else:
            # Unexpected return type — treat as blocking error
            return {
                "error": f"Unexpected result type: {type(result).__name__}",
                "dispatched": False,
            }

    except Exception as e:
        logger.debug(f"decide() failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "dispatched": False,
        }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Compute the next SDLC dispatch decision for an issue.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        metavar="N",
        help="GitHub issue number to look up the session.",
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        help="Explicit AgentSession ID (overrides --issue-number lookup).",
    )
    parser.add_argument(
        "--proposed-skill",
        metavar="SKILL",
        help="The skill the LLM was about to invoke (passed to G3 guard for PR-lock detection).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "pretty"],
        default="json",
        help="Output format. 'json' is machine-parseable (default); 'pretty' is indented.",
    )
    args = parser.parse_args(argv)

    if not args.issue_number and not args.session_id:
        print(
            json.dumps({"error": "Must supply --issue-number or --session-id", "dispatched": False})
        )
        return 2

    result = decide(
        issue_number=args.issue_number,
        session_id=args.session_id,
        proposed_skill=args.proposed_skill,
    )

    if args.format == "pretty":
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))

    # Exit 1 on error, 0 on dispatch or block (both are valid outcomes)
    if result.get("error") and not result.get("dispatched"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
