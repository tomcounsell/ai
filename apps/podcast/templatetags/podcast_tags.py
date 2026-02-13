import json
import logging

from django import template

logger = logging.getLogger(__name__)

register = template.Library()


@register.filter
def duration_hhmmss(seconds: int | None) -> str:
    """Convert seconds to HH:MM:SS format for iTunes duration tag."""
    if seconds is None:
        return "00:00:00"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@register.filter
def default_zero(value: int | None) -> int:
    """Return 0 if the value is None, otherwise return the value."""
    if value is None:
        return 0
    return value


@register.inclusion_tag("podcast/_show_notes.html")
def episode_show_notes(episode) -> dict:
    """Render rich HTML show notes from Episode fields.

    Produces structured show notes including an overview from the episode
    description, key timestamps parsed from the chapters JSON field,
    sources text, and a link to the full report when available.

    Usage in templates::

        {% load podcast_tags %}
        {% episode_show_notes episode %}
    """
    chapters = []
    if episode.chapters:
        try:
            chapters = json.loads(episode.chapters)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid chapters JSON for episode %s", episode.pk)

    return {
        "episode": episode,
        "description": episode.description,
        "chapters": chapters,
        "sources_text": episode.sources_text,
        "report_text": episode.report_text,
    }
