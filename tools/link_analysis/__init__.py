"""
Link Analysis Tool

URL extraction, validation, and content analysis.
Includes YouTube video processing with audio extraction and transcription.
"""

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
import requests

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"  # Current Perplexity model

logger = logging.getLogger(__name__)

# =============================================================================
# YouTube URL Detection and Processing
# =============================================================================

# YouTube URL patterns - capture video ID
YOUTUBE_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})"),
]

# Maximum video duration in seconds
# Set via env var YOUTUBE_MAX_VIDEO_DURATION, default 10 hours (36000s)
# Practical limits: Whisper cost ~$0.006/min ($3.60/10hr), transcription ~real-time.
# Context window (200k tokens) fits 10+ hour lectures easily. 10hr covers any realistic content.
MAX_VIDEO_DURATION = int(os.getenv("YOUTUBE_MAX_VIDEO_DURATION", "36000"))

# Directory for downloaded YouTube audio
YOUTUBE_MEDIA_DIR = Path(__file__).parent.parent.parent / "data" / "media" / "youtube"
YOUTUBE_MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def extract_youtube_id(url: str) -> str | None:
    """
    Extract YouTube video ID from URL.

    Supports:
    - youtube.com/watch?v=VIDEO_ID
    - youtu.be/VIDEO_ID
    - youtube.com/shorts/VIDEO_ID
    - youtube.com/embed/VIDEO_ID
    - youtube.com/v/VIDEO_ID

    Args:
        url: URL to extract video ID from

    Returns:
        Video ID string or None if not a YouTube URL
    """
    for pattern in YOUTUBE_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video URL."""
    return extract_youtube_id(url) is not None


def extract_youtube_urls(text: str) -> list[tuple[str, str]]:
    """
    Extract all YouTube URLs from text with their video IDs.

    Args:
        text: Text containing URLs

    Returns:
        List of (url, video_id) tuples
    """
    # First extract all URLs
    urls_result = extract_urls(text)
    youtube_urls = []

    for url in urls_result.get("urls", []):
        video_id = extract_youtube_id(url)
        if video_id:
            youtube_urls.append((url, video_id))

    return youtube_urls


def get_youtube_video_info(video_id: str) -> dict | None:
    """
    Get video information using yt-dlp without downloading.

    Args:
        video_id: YouTube video ID

    Returns:
        Dict with video info (title, duration, etc.) or None on error
    """
    try:
        import json as json_module

        url = f"https://www.youtube.com/watch?v={video_id}"
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            url,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            info = json_module.loads(result.stdout)
            return {
                "video_id": video_id,
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "view_count": info.get("view_count", 0),
                "description": info.get("description", "")[
                    :500
                ],  # Truncate description
                "is_live": info.get("is_live", False),
            }
        else:
            logger.error(f"yt-dlp info failed: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.error(f"yt-dlp info timed out for {video_id}")
        return None
    except FileNotFoundError:
        logger.error("yt-dlp not installed. Install with: pip install yt-dlp")
        return None
    except Exception as e:
        logger.error(f"Error getting YouTube video info: {e}")
        return None


def download_youtube_audio(
    video_id: str, output_dir: Path | None = None
) -> Path | None:
    """
    Download audio from YouTube video using yt-dlp.

    Args:
        video_id: YouTube video ID
        output_dir: Directory to save audio file (default: YOUTUBE_MEDIA_DIR)

    Returns:
        Path to downloaded audio file or None on error
    """
    if output_dir is None:
        output_dir = YOUTUBE_MEDIA_DIR

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"youtube_{video_id}.mp3"

    # Skip if already downloaded
    if output_path.exists():
        logger.info(f"Audio already cached: {output_path}")
        return output_path

    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        # Output template without extension (yt-dlp adds it)
        output_template = str(output_path).replace(".mp3", "")

        cmd = [
            "yt-dlp",
            "--format",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "192K",
            "--output",
            output_template + ".%(ext)s",
            "--no-warnings",
            "--quiet",
            url,
        ]

        logger.info(f"Downloading YouTube audio: {video_id}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for download
        )

        if result.returncode == 0:
            # yt-dlp may create file with different extension during conversion
            # Check for the expected mp3 file
            if output_path.exists():
                logger.info(f"Downloaded audio: {output_path}")
                return output_path
            else:
                # Try to find the downloaded file
                for ext in [".mp3", ".m4a", ".webm", ".opus"]:
                    alt_path = output_dir / f"youtube_{video_id}{ext}"
                    if alt_path.exists():
                        logger.info(f"Downloaded audio: {alt_path}")
                        return alt_path
                logger.error(f"Download succeeded but file not found: {output_path}")
                return None
        else:
            logger.error(f"yt-dlp download failed: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.error(f"yt-dlp download timed out for {video_id}")
        return None
    except FileNotFoundError:
        logger.error("yt-dlp not installed. Install with: pip install yt-dlp")
        return None
    except Exception as e:
        logger.error(f"Error downloading YouTube audio: {e}")
        return None


async def download_youtube_audio_async(
    video_id: str, output_dir: Path | None = None
) -> Path | None:
    """
    Async wrapper for downloading YouTube audio.

    Args:
        video_id: YouTube video ID
        output_dir: Directory to save audio file

    Returns:
        Path to downloaded audio file or None on error
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        download_youtube_audio,
        video_id,
        output_dir,
    )


async def transcribe_audio_file(filepath: Path) -> str | None:
    """
    Transcribe audio file using OpenAI Whisper API.

    Args:
        filepath: Path to audio file

    Returns:
        Transcription text or None on error
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("No OPENAI_API_KEY for audio transcription")
        return None

    try:
        # Determine MIME type based on extension
        ext = filepath.suffix.lower()
        mime_types = {
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".wav": "audio/wav",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
            ".opus": "audio/opus",
        }
        mime_type = mime_types.get(ext, "audio/mpeg")

        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(filepath, "rb") as f:
                files = {"file": (filepath.name, f, mime_type)}
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
        logger.error(f"Audio transcription failed: {e}")
        return None


async def summarize_transcript(text: str, max_length: int = 500) -> str:
    """
    Summarize long transcript using OpenAI API.

    Args:
        text: Text to summarize
        max_length: Maximum length of summary

    Returns:
        Summary text or original text if summarization fails
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("No OPENAI_API_KEY for summarization")
        # Return truncated text as fallback
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"Summarize the following transcript concisely in "
                                f"{max_length} characters or less. Focus on the main "
                                f"points and key information."
                            ),
                        },
                        {
                            "role": "user",
                            "content": text,
                        },
                    ],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )

            if response.status_code == 200:
                result = response.json()
                summary = (
                    result.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
                return summary.strip()
            else:
                logger.error(f"OpenAI summarization error: {response.status_code}")
                # Fallback to truncation
                if len(text) > max_length:
                    return text[:max_length] + "..."
                return text

    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text


async def process_youtube_url(url: str) -> dict:
    """
    Process a YouTube URL: download audio, transcribe, and optionally summarize.

    Args:
        url: YouTube video URL

    Returns:
        Dict with:
            - success: bool
            - video_id: str
            - title: str (if available)
            - transcript: str (full transcript)
            - summary: str (summary if transcript > 2000 chars)
            - context: str (formatted context for message enrichment)
            - error: str (if failed)
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        return {
            "success": False,
            "error": "Not a valid YouTube URL",
            "context": "",
        }

    result = {
        "success": False,
        "video_id": video_id,
        "url": url,
        "title": None,
        "transcript": None,
        "summary": None,
        "context": "",
    }

    # Get video info first
    video_info = get_youtube_video_info(video_id)
    if video_info:
        result["title"] = video_info.get("title")
        duration = video_info.get("duration", 0)

        # Check if video is live
        if video_info.get("is_live"):
            result["error"] = "Cannot transcribe live streams"
            result["context"] = f"[YouTube Live Stream: {result['title']}]"
            return result

        # Check duration limit
        if duration > MAX_VIDEO_DURATION:
            result["error"] = (
                f"Video too long ({duration}s > {MAX_VIDEO_DURATION}s limit)"
            )
            result["context"] = (
                f"[YouTube video too long to transcribe: {result['title']} "
                f"({duration // 60}:{duration % 60:02d})]"
            )
            return result

    # Download audio
    audio_path = await download_youtube_audio_async(video_id)
    if not audio_path:
        result["error"] = "Failed to download audio"
        result["context"] = f"[YouTube video, could not download: {url}]"
        return result

    # Transcribe audio
    transcript = await transcribe_audio_file(audio_path)
    if not transcript:
        result["error"] = "Failed to transcribe audio"
        result["context"] = f"[YouTube video, transcription failed: {url}]"
        return result

    result["transcript"] = transcript
    result["success"] = True

    # Summarize if too long
    if len(transcript) > 2000:
        summary = await summarize_transcript(transcript, max_length=500)
        result["summary"] = summary
        title_part = f" - {result['title']}" if result["title"] else ""
        result["context"] = f"[YouTube video{title_part} transcript summary: {summary}]"
    else:
        title_part = f" - {result['title']}" if result["title"] else ""
        result["context"] = f"[YouTube video{title_part} transcript: {transcript}]"

    return result


async def process_youtube_urls_in_text(text: str) -> tuple[str, list[dict]]:
    """
    Process all YouTube URLs in text and return enriched text with results.

    Args:
        text: Text potentially containing YouTube URLs

    Returns:
        Tuple of (enriched_text, list of processing results)
    """
    youtube_urls = extract_youtube_urls(text)
    if not youtube_urls:
        return text, []

    results = []
    context_parts = []

    for url, video_id in youtube_urls:
        result = await process_youtube_url(url)
        results.append(result)
        if result.get("context"):
            context_parts.append(result["context"])

    # Append all YouTube contexts to the text
    if context_parts:
        enriched_text = text + "\n\n" + "\n\n".join(context_parts)
        return enriched_text, results

    return text, results


# =============================================================================
# General URL Processing
# =============================================================================

# URL regex pattern
URL_PATTERN = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.~:/?#[\]@!$&\'()*+,;=%]*",
    re.IGNORECASE,
)


class LinkAnalysisError(Exception):
    """Link analysis operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def extract_urls(text: str) -> dict:
    """
    Extract URLs from text.

    Args:
        text: Text containing URLs

    Returns:
        dict with:
            - urls: List of extracted URLs
            - count: Number of URLs found
    """
    if not text:
        return {"urls": [], "count": 0}

    urls = URL_PATTERN.findall(text)
    unique_urls = list(dict.fromkeys(urls))  # Preserve order, remove duplicates

    return {
        "urls": unique_urls,
        "count": len(unique_urls),
    }


def validate_url(url: str, timeout: int = 10) -> dict:
    """
    Validate a URL by checking if it's accessible.

    Args:
        url: URL to validate
        timeout: Request timeout in seconds

    Returns:
        dict with validation result
    """
    if not url:
        return {"url": url, "valid": False, "error": "URL cannot be empty"}

    # Check URL format
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return {"url": url, "valid": False, "error": "Invalid URL format"}
    except Exception as e:
        return {"url": url, "valid": False, "error": str(e)}

    # Check accessibility
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return {
            "url": url,
            "valid": True,
            "status_code": response.status_code,
            "final_url": response.url,
            "redirected": response.url != url,
        }
    except requests.exceptions.Timeout:
        return {"url": url, "valid": False, "error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        return {"url": url, "valid": False, "error": str(e)}


def get_metadata(url: str, timeout: int = 10) -> dict:
    """
    Get metadata from a URL (title, description, etc.).

    Args:
        url: URL to fetch metadata from
        timeout: Request timeout

    Returns:
        dict with metadata
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        content = response.text

        metadata = {
            "url": url,
            "title": None,
            "description": None,
            "content_type": response.headers.get("content-type"),
        }

        # Extract title
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.IGNORECASE)
        if title_match:
            metadata["title"] = title_match.group(1).strip()

        # Extract meta description
        desc_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']',
            content,
            re.IGNORECASE,
        )
        if not desc_match:
            desc_match = re.search(
                r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description["\']',
                content,
                re.IGNORECASE,
            )
        if desc_match:
            metadata["description"] = desc_match.group(1).strip()

        return metadata

    except requests.exceptions.RequestException as e:
        return {"url": url, "error": str(e)}


async def summarize_url_content(url: str, timeout: float = 30.0) -> str | None:
    """
    Use Perplexity API to summarize URL content.

    Args:
        url: URL to summarize
        timeout: Request timeout in seconds

    Returns:
        Summary string, or None if summarization failed
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY not set, skipping URL summarization")
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                PERPLEXITY_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Summarize the main points of this URL in 2-3 sentences. Be concise and informative: {url}",
                        }
                    ],
                    "max_tokens": 256,
                },
            )

            if response.status_code == 200:
                data = response.json()
                summary = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
                if summary:
                    logger.info(f"Successfully summarized URL: {url[:50]}...")
                    return summary.strip()
            else:
                logger.error(
                    f"Perplexity API error {response.status_code}: {response.text[:200]}"
                )

    except httpx.TimeoutException:
        logger.warning(f"Timeout summarizing URL: {url[:50]}...")
    except Exception as e:
        logger.error(f"Error summarizing URL: {e}")

    return None


def analyze_url(
    url: str,
    analyze_content: bool = True,
) -> dict:
    """
    Analyze a URL's content using AI.

    Args:
        url: URL to analyze
        analyze_content: Whether to analyze page content

    Returns:
        dict with analysis
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return {"error": "PERPLEXITY_API_KEY environment variable not set"}

    # Get basic validation and metadata
    validation = validate_url(url)
    if not validation.get("valid"):
        return {
            "url": url,
            "validation": validation,
            "error": f"URL not accessible: {validation.get('error')}",
        }

    metadata = get_metadata(url)

    if not analyze_content:
        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
        }

    # Use Perplexity to analyze the content
    try:
        response = requests.post(
            PERPLEXITY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Summarize the content of the given URL concisely.",
                    },
                    {
                        "role": "user",
                        "content": f"Analyze and summarize this URL: {url}",
                    },
                ],
                "max_tokens": 512,
            },
            timeout=60,
        )

        response.raise_for_status()
        result = response.json()

        summary = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
            "analysis": {
                "summary": summary,
            },
        }

    except requests.exceptions.RequestException as e:
        return {
            "url": url,
            "validation": validation,
            "metadata": metadata,
            "error": f"Analysis failed: {str(e)}",
        }


def analyze_text_links(
    text: str,
    analyze_content: bool = False,
    validate_links: bool = True,
) -> dict:
    """
    Extract and analyze all links in text.

    Args:
        text: Text containing URLs
        analyze_content: Analyze page content for each URL
        validate_links: Validate each URL

    Returns:
        dict with all extracted and analyzed links
    """
    extracted = extract_urls(text)
    urls = extracted["urls"]

    results = []
    for url in urls:
        result = {"url": url}

        if validate_links:
            result["validation"] = validate_url(url)

        if analyze_content:
            analysis = analyze_url(url, analyze_content=True)
            result.update(analysis)

        results.append(result)

    return {
        "text_length": len(text),
        "urls_found": len(urls),
        "results": results,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python -m tools.link_analysis 'https://example.com' or 'text with urls'"
        )
        sys.exit(1)

    arg = " ".join(sys.argv[1:])

    if arg.startswith(("http://", "https://")):
        print(f"Analyzing URL: {arg}")
        result = analyze_url(arg)
    else:
        print("Extracting URLs from text")
        result = extract_urls(arg)

    import json

    print(json.dumps(result, indent=2))
