"""Two-arm retrieval adapters for the hybrid-retrieval eval harness.

Two measurement arms, matching the plan's Decision Record item 3:
  - ``run_current_arm``: today's four-signal RRF path
    (``agent/memory_retrieval.py::retrieve_memories``).
  - ``run_hybrid_arm``: forced ``retrieval_mode='hybrid'`` via
    ``ContextAssembler.assemble()`` (spike-1's confirmed call shape).

``retrieval_mode='auto'`` is NOT a third measurement arm -- see
:func:`assert_auto_resolves_to_hybrid`, a single schema-level assertion
(NIT / Decision Record item 3).

Both arm functions distinguish an ERRORED query (excluded from scoring)
from a genuinely EMPTY result (a legitimate zero-recall data point) --
Failure Path Test Strategy's "errored-vs-empty-arm distinction". Any
exception inside either arm is logged and returns ``errored=True``; it
never masquerades as a scored empty result.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ZeroVectorContributionError(RuntimeError):
    """Raised when the forced-hybrid arm's vector signal did not fire for a
    query built from a genuinely (current-provider-dimension) embedded
    record.

    Concern 1: this is a DEGRADATION ERROR, not a scored data point. The
    caller (``run_hybrid_arm``) catches this internally and returns
    ``errored=True`` so it is excluded from scoring, never recorded as a
    legitimate tie/loss for the hybrid arm.
    """


@dataclass
class ArmQueryResult:
    """One retrieval arm's result for a single query.

    ``errored=True`` means this query is EXCLUDED from scoring entirely --
    it is not the same as a genuinely empty ``memory_ids`` list (a
    legitimate zero-recall data point with ``errored=False``).
    """

    memory_ids: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    errored: bool = False
    error_message: str | None = None


def run_current_arm(query_text: str, project_key: str, k: int) -> ArmQueryResult:
    """Run the current four-signal RRF path (``retrieve_memories``).

    ``retrieve_memories`` is itself fail-silent internally (returns ``[]``
    on any internal signal failure, per its own docstring), so from this
    adapter's perspective an internal degradation is indistinguishable from
    a genuinely empty result -- both surface as ``errored=False,
    memory_ids=[]``. This wrapper's own try/except exists to catch failures
    OUTSIDE that fail-silent contract (e.g. import errors, a bad
    ``project_key``) and to let unit tests exercise the errored path by
    monkeypatching ``retrieve_memories`` to raise.
    """
    from agent.memory_retrieval import retrieve_memories

    start = time.monotonic()
    try:
        records = retrieve_memories(query_text, project_key, limit=k)
        latency_ms = (time.monotonic() - start) * 1000
        memory_ids = [mid for r in records if (mid := getattr(r, "memory_id", None)) is not None]
        return ArmQueryResult(memory_ids=memory_ids, latency_ms=latency_ms, errored=False)
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("[memory_eval] current arm errored for query=%r: %s", query_text, e)
        return ArmQueryResult(
            memory_ids=[], latency_ms=latency_ms, errored=True, error_message=str(e)
        )


def vector_signal_available(query_text: str, project_key: str) -> bool:
    """Probe whether popoto's hybrid vector search would return a non-empty
    signal for this query/partition.

    Calls the SAME internal method popoto's ``ContextAssembler._pull_path_hybrid``
    uses (``QueryBuilder._get_vector_scores``) -- not a reimplementation.
    ``AssemblyResult.metadata`` does not expose a "did the vector signal
    fire" field (confirmed by spike-1's live metadata:
    ``{"pull_count", "push_count", "token_count", "timing_ms",
    "total_candidates"}``), so this probe is the harness's own concrete
    stand-in for Concern 1's "per-record non-zero vector contribution"
    assertion.

    Returns ``False`` (never raises) on ANY failure, including a
    provider/corpus dimension-mismatch failure inside the vector-scoring
    path -- such a failure IS a zero-vector-contribution signal for this
    probe's purposes, exactly mirroring how ``_pull_path_hybrid`` itself
    catches it and degrades to BM25-only.
    """
    from models.memory import Memory

    try:
        vector_results = Memory.query.filter(project_key=project_key)._get_vector_scores(
            query_text, limit=50
        )
        return len(vector_results) > 0
    except Exception as e:
        logger.warning("[memory_eval] vector-signal probe failed for query=%r: %s", query_text, e)
        return False


def run_hybrid_arm(
    query_text: str,
    project_key: str,
    k: int,
    *,
    assert_nonzero_vector: bool = False,
) -> ArmQueryResult:
    """Run the forced ``retrieval_mode='hybrid'`` path via ``ContextAssembler.assemble()``.

    Matches spike-1's confirmed call shape:
    ``ContextAssembler(Memory, {}, retrieval_mode="hybrid",
    max_items=k).assemble(query_cues={"query": query_text},
    partition_filters={"project_key": project_key})`` -> ``AssemblyResult.records``.

    When ``assert_nonzero_vector`` is True (embedded-subset queries only,
    per Concern 1), :func:`vector_signal_available` is probed first; a
    zero-vector-contribution query raises
    :class:`ZeroVectorContributionError`, caught below and returned as
    ``errored=True`` -- never scored as a legitimate tie/loss.
    """
    from popoto.recipes.context_assembler import ContextAssembler

    from models.memory import Memory

    start = time.monotonic()
    try:
        if assert_nonzero_vector and not vector_signal_available(query_text, project_key):
            raise ZeroVectorContributionError(
                "Forced-hybrid arm has zero vector contribution for an "
                f"embedded-subset query {query_text!r} -- this is a "
                "degradation error (Concern 1), never a scored data point."
            )

        assembler = ContextAssembler(Memory, {}, retrieval_mode="hybrid", max_items=k)
        result = assembler.assemble(
            query_cues={"query": query_text},
            partition_filters={"project_key": project_key},
        )
        latency_ms = (time.monotonic() - start) * 1000
        memory_ids = [
            mid for r in result.records if (mid := getattr(r, "memory_id", None)) is not None
        ]
        return ArmQueryResult(memory_ids=memory_ids, latency_ms=latency_ms, errored=False)
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("[memory_eval] hybrid arm errored for query=%r: %s", query_text, e)
        return ArmQueryResult(
            memory_ids=[], latency_ms=latency_ms, errored=True, error_message=str(e)
        )


def assert_auto_resolves_to_hybrid() -> None:
    """Single assertion (not a third measurement arm) that
    ``retrieval_mode='auto'`` resolves to ``'hybrid'`` for ``Memory``.

    popoto resolves ``auto`` -> ``hybrid`` at schema level whenever a model
    has BOTH a ``BM25Field`` and an ``EmbeddingField`` (always true for
    ``Memory``), so a full ``auto`` measurement arm would duplicate
    forced-``hybrid`` and add no gate signal (NIT / Decision Record item
    3). This assertion de-risks the IF-WIN cutover (which ships ``auto``)
    without running a third arm.

    Raises ``AssertionError`` if ``auto`` does not resolve to ``hybrid`` --
    that would mean ``Memory`` no longer has both fields, which should stop
    the harness immediately since the whole eval's premise (measuring
    hybrid) would no longer apply.
    """
    from popoto.recipes.context_assembler import ContextAssembler

    from models.memory import Memory

    assembler = ContextAssembler(Memory, {}, retrieval_mode="auto")
    assert assembler._effective_mode == "hybrid", (
        f"retrieval_mode='auto' resolved to {assembler._effective_mode!r}, not "
        "'hybrid', for Memory -- Memory no longer has both a BM25Field and an "
        "EmbeddingField, invalidating this eval's premise."
    )
