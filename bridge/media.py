"""Media detection, download, transcription, and document extraction."""

import asyncio
import logging
import os
from pathlib import Path

import httpx
from telethon import TelegramClient
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Media Directories
# =============================================================================

# Directory for downloaded media files
MEDIA_DIR = Path(__file__).parent.parent / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Media Constants
# =============================================================================

# Image extensions (for choosing send method - images sent without caption)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Video extensions (Telegram can preview these)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}

# Audio extensions (Telegram can play these)
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}

# Voice/audio extensions
VOICE_EXTENSIONS = {".ogg", ".oga", ".mp3", ".wav", ".m4a", ".opus"}

# Supported image extensions for vision
VISION_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Magic bytes for file type validation
FILE_MAGIC_BYTES = {
    "pdf": b"%PDF",
    "png": b"\x89PNG",
    "jpg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": b"RIFF",  # RIFF....WEBP
}

# Text-extractable document extensions
TEXT_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".css",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".sh",
    ".bash",
    ".sql",
    ".r",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
}


# =============================================================================
# Media Validation and Text Extraction
# =============================================================================


def validate_media_file(filepath: Path) -> tuple[bool, str]:
    """
    Validate a downloaded media file by checking magic bytes and basic structure.

    Returns (is_valid, error_reason). If valid, error_reason is empty.
    Uses stdlib only — no external dependencies.
    """
    if not filepath.exists():
        return False, "file does not exist"

    if filepath.stat().st_size == 0:
        return False, "file is empty (0 bytes)"

    ext = filepath.suffix.lower()

    try:
        with open(filepath, "rb") as f:
            header = f.read(32)

        if ext == ".pdf":
            if not header.startswith(b"%PDF"):
                return False, "file does not start with %PDF header"
            # Check for minimal PDF structure: must contain at least some content
            with open(filepath, "rb") as f:
                content = f.read()
            if b"%%EOF" not in content and b"endobj" not in content:
                return False, "PDF file is truncated or corrupted (no EOF marker)"

        elif ext in (".png",):
            if not header.startswith(b"\x89PNG"):
                return False, "file does not have PNG magic bytes"

        elif ext in (".jpg", ".jpeg"):
            if not header.startswith(b"\xff\xd8\xff"):
                return False, "file does not have JPEG magic bytes"

        elif ext in (".gif",):
            if not header.startswith((b"GIF87a", b"GIF89a")):
                return False, "file does not have GIF magic bytes"

        elif ext == ".webp":
            if not header.startswith(b"RIFF") or b"WEBP" not in header[:16]:
                return False, "file does not have WebP magic bytes"

        # For other extensions, just check it's not empty (already done above)
        return True, ""

    except Exception as e:
        return False, f"validation error: {e}"


def extract_document_text(filepath: Path, max_chars: int = 5000) -> str | None:
    """
    Extract text content from a document file.

    For text-based files, reads directly. For PDFs, extracts what we can with stdlib.
    Returns extracted text, or None if extraction failed.
    This allows us to inline document content so the agent doesn't need to read the raw file.
    """
    ext = filepath.suffix.lower()

    try:
        # Text-based documents: read directly
        if ext in TEXT_DOCUMENT_EXTENSIONS:
            content = filepath.read_text(errors="replace")
            if len(content) > max_chars:
                content = (
                    content[:max_chars]
                    + f"\n\n[... truncated, {len(filepath.read_bytes())} bytes total]"
                )
            return content

        # PDF: try to extract text with stdlib
        if ext == ".pdf":
            return _extract_pdf_text_stdlib(filepath, max_chars)

        return None

    except Exception as e:
        logger.warning(f"Could not extract text from {filepath.name}: {e}")
        return None


def _extract_pdf_text_stdlib(filepath: Path, max_chars: int = 5000) -> str | None:
    """
    PDF text extraction using pypdf library.

    Handles compressed streams, multiple pages, and most PDF formats.
    Won't work for scanned/image-only PDFs (would need OCR).
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(filepath)
        text_parts = []

        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())

        if text_parts:
            extracted = "\n\n".join(text_parts)
            if len(extracted) > max_chars:
                extracted = extracted[:max_chars] + "..."
            return extracted

        return None

    except Exception as e:
        logger.debug(f"PDF extraction failed: {e}")
        return None


# =============================================================================
# Media Type Detection
# =============================================================================


def get_media_type(message) -> str | None:
    """Determine the type of media in a message."""
    if not message.media:
        return None

    if isinstance(message.media, MessageMediaPhoto):
        return "photo"

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc:
            # Check for voice message
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeAudio):
                    if attr.voice:
                        return "voice"
                    return "audio"
            # Check for other document types
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name.lower()
                    if any(filename.endswith(ext) for ext in VISION_EXTENSIONS):
                        return "image"
                    if any(filename.endswith(ext) for ext in VOICE_EXTENSIONS):
                        return "audio"
            return "document"

    return None


# =============================================================================
# Media Download
# =============================================================================


async def download_media(
    client: TelegramClient, message, prefix: str = "media"
) -> Path | None:
    """
    Download media from a Telegram message.

    Returns the path to the downloaded file, or None if download failed.
    """
    try:
        # Generate unique filename with timestamp
        timestamp = message.date.strftime("%Y%m%d_%H%M%S")
        # Determine extension
        ext = ".bin"
        if isinstance(message.media, MessageMediaPhoto):
            ext = ".jpg"
        elif isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc:
                for attr in doc.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        ext = Path(attr.file_name).suffix.lower() or ext
                        break
                    if isinstance(attr, DocumentAttributeAudio):
                        if attr.voice:
                            ext = ".ogg"  # Telegram voice messages are typically ogg
                        break

        filename = f"{prefix}_{timestamp}_{message.id}{ext}"
        filepath = MEDIA_DIR / filename

        # Download
        await client.download_media(message, filepath)

        if filepath.exists():
            return filepath
        return None

    except Exception as e:
        logger.error(f"Failed to download media: {e}")
        return None


# =============================================================================
# Media Transcription and Description
# =============================================================================


async def transcribe_voice(filepath: Path) -> str | None:
    """
    Transcribe voice/audio file using OpenAI Whisper API.

    Returns transcription text, or None if transcription failed.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("No OPENAI_API_KEY for voice transcription")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(filepath, "rb") as f:
                files = {"file": (filepath.name, f, "audio/ogg")}
                data = {"model": "whisper-1"}
                headers = {"Authorization": f"Bearer {api_key}"}

                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    files=files,
                    data=data,
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    return result.get("text", "").strip()
                else:
                    logger.error(
                        f"Whisper API error: {response.status_code} - {response.text}"
                    )
                    return None

    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        return None


async def describe_image(filepath: Path) -> str | None:
    """
    Describe an image using Ollama LLaVA vision model.

    Returns image description text, or None if description failed.
    Falls back gracefully if Ollama or LLaVA is not available.
    """
    try:
        import ollama
    except ImportError:
        logger.warning("ollama library not installed for image vision")
        return None

    try:
        # Run the synchronous ollama.chat in a thread pool to not block the event loop
        loop = asyncio.get_event_loop()

        def _describe():
            response = ollama.chat(
                model="llama3.2-vision:11b",
                messages=[
                    {
                        "role": "user",
                        "content": "Describe this image in detail. What do you see?",
                        "images": [str(filepath)],
                    }
                ],
            )
            return response["message"]["content"]

        description = await loop.run_in_executor(None, _describe)
        return description.strip() if description else None

    except Exception as e:
        logger.error(f"Image description failed: {e}")
        return None


# =============================================================================
# Incoming Media Processing
# =============================================================================


async def process_incoming_media(
    client: TelegramClient, message
) -> tuple[str, list[Path]]:
    """
    Process media in an incoming message.

    Returns (description_text, list_of_file_paths).
    The description_text is meant to be prepended to the message for context.

    Files are validated after download. Invalid/corrupted files are described
    but not referenced by path, preventing downstream API errors when the agent
    tries to read them.
    """
    media_type = get_media_type(message)
    if not media_type:
        return "", []

    # Download the media
    downloaded = await download_media(client, message, prefix=media_type)
    if not downloaded:
        return f"[User sent a {media_type} but download failed]", []

    # Validate the downloaded file
    is_valid, validation_error = validate_media_file(downloaded)
    if not is_valid:
        logger.warning(
            f"Invalid {media_type} file {downloaded.name}: {validation_error}"
        )
        # Try to extract text content even from invalid files (best effort)
        extracted = extract_document_text(downloaded)
        if extracted:
            return (
                f"[User sent a {media_type} (file appears corrupted: {validation_error}), "
                f"but partial text was extracted]\n\nExtracted content:\n{extracted}"
            ), []
        return (
            f"[User sent a {media_type} but the file is invalid/corrupted: {validation_error}. "
            f"File cannot be read.]"
        ), []

    files = [downloaded]
    description = ""

    if media_type == "voice":
        # Transcribe voice message
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f'[Voice message transcription: "{transcription}"]'
        else:
            description = f"[User sent a voice message - saved to {downloaded.name}]"

    elif media_type in ("photo", "image"):
        # Use Ollama LLaVA to describe the image
        image_description = await describe_image(downloaded)
        if image_description:
            description = (
                f"[User sent an image]\nImage description: {image_description}"
            )
        else:
            # Fallback if vision model is not available
            description = f"[User sent an image - saved to {downloaded.name}]"

    elif media_type == "audio":
        # Try transcribing audio files too
        transcription = await transcribe_voice(downloaded)
        if transcription:
            description = f'[Audio file transcription: "{transcription}"]'
        else:
            description = f"[User sent an audio file - saved to {downloaded.name}]"

    elif media_type == "document":
        # Try to extract and inline document text content
        extracted = extract_document_text(downloaded)
        if extracted:
            description = (
                f"[User sent a document: {downloaded.name}]\n\n"
                f"Document content:\n{extracted}"
            )
        else:
            description = f"[User sent a document - saved to {downloaded.name}]"

    return description, files
