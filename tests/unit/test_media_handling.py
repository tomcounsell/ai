"""
Tests for media receiving and processing functions.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridge.telegram_bridge import (
    get_media_type,
    VOICE_EXTENSIONS,
    VISION_EXTENSIONS,
    MEDIA_DIR,
)


class TestGetMediaType:
    """Tests for get_media_type function."""

    def test_no_media_returns_none(self):
        """Message without media returns None."""
        message = MagicMock()
        message.media = None
        assert get_media_type(message) is None

    def test_photo_returns_photo(self):
        """Photo media returns 'photo'."""
        from telethon.tl.types import MessageMediaPhoto

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaPhoto)
        # Need to set the class for isinstance check
        message.media.__class__ = MessageMediaPhoto
        assert get_media_type(message) == "photo"

    def test_voice_message_returns_voice(self):
        """Voice message returns 'voice'."""
        from telethon.tl.types import (
            MessageMediaDocument,
            DocumentAttributeAudio,
        )

        audio_attr = MagicMock(spec=DocumentAttributeAudio)
        audio_attr.__class__ = DocumentAttributeAudio
        audio_attr.voice = True

        doc = MagicMock()
        doc.attributes = [audio_attr]

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaDocument)
        message.media.__class__ = MessageMediaDocument
        message.media.document = doc

        assert get_media_type(message) == "voice"

    def test_audio_file_returns_audio(self):
        """Regular audio file (not voice) returns 'audio'."""
        from telethon.tl.types import (
            MessageMediaDocument,
            DocumentAttributeAudio,
        )

        audio_attr = MagicMock(spec=DocumentAttributeAudio)
        audio_attr.__class__ = DocumentAttributeAudio
        audio_attr.voice = False

        doc = MagicMock()
        doc.attributes = [audio_attr]

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaDocument)
        message.media.__class__ = MessageMediaDocument
        message.media.document = doc

        assert get_media_type(message) == "audio"

    def test_image_document_returns_image(self):
        """Document with image extension returns 'image'."""
        from telethon.tl.types import (
            MessageMediaDocument,
            DocumentAttributeFilename,
        )

        filename_attr = MagicMock(spec=DocumentAttributeFilename)
        filename_attr.__class__ = DocumentAttributeFilename
        filename_attr.file_name = "photo.png"

        doc = MagicMock()
        doc.attributes = [filename_attr]

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaDocument)
        message.media.__class__ = MessageMediaDocument
        message.media.document = doc

        assert get_media_type(message) == "image"

    def test_generic_document_returns_document(self):
        """Generic document returns 'document'."""
        from telethon.tl.types import (
            MessageMediaDocument,
            DocumentAttributeFilename,
        )

        filename_attr = MagicMock(spec=DocumentAttributeFilename)
        filename_attr.__class__ = DocumentAttributeFilename
        filename_attr.file_name = "report.pdf"

        doc = MagicMock()
        doc.attributes = [filename_attr]

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaDocument)
        message.media.__class__ = MessageMediaDocument
        message.media.document = doc

        assert get_media_type(message) == "document"


class TestMediaExtensions:
    """Tests for media extension sets."""

    def test_voice_extensions_comprehensive(self):
        """Voice extensions include common audio formats."""
        assert '.ogg' in VOICE_EXTENSIONS
        assert '.mp3' in VOICE_EXTENSIONS
        assert '.wav' in VOICE_EXTENSIONS
        assert '.opus' in VOICE_EXTENSIONS

    def test_vision_extensions_comprehensive(self):
        """Vision extensions include common image formats."""
        assert '.png' in VISION_EXTENSIONS
        assert '.jpg' in VISION_EXTENSIONS
        assert '.jpeg' in VISION_EXTENSIONS
        assert '.gif' in VISION_EXTENSIONS
        assert '.webp' in VISION_EXTENSIONS


class TestMediaDir:
    """Tests for media directory setup."""

    def test_media_dir_exists(self):
        """Media directory should exist (created on import)."""
        assert MEDIA_DIR.exists()

    def test_media_dir_is_in_data(self):
        """Media directory should be under data/."""
        assert 'data' in str(MEDIA_DIR)
        assert 'media' in str(MEDIA_DIR)


class TestTranscribeVoiceIntegration:
    """Integration tests for voice transcription (requires API key)."""

    @pytest.mark.asyncio
    async def test_transcribe_voice_no_api_key(self):
        """Transcription returns None when no API key is set."""
        from bridge.telegram_bridge import transcribe_voice

        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}):
            # Reload to pick up env change
            result = await transcribe_voice(Path("/tmp/fake.ogg"))
            assert result is None


class TestProcessIncomingMedia:
    """Tests for process_incoming_media function."""

    @pytest.mark.asyncio
    async def test_no_media_returns_empty(self):
        """Message without media returns empty description and files."""
        from bridge.telegram_bridge import process_incoming_media

        client = AsyncMock()
        message = MagicMock()
        message.media = None

        description, files = await process_incoming_media(client, message)
        assert description == ""
        assert files == []

    @pytest.mark.asyncio
    async def test_download_failure_returns_error_description(self):
        """Failed download returns error description."""
        from bridge.telegram_bridge import process_incoming_media
        from telethon.tl.types import MessageMediaPhoto

        client = AsyncMock()
        client.download_media = AsyncMock(return_value=None)

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaPhoto)
        message.media.__class__ = MessageMediaPhoto

        with patch('bridge.telegram_bridge.download_media', return_value=None):
            description, files = await process_incoming_media(client, message)
            assert "download failed" in description.lower()
            assert files == []
