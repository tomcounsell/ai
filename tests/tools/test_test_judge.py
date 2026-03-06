"""Tests for the test judge tool."""

import os

from tools.test_judge import create_quality_gate, judge_batch, judge_test_result


class TestJudgeValidation:
    """Test input validation."""

    def test_empty_test_output_returns_error(self):
        """Test that empty test output returns error."""
        result = judge_test_result(test_output="", expected_criteria=["test passes"])
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_empty_criteria_returns_error(self):
        """Test that empty criteria returns error."""
        result = judge_test_result(test_output="test output", expected_criteria=[])
        assert "error" in result
        assert "criteria" in result["error"].lower()

    def test_missing_api_key_returns_error(self):
        """Test that missing API keys return error."""
        original_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
        original_openrouter = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            result = judge_test_result(test_output="test", expected_criteria=["passes"])
            assert "error" in result
            assert "API_KEY" in result["error"]
        finally:
            if original_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = original_anthropic
            if original_openrouter:
                os.environ["OPENROUTER_API_KEY"] = original_openrouter


class TestJudgeExecution:
    """Test actual judgment execution with real API."""

    def test_judge_passing_output(self, anthropic_api_key):
        """Test judging output that clearly passes."""
        result = judge_test_result(
            test_output="5 tests passed, 0 failed, 100% success rate",
            expected_criteria=[
                "All tests pass",
                "No failures reported",
            ],
        )
        assert "error" not in result
        assert result.get("pass_fail") is True
        assert result.get("confidence", 0) > 0.5

    def test_judge_failing_output(self, anthropic_api_key):
        """Test judging output that clearly fails."""
        result = judge_test_result(
            test_output="ERROR: Connection refused. Unable to reach database.",
            expected_criteria=[
                "Successfully connects to database",
                "Returns valid data",
            ],
        )
        assert "error" not in result
        assert result.get("pass_fail") is False

    def test_judge_with_context(self, anthropic_api_key):
        """Test judging with additional context."""
        result = judge_test_result(
            test_output="Response time: 150ms",
            expected_criteria=[
                "Response time is under 200ms",
            ],
            context="This is a performance test for the API endpoint",
        )
        assert "error" not in result
        assert result.get("pass_fail") is True

    def test_judge_criteria_results(self, anthropic_api_key):
        """Test that criteria results are returned."""
        result = judge_test_result(
            test_output="User created successfully. ID: 12345",
            expected_criteria=[
                "User is created",
                "ID is returned",
            ],
        )
        assert "error" not in result
        assert "criteria_results" in result
        assert len(result["criteria_results"]) == 2


class TestJudgeStrictness:
    """Test strictness levels."""

    def test_lenient_strictness(self, anthropic_api_key):
        """Test lenient strictness level."""
        result = judge_test_result(
            test_output="Result: approximately 100 (actual: 99)",
            expected_criteria=["Result is 100"],
            strictness="lenient",
        )
        assert "error" not in result
        # Lenient should pass approximate matches
        assert result.get("pass_fail") is True

    def test_strict_strictness(self, anthropic_api_key):
        """Test strict strictness level."""
        result = judge_test_result(
            test_output="Result: approximately 100 (actual: 99)",
            expected_criteria=["Result is exactly 100"],
            strictness="strict",
        )
        assert "error" not in result
        # Strict should fail approximate matches
        assert result.get("pass_fail") is False


class TestJudgeBatch:
    """Test batch judgment."""

    def test_judge_batch_multiple_cases(self, anthropic_api_key):
        """Test judging multiple test cases."""
        test_cases = [
            {
                "output": "Test passed: addition works correctly",
                "criteria": ["Test passes", "Addition is mentioned"],
            },
            {
                "output": "Error: division by zero",
                "criteria": ["Test passes without errors"],
            },
        ]
        result = judge_batch(test_cases)

        assert "results" in result
        assert len(result["results"]) == 2
        assert "summary" in result
        assert result["summary"]["total"] == 2

    def test_judge_batch_summary(self, anthropic_api_key):
        """Test batch judgment summary statistics."""
        test_cases = [
            {"output": "SUCCESS", "criteria": ["Shows success"]},
            {"output": "SUCCESS", "criteria": ["Shows success"]},
            {"output": "FAILED", "criteria": ["Shows success"]},
        ]
        result = judge_batch(test_cases)

        assert result["summary"]["total"] == 3
        assert "passed" in result["summary"]
        assert "failed" in result["summary"]
        assert "pass_rate" in result["summary"]


class TestQualityGate:
    """Test quality gate creation."""

    def test_create_quality_gate(self):
        """Test creating a quality gate."""
        gate = create_quality_gate(
            criteria=["Tests pass", "No errors"], min_pass_rate=0.9, min_confidence=0.8
        )

        assert gate["criteria"] == ["Tests pass", "No errors"]
        assert gate["min_pass_rate"] == 0.9
        assert gate["min_confidence"] == 0.8

    def test_quality_gate_defaults(self):
        """Test quality gate with default values."""
        gate = create_quality_gate(criteria=["Basic test"])

        assert gate["min_pass_rate"] == 0.9
        assert gate["min_confidence"] == 0.8


class TestJudgeOutputFormat:
    """Test judgment output format."""

    def test_judgment_has_required_fields(self, anthropic_api_key):
        """Test that judgment has all required fields."""
        result = judge_test_result(
            test_output="Test passed", expected_criteria=["Test passes"]
        )

        assert "error" not in result
        assert "pass_fail" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "criteria_results" in result

    def test_confidence_in_valid_range(self, anthropic_api_key):
        """Test that confidence is in valid range."""
        result = judge_test_result(
            test_output="Test passed successfully", expected_criteria=["Test passes"]
        )

        if "error" not in result:
            confidence = result.get("confidence", 0)
            assert 0 <= confidence <= 1

    def test_suggestions_returned(self, anthropic_api_key):
        """Test that suggestions are returned."""
        result = judge_test_result(
            test_output="Partial success: 3 of 5 tests passed",
            expected_criteria=["All tests pass"],
        )

        assert "error" not in result
        assert "suggestions" in result
