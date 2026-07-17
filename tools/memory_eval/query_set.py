"""Ground-truth construction for the hybrid-retrieval eval harness.

Two complementary constructions, per docs/plans/hybrid-retrieval-eval.md:

1. **Known-item set (REQUIRED, sole gate driver).** Sample N valor memories
   (restricted to records whose stored embedding matches the CURRENT
   provider's dimension, biased toward higher importance), and LLM-generate
   one realistic natural-language query per record whose answer is that
   record. The (query -> gold memory_id) pair is an objective label that is
   independent of either retriever, so it cannot favor one arm.

2. **Pooled 0-3 judgments (CONDITIONAL corroboration).** Built only when
   the known-item result lands near the decision threshold (proximity
   check, plan Concern 4). Pools the union of both arms' top-k and grades
   each (query, memory) pair 0-3 for nDCG. Pooling -- rather than judging a
   single arm's results -- is what keeps the graded metric fair.

LLM calls route through the repo's PydanticAI non-harness path
(``agent.llm.run_typed``, Haiku by default). Degenerate generations
(empty/too-short queries, or queries that just parrot the memory verbatim)
are SKIPPED, never scored (plan: Empty/Invalid Input Handling).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Minimum character length for a generated query to count as non-degenerate.
# Provisional/tunable: short enough to admit terse-but-real queries, long
# enough to reject empty/one-word junk. Grain of salt applies.
MIN_QUERY_CHARS = 12

# Cap on memory content passed into a generation/judging prompt. Provisional/
# tunable -- keeps prompt cost bounded on long memories without losing the
# lede. Grain of salt applies.
MAX_CONTENT_CHARS = 1200


class GeneratedQuery(BaseModel):
    """Schema for the known-item query-generation LLM call."""

    query: str = Field(description="A realistic natural-language search query")


class RelevanceGrade(BaseModel):
    """Schema for the pooled-judgment grading LLM call."""

    grade: int = Field(ge=0, le=3, description="Relevance grade 0-3")


@dataclass(frozen=True)
class KnownItem:
    """One known-item ground-truth pair: query text -> gold memory id."""

    query: str
    gold_memory_id: str


_GENERATION_PROMPT = """You are constructing a retrieval evaluation set for an \
AI agent's long-term memory system. Below is one stored memory. Write ONE \
realistic natural-language search query that a user or agent might type when \
they need exactly this memory back.

Rules:
- The query must be answerable by THIS memory (it is the gold answer).
- Do NOT copy sentences verbatim from the memory; paraphrase the information
  need the way a real searcher would.
- Keep it short: one line, no markdown, no quotes around it.

Memory content:
---
{content}
---
"""

_GRADING_PROMPT = """You are judging retrieval relevance for an AI agent's \
long-term memory system. Grade how relevant the memory below is to the query, \
on this scale:

0 = irrelevant (does not help answer the query at all)
1 = marginally related (same broad topic, does not answer it)
2 = relevant (partially answers or strongly supports answering)
3 = perfectly relevant (directly and completely answers the query)

Query: {query}

Memory content:
---
{content}
---
"""


def _is_degenerate(query: str, source_content: str) -> bool:
    """True when a generated query must be skipped (never scored)."""
    q = (query or "").strip()
    if len(q) < MIN_QUERY_CHARS:
        return True
    # Verbatim parroting: the "query" is just a slice of the memory itself.
    if q.lower() in source_content.lower():
        return True
    return False


async def _generate_one(content: str) -> str | None:
    """Generate one known-item query; None on degenerate output or LLM failure."""
    from agent.llm import LLMCallError, run_typed

    prompt = _GENERATION_PROMPT.format(content=content[:MAX_CONTENT_CHARS])
    try:
        result = await run_typed(prompt, GeneratedQuery)
    except (LLMCallError, ValueError) as e:
        logger.warning("[memory_eval] known-item generation failed: %s", e)
        return None
    query = result.query.strip()
    if _is_degenerate(query, content):
        logger.info("[memory_eval] skipping degenerate generated query: %r", query[:80])
        return None
    return query


def build_known_item_set(records: list, *, n_queries: int, seed: int) -> list[KnownItem]:
    """Build the known-item ground-truth set from a corpus snapshot.

    ``records`` must already be filtered to current-provider-dimension-valid
    embedded records (the caller owns coverage filtering). Sampling is
    deterministic for a given seed and biased toward higher ``importance``
    (importance-weighted sampling without replacement).
    """
    import random

    candidates = [r for r in records if (getattr(r, "content", "") or "").strip()]
    if not candidates:
        return []

    rng = random.Random(seed)
    # Importance-weighted sampling without replacement.
    pool = list(candidates)
    weights = [max(0.1, float(getattr(r, "importance", 1.0) or 1.0)) for r in pool]
    sampled = []
    while pool and len(sampled) < n_queries:
        idx = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        sampled.append(pool.pop(idx))
        weights.pop(idx)

    async def _generate_all() -> list[KnownItem]:
        items: list[KnownItem] = []
        for record in sampled:
            content = getattr(record, "content", "") or ""
            query = await _generate_one(content)
            if query is None:
                continue
            items.append(KnownItem(query=query, gold_memory_id=record.memory_id))
        return items

    items = asyncio.run(_generate_all())
    logger.info(
        "[memory_eval] known-item set: %d queries from %d sampled records "
        "(%d skipped as degenerate/errored)",
        len(items),
        len(sampled),
        len(sampled) - len(items),
    )
    return items


def build_pooled_judgments(
    query: str,
    pooled_records: dict[str, str],
) -> dict[str, int]:
    """Grade a pooled union of retrieved memories 0-3 against ``query``.

    ``pooled_records`` maps memory_id -> content for the union of both
    arms' top-k. A grading failure for one pair drops that pair from the
    judgment dict (logged) rather than fabricating a grade.
    """
    from agent.llm import LLMCallError, run_typed

    async def _grade_all() -> dict[str, int]:
        grades: dict[str, int] = {}
        for memory_id, content in pooled_records.items():
            prompt = _GRADING_PROMPT.format(query=query, content=content[:MAX_CONTENT_CHARS])
            try:
                result = await run_typed(prompt, RelevanceGrade)
            except (LLMCallError, ValueError) as e:
                logger.warning(
                    "[memory_eval] pooled grading failed for memory_id=%s: %s", memory_id, e
                )
                continue
            grades[memory_id] = int(result.grade)
        return grades

    return asyncio.run(_grade_all())
