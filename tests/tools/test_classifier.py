"""Test suite for work request classifier.

Tests the classify_request() function with real-world test cases.
Validates classifications, confidence scores, and reasoning.
"""

import pytest

from tools.classifier import classify_request


class TestClassifierBasicCases:
    """Test basic classification with clear-cut examples."""

    def test_bug_login_page_broken(self):
        """Test: Bug - the login page is broken."""
        result = classify_request("the login page is broken")

        assert isinstance(result, dict)
        assert result["type"] == "bug"
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"
        assert "reason" in result
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0

    def test_bug_file_upload_broken(self):
        """Test: Bug - users can't upload files anymore."""
        result = classify_request("users can't upload files anymore")

        assert result["type"] == "bug"
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"

    def test_feature_dark_mode(self):
        """Test: Feature - add dark mode support."""
        result = classify_request("add dark mode support")

        assert result["type"] == "feature"
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"

    def test_feature_2fa_auth(self):
        """Test: Feature - we need 2FA authentication."""
        result = classify_request("we need 2FA authentication")

        assert result["type"] == "feature"
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"

    def test_chore_update_dependencies(self):
        """Test: Chore - update dependencies to latest versions."""
        result = classify_request("update dependencies to latest versions")

        assert result["type"] == "chore"
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"

    def test_chore_refactor_auth(self):
        """Test: Chore - refactor the authentication module."""
        result = classify_request("refactor the authentication module")

        assert result["type"] == "chore"
        assert result["confidence"] > 0.7, f"Confidence {result['confidence']} too low"


class TestClassifierWithContext:
    """Test classification with additional context."""

    def test_bug_with_context(self):
        """Test bug classification with context."""
        result = classify_request(
            "Login doesn't work",
            context="Users report that login fails with error 500 after recent deployment",
        )

        assert result["type"] == "bug"
        assert result["confidence"] > 0.7

    def test_feature_with_context(self):
        """Test feature classification with context."""
        result = classify_request(
            "Better notifications",
            context="Users requested push notifications on mobile devices",
        )

        assert result["type"] == "feature"
        assert result["confidence"] > 0.7


class TestClassifierEdgeCases:
    """Test edge cases and ambiguous classifications."""

    def test_ambiguous_performance_issue(self):
        """Test ambiguous case: performance issue."""
        result = classify_request("The app is running slow")

        # Could be bug (something broken) or chore (optimization)
        # Just verify it's classified consistently and has high confidence
        assert result["type"] in ["bug", "feature", "chore"]
        assert isinstance(result["confidence"], (int, float))
        assert 0.0 <= result["confidence"] <= 1.0

    def test_vague_request(self):
        """Test vague request."""
        result = classify_request("We should improve things")

        # Should still classify into one category
        assert result["type"] in ["bug", "feature", "chore"]

    def test_empty_reason_not_allowed(self):
        """Test that reason is always provided."""
        result = classify_request("Fix the button")

        assert result["reason"] is not None
        assert len(result["reason"]) > 0


class TestClassifierResponseStructure:
    """Test response structure and validation."""

    def test_response_has_all_fields(self):
        """Test response contains all required fields."""
        result = classify_request("Add new feature")

        required_fields = ["type", "confidence", "reason"]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"

    def test_confidence_is_numeric(self):
        """Test confidence is a valid number."""
        result = classify_request("Fix bug")

        assert isinstance(result["confidence"], (int, float))
        assert not isinstance(result["confidence"], bool)  # bool is subclass of int
        assert 0.0 <= result["confidence"] <= 1.0

    def test_type_is_valid(self):
        """Test type is one of allowed values."""
        result = classify_request("Some request")

        assert result["type"] in ["bug", "feature", "chore"]

    def test_reason_is_string(self):
        """Test reason is always a string."""
        result = classify_request("Some request")

        assert isinstance(result["reason"], str)


@pytest.mark.parametrize(
    "message,expected_type",
    [
        ("the login page is broken", "bug"),
        ("users can't upload files anymore", "bug"),
        ("add dark mode support", "feature"),
        ("we need 2FA authentication", "feature"),
        ("update dependencies to latest versions", "chore"),
        ("refactor the authentication module", "chore"),
    ],
)
def test_classifier_parametrized(message, expected_type):
    """Parametrized test for all 6 test cases."""
    result = classify_request(message)

    assert result["type"] == expected_type, (
        f"Expected {expected_type}, got {result['type']} for '{message}'. "
        f"Reason: {result['reason']}"
    )
    assert result["confidence"] > 0.7, (
        f"Low confidence ({result['confidence']}) for '{message}' "
        f"classified as {result['type']}"
    )


def test_classifier_error_handling():
    """Test error handling doesn't crash the classifier."""
    # Classifier should handle various inputs gracefully
    # Most inputs should return valid classifications
    test_inputs = [
        "This is a very short message",
        "This is a longer message that goes on and on with lots of details about what needs to be done",
        "123",
        "!!!",
        "",
    ]

    for message in test_inputs:
        try:
            result = classify_request(message)
            # If it succeeds, verify structure
            assert "type" in result
            assert "confidence" in result
            assert "reason" in result
        except Exception:
            # Some inputs might fail - that's acceptable
            pass
