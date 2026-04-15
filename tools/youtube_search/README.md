# YouTube Search Tool

Search YouTube videos by query using yt-dlp. No API key required.

## Usage

```bash
# Basic search (returns 5 results)
valor-youtube-search "python tutorial"

# Limit results
valor-youtube-search --limit 3 "machine learning basics"
```

## Python API

```python
# Sync
from tools.youtube_search import youtube_search_sync
results = youtube_search_sync("python tutorial", limit=5)

# Async
from tools.youtube_search import youtube_search
results = await youtube_search("python tutorial", limit=5)
```

## Result Fields

**Guaranteed** (always present):
- `title` - Video title
- `url` - Video URL
- `video_id` - YouTube video ID

**Best-effort** (may be None with flat_playlist):
- `duration` - Duration in seconds
- `view_count` - Number of views
- `uploader` - Channel name
- `description` - Video description
- `upload_date` - Upload date (YYYYMMDD format)
