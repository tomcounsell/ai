"""
Episode configuration loader for podcast production tools.

Resolution order:
1. Read episode_config.json from episode directory (snapshot at setup time)
2. Infer podcast slug from directory path and query PodcastConfig from DB
3. Fall back to public Yudame Research defaults

This ensures:
- Tools can work offline using the JSON snapshot
- Legacy episodes without config files still work
- New episodes get config from the database via setup_episode.py
"""

import json
import os
import re
from pathlib import Path

# Default config for public Yudame Research feed
DEFAULT_CONFIG = {
    "podcast_slug": "yudame-research",
    "podcast_title": "Yudame Research",
    "is_public": True,
    "website_url": "https://research.yuda.me",
    "opening_script": "",
    "closing_script": "",
    "depth_level": "accessible",
    "sponsor_break": True,
    "companion_access": "public",
}


def load_config(episode_dir: str | Path) -> dict:
    """
    Load episode configuration from the given directory.

    Args:
        episode_dir: Path to the episode working directory

    Returns:
        dict with podcast configuration values
    """
    episode_dir = Path(episode_dir)

    # 1. Try episode_config.json in the directory
    config_file = episode_dir / "episode_config.json"
    if config_file.exists():
        with open(config_file) as f:
            return json.load(f)

    # 2. Try to infer podcast slug from directory path
    podcast_slug = _infer_podcast_slug(episode_dir)
    if podcast_slug:
        config = _load_from_db(podcast_slug)
        if config:
            return config

    # 3. Fall back to defaults
    return DEFAULT_CONFIG.copy()


def _infer_podcast_slug(episode_dir: Path) -> str | None:
    """
    Infer podcast slug from episode directory path.

    Expected patterns:
    - pending-episodes/{podcast-slug}/{episode-slug}/
    - pending-episodes/{date}-{episode-slug}/ (legacy, returns None)
    """
    parts = episode_dir.parts

    # Find pending-episodes in path
    try:
        idx = parts.index("pending-episodes")
    except ValueError:
        return None

    # Check if there's a podcast-slug directory after pending-episodes
    if len(parts) > idx + 2:
        potential_slug = parts[idx + 1]
        # Skip date-prefixed directories (legacy pattern)
        if re.match(r"^\d{4}-\d{2}-\d{2}", potential_slug):
            return None
        return potential_slug

    return None


def _load_from_db(podcast_slug: str) -> dict | None:
    """
    Load config from database for the given podcast slug.

    Returns None if podcast not found or Django not configured.
    """
    try:
        # Only import Django when needed (allows offline use)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
        import django

        django.setup()

        from apps.podcast.models import Podcast

        podcast = Podcast.objects.select_related("config").get(slug=podcast_slug)

        # If podcast has config, use it
        if hasattr(podcast, "config"):
            return podcast.config.to_dict()

        # Podcast exists but no config - return minimal info with defaults
        return {
            **DEFAULT_CONFIG,
            "podcast_slug": podcast.slug,
            "podcast_title": podcast.title,
            "is_public": podcast.is_public,
            "website_url": podcast.website_url or DEFAULT_CONFIG["website_url"],
        }

    except Exception:
        return None


def save_config(episode_dir: str | Path, config: dict) -> Path:
    """
    Save configuration to episode_config.json in the episode directory.

    Args:
        episode_dir: Path to the episode working directory
        config: Configuration dict to save

    Returns:
        Path to the saved config file
    """
    episode_dir = Path(episode_dir)
    episode_dir.mkdir(parents=True, exist_ok=True)

    config_file = episode_dir / "episode_config.json"
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)

    return config_file
