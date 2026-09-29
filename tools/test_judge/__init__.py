"""
Test Judge Tool

AI-powered test evaluation and quality assessment for subjective testing.
"""

import os
from dataclasses import dataclass
from typing import Literal

import anthropic
import requests

from agent.llm.tasks import Backend, LLMTask, TaskKind
from config.models import MODEL_REASONING, OPENROUTER_SONNET, OPENROUTER_URL
from tools.llm_reply import ReplyTruncated, anthropic_text, openrouter_text, parse_last_json

# Thinking: free-text JSON judgment over a test run (raw Anthropic / OpenRouter).
# Fail-safe: an ``{"error": ...}`` dict on timeout, transport, or parse failure.
TEST_JUDGE = LLMTask(
    site="test_judge.judge",
    kind=TaskKind.THINKING,
    backend=Backend.ANTHROPIC,
)

DEFAULT_MODEL = MODEL_REASONING
DEFAULT_MODEL_OPENROUTER = OPENROUTER_SONNET

# Multi-criterion judgment benefits from up-front thinking, so the Anthropic
# request uses adaptive thinking at medium effort. MAX_TOKENS covers thinking
# plus the JSON reply on both paths (OpenRouter thinks by default too).
MAX_TOKENS = 8192
REQUEST_TIMEOUT = 120


@dataclass
class JudgmentResult:
    """Result of a test judgment."""

    pass_fail: bool
    confidence: float
    reasoning: str
    criteria_results: dict[str, bool]
    suggestions: list[str]


class TestJudgeError(Exception):
    """Test judgment operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def judge_test_result(
    test_output: str,
    expected_criteria: list[str],
    context: str | None = None,
    strictness: Literal["lenient", "standard", "strict"] = "standard",
) -> dict:
    """
    Judge a test result against expected criteria using AI.

    Args:
        test_output: The actual output from the test
        expected_criteria: List of criteria the output should meet
        context: Additional context about the test
        strictness: How strictly to judge (lenient, standard, strict)

    Returns:
        dict with judgment:
            - pass_fail: Overall pass/fail
            - confidence: Confidence score 0-1
            - reasoning: Explanation of judgment
            - criteria_results: Pass/fail for each criterion
            - suggestions: Improvement suggestions
    """
    if not test_output:
        return {"error": "Test output cannot be empty"}

    if not expected_criteria:
        return {"error": "Expected criteria cannot be empty"}

    # Try Anthropic first, fall back to OpenRouter
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    use_anthropic = bool(api_key)

    if not use_anthropic:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"}

    strictness_instructions = {
        "lenient": "Be generous in interpretation. Accept reasonable approximations.",
        "standard": "Apply reasonable standards. Minor issues don't cause failure.",
        "strict": "Be rigorous. All criteria must be fully met with no compromises.",
    }

    intro = "You are a test evaluator. Judge whether this test output meets the specified criteria."
    criteria_list = chr(10).join(f"- {c}" for c in expected_criteria)
    context_section = f"## Context\n{context}" if context else ""
    strictness_text = strictness_instructions.get(strictness, strictness_instructions["standard"])
    prompt = f"""{intro}

## Test Output
{test_output}

## Expected Criteria
{criteria_list}

{context_section}

## Strictness
{strictness_text}

## Instructions
For each criterion, determine if it is met. Then provide an overall pass/fail judgment.

Think the problem through before you answer, then end your reply with a JSON
object in this exact format (the JSON object must be the last thing in your reply):
{{
    "pass_fail": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of judgment",
    "criteria_results": {{
        "criterion text": true/false,
        ...
    }},
    "suggestions": ["suggestion 1", "suggestion 2"]
}}"""

    try:
        if use_anthropic:
            client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)
            response = client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
            )
            content = anthropic_text(response, max_tokens=MAX_TOKENS)
        else:
            raw_response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_MODEL_OPENROUTER,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": MAX_TOKENS,
                },
                timeout=REQUEST_TIMEOUT,
            )
            raw_response.raise_for_status()
            content = openrouter_text(raw_response.json(), max_tokens=MAX_TOKENS)

        if not content:
            return {"error": "No response from AI"}

        try:
            judgment = parse_last_json(content)
        except ValueError:
            return {
                "error": "Failed to parse AI response",
                "raw_response": content,
            }
        if not isinstance(judgment, dict):
            return {
                "error": "Failed to parse AI response",
                "raw_response": content,
            }
        judgment["test_output_preview"] = (
            test_output[:200] + "..." if len(test_output) > 200 else test_output
        )
        return judgment

    except ReplyTruncated as e:
        return {"error": str(e)}
    except anthropic.APITimeoutError:
        return {"error": "Judgment request timed out"}
    except anthropic.APIError as e:
        return {"error": f"API request failed: {str(e)}"}
    except requests.exceptions.Timeout:
        return {"error": "Judgment request timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def judge_batch(
    test_cases: list[dict],
    strictness: Literal["lenient", "standard", "strict"] = "standard",
) -> dict:
    """
    Judge multiple test results.

    Args:
        test_cases: List of dicts with 'output' and 'criteria' keys
        strictness: Strictness level for all judgments

    Returns:
        dict with:
            - results: List of judgment results
            - summary: Aggregate statistics
    """
    results = []
    passed = 0
    failed = 0

    for case in test_cases:
        output = case.get("output", "")
        criteria = case.get("criteria", [])
        context = case.get("context")

        result = judge_test_result(
            test_output=output,
            expected_criteria=criteria,
            context=context,
            strictness=strictness,
        )

        results.append(result)

        if "error" not in result:
            if result.get("pass_fail"):
                passed += 1
            else:
                failed += 1

    total = passed + failed
    return {
        "results": results,
        "summary": {
            "total": len(test_cases),
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / total if total > 0 else 0,
            "errors": len(test_cases) - total,
        },
    }


def create_quality_gate(
    criteria: list[str],
    min_pass_rate: float = 0.9,
    min_confidence: float = 0.8,
) -> dict:
    """
    Create a quality gate configuration.

    Args:
        criteria: List of criteria for the gate
        min_pass_rate: Minimum pass rate required
        min_confidence: Minimum confidence required

    Returns:
        Quality gate configuration dict
    """
    return {
        "criteria": criteria,
        "min_pass_rate": min_pass_rate,
        "min_confidence": min_confidence,
    }


if __name__ == "__main__":
    # Example usage
    result = judge_test_result(
        test_output="The function returns the sum of two numbers correctly.",
        expected_criteria=[
            "Output describes a mathematical operation",
            "Output mentions correctness or accuracy",
        ],
    )

    import json

    print(json.dumps(result, indent=2))
