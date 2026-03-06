"""Shared fixtures for tool tests."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load shared API keys from parent env file
load_dotenv(Path.home() / "src" / ".env")
load_dotenv()


@pytest.fixture
def perplexity_api_key():
    """Get Perplexity API key for tests."""
    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        pytest.skip("PERPLEXITY_API_KEY not set")
    return key


@pytest.fixture
def anthropic_api_key():
    """Get Anthropic API key for tests."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return key


@pytest.fixture
def openrouter_api_key():
    """Get OpenRouter API key for tests."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")
    return key


@pytest.fixture
def openai_api_key():
    """Get OpenAI API key for tests."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    return key


@pytest.fixture
def temp_python_file(tmp_path):
    """Create a temporary Python file for testing."""
    test_file = tmp_path / "test_code.py"
    test_file.write_text('''
def add(a, b):
    """Add two numbers."""
    return a + b

def greet(name):
    """Greet someone."""
    return f"Hello, {name}!"

class Calculator:
    """Simple calculator class."""

    def multiply(self, a, b):
        return a * b
''')
    return test_file


@pytest.fixture
def temp_markdown_file(tmp_path):
    """Create a temporary Markdown file for testing."""
    test_file = tmp_path / "test_doc.md"
    test_file.write_text("""# Test Documentation

This is a test document for knowledge search.

## Features

- Feature one
- Feature two
- Feature three

## Installation

Run `pip install test-package` to install.

## Configuration

Set the `API_KEY` environment variable.
""")
    return test_file


@pytest.fixture
def temp_docs_dir(tmp_path):
    """Create a temporary documentation directory."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    (docs_dir / "getting-started.md").write_text("""# Getting Started

Welcome to the documentation.

## Quick Start

1. Install the package
2. Configure settings
3. Run the application
""")

    (docs_dir / "api-reference.md").write_text("""# API Reference

## Endpoints

### GET /users

Returns a list of users.

### POST /users

Creates a new user.
""")

    (docs_dir / "configuration.md").write_text("""# Configuration

## Environment Variables

- `DATABASE_URL`: Database connection string
- `API_KEY`: API authentication key
- `DEBUG`: Enable debug mode
""")

    return docs_dir


@pytest.fixture
def sample_code():
    """Sample Python code for testing."""
    return '''
def fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

def is_prime(n: int) -> bool:
    """Check if a number is prime."""
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True
'''


@pytest.fixture
def sample_test_output():
    """Sample test output for judge testing."""
    return """
PASSED tests/test_math.py::test_addition
PASSED tests/test_math.py::test_subtraction
PASSED tests/test_math.py::test_multiplication
FAILED tests/test_math.py::test_division - AssertionError: Expected 2.5, got 2
PASSED tests/test_string.py::test_concat

==================== 4 passed, 1 failed ====================
"""


@pytest.fixture
def temp_image_url():
    """A public test image URL."""
    return "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png"


@pytest.fixture
def temp_telegram_history(tmp_path):
    """Create temporary Telegram history files for testing."""
    history_dir = tmp_path / "telegram_history"
    history_dir.mkdir()

    import json

    messages = [
        {
            "id": 1,
            "chat_id": "test_group_1",
            "sender": "user1",
            "text": "Hello everyone!",
            "date": "2024-01-15T10:00:00",
        },
        {
            "id": 2,
            "chat_id": "test_group_1",
            "sender": "user2",
            "text": "Hi! How are you doing?",
            "date": "2024-01-15T10:05:00",
        },
        {
            "id": 3,
            "chat_id": "test_group_2",
            "sender": "user1",
            "text": "This is a test message about Python programming",
            "date": "2024-01-15T11:00:00",
        },
        {
            "id": 4,
            "chat_id": "test_group_1",
            "sender": "user3",
            "text": "Let's discuss the project deployment",
            "date": "2024-01-15T12:00:00",
        },
    ]

    history_file = history_dir / "messages.json"
    history_file.write_text(json.dumps(messages, indent=2))

    return history_dir
