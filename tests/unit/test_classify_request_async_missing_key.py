"""Regression tests for the missing-API-key path in classify_request_async.

Covers #1899: a missing Anthropic API key should degrade quietly (WARNING log,
message-preserving sentinel default) instead of raising a ValueError that
surfaces as an ERROR-level Sentry event.
"""

import logging
from unittest.mock import patch

import pytest

from tools.classifier import classify_request_async


@pytest.mark.asyncio
async def test_missing_key_returns_sentinel_default_without_raising(caplog):
    """Missing key returns a message-preserving default instead of raising."""
    with patch("tools.classifier.get_anthropic_api_key", return_value=None):
        with caplog.at_level(logging.WARNING, logger="tools.classifier"):
            result = await classify_request_async("Fix the login bug")

    assert result["type"] is None
    assert result["confidence"] == 0.0
    assert "reason" in result


@pytest.mark.asyncio
async def test_missing_key_logs_warning_not_error(caplog):
    """The missing-key path logs at WARNING, never ERROR."""
    with patch("tools.classifier.get_anthropic_api_key", return_value=None):
        with caplog.at_level(logging.DEBUG, logger="tools.classifier"):
            await classify_request_async("Fix the login bug")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert not error_records, f"Expected no ERROR logs, got: {[r.message for r in error_records]}"
    assert warning_records, "Expected at least one WARNING log for the missing-key path"


@pytest.mark.asyncio
async def test_missing_key_empty_string_also_handled(caplog):
    """An empty-string key (legacy resolver behavior) is treated the same as absent."""
    with patch("tools.classifier.get_anthropic_api_key", return_value=""):
        with caplog.at_level(logging.WARNING, logger="tools.classifier"):
            result = await classify_request_async("Fix the login bug")

    assert result["type"] is None
    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_real_api_error_still_logs_error(caplog):
    """A genuine API failure (key present) keeps ERROR-level visibility.

    The missing-key downgrade must not swallow real failures: with a valid key,
    an exception from the Anthropic client still hits the outer handler and logs
    at ERROR (feeding Sentry), then re-raises. This is the counterpart to the
    quiet missing-key path (#1899).
    """
    with patch("tools.classifier.get_anthropic_api_key", return_value="sk-ant-real"):
        with patch("tools.classifier.anthropic_slot", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.DEBUG, logger="tools.classifier"):
                with pytest.raises(RuntimeError):
                    await classify_request_async("Fix the login bug")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Expected an ERROR log for a genuine API failure"
    assert any("Classification failed (async)" in r.message for r in error_records)
