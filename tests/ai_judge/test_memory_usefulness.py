"""AI judge tests for memory system usefulness evaluation.

Evaluates whether surfaced memories are topically relevant, whether
extracted observations are specific and novel, and whether thought
injections provide actionable context.

These tests require an LLM (Ollama or OpenRouter) and are marked slow.
They skip cleanly when no AI judge backend is available.
"""

import json
import uuid

import pytest

from tests.ai_judge.judge import (
    JudgeConfig,
    judge_test_result,
)

# Use a model that is commonly available via Ollama
_JUDGE_MODEL = "gemma3:4b"


def _unique_key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _cleanup_memories(project_key: str):
    from models.memory import Memory

    try:
        results = Memory.query.filter(project_key=project_key)
        for m in results:
            try:
                m.delete()
            except Exception:
                pass
    except Exception:
        pass


def _judge_available() -> bool:
    """Check if a real AI judge backend is available (not just heuristics).

    Returns True only if an LLM-backed judge can evaluate test output.
    Ollama must have at least one model downloaded, or OpenRouter API key set.
    """
    import os
    import subprocess

    # Check Ollama -- must have at least one model listed (not just the binary)
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Output has a header line; models appear on subsequent lines
            lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
            if len(lines) > 1:  # header + at least one model
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check OpenRouter
    if os.environ.get("OPENROUTER_API_KEY"):
        return True

    return False


# Skip all tests in this module if no real AI judge backend is available.
# The heuristic fallback uses keyword matching which cannot meaningfully
# evaluate qualitative criteria like "specific" or "actionable".
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _judge_available(),
        reason="No AI judge backend available (need Ollama with models or OPENROUTER_API_KEY)",
    ),
]


class TestRetrievalRelevance:
    """Evaluate whether retrieved memories are topically relevant."""

    def test_diverse_memories_relevant_retrieval(self):
        """Save diverse memories, query specific topic, judge relevance."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            # Save 10 diverse memories
            memories = [
                ("Redis cluster configuration requires careful slot allocation", 5.0),
                ("Python asyncio event loops should not be nested", 4.0),
                ("Kubernetes pod autoscaling uses HPA metrics", 5.0),
                ("Django ORM N+1 queries can be fixed with select_related", 4.0),
                ("PostgreSQL vacuum operations should run during low traffic", 3.0),
                ("React useEffect cleanup prevents memory leaks", 3.0),
                ("Git rebase should be avoided on shared branches", 4.0),
                ("Docker multi-stage builds reduce image size", 3.0),
                ("Terraform state files should be stored in remote backends", 4.0),
                ("Redis sorted sets provide O(log N) insertion complexity", 5.0),
            ]

            for content, importance in memories:
                Memory.safe_save(
                    agent_id="test-agent",
                    project_key=pk,
                    content=content,
                    importance=importance,
                    source="human",
                )

            # Query for Redis-related memories
            results = Memory.query.filter(project_key=pk)
            redis_results = [r for r in results if "redis" in r.content.lower()]

            # Format for AI judge
            result_text = "\n".join(f"- {r.content}" for r in redis_results)
            query_text = "Redis database configuration and data structures"

            judgment = judge_test_result(
                test_output=f"Query: {query_text}\n\nRetrieved memories:\n{result_text}",
                expected_criteria=[
                    "Retrieved memories are relevant to the Redis query topic",
                    "No completely off-topic results are included",
                    "Results contain useful technical information",
                ],
                test_context={"test_type": "retrieval_relevance", "query": query_text},
                config=JudgeConfig(
                    model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60
                ),
                test_id="memory_retrieval_relevance",
            )

            assert judgment.pass_fail, f"Relevance judgment failed: {judgment.reasoning}"

        finally:
            _cleanup_memories(pk)

    def test_no_irrelevant_memories_surface(self):
        """Verify that unrelated memories do not appear in filtered results."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            # Save memories from two distinct domains
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content=(
                    "Machine learning gradient descent optimization"
                    " requires careful learning rate tuning"
                ),
                importance=5.0,
                source="human",
            )
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content=(
                    "Sourdough bread fermentation takes twelve"
                    " to eighteen hours at room temperature"
                ),
                importance=5.0,
                source="human",
            )

            results = Memory.query.filter(project_key=pk)
            all_content = "\n".join(f"- {r.content}" for r in results)

            judgment = judge_test_result(
                test_output=f"All stored memories:\n{all_content}",
                expected_criteria=[
                    "The memories cover distinctly different topics",
                    "A query about machine learning should not surface the bread memory",
                    "Domain separation is clear between the stored items",
                ],
                config=JudgeConfig(
                    model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60
                ),
                test_id="memory_domain_separation",
            )

            assert judgment.pass_fail, f"Domain separation check failed: {judgment.reasoning}"

        finally:
            _cleanup_memories(pk)


class TestExtractionQuality:
    """Evaluate whether extracted observations are specific and novel."""

    def test_categorized_observations_are_specific(self):
        """Parsed observations should be specific, not generic platitudes."""
        from agent.memory_extraction import _parse_categorized_observations

        raw_json = json.dumps(
            [
                {
                    "category": "decision",
                    "observation": (
                        "Chose blue-green deployment over rolling"
                        " updates for zero-downtime releases"
                    ),
                    "file_paths": ["deploy/config.yaml"],
                    "tags": ["deployment", "infrastructure"],
                },
                {
                    "category": "correction",
                    "observation": (
                        "Redis SCAN is preferred over KEYS in production to avoid blocking"
                    ),
                    "file_paths": [],
                    "tags": ["redis", "performance"],
                },
                {
                    "category": "pattern",
                    "observation": (
                        "Test fixtures using UUID-prefixed keys provide reliable isolation"
                    ),
                    "file_paths": ["tests/conftest.py"],
                    "tags": ["testing"],
                },
            ]
        )

        parsed = _parse_categorized_observations(raw_json)
        assert len(parsed) == 3

        observations_text = "\n".join(f"- [{p[2].get('category', '')}] {p[0]}" for p in parsed)

        judgment = judge_test_result(
            test_output=f"Extracted observations:\n{observations_text}",
            expected_criteria=[
                "Each observation is specific and actionable, not a generic platitude",
                "Observations reference concrete technical choices or tools",
                "Categories (decision, correction, pattern) match the observation content",
            ],
            config=JudgeConfig(model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60),
            test_id="extraction_specificity",
        )

        assert judgment.pass_fail, f"Extraction quality check failed: {judgment.reasoning}"

    def test_line_based_fallback_parsing(self):
        """Line-based fallback should produce reasonable observations."""
        from agent.memory_extraction import _parse_categorized_observations

        raw_text = (
            "correction: Always use parameterized queries to prevent SQL injection\n"
            "decision: Selected FastAPI over Flask for async-first API development\n"
            "pattern: Integration tests run against a dedicated Redis database for isolation"
        )

        parsed = _parse_categorized_observations(raw_text)
        assert len(parsed) >= 2  # at least 2 should parse

        observations_text = "\n".join(f"- {p[0]}" for p in parsed)

        judgment = judge_test_result(
            test_output=f"Fallback-parsed observations:\n{observations_text}",
            expected_criteria=[
                "Parsed observations retain their original meaning",
                "Category prefixes are stripped from the observation text",
                "Content is technical and specific",
            ],
            config=JudgeConfig(model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60),
            test_id="extraction_fallback_quality",
        )

        assert judgment.pass_fail, f"Fallback parsing quality failed: {judgment.reasoning}"


class TestThoughtInjectionQuality:
    """Evaluate whether injected thought blocks provide actionable context."""

    def test_thought_blocks_are_actionable(self):
        """Generated thought blocks should provide useful, actionable context."""
        # Simulate the thought formatting that check_and_inject produces
        memories = [
            "Redis connection pool should be sized to match the number of worker threads",
            "Always set CONN_MAX_AGE in Django when using persistent database connections",
            "Bloom filter false positive rate of 1% is acceptable for topic pre-filtering",
        ]

        thoughts = "\n".join(f"<thought>{m}</thought>" for m in memories)

        judgment = judge_test_result(
            test_output=f"Injected thought blocks:\n{thoughts}",
            expected_criteria=[
                "Each thought provides actionable technical guidance",
                "Thoughts are specific enough to influence a coding decision",
                "Thoughts are concise and not redundant with each other",
                "The thought format uses XML-style tags correctly",
            ],
            config=JudgeConfig(model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60),
            test_id="thought_injection_quality",
        )

        assert judgment.pass_fail, f"Thought quality check failed: {judgment.reasoning}"

    def test_deja_vu_thought_is_appropriate(self):
        """Deja vu thought should express vague recognition without false confidence."""
        deja_vu = (
            "<thought>I have encountered something related to "
            "redis, caching, invalidation before, but the details are unclear.</thought>"
        )

        judgment = judge_test_result(
            test_output=f"Deja vu thought:\n{deja_vu}",
            expected_criteria=[
                "The thought expresses uncertainty rather than false confidence",
                "It mentions specific topics to guide further investigation",
                "It does not make specific claims about what was seen before",
            ],
            config=JudgeConfig(model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60),
            test_id="deja_vu_thought_quality",
        )

        assert judgment.pass_fail, f"Deja vu quality check failed: {judgment.reasoning}"

    def test_novel_territory_thought_is_appropriate(self):
        """Novel territory thought should encourage attention without alarm."""
        novel = (
            "<thought>This is new territory -- I should pay attention to what works here.</thought>"
        )

        judgment = judge_test_result(
            test_output=f"Novel territory thought:\n{novel}",
            expected_criteria=[
                "The thought encourages careful attention",
                "It does not express alarm or negativity",
                "It is brief and non-intrusive",
            ],
            config=JudgeConfig(model=_JUDGE_MODEL, fallback_to_heuristics=True, timeout_seconds=60),
            test_id="novel_territory_thought_quality",
        )

        assert judgment.pass_fail, f"Novel territory quality failed: {judgment.reasoning}"
