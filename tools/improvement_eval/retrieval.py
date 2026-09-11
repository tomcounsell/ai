"""Isolated retrieval adapter and the baseline parity gate (#3216).

The adapter runs ``agent.memory_retrieval.retrieve_memories`` — the
four-signal RRF path whose every input is persisted state (BM25, stored
relevance scores, confidence, on-disk embeddings) — and returns the ranked
memory ids. Ranking never goes through the decay-clock query path whose
clock cannot be pinned and which would make the harness irreproducible by
construction.

Gate ordering is load-bearing: :func:`baseline_parity` runs on the
incumbent arm before the candidate arm is ever invoked. A miss raises
:class:`InfraFailure`, and the candidate number is never read.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InfraFailure


@dataclass(frozen=True)
class RankedBaseline:
    """Recorded incumbent ranking a frozen corpus must reproduce."""

    ids: list[str]
    corpus_digest: str


def retrieve_ranked_ids(query_text: str, project_key: str, *, limit: int = 10) -> list[str]:
    """Return the ranked memory ids for one retrieval input.

    Thin adapter over the production retrieval path; the ids (not scores)
    are what the parity gate compares, because a float equality across two
    processes fails for reasons nobody wants to debug.
    """
    from agent.memory_retrieval import retrieve_memories

    records = retrieve_memories(query_text, project_key, limit=limit)
    ranked = []
    for record in records:
        memory_id = getattr(record, "memory_id", None)
        ranked.append(str(memory_id) if memory_id is not None else "")
    return ranked


def baseline_parity(retrieved_ids: list[str], baseline: RankedBaseline, corpus_digest: str) -> None:
    """Assert the incumbent reproduces its recorded baseline on this corpus.

    Compares the ordered retrieved ids against the baseline captured under the
    same corpus digest. Any miss — a digest mismatch or a ranking mismatch —
    raises :class:`InfraFailure` before any candidate result is read.
    """
    if corpus_digest != baseline.corpus_digest:
        raise InfraFailure(
            "baseline parity: corpus digest "
            f"{corpus_digest} does not match the digest "
            f"{baseline.corpus_digest} the baseline was captured under"
        )
    if list(retrieved_ids) != list(baseline.ids):
        raise InfraFailure(
            "baseline parity: incumbent ranking "
            f"{list(retrieved_ids)} does not reproduce the recorded baseline "
            f"{list(baseline.ids)} on corpus {corpus_digest}"
        )
    return None
