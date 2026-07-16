"""Tests for size-aware media download timeout + retry (issue #1322).

Covers:
- `compute_media_timeout(size_bytes)`: pure helper. None / 0 / negative
  fall back to baseline (10s); positive sizes scale; cap at 120s.
- Retry path emits the `(retried)` suffix on terminal failure.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridge.media import compute_media_timeout

# ---------------------------------------------------------------------------
# compute_media_timeout: pure helper
# ---------------------------------------------------------------------------


class TestComputeMediaTimeout:
    """The formula: max(10.0, min(120.0, 5.0 + size_bytes / (1024*1024)))
    with None / non-positive falling back to 10.0."""

    def test_none_returns_baseline(self):
        assert compute_media_timeout(None) == 10.0

    def test_zero_returns_baseline(self):
        # Zero-byte file shouldn't get less than the floor.
        assert compute_media_timeout(0) == 10.0

    def test_negative_returns_baseline(self):
        # Defensive: negative is nonsense -> baseline.
        assert compute_media_timeout(-1) == 10.0
        assert compute_media_timeout(-1024 * 1024) == 10.0

    def test_small_file_returns_baseline_floor(self):
        # 1MB -> 5 + 1 = 6, floored to 10.
        assert compute_media_timeout(1 * 1024 * 1024) == 10.0

    def test_medium_file_scales(self):
        # 10MB -> 5 + 10 = 15.
        assert compute_media_timeout(10 * 1024 * 1024) == 15.0

    def test_large_file_scales(self):
        # 50MB -> 5 + 50 = 55.
        assert compute_media_timeout(50 * 1024 * 1024) == 55.0

    def test_cap_at_120s(self):
        # 200MB -> would be 205, but capped at 120.
        assert compute_media_timeout(200 * 1024 * 1024) == 120.0

    def test_huge_file_still_capped(self):
        # 1GB -> still 120.
        assert compute_media_timeout(1024 * 1024 * 1024) == 120.0


# ---------------------------------------------------------------------------
# Retry path: error string carries `(retried)` after both attempts time out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_emits_retried_suffix_on_terminal_timeout():
    """Both download attempts time out -> `media_download_error` set on the
    record contains "(retried)" so downstream can distinguish first-attempt
    failures from terminal ones.

    We exercise the actual code path in `telegram_bridge` by importing the
    module-level coroutine and mocking the wait_for to time out twice.
    """
    # Import lazily — we only need the symbol that wraps the retry logic.
    from bridge import telegram_bridge

    # Sanity check: the helper is wired to bridge code.
    assert hasattr(telegram_bridge, "_download_media_with_retry"), (
        "telegram_bridge must expose `_download_media_with_retry` "
        "for the size-aware retry path (issue #1322)."
    )

    fake_message = MagicMock()
    # message.file.size for a 30MB file -> first timeout = 35s, second = 70s
    fake_message.file = SimpleNamespace(size=30 * 1024 * 1024)
    fake_message.id = 42

    fake_client = MagicMock()

    # Patch wait_for to time out on every attempt — that's the only seam
    # the retry helper relies on. The fake must close the coroutine it is
    # handed (the eagerly-created download_media(...) arg); otherwise it leaks
    # a "coroutine 'download_media' was never awaited" RuntimeWarning into
    # pytest's teardown, one of the un-awaited-coroutine leaks that wedged the
    # full suite (#2118).
    async def _always_timeout(coro, *args, **kwargs):
        coro.close()
        raise TimeoutError()

    with patch.object(asyncio, "wait_for", new=_always_timeout):
        local_path, error = await telegram_bridge._download_media_with_retry(
            fake_client,
            fake_message,
            prefix="media",
        )

    assert local_path is None
    assert error is not None
    assert "(retried)" in error
    # Sanity: the second-attempt timeout should be reflected (~70s, capped at 120).
    assert "70" in error or "120" in error or "after" in error


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    """First wait_for raises TimeoutError, second returns a path -> success
    with no error string."""
    from bridge import telegram_bridge

    fake_message = MagicMock()
    fake_message.file = SimpleNamespace(size=10 * 1024 * 1024)  # 10MB
    fake_message.id = 17
    fake_client = MagicMock()

    success_path = Path("/tmp/fake_media.bin")

    call_count = {"n": 0}

    async def flaky(coro, *_args, **_kwargs):
        coro.close()  # dispose the download_media(...) coroutine (#2118)
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError()
        return success_path

    with patch.object(asyncio, "wait_for", new=flaky):
        local_path, error = await telegram_bridge._download_media_with_retry(
            fake_client,
            fake_message,
            prefix="media",
        )

    assert local_path == success_path
    assert error is None
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# sdlc-1330: silent persist branches now log WARNING
# ---------------------------------------------------------------------------


def test_persist_warns_when_record_none_string_in_source():
    """sdlc-1330: the bridge persist block must emit a WARNING when
    TelegramMessage.query.get returns None twice. We assert the exact log
    string is present in the source so the wiring can't silently regress.

    The persist block is inline in a deep event handler; a full mock-based
    test would require patching the entire Telethon stack. The Verification
    table in docs/plans/sdlc-1330.md greps for the same string."""
    src = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
    text = src.read_text()
    assert "TelegramMessage.query.get returned None" in text, (
        "Expected the sdlc-1330 WARNING string in bridge/telegram_bridge.py"
    )
    # Verify single bounded re-query is wired (two get() calls in a row inside
    # the persist try-block).
    assert text.count("TelegramMessage.query.get(stored_msg_id)") >= 2, (
        "Expected at least 2 calls to query.get(stored_msg_id) for the single-retry pattern"
    )


def test_persist_warns_when_download_returns_no_path_with_no_error_string_in_source():
    """sdlc-1330: the second silent branch (`_local_path is None AND
    _download_error is None AND message.media`) must log a WARNING. Assert
    the log string and the conjunction is wired in source."""
    src = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
    text = src.read_text()
    assert "download returned no path with no error" in text, (
        "Expected the sdlc-1330 no-path WARNING string in bridge/telegram_bridge.py"
    )
    # The conjunction guards against false positives when there's no media at
    # all (skipping persist entirely is the normal case).
    assert "_local_path is None" in text
    assert "_download_error is None" in text


@pytest.mark.asyncio
async def test_first_attempt_success_no_retry():
    """First attempt succeeds -> single call, no retry telemetry."""
    from bridge import telegram_bridge

    fake_message = MagicMock()
    fake_message.file = SimpleNamespace(size=1024)  # tiny
    fake_message.id = 99
    fake_client = MagicMock()
    success_path = Path("/tmp/ok.bin")

    call_count = {"n": 0}

    async def succeed(coro, *_args, **_kwargs):
        coro.close()  # dispose the download_media(...) coroutine (#2118)
        call_count["n"] += 1
        return success_path

    with patch.object(asyncio, "wait_for", new=succeed):
        local_path, error = await telegram_bridge._download_media_with_retry(
            fake_client,
            fake_message,
            prefix="media",
        )

    assert local_path == success_path
    assert error is None
    assert call_count["n"] == 1
