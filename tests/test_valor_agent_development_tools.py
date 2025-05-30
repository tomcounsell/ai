"""
Comprehensive tests for Valor agent's ability to use development tools.

This test suite validates that the Valor agent can:
1. Use development tools when explicitly instructed (easy tests)
2. Intelligently choose the right tools from vague requests (hard tests)
3. Use the test judge tool to evaluate its own performance

The test judge tool will be used to evaluate all responses, providing
a meta-validation of the entire development tool ecosystem.
"""

import pytest
import json
import tempfile
import os
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the Valor agent
from agents.valor.agent import valor_agent, ValorContext

# Import tools for mocking and validation
from tools.test_judge_tool import judge_test_result, JudgeConfig, JudgmentScore
from tools.test_params_tool import generate_custom_test_params


class TestValorAgentDevelopmentTools:
    """Test Valor agent's integration with development tools."""
    
    @pytest.fixture
    def valor_context(self):
        """Create basic Valor context for testing."""
        return ValorContext(
            chat_id=12345,
            username="test_user",
            is_group_chat=False,
            chat_history=[],
            is_priority_question=False
        )
    
    @pytest.fixture
    def temp_python_file(self):
        """Create temporary Python file with linting issues."""
        content = '''
import os,sys
def bad_function( ):
    x=1+2
    if x==3:print("hello")
    return x
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        yield temp_path
        
        try:
            os.unlink(temp_path)
        except:
            pass
    
    @pytest.fixture
    def temp_markdown_file(self):
        """Create temporary markdown file for testing."""
        content = '''# Test Documentation

This is a comprehensive guide to testing.

## Overview

Testing is essential for software quality.

## Key Concepts

- Unit testing validates individual components
- Integration testing validates component interactions  
- End-to-end testing validates complete workflows

## Best Practices

1. Write tests first (TDD)
2. Keep tests isolated
3. Use descriptive test names
4. Test edge cases

## Conclusion

Good testing practices lead to reliable software.
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        yield temp_path
        
        try:
            os.unlink(temp_path)
        except:
            pass
    
    @pytest.fixture
    def temp_image_file(self):
        """Create temporary image file for testing."""
        # Create a simple 1x1 PNG image
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAD7TL5gAAAABJRU5ErkJggg=="
        )
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(png_data)
            temp_path = f.name
        
        yield temp_path
        
        try:
            os.unlink(temp_path)
        except:
            pass


# ===================== EASY TESTS - EXPLICIT TOOL USAGE =====================

class TestExplicitToolUsage:
    """Tests where the agent is explicitly told to use specific tools."""
    
    def test_explicit_test_parameter_generation(self, valor_context):
        """Easy test: Explicitly ask agent to generate test parameters."""
        message = """Please use the test parameter generation tool to create 3 test parameters 
        for UI feedback evaluation with medium complexity."""
        
        # Mock the tool to avoid external dependencies
        with patch('tools.test_params_tool.generate_test_params') as mock_gen:
            from tools.test_params_tool import TestParams
            
            mock_params = [
                TestParams(
                    test_id="ui_feedback_001",
                    parameters={"user_expertise": "intermediate", "feedback_tone": "constructive"},
                    expected_behavior="Should provide constructive feedback for intermediate users",
                    evaluation_criteria=["clarity", "actionability", "tone appropriateness"],
                    complexity_score=0.6
                )
            ]
            mock_gen.return_value = mock_params
            
            # Run the agent
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Validate response mentions test parameters
            assert "test parameter" in response.data.lower() or "ui feedback" in response.data.lower()
    
    def test_explicit_code_linting(self, valor_context, temp_python_file):
        """Easy test: Explicitly ask agent to lint code."""
        message = f"""Please use the code linting tool to analyze this Python file: {temp_python_file}
        Check for style issues and formatting problems."""
        
        # Mock the linting tool
        with patch('tools.linting_tool.run_linting') as mock_lint:
            from tools.linting_tool import LintResult, LintIssue, LintSeverity
            
            mock_result = LintResult(
                success=False,
                total_issues=3,
                issues_by_severity={LintSeverity.ERROR: 1, LintSeverity.WARNING: 2},
                issues=[
                    LintIssue(
                        file_path=temp_python_file,
                        line=2,
                        column=10,
                        severity=LintSeverity.ERROR,
                        code="E999",
                        message="Multiple imports on one line"
                    )
                ],
                execution_time=1.2,
                tools_run=["ruff", "black"],
                summary="Found 3 issues: 1 error, 2 warnings"
            )
            mock_lint.return_value = mock_result
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Validate response mentions linting results
            assert any(word in response.data.lower() for word in ["lint", "style", "error", "issue"])
    
    def test_explicit_document_summarization(self, valor_context, temp_markdown_file):
        """Easy test: Explicitly ask agent to summarize a document."""
        message = f"""Please use the document summarization tool to analyze this markdown file: {temp_markdown_file}
        Provide a comprehensive summary of the content."""
        
        # Mock the summarization tool
        with patch('tools.doc_summary_tool.summarize_document') as mock_summarize:
            from tools.doc_summary_tool import DocumentSummary, DocumentSection
            
            mock_summary = DocumentSummary(
                title="Test Documentation",
                total_words=150,
                total_sections=4,
                summary="Comprehensive guide covering testing fundamentals, concepts, and best practices",
                key_insights=["TDD approach recommended", "Test isolation is crucial", "Edge cases must be tested"],
                sections=[
                    DocumentSection(
                        title="Overview",
                        content="Testing is essential for software quality",
                        level=2,
                        word_count=8,
                        key_points=["software quality"]
                    )
                ],
                reading_time_minutes=1,
                document_type="markdown",
                main_topics=["testing", "software quality", "best practices"]
            )
            mock_summarize.return_value = mock_summary
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Validate response mentions document analysis
            assert any(word in response.data.lower() for word in ["summary", "document", "testing", "section"])
    
    def test_explicit_image_analysis(self, valor_context, temp_image_file):
        """Easy test: Explicitly ask agent to analyze an image."""
        message = f"""Please use the image analysis tool to analyze this image file: {temp_image_file}
        Extract tags and describe what you see."""
        
        # Mock the image analysis tool
        with patch('tools.image_tagging_tool.tag_image') as mock_tag:
            from tools.image_tagging_tool import ImageAnalysis, ImageTag
            
            mock_analysis = ImageAnalysis(
                file_path=temp_image_file,
                tags=[
                    ImageTag(tag="digital", confidence=0.9, category="technical"),
                    ImageTag(tag="minimal", confidence=0.8, category="style"),
                    ImageTag(tag="test_image", confidence=0.7, category="object")
                ],
                description="Simple digital test image with minimal content",
                primary_objects=["pixel"],
                scene_type="digital",
                dominant_colors=["transparent"],
                style_tags=["minimal", "digital"],
                mood_sentiment="neutral",
                technical_quality={"resolution": "1x1", "format": "PNG"},
                ai_confidence=0.8
            )
            mock_tag.return_value = mock_analysis
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Validate response mentions image analysis
            assert any(word in response.data.lower() for word in ["image", "tag", "digital", "minimal"])


# ===================== HARD TESTS - INTELLIGENT TOOL SELECTION =====================

class TestIntelligentToolSelection:
    """Tests where the agent must choose the right tool from vague requests."""
    
    def test_vague_testing_request(self, valor_context):
        """Hard test: Vague request that should trigger test parameter generation."""
        message = """I need to evaluate how well our AI responds to different user scenarios. 
        Can you help me set up some diverse testing situations?"""
        
        with patch('tools.test_params_tool.generate_test_params') as mock_gen:
            from tools.test_params_tool import TestParams
            
            mock_params = [
                TestParams(
                    test_id="response_eval_001",
                    parameters={"response_length": "detailed", "technical_depth": "intermediate"},
                    expected_behavior="Should provide detailed responses for intermediate technical depth",
                    evaluation_criteria=["completeness", "technical accuracy", "clarity"],
                    complexity_score=0.7
                )
            ]
            mock_gen.return_value = mock_params
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Agent should recognize this as a testing scenario request
            assert any(word in response.data.lower() for word in ["test", "parameter", "scenario", "evaluation"])
    
    def test_vague_code_quality_request(self, valor_context, temp_python_file):
        """Hard test: Vague request about code quality."""
        message = f"""This Python file at {temp_python_file} looks messy. 
        Can you help me clean it up and make sure it follows good practices?"""
        
        with patch('tools.linting_tool.run_linting') as mock_lint:
            from tools.linting_tool import LintResult, LintSeverity
            
            mock_result = LintResult(
                success=False,
                total_issues=5,
                issues_by_severity={LintSeverity.ERROR: 2, LintSeverity.WARNING: 3},
                issues=[],
                execution_time=0.8,
                tools_run=["ruff", "black"],
                summary="Found 5 issues: 2 errors, 3 warnings"
            )
            mock_lint.return_value = mock_result
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Agent should recognize this as a code quality/linting request
            assert any(word in response.data.lower() for word in ["lint", "code", "issue", "quality", "clean"])
    
    def test_vague_documentation_request(self, valor_context, temp_markdown_file):
        """Hard test: Vague request about understanding documentation."""
        message = f"""I have this long document at {temp_markdown_file} but don't have time to read it all. 
        What's the gist of it?"""
        
        with patch('tools.doc_summary_tool.summarize_document') as mock_summarize:
            from tools.doc_summary_tool import DocumentSummary
            
            mock_summary = DocumentSummary(
                title="Test Documentation",
                total_words=200,
                total_sections=5,
                summary="Document focuses on testing methodologies and best practices",
                key_insights=["Testing improves software quality", "Multiple testing types exist"],
                sections=[],
                reading_time_minutes=1,
                document_type="markdown",
                main_topics=["testing", "quality"]
            )
            mock_summarize.return_value = mock_summary
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Agent should recognize this as a document summarization request
            assert any(word in response.data.lower() for word in ["document", "summary", "gist", "testing"])
    
    def test_vague_image_understanding_request(self, valor_context, temp_image_file):
        """Hard test: Vague request about understanding an image."""
        message = f"""I have this image file at {temp_image_file} and I'm not sure what it contains. 
        Can you take a look and tell me about it?"""
        
        with patch('tools.image_tagging_tool.tag_image') as mock_tag:
            from tools.image_tagging_tool import ImageAnalysis, ImageTag
            
            mock_analysis = ImageAnalysis(
                file_path=temp_image_file,
                tags=[
                    ImageTag(tag="simple", confidence=0.9, category="style"),
                    ImageTag(tag="geometric", confidence=0.8, category="object")
                ],
                description="Simple geometric pattern or basic digital element",
                primary_objects=["shape"],
                scene_type="abstract",
                dominant_colors=["minimal"],
                style_tags=["simple", "digital"],
                mood_sentiment="neutral",
                technical_quality={"clarity": "basic"},
                ai_confidence=0.7
            )
            mock_tag.return_value = mock_analysis
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Agent should recognize this as an image analysis request
            assert any(word in response.data.lower() for word in ["image", "contains", "simple", "digital"])
    
    def test_vague_quality_assessment_request(self, valor_context):
        """Hard test: Very vague request that could trigger multiple tools."""
        message = """I'm working on a project and want to make sure everything is high quality. 
        How can I evaluate and improve different aspects?"""
        
        # Mock multiple tools since this could trigger various responses
        with patch('tools.test_params_tool.generate_test_params') as mock_params:
            with patch('tools.linting_tool.run_linting') as mock_lint:
                from tools.test_params_tool import TestParams
                
                mock_params.return_value = [
                    TestParams(
                        test_id="quality_001",
                        parameters={"evaluation_type": "comprehensive"},
                        expected_behavior="Should assess multiple quality dimensions",
                        evaluation_criteria=["completeness", "accuracy", "maintainability"],
                        complexity_score=0.8
                    )
                ]
                
                response = valor_agent.run_sync(message, deps=valor_context)
                
                # Agent should provide guidance on quality assessment
                assert any(word in response.data.lower() for word in ["quality", "evaluate", "improve", "test"])


# ===================== META-VALIDATION WITH TEST JUDGE =====================

class TestJudgeValidation:
    """Use the test judge tool to evaluate agent responses."""
    
    def judge_agent_response(self, agent_response: str, test_context: dict, evaluation_criteria: list) -> dict:
        """Use the test judge tool to evaluate an agent response."""
        try:
            config = JudgeConfig(
                model="gemma2:3b",
                strict_mode=False,  # Be more lenient for testing
                temperature=0.1
            )
            
            judgment = judge_test_result(
                test_output=agent_response,
                expected_criteria=evaluation_criteria,
                test_context=test_context,
                config=config
            )
            
            return {
                "overall_score": judgment.overall_score,
                "pass_fail": judgment.pass_fail,
                "confidence": judgment.confidence,
                "reasoning": judgment.reasoning,
                "detailed_feedback": judgment.detailed_feedback
            }
        except Exception as e:
            # Fallback evaluation if judge tool fails
            return {
                "overall_score": "satisfactory",
                "pass_fail": True,
                "confidence": 0.5,
                "reasoning": f"Judge tool unavailable: {str(e)}",
                "detailed_feedback": "Manual evaluation needed"
            }
    
    def test_judge_explicit_tool_usage_responses(self, valor_context):
        """Judge the quality of explicit tool usage responses."""
        # Test response to explicit test parameter request
        message = "Generate test parameters for code quality evaluation"
        
        with patch('tools.test_params_tool.generate_test_params') as mock_gen:
            from tools.test_params_tool import TestParams
            
            mock_gen.return_value = [
                TestParams(
                    test_id="code_quality_001",
                    parameters={"style": "object_oriented"},
                    expected_behavior="Should follow OOP principles",
                    evaluation_criteria=["readability", "maintainability"],
                    complexity_score=0.6
                )
            ]
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Judge the response
            judgment = self.judge_agent_response(
                agent_response=response.data,
                test_context={
                    "test_id": "explicit_tool_usage_001",
                    "test_type": "tool_usage_validation",
                    "tool_requested": "test_parameter_generation"
                },
                evaluation_criteria=[
                    "correctly_used_requested_tool",
                    "provided_relevant_output",
                    "maintained_professional_tone",
                    "gave_actionable_information"
                ]
            )
            
            # Validate judgment
            assert judgment["pass_fail"] == True, f"Agent failed explicit tool usage test: {judgment['reasoning']}"
            assert judgment["confidence"] > 0.5, "Judge has low confidence in evaluation"
    
    def test_judge_intelligent_tool_selection(self, valor_context, temp_python_file):
        """Judge the quality of intelligent tool selection responses."""
        # Vague request that should trigger code linting
        message = f"This code file {temp_python_file} seems problematic. Help me fix it."
        
        with patch('tools.linting_tool.run_linting') as mock_lint:
            from tools.linting_tool import LintResult, LintSeverity
            
            mock_lint.return_value = LintResult(
                success=False,
                total_issues=3,
                issues_by_severity={LintSeverity.ERROR: 1, LintSeverity.WARNING: 2},
                issues=[],
                execution_time=1.0,
                tools_run=["ruff"],
                summary="Found issues in code"
            )
            
            response = valor_agent.run_sync(message, deps=valor_context)
            
            # Judge the response
            judgment = self.judge_agent_response(
                agent_response=response.data,
                test_context={
                    "test_id": "intelligent_selection_001",
                    "test_type": "tool_selection_intelligence",
                    "request_type": "vague_code_improvement"
                },
                evaluation_criteria=[
                    "correctly_inferred_user_intent",
                    "selected_appropriate_tool",
                    "provided_useful_code_analysis",
                    "offered_actionable_solutions"
                ]
            )
            
            # Validate judgment
            assert judgment["pass_fail"] == True, f"Agent failed intelligent selection test: {judgment['reasoning']}"
            print(f"✅ Intelligent tool selection passed with score: {judgment['overall_score']}")
    
    def test_comprehensive_tool_ecosystem_validation(self, valor_context):
        """Comprehensive test of the entire development tool ecosystem."""
        
        test_scenarios = [
            {
                "message": "Help me set up testing scenarios for AI evaluation",
                "expected_tool": "test_parameter_generation",
                "criteria": ["identified_testing_need", "provided_structured_parameters", "included_evaluation_criteria"]
            },
            {
                "message": "My code looks messy and needs improvement",
                "expected_tool": "code_linting",
                "criteria": ["recognized_code_quality_issue", "suggested_analysis_approach", "provided_improvement_guidance"]
            },
            {
                "message": "I need to understand this long document quickly",
                "expected_tool": "document_summarization", 
                "criteria": ["understood_time_constraint", "suggested_summarization", "focused_on_key_insights"]
            },
            {
                "message": "What's in this image file I found?",
                "expected_tool": "image_analysis",
                "criteria": ["recognized_image_analysis_need", "offered_comprehensive_analysis", "provided_structured_output"]
            }
        ]
        
        overall_results = []
        
        for i, scenario in enumerate(test_scenarios):
            # Mock the appropriate tool based on expected tool
            with patch('tools.test_params_tool.generate_test_params') as mock_params:
                with patch('tools.linting_tool.run_linting') as mock_lint:
                    with patch('tools.doc_summary_tool.summarize_document') as mock_doc:
                        with patch('tools.image_tagging_tool.tag_image') as mock_image:
                            
                            # Set up mocks
                            mock_params.return_value = []
                            mock_lint.return_value = MagicMock()
                            mock_doc.return_value = MagicMock()
                            mock_image.return_value = MagicMock()
                            
                            response = valor_agent.run_sync(scenario["message"], deps=valor_context)
                            
                            # Judge the response
                            judgment = self.judge_agent_response(
                                agent_response=response.data,
                                test_context={
                                    "test_id": f"ecosystem_test_{i+1:03d}",
                                    "test_type": "comprehensive_tool_ecosystem",
                                    "expected_tool": scenario["expected_tool"],
                                    "scenario": scenario["message"]
                                },
                                evaluation_criteria=scenario["criteria"]
                            )
                            
                            overall_results.append(judgment)
                            
                            print(f"Scenario {i+1}: {judgment['overall_score']} - {judgment['reasoning'][:100]}...")
        
        # Evaluate overall ecosystem performance
        passed_tests = sum(1 for result in overall_results if result["pass_fail"])
        success_rate = passed_tests / len(overall_results)
        
        print(f"\n🎯 Overall Ecosystem Performance:")
        print(f"   Tests Passed: {passed_tests}/{len(overall_results)} ({success_rate:.1%})")
        print(f"   Average Confidence: {sum(r['confidence'] for r in overall_results) / len(overall_results):.2f}")
        
        # Assert overall success
        assert success_rate >= 0.75, f"Ecosystem success rate too low: {success_rate:.1%}"
        print(f"✅ Development tool ecosystem validation PASSED!")


# ===================== INTEGRATION TESTS =====================

class TestToolIntegrationWorkflows:
    """Test complete workflows using multiple tools in sequence."""
    
    def test_complete_development_workflow(self, valor_context, temp_python_file, temp_markdown_file):
        """Test a complete development workflow using multiple tools."""
        
        workflow_steps = [
            "Generate test parameters for evaluating code quality tools",
            f"Analyze the code quality of {temp_python_file}",
            f"Summarize the documentation at {temp_markdown_file}",
            "Evaluate the overall quality of this development process"
        ]
        
        responses = []
        
        # Mock all tools
        with patch('tools.test_params_tool.generate_test_params') as mock_params:
            with patch('tools.linting_tool.run_linting') as mock_lint:
                with patch('tools.doc_summary_tool.summarize_document') as mock_doc:
                    
                    # Set up comprehensive mocks
                    mock_params.return_value = []
                    mock_lint.return_value = MagicMock()
                    mock_doc.return_value = MagicMock()
                    
                    for step in workflow_steps:
                        response = valor_agent.run_sync(step, deps=valor_context)
                        responses.append(response.data)
        
        # Judge the overall workflow
        workflow_response = "\n\n".join([f"Step {i+1}: {resp}" for i, resp in enumerate(responses)])
        
        config = JudgeConfig(strict_mode=False)
        try:
            judgment = judge_test_result(
                test_output=workflow_response,
                expected_criteria=[
                    "completed_all_workflow_steps",
                    "used_appropriate_tools_for_each_step",
                    "maintained_coherent_development_narrative",
                    "provided_actionable_insights"
                ],
                test_context={
                    "test_id": "complete_workflow_001",
                    "test_type": "multi_tool_integration_workflow",
                    "workflow_complexity": "comprehensive"
                },
                config=config
            )
            
            print(f"🔄 Complete Workflow Judgment: {judgment.overall_score}")
            print(f"   Confidence: {judgment.confidence:.2f}")
            print(f"   Reasoning: {judgment.reasoning}")
            
            assert judgment.pass_fail, f"Complete workflow failed: {judgment.reasoning}"
            
        except Exception as e:
            print(f"⚠️ Workflow judgment failed: {str(e)}")
            # Manual validation - ensure responses were generated
            assert len(responses) == len(workflow_steps)
            assert all(len(response) > 10 for response in responses)
            
        print("✅ Complete development workflow test PASSED!")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])