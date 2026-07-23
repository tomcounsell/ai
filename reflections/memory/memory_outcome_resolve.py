"""reflections/memory/memory_outcome_resolve.py — Durable outcome attribution sweep.

What it does: crashed/killed Claude Code sessions never reach the Stop-hook
handler (`.claude/hooks/hook_utils/memory_bridge.py::extract`), so their
per-session sidecar file (`data/sessions/{session_id}/memory_buffer.json`,
containing the `injected[]` list of memories shown to the agent) is orphaned
on disk instead of being judged and cleaned up. Without this sweep, those
injections receive NO outcome signal at all -- silently lost, forever.

The orphaned sidecar IS the durable journal (spike-3, issue #2203) -- no new
storage is introduced. This sweep walks the sidecar directory, finds files
whose mtime is older than `INJECTION_RESOLVE_TTL`, resolves every unresolved
`injected[]` entry to the neutral "deferred" outcome (pressure builds, no
confidence effect -- see `popoto.fields.observation.VALID_OUTCOMES`), feeds
that through `ObservationProtocol.on_context_used` + `_persist_outcome_metadata`
(the same call shape `agent.memory_extraction.detect_outcomes_async` uses at
clean-session-stop), and then cleans the sidecar up via
`hook_utils.memory_bridge.cleanup_sidecar`.

TTL-only liveness gating: there is no session-liveness helper importable from
memory_bridge.py (confirmed by grep -- no is_session_live/session_live/is_live
symbol). A live session keeps its sidecar mtime fresh because `recall()`
rewrites the sidecar via `_save_sidecar` on every recall injection
(memory_bridge.py's PostToolUse path). `INJECTION_RESOLVE_TTL` is set with
headroom over the max plausible gap between recall injections in a live
session; even a mis-estimate is harmless because "deferred" is a no-op
outcome -- the TTL protects against churn, not correctness.

Cadence: recommended daily-or-faster (see reflections.yaml, vault).
Failure modes:
    - sidecar directory missing/unreadable -> return {"status": "ok", ...},
      zero resolved (nothing to sweep is not an error)
    - a single sidecar is malformed/partial (crashed mid-write) -> logged,
      skipped, sweep continues with the next sidecar (fail-silent per-record,
      same convention as agent.memory_extraction._persist_outcome_metadata)
    - hook_utils.memory_bridge import fails -> return {"status": "error", ...}
Related reflections:
    - memory_decay_prune / memory_quality_audit operate on already-durable
      Memory records; this sweep is upstream of them -- it is what makes the
      outcome-history / dismissal_count signal those reflections read honest
      for crashed sessions, the same way the honest bigram-overlap fallback
      (agent.memory_extraction.detect_outcomes_async) makes it honest for the
      clean-stop path.
Apply gating: none -- this sweep only ever emits the neutral "deferred"
    outcome (never "acted"/"dismissed"), so there is no destructive apply
    switch to gate; it always runs live. Idempotent: a sidecar that has
    already been resolved and cleaned up simply no longer exists on the next
    pass, so double-processing is impossible.
See also: config/reflections.yaml (declaration), docs/features/reflections.md,
    docs/features/subconscious-memory.md
"""

from __future__ import annotations

import logging
import sys
import time as _time
from pathlib import Path

logger = logging.getLogger("reflections.memory_management")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HOOKS_DIR = _PROJECT_ROOT / ".claude" / "hooks"

_SIDECAR_FILENAME = "memory_buffer.json"


def _import_memory_bridge():
    """Import `hook_utils.memory_bridge`, adding `.claude/hooks` to sys.path.

    `memory_bridge.py` lives on the hook-local sys.path (`.claude/hooks`), not
    a normally-importable package from `reflections/` -- other production code
    (e.g. `agent/tui_interaction_capture.py`) has historically worked around
    this by duplicating small constants rather than importing. For the sidecar
    directory root and `cleanup_sidecar`, duplicating would risk drifting from
    the real sidecar layout, so instead this mirrors the exact sys.path
    pattern the hook scripts themselves use (see `.claude/hooks/stop.py`) to
    import the real module and reuse its logic as the single source of truth.
    """
    hooks_dir = str(_HOOKS_DIR)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from hook_utils import memory_bridge

    return memory_bridge


def _resolve_stale_sidecar(mb, session_id: str) -> tuple[int, str | None]:
    """Resolve one stale sidecar's unresolved injections to "deferred".

    Returns (resolved_count, error_or_None). `resolved_count` is the number
    of memories fed through ObservationProtocol.on_context_used for this
    sidecar (0 if the sidecar had no injected[] entries -- still a valid,
    cleaned-up no-op).

    Any exception is caught and reported via the second tuple element so the
    caller can log-and-continue without one malformed sidecar aborting the
    rest of the sweep.
    """
    try:
        state = mb._load_sidecar(session_id)
        injected = state.get("injected", [])
        if not injected:
            return 0, None

        outcome_map: dict[str, str] = {}
        memory_keys: list[str] = []
        for item in injected:
            if not isinstance(item, dict):
                continue
            memory_id = item.get("memory_id", "")
            if not memory_id:
                continue
            outcome_map[memory_id] = "deferred"
            memory_keys.append(memory_id)

        if not memory_keys:
            return 0, None

        from popoto import ObservationProtocol

        from config.memory_defaults import DEFAULT_PROJECT_KEY
        from models.memory import Memory
        from models.memory_gate import _increment_gate_counter

        memories = []
        for key in memory_keys:
            try:
                results = Memory.query.filter(memory_id=key)
                if results:
                    memories.append(results[0])
            except Exception:  # noqa: S112 -- memory ops silent by design
                continue

        if not memories:
            return 0, None

        redis_outcome_map = {}
        for m in memories:
            mid = getattr(m, "memory_id", "")
            if mid in outcome_map:
                redis_key = getattr(m.db_key, "redis_key", "")
                if redis_key:
                    redis_outcome_map[redis_key] = outcome_map[mid]

        if redis_outcome_map:
            ObservationProtocol.on_context_used(memories, redis_outcome_map)

        from agent.memory_extraction import _persist_outcome_metadata

        _persist_outcome_metadata(memories, outcome_map)

        for m in memories:
            _increment_gate_counter(
                getattr(m, "project_key", None) or DEFAULT_PROJECT_KEY,
                "outcome_resolve_count",
            )

        return len(memories), None
    except Exception as e:
        return 0, str(e)


async def run() -> dict:
    """Sweep orphaned session sidecars and resolve their injections to "deferred".

    Iterates `data/sessions/*/memory_buffer.json` (the sidecar directory root
    from `hook_utils.memory_bridge._get_sidecar_dir`), selecting sidecars
    whose mtime exceeds `INJECTION_RESOLVE_TTL` -- TTL-only gating, no
    session-liveness dependency (see module docstring). For each stale
    sidecar: unresolved `injected[]` entries are resolved to the neutral
    "deferred" outcome via `ObservationProtocol.on_context_used` +
    `agent.memory_extraction._persist_outcome_metadata`, then the sidecar is
    removed via `cleanup_sidecar`. Idempotent (a resolved-and-cleaned-up
    sidecar no longer exists to re-process) and fail-silent per sidecar
    (a malformed/partial file is logged and skipped, never aborts the sweep).
    Bounded by `OUTCOME_RESOLVE_MAX_PER_RUN` per invocation.
    """
    from config.memory_defaults import (
        INJECTION_RESOLVE_TTL,
        OUTCOME_RESOLVE_MAX_PER_RUN,
    )

    findings: list[str] = []

    try:
        mb = _import_memory_bridge()
    except Exception as e:
        logger.warning(f"Memory outcome resolve: could not import memory_bridge: {e}")
        return {"status": "error", "findings": [], "summary": f"Import error: {e}"}

    try:
        sidecar_root = mb._get_sidecar_dir("_placeholder_").parent
    except Exception as e:
        logger.warning(f"Memory outcome resolve: could not resolve sidecar root: {e}")
        return {"status": "error", "findings": [], "summary": f"Sidecar root error: {e}"}

    if not sidecar_root.exists():
        summary = "Memory outcome resolve: sidecar directory does not exist, nothing to sweep"
        logger.info(summary)
        return {"status": "ok", "findings": [], "summary": summary}

    now = _time.time()
    cutoff = now - INJECTION_RESOLVE_TTL

    stale_session_ids: list[str] = []
    try:
        for entry in sidecar_root.iterdir():
            if not entry.is_dir():
                continue
            sidecar_path = entry / _SIDECAR_FILENAME
            if not sidecar_path.exists():
                continue
            try:
                mtime = sidecar_path.stat().st_mtime
            except OSError:
                continue
            if mtime <= cutoff:
                stale_session_ids.append(entry.name)
    except Exception as e:
        logger.warning(f"Memory outcome resolve: could not scan sidecar directory: {e}")
        return {"status": "error", "findings": [], "summary": f"Scan error: {e}"}

    capped = stale_session_ids[:OUTCOME_RESOLVE_MAX_PER_RUN]

    swept_count = 0
    resolved_count = 0
    error_count = 0

    for session_id in capped:
        resolved, error = _resolve_stale_sidecar(mb, session_id)
        if error is not None:
            error_count += 1
            logger.warning(
                f"Memory outcome resolve: sidecar {session_id!r} malformed/partial, "
                f"skipping resolution (cleanup still attempted): {error}"
            )
        resolved_count += resolved
        # Cleanup runs regardless of resolution outcome (mirrors the clean-stop
        # path's finally-block semantics) -- an orphaned sidecar must not be
        # left behind just because its content was unreadable.
        try:
            mb.cleanup_sidecar(session_id)
            swept_count += 1
        except Exception as e:
            logger.debug(f"Memory outcome resolve: cleanup failed for {session_id!r}: {e}")

    findings.append(
        f"Scanned {len(stale_session_ids)} stale sidecars (cap={OUTCOME_RESOLVE_MAX_PER_RUN}), "
        f"swept {swept_count}, resolved {resolved_count} memories to 'deferred', "
        f"{error_count} malformed/skipped"
    )

    summary = (
        f"Memory outcome resolve: {swept_count} sidecars swept, "
        f"{resolved_count} memories resolved to 'deferred'"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
