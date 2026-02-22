import json
import logging
from xml.sax.saxutils import escape as xml_escape_str

import markdown as md
from django import template
from django.utils.safestring import mark_safe

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


@register.filter(name="xml_escape")
def xml_escape(value: str | None) -> str:
    """Escape text for safe inclusion in XML elements (outside CDATA)."""
    if not value:
        return ""
    return xml_escape_str(str(value))


@register.filter
def default_zero(value: int | None) -> int:
    """Return 0 if the value is None, otherwise return the value."""
    if value is None:
        return 0
    return value


@register.filter(name="render_markdown")
def render_markdown(text: str | None) -> str:
    """Convert markdown text to safe HTML."""
    if not text:
        return ""
    html = md.markdown(
        text,
        extensions=["tables", "fenced_code", "toc", "nl2br"],
    )
    return mark_safe(
        html
    )  # nosec B703 B308 — content is from our own DB, not user input


def _seconds_to_timestamp(seconds: int | float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS format."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


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
            raw = json.loads(episode.chapters)
            # Handle Podcasting 2.0 format: {"version": "...", "chapters": [...]}
            chapter_list = raw.get("chapters", []) if isinstance(raw, dict) else raw
            for ch in chapter_list:
                start = ch.get("startTime", ch.get("start_time", 0))
                title = ch.get("title", "")
                if title:
                    chapters.append(
                        {"start_time": _seconds_to_timestamp(start), "title": title}
                    )
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning("Invalid chapters JSON for episode %s", episode.pk)

    return {
        "episode": episode,
        "description": episode.description,
        "chapters": chapters,
        "sources_text": episode.sources_text if episode.has_meaningful_sources else "",
        "report_text": episode.report_text,
    }
