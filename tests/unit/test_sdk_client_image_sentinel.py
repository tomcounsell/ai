"""
Unit tests for the image-dimension sentinel fallback in get_response_via_harness().

The sentinel (IMAGE_DIMENSION_SENTINEL) detects Claude Code's exit-code-0 error:
  "An image in the conversation exceeds the dimension limit for many-image requests"

These tests mock _run_harness_subprocess so they run instantly without a real
claude CLI binary, API key, or network.
"""

import pytest


@pytest.mark.asyncio
async def test_sentinel_fires_full_context_message(monkeypatch):
    """When --resume returns the sentinel string (exit 0), full_context_message is used."""
    from agent.sdk_client import IMAGE_DIMENSION_SENTINEL, get_response_via_harness

    call_log: list[list[str]] = []

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        call_log.append(cmd)
        if "--resume" in cmd:
            # First call: simulate the image-dimension error with exit code 0
            return (
                f"An image in the conversation {IMAGE_DIMENSION_SENTINEL} (2000px).",
                None,
                0,
            )
        # Second call (fallback): normal response
        return ("Fallback response from full context", "new-session-uuid", 0)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)

    result = await get_response_via_harness(
        message="hello",
        working_dir="/tmp",
        prior_uuid="12345678-1234-1234-1234-123456789abc",
        full_context_message="Full context: system state + hello",
    )

    # Should have been called twice: first --resume, then fallback
    assert len(call_log) == 2, f"Expected 2 subprocess calls, got {len(call_log)}"
    assert "--resume" in call_log[0], "First call should use --resume"
    assert "--resume" not in call_log[1], "Second call should NOT use --resume"
    assert result == "Fallback response from full context"


@pytest.mark.asyncio
async def test_sentinel_no_full_context_message(monkeypatch):
    """When sentinel fires and full_context_message is None, plain-language error returned."""
    from agent.sdk_client import IMAGE_DIMENSION_SENTINEL, get_response_via_harness

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        return (
            f"An image in the conversation {IMAGE_DIMENSION_SENTINEL} (2000px).",
            None,
            0,
        )

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)

    result = await get_response_via_harness(
        message="hello",
        working_dir="/tmp",
        prior_uuid="12345678-1234-1234-1234-123456789abc",
        full_context_message=None,
    )

    # Raw sentinel text must NOT reach the caller
    assert IMAGE_DIMENSION_SENTINEL not in result, (
        "Raw sentinel string should not be returned to the caller"
    )
    # Must be a human-readable error
    assert "new thread" in result.lower() or "large" in result.lower(), (
        f"Expected plain-language error, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_sentinel_does_not_fire_on_first_turn(monkeypatch):
    """Without prior_uuid, a response containing the sentinel text passes through unchanged."""
    from agent.sdk_client import IMAGE_DIMENSION_SENTINEL, get_response_via_harness

    sentinel_text = f"An image in the conversation {IMAGE_DIMENSION_SENTINEL} (2000px)."
    call_count = 0

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        nonlocal call_count
        call_count += 1
        return (sentinel_text, None, 0)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)

    result = await get_response_via_harness(
        message="hello",
        working_dir="/tmp",
        prior_uuid=None,  # No prior UUID — not a resume path
        full_context_message="full context",
    )

    # Without prior_uuid the sentinel must not trigger a retry
    assert call_count == 1, "Should only call subprocess once when not resuming"
    assert result == sentinel_text, "Non-resume result should be returned as-is"


@pytest.mark.asyncio
async def test_sentinel_does_not_fire_on_empty_result(monkeypatch):
    """Sentinel check must not fire when result_text is empty or None."""
    call_count = 0

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        nonlocal call_count
        call_count += 1
        return ("", None, 0)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)

    from agent.sdk_client import get_response_via_harness

    await get_response_via_harness(
        message="hello",
        working_dir="/tmp",
        prior_uuid="12345678-1234-1234-1234-123456789abc",
        full_context_message="full context",
    )

    assert call_count == 1, "Should only call subprocess once for empty result"


@pytest.mark.asyncio
async def test_sentinel_does_not_fire_on_normal_resume(monkeypatch):
    """A normal successful --resume must not trigger the sentinel fallback."""
    normal_response = "Here is the summary you requested."
    call_count = 0

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        nonlocal call_count
        call_count += 1
        return (normal_response, "new-uuid", 0)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)

    from agent.sdk_client import get_response_via_harness

    result = await get_response_via_harness(
        message="hello",
        working_dir="/tmp",
        prior_uuid="12345678-1234-1234-1234-123456789abc",
        full_context_message="full context",
    )

    assert call_count == 1, "Normal resume should only call subprocess once"
    assert result == normal_response
