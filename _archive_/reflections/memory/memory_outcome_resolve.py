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
clean-session-stop), and then removes the sidecar with a compare-and-delete.

TTL-only liveness gating: there is no session-liveness helper importable from
memory_bridge.py (confirmed by grep -- no is_session_live/session_live/is_live
symbol). A live session keeps its sidecar mtime fresh because `recall()`
rewrites the sidecar via `_save_sidecar` on every recall injection
(memory_bridge.py's PostToolUse path). `INJECTION_RESOLVE_TTL` is set with
headroom over the max plausible gap between recall injections in a live
session; even a mis-estimate is harmless because "deferred" is a no-op
outcome -- the TTL protects against churn, not correctness.

Compare-and-delete cleanup (CONCERN 2 -- why NOT `cleanup_sidecar`): a crashed
session can be RESUMED. On resume, `recall()` rewrites the same sidecar with a
fresh `injected[]` list. The blind `hook_utils.memory_bridge.cleanup_sidecar`
unlinks unconditionally, so if the sweep read the stale sidecar, resolved its
injections, then blindly unlinked, it would DESTROY the resumed session's new
injections (they would never get an outcome -- the exact bug this reflection
fixes). Instead the sweep captures `mtime_at_read` when it reads the file and,
immediately before unlinking, re-`stat()`s: it unlinks ONLY IF the mtime is
unchanged. If a resume rewrote the file (mtime moved), the sidecar is left in
place for the resumed session's own Stop hook (or a later sweep) to handle. No
liveness primitive required.

Malformed-sidecar handling (ADVISORY 3): a sidecar crashed mid-write is not
valid JSON. Rather than retry it unbounded every tick, the sweep QUARANTINES it
(renames it to a `.corrupt` sibling so the next pass -- which only matches the
exact `memory_buffer.json` name -- never re-scans it) and increments the
`{DEFAULT_PROJECT_KEY}:memory-gate:outcome_resolve_malformed_count` counter via
the same `models.memory_gate._increment_gate_counter` pattern the write-gate and
prune reflections use. One bad sidecar never aborts the whole sweep.

Cadence: recommended daily-or-faster (see reflections.yaml, vault).
Failure modes:
    - sidecar directory missing/unreadable -> return {"status": "ok", ...},
      zero resolved (nothing to sweep is not an error)
    - a single sidecar is malformed/partial (crashed mid-write) -> quarantined
      to `.corrupt`, counter incremented, sweep continues with the next sidecar
    - an unresolvable memory_id (tier-1 hard-deleted between injection and
      sweep) -> that id is skipped, the rest of the sidecar still resolves
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

import json
import logging
import sys
import time as _time
from pathlib import Path

logger = logging.getLogger("reflections.memory_management")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HOOKS_DIR = _PROJECT_ROOT / ".claude" / "hooks"

_SIDECAR_FILENAME = "memory_buffer.json"
_CORRUPT_SUFFIX = ".corrupt"


def _import_memory_bridge():
    """Import `hook_utils.memory_bridge`, adding `.claude/hooks` to sys.path.

    `memory_bridge.py` lives on the hook-local sys.path (`.claude/hooks`), not
    a normally-importable package from `reflections/`. This mirrors the exact
    sys.path pattern the hook scripts themselves use (see `.claude/hooks/stop.py`)
    to import the real module and reuse its sidecar-directory layout as the
    single source of truth (rather than duplicating the path and risking drift).
    """
    hooks_dir = str(_HOOKS_DIR)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from hook_utils import memory_bridge

    return memory_bridge


def _quarantine_sidecar(sidecar_path: Path) -> None:
    """Move a malformed/partial sidecar aside so it is never re-scanned.

    Renames `memory_buffer.json` -> `memory_buffer.json.corrupt` (overwriting
    any prior quarantine). The next sweep only matches the exact
    `memory_buffer.json` name, so the quarantined file is inert -- this bounds a
    crashed-mid-write sidecar from being retried unbounded (ADVISORY 3). The
    corrupt bytes are preserved (not unlinked) for forensics. Best-effort: a
    filesystem hiccup during quarantine must never abort the sweep.
    """
    try:
        target = sidecar_path.with_name(sidecar_path.name + _CORRUPT_SUFFIX)
        sidecar_path.replace(target)
    except OSError as e:
        logger.debug("Memory outcome resolve: could not quarantine %s: %s", sidecar_path, e)


def _resolve_injections(injected: list) -> tuple[int, str | None]:
    """Resolve a parsed `injected[]` list to the neutral "deferred" outcome.

    Returns (resolved_count, error_or_None). `resolved_count` is the number of
    Memory instances actually fed through ObservationProtocol.on_context_used
    for this sidecar (0 when the sidecar had no resolvable entries -- e.g. an
    empty `injected[]`, or every named memory_id has since been hard-deleted).

    Keying contract (issue #2203): `on_context_used` keys its outcome map by
    each instance's REDIS KEY, not by memory_id -- an unmatched instance
    silently defaults to "deferred", which would MASK a mis-key. This reuses
    the exact clean-stop pattern from `agent.memory_extraction.detect_outcomes_async`:
    build `redis_outcome_map[m.db_key.redis_key] = "deferred"` and pass
    `(instances, redis_outcome_map)`.

    An unresolvable memory_id (tier-1 hard-deleted between injection and sweep)
    is skipped via the empty-`filter` result and the `continue`, never aborting
    the sidecar (ADVISORY 2). Any unexpected exception is caught and reported
    via the second tuple element so the caller can log-and-continue.
    """
    try:
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
                # ADVISORY 2: a memory_id that no longer resolves (tier-1
                # hard-deleted between injection and sweep) yields an empty
                # result -- skip it, never abort the whole sidecar.
            except Exception:  # noqa: S112 -- memory ops silent by design
                continue

        if not memories:
            return 0, None

        # Key the outcome map by REDIS KEY (not memory_id) -- see docstring.
        redis_outcome_map = {}
        for m in memories:
            mid = getattr(m, "memory_id", "")
            if mid in outcome_map:
                redis_key = getattr(m.db_key, "redis_key", "")
                if redis_key:
                    redis_outcome_map[redis_key] = outcome_map[mid]

        if redis_outcome_map:
            ObservationProtocol.on_context_used(memories, redis_outcome_map)

        # Record last_outcome/outcome_history without touching dismissal_count
        # (the "deferred" branch is a no-op on the decay counters). This
        # outcome_map is memory_id-keyed, which is _persist_outcome_metadata's
        # own contract (distinct from on_context_used's redis-key contract).
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
    from `hook_utils.memory_bridge._get_sidecar_dir`), selecting sidecars whose
    mtime exceeds `INJECTION_RESOLVE_TTL` -- TTL-only gating, no session-liveness
    dependency (see module docstring). For each stale sidecar:

      1. Capture `mtime_at_read` and read the file raw.
      2. If the JSON is malformed/partial, QUARANTINE it (rename to `.corrupt`)
         and increment `outcome_resolve_malformed_count` -- never retry it.
      3. Otherwise resolve unresolved `injected[]` entries to the neutral
         "deferred" outcome via `ObservationProtocol.on_context_used` +
         `agent.memory_extraction._persist_outcome_metadata`.
      4. COMPARE-AND-DELETE: re-`stat()`; unlink ONLY IF the mtime is unchanged.
         A resumed session that rewrote the sidecar (mtime moved) is left in
         place -- its new injections are handled by its own Stop hook or a
         later sweep (CONCERN 2 -- the resume race).

    Idempotent (a resolved-and-deleted sidecar no longer exists to re-process)
    and fail-silent per sidecar (a malformed file is quarantined, an unexpected
    resolution error is logged, and neither aborts the sweep). Bounded by
    `OUTCOME_RESOLVE_MAX_PER_RUN` per invocation. Returns a dict with a
    `resolved`/`swept`/`malformed` count (observable behavior, not just
    "didn't crash").
    """
    from config.memory_defaults import (
        DEFAULT_PROJECT_KEY,
        INJECTION_RESOLVE_TTL,
        OUTCOME_RESOLVE_MAX_PER_RUN,
    )
    from models.memory_gate import _increment_gate_counter

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
        return {
            "status": "ok",
            "findings": [],
            "summary": summary,
            "swept": 0,
            "resolved": 0,
            "malformed": 0,
        }

    now = _time.time()
    cutoff = now - INJECTION_RESOLVE_TTL

    stale_paths: list[Path] = []
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
                stale_paths.append(sidecar_path)
    except Exception as e:
        logger.warning(f"Memory outcome resolve: could not scan sidecar directory: {e}")
        return {"status": "error", "findings": [], "summary": f"Scan error: {e}"}

    capped = stale_paths[:OUTCOME_RESOLVE_MAX_PER_RUN]

    swept_count = 0
    resolved_count = 0
    error_count = 0
    malformed_count = 0
    resumed_skipped_count = 0

    for sidecar_path in capped:
        session_id = sidecar_path.parent.name

        # Capture mtime BEFORE any work so the compare-and-delete below can
        # detect a concurrent resume that rewrote the file.
        try:
            mtime_at_read = sidecar_path.stat().st_mtime
            raw = sidecar_path.read_text()
        except OSError:
            # Vanished between scan and read (a resume/other sweep won the race).
            continue

        # Explicit malformed detection -- do NOT delegate to _load_sidecar,
        # which swallows json.JSONDecodeError and returns an empty default
        # (that would silently DELETE a corrupt sidecar instead of quarantining
        # it, and never bump the malformed counter). ADVISORY 3.
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("sidecar JSON is not an object")
            injected = data.get("injected", [])
            if not isinstance(injected, list):
                injected = []
        except (json.JSONDecodeError, ValueError) as e:
            malformed_count += 1
            _quarantine_sidecar(sidecar_path)
            _increment_gate_counter(DEFAULT_PROJECT_KEY, "outcome_resolve_malformed_count")
            logger.warning(
                f"Memory outcome resolve: sidecar {session_id!r} malformed/partial, "
                f"quarantined to {_SIDECAR_FILENAME}{_CORRUPT_SUFFIX}: {e}"
            )
            continue

        resolved, error = _resolve_injections(injected)
        if error is not None:
            error_count += 1
            logger.warning(
                f"Memory outcome resolve: resolution error for sidecar {session_id!r} "
                f"(cleanup still evaluated): {error}"
            )
        resolved_count += resolved

        # COMPARE-AND-DELETE (CONCERN 2): only unlink if the file has not been
        # rewritten since we read it. A resumed session rewrites the sidecar
        # via recall(); deleting it then would destroy the resumed session's
        # fresh injections. If the mtime moved, leave the sidecar for the
        # resumed session's own Stop hook or a later sweep.
        try:
            current_mtime = sidecar_path.stat().st_mtime
        except OSError:
            # Already removed by a concurrent resume/sweep -- nothing to do.
            swept_count += 1
            continue

        if current_mtime == mtime_at_read:
            try:
                sidecar_path.unlink()
                swept_count += 1
            except OSError as e:
                logger.debug(f"Memory outcome resolve: unlink failed for {session_id!r}: {e}")
        else:
            resumed_skipped_count += 1
            logger.debug(
                f"Memory outcome resolve: sidecar {session_id!r} was rewritten "
                "(resume race) -- leaving it in place for the resumed session"
            )

    findings.append(
        f"Scanned {len(stale_paths)} stale sidecars (cap={OUTCOME_RESOLVE_MAX_PER_RUN}), "
        f"swept {swept_count}, resolved {resolved_count} memories to 'deferred', "
        f"{malformed_count} malformed/quarantined, {resumed_skipped_count} left "
        f"(resume race), {error_count} resolution errors"
    )

    summary = (
        f"Memory outcome resolve: {swept_count} sidecars swept, "
        f"{resolved_count} memories resolved to 'deferred', "
        f"{malformed_count} malformed/quarantined"
    )
    logger.info(summary)
    return {
        "status": "ok",
        "findings": findings,
        "summary": summary,
        "swept": swept_count,
        "resolved": resolved_count,
        "malformed": malformed_count,
    }
