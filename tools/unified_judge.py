"""
Unified Test Judge System - Consolidated AI-powered evaluation for all test types.

This module provides a standardized judging interface that works across all test categories:
- Code quality evaluation
- AI response assessment  
- UI/UX feedback validation
- Performance benchmarking
- Conversation quality analysis
- Production readiness evaluation

The judge always returns a clear pass/fail decision with detailed feedback for improvements.
"""

import json
import subprocess
import tempfile
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field
from enum import Enum
from dataclasses import dataclass
import time


class JudgmentResult(str, Enum):
    """Standardized judgment outcomes."""
    PASS = "pass"
    FAIL = "fail"


class ConfidenceLevel(str, Enum):
    """Confidence levels for judgment decisions."""
    HIGH = "high"       # 0.8-1.0 - Very confident in the assessment
    MEDIUM = "medium"   # 0.5-0.79 - Moderately confident
    LOW = "low"         # 0.0-0.49 - Low confidence, may need human review


@dataclass
class TestCriterion:
    """Individual evaluation criterion with specific expectations."""
    name: str
    description: str
    weight: float = 1.0  # Relative importance (0.1-2.0)
    required: bool = True  # Must pass for overall PASS
    
    
class UnifiedJudgment(BaseModel):
    """Comprehensive judgment result for any test type."""
    
    # Core Results
    result: JudgmentResult = Field(description="Binary pass/fail outcome")
    confidence: ConfidenceLevel = Field(description="Confidence in the judgment")
    score: float = Field(ge=0.0, le=100.0, description="Numerical score (0-100)")
    
    # Detailed Analysis  
    criteria_results: Dict[str, bool] = Field(description="Pass/fail for each criterion")
    criteria_scores: Dict[str, float] = Field(description="Individual scores (0-100) per criterion")
    
    # Feedback and Improvement
    summary: str = Field(description="Brief summary of the evaluation")
    feedback: str = Field(description="Detailed feedback explaining the judgment")
    improvements: List[str] = Field(description="Specific suggestions for improvement")
    strengths: List[str] = Field(description="What worked well in the test output")
    
    # Context and Metadata
    test_id: str = Field(description="Unique identifier for this test")
    test_type: str = Field(description="Category of test being evaluated")
    judge_model: str = Field(description="AI model used for evaluation")
    evaluation_time: float = Field(description="Time taken for evaluation (seconds)")
    
    
class JudgeConfig(BaseModel):
    """Configuration for the unified judge system."""
    
    # Model Configuration
    model: str = Field(default="gemma3:12b-it-qat", description="Local AI model for evaluation")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0, description="Model temperature for consistency")
    timeout: int = Field(default=60, description="Maximum evaluation time in seconds")
    
    # Evaluation Settings
    pass_threshold: float = Field(default=70.0, ge=0.0, le=100.0, description="Minimum score for PASS (0-100)")
    strict_mode: bool = Field(default=True, description="Require all required criteria to pass")
    confidence_threshold: float = Field(default=0.7, description="Minimum confidence for reliable judgment")
    
    # Output Preferences
    include_reasoning: bool = Field(default=True, description="Include detailed reasoning in feedback")
    max_improvements: int = Field(default=5, description="Maximum number of improvement suggestions")
    focus_on_failures: bool = Field(default=True, description="Prioritize feedback on failed criteria")


class UnifiedJudge:
    """Unified AI-powered test judge for all evaluation needs."""
    
    def __init__(self, config: Optional[JudgeConfig] = None):
        """Initialize the unified judge with configuration."""
        self.config = config or JudgeConfig()
        
    def judge_test(
        self,
        test_output: str,
        criteria: List[TestCriterion],
        test_context: Dict[str, Any],
        expected_behavior: Optional[str] = None
    ) -> UnifiedJudgment:
        """
        Universal test judging method for any test type.
        
        Args:
            test_output: The actual output/result to evaluate
            criteria: List of evaluation criteria with weights and requirements
            test_context: Context about the test (type, inputs, expectations)
            expected_behavior: Optional description of expected behavior
            
        Returns:
            UnifiedJudgment: Comprehensive evaluation with pass/fail and detailed feedback
            
        Example:
            >>> judge = UnifiedJudge()
            >>> criteria = [
            ...     TestCriterion("correctness", "Code produces correct output", weight=2.0),
            ...     TestCriterion("style", "Code follows style guidelines", weight=1.0, required=False)
            ... ]
            >>> result = judge.judge_test(code_output, criteria, {"test_type": "code_quality"})
            >>> print(f"Result: {result.result}, Score: {result.score}")
        """
        start_time = time.time()
        
        try:
            # Build comprehensive evaluation prompt
            prompt = self._build_evaluation_prompt(
                test_output, criteria, test_context, expected_behavior
            )
            
            # Execute AI judgment
            raw_response = self._execute_ai_judgment(prompt)
            
            # Parse and structure the result
            judgment = self._parse_judgment_response(
                raw_response, criteria, test_context, time.time() - start_time
            )
            
            return judgment
            
        except Exception as e:
            # Return failure with error details
            return self._create_error_judgment(
                str(e), criteria, test_context, time.time() - start_time
            )
    
    def judge_code_quality(
        self,
        code: str,
        language: str,
        custom_criteria: Optional[List[str]] = None
    ) -> UnifiedJudgment:
        """Specialized method for code quality evaluation."""
        
        # Standard code quality criteria
        criteria = [
            TestCriterion("syntax", "Code is syntactically correct", weight=2.0),
            TestCriterion("logic", "Logic is correct and implements requirements", weight=2.0),
            TestCriterion("style", "Follows language style guidelines", weight=1.0, required=False),
            TestCriterion("readability", "Code is clear and well-structured", weight=1.5),
            TestCriterion("efficiency", "Implementation is reasonably efficient", weight=1.0, required=False)
        ]
        
        # Add custom criteria if provided
        if custom_criteria:
            for criterion in custom_criteria:
                criteria.append(TestCriterion(f"custom_{len(criteria)}", criterion, weight=1.0))
        
        test_context = {
            "test_type": "code_quality",
            "language": language,
            "test_id": f"code_{hash(code) % 10000}"
        }
        
        return self.judge_test(code, criteria, test_context)
    
    def judge_response_quality(
        self,
        response: str,
        prompt: str,
        evaluation_focus: Optional[List[str]] = None
    ) -> UnifiedJudgment:
        """Specialized method for AI response quality evaluation."""
        
        criteria = [
            TestCriterion("relevance", "Response addresses the prompt appropriately", weight=2.0),
            TestCriterion("accuracy", "Information provided is correct", weight=2.0),
            TestCriterion("completeness", "Response fully answers the question", weight=1.5),
            TestCriterion("clarity", "Response is clear and well-structured", weight=1.0),
            TestCriterion("helpfulness", "Response provides value to the user", weight=1.5)
        ]
        
        # Add focus areas if specified
        if evaluation_focus:
            for focus in evaluation_focus:
                criteria.append(TestCriterion(f"focus_{len(criteria)}", focus, weight=1.5))
        
        test_context = {
            "test_type": "response_quality",
            "original_prompt": prompt,
            "test_id": f"response_{hash(response) % 10000}"
        }
        
        expected_behavior = f"High-quality response to: {prompt[:100]}..."
        
        return self.judge_test(response, criteria, test_context, expected_behavior)
    
    def judge_conversation_quality(
        self,
        conversation: List[Dict[str, str]],
        persona_context: str,
        evaluation_criteria: Optional[List[str]] = None
    ) -> UnifiedJudgment:
        """Specialized method for conversation quality evaluation."""
        
        # Format conversation for evaluation
        conversation_text = self._format_conversation(conversation)
        
        criteria = [
            TestCriterion("persona_consistency", "Maintains character throughout conversation", weight=2.0),
            TestCriterion("context_awareness", "Demonstrates understanding of conversation flow", weight=2.0),
            TestCriterion("natural_flow", "Conversation feels natural and human-like", weight=1.5),
            TestCriterion("helpfulness", "Provides valuable assistance to the user", weight=1.5),
            TestCriterion("appropriateness", "Responses are appropriate for the context", weight=1.0)
        ]
        
        # Add custom evaluation criteria
        if evaluation_criteria:
            for criterion in evaluation_criteria:
                criteria.append(TestCriterion(f"custom_{len(criteria)}", criterion, weight=1.0))
        
        test_context = {
            "test_type": "conversation_quality",
            "persona": persona_context,
            "message_count": len(conversation),
            "test_id": f"conv_{hash(conversation_text) % 10000}"
        }
        
        return self.judge_test(conversation_text, criteria, test_context, persona_context)
    
    def judge_performance_metrics(
        self,
        metrics: Dict[str, float],
        targets: Dict[str, float],
        metric_descriptions: Optional[Dict[str, str]] = None
    ) -> UnifiedJudgment:
        """Specialized method for performance metrics evaluation."""
        
        criteria = []
        for metric_name, target_value in targets.items():
            actual_value = metrics.get(metric_name, 0.0)
            description = metric_descriptions.get(metric_name, f"{metric_name} meets target threshold")
            
            criteria.append(TestCriterion(
                name=metric_name,
                description=description,
                weight=1.0,
                required=True
            ))
        
        # Format metrics for evaluation
        metrics_text = json.dumps({"actual": metrics, "targets": targets}, indent=2)
        
        test_context = {
            "test_type": "performance_metrics",
            "metrics": metrics,
            "targets": targets,
            "test_id": f"perf_{hash(str(metrics)) % 10000}"
        }
        
        return self.judge_test(metrics_text, criteria, test_context)
    
    def _build_evaluation_prompt(
        self,
        test_output: str,
        criteria: List[TestCriterion],
        test_context: Dict[str, Any],
        expected_behavior: Optional[str] = None
    ) -> str:
        """Build comprehensive evaluation prompt for AI judge."""
        
        # Format criteria for prompt
        criteria_text = []
        for criterion in criteria:
            required_text = "REQUIRED" if criterion.required else "OPTIONAL"
            weight_text = f"(weight: {criterion.weight})"
            criteria_text.append(f"- {criterion.name}: {criterion.description} {weight_text} [{required_text}]")
        
        criteria_list = "\n".join(criteria_text)
        
        # Build evaluation context
        context_items = []
        for key, value in test_context.items():
            if key != "test_id":
                context_items.append(f"- {key}: {value}")
        context_text = "\n".join(context_items) if context_items else "No additional context"
        
        # Add expected behavior if provided
        expected_text = f"\nEXPECTED BEHAVIOR:\n{expected_behavior}\n" if expected_behavior else ""
        
        prompt = f"""You are a comprehensive AI test judge. Evaluate the following test output against the specified criteria.

EVALUATION INSTRUCTIONS:
- Provide objective, fair assessment based on the criteria
- Each criterion must be evaluated independently
- Consider criterion weights when calculating overall score
- Required criteria MUST pass for overall PASS result
- Provide specific, actionable feedback for improvements

TEST CONTEXT:
{context_text}
{expected_text}
EVALUATION CRITERIA:
{criteria_list}

TEST OUTPUT TO EVALUATE:
```
{test_output}
```

IMPORTANT: Respond with valid JSON in this exact format:
{{
    "overall_score": 0-100,
    "result": "pass|fail",
    "confidence": 0.0-1.0,
    "criteria_evaluation": {{
        "{criteria[0].name}": {{"score": 0-100, "pass": true|false, "reasoning": "explanation"}},
        "{criteria[1].name if len(criteria) > 1 else 'example'}": {{"score": 0-100, "pass": true|false, "reasoning": "explanation"}}
    }},
    "summary": "Brief evaluation summary",
    "detailed_feedback": "Comprehensive feedback explaining the judgment",
    "improvements": ["specific improvement 1", "specific improvement 2"],
    "strengths": ["strength 1", "strength 2"]
}}

Evaluation Guidelines:
1. Score each criterion 0-100 based on quality
2. Mark criterion as pass if score >= 70 (adjust for context)
3. Overall PASS requires: (weighted average >= {self.config.pass_threshold}) AND (all required criteria pass)
4. Provide specific, actionable improvements for failed criteria
5. Highlight what worked well in strengths
6. Be fair but thorough in your assessment

Respond with ONLY the JSON, no additional text."""

        return prompt
    
    def _execute_ai_judgment(self, prompt: str) -> str:
        """Execute AI judgment using local Ollama model."""
        try:
            cmd = ["ollama", "run", self.config.model]
            
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.config.timeout
            )
            
            if result.returncode != 0:
                raise Exception(f"AI model execution failed: {result.stderr}")
            
            return result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            raise Exception(f"AI evaluation timed out after {self.config.timeout} seconds")
        except FileNotFoundError:
            raise Exception("Ollama not found. Install with: curl -fsSL https://ollama.ai/install.sh | sh")
        except Exception as e:
            raise Exception(f"AI judgment execution error: {str(e)}")
    
    def _parse_judgment_response(
        self,
        raw_response: str,
        criteria: List[TestCriterion],
        test_context: Dict[str, Any],
        evaluation_time: float
    ) -> UnifiedJudgment:
        """Parse AI response into structured judgment."""
        
        try:
            # Clean up markdown code blocks if present
            cleaned_response = self._extract_json_from_response(raw_response)
            
            # Parse JSON response
            parsed = json.loads(cleaned_response)
            
            # Extract criteria results
            criteria_results = {}
            criteria_scores = {}
            
            criteria_eval = parsed.get("criteria_evaluation", {})
            for criterion in criteria:
                if criterion.name in criteria_eval:
                    eval_data = criteria_eval[criterion.name]
                    criteria_results[criterion.name] = eval_data.get("pass", False)
                    criteria_scores[criterion.name] = float(eval_data.get("score", 0))
                else:
                    # Default for missing criteria
                    criteria_results[criterion.name] = False
                    criteria_scores[criterion.name] = 0.0
            
            # Determine overall result
            overall_score = float(parsed.get("overall_score", 0))
            
            # Check if result should be PASS or FAIL
            confidence_raw = float(parsed.get("confidence", 0.5))
            
            # Pass requirements: score threshold AND required criteria
            score_passes = overall_score >= self.config.pass_threshold
            required_pass = all(
                criteria_results.get(c.name, False) 
                for c in criteria if c.required
            )
            
            result = JudgmentResult.PASS if (score_passes and required_pass) else JudgmentResult.FAIL
            
            # Determine confidence level
            if confidence_raw >= 0.8:
                confidence = ConfidenceLevel.HIGH
            elif confidence_raw >= 0.5:
                confidence = ConfidenceLevel.MEDIUM
            else:
                confidence = ConfidenceLevel.LOW
            
            return UnifiedJudgment(
                result=result,
                confidence=confidence,
                score=overall_score,
                criteria_results=criteria_results,
                criteria_scores=criteria_scores,
                summary=parsed.get("summary", "Evaluation completed"),
                feedback=parsed.get("detailed_feedback", "No detailed feedback provided"),
                improvements=parsed.get("improvements", []),
                strengths=parsed.get("strengths", []),
                test_id=test_context.get("test_id", "unknown"),
                test_type=test_context.get("test_type", "general"),
                judge_model=self.config.model,
                evaluation_time=evaluation_time
            )
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # Fallback to keyword-based parsing
            return self._fallback_judgment_parsing(
                raw_response, criteria, test_context, evaluation_time
            )
    
    def _extract_json_from_response(self, response: str) -> str:
        """Extract JSON from AI response, handling markdown code blocks."""
        
        # Handle markdown JSON blocks
        if "```json" in response:
            start_marker = response.find("```json") + 7
            end_marker = response.find("```", start_marker)
            if end_marker != -1:
                return response[start_marker:end_marker].strip()
        elif "```" in response:
            start_marker = response.find("```") + 3
            end_marker = response.find("```", start_marker)
            if end_marker != -1:
                return response[start_marker:end_marker].strip()
        
        # Try to find JSON object boundaries
        json_start = response.find('{')
        json_end = response.rfind('}') + 1
        
        if json_start != -1 and json_end > json_start:
            return response[json_start:json_end]
        
        raise ValueError("No valid JSON found in response")
    
    def _fallback_judgment_parsing(
        self,
        raw_response: str,
        criteria: List[TestCriterion],
        test_context: Dict[str, Any],
        evaluation_time: float
    ) -> UnifiedJudgment:
        """Fallback judgment when JSON parsing fails."""
        
        text_lower = raw_response.lower()
        
        # Simple keyword-based assessment
        if any(word in text_lower for word in ["excellent", "outstanding", "perfect", "great"]):
            score = 85.0
            result = JudgmentResult.PASS
        elif any(word in text_lower for word in ["good", "well", "solid", "adequate"]):
            score = 75.0
            result = JudgmentResult.PASS
        elif any(word in text_lower for word in ["satisfactory", "meets", "acceptable"]):
            score = 70.0
            result = JudgmentResult.PASS
        elif any(word in text_lower for word in ["poor", "weak", "insufficient"]):
            score = 40.0
            result = JudgmentResult.FAIL
        else:
            score = 30.0
            result = JudgmentResult.FAIL
        
        # Create default criteria results
        criteria_results = {c.name: (score >= 70.0) for c in criteria}
        criteria_scores = {c.name: score for c in criteria}
        
        return UnifiedJudgment(
            result=result,
            confidence=ConfidenceLevel.LOW,
            score=score,
            criteria_results=criteria_results,
            criteria_scores=criteria_scores,
            summary="Fallback evaluation due to parsing error",
            feedback=f"Could not parse structured response. Raw output: {raw_response[:200]}...",
            improvements=["Improve response format", "Provide more structured output"],
            strengths=["Response was generated"] if raw_response else [],
            test_id=test_context.get("test_id", "unknown"),
            test_type=test_context.get("test_type", "general"),
            judge_model=self.config.model,
            evaluation_time=evaluation_time
        )
    
    def _create_error_judgment(
        self,
        error_message: str,
        criteria: List[TestCriterion],
        test_context: Dict[str, Any],
        evaluation_time: float
    ) -> UnifiedJudgment:
        """Create judgment for technical failures."""
        
        criteria_results = {c.name: False for c in criteria}
        criteria_scores = {c.name: 0.0 for c in criteria}
        
        return UnifiedJudgment(
            result=JudgmentResult.FAIL,
            confidence=ConfidenceLevel.LOW,
            score=0.0,
            criteria_results=criteria_results,
            criteria_scores=criteria_scores,
            summary="Technical evaluation failure",
            feedback=f"Evaluation failed due to technical error: {error_message}",
            improvements=[
                "Check system configuration",
                "Verify AI model availability",
                "Review input format"
            ],
            strengths=[],
            test_id=test_context.get("test_id", "unknown"),
            test_type=test_context.get("test_type", "general"),
            judge_model=self.config.model,
            evaluation_time=evaluation_time
        )
    
    def _format_conversation(self, conversation: List[Dict[str, str]]) -> str:
        """Format conversation for evaluation."""
        formatted_messages = []
        for msg in conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            formatted_messages.append(f"{role}: {content}")
        
        return "\n".join(formatted_messages)


# Convenience functions for common use cases
def judge_code(
    code: str, 
    language: str, 
    custom_criteria: Optional[List[str]] = None,
    config: Optional[JudgeConfig] = None
) -> UnifiedJudgment:
    """Quick function to judge code quality."""
    judge = UnifiedJudge(config)
    return judge.judge_code_quality(code, language, custom_criteria)


def judge_response(
    response: str,
    prompt: str,
    evaluation_focus: Optional[List[str]] = None,
    config: Optional[JudgeConfig] = None
) -> UnifiedJudgment:
    """Quick function to judge AI response quality."""
    judge = UnifiedJudge(config)
    return judge.judge_response_quality(response, prompt, evaluation_focus)


def judge_conversation(
    conversation: List[Dict[str, str]],
    persona_context: str,
    evaluation_criteria: Optional[List[str]] = None,
    config: Optional[JudgeConfig] = None
) -> UnifiedJudgment:
    """Quick function to judge conversation quality."""
    judge = UnifiedJudge(config)
    return judge.judge_conversation_quality(conversation, persona_context, evaluation_criteria)


# Example usage and testing
if __name__ == "__main__":
    # Example: Judge code quality
    sample_code = """
    def fibonacci(n):
        if n <= 1:
            return n
        return fibonacci(n-1) + fibonacci(n-2)
    """
    
    print("🧪 Testing Unified Judge System")
    print("=" * 50)
    
    # Test code judgment
    result = judge_code(sample_code, "python", ["Has proper documentation"])
    
    print(f"Result: {result.result}")
    print(f"Score: {result.score}")
    print(f"Confidence: {result.confidence}")
    print(f"Summary: {result.summary}")
    print(f"Feedback: {result.feedback}")
    
    if result.improvements:
        print("Improvements:")
        for improvement in result.improvements:
            print(f"  - {improvement}")
            
    if result.strengths:
        print("Strengths:")
        for strength in result.strengths:
            print(f"  - {strength}")