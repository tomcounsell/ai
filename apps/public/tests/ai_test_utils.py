"""
Utilities for AI-powered browser testing.

This module provides helper functions and utilities for working with browser-use
and AI-powered browser testing.
"""

import asyncio
import json
import os
import uuid
from typing import Any, Dict, List, Optional

try:
    import playwright.async_api
    from browser_use import Agent, BrowserAgent, use
    from playwright.async_api import Browser, BrowserContext, Page

    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False

    class Page:
        pass

    class BrowserAgent:
        pass


async def create_test_scenario(
    feature_name: str, complexity: str = "basic"
) -> list[str]:
    """
    Generate a test scenario using AI for the given feature.

    Args:
        feature_name: The name of the feature to test
        complexity: The complexity of the test ("basic", "medium", "comprehensive")

    Returns:
        List of test steps
    """
    if not BROWSER_USE_AVAILABLE:
        return [
            f"# browser-use not available - would generate test for: {feature_name}"
        ]

    # Base prompts for different complexity levels
    prompts = {
        "basic": "Create a simple test that covers the core functionality",
        "medium": "Create a test that covers the main user flow and error cases",
        "comprehensive": "Create an exhaustive test covering all edge cases and variations",
    }

    # Get the base prompt for the specified complexity
    base_prompt = prompts.get(complexity, prompts["basic"])

    # Set limits based on complexity
    max_steps = 10 if complexity == "basic" else 20 if complexity == "medium" else 30

    # Define test scenario prompt
    prompt = f"""
    {base_prompt} for the following feature in a Django web application:

    FEATURE: {feature_name}

    Create a detailed browser test scenario that:
    1. Uses specific step-by-step instructions for browser-use to execute
    2. Tests the full workflow of this feature
    3. Verifies expected results at each step
    4. Takes screenshots at important moments
    5. Has at most {max_steps} steps

    Format each step as a specific instruction that browser-use can execute.
    Use [USERNAME] and [PASSWORD] placeholders for credentials.
    Use [TODO_TITLE] or similar placeholders for dynamic content.

    Examples of good steps:
    - "Go to http://localhost:8000/account/login/"
    - "Fill in the username field with [USERNAME]"
    - "Click on the 'Save' button"
    - "Verify that the page contains the text 'Item saved successfully'"
    - "Take a screenshot and save it as 'login_complete.png'"
    """

    # Use browser-use Agent to generate the scenario
    agent = Agent()
    response = await agent.complete(prompt)

    # Parse the response into a list of steps
    steps = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("STEP"):
            # Remove step numbers if present (e.g., "1. Go to...")
            if line[0].isdigit() and line[1:3] in [". ", ") "]:
                line = line[3:]
            steps.append(line)

    return steps


async def analyze_test_failure(failure_report: dict[str, Any]) -> str:
    """
    Use AI to analyze a test failure and suggest potential fixes.

    Args:
        failure_report: The test failure report

    Returns:
        Analysis and suggestions for fixing the test
    """
    if not BROWSER_USE_AVAILABLE:
        return "browser-use not available for failure analysis"

    # Extract key information from the failure report
    steps = failure_report.get("steps", [])
    issues = failure_report.get("issues", [])
    screenshots = failure_report.get("screenshots", [])

    # Create a prompt for analysis
    prompt = f"""
    Analyze this browser test failure and suggest fixes:

    TEST: {failure_report.get("test_name", "Unknown test")}

    STEPS EXECUTED:
    {json.dumps(steps, indent=2)}

    ISSUES REPORTED:
    {json.dumps(issues, indent=2)}

    {len(screenshots)} screenshots were captured during the test.

    Based on this information, provide:
    1. The most likely cause of the failure
    2. Specific suggestions to fix the test
    3. Possible application issues that might have caused the failure
    """

    # Use browser-use Agent to analyze
    agent = Agent()
    analysis = await agent.complete(prompt)

    return analysis


async def prepare_test_data(data_requirements: list[str]) -> dict[str, Any]:
    """
    Use AI to help prepare test data based on requirements.

    Args:
        data_requirements: List of data requirements

    Returns:
        Dictionary of prepared test data
    """
    if not BROWSER_USE_AVAILABLE:
        return {"error": "browser-use not available for data preparation"}

    # Create a prompt for data preparation
    requirements_text = "\n".join([f"- {req}" for req in data_requirements])

    prompt = f"""
    Prepare test data according to these requirements:

    {requirements_text}

    For each requirement, generate appropriate test data.
    Return the data in a structured format suitable for testing.
    Include variations for different test cases where appropriate.

    Format your response as a valid JSON object with meaningful keys.
    """

    # Use browser-use Agent to generate data
    agent = Agent()
    response = await agent.complete(prompt)

    # Try to parse the response as JSON
    try:
        # Clean the response to extract just the JSON part
        # This is needed because the agent might add explanatory text
        response_lines = response.strip().split("\n")
        json_start = 0
        json_end = len(response_lines)

        for i, line in enumerate(response_lines):
            if line.strip().startswith("{"):
                json_start = i
                break

        for i, line in enumerate(response_lines[::-1]):
            if line.strip().endswith("}"):
                json_end = len(response_lines) - i
                break

        json_text = "\n".join(response_lines[json_start:json_end])
        data = json.loads(json_text)
        return data
    except json.JSONDecodeError:
        # If parsing fails, return the raw response
        return {"raw_response": response}


async def generate_data_for_model(
    model_name: str, field_specs: dict[str, str] | None = None
) -> dict[str, Any]:
    """
    Generate test data for a Django model.

    Args:
        model_name: Name of the Django model
        field_specs: Optional specifications for specific fields

    Returns:
        Dictionary with generated field values
    """
    if not BROWSER_USE_AVAILABLE:
        return {"error": "browser-use not available for data generation"}

    field_specs_text = ""
    if field_specs:
        field_specs_text = "\n".join(
            [f"- {field}: {spec}" for field, spec in field_specs.items()]
        )

    prompt = f"""
    Generate appropriate test data for a Django model named "{model_name}".

    Field specifications:
    {field_specs_text if field_specs else "No specific requirements - generate reasonable values for typical fields"}

    Create a complete set of field values that would be valid for this model.
    Return the data as a JSON object with field names as keys.
    Include realistic values for common fields like names, emails, descriptions, etc.

    Format your response as a valid JSON object.
    """

    # Use browser-use Agent to generate data
    agent = Agent()
    response = await agent.complete(prompt)

    # Try to parse the response as JSON
    try:
        # Extract the JSON part from the response
        import re

        json_match = re.search(r"(\{.*\})", response, re.DOTALL)
        if json_match:
            json_text = json_match.group(1)
            data = json.loads(json_text)
            return data
        else:
            return {"raw_response": response}
    except (json.JSONDecodeError, Exception) as e:
        return {"error": str(e), "raw_response": response}


class TestDataGenerator:
    """Helper class for generating test data."""

    @staticmethod
    def random_email() -> str:
        """Generate a random email address."""
        return f"test_{uuid.uuid4().hex[:8]}@example.com"

    @staticmethod
    def random_username() -> str:
        """Generate a random username."""
        return f"testuser_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def random_password() -> str:
        """Generate a random but valid password."""
        return f"TestPass_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def random_title() -> str:
        """Generate a random title."""
        return f"Test Title {uuid.uuid4().hex[:8]}"

    @staticmethod
    def random_description() -> str:
        """Generate a random description."""
        return f"This is a test description {uuid.uuid4().hex[:8]}"

    @staticmethod
    def random_phone() -> str:
        """Generate a random phone number."""
        return f"+1555{uuid.uuid4().hex[:8][:8]}"

    @staticmethod
    def random_url() -> str:
        """Generate a random URL."""
        return f"https://example.com/{uuid.uuid4().hex[:8]}"

    @staticmethod
    def random_address() -> dict[str, str]:
        """Generate a random address."""
        return {
            "street": f"{uuid.uuid4().hex[:3]} Main St",
            "city": "Testville",
            "state": "CA",
            "zip": f"9{uuid.uuid4().hex[:4]}",
            "country": "US",
        }


def ensure_test_dirs():
    """Ensure that test directories exist."""
    dirs = [
        "test_screenshots",
        "test_screenshots/ai_testing",
        "test_screenshots/visual",
        "test_reports",
        "test_reports/ai_browser",
    ]

    for dir_path in dirs:
        os.makedirs(dir_path, exist_ok=True)


def is_server_running(host: str = "localhost", port: int = 8000) -> bool:
    """Check if the Django server is running."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, port))
    sock.close()
    return result == 0


if __name__ == "__main__":
    # Run a simple demo when this file is executed directly
    async def demo():
        print("AI Test Utils Demo")

        if not BROWSER_USE_AVAILABLE:
            print("browser-use not available. Install with:")
            print("uv add --dev browser-use playwright pytest-asyncio")
            return

        ensure_test_dirs()

        # Generate a test scenario
        print("\nGenerating test scenario...")
        steps = await create_test_scenario("User login to the application", "basic")
        print("\nGenerated steps:")
        for i, step in enumerate(steps, 1):
            print(f"{i}. {step}")

        # Generate test data
        print("\nGenerating test data...")
        data = await generate_data_for_model(
            "User",
            {"username": "Must be unique", "email": "Must be valid email format"},
        )
        print("\nGenerated data:")
        print(json.dumps(data, indent=2))

        print("\nDemo complete!")

    asyncio.run(demo())
