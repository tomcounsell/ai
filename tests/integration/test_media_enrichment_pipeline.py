"""Integration test for sdlc-1297 + sdlc-1322 follow-up (sdlc-1344).

Stitches a TelegramMessage record (representing a bridge-side download) through
``enrich_message`` (worker-side AI) without standing up a real Telegram client.
Verifies that the agent receives ``[User sent an image]\\nImage description: ...``
when the bridge has populated ``media_local_path``, and that the failure path
(``media_download_error`` set, ``media_local_path=None``) gracefully returns the
bare caption with the expected log lines.

The size-aware retry tests (``test_bridge_retries_slow_download_once``,
``test_bridge_gives_up_after_retry``) exercise the bridge's
``_download_media_with_retry`` wrapper end-to-end through a TelegramMessage
record so we know the persisted ``media_local_path`` / ``media_download_error``
contract matches what the worker reads in ``enrich_message``.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.sdlc]


@pytest.fixture
def media_fixture(tmp_path: Path) -> Path:
    """Copy the repo PNG fixture to a unique path under the worktree's
    data/media/ so the test mirrors production layout."""
    src = Path("tests/fixtures/sample.png")
    assert src.exists(), f"missing fixture: {src.resolve()}"
    dst_dir = Path("data/media")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"photo_test_{uuid.uuid4().hex}.png"
    shutil.copy(src, dst)
    yield dst.resolve()
    try:
        dst.unlink()
    except FileNotFoundError:
        pass


@pytest.mark.asyncio
async def test_media_enrichment_happy_path(media_fixture: Path):
    """Bridge persisted a downloaded photo path on TelegramMessage; worker calls
    enrich_message and the resulting text begins with [User sent an image]."""
    from models.telegram import TelegramMessage

    tm = TelegramMessage.create(
        chat_id="test-1297-happy",
        message_id=1,
        direction="in",
        sender="tester",
        content="can you see this",
        timestamp=0.0,
        message_type="photo",
        project_key="test-media-1297",
        has_media=True,
        media_type="photo",
        media_local_path=str(media_fixture),
    )
    try:
        from bridge.enrichment import enrich_message

        fake_desc = (
            "[User sent an image]\nImage description: a tiny test image of nothing in particular."
        )
        with patch(
            "bridge.media.process_downloaded_media",
            new_callable=AsyncMock,
            return_value=(fake_desc, [media_fixture]),
        ):
            enriched = await enrich_message(
                message_text="can you see this",
                telegram_message=tm,
            )

        assert enriched.startswith("[User sent an image]")
        assert "Image description: " in enriched
        # Original caption preserved
        assert "can you see this" in enriched
    finally:
        tm.delete()


@pytest.mark.asyncio
async def test_media_enrichment_failure_path(caplog):
    """Bridge-side download failed: media_local_path is None and
    media_download_error is set. Worker logs WARNING + summary
    media=skipped:download_failed, agent receives bare caption."""
    from models.telegram import TelegramMessage

    tm = TelegramMessage.create(
        chat_id="test-1297-fail",
        message_id=2,
        direction="in",
        sender="tester",
        content="caption only",
        timestamp=0.0,
        message_type="photo",
        project_key="test-media-1297",
        has_media=True,
        media_type="photo",
        media_local_path=None,
        media_download_error="simulated",
    )
    try:
        from bridge.enrichment import enrich_message

        with caplog.at_level("INFO"):
            enriched = await enrich_message(
                message_text="caption only",
                telegram_message=tm,
            )

        assert enriched == "caption only"
        assert any("media download failed at intake" in r.message for r in caplog.records)
        assert any("media=skipped:download_failed" in r.message for r in caplog.records)
    finally:
        tm.delete()


# =============================================================================
# Size-aware retry path (sdlc-1322 follow-up, issue #1344)
# =============================================================================


@pytest.mark.asyncio
async def test_bridge_retries_slow_download_once(media_fixture: Path):
    """First download attempt times out; the bridge retries once with a 2x leash
    and the second attempt succeeds.

    Asserts the persistence-shape the worker reads:
    - ``media_local_path`` is set to the resolved absolute path.
    - ``media_download_error`` is None (transient failure swallowed by retry).
    """
    from bridge import telegram_bridge

    fake_message = MagicMock()
    fake_message.file = SimpleNamespace(size=10 * 1024 * 1024)  # 10MB -> 15s/30s
    fake_message.id = 4242
    fake_client = MagicMock()

    call_count = {"n": 0}

    async def flaky_wait_for(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError()
        return media_fixture

    with patch.object(asyncio, "wait_for", new=AsyncMock(side_effect=flaky_wait_for)):
        local_path, error = await telegram_bridge._download_media_with_retry(
            fake_client,
            fake_message,
            prefix="photo",
        )

    assert local_path == media_fixture, "second attempt should return the downloaded path"
    assert error is None, "transient timeout cleared by successful retry"
    assert call_count["n"] == 2, "retry helper must invoke wait_for twice"

    # End-to-end persistence shape: this is what the bridge writes to the record
    # and what the worker subsequently reads.
    from models.telegram import TelegramMessage

    tm = TelegramMessage.create(
        chat_id="test-1344-retry-ok",
        message_id=4242,
        direction="in",
        sender="tester",
        content="late but here",
        timestamp=0.0,
        message_type="photo",
        project_key="test-media-1344",
        has_media=True,
        media_type="photo",
        media_local_path=str(local_path.resolve()) if local_path else None,
        media_download_error=error,
    )
    try:
        assert tm.media_local_path == str(media_fixture.resolve())
        assert tm.media_download_error is None
    finally:
        tm.delete()


@pytest.mark.asyncio
async def test_bridge_gives_up_after_retry():
    """Both download attempts time out; the bridge persists
    ``media_download_error="timeout after Xs (retried)"`` so the worker can
    distinguish a terminal too-big-for-our-budget failure from a first-attempt
    fluke."""
    from bridge import telegram_bridge

    fake_message = MagicMock()
    # 30MB -> first timeout = 35s, second = 70s (still under 120s cap)
    fake_message.file = SimpleNamespace(size=30 * 1024 * 1024)
    fake_message.id = 9001
    fake_client = MagicMock()

    with patch.object(asyncio, "wait_for", new=AsyncMock(side_effect=TimeoutError())):
        local_path, error = await telegram_bridge._download_media_with_retry(
            fake_client,
            fake_message,
            prefix="document",
        )

    assert local_path is None
    assert error is not None
    assert "(retried)" in error, f"terminal failure must carry the (retried) suffix: {error!r}"
    assert error.startswith("timeout after "), f"unexpected error shape: {error!r}"

    # End-to-end persistence shape — the worker keys off this exact string.
    from models.telegram import TelegramMessage

    tm = TelegramMessage.create(
        chat_id="test-1344-retry-fail",
        message_id=9001,
        direction="in",
        sender="tester",
        content="too big",
        timestamp=0.0,
        message_type="document",
        project_key="test-media-1344",
        has_media=True,
        media_type="document",
        media_local_path=None,
        media_download_error=error,
    )
    try:
        assert tm.media_local_path is None
        assert tm.media_download_error == error
        assert "(retried)" in tm.media_download_error
    finally:
        tm.delete()
