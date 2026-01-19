"""
AI Judge Framework for Intelligence Validation

Uses AI models to evaluate test results based on criteria rather than keyword matching.
Supports local LLMs (Ollama) for cost-effective evaluation.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class JudgmentScore(Enum):
    """Score levels for AI judgments."""
    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    FAIL = "fail"


@dataclass
class JudgeConfig:
    """Configuration for AI judge."""
    model: str = "gemma2:3b"  # Local model for speed
    temperature: float = 0.1  # Low for consistency
    strict_mode: bool = True  # High quality standards
    custom_criteria: Optional[List[str]] = None
    timeout_seconds: int = 30
    fallback_to_heuristics: bool = True


@dataclass
class JudgmentResult:
    """Result of an AI judgment."""
    test_id: str
    overall_score: JudgmentScore
    criteria_scores: Dict[str, str]
    pass_fail: bool
    confidence: float
    reasoning: str
    timestamp: datetime = field(default_factory=datetime.now)
    model_used: str = ""
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "test_id": self.test_id,
            "overall_score": self.overall_score.value,
            "criteria_scores": self.criteria_scores,
            "pass_fail": self.pass_fail,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
            "model_used": self.model_used,
        }


def _call_ollama(prompt: str, config: JudgeConfig) -> Optional[str]:
    """Call Ollama for local LLM inference."""
    try:
        result = subprocess.run(
            [
                "ollama", "run", config.model,
                "--format", "json",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _call_openrouter(prompt: str, config: JudgeConfig) -> Optional[str]:
    """Call OpenRouter API for cloud LLM inference."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    try:
        import httpx
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemma-2-9b-it:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": config.temperature,
            },
            timeout=config.timeout_seconds
        )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        return None
    except Exception:
        return None


def _parse_judgment_response(response: str, test_id: str) -> JudgmentResult:
    """Parse LLM response into structured judgment."""
    # Try to parse as JSON first
    try:
        data = json.loads(response)
        return JudgmentResult(
            test_id=test_id,
            overall_score=JudgmentScore(data.get("overall_score", "acceptable")),
            criteria_scores=data.get("criteria_scores", {}),
            pass_fail=data.get("pass_fail", True),
            confidence=float(data.get("confidence", 0.7)),
            reasoning=data.get("reasoning", ""),
            raw_response=response
        )
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: Parse from natural language
    response_lower = response.lower()

    # Determine pass/fail
    pass_indicators = ["pass", "good", "excellent", "acceptable", "correct", "yes"]
    fail_indicators = ["fail", "poor", "incorrect", "no", "wrong", "bad"]

    pass_count = sum(1 for word in pass_indicators if word in response_lower)
    fail_count = sum(1 for word in fail_indicators if word in response_lower)

    pass_fail = pass_count > fail_count

    # Determine score
    if "excellent" in response_lower:
        score = JudgmentScore.EXCELLENT
        confidence = 0.9
    elif "good" in response_lower:
        score = JudgmentScore.GOOD
        confidence = 0.85
    elif "acceptable" in response_lower:
        score = JudgmentScore.ACCEPTABLE
        confidence = 0.75
    elif "poor" in response_lower:
        score = JudgmentScore.POOR
        confidence = 0.7
        pass_fail = False
    else:
        score = JudgmentScore.ACCEPTABLE if pass_fail else JudgmentScore.POOR
        confidence = 0.6

    return JudgmentResult(
        test_id=test_id,
        overall_score=score,
        criteria_scores={},
        pass_fail=pass_fail,
        confidence=confidence,
        reasoning=response[:500],
        raw_response=response
    )


def _heuristic_judgment(
    test_output: str,
    expected_criteria: List[str],
    test_id: str
) -> JudgmentResult:
    """Fallback heuristic-based judgment when LLM is unavailable."""
    output_lower = test_output.lower()
    criteria_met = 0
    criteria_scores = {}

    for criterion in expected_criteria:
        criterion_lower = criterion.lower()
        # Extract key words from criterion
        key_words = [w for w in criterion_lower.split() if len(w) > 3]

        matches = sum(1 for word in key_words if word in output_lower)
        score_ratio = matches / max(len(key_words), 1)

        if score_ratio > 0.5:
            criteria_scores[criterion] = "met"
            criteria_met += 1
        elif score_ratio > 0.2:
            criteria_scores[criterion] = "partial"
            criteria_met += 0.5
        else:
            criteria_scores[criterion] = "not met"

    pass_ratio = criteria_met / max(len(expected_criteria), 1)

    if pass_ratio > 0.8:
        score = JudgmentScore.GOOD
    elif pass_ratio > 0.5:
        score = JudgmentScore.ACCEPTABLE
    else:
        score = JudgmentScore.POOR

    return JudgmentResult(
        test_id=test_id,
        overall_score=score,
        criteria_scores=criteria_scores,
        pass_fail=pass_ratio > 0.5,
        confidence=0.5,  # Lower confidence for heuristic
        reasoning=f"Heuristic evaluation: {criteria_met}/{len(expected_criteria)} criteria met",
        model_used="heuristic"
    )


def judge_test_result(
    test_output: str,
    expected_criteria: List[str],
    test_context: Optional[Dict[str, Any]] = None,
    config: Optional[JudgeConfig] = None,
    test_id: Optional[str] = None
) -> JudgmentResult:
    """
    Judge test results using AI model.

    Args:
        test_output: The output to evaluate
        expected_criteria: List of criteria the output should meet
        test_context: Optional context about the test
        config: Judge configuration
        test_id: Optional test identifier

    Returns:
        JudgmentResult with pass/fail decision and reasoning
    """
    if config is None:
        config = JudgeConfig()
    if test_id is None:
        test_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Build evaluation prompt
    criteria_text = "\n".join(f"- {c}" for c in expected_criteria)
    context_text = json.dumps(test_context) if test_context else "No additional context"

    prompt = f"""Evaluate the following test output against the given criteria.

TEST OUTPUT:
{test_output[:2000]}

CRITERIA TO EVALUATE:
{criteria_text}

CONTEXT:
{context_text}

Respond with a JSON object containing:
- "overall_score": one of "excellent", "good", "acceptable", "poor", "fail"
- "criteria_scores": object mapping each criterion to "excellent", "good", "acceptable", "poor", or "fail"
- "pass_fail": true or false
- "confidence": number between 0 and 1
- "reasoning": brief explanation of your judgment

JSON response:"""

    # Try Ollama first (local, fast)
    response = _call_ollama(prompt, config)

    # Fallback to OpenRouter if Ollama fails
    if response is None:
        response = _call_openrouter(prompt, config)

    # Parse response
    if response:
        result = _parse_judgment_response(response, test_id)
        result.model_used = config.model
        return result

    # Final fallback to heuristics
    if config.fallback_to_heuristics:
        return _heuristic_judgment(test_output, expected_criteria, test_id)

    # If all else fails, return uncertain result
    return JudgmentResult(
        test_id=test_id,
        overall_score=JudgmentScore.ACCEPTABLE,
        criteria_scores={c: "unknown" for c in expected_criteria},
        pass_fail=True,  # Assume pass if we can't evaluate
        confidence=0.3,
        reasoning="Unable to evaluate: LLM unavailable and heuristics disabled",
        model_used="none"
    )


def judge_response_quality(
    response: str,
    prompt: str,
    evaluation_criteria: Optional[List[str]] = None,
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """
    Judge the quality of an AI response to a prompt.

    Args:
        response: The AI response to evaluate
        prompt: The original prompt that generated the response
        evaluation_criteria: Specific criteria to evaluate against
        config: Judge configuration

    Returns:
        JudgmentResult with quality assessment
    """
    if evaluation_criteria is None:
        evaluation_criteria = [
            "Response addresses the user's query directly",
            "Information is accurate and helpful",
            "Tone is appropriate for the context",
            "Response is concise without missing important details"
        ]

    context = {
        "test_type": "response_quality",
        "original_prompt": prompt[:500]
    }

    return judge_test_result(
        test_output=response,
        expected_criteria=evaluation_criteria,
        test_context=context,
        config=config,
        test_id=f"response_quality_{datetime.now().strftime('%H%M%S')}"
    )


def judge_tool_selection(
    selected_tools: List[str],
    user_intent: str,
    context: Optional[Dict[str, Any]] = None,
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """
    Judge whether the right tools were selected for a task.

    Args:
        selected_tools: List of tools that were selected
        user_intent: The user's original request
        context: Additional context about the selection
        config: Judge configuration

    Returns:
        JudgmentResult with tool selection assessment
    """
    criteria = [
        "Selected tools are appropriate for the user's request",
        "No unnecessary tools were selected",
        "All necessary tools were included",
        "Tool selection is efficient for the task"
    ]

    test_output = f"""
User Request: {user_intent}
Selected Tools: {', '.join(selected_tools)}
Context: {json.dumps(context) if context else 'None'}
"""

    return judge_test_result(
        test_output=test_output,
        expected_criteria=criteria,
        test_context={"test_type": "tool_selection"},
        config=config,
        test_id=f"tool_selection_{datetime.now().strftime('%H%M%S')}"
    )


class AIJudgeTestRunner:
    """Runner for AI-judged tests."""

    def __init__(self, config: Optional[JudgeConfig] = None):
        self.config = config or JudgeConfig()
        self.results: List[JudgmentResult] = []

    def add_result(self, result: JudgmentResult):
        """Add a judgment result."""
        self.results.append(result)

    def run_test(
        self,
        test_name: str,
        test_output: str,
        criteria: List[str]
    ) -> JudgmentResult:
        """Run a single test and record the result."""
        result = judge_test_result(
            test_output=test_output,
            expected_criteria=criteria,
            config=self.config,
            test_id=test_name
        )
        self.add_result(result)
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all test results."""
        if not self.results:
            return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}

        passed = sum(1 for r in self.results if r.pass_fail)
        total = len(self.results)

        avg_confidence = sum(r.confidence for r in self.results) / total

        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total,
            "average_confidence": avg_confidence,
            "results": [r.to_dict() for r in self.results]
        }

    def save_results(self, filepath: Path):
        """Save results to JSON file."""
        summary = self.get_summary()
        with open(filepath, "w") as f:
            json.dump(summary, f, indent=2, default=str)
