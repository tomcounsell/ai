"""Unit tests for sdlc-1297: bridge-side download + worker-side AI media enrichment.

Covers ``bridge.enrichment.enrich_message`` reading ``media_local_path`` off a
``TelegramMessage``-shaped object and dispatching to ``process_downloaded_media``,
without ever touching Telethon.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.sdlc


@pytest.mark.asyncio
async def test_enrich_message_with_media_local_path_invokes_worker_path(tmp_path):
    """Happy path: TelegramMessage carries an absolute media_local_path and
    enrich_message returns text starting with [User sent an image]."""
    from bridge.enrichment import enrich_message

    img = tmp_path / "photo.png"
    # 1x1 PNG header bytes — actual content is irrelevant because we mock
    # process_downloaded_media at the dispatch boundary.
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    tm = SimpleNamespace(
        has_media=True,
        media_type="photo",
        media_local_path=str(img.resolve()),
        media_download_error=None,
    )

    fake_description = "[User sent an image]\nImage description: a tiny test image."
    with patch(
        "bridge.media.process_downloaded_media",
        new_callable=AsyncMock,
        return_value=(fake_description, [img]),
    ) as proc:
        result = await enrich_message(
            message_text="can you see this",
            telegram_message=tm,
        )

    assert result.startswith("[User sent an image]")
    assert "can you see this" in result
    proc.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_message_skips_when_download_failed(caplog):
    """Failure path: bridge persisted a download error; worker logs WARNING and
    returns the bare caption, summary contains media=skipped:download_failed."""
    from bridge.enrichment import enrich_message

    tm = SimpleNamespace(
        has_media=True,
        media_type="photo",
        media_local_path=None,
        media_download_error="timeout after 10s",
    )

    with caplog.at_level("INFO"):
        result = await enrich_message(
            message_text="here is a picture",
            telegram_message=tm,
        )

    assert result == "here is a picture"
    assert any("media download failed at intake" in r.message for r in caplog.records)
    assert any("media=skipped:download_failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_enrich_message_skips_when_file_unreadable(tmp_path, caplog):
    """File at media_local_path doesn't exist: WARNING, summary
    media=skipped:file_unreadable, bare caption returned."""
    from bridge.enrichment import enrich_message

    missing = tmp_path / "does_not_exist.png"
    tm = SimpleNamespace(
        has_media=True,
        media_type="photo",
        media_local_path=str(missing),
        media_download_error=None,
    )

    with caplog.at_level("INFO"):
        result = await enrich_message(
            message_text="caption",
            telegram_message=tm,
        )

    assert result == "caption"
    assert any("not readable" in r.message for r in caplog.records)
    assert any("media=skipped:file_unreadable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_enrich_message_no_telegram_message_record_is_a_normal_path(caplog):
    """Non-Telegram / manual-test sessions pass telegram_message=None:
    no warning, no error, returns text unchanged with media=skipped:no_record."""
    from bridge.enrichment import enrich_message

    with caplog.at_level("WARNING"):
        result = await enrich_message(
            message_text="hello there",
            telegram_message=None,
        )

    assert result == "hello there"
    # Should NOT emit a warning — this is a normal path
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not warnings, f"unexpected warnings: {[r.message for r in warnings]}"


@pytest.mark.asyncio
async def test_enrich_message_no_media_summary_says_no(caplog):
    """has_media=False → summary line shows media=no, idempotent vs raw text."""
    from bridge.enrichment import enrich_message

    tm = SimpleNamespace(
        has_media=False,
        media_type=None,
        media_local_path=None,
        media_download_error=None,
    )

    with caplog.at_level("INFO"):
        result = await enrich_message(
            message_text="just text",
            telegram_message=tm,
        )

    assert result == "just text"
    assert any("media=no," in r.message for r in caplog.records)
