"""reflections/memory/memory_distill_backfill.py — Distill provisional human-ingest Memory records.

What it does: Finds active (non-superseded) ``Memory`` records left in
    ``metadata.distill_status == "provisional"`` by
    ``.claude/hooks/hook_utils/memory_bridge.py::ingest()`` (spike-1: the hook
    process itself never calls an LLM -- it is ephemeral and a daemon thread
    would be killed mid-flight, see the plan's spike-1) and distills each
    verbatim human utterance into a standalone fact + content-derived
    importance via ``agent.memory_extraction.distill_human_prompt_async``.

    Every provisional record eventually reaches a TERMINAL state --
    ``distilled`` (success) or ``distill_abandoned`` (attempt-cap breach or a
    write-filter drop) -- so a permanently-refusing record cannot retry
    forever or crowd out fresh ones (spike-2b / Risk 1b).

Cadence: 300s (5 minutes) -- see config/reflections.yaml. Faster than the
    daily cadence this module's *shape* is mirrored from
    (``memory_embedding_backfill.py``) because ingest freshness matters here:
    a distilled memory is only useful once available for recall.

Scan ordering (blocker, critique r2): candidates are sorted ASCENDING by
    ``metadata.distill_last_attempt_at`` using the defensive sort key
    ``key=lambda r: r.metadata.get("distill_last_attempt_at", 0)`` -- never a
    bare attribute/dict access that could surface ``None`` into ``sorted()``.
    A ``None``-vs-float comparison would raise ``TypeError`` at scan setup,
    OUTSIDE the per-record ``try/except`` below, aborting the entire run every
    cycle (fresh provisionals -- seeded ``distill_last_attempt_at: 0`` by
    ``ingest()`` -- are the steady-state case, not the exception). Least-
    recently-attempted first means a poison-pill record that keeps failing
    sinks to the back of the queue and never starves fresh records.

Write ordering (critique r2 CONCERN, resolved): the LLM call happens BEFORE
    any write. A crash between "bump distill_attempts" and "call the LLM"
    would inflate the attempt count from process instability, not a genuine
    distillation failure, if the counter bump were a separate write issued
    first. Instead every code path below performs exactly ONE partial
    ``save()`` per record per run that carries the attempt-counter bump/
    timestamp stamp TOGETHER with whatever outcome the LLM call produced
    (failure, content-gate refusal, or a settled distillation) -- except the
    rare write-filter-drop defensive branch, which issues a second,
    abandon-only save specifically because the first save's write never
    landed at all.

Content gate on distillation output (critique r3 fix): a successful
    distillation's ``fact`` is run through ``agent.memory_quality.gate_reason``
    -- the SAME junk heuristic ``Memory.save()`` applies on INSERT -- before
    it is written. Distillation is an UPDATE, so ``Memory.save()``'s
    INSERT-only content gate does not see it; without this explicit check, a
    low-quality distilled fact (fragment/ack/too-short) would bypass content
    quality entirely. A gate hit is treated exactly like an LLM failure
    (bump attempts, do not settle the record) and counted separately
    (``distill_refused``, not ``distill_failed``) so the two failure shapes
    stay distinguishable in telemetry.

Race-1 guard (primary defense, not a scheduler assumption): immediately
    before the settled-distillation write, the record is RE-FETCHED from
    Redis (``Memory.query.filter(memory_id=...).first()``) and the write is
    skipped if its current ``distill_status`` is no longer ``"provisional"``
    -- i.e. a concurrent/overlapping reflection run already distilled or
    abandoned it. This compare-before-write does not depend on the
    reflection scheduler running a single instance at a time (that
    single-instance behavior is real but unspiked -- see the plan's Race
    Conditions section, "belt, not the buckle").

Write-filter floor (spike-2b, revision blocker): ``WriteFilterMixin`` gates
    EVERY ``Memory.save()`` call -- INSERT or partial UPDATE alike -- on
    ``self.importance``, before the ``update_fields`` branch even runs. If
    the write-filter silently drops the settled-distillation save (returns
    ``False``), the record must NOT be left un-updated in memory only:
    the boolean return is inspected, and a ``False`` routes through the same
    attempt-bump/abandon-on-cap path as an LLM failure (with
    ``distill_failed``, since it is a mechanical write failure, not a
    content-quality refusal). ``compute_ingest_importance`` already floors
    every computed importance at ``MEMORY_WF_MIN_THRESHOLD`` by construction,
    so this should never actually fire -- it exists purely as a defensive
    backstop (belt and suspenders, per the plan).

Failure modes:
    - Memory import fails -> return {"status": "error", ...}
    - Memory.query.all() raises -> return {"status": "error", ...}
    - Per-record distillation/save exception -> logged, skipped, run continues
      (fail-open per record -- one poisoned record never aborts the batch)

Apply gating (inverted from memory_embedding_backfill.py -- distillation is
    this feature's steady state, not a one-off remediation): APPLY-ON BY
    DEFAULT. ``MEMORY_DISTILL_BACKFILL_APPLY`` defaults to "true" and acts as
    an operator KILL SWITCH (set to "false"/"0"/"no" to force dry-run), not
    an opt-in gate. Deliberately undocumented in ``.env.example`` /
    ``config/settings.py`` -- an operator-only runtime override, not a
    deployed configuration value. See "Update System" in the plan for the
    full rationale (shipping dry-run-by-default here would make the whole
    feature inert, contradicting the "fully automatic" success criterion).

See also: config/reflections.yaml (declaration, name=memory-distill-backfill),
    reflections/memory/memory_embedding_backfill.py (structural precedent --
    cadence/cap/fail-open/env-toggle shape, INVERTED apply-default),
    agent/memory_extraction.py (``distill_human_prompt_async``,
    ``CATEGORY_IMPORTANCE``), agent/memory_quality.py (``gate_reason``),
    config/memory_defaults.py (``compute_ingest_importance``,
    ``MAX_DISTILL_ATTEMPTS``, ``MAX_DISTILL_PER_RUN``,
    ``DISTILL_SOURCE_WEIGHT``), models/memory_distill_gate.py (telemetry
    counters), docs/plans/memory-distilled-ingest.md.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

logger = logging.getLogger("reflections.memory_management")


def _apply_mode_enabled() -> bool:
    """Read the apply-mode kill switch at call time (not module-capture).

    Defaults to True ("apply") -- distillation is the feature's steady state
    (see module docstring / plan Update System). An operator sets
    ``MEMORY_DISTILL_BACKFILL_APPLY=false`` (also "0"/"no") to force dry-run.
    Read at call time so tests can toggle it with ``monkeypatch.setenv`` /
    ``patch.dict(os.environ, ...)``.
    """
    import os

    return os.environ.get("MEMORY_DISTILL_BACKFILL_APPLY", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _bump_metadata_after_attempt(meta: dict) -> dict:
    """Return a NEW metadata dict with the attempt counter bumped and timestamp stamped.

    Does not mutate ``meta`` in place (callers may still hold a reference to
    the original dict for logging). If the bumped attempt count reaches
    ``MAX_DISTILL_ATTEMPTS``, ``distill_status`` is set to the terminal
    ``distill_abandoned`` -- otherwise the existing ``distill_status`` is left
    untouched (still ``"provisional"``, eligible for retry next run). Used
    for every non-cap-breach outcome (LLM failure, content-gate refusal, a
    settled distillation, and the write-filter-drop defensive backstop) --
    the already-at-cap fast path in ``_process_record`` builds its own
    terminal metadata directly instead, since it deliberately does NOT bump
    the attempt counter (no distillation was attempted).
    """
    from config.memory_defaults import MAX_DISTILL_ATTEMPTS

    updated = dict(meta) if isinstance(meta, dict) else {}
    attempts = updated.get("distill_attempts", 0)
    if not isinstance(attempts, int):
        attempts = 0
    bumped = attempts + 1
    updated["distill_attempts"] = bumped
    updated["distill_last_attempt_at"] = time.time()
    if bumped >= MAX_DISTILL_ATTEMPTS:
        updated["distill_status"] = "distill_abandoned"
    return updated


async def _process_record(record, *, findings: list[str]) -> str:
    """Distill (or abandon) a single provisional record. Returns an outcome tag.

    Outcome tags: "distilled", "distill_failed", "distill_refused",
    "distill_abandoned", "skipped_race". Never raises -- all exceptions are
    caught by the caller's per-record try/except (fail-open, one poisoned
    record must not abort the batch).
    """
    from agent.memory_extraction import (
        CATEGORY_IMPORTANCE,
        DEFAULT_CATEGORY_IMPORTANCE,
        DISTILL_MODEL,
        DISTILL_PROMPT_VERSION,
        distill_human_prompt_async,
    )
    from agent.memory_quality import gate_reason
    from config.memory_defaults import (
        DISTILL_SOURCE_WEIGHT,
        MAX_DISTILL_ATTEMPTS,
        compute_ingest_importance,
    )
    from models.memory import Memory
    from models.memory_distill_gate import _increment_distill_counter

    meta = record.metadata if isinstance(record.metadata, dict) else {}
    attempts = meta.get("distill_attempts", 0)
    if not isinstance(attempts, int):
        attempts = 0

    # Attempt ceiling: transition straight to terminal WITHOUT attempting
    # distillation again -- no LLM call, no counter bump (the record is
    # already at/over cap; `distill_attempts` itself is left untouched, only
    # `distill_status` moves to terminal).
    if attempts >= MAX_DISTILL_ATTEMPTS:
        abandoned_meta = dict(meta)
        abandoned_meta["distill_status"] = "distill_abandoned"
        abandoned_meta["distill_last_attempt_at"] = time.time()
        record.metadata = abandoned_meta
        record.save(update_fields=["metadata"])
        _increment_distill_counter(record.project_key, "distill_abandoned")
        return "distill_abandoned"

    # LLM call happens FIRST -- no separate pre-call counter-bump write
    # (critique r2 CONCERN; see module docstring "Write ordering").
    distilled = await distill_human_prompt_async(record.content)

    if distilled is None:
        # Fail-open: single write bundles the attempt bump + terminal
        # transition (if cap breached) together.
        record.metadata = _bump_metadata_after_attempt(meta)
        record.save(update_fields=["metadata"])
        _increment_distill_counter(record.project_key, "distill_failed")
        return "distill_failed"

    fact = distilled["fact"]
    category = distilled["category"]

    # Content-quality gate on the distillation OUTPUT (critique r3 fix): a
    # low-quality fact must not bypass the same junk heuristic Memory.save()
    # applies on INSERT just because this write is an UPDATE.
    reason = gate_reason(fact)
    if reason is not None:
        record.metadata = _bump_metadata_after_attempt(meta)
        record.save(update_fields=["metadata"])
        _increment_distill_counter(record.project_key, "distill_refused")
        findings.append(f"Distillation output refused by content gate (reason={reason}).")
        return "distill_refused"

    content_value = CATEGORY_IMPORTANCE.get(category, DEFAULT_CATEGORY_IMPORTANCE)
    importance = compute_ingest_importance(DISTILL_SOURCE_WEIGHT, content_value)

    # Race-1 primary guard: re-read from Redis immediately before the write.
    # Skip entirely if another run already settled this record -- do NOT
    # trust the `record` reference captured at scan time.
    fresh = Memory.query.filter(memory_id=record.memory_id).first()
    fresh_meta = getattr(fresh, "metadata", None) if fresh is not None else None
    if (
        fresh is None
        or not isinstance(fresh_meta, dict)
        or fresh_meta.get("distill_status") != "provisional"
    ):
        return "skipped_race"

    new_meta = _bump_metadata_after_attempt(fresh_meta)
    new_meta["distill_status"] = "distilled"
    new_meta["distill_model"] = DISTILL_MODEL
    new_meta["distill_prompt_version"] = DISTILL_PROMPT_VERSION
    new_meta["category"] = category

    fresh.content = fact
    fresh.importance = importance
    fresh.metadata = new_meta
    result = fresh.save(update_fields=["content", "importance", "metadata"])

    if result is False:
        # Defensive backstop only (spike-2b): compute_ingest_importance already
        # floors every value above the write-filter threshold, so this should
        # never actually trip. If it somehow does, the record must not be left
        # silently un-updated -- route through the same bump/abandon path,
        # now via a SECOND save because the first write never landed.
        fresh.metadata = _bump_metadata_after_attempt(fresh_meta)
        fresh.save(update_fields=["metadata"])
        _increment_distill_counter(record.project_key, "distill_failed")
        findings.append(
            f"Write-filter dropped settled distillation for {record.memory_id} "
            "(defensive backstop fired -- should not happen post-flooring)."
        )
        return "distill_failed"

    _increment_distill_counter(record.project_key, "distilled")
    return "distilled"


async def run() -> dict:
    """Distill provisional human-ingest Memory records out of band.

    Default: APPLY mode (distillation is the steady state). Set
    ``MEMORY_DISTILL_BACKFILL_APPLY=false`` to force dry-run (report
    candidates, distill nothing).
    """
    from config.memory_defaults import MAX_DISTILL_PER_RUN

    apply_mode = _apply_mode_enabled()
    mode_str = "APPLIED" if apply_mode else "DRY RUN"

    findings: list[str] = []

    try:
        from models.memory import Memory
    except Exception as e:
        logger.warning("memory-distill-backfill: Memory import failed: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"memory-distill-backfill error: Memory import failed: {e}",
        }

    try:
        all_memories = Memory.query.all()
    except Exception as e:
        logger.warning("memory-distill-backfill: could not query memories: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"memory-distill-backfill error: query failed: {e}",
        }

    candidates = []
    for memory in all_memories:
        if getattr(memory, "superseded_by", ""):
            continue
        meta = getattr(memory, "metadata", None)
        if not isinstance(meta, dict):
            continue
        if meta.get("distill_status") != "provisional":
            continue
        candidates.append(memory)

    candidate_count = len(candidates)
    findings.append(f"{candidate_count} provisional records awaiting distillation.")

    # Scan-sort TypeError guard (blocker regression, critique r2): the
    # defensive `.get("distill_last_attempt_at", 0)` key must be used here,
    # in Python, over the already-fetched candidate set -- never a Redis-side
    # sort, and never a bare `.metadata["distill_last_attempt_at"]` access
    # that could raise on a legacy/missing key. Ascending order: least-
    # recently-attempted first, so a poison-pill record sinks to the back.
    candidates.sort(key=lambda r: r.metadata.get("distill_last_attempt_at", 0))

    batch = candidates[:MAX_DISTILL_PER_RUN]

    if not apply_mode:
        findings.append(
            f"[DRY RUN] Would process up to {len(batch)} records "
            f"(cap={MAX_DISTILL_PER_RUN}). "
            "Set MEMORY_DISTILL_BACKFILL_APPLY=false to keep dry-run; unset "
            "(or 'true') to apply -- apply is the default."
        )
        summary = (
            f"memory-distill-backfill [{mode_str}]: {candidate_count} provisional, "
            f"0 processed (dry run)"
        )
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    outcomes: dict[str, int] = {}
    for record in batch:
        try:
            outcome = await _process_record(record, findings=findings)
        except Exception as e:
            logger.warning(
                "memory-distill-backfill: processing failed for %s: %s",
                getattr(record, "memory_id", "?"),
                e,
            )
            outcome = "error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    findings.append(
        f"Processed {len(batch)} of {candidate_count} provisional records "
        f"(cap={MAX_DISTILL_PER_RUN}): "
        + ", ".join(f"{count} {tag}" for tag, count in sorted(outcomes.items()))
    )

    # Best-effort metric -- never crash the reflection.
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.distill_backfill_processed",
            float(len(batch)),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
    except Exception as e:
        logger.debug("memory-distill-backfill: metric emission failed: %s", e)

    summary = (
        f"memory-distill-backfill [{mode_str}]: {candidate_count} provisional, "
        f"{len(batch)} processed ("
        + ", ".join(f"{count} {tag}" for tag, count in sorted(outcomes.items()))
        + ")"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


def sweep_provisional_to_abandoned() -> dict:
    """One-off idempotent drain: transition every remaining provisional record to terminal.

    Used for clean teardown when the feature is disabled (registry
    ``enabled: false`` or the apply kill switch) -- without this, disabling
    the reflection would strand in-flight provisional records forever
    (Reversibility concern in the plan). Content and importance are left
    UNTOUCHED (verbatim content, floored importance) -- only
    ``metadata.distill_status`` moves to the terminal ``distill_abandoned``
    state via a metadata-only partial save.

    Idempotent: a second run over already-abandoned (or already-distilled)
    records is a no-op -- the scan filters on ``distill_status ==
    "provisional"`` only, exactly like ``run()``'s scan. Synchronous (no LLM
    call), unlike ``run()``, so it is safe to invoke directly from a CLI
    entry point without an event loop.

    Returns a summary dict (not a reflection-shaped one -- CLI-only entry
    point, not registered in config/reflections.yaml).
    """
    from models.memory import Memory
    from models.memory_distill_gate import _increment_distill_counter

    try:
        all_memories = Memory.query.all()
    except Exception as e:
        logger.warning("memory-distill-backfill sweep: could not query memories: %s", e)
        return {"status": "error", "abandoned": 0, "message": str(e)}

    abandoned = 0
    for memory in all_memories:
        if getattr(memory, "superseded_by", ""):
            continue
        meta = getattr(memory, "metadata", None)
        if not isinstance(meta, dict) or meta.get("distill_status") != "provisional":
            continue
        try:
            new_meta = dict(meta)
            new_meta["distill_status"] = "distill_abandoned"
            new_meta["distill_last_attempt_at"] = time.time()
            memory.metadata = new_meta
            memory.save(update_fields=["metadata"])
            _increment_distill_counter(memory.project_key, "distill_abandoned")
            abandoned += 1
        except Exception as e:
            logger.warning(
                "memory-distill-backfill sweep: abandon failed for %s: %s",
                getattr(memory, "memory_id", "?"),
                e,
            )
            continue

    logger.info("memory-distill-backfill sweep: abandoned %d provisional records", abandoned)
    return {"status": "ok", "abandoned": abandoned}


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="memory-distill-backfill CLI (sweep entry point only; the "
        "reflection scheduler drives run() on its own cadence)."
    )
    parser.add_argument(
        "--sweep-abandon",
        action="store_true",
        help="Transition every remaining provisional record to terminal "
        "distill_abandoned (idempotent teardown for when the feature is disabled).",
    )
    args = parser.parse_args()

    if args.sweep_abandon:
        result = sweep_provisional_to_abandoned()
        print(result)
    else:
        # No flag given -- run one live cycle synchronously (dev/debug convenience).
        result = asyncio.run(run())
        print(result)


if __name__ == "__main__":
    _main()
