"""reflections/housekeeping/side_effect_drain.py — Run due post-session side-effect jobs.

What it does: calls agent.side_effects.run_due, which executes every pending
    SideEffectJob whose next_attempt_at has passed, backs off the ones whose
    handler raised, and dead-letters the ones that exhausted their attempts.
Cadence: 60s (post-session memory extraction should land within a minute or two
    of the session finishing, the latency the in-process task used to give)
Failure modes:
    - A handler raises -> caught per job, attempts incremented, backoff applied;
      at MAX_JOB_ATTEMPTS the job becomes DeadLetter(stage="extraction")
    - Redis unreachable mid-drain -> the job stays pending with unchanged
      attempts, so nothing is double-counted
Related reflections:
    - dead_letter_replay: replays what this drain gave up on
See also: config/reflections.yaml (declaration), docs/features/side-effect-jobs.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.housekeeping")

# Jobs drained per tick. Bounded so one backlog cannot monopolise the
# reflection process; the next tick is 60s away.
DRAIN_LIMIT = 25


async def run() -> dict:
    """Drain due side-effect jobs and report what happened."""
    try:
        from agent.side_effects import run_due

        result = await run_due(limit=DRAIN_LIMIT)
        summary = (
            f"Side-effect drain: {result['ran']} ran, {result['failed']} retrying, "
            f"{result['dead_lettered']} dead-lettered, {result['skipped']} skipped"
        )
        logger.info(summary)
        return {"summary": summary, **result}
    except Exception as e:
        logger.error("Side-effect drain failed: %s", e, exc_info=True)
        return {"summary": f"Side-effect drain failed: {e}"}
