"""
Minimal Test Judge - Simple AI-powered evaluation for subjective tests.

Use this only when you need AI evaluation. For most tests, use simple assertions:
- assert value == expected
- assert response.status_code == 200
- assert "text" in output

Use AI judge only for subjective evaluation:
- "Is this response helpful?"
- "Does this code look good?"
- "Is this UI element visible?" (with screenshot)
"""

import json
import subprocess
import base64
from typing import Dict, Any, Optional
from pathlib import Path


def judge_text(text: str, question: str, model: str = "gemma3:12b-it-qat") -> Dict[str, Any]:
    """
    Ask AI to evaluate text and return pass/fail with feedback.
    
    Args:
        text: The text/output to evaluate
        question: What you want to check (e.g., "Is this response helpful?")
        model: Ollama model to use
        
    Returns:
        {"pass": bool, "feedback": str}
        
    Example:
        >>> result = judge_text("Hello there!", "Is this a friendly greeting?")
        >>> print(result["pass"])  # True
        >>> if not result["pass"]:
        ...     print(result["feedback"])  # What's wrong
    """
    
    prompt = f"""Question: {question}

Text to evaluate:
{text}

Respond with ONLY this JSON format:
{{"pass": true/false, "feedback": "brief explanation"}}

If it passes your evaluation, set pass=true with positive feedback.
If it fails, set pass=false and explain what's wrong or missing."""

    return _call_ai_model(prompt, model)


def judge_code(code: str, question: str = "Is this code correct and readable?", model: str = "gemma3:12b-it-qat") -> Dict[str, Any]:
    """
    Evaluate code quality with AI.
    
    Args:
        code: Code to evaluate
        question: What to check (default: general code quality)
        model: Ollama model to use
        
    Returns:
        {"pass": bool, "feedback": str}
    """
    return judge_text(code, question, model)


def judge_screenshot(
    image_path: str, 
    question: str, 
    model: str = "gemma3:12b-it-qat"
) -> Dict[str, Any]:
    """
    Evaluate a screenshot with AI vision and return pass/fail.
    
    Perfect for browser tests:
    - "Is the login button visible?"
    - "Is the user menu showing?" 
    - "Does the page look correct?"
    - "Is there an error message displayed?"
    
    Args:
        image_path: Path to screenshot image file
        question: What to check in the image
        model: Ollama model with vision support
        
    Returns:
        {"pass": bool, "feedback": str}
        
    Example:
        >>> # After taking a screenshot
        >>> result = judge_screenshot("login_page.png", "Is the login button visible?")
        >>> assert result["pass"], result["feedback"]
    """
    
    # Check if image file exists
    if not Path(image_path).exists():
        return {"pass": False, "feedback": f"Screenshot file not found: {image_path}"}
    
    try:
        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
        
        # Build prompt for vision model
        prompt = f"""Look at this screenshot and answer: {question}

Respond with ONLY this JSON format:
{{"pass": true/false, "feedback": "what you see and your assessment"}}

If what's asked about is visible/present, set pass=true.
If it's missing or not visible, set pass=false and describe what you see instead."""

        # Use vision-capable model for screenshots
        vision_model = "granite3.2-vision:latest"  # Has vision capabilities
        
        # Create temporary file with image prompt
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            prompt_file = f.name
        
        try:
            # Call vision model with image
            cmd = ["ollama", "run", vision_model, f"[img:{image_path}] $(cat {prompt_file})"]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                shell=True
            )
            
            if result.returncode != 0:
                return {"pass": False, "feedback": f"Vision model failed: {result.stderr}"}
            
            return _parse_json_response(result.stdout.strip())
            
        finally:
            # Clean up temp file
            Path(prompt_file).unlink(missing_ok=True)
            
    except Exception as e:
        return {"pass": False, "feedback": f"Screenshot evaluation error: {str(e)}"}


def simple_pass_fail(condition: bool, failure_message: str = "Test failed") -> Dict[str, Any]:
    """
    For simple objective tests that don't need AI evaluation.
    Use this for most tests!
    
    Args:
        condition: True if test passes, False if it fails
        failure_message: What to show when it fails
        
    Returns:
        {"pass": bool, "feedback": str}
        
    Example:
        >>> result = simple_pass_fail(response_time < 2.0, f"Too slow: {response_time}s")
        >>> assert result["pass"], result["feedback"]
    """
    return {
        "pass": condition,
        "feedback": "Test passed" if condition else failure_message
    }


def _call_ai_model(prompt: str, model: str) -> Dict[str, Any]:
    """Call AI model and parse response."""
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return {"pass": False, "feedback": f"AI model failed: {result.stderr}"}
        
        return _parse_json_response(result.stdout.strip())
        
    except subprocess.TimeoutExpired:
        return {"pass": False, "feedback": "AI evaluation timed out"}
    except Exception as e:
        return {"pass": False, "feedback": f"AI evaluation error: {str(e)}"}


def _parse_json_response(response_text: str) -> Dict[str, Any]:
    """Parse JSON response from AI model."""
    try:
        # Handle markdown code blocks
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        
        # Find JSON in response
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            response_text = response_text[json_start:json_end]
        
        parsed = json.loads(response_text)
        
        return {
            "pass": bool(parsed.get("pass", False)),
            "feedback": str(parsed.get("feedback", "No feedback provided"))
        }
        
    except json.JSONDecodeError:
        # Fallback: guess based on keywords
        text_lower = response_text.lower()
        if any(word in text_lower for word in ["yes", "correct", "good", "visible", "present"]):
            return {"pass": True, "feedback": "Appears to pass (fallback evaluation)"}
        else:
            return {"pass": False, "feedback": f"Appears to fail: {response_text[:100]}..."}


# Convenience functions for common use cases
def check_response_quality(response: str, prompt: str) -> Dict[str, Any]:
    """Check if an AI response is helpful and accurate."""
    return judge_text(
        f"PROMPT: {prompt}\n\nRESPONSE: {response}",
        "Is this response helpful and accurate?"
    )


def check_code_works(code: str, language: str = "python") -> Dict[str, Any]:
    """Check if code looks correct and follows good practices."""
    return judge_code(code, f"Is this {language} code correct and well-written?")


def check_ui_element(screenshot_path: str, element_description: str) -> Dict[str, Any]:
    """Check if a UI element is visible in a screenshot."""
    return judge_screenshot(screenshot_path, f"Is the {element_description} visible and properly displayed?")


if __name__ == "__main__":
    # Quick test of the minimal judge system
    print("🧪 Testing Minimal Judge System")
    print("=" * 40)
    
    # Test 1: Simple pass/fail (most common case)
    result = simple_pass_fail(2 + 2 == 4, "Math is broken")
    print(f"Simple math: {result}")
    
    # Test 2: Simple failure
    result = simple_pass_fail(2 + 2 == 5, "Expected 2+2=4, got 5")  
    print(f"Wrong math: {result}")
    
    # Test 3: Performance check
    response_time = 1.5
    result = simple_pass_fail(
        response_time < 2.0,
        f"Response too slow: {response_time}s > 2.0s limit"
    )
    print(f"Performance: {result}")
    
    print("\n💡 Use simple_pass_fail() for 90% of your tests!")
    print("   Only use AI judges for subjective evaluation.")
    
    # Note: AI judge tests would require actual AI model calls
    # which we're not running in this demo