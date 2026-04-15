"""YouTube search tool using yt-dlp.

Provides sync and async APIs for searching YouTube videos by query string.
Returns structured results with title, URL, duration, view count, uploader, and description.

No API key required -- uses yt-dlp's built-in YouTube search capability.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

logger = logging.getLogger(__name__)

# Fields guaranteed to be present in every result
GUARANTEED_FIELDS = {"title", "url", "video_id"}

# Fields that may be None when using flat_playlist=True
BEST_EFFORT_FIELDS = {"duration", "view_count", "uploader", "description", "upload_date"}


def youtube_search_sync(query: str, limit: int = 5) -> list[dict]:
    """Search YouTube and return structured results.

    This is the primary sync implementation. Use youtube_search() for async contexts.

    Args:
        query: Search query string. Must not be empty or whitespace-only.
        limit: Maximum number of results to return (default 5).

    Returns:
        List of result dicts. Each dict has guaranteed fields (title, url, video_id)
        and best-effort fields (duration, view_count, uploader, description, upload_date)
        which may be None.

    Raises:
        ValueError: If query is empty or whitespace-only.
        RuntimeError: If yt-dlp extraction fails or times out.
    """
    if not query or not query.strip():
        raise ValueError("Search query must not be empty")

    query = query.strip()
    logger.info("YouTube search: query=%r limit=%d", query, limit)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "socket_timeout": 30,
    }

    search_url = f"ytsearch{limit}:{query}"

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_extract_info, ydl_opts, search_url)
            info = future.result(timeout=30)
    except TimeoutError:
        logger.error("YouTube search timed out: query=%r", query)
        raise RuntimeError(f"YouTube search timed out for query: {query}")
    except Exception as e:
        logger.error("YouTube search failed: query=%r error=%s", query, e)
        raise RuntimeError(f"YouTube search failed: {e}") from e

    entries = info.get("entries", []) if info else []
    results = []

    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id", "")
        result = {
            "title": entry.get("title", "Unknown"),
            "url": entry.get("url") or f"https://www.youtube.com/watch?v={video_id}",
            "video_id": video_id,
            "duration": entry.get("duration"),
            "view_count": entry.get("view_count"),
            "uploader": entry.get("uploader"),
            "description": entry.get("description"),
            "upload_date": entry.get("upload_date"),
        }
        results.append(result)

    logger.info("YouTube search returned %d results for query=%r", len(results), query)
    return results


def _extract_info(ydl_opts: dict, search_url: str) -> dict:
    """Run yt-dlp extract_info in a thread-safe manner."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(search_url, download=False)


async def youtube_search(query: str, limit: int = 5) -> list[dict]:
    """Async wrapper for youtube_search_sync.

    Runs the sync search in a thread executor to avoid blocking the event loop.
    Matches the pattern used by download_youtube_audio_async in tools/link_analysis.

    Args:
        query: Search query string.
        limit: Maximum number of results to return (default 5).

    Returns:
        List of result dicts (same structure as youtube_search_sync).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, youtube_search_sync, query, limit)


def format_results(results: list[dict]) -> str:
    """Format search results as human-readable text for CLI output.

    Handles None values gracefully for best-effort fields.

    Args:
        results: List of result dicts from youtube_search_sync.

    Returns:
        Formatted string with one result per block.
    """
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        parts = [f"{i}. {r['title']}"]
        parts.append(f"   URL: {r['url']}")

        if r.get("uploader"):
            parts.append(f"   Uploader: {r['uploader']}")

        if r.get("duration") is not None:
            minutes, seconds = divmod(int(r["duration"]), 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                parts.append(f"   Duration: {hours}:{minutes:02d}:{seconds:02d}")
            else:
                parts.append(f"   Duration: {minutes}:{seconds:02d}")

        if r.get("view_count") is not None:
            parts.append(f"   Views: {r['view_count']:,}")

        if r.get("description"):
            desc = r["description"][:150]
            if len(r["description"]) > 150:
                desc += "..."
            parts.append(f"   Description: {desc}")

        lines.append("\n".join(parts))

    return "\n\n".join(lines)
