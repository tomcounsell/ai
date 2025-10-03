"""
Tests for Creative Juices MCP Server.
"""

import pytest

from apps.ai.mcp.creative_juices_server import (
    get_inspiration,
    think_outside_the_box,
    reality_check,
    MUSK_QUESTIONS,
)


@pytest.mark.asyncio
async def test_get_inspiration():
    """Test get_inspiration returns valid structure."""
    result = await get_inspiration()

    assert "sparks" in result
    assert "instruction" in result
    assert len(result["sparks"]) == 3
    assert all("-" in spark for spark in result["sparks"])
    assert (
        result["instruction"] == "Use these unexpected combinations as initial lenses:"
    )


@pytest.mark.asyncio
async def test_think_outside_the_box():
    """Test think_outside_the_box returns valid structure."""
    result = await think_outside_the_box()

    assert "sparks" in result
    assert "instruction" in result
    assert len(result["sparks"]) == 3
    assert all("-" in spark for spark in result["sparks"])
    assert result["instruction"] == "Shatter your assumptions with these:"


@pytest.mark.asyncio
async def test_reality_check():
    """Test reality_check returns valid structure."""
    result = await reality_check()

    assert "questions" in result
    assert "frameworks" in result
    assert "instruction" in result
    assert len(result["questions"]) == 4
    assert len(result["frameworks"]) == 4
    assert (
        result["instruction"]
        == "Ground your thinking with one question from each Musk framework:"
    )

    # Verify frameworks match expected names
    expected_frameworks = {
        "first_principles",
        "limit_thinking",
        "platonic_ideal",
        "optimization",
    }
    assert set(result["frameworks"]) == expected_frameworks


@pytest.mark.asyncio
async def test_word_lists_loaded():
    """Test that word lists are properly loaded."""
    from apps.ai.mcp.creative_juices_words import VERBS, NOUNS

    # Check both categories exist
    assert "inspiring" in VERBS
    assert "inspiring" in NOUNS
    assert "out_of_the_box" in VERBS
    assert "out_of_the_box" in NOUNS

    # Check lists have content
    assert len(VERBS["inspiring"]) > 50
    assert len(NOUNS["inspiring"]) > 50
    assert len(VERBS["out_of_the_box"]) > 50
    assert len(NOUNS["out_of_the_box"]) > 50

    # Check that words are strings
    assert all(isinstance(verb, str) for verb in VERBS["inspiring"])
    assert all(isinstance(noun, str) for noun in NOUNS["inspiring"])
    assert all(isinstance(verb, str) for verb in VERBS["out_of_the_box"])
    assert all(isinstance(noun, str) for noun in NOUNS["out_of_the_box"])


@pytest.mark.asyncio
async def test_musk_frameworks_loaded():
    """Test that Musk frameworks are properly loaded."""
    assert "first_principles" in MUSK_QUESTIONS
    assert "limit_thinking" in MUSK_QUESTIONS
    assert "platonic_ideal" in MUSK_QUESTIONS
    assert "optimization" in MUSK_QUESTIONS

    # Check each framework has questions
    for framework, questions in MUSK_QUESTIONS.items():
        assert len(questions) >= 5
        assert all(isinstance(q, str) for q in questions)
        assert all(len(q) > 10 for q in questions)  # Questions should be substantial


@pytest.mark.asyncio
async def test_get_inspiration_uses_inspiring_words():
    """Test that get_inspiration uses words from inspiring category."""
    results = []
    # Run multiple times to get variety
    for _ in range(10):
        result = await get_inspiration()
        results.extend(result["sparks"])

    # Verify all sparks are non-empty strings with hyphens
    assert all(isinstance(spark, str) for spark in results)
    assert all("-" in spark for spark in results)
    assert len(results) == 30  # 10 calls × 3 sparks each


@pytest.mark.asyncio
async def test_think_outside_the_box_uses_out_of_the_box_words():
    """Test that think_outside_the_box uses words from out_of_the_box category."""
    results = []
    # Run multiple times to get variety
    for _ in range(10):
        result = await think_outside_the_box()
        results.extend(result["sparks"])

    # Verify all sparks are non-empty strings with hyphens
    assert all(isinstance(spark, str) for spark in results)
    assert all("-" in spark for spark in results)
    assert len(results) == 30  # 10 calls × 3 sparks each


@pytest.mark.asyncio
async def test_reality_check_questions_from_frameworks():
    """Test that reality_check returns questions from correct frameworks."""
    result = await reality_check()

    # Verify each question comes from its corresponding framework
    for question, framework in zip(result["questions"], result["frameworks"]):
        assert question in MUSK_QUESTIONS[framework]


@pytest.mark.asyncio
async def test_all_tools_are_async():
    """Test that all tools are async functions."""
    import inspect

    assert inspect.iscoroutinefunction(get_inspiration)
    assert inspect.iscoroutinefunction(think_outside_the_box)
    assert inspect.iscoroutinefunction(reality_check)


@pytest.mark.asyncio
async def test_get_inspiration_randomness():
    """Test that get_inspiration produces varied results."""
    results = []
    for _ in range(5):
        result = await get_inspiration()
        results.append(tuple(result["sparks"]))

    # At least some results should be different (very high probability with random selection)
    unique_results = set(results)
    assert len(unique_results) > 1


@pytest.mark.asyncio
async def test_think_outside_the_box_randomness():
    """Test that think_outside_the_box produces varied results."""
    results = []
    for _ in range(5):
        result = await think_outside_the_box()
        results.append(tuple(result["sparks"]))

    # At least some results should be different
    unique_results = set(results)
    assert len(unique_results) > 1


@pytest.mark.asyncio
async def test_reality_check_randomness():
    """Test that reality_check produces varied results."""
    results = []
    for _ in range(5):
        result = await reality_check()
        results.append(tuple(result["questions"]))

    # At least some results should be different
    unique_results = set(results)
    assert len(unique_results) > 1
