# YouTube Search

Search YouTube for videos by query string, returning structured results with metadata. Uses `yt-dlp` as the search backend -- no API key required.

## Usage

### CLI

```bash
# Search with default 5 results
valor-youtube-search "python tutorial"

# Limit results
valor-youtube-search --limit 3 "machine learning basics"
```

Output includes title, URL, uploader, duration, view count, and description snippet for each result.

### Python API

```python
# Sync (primary)
from tools.youtube_search import youtube_search_sync
results = youtube_search_sync("python tutorial", limit=5)

# Async (wraps sync via run_in_executor)
from tools.youtube_search import youtube_search
results = await youtube_search("python tutorial", limit=5)
```

### Result Structure

Each result is a dict with guaranteed and best-effort fields:

| Field | Type | Guaranteed | Notes |
|-------|------|-----------|-------|
| `title` | str | Yes | Video title |
| `url` | str | Yes | Full YouTube URL |
| `video_id` | str | Yes | YouTube video ID |
| `duration` | int or None | No | Duration in seconds |
| `view_count` | int or None | No | Number of views |
| `uploader` | str or None | No | Channel name |
| `description` | str or None | No | Video description |
| `upload_date` | str or None | No | YYYYMMDD format |

Best-effort fields may be `None` when using `flat_playlist=True` mode (which is faster but returns less metadata).

## Architecture

```
Agent/CLI -> youtube_search_sync() -> yt-dlp YoutubeDL.extract_info()
                                       (ytsearchN:query, flat_playlist=True)
                                       ThreadPoolExecutor with 30s timeout
```

- **Sync-first design**: `youtube_search_sync()` is the primary implementation
- **Async wrapper**: `youtube_search()` delegates to sync via `run_in_executor()`, matching the pattern in `tools/link_analysis/`
- **Timeout**: `socket_timeout=30` for individual network ops, plus `ThreadPoolExecutor` with 30s hard cap on total extraction time
- **No API key**: Uses yt-dlp's built-in YouTube search (`ytsearchN:` URL format)

## Error Handling

- Empty/whitespace query: `ValueError` (CLI exits with code 1 and usage message)
- Network timeout: `RuntimeError` (CLI prints to stderr, exits with code 1)
- yt-dlp extraction failure: `RuntimeError` (CLI prints to stderr, exits with code 1)
- All errors print to stderr so the agent can distinguish errors from results

## Files

| Path | Purpose |
|------|---------|
| `tools/youtube_search/__init__.py` | Search functions (sync, async, formatter) |
| `tools/youtube_search/cli.py` | CLI entry point |
| `tools/youtube_search/manifest.json` | Tool manifest |
| `tests/unit/test_youtube_search.py` | Unit and integration tests |

## Related

- `tools/link_analysis/` -- YouTube video metadata and transcription (for known video IDs)
- Issue [#260](https://github.com/tomcounsell/ai/issues/260) -- Original feature request
