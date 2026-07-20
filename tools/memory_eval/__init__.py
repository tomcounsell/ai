"""Read-only offline evaluation harness for popoto hybrid retrieval vs. the
current four-signal RRF memory-recall path.

See docs/plans/hybrid-retrieval-eval.md (methodology) and
docs/features/hybrid-retrieval-eval.md (measured results + verdict, written
by the run-eval task). Entry point: ``python -m tools.memory_eval.hybrid_eval``.

This package is a dev-invoked measurement tool, never imported by the live
recall path (``agent/memory_retrieval.py``, ``tools/memory_search/``).
"""
