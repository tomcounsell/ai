"""
Integration tests for test-judge tool.

Run with: pytest tools/test-judge/tests/ -v
"""

import os

import pytest

from tools.test_judge import create_quality_gate, judge_batch, judge_test_result


class TestJudgeInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.test_judge import judge_test_result

        assert callable(judge_test_result)

    def test_api_key_required(self):
        """Tool returns error when API keys missing."""
        original_anthropic = os.environ.get("ANTHROPIC_API_KEY")
        original_openrouter = os.environ.get("OPENROUTER_API_KEY")

        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        if "OPENROUTER_API_KEY" in os.environ:
            del os.environ["OPENROUTER_API_KEY"]

        try:
            result = judge_test_result("test output", ["criterion"])
            assert "error" in result
        finally:
            if original_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = original_anthropic
            if original_openrouter:
                os.environ["OPENROUTER_API_KEY"] = original_openrouter


class TestJudgeValidation:
    """Test input validation."""

    def test_empty_output(self):
        """Empty test output returns error."""
        result = judge_test_result("", ["criterion"])
        assert "error" in result

    def test_empty_criteria(self):
        """Empty criteria returns error."""
        result = judge_test_result("test output", [])
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestJudgeCore:
    """Test core judgment functionality."""

    def test_basic_judgment(self):
        """Basic judgment returns expected structure."""
        result = judge_test_result(
            test_output="The function correctly adds 2 and 3 to get 5.",
            expected_criteria=[
                "Describes a mathematical operation",
                "Mentions the result",
            ],
        )

        assert "error" not in result, f"Judgment failed: {result.get('error')}"
        assert "pass_fail" in result
        assert "confidence" in result
        assert "reasoning" in result

    def test_clear_pass(self):
        """Clear pass case is identified."""
        result = judge_test_result(
            test_output="SUCCESS: All tests passed. Coverage: 100%. No errors found.",
            expected_criteria=[
                "Indicates success",
                "Mentions test coverage",
            ],
        )

        assert "error" not in result
        assert result.get("pass_fail") is True

    def test_clear_fail(self):
        """Clear fail case is identified."""
        result = judge_test_result(
            test_output="ERROR: Test failed. Expected 5 but got 3.",
            expected_criteria=[
                "Indicates all tests passed",
                "Shows no errors",
            ],
        )

        assert "error" not in result
        assert result.get("pass_fail") is False

    def test_strictness_levels(self):
        """Different strictness levels affect judgment."""
        output = "The function mostly works but has some edge cases."

        lenient = judge_test_result(
            output, ["Function works correctly"], strictness="lenient"
        )

        strict = judge_test_result(
            output, ["Function works correctly"], strictness="strict"
        )

        # Both should complete without error
        assert "error" not in lenient
        assert "error" not in strict


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestJudgeBatch:
    """Test batch judgment functionality."""

    def test_batch_judgment(self):
        """Batch judgment processes multiple cases."""
        test_cases = [
            {
                "output": "Test passed successfully.",
                "criteria": ["Indicates success"],
            },
            {
                "output": "Error: assertion failed.",
                "criteria": ["Indicates success"],
            },
        ]

        result = judge_batch(test_cases)

        assert "error" not in result
        assert "results" in result
        assert "summary" in result
        assert len(result["results"]) == 2
        assert result["summary"]["total"] == 2


class TestQualityGate:
    """Test quality gate creation."""

    def test_create_quality_gate(self):
        """Quality gate is created with defaults."""
        gate = create_quality_gate(["criterion 1", "criterion 2"])

        assert "criteria" in gate
        assert gate["min_pass_rate"] == 0.9
        assert gate["min_confidence"] == 0.8

    def test_custom_thresholds(self):
        """Quality gate accepts custom thresholds."""
        gate = create_quality_gate(
            ["criterion"],
            min_pass_rate=0.95,
            min_confidence=0.9,
        )

        assert gate["min_pass_rate"] == 0.95
        assert gate["min_confidence"] == 0.9
