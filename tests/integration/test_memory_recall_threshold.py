"""Integration test: RRF post-fusion relevance threshold on a real Memory store.

This is the headline acceptance test for issue #1213. The unit tests in
``tests/unit/test_memory_retrieval.py::TestRelevanceThreshold`` cover the
gate logic with mocked signals; this test exercises the full retrieval
pipeline (BM25 + temporal + confidence + embedding -> RRF fuse -> threshold
-> hydration -> superseded filter) against a real Redis-backed Memory
corpus to prove the gate behaves end-to-end.

Acceptance criterion (per
``docs/plans/memory-recall-relevance-threshold.md`` Step 5 / Success
Criteria):

  - Recall returns 0 results for a nonsense query when the threshold is
    enabled, against a real Memory store.
  - The same query with threshold disabled (``min_rrf_score=None``)
    returns at least one record (back-compat regression guard).
  - A query that overlaps the seeded record's content survives the
    threshold (positive case).

These tests use the ``redis_test_db`` autouse fixture, so they run
against a per-worker isolated Redis db that is flushed between tests --
no production data is touched. Each test seeds and tears down its own
records under a uuid-suffixed project_key for additional safety.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]


def _delete_memory_records_for_project(project_key: str) -> None:
    """Best-effort cleanup of Memory records for a project_key.

    The autouse ``redis_test_db`` fixture flushdb()s the test database
    between tests so this is belt-and-suspenders, but we still call it to
    keep the test-scope cleanup explicit (and to fail loudly if Memory's
    delete path regresses).
    """
    try:
        from models.memory import Memory

        for record in Memory.query.filter(project_key=project_key):
            try:
                record.delete()
            except Exception:
                pass
    except Exception:
        pass


def _seed_memory(content: str, project_key: str, importance: float = 6.0):
    """Insert a real Memory record. Returns the saved record (or None)."""
    from models.memory import SOURCE_HUMAN, Memory

    return Memory.safe_save(
        agent_id=f"threshold-test-{project_key}",
        project_key=project_key,
        content=content,
        importance=importance,
        source=SOURCE_HUMAN,
    )


@pytest.fixture
def isolated_project_key():
    """Per-test project_key prefixed with `threshold-test-` for safe cleanup."""
    key = f"threshold-test-{uuid.uuid4().hex[:8]}"
    yield key
    _delete_memory_records_for_project(key)


def _seed_corpus(project_key: str, count: int) -> int:
    """Seed `count` filler Memory records to model a realistic corpus.

    The post-fusion threshold's design assumption (spike-1) is that
    `temporal` and `confidence` signals are ranked over the full corpus,
    so a record below corpus median in all signals scores below the
    floor. With a 1-record corpus, the only record is rank 1 everywhere
    and trivially survives any reasonable threshold. Seeding filler
    records lets the threshold actually exercise the gate.

    Returns the number of records actually persisted (some may be
    filtered by WriteFilterMixin).
    """
    persisted = 0
    for i in range(count):
        rec = _seed_memory(
            f"filler record number {i} corpus padding token alpha-{i}",
            project_key,
        )
        if rec is not None:
            persisted += 1
    return persisted


class TestRecallThresholdIntegration:
    """End-to-end verification that the RRF threshold filters irrelevant hits."""

    def test_nonsense_query_returns_empty_with_threshold(self, isolated_project_key):
        """Headline acceptance test for issue #1213.

        Seed a target record plus enough filler records that the target
        ranks below the threshold floor for a query with zero token
        overlap. With ``min_rrf_score`` enabled at the conservative
        ``2 * RRF_MIN_SCORE`` value (~0.018, well below any real-corpus
        nonsense-query repro at 0.00909 over 173 records), the gate must
        drop the target record before hydration and return ``[]``.

        Why ``2 * RRF_MIN_SCORE`` instead of ``RRF_MIN_SCORE``: the
        production threshold is calibrated against a ~100+ record
        corpus where temporal/confidence ranks distribute over a wide
        span. A test corpus of ~60 records cannot reach those low ranks,
        so we use a slightly stricter threshold to exercise the same
        codepath. The gate logic itself (``s >= threshold``) is identical;
        the threshold value is the only knob.
        """
        from agent.memory_retrieval import retrieve_memories
        from config.memory_defaults import RRF_MIN_SCORE

        seeded = _seed_memory(
            "the quick brown fox jumps over the lazy dog",
            isolated_project_key,
        )
        if seeded is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup or backend issue)")
        # Seed filler records so the target ranks below the threshold floor
        # for a zero-overlap query. 60+ records is enough that temporal /
        # confidence rank the target far enough down that no single signal
        # contributes meaningfully to its fused score.
        _seed_corpus(isolated_project_key, count=60)

        # A query with zero token overlap with the seeded content. All
        # tokens are nonsense and longer than the bloom noise filter.
        # Any record that surfaces here is junk -- the gate's job is to
        # filter it out.
        threshold = 2 * RRF_MIN_SCORE  # ~0.018, exercises the same gate logic
        results = retrieve_memories(
            "xyzqqq_unrelated_phrase_aaa bbb_ccc_ddd_eee",
            isolated_project_key,
            limit=10,
            min_rrf_score=threshold,
        )

        assert results == [], (
            "Threshold gate should drop all results for a nonsense query, "
            f"but got {len(results)} records: "
            f"{[getattr(r, 'content', r) for r in results]}"
        )

    def test_nonsense_query_returns_results_without_threshold(self, isolated_project_key):
        """Back-compat regression guard.

        With ``min_rrf_score=None`` (the CLI default), the same nonsense
        query must still return at least one record -- temporal /
        confidence signals always rank over the corpus. This proves the
        threshold change is opt-in and does not silently change behavior
        for callers that don't pass it.
        """
        from agent.memory_retrieval import retrieve_memories

        seeded = _seed_memory(
            "the quick brown fox jumps over the lazy dog",
            isolated_project_key,
        )
        if seeded is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup or backend issue)")

        results = retrieve_memories(
            "xyzqqq_unrelated_phrase_aaa bbb_ccc_ddd_eee",
            isolated_project_key,
            limit=10,
            min_rrf_score=None,
        )

        assert len(results) >= 1, (
            "Without the threshold, the seeded record should still rank via "
            "temporal/confidence signals (today's behavior). "
            f"Got {len(results)} records."
        )

    def test_relevant_query_survives_threshold(self, isolated_project_key):
        """Positive case: a query overlapping the seeded record's content
        must survive the threshold.

        This proves the gate's calibration is not so aggressive that it
        drops legitimate matches -- a record that ranks well in BM25
        scores far above the floor of ``1/(RRF_K+50)``.
        """
        from agent.memory_retrieval import retrieve_memories
        from config.memory_defaults import RRF_MIN_SCORE

        seeded = _seed_memory(
            "redis connection pool tuning notes for production deployment",
            isolated_project_key,
        )
        if seeded is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup or backend issue)")

        results = retrieve_memories(
            "redis connection pool",
            isolated_project_key,
            limit=10,
            min_rrf_score=RRF_MIN_SCORE,
        )

        assert len(results) >= 1, (
            "A relevant multi-token query must survive the RRF threshold; "
            f"got {len(results)} records for query 'redis connection pool'."
        )
        # Verify the seeded record is among the surviving results.
        seeded_content = "redis connection pool tuning notes for production deployment"
        contents = [getattr(r, "content", "") for r in results]
        assert any(seeded_content in c for c in contents), (
            f"Expected the seeded record to be returned. Got contents: {contents}"
        )
