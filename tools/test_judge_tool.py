"""Test judging tool for AI subjective testing with local model support."""

import json
import subprocess
import tempfile
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from enum import Enum


class JudgmentScore(str, Enum):
    """Standardized judgment scores."""
    EXCELLENT = "excellent"
    GOOD = "good" 
    SATISFACTORY = "satisfactory"
    POOR = "poor"
    FAIL = "fail"


class JudgmentResult(BaseModel):
    """Structured test judgment result."""
    test_id: str
    overall_score: JudgmentScore
    criteria_scores: Dict[str, JudgmentScore]
    detailed_feedback: str
    pass_fail: bool = Field(description="Binary pass/fail determination")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in judgment (0-1)")
    reasoning: str = Field(description="Brief explanation of the judgment")


class JudgeConfig(BaseModel):
    """Configuration for test judging."""
    model: str = Field(default="gemma2:3b", description="Local model to use for judging")
    temperature: float = Field(default=0.1, description="Model temperature for consistency")
    strict_mode: bool = Field(default=True, description="Whether to use strict pass/fail criteria")
    custom_criteria: Optional[List[str]] = Field(default=None, description="Additional evaluation criteria")


def judge_test_result(
    test_output: str,
    expected_criteria: List[str],
    test_context: Dict[str, Any],
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """
    Judge test results using local AI model.
    
    Uses Ollama with local models for fast, cost-effective test evaluation.
    Particularly effective for binary pass/fail decisions and consistent scoring.
    """
    if config is None:
        config = JudgeConfig()
    
    # Build comprehensive prompt for the judge
    judge_prompt = _build_judge_prompt(test_output, expected_criteria, test_context, config)
    
    # Execute judgment using local model
    judgment_result = _execute_local_judgment(judge_prompt, config)
    
    # Parse and structure the result
    return _parse_judgment_result(judgment_result, test_context.get("test_id", "unknown"))


def judge_code_quality(
    code: str,
    language: str,
    quality_criteria: List[str],
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """Judge code quality using local model."""
    test_context = {
        "test_id": f"code_quality_{hash(code) % 10000}",
        "test_type": "code_quality",
        "language": language
    }
    
    return judge_test_result(code, quality_criteria, test_context, config)


def judge_response_quality(
    response: str,
    prompt: str,
    evaluation_criteria: List[str],
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """Judge AI response quality."""
    test_context = {
        "test_id": f"response_quality_{hash(response) % 10000}",
        "test_type": "response_evaluation",
        "original_prompt": prompt
    }
    
    return judge_test_result(response, evaluation_criteria, test_context, config)


def batch_judge_tests(
    test_cases: List[Dict[str, Any]],
    config: Optional[JudgeConfig] = None
) -> List[JudgmentResult]:
    """Judge multiple test cases in batch for efficiency."""
    results = []
    
    for test_case in test_cases:
        judgment = judge_test_result(
            test_output=test_case["output"],
            expected_criteria=test_case["criteria"],
            test_context=test_case.get("context", {}),
            config=config
        )
        results.append(judgment)
    
    return results


def _build_judge_prompt(
    test_output: str,
    expected_criteria: List[str],
    test_context: Dict[str, Any],
    config: JudgeConfig
) -> str:
    """Build comprehensive prompt for local model judgment."""
    
    criteria_text = "\n".join([f"- {criterion}" for criterion in expected_criteria])
    
    if config.custom_criteria:
        additional_criteria = "\n".join([f"- {criterion}" for criterion in config.custom_criteria])
        criteria_text += f"\n\nAdditional Criteria:\n{additional_criteria}"
    
    strict_instructions = ""
    if config.strict_mode:
        strict_instructions = """
STRICT MODE: Apply rigorous standards. Only mark as PASS if ALL criteria are clearly met.
When in doubt, prefer FAIL to maintain quality standards.
"""
    
    prompt = f"""You are an AI test judge. Evaluate the following test output against the specified criteria.

{strict_instructions}

TEST CONTEXT:
- Test Type: {test_context.get('test_type', 'general')}
- Test ID: {test_context.get('test_id', 'unknown')}

EVALUATION CRITERIA:
{criteria_text}

TEST OUTPUT TO JUDGE:
```
{test_output}
```

Provide your judgment in this exact JSON format:
{{
    "overall_score": "excellent|good|satisfactory|poor|fail",
    "pass_fail": true|false,
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of judgment",
    "criteria_scores": {{
        "criterion_1": "excellent|good|satisfactory|poor|fail",
        "criterion_2": "excellent|good|satisfactory|poor|fail"
    }},
    "detailed_feedback": "Specific feedback on strengths and weaknesses"
}}

Judge based on:
1. Does the output meet each specified criterion?
2. Overall quality and completeness
3. Any critical issues or failures
4. Consistency with expected behavior

Respond with ONLY the JSON, no additional text."""
    
    return prompt


def _execute_local_judgment(prompt: str, config: JudgeConfig) -> str:
    """Execute judgment using local Ollama model."""
    try:
        # Execute Ollama command directly with prompt as input
        cmd = ["ollama", "run", "--temperature", str(config.temperature), config.model]
        
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,  # 60 second timeout
            shell=False
        )
        
        if result.returncode != 0:
            raise Exception(f"Ollama execution failed: {result.stderr}")
        
        return result.stdout.strip()
        
    except subprocess.TimeoutExpired:
        return _fallback_judgment("Timeout during model execution")
    except FileNotFoundError:
        return _fallback_judgment("Ollama not found - install with: curl -fsSL https://ollama.ai/install.sh | sh")
    except Exception as e:
        return _fallback_judgment(f"Error executing local model: {str(e)}")


def _parse_judgment_result(raw_result: str, test_id: str) -> JudgmentResult:
    """Parse raw judgment result into structured format."""
    try:
        # Clean up markdown code blocks if present
        cleaned_result = raw_result
        if "```json" in cleaned_result:
            # Extract content between ```json and ```
            start_marker = cleaned_result.find("```json") + 7
            end_marker = cleaned_result.find("```", start_marker)
            if end_marker != -1:
                cleaned_result = cleaned_result[start_marker:end_marker].strip()
        elif "```" in cleaned_result:
            # Handle generic code blocks
            start_marker = cleaned_result.find("```") + 3
            end_marker = cleaned_result.find("```", start_marker)
            if end_marker != -1:
                cleaned_result = cleaned_result[start_marker:end_marker].strip()
        
        # Try to extract JSON from the response
        json_start = cleaned_result.find('{')
        json_end = cleaned_result.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("No JSON found in response")
        
        json_str = cleaned_result[json_start:json_end]
        parsed = json.loads(json_str)
        
        return JudgmentResult(
            test_id=test_id,
            overall_score=JudgmentScore(parsed["overall_score"]),
            criteria_scores={k: JudgmentScore(v) for k, v in parsed.get("criteria_scores", {}).items()},
            detailed_feedback=parsed.get("detailed_feedback", "No detailed feedback provided"),
            pass_fail=parsed.get("pass_fail", False),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", "No reasoning provided")
        )
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback parsing for malformed responses
        return _fallback_judgment_parsing(raw_result, test_id)


def _fallback_judgment_parsing(raw_result: str, test_id: str) -> JudgmentResult:
    """Fallback judgment parsing when JSON parsing fails."""
    # Simple keyword-based fallback
    text_lower = raw_result.lower()
    
    if any(word in text_lower for word in ["excellent", "outstanding", "perfect"]):
        overall_score = JudgmentScore.EXCELLENT
        pass_fail = True
    elif any(word in text_lower for word in ["good", "well", "solid"]):
        overall_score = JudgmentScore.GOOD
        pass_fail = True
    elif any(word in text_lower for word in ["satisfactory", "adequate", "meets"]):
        overall_score = JudgmentScore.SATISFACTORY
        pass_fail = True
    elif any(word in text_lower for word in ["poor", "weak", "insufficient"]):
        overall_score = JudgmentScore.POOR
        pass_fail = False
    else:
        overall_score = JudgmentScore.FAIL
        pass_fail = False
    
    return JudgmentResult(
        test_id=test_id,
        overall_score=overall_score,
        criteria_scores={},
        detailed_feedback=f"Fallback parsing of response: {raw_result[:200]}...",
        pass_fail=pass_fail,
        confidence=0.3,  # Low confidence for fallback parsing
        reasoning="Parsed using fallback method due to JSON parsing error"
    )


def _fallback_judgment(error_message: str) -> str:
    """Generate fallback judgment when model execution fails."""
    return json.dumps({
        "overall_score": "fail",
        "pass_fail": False,
        "confidence": 0.0,
        "reasoning": f"Technical failure: {error_message}",
        "criteria_scores": {},
        "detailed_feedback": f"Unable to complete judgment due to: {error_message}"
    })


# Example usage functions for specific test types
def judge_ui_feedback(feedback: str, ui_context: Dict[str, Any]) -> JudgmentResult:
    """Judge UI feedback quality."""
    criteria = [
        "provides specific actionable suggestions",
        "considers user experience principles", 
        "appropriate tone for target audience",
        "addresses usability concerns"
    ]
    
    return judge_test_result(feedback, criteria, {
        "test_id": f"ui_feedback_{hash(feedback) % 10000}",
        "test_type": "ui_feedback",
        **ui_context
    })


def judge_code_review(review: str, code_context: Dict[str, Any]) -> JudgmentResult:
    """Judge code review quality."""
    criteria = [
        "identifies actual issues in the code",
        "provides constructive improvement suggestions",
        "follows code review best practices",
        "appropriate level of detail"
    ]
    
    return judge_test_result(review, criteria, {
        "test_id": f"code_review_{hash(review) % 10000}",
        "test_type": "code_review",
        **code_context
    })