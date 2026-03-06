"""
Tests for media receiving and processing functions.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bridge.media import (
    MEDIA_DIR,
    VISION_EXTENSIONS,
    VOICE_EXTENSIONS,
    get_media_type,
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
            DocumentAttributeAudio,
            MessageMediaDocument,
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
            DocumentAttributeAudio,
            MessageMediaDocument,
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
            DocumentAttributeFilename,
            MessageMediaDocument,
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
            DocumentAttributeFilename,
            MessageMediaDocument,
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
        assert ".ogg" in VOICE_EXTENSIONS
        assert ".mp3" in VOICE_EXTENSIONS
        assert ".wav" in VOICE_EXTENSIONS
        assert ".opus" in VOICE_EXTENSIONS

    def test_vision_extensions_comprehensive(self):
        """Vision extensions include common image formats."""
        assert ".png" in VISION_EXTENSIONS
        assert ".jpg" in VISION_EXTENSIONS
        assert ".jpeg" in VISION_EXTENSIONS
        assert ".gif" in VISION_EXTENSIONS
        assert ".webp" in VISION_EXTENSIONS


class TestMediaDir:
    """Tests for media directory setup."""

    def test_media_dir_exists(self):
        """Media directory should exist (created on import)."""
        assert MEDIA_DIR.exists()

    def test_media_dir_is_in_data(self):
        """Media directory should be under data/."""
        assert "data" in str(MEDIA_DIR)
        assert "media" in str(MEDIA_DIR)


class TestTranscribeVoiceIntegration:
    """Integration tests for voice transcription (requires API key)."""

    @pytest.mark.asyncio
    async def test_transcribe_voice_no_api_key(self):
        """Transcription returns None when no API key is set."""
        from bridge.media import transcribe_voice

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            # Reload to pick up env change
            result = await transcribe_voice(Path("/tmp/fake.ogg"))
            assert result is None


class TestProcessIncomingMedia:
    """Tests for process_incoming_media function."""

    @pytest.mark.asyncio
    async def test_no_media_returns_empty(self):
        """Message without media returns empty description and files."""
        from bridge.media import process_incoming_media

        client = AsyncMock()
        message = MagicMock()
        message.media = None

        description, files = await process_incoming_media(client, message)
        assert description == ""
        assert files == []

    @pytest.mark.asyncio
    async def test_download_failure_returns_error_description(self):
        """Failed download returns error description."""
        from telethon.tl.types import MessageMediaPhoto

        from bridge.media import process_incoming_media

        client = AsyncMock()
        client.download_media = AsyncMock(return_value=None)

        message = MagicMock()
        message.media = MagicMock(spec=MessageMediaPhoto)
        message.media.__class__ = MessageMediaPhoto

        with patch("bridge.media.download_media", return_value=None):
            description, files = await process_incoming_media(client, message)
            assert "download failed" in description.lower()
            assert files == []


class TestDescribeImage:
    """Tests for describe_image function using real Ollama LLaVA.

    These tests use the actual local Ollama installation - no mocking.
    Local models are free and should always be tested directly.
    """

    @pytest.fixture(autouse=True)
    def check_ollama(self):
        """Check if Ollama is available with llama3.2-vision model."""
        try:
            import httpx

            response = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
            if response.status_code != 200:
                pytest.skip("Ollama not responding")
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            if not any("llama3.2-vision" in name for name in model_names):
                pytest.skip("llama3.2-vision model not available")
        except Exception as e:
            pytest.skip(f"Ollama not available: {e}")

    @pytest.fixture
    def sample_image(self, tmp_path):
        """Create a minimal valid PNG image for testing."""
        import struct
        import zlib

        def create_minimal_png(width=100, height=100):
            """Create a minimal valid PNG file."""
            signature = b"\x89PNG\r\n\x1a\n"

            ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = (
                struct.pack(">I", 13)
                + b"IHDR"
                + ihdr_data
                + struct.pack(">I", ihdr_crc)
            )

            raw_data = b"\x00" * (width * 3 + 1) * height
            compressed = zlib.compress(raw_data)
            idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
            idat = (
                struct.pack(">I", len(compressed))
                + b"IDAT"
                + compressed
                + struct.pack(">I", idat_crc)
            )

            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

            return signature + ihdr + idat + iend

        test_image = tmp_path / "test.png"
        test_image.write_bytes(create_minimal_png())
        return test_image

    @pytest.fixture
    def colored_image(self, tmp_path):
        """Create a PNG with actual colors for more interesting descriptions."""
        import struct
        import zlib

        width, height = 50, 50

        # Create a red square
        signature = b"\x89PNG\r\n\x1a\n"

        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

        # Red pixels (RGB)
        row_data = b"\x00" + (b"\xff\x00\x00" * width)  # Filter byte + red pixels
        raw_data = row_data * height
        compressed = zlib.compress(raw_data)
        idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
        idat = (
            struct.pack(">I", len(compressed))
            + b"IDAT"
            + compressed
            + struct.pack(">I", idat_crc)
        )

        iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

        test_image = tmp_path / "red_square.png"
        test_image.write_bytes(signature + ihdr + idat + iend)
        return test_image

    @pytest.mark.asyncio
    async def test_describe_image_returns_description(self, sample_image):
        """describe_image returns a meaningful text description."""
        from bridge.media import describe_image

        result = await describe_image(sample_image)

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 10  # Should have meaningful content

    @pytest.mark.asyncio
    async def test_describe_image_no_leading_trailing_whitespace(self, sample_image):
        """Description should not have leading or trailing whitespace."""
        from bridge.media import describe_image

        result = await describe_image(sample_image)

        assert result is not None
        assert result == result.strip()

    @pytest.mark.asyncio
    async def test_describe_colored_image(self, colored_image):
        """Vision model can describe a colored image."""
        from bridge.media import describe_image

        result = await describe_image(colored_image)

        assert result is not None
        assert len(result) > 10
        # The model should mention something about the color or shape
        # We don't assert specific words since LLM output varies

    @pytest.mark.asyncio
    async def test_describe_nonexistent_image(self):
        """Returns None for non-existent image file."""
        from bridge.media import describe_image

        result = await describe_image(Path("/nonexistent/path/image.jpg"))

        # Should handle gracefully (return None or raise handled exception)
        # The function catches exceptions and returns None
        assert result is None
