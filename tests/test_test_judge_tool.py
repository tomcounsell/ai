"""Tests for test_judge_tool.py - Test judging functionality with local models."""

import pytest
import json
import tempfile
import os
from unittest.mock import patch, MagicMock
from tools.test_judge_tool import (
    JudgmentScore,
    JudgmentResult,
    JudgeConfig,
    judge_test_result,
    judge_code_quality,
    judge_response_quality,
    batch_judge_tests,
    judge_ui_feedback,
    judge_code_review,
    _build_judge_prompt,
    _parse_judgment_result,
    _fallback_judgment_parsing
)


class JudgmentResultScore:
    """Test JudgmentScore enum."""
    
    def test_judgment_score_values(self):
        """Test all judgment score values are valid."""
        assert JudgmentScore.EXCELLENT == "excellent"
        assert JudgmentScore.GOOD == "good"
        assert JudgmentScore.SATISFACTORY == "satisfactory"
        assert JudgmentScore.POOR == "poor"
        assert JudgmentScore.FAIL == "fail"
    
    def test_judgment_score_ordering(self):
        """Test judgment scores can be compared."""
        scores = [JudgmentScore.EXCELLENT, JudgmentScore.GOOD, JudgmentScore.SATISFACTORY, 
                 JudgmentScore.POOR, JudgmentScore.FAIL]
        
        # All scores should be valid strings
        assert all(isinstance(score.value, str) for score in scores)


class TestJudgmentResult:
    """Test JudgmentResult model validation."""
    
    def test_valid_judgment_creation(self):
        """Test creating valid test judgment."""
        judgment = JudgmentResult(
            test_id="test_001",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={"clarity": JudgmentScore.EXCELLENT, "accuracy": JudgmentScore.GOOD},
            detailed_feedback="Good overall performance with clear explanations.",
            pass_fail=True,
            confidence=0.85,
            reasoning="Meets all criteria with minor improvements possible."
        )
        
        assert judgment.test_id == "test_001"
        assert judgment.overall_score == JudgmentScore.GOOD
        assert judgment.criteria_scores["clarity"] == JudgmentScore.EXCELLENT
        assert judgment.pass_fail == True
        assert judgment.confidence == 0.85
    
    def test_confidence_bounds_validation(self):
        """Test confidence score validation."""
        # Valid confidence scores
        valid_judgment = JudgmentResult(
            test_id="test_001",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={},
            detailed_feedback="test",
            pass_fail=True,
            confidence=0.5,
            reasoning="test"
        )
        assert valid_judgment.confidence == 0.5
        
        # Test boundary values
        boundary_low = JudgmentResult(
            test_id="test_002",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={},
            detailed_feedback="test",
            pass_fail=True,
            confidence=0.0,
            reasoning="test"
        )
        assert boundary_low.confidence == 0.0
        
        boundary_high = JudgmentResult(
            test_id="test_003",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={},
            detailed_feedback="test",
            pass_fail=True,
            confidence=1.0,
            reasoning="test"
        )
        assert boundary_high.confidence == 1.0


class TestJudgeConfig:
    """Test JudgeConfig model validation."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = JudgeConfig()
        
        assert config.model == "gemma2:3b"
        assert config.temperature == 0.1
        assert config.strict_mode == True
        assert config.custom_criteria is None
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = JudgeConfig(
            model="llama2:7b",
            temperature=0.3,
            strict_mode=False,
            custom_criteria=["creativity", "originality"]
        )
        
        assert config.model == "llama2:7b"
        assert config.temperature == 0.3
        assert config.strict_mode == False
        assert config.custom_criteria == ["creativity", "originality"]


class TestBuildJudgePrompt:
    """Test judge prompt building functionality."""
    
    def test_basic_prompt_construction(self):
        """Test basic prompt construction."""
        prompt = _build_judge_prompt(
            test_output="This is a test response.",
            expected_criteria=["clarity", "accuracy"],
            test_context={"test_id": "test_001", "test_type": "response_evaluation"},
            config=JudgeConfig()
        )
        
        assert "test_001" in prompt
        assert "response_evaluation" in prompt
        assert "clarity" in prompt
        assert "accuracy" in prompt
        assert "This is a test response." in prompt
        assert "JSON format" in prompt
    
    def test_strict_mode_prompt(self):
        """Test strict mode affects prompt."""
        config_strict = JudgeConfig(strict_mode=True)
        config_normal = JudgeConfig(strict_mode=False)
        
        prompt_strict = _build_judge_prompt(
            test_output="test",
            expected_criteria=["test"],
            test_context={"test_id": "test_001"},
            config=config_strict
        )
        
        prompt_normal = _build_judge_prompt(
            test_output="test",
            expected_criteria=["test"],
            test_context={"test_id": "test_001"},
            config=config_normal
        )
        
        assert "STRICT MODE" in prompt_strict
        assert "STRICT MODE" not in prompt_normal
        assert "rigorous standards" in prompt_strict
    
    def test_custom_criteria_inclusion(self):
        """Test custom criteria are included in prompt."""
        config = JudgeConfig(custom_criteria=["creativity", "innovation"])
        
        prompt = _build_judge_prompt(
            test_output="test",
            expected_criteria=["accuracy"],
            test_context={"test_id": "test_001"},
            config=config
        )
        
        assert "creativity" in prompt
        assert "innovation" in prompt
        assert "Additional Criteria" in prompt


class TestParseJudgmentResult:
    """Test judgment result parsing functionality."""
    
    def test_valid_json_parsing(self):
        """Test parsing valid JSON response."""
        json_response = '''
        {
            "overall_score": "good",
            "pass_fail": true,
            "confidence": 0.8,
            "reasoning": "Meets most criteria well",
            "criteria_scores": {
                "clarity": "excellent",
                "accuracy": "good"
            },
            "detailed_feedback": "Strong performance with minor areas for improvement"
        }
        '''
        
        judgment = _parse_judgment_result(json_response, "test_001")
        
        assert judgment.test_id == "test_001"
        assert judgment.overall_score == JudgmentScore.GOOD
        assert judgment.pass_fail == True
        assert judgment.confidence == 0.8
        assert judgment.criteria_scores["clarity"] == JudgmentScore.EXCELLENT
        assert "Strong performance" in judgment.detailed_feedback
    
    def test_malformed_json_fallback(self):
        """Test fallback parsing for malformed JSON."""
        malformed_response = "This is an excellent response that meets all criteria very well."
        
        judgment = _parse_judgment_result(malformed_response, "test_001")
        
        assert judgment.test_id == "test_001"
        assert judgment.overall_score == JudgmentScore.EXCELLENT
        assert judgment.confidence < 0.5  # Low confidence for fallback
        assert "fallback method" in judgment.reasoning
    
    def test_embedded_json_extraction(self):
        """Test extraction of JSON from mixed content."""
        mixed_response = '''
        Here's my analysis:
        
        {
            "overall_score": "satisfactory",
            "pass_fail": true,
            "confidence": 0.6,
            "reasoning": "Adequate performance",
            "criteria_scores": {},
            "detailed_feedback": "Meets basic requirements"
        }
        
        Additional comments outside JSON.
        '''
        
        judgment = _parse_judgment_result(mixed_response, "test_001")
        
        assert judgment.overall_score == JudgmentScore.SATISFACTORY
        assert judgment.pass_fail == True
        assert judgment.confidence == 0.6


class TestFallbackJudgmentParsing:
    """Test fallback judgment parsing for non-JSON responses."""
    
    def test_excellent_keywords(self):
        """Test detection of excellent performance keywords."""
        response = "This is an outstanding and excellent piece of work that is perfect."
        judgment = _fallback_judgment_parsing(response, "test_001")
        
        assert judgment.overall_score == JudgmentScore.EXCELLENT
        assert judgment.pass_fail == True
    
    def test_poor_keywords(self):
        """Test detection of poor performance keywords."""
        response = "This work is poor and weak with insufficient quality."
        judgment = _fallback_judgment_parsing(response, "test_001")
        
        assert judgment.overall_score == JudgmentScore.POOR
        assert judgment.pass_fail == False
    
    def test_good_keywords(self):
        """Test detection of good performance keywords."""
        response = "This is good work that is well executed and solid."
        judgment = _fallback_judgment_parsing(response, "test_001")
        
        assert judgment.overall_score == JudgmentScore.GOOD
        assert judgment.pass_fail == True
    
    def test_neutral_fallback(self):
        """Test fallback for neutral or unclear responses."""
        response = "This is some text without clear quality indicators."
        judgment = _fallback_judgment_parsing(response, "test_001")
        
        assert judgment.overall_score == JudgmentScore.FAIL
        assert judgment.pass_fail == False


class TestJudgeTestResult:
    """Test main judge_test_result functionality with mocked subprocess."""
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_successful_ollama_execution(self, mock_run):
        """Test successful Ollama model execution."""
        # Mock successful Ollama response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "overall_score": "good",
                "pass_fail": True,
                "confidence": 0.8,
                "reasoning": "Test passed",
                "criteria_scores": {},
                "detailed_feedback": "Good performance"
            })
        )
        
        judgment = judge_test_result(
            test_output="Test output",
            expected_criteria=["accuracy"],
            test_context={"test_id": "test_001"}
        )
        
        assert judgment.overall_score == JudgmentScore.GOOD
        assert judgment.pass_fail == True
        assert mock_run.called
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_ollama_execution_failure(self, mock_run):
        """Test Ollama execution failure handling."""
        # Mock failed Ollama execution
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Ollama error"
        )
        
        judgment = judge_test_result(
            test_output="Test output",
            expected_criteria=["accuracy"],
            test_context={"test_id": "test_001"}
        )
        
        assert judgment.overall_score == JudgmentScore.FAIL
        assert judgment.pass_fail == False
        assert "Ollama execution failed" in judgment.detailed_feedback
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_ollama_timeout_handling(self, mock_run):
        """Test Ollama timeout handling."""
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired("ollama", 60)
        
        judgment = judge_test_result(
            test_output="Test output",
            expected_criteria=["accuracy"],
            test_context={"test_id": "test_001"}
        )
        
        assert judgment.overall_score == JudgmentScore.FAIL
        assert "Timeout" in judgment.detailed_feedback
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_ollama_not_found(self, mock_run):
        """Test handling when Ollama is not installed."""
        mock_run.side_effect = FileNotFoundError("Ollama not found")
        
        judgment = judge_test_result(
            test_output="Test output",
            expected_criteria=["accuracy"],
            test_context={"test_id": "test_001"}
        )
        
        assert judgment.overall_score == JudgmentScore.FAIL
        assert "Ollama not found" in judgment.detailed_feedback


class TestSpecificJudgingFunctions:
    """Test specific judging functions for different contexts."""
    
    @patch('tools.test_judge_tool.judge_test_result')
    def test_judge_code_quality(self, mock_judge):
        """Test code quality judging."""
        mock_judge.return_value = JudgmentResult(
            test_id="code_quality_123",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={},
            detailed_feedback="Good code quality",
            pass_fail=True,
            confidence=0.8,
            reasoning="Meets standards"
        )
        
        result = judge_code_quality(
            code="def hello(): print('Hello')",
            language="python",
            quality_criteria=["readability", "correctness"]
        )
        
        assert result.test_id == "code_quality_123"
        assert mock_judge.called
        
        # Check that proper context was passed
        call_args = mock_judge.call_args
        test_context = call_args[0][2]  # Third positional argument
        assert test_context["test_type"] == "code_quality"
        assert test_context["language"] == "python"
    
    @patch('tools.test_judge_tool.judge_test_result')
    def test_judge_response_quality(self, mock_judge):
        """Test response quality judging."""
        mock_judge.return_value = JudgmentResult(
            test_id="response_quality_456",
            overall_score=JudgmentScore.SATISFACTORY,
            criteria_scores={},
            detailed_feedback="Adequate response",
            pass_fail=True,
            confidence=0.7,
            reasoning="Meets basic requirements"
        )
        
        result = judge_response_quality(
            response="This is a test response",
            prompt="What is 2+2?",
            evaluation_criteria=["accuracy", "clarity"]
        )
        
        assert result.test_id == "response_quality_456"
        assert mock_judge.called
        
        # Check context includes original prompt
        call_args = mock_judge.call_args
        test_context = call_args[0][2]  # Third positional argument
        assert test_context["original_prompt"] == "What is 2+2?"
    
    @patch('tools.test_judge_tool.judge_test_result')
    def test_judge_ui_feedback(self, mock_judge):
        """Test UI feedback judging."""
        mock_judgment = JudgmentResult(
            test_id="ui_feedback_789",
            overall_score=JudgmentScore.EXCELLENT,
            criteria_scores={},
            detailed_feedback="Excellent UI feedback",
            pass_fail=True,
            confidence=0.9,
            reasoning="Comprehensive and actionable"
        )
        mock_judge.return_value = mock_judgment
        
        result = judge_ui_feedback(
            feedback="Improve button contrast for better accessibility",
            ui_context={"screen_type": "mobile", "user_type": "accessibility"}
        )
        
        assert result.test_id == "ui_feedback_789"
        assert mock_judge.called
        
        # Check UI-specific criteria
        call_args = mock_judge.call_args
        criteria = call_args[0][1]  # expected_criteria argument
        criteria_text = " ".join(criteria)  # Join all criteria for substring search
        assert "actionable suggestions" in criteria_text
        assert "user experience" in criteria_text
    
    @patch('tools.test_judge_tool.judge_test_result')
    def test_judge_code_review(self, mock_judge):
        """Test code review judging."""
        mock_judgment = JudgmentResult(
            test_id="code_review_101",
            overall_score=JudgmentScore.GOOD,
            criteria_scores={},
            detailed_feedback="Good code review",
            pass_fail=True,
            confidence=0.8,
            reasoning="Identifies key issues"
        )
        mock_judge.return_value = mock_judgment
        
        result = judge_code_review(
            review="This function should handle edge cases better",
            code_context={"language": "python", "complexity": "medium"}
        )
        
        assert result.test_id == "code_review_101"
        
        # Check code review specific criteria
        call_args = mock_judge.call_args
        criteria = call_args[0][1]  # expected_criteria argument
        criteria_text = " ".join(criteria)  # Join all criteria for substring search
        assert "identifies actual issues" in criteria_text
        assert "constructive improvement" in criteria_text


class TestBatchJudging:
    """Test batch judging functionality."""
    
    @patch('tools.test_judge_tool.judge_test_result')
    def test_batch_judge_tests(self, mock_judge):
        """Test batch judging of multiple tests."""
        # Mock different judgments for each test
        mock_judgments = [
            JudgmentResult(
                test_id="test_1",
                overall_score=JudgmentScore.GOOD,
                criteria_scores={},
                detailed_feedback="Good",
                pass_fail=True,
                confidence=0.8,
                reasoning="Passes"
            ),
            JudgmentResult(
                test_id="test_2",
                overall_score=JudgmentScore.POOR,
                criteria_scores={},
                detailed_feedback="Poor",
                pass_fail=False,
                confidence=0.6,
                reasoning="Fails"
            )
        ]
        
        mock_judge.side_effect = mock_judgments
        
        test_cases = [
            {
                "output": "Good response",
                "criteria": ["accuracy"],
                "context": {"test_id": "test_1"}
            },
            {
                "output": "Poor response",
                "criteria": ["accuracy"],
                "context": {"test_id": "test_2"}
            }
        ]
        
        results = batch_judge_tests(test_cases)
        
        assert len(results) == 2
        assert results[0].overall_score == JudgmentScore.GOOD
        assert results[1].overall_score == JudgmentScore.POOR
        assert mock_judge.call_count == 2
    
    def test_batch_judge_empty_list(self):
        """Test batch judging with empty test list."""
        results = batch_judge_tests([])
        assert len(results) == 0


class TestConfigurationVariations:
    """Test different configuration variations."""
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_different_models(self, mock_run):
        """Test using different local models."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "overall_score": "good",
                "pass_fail": True,
                "confidence": 0.8,
                "reasoning": "Test",
                "criteria_scores": {},
                "detailed_feedback": "Test"
            })
        )
        
        config = JudgeConfig(model="llama2:7b")
        
        judge_test_result(
            test_output="test",
            expected_criteria=["test"],
            test_context={"test_id": "test_001"},
            config=config
        )
        
        # Check that the correct model was used
        call_args = mock_run.call_args[0][0]  # First argument (command list)
        assert "llama2:7b" in call_args
    
    @patch('tools.test_judge_tool.subprocess.run')
    def test_different_temperature(self, mock_run):
        """Test using different temperature settings."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "overall_score": "good",
                "pass_fail": True,
                "confidence": 0.8,
                "reasoning": "Test",
                "criteria_scores": {},
                "detailed_feedback": "Test"
            })
        )
        
        config = JudgeConfig(temperature=0.5)
        
        judge_test_result(
            test_output="test",
            expected_criteria=["test"],
            test_context={"test_id": "test_001"},
            config=config
        )
        
        # Check that temperature was set correctly
        call_args = mock_run.call_args[0][0]
        assert "--temperature" in call_args
        temp_index = call_args.index("--temperature")
        assert call_args[temp_index + 1] == "0.5"


class TestIntegration:
    """Integration tests for the complete judging workflow."""
    
    def test_end_to_end_workflow_mock(self):
        """Test complete end-to-end workflow with mocked components."""
        with patch('tools.test_judge_tool.subprocess.run') as mock_run:
            # Mock successful Ollama execution
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "overall_score": "excellent",
                    "pass_fail": True,
                    "confidence": 0.95,
                    "reasoning": "Exceeds all criteria",
                    "criteria_scores": {
                        "accuracy": "excellent",
                        "clarity": "good",
                        "completeness": "excellent"
                    },
                    "detailed_feedback": "Outstanding response that demonstrates clear understanding and provides comprehensive, accurate information with excellent clarity."
                })
            )
            
            # Test complete workflow
            judgment = judge_test_result(
                test_output="The capital of France is Paris. It is located in the northern part of the country and serves as the political, economic, and cultural center.",
                expected_criteria=["accuracy", "clarity", "completeness"],
                test_context={
                    "test_id": "geography_001",
                    "test_type": "factual_knowledge",
                    "subject": "geography"
                },
                config=JudgeConfig(
                    model="gemma2:3b",
                    strict_mode=True,
                    custom_criteria=["geographic_precision"]
                )
            )
            
            # Validate complete judgment
            assert judgment.test_id == "geography_001"
            assert judgment.overall_score == JudgmentScore.EXCELLENT
            assert judgment.pass_fail == True
            assert judgment.confidence == 0.95
            assert len(judgment.criteria_scores) == 3
            assert judgment.criteria_scores["accuracy"] == JudgmentScore.EXCELLENT
            assert "Outstanding response" in judgment.detailed_feedback
            
            # Verify Ollama was called correctly
            assert mock_run.called
            call_args = mock_run.call_args
            assert "gemma2:3b" in call_args[0][0]
            assert "--temperature" in call_args[0][0]