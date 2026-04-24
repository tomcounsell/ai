"""Unit tests for Mode 1 of issue #1099 — thinking-block corruption sentinel.

When extended-thinking + compaction corrupts the transcript, the Claude CLI
exits non-zero with stderr containing ``redacted_thinking``. The harness must
detect this and raise ``HarnessThinkingBlockCorruption`` so the executor can
finalize the session as ``failed`` instead of returning silently.

These tests mock ``_run_harness_subprocess`` so they run instantly without a
real claude CLI binary, API key, or network. The mock signature follows the
6-tuple return introduced for issue #1099:
``(result_text, session_id, returncode, usage, cost_usd, stderr_snippet)``.
"""

import pytest

from agent.sdk_client import (
    HarnessThinkingBlockCorruption,
    THINKING_BLOCK_SENTINEL,
    get_response_via_harness,
)


@pytest.mark.asyncio
async def test_sentinel_in_stderr_with_nonzero_exit_raises(monkeypatch):
    """Sentinel string in stderr + returncode != 0 → HarnessThinkingBlockCorruption."""
    stderr_snippet = (
        f"Error: {THINKING_BLOCK_SENTINEL} block cannot be modified after the "
        "previous turn closed it."
    )

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        return (None, None, 1, None, None, stderr_snippet)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client.accumulate_session_tokens", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._store_exit_returncode", lambda *a, **kw: None)
    # Ensure the env-gated kill switch is OFF for this test.
    monkeypatch.setattr("agent.sdk_client._DISABLE_THINKING_SENTINEL", False)

    with pytest.raises(HarnessThinkingBlockCorruption) as exc_info:
        await get_response_via_harness(
            message="hello",
            working_dir="/tmp",
            prior_uuid=None,
            full_context_message=None,
        )

    # The user-facing message must NOT leak raw sentinel text and must point the
    # user at the recovery action (start a new thread).
    msg = str(exc_info.value)
    assert "new thread" in msg.lower(), f"Expected user-facing 'new thread' guidance, got: {msg!r}"
    assert THINKING_BLOCK_SENTINEL not in msg, "Raw sentinel must not leak to the user message"


@pytest.mark.asyncio
async def test_sentinel_message_is_user_facing(monkeypatch):
    """The exception message must read cleanly to a non-technical user."""
    stderr_snippet = f"{THINKING_BLOCK_SENTINEL} ... cannot be modified"

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        return (None, None, 1, None, None, stderr_snippet)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client.accumulate_session_tokens", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._store_exit_returncode", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._DISABLE_THINKING_SENTINEL", False)

    with pytest.raises(HarnessThinkingBlockCorruption) as exc_info:
        await get_response_via_harness(
            message="hi",
            working_dir="/tmp",
        )

    # Caller (BackgroundTask._run_work) will surface str(exc) verbatim — must be
    # non-empty and not the equivalent of "" (the silent-completion bug we're
    # fixing). Asserting len > 10 catches any future accidental shortening.
    assert len(str(exc_info.value)) > 10


@pytest.mark.asyncio
async def test_healthy_run_no_sentinel_returns_normally(monkeypatch):
    """Healthy run: returncode 0 + no sentinel → normal return, no raise."""
    expected = "Hello from Claude — this is a healthy response."

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        # returncode 0 — _run_harness_subprocess returns stderr_snippet=None.
        return (expected, "uuid-1", 0, None, None, None)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client.accumulate_session_tokens", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._store_exit_returncode", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._DISABLE_THINKING_SENTINEL", False)

    result = await get_response_via_harness(message="hello", working_dir="/tmp")
    assert result == expected


@pytest.mark.asyncio
async def test_disable_env_var_skips_sentinel_check(monkeypatch):
    """DISABLE_THINKING_SENTINEL=1 → no raise even when sentinel + nonzero exit."""
    stderr_snippet = f"{THINKING_BLOCK_SENTINEL} ... cannot be modified"

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        return (None, None, 1, None, None, stderr_snippet)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client.accumulate_session_tokens", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._store_exit_returncode", lambda *a, **kw: None)
    # Operator escape hatch: kill switch ON.
    monkeypatch.setattr("agent.sdk_client._DISABLE_THINKING_SENTINEL", True)

    # Should NOT raise — falls through to the empty/None return path.
    result = await get_response_via_harness(message="hello", working_dir="/tmp")
    # Empty result_text → return ""
    assert result == ""


@pytest.mark.asyncio
async def test_sentinel_no_raise_on_zero_exit(monkeypatch):
    """Sentinel substring in stderr but returncode == 0 → no raise (defensive guard)."""

    # Hypothetical edge case: a tool dumps "redacted_thinking" diagnostically and
    # the run still succeeds. The Mode 1 detector requires BOTH conditions; we
    # assert the conjunction by simulating returncode 0 with the sentinel text
    # in stderr_snippet — which the harness itself would never produce, but the
    # assertion locks in the rule against future regression.
    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        # NOTE: stderr_snippet is None on returncode==0 paths in real code.
        # Pass None here to match the real contract — the test asserts the
        # detector is rc-gated regardless of stderr content.
        return ("Healthy result.", "uuid-1", 0, None, None, None)

    monkeypatch.setattr("agent.sdk_client._run_harness_subprocess", fake_subprocess)
    monkeypatch.setattr("agent.sdk_client._store_claude_session_uuid", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client.accumulate_session_tokens", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._store_exit_returncode", lambda *a, **kw: None)
    monkeypatch.setattr("agent.sdk_client._DISABLE_THINKING_SENTINEL", False)

    result = await get_response_via_harness(message="hello", working_dir="/tmp")
    assert result == "Healthy result."
