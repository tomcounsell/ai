"""
Integration tests for documentation tool.

Run with: pytest tools/documentation/tests/ -v
"""

import os

import pytest

from tools.documentation import (
    generate_docs,
    generate_docstring,
    generate_readme,
)


class TestDocumentationInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.documentation import generate_docs

        assert callable(generate_docs)

    def test_api_key_required(self):
        """Tool returns error when API keys missing."""
        original_anthropic = os.environ.get("ANTHROPIC_API_KEY")
        original_openrouter = os.environ.get("OPENROUTER_API_KEY")

        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        if "OPENROUTER_API_KEY" in os.environ:
            del os.environ["OPENROUTER_API_KEY"]

        try:
            result = generate_docs("def foo(): pass")
            assert "error" in result
        finally:
            if original_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = original_anthropic
            if original_openrouter:
                os.environ["OPENROUTER_API_KEY"] = original_openrouter


class TestDocumentationValidation:
    """Test input validation."""

    def test_empty_source(self):
        """Empty source returns error."""
        result = generate_docs("")
        assert "error" in result

    def test_whitespace_source(self):
        """Whitespace source returns error."""
        result = generate_docs("   ")
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestDocstringGeneration:
    """Test docstring generation."""

    @pytest.fixture
    def sample_function(self):
        """Sample function for testing."""
        return """
def calculate_total(items, tax_rate=0.08, discount=None):
    total = sum(item["price"] * item["quantity"] for item in items)
    if discount:
        total -= discount
    total *= (1 + tax_rate)
    return round(total, 2)
"""

    def test_google_style(self, sample_function):
        """Generate Google style docstring."""
        result = generate_docstring(sample_function, style="google")

        assert "error" not in result, f"Generation failed: {result.get('error')}"
        assert "documentation" in result
        assert (
            "Args:" in result["documentation"]
            or "Parameters:" in result["documentation"]
        )

    def test_numpy_style(self, sample_function):
        """Generate NumPy style docstring."""
        result = generate_docstring(sample_function, style="numpy")

        assert "error" not in result, f"Generation failed: {result.get('error')}"
        assert "documentation" in result

    def test_with_examples(self, sample_function):
        """Docstring includes examples."""
        result = generate_docstring(sample_function, include_examples=True)

        assert "error" not in result


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestReadmeGeneration:
    """Test README generation."""

    @pytest.fixture
    def sample_module(self):
        """Sample module for testing."""
        return '''
"""
Simple Calculator Module

A basic calculator with common operations.
"""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
'''

    def test_readme_generation(self, sample_module):
        """Generate README from module."""
        result = generate_readme(sample_module)

        assert "error" not in result, f"Generation failed: {result.get('error')}"
        assert "documentation" in result
        assert "#" in result["documentation"]  # Contains markdown headers

    def test_detail_levels(self, sample_module):
        """Different detail levels work."""
        for level in ["minimal", "standard", "comprehensive"]:
            result = generate_readme(sample_module, detail_level=level)
            assert "error" not in result, f"Failed for {level}: {result.get('error')}"


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestApiDocGeneration:
    """Test API documentation generation."""

    def test_api_docs(self):
        """Generate API documentation."""
        code = '''
class UserService:
    def get_user(self, user_id: int) -> dict:
        """Get user by ID."""
        pass

    def create_user(self, name: str, email: str) -> dict:
        """Create a new user."""
        pass
'''
        result = generate_docs(code, doc_type="api")

        assert "error" not in result, f"Generation failed: {result.get('error')}"
        assert "documentation" in result
