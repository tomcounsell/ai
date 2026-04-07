"""
Tests for the AI Judge Framework
"""

from .judge import (
    AIJudgeTestRunner,
    JudgeConfig,
    JudgmentResult,
    JudgmentScore,
    _heuristic_judgment,
    _parse_judgment_response,
    judge_response_quality,
    judge_test_result,
    judge_tool_selection,
)


class TestJudgmentScore:
    """Tests for JudgmentScore enum."""

    def test_score_values(self):
        """Test all score values are defined."""
        assert JudgmentScore.EXCELLENT.value == "excellent"
        assert JudgmentScore.GOOD.value == "good"
        assert JudgmentScore.ACCEPTABLE.value == "acceptable"
        assert JudgmentScore.POOR.value == "poor"
        assert JudgmentScore.FAIL.value == "fail"

    def test_score_from_string(self):
        """Test creating score from string."""
        score = JudgmentScore("good")
        assert score == JudgmentScore.GOOD


class TestJudgeConfig:
    """Tests for JudgeConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = JudgeConfig()
        assert config.model == "gemma2:3b"
        assert config.temperature == 0.1
        assert config.strict_mode is True
        assert config.fallback_to_heuristics is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = JudgeConfig(
            model="llama3:8b",
            temperature=0.3,
            strict_mode=False,
            custom_criteria=["custom1", "custom2"],
        )
        assert config.model == "llama3:8b"
        assert config.temperature == 0.3
        assert config.strict_mode is False
        assert len(config.custom_criteria) == 2


class TestJudgmentResult:
    """Tests for JudgmentResult."""

    def test_result_creation(self):
        """Test creating a judgment result."""
        result = JudgmentResult(
            test_id="test_001",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={"criterion1": "good"},
            pass_fail=True,
            confidence=0.85,
            reasoning="Test passed successfully",
        )
        assert result.test_id == "test_001"
        assert result.pass_fail is True
        assert result.confidence == 0.85

    def test_result_to_dict(self):
        """Test converting result to dictionary."""
        result = JudgmentResult(
            test_id="test_002",
            overall_score=JudgmentScore.EXCELLENT,
            criteria_scores={"accuracy": "excellent"},
            pass_fail=True,
            confidence=0.95,
            reasoning="Excellent performance",
        )
        d = result.to_dict()
        assert d["test_id"] == "test_002"
        assert d["overall_score"] == "excellent"
        assert d["pass_fail"] is True
        assert "timestamp" in d


class TestHeuristicJudgment:
    """Tests for heuristic judgment fallback."""

    def test_heuristic_pass(self):
        """Test heuristic judgment for passing output."""
        result = _heuristic_judgment(
            test_output="The function returns accurate results with proper formatting",
            expected_criteria=["Returns accurate results", "Uses proper formatting"],
            test_id="heuristic_test_1",
        )
        assert result.pass_fail is True
        assert result.model_used == "heuristic"
        assert result.confidence == 0.5

    def test_heuristic_fail(self):
        """Test heuristic judgment for failing output."""
        result = _heuristic_judgment(
            test_output="Error occurred during processing",
            expected_criteria=[
                "Returns successful response",
                "Contains user data",
                "Properly formatted JSON",
            ],
            test_id="heuristic_test_2",
        )
        # May pass or fail depending on keyword matching
        assert result.model_used == "heuristic"
        assert 0.0 <= result.confidence <= 1.0


class TestParseJudgmentResponse:
    """Tests for response parsing."""

    def test_parse_json_response(self):
        """Test parsing valid JSON response."""
        response = """{
            "overall_score": "good",
            "criteria_scores": {"accuracy": "excellent"},
            "pass_fail": true,
            "confidence": 0.88,
            "reasoning": "Test performed well"
        }"""
        result = _parse_judgment_response(response, "parse_test_1")
        assert result.overall_score == JudgmentScore.GOOD
        assert result.pass_fail is True
        assert result.confidence == 0.88

    def test_parse_natural_language(self):
        """Test parsing natural language response."""
        response = "The test output is good and meets all criteria. PASS"
        result = _parse_judgment_response(response, "parse_test_2")
        assert result.pass_fail is True
        assert result.overall_score == JudgmentScore.GOOD

    def test_parse_failure_response(self):
        """Test parsing failure response."""
        response = "The output is poor and fails to meet basic requirements"
        result = _parse_judgment_response(response, "parse_test_3")
        assert result.pass_fail is False
        assert result.overall_score == JudgmentScore.POOR


class TestJudgeTestResult:
    """Tests for main judge_test_result function."""

    def test_basic_judgment(self):
        """Test basic judgment execution."""
        result = judge_test_result(
            test_output="The function correctly calculates the sum of two numbers",
            expected_criteria=[
                "Performs calculation correctly",
                "Handles numeric input",
            ],
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)
        assert result.test_id is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_judgment_with_context(self):
        """Test judgment with additional context."""
        result = judge_test_result(
            test_output="User authentication successful",
            expected_criteria=["Authenticates user", "Returns success status"],
            test_context={"test_type": "authentication", "user_type": "admin"},
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)

    def test_judgment_with_custom_id(self):
        """Test judgment with custom test ID."""
        result = judge_test_result(
            test_output="Test output",
            expected_criteria=["Basic criterion"],
            test_id="custom_test_123",
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert result.test_id == "custom_test_123"


class TestJudgeResponseQuality:
    """Tests for response quality judgment."""

    def test_response_quality_judgment(self):
        """Test response quality evaluation."""
        result = judge_response_quality(
            response="Here's how to implement a binary search: First, find the middle element...",
            prompt="How do I implement binary search?",
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)
        assert "response_quality" in result.test_id

    def test_response_quality_with_custom_criteria(self):
        """Test response quality with custom criteria."""
        result = judge_response_quality(
            response="The weather in Tokyo is currently sunny with 25°C",
            prompt="What's the weather in Tokyo?",
            evaluation_criteria=[
                "Mentions Tokyo",
                "Provides temperature",
                "Describes weather conditions",
            ],
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)


class TestJudgeToolSelection:
    """Tests for tool selection judgment."""

    def test_tool_selection_judgment(self):
        """Test tool selection evaluation."""
        result = judge_tool_selection(
            selected_tools=["search", "summarize"],
            user_intent="Find and summarize the latest Python news",
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)
        assert "tool_selection" in result.test_id

    def test_tool_selection_with_context(self):
        """Test tool selection with context."""
        result = judge_tool_selection(
            selected_tools=["image_analysis"],
            user_intent="What's in this image?",
            context={"has_image": True, "image_type": "photo"},
            config=JudgeConfig(fallback_to_heuristics=True),
        )
        assert isinstance(result, JudgmentResult)


class TestAIJudgeTestRunner:
    """Tests for the test runner."""

    def test_runner_creation(self):
        """Test creating a test runner."""
        runner = AIJudgeTestRunner()
        assert runner.config is not None
        assert len(runner.results) == 0

    def test_runner_with_custom_config(self):
        """Test runner with custom config."""
        config = JudgeConfig(model="llama3:8b")
        runner = AIJudgeTestRunner(config=config)
        assert runner.config.model == "llama3:8b"

    def test_run_test(self):
        """Test running a single test."""
        runner = AIJudgeTestRunner(config=JudgeConfig(fallback_to_heuristics=True))
        result = runner.run_test(
            test_name="runner_test_1",
            test_output="Function executed successfully",
            criteria=["Executes without error"],
        )
        assert result.test_id == "runner_test_1"
        assert len(runner.results) == 1

    def test_get_summary(self):
        """Test getting results summary."""
        runner = AIJudgeTestRunner(config=JudgeConfig(fallback_to_heuristics=True))

        # Run multiple tests
        runner.run_test("test_1", "Good output meeting criteria", ["Basic criterion"])
        runner.run_test("test_2", "Another good output", ["Another criterion"])

        summary = runner.get_summary()
        assert summary["total"] == 2
        assert "pass_rate" in summary
        assert "average_confidence" in summary

    def test_empty_summary(self):
        """Test summary with no results."""
        runner = AIJudgeTestRunner()
        summary = runner.get_summary()
        assert summary["total"] == 0
        assert summary["pass_rate"] == 0.0

    def test_save_results(self, tmp_path):
        """Test saving results to file."""
        runner = AIJudgeTestRunner(config=JudgeConfig(fallback_to_heuristics=True))
        runner.run_test("save_test", "Test output", ["Criterion"])

        filepath = tmp_path / "results.json"
        runner.save_results(filepath)

        assert filepath.exists()
        import json

        with open(filepath) as f:
            data = json.load(f)
        assert data["total"] == 1
