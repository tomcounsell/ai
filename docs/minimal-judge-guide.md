# Minimal Judge System Guide

A simple, focused approach to test evaluation that avoids over-engineering.

## Philosophy: Keep It Simple

**Use simple assertions for 90% of tests:**
```python
assert response.status_code == 200
assert len(data) > 0
assert "expected_text" in output
```

**Use AI judges only for subjective evaluation:**
- "Is this response helpful?"
- "Does this code look good?"  
- "Is this UI element visible?" (with screenshots)

## Quick Start

```python
from tools.minimal_judge import simple_pass_fail, judge_text, judge_screenshot

# For most tests - simple assertions
result = simple_pass_fail(value == expected, "Values don't match")
assert result["pass"], result["feedback"]

# For subjective evaluation - AI judge
result = judge_text(response, "Is this response helpful?")
assert result["pass"], result["feedback"]

# For UI testing - screenshot judge  
result = judge_screenshot("login_page.png", "Is the login button visible?")
assert result["pass"], result["feedback"]
```

## Core Functions

### `simple_pass_fail(condition, failure_message)` 
**Use this for 90% of your tests!**

For objective tests that have a clear true/false answer:

```python
# Performance testing
result = simple_pass_fail(
    response_time < 2.0,
    f"Too slow: {response_time}s > 2.0s limit"
)

# Data validation  
result = simple_pass_fail(
    len(results) == expected_count,
    f"Expected {expected_count} results, got {len(results)}"
)

# API testing
result = simple_pass_fail(
    response.status_code == 200,
    f"API failed with status {response.status_code}"
)
```

### `judge_text(text, question)` 
**For subjective text evaluation**

When you need AI to evaluate quality, helpfulness, or correctness:

```python
# Response quality
result = judge_text(
    ai_response, 
    "Is this response helpful and accurate?"
)

# Code review
result = judge_text(
    code_snippet,
    "Is this code well-written and following best practices?"
)

# Content evaluation
result = judge_text(
    generated_summary,
    "Does this summary capture the key points?"
)
```

### `judge_screenshot(image_path, question)`
**For visual UI testing**

Perfect for browser automation and UI testing:

```python
# Login page testing
result = judge_screenshot(
    "login_page.png",
    "Is the login button visible and clickable?"
)

# Navigation testing  
result = judge_screenshot(
    "dashboard.png", 
    "Is the user menu showing in the top-right?"
)

# Error state testing
result = judge_screenshot(
    "error_page.png",
    "Is there an error message displayed to the user?"
)

# Form validation
result = judge_screenshot(
    "form_validation.png",
    "Are the form validation errors clearly visible?"
)
```

## Return Format

All judge functions return the same simple format:

```python
{
    "pass": True,  # or False
    "feedback": "Test passed"  # or explanation of failure
}
```

Use it in your tests:

```python
result = judge_text(output, "Is this good?")
assert result["pass"], result["feedback"]
```

## Browser Testing Example

Complete example of using screenshot judge in browser tests:

```python
from selenium import webdriver
from tools.minimal_judge import judge_screenshot, simple_pass_fail

def test_login_flow():
    driver = webdriver.Chrome()
    
    try:
        # Navigate to login page
        driver.get("https://example.com/login")
        
        # Take screenshot
        driver.save_screenshot("login_page.png")
        
        # Use AI to check if login form is visible
        result = judge_screenshot(
            "login_page.png",
            "Is the login form with username and password fields visible?"
        )
        assert result["pass"], f"Login form not visible: {result['feedback']}"
        
        # Fill out form
        driver.find_element("name", "username").send_keys("testuser")
        driver.find_element("name", "password").send_keys("testpass")
        driver.find_element("css", "[type=submit]").click()
        
        # Take screenshot after login
        driver.save_screenshot("after_login.png")
        
        # Check if logged in successfully
        result = judge_screenshot(
            "after_login.png", 
            "Is the user dashboard or welcome message visible?"
        )
        assert result["pass"], f"Login failed: {result['feedback']}"
        
        # Also check URL changed (objective test)
        result = simple_pass_fail(
            "dashboard" in driver.current_url,
            f"Expected dashboard URL, got {driver.current_url}"
        )
        assert result["pass"], result["feedback"]
        
    finally:
        driver.quit()
```

## When to Use Which Judge

| Test Type | Function | Example |
|-----------|----------|---------|
| **Value comparison** | `simple_pass_fail` | `assert value == expected` |
| **Performance check** | `simple_pass_fail` | `assert response_time < 2.0` |
| **API response** | `simple_pass_fail` | `assert status == 200` |
| **Count/length** | `simple_pass_fail` | `assert len(items) == 5` |
| **Response quality** | `judge_text` | "Is this helpful?" |
| **Code quality** | `judge_text` | "Is this well-written?" |
| **UI element visible** | `judge_screenshot` | "Is button showing?" |
| **Page layout** | `judge_screenshot` | "Does page look right?" |

## Model Configuration

### Text Evaluation
- **Default model**: `gemma3:12b-it-qat` (good instruction following)
- **Requirements**: Ollama installed with model available
- **Timeout**: 30 seconds per evaluation

### Screenshot Evaluation  
- **Default model**: `granite3.2-vision:latest` (has vision capabilities)
- **Supported formats**: PNG, JPG, most image formats
- **Requirements**: Vision-capable model in Ollama

### Setup
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull required models
ollama pull gemma3:12b-it-qat
ollama pull granite3.2-vision:latest
```

## Best Practices

### 1. Prefer Simple Assertions
Most tests should use simple assertions:

```python
# Good - simple and fast
assert response.status_code == 200
assert "success" in response.json()

# Avoid - unnecessary AI call for objective test
result = judge_text(str(response.json()), "Does this indicate success?")
```

### 2. Use AI Judges Sparingly
Only for truly subjective evaluation:

```python
# Good - subjective question needs AI
result = judge_text(summary, "Is this summary accurate and helpful?")

# Bad - objective question, use assertion
result = judge_text(output, "Does this contain the word 'error'?")
# Should be: assert "error" in output
```

### 3. Clear, Specific Questions
Make questions specific and actionable:

```python
# Good - specific and clear
judge_screenshot("page.png", "Is the red 'Submit' button visible in the bottom-right?")

# Bad - vague and subjective  
judge_screenshot("page.png", "Does this page look good?")
```

### 4. Handle Failures Gracefully
Always check results and provide context:

```python
result = judge_screenshot("login.png", "Is login form visible?")
assert result["pass"], f"Login form check failed: {result['feedback']}"
```

## Migration from Complex Judge Systems

If you have existing complex judge code, simplify it:

```python
# Before - over-engineered
judgment = UnifiedJudge().judge_test(
    test_output=code,
    criteria=[
        TestCriterion("syntax", "Code is syntactically correct", weight=2.0),
        TestCriterion("style", "Code follows style guidelines", weight=1.0)  
    ],
    test_context={"test_type": "code_quality"}
)

# After - simple and clear
result = judge_text(code, "Is this code syntactically correct and well-formatted?")
assert result["pass"], result["feedback"]
```

## Troubleshooting

### AI Model Not Available
```python
# Error: "Ollama not found" or "Model not available"
# Solution: Install and pull models

curl -fsSL https://ollama.ai/install.sh | sh
ollama pull gemma3:12b-it-qat
ollama pull granite3.2-vision:latest
```

### Screenshot Judge Fails
```python
# Error: "Screenshot file not found"  
# Solution: Check file path and ensure screenshot was saved

# Error: "Vision model failed"
# Solution: Ensure granite3.2-vision model is available
ollama pull granite3.2-vision:latest
```

### Timeout Issues
```python
# Error: "AI evaluation timed out"
# Solution: Use simpler questions or increase timeout in model call
```

## Examples from Real Tests

### Response Quality Testing
```python
def test_ai_response_quality():
    response = ai_agent.respond("What is Python?")
    
    result = judge_text(
        response,
        "Is this response accurate and helpful for someone learning Python?"
    )
    assert result["pass"], result["feedback"]
```

### Browser UI Testing
```python
def test_navigation_menu():
    driver.get("https://app.example.com")
    driver.save_screenshot("nav_test.png")
    
    result = judge_screenshot(
        "nav_test.png",
        "Is the main navigation menu visible with Home, About, Contact links?"
    )
    assert result["pass"], result["feedback"]
```

### Code Quality Testing
```python
def test_generated_code():
    generated_code = code_generator.generate("fibonacci function")
    
    result = judge_text(
        generated_code,
        "Is this a correct implementation of the Fibonacci sequence?"
    )
    assert result["pass"], result["feedback"]
```

The minimal judge system provides exactly what you need without over-engineering: simple pass/fail decisions with helpful feedback when things go wrong.