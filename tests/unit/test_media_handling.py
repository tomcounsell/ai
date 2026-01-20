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


class TestDescribeImage:
    """Tests for describe_image function using Ollama LLaVA."""

    @pytest.mark.asyncio
    async def test_describe_image_no_ollama(self):
        """Returns None when ollama library is not installed."""
        from bridge.telegram_bridge import describe_image

        # Mock the import to fail
        with patch.dict('sys.modules', {'ollama': None}):
            result = await describe_image(Path("/tmp/fake.jpg"))
            assert result is None

    @pytest.mark.asyncio
    async def test_describe_image_exception_handling(self):
        """Returns None and logs error on exception."""
        from bridge.telegram_bridge import describe_image

        # Create a mock ollama module
        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(side_effect=Exception("Connection refused"))

        with patch.dict('sys.modules', {'ollama': mock_ollama}):
            result = await describe_image(Path("/tmp/fake.jpg"))
            assert result is None

    @pytest.mark.asyncio
    async def test_describe_image_returns_content(self):
        """Returns description when ollama succeeds."""
        from bridge.telegram_bridge import describe_image

        mock_response = {
            'message': {
                'content': 'This is a photo of a cat sitting on a windowsill.'
            }
        }

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(return_value=mock_response)

        with patch.dict('sys.modules', {'ollama': mock_ollama}):
            result = await describe_image(Path("/tmp/fake.jpg"))
            assert result is not None
            assert "cat" in result.lower()

    @pytest.mark.asyncio
    async def test_describe_image_empty_content(self):
        """Returns None when ollama returns empty content."""
        from bridge.telegram_bridge import describe_image

        mock_response = {
            'message': {
                'content': ''
            }
        }

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(return_value=mock_response)

        with patch.dict('sys.modules', {'ollama': mock_ollama}):
            result = await describe_image(Path("/tmp/fake.jpg"))
            # Empty content should return None or empty string
            assert result is None or result == ""

    @pytest.mark.asyncio
    async def test_describe_image_strips_whitespace(self):
        """Strips whitespace from returned description."""
        from bridge.telegram_bridge import describe_image

        mock_response = {
            'message': {
                'content': '  A beautiful sunset over the ocean.  \n'
            }
        }

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(return_value=mock_response)

        with patch.dict('sys.modules', {'ollama': mock_ollama}):
            result = await describe_image(Path("/tmp/fake.jpg"))
            assert result == "A beautiful sunset over the ocean."


class TestDescribeImageIntegration:
    """Integration tests for describe_image with real Ollama."""

    @pytest.fixture
    def ollama_available(self):
        """Check if Ollama is available with llama3.2-vision model."""
        try:
            import ollama
            models = ollama.list()
            model_names = [m.get('name', '') for m in models.get('models', [])]
            if not any('llama3.2-vision' in name for name in model_names):
                pytest.skip("llama3.2-vision model not available")
            return True
        except ImportError:
            pytest.skip("Ollama library not installed")
        except Exception:
            pytest.skip("Ollama not available")

    @pytest.fixture
    def sample_image(self, tmp_path):
        """Create a minimal valid PNG image for testing."""
        import struct
        import zlib

        def create_minimal_png(width=100, height=100):
            """Create a minimal valid PNG file."""
            signature = b'\x89PNG\r\n\x1a\n'

            ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff
            ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)

            raw_data = b'\x00' * (width * 3 + 1) * height
            compressed = zlib.compress(raw_data)
            idat_crc = zlib.crc32(b'IDAT' + compressed) & 0xffffffff
            idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)

            iend_crc = zlib.crc32(b'IEND') & 0xffffffff
            iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)

            return signature + ihdr + idat + iend

        test_image = tmp_path / "test.png"
        test_image.write_bytes(create_minimal_png())
        return test_image

    @pytest.mark.asyncio
    async def test_describe_real_image(self, ollama_available, sample_image):
        """Integration test: describe a real image with Ollama."""
        from bridge.telegram_bridge import describe_image

        result = await describe_image(sample_image)

        assert result is not None
        assert len(result) > 10  # Should have meaningful description
        assert isinstance(result, str)
