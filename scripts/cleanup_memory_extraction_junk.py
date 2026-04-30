"""One-shot cleanup of JSON-shrapnel and refusal-prose Memory records.

Issue #1212. Marks junk Memory records produced before the parser was hardened
(see ``agent/memory_extraction.py::_parse_categorized_observations`` and
``_looks_like_refusal``) as superseded. Original records are NEVER deleted —
``superseded_by="cleanup-junk-extraction"`` is reversible by clearing the
field, matching the ``memory-dedup`` convention from
``scripts/memory_consolidation.py``.

Selectors (all three must match for a record to be a candidate):
    1. ``agent_id`` starts with ``"extraction-"`` — limits the blast radius
       to records produced by the post-session extraction pipeline. Human
       saves, post-merge learnings, and Telegram messages are untouched.
    2. ``superseded_by`` is empty — already-superseded records are skipped
       so re-runs are idempotent.
    3. ``content`` matches a refusal pattern OR is a single-line JSON-syntax
       fragment, per ``agent.memory_extraction._looks_like_refusal``. This
       reuses the exact predicate the parser uses going forward, so the
       cleanup and the prevention agree on what "junk" means.

Safety rails:
    - Default mode is ``--dry-run`` — prints candidate IDs and content samples
      WITHOUT touching Redis. The operator must explicitly pass ``--apply``.
    - Per-record try/except around ``record.save()`` — one failure never
      blocks the rest.
    - ``record.save()`` is governed by ``WriteFilterMixin`` and may return
      ``False`` silently (matching ``scripts/memory_consolidation.py:298``).
      The script counts those blocked writes and reports three numbers in
      the final summary: superseded / blocked / total candidates.
    - Uses Popoto ORM exclusively — never raw Redis (CLAUDE.md rule,
      enforced by ``.claude/hooks/validators/validate_no_raw_redis_delete.py``).

Usage::

    # Dry-run (default): print what would be superseded, no writes
    python scripts/cleanup_memory_extraction_junk.py
    python scripts/cleanup_memory_extraction_junk.py --dry-run

    # Apply: mark candidates superseded
    python scripts/cleanup_memory_extraction_junk.py --apply

Output (apply mode example)::

    [cleanup] would-supersede mem_abc123: There is no agent session response...
    [cleanup] WriteFilter blocked superseded_by write for mem_def456
    [cleanup] Total: 87 candidates / 86 superseded / 1 blocked

The supersede / blocked / total count breakdown is the documented PR-description
deliverable for issue #1212 acceptance criterion 5.
"""

from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)

CLEANUP_SUPERSEDED_BY_VALUE = "cleanup-junk-extraction"
CLEANUP_RATIONALE = "auto-cleanup: refusal/json-shrapnel from issue #1212"


def _is_candidate(record, looks_like_refusal) -> bool:
    """Return True if the record is a junk-extraction candidate.

    Matches the three selectors documented at module top: agent_id prefix,
    not already superseded, and content matches a refusal/shrapnel pattern.
    """
    try:
        agent_id = str(record.agent_id or "")
        if not agent_id.startswith("extraction-"):
            return False
        if (record.superseded_by or "") != "":
            return False
        content = record.content or ""
        return looks_like_refusal(content)
    except Exception:
        # If we can't read a field, treat it as "not a candidate" — safer to
        # leave a weird record alone than to mass-supersede unknowns.
        return False


def _iter_candidates(looks_like_refusal):
    """Yield candidate Memory records matching the junk-extraction selectors.

    Uses Popoto ``Memory.query.all()`` and applies the predicate in Python
    rather than push it into Redis — the candidate set is small (~hundreds
    of records on a populated machine) and the predicate involves substring
    + regex matching that Redis cannot natively express.
    """
    from models.memory import Memory

    try:
        records = Memory.query.all()
    except Exception as e:
        logger.error(f"[cleanup] Failed to load memories from Popoto: {e}")
        return

    for record in records:
        if _is_candidate(record, looks_like_refusal):
            yield record


def run_cleanup(*, dry_run: bool = True) -> dict[str, int]:
    """Mark junk-extraction Memory records as superseded.

    Args:
        dry_run: When True (default), print candidates without touching Redis.
            When False, set ``superseded_by="cleanup-junk-extraction"`` on
            each candidate via Popoto's ``record.save()``.

    Returns:
        Dict with three counts: ``total`` (candidates seen),
        ``superseded`` (saves that returned truthy), and ``blocked``
        (saves that returned ``False`` due to ``WriteFilterMixin``).
    """
    # Lazy import so the script doesn't blow up at parse time if the agent
    # module has an import error — useful for the cleanup-runs-on-broken-tree
    # case where the operator wants to fix junk before redeploying.
    from agent.memory_extraction import _looks_like_refusal

    total = 0
    superseded = 0
    blocked = 0

    for record in _iter_candidates(_looks_like_refusal):
        total += 1
        memory_id = getattr(record, "memory_id", "<unknown>")
        content_sample = (record.content or "")[:80].replace("\n", " ")

        if dry_run:
            print(f"[cleanup] would-supersede {memory_id}: {content_sample}")
            continue

        try:
            record.superseded_by = CLEANUP_SUPERSEDED_BY_VALUE
            record.superseded_by_rationale = CLEANUP_RATIONALE
            result = record.save()
            if result is False:
                blocked += 1
                logger.warning(f"[cleanup] WriteFilter blocked superseded_by write for {memory_id}")
                print(f"[cleanup] blocked {memory_id}: {content_sample}")
            else:
                superseded += 1
                print(f"[cleanup] superseded {memory_id}: {content_sample}")
        except Exception as e:
            # Per-record fail-silent: log once, continue. WriteFilter veto is
            # a result == False (handled above); this branch is for genuine
            # exceptions (Redis down, schema drift, etc).
            logger.warning(f"[cleanup] save raised for {memory_id}: {e}")
            print(f"[cleanup] error {memory_id}: {e}")

    verb = "would be" if dry_run else "were"
    print(
        f"[cleanup] Total: {total} candidates / {superseded} {verb} superseded / {blocked} blocked"
    )

    return {"total": total, "superseded": superseded, "blocked": blocked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cleanup JSON-shrapnel and refusal-prose Memory records (issue #1212). "
            "Default mode is --dry-run; pass --apply to actually mark records superseded."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print candidates without touching Redis (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Mark candidates as superseded (writes to Redis via Popoto).",
    )
    args = parser.parse_args(argv)

    # --apply trumps the default --dry-run if both are present
    dry_run = not args.apply

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    counts = run_cleanup(dry_run=dry_run)
    # Always exit 0 if the run completed; counts are reported in stdout.
    # Non-zero exit would imply "the cleanup failed" which is misleading when
    # the cleanup ran and just found zero candidates or had some blocked
    # writes. Operators can grep stdout for "blocked" or "0 candidates" if
    # they want stricter signals.
    _ = counts
    return 0


if __name__ == "__main__":
    sys.exit(main())
