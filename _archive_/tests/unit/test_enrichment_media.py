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
async def test_enrichment_self_heals_orphan_media_file(tmp_path, caplog, monkeypatch):
    """sdlc-1330: when media_local_path is None and media_download_error is None
    AND a unique file matching *_{message_id}.* exists in MEDIA_DIR, the worker
    adopts it and runs AI enrichment instead of dropping the message."""
    import bridge.enrichment as enrichment_mod
    import bridge.media as media_mod

    # Redirect MEDIA_DIR to the test tmp_path
    monkeypatch.setattr(media_mod, "MEDIA_DIR", tmp_path)

    orphan = tmp_path / "voice_20260508_111815_9730.ogg"
    orphan.write_bytes(b"OggS\x00")  # minimal OGG header

    tm = SimpleNamespace(
        has_media=True,
        media_type="voice",
        media_local_path=None,
        media_download_error=None,
        message_id=9730,
    )

    fake_transcription = "Hello from the orphan file"
    with patch(
        "bridge.media.process_downloaded_media",
        new_callable=AsyncMock,
        return_value=(fake_transcription, [orphan]),
    ) as proc:
        with caplog.at_level("INFO"):
            result = await enrichment_mod.enrich_message(
                message_text="--file attachment only--",
                telegram_message=tm,
            )

    assert "Hello from the orphan file" in result
    assert "--file attachment only--" not in result or result.startswith(fake_transcription)
    proc.assert_called_once()
    assert any("self-heal: recovered orphan media file" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_enrichment_skips_self_heal_on_multiple_matches(tmp_path, caplog, monkeypatch):
    """sdlc-1330: when two files match the same message_id, skip self-heal to
    avoid adopting the wrong file. The existing 'legacy record?' warning still
    fires."""
    import bridge.media as media_mod

    monkeypatch.setattr(media_mod, "MEDIA_DIR", tmp_path)

    (tmp_path / "voice_20260508_111815_9730.ogg").write_bytes(b"OggS\x00")
    (tmp_path / "voice_20260509_120000_9730.ogg").write_bytes(b"OggS\x00")

    tm = SimpleNamespace(
        has_media=True,
        media_type="voice",
        media_local_path=None,
        media_download_error=None,
        message_id=9730,
    )

    from bridge.enrichment import enrich_message

    with caplog.at_level("WARNING"):
        result = await enrich_message(
            message_text="caption",
            telegram_message=tm,
        )

    assert result == "caption"
    assert any("multiple matches" in r.message for r in caplog.records)
    assert any("has_media=True but media_local_path is unset" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_enrichment_skips_self_heal_when_message_id_missing(tmp_path, caplog, monkeypatch):
    """sdlc-1330: with message_id unset, self-heal can't run and we fall
    through to the existing 'legacy record?' warning."""
    import bridge.media as media_mod

    monkeypatch.setattr(media_mod, "MEDIA_DIR", tmp_path)

    tm = SimpleNamespace(
        has_media=True,
        media_type="voice",
        media_local_path=None,
        media_download_error=None,
        message_id=None,
    )

    from bridge.enrichment import enrich_message

    with caplog.at_level("WARNING"):
        result = await enrich_message(
            message_text="caption",
            telegram_message=tm,
        )

    assert result == "caption"
    assert any("has_media=True but media_local_path is unset" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_enrichment_skips_self_heal_when_no_matches(tmp_path, caplog, monkeypatch):
    """sdlc-1330: with message_id set but MEDIA_DIR empty, fall through to the
    existing 'legacy record?' warning."""
    import bridge.media as media_mod

    monkeypatch.setattr(media_mod, "MEDIA_DIR", tmp_path)

    tm = SimpleNamespace(
        has_media=True,
        media_type="voice",
        media_local_path=None,
        media_download_error=None,
        message_id=9730,
    )

    from bridge.enrichment import enrich_message

    with caplog.at_level("WARNING"):
        result = await enrich_message(
            message_text="caption",
            telegram_message=tm,
        )

    assert result == "caption"
    assert any("has_media=True but media_local_path is unset" in r.message for r in caplog.records)


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
