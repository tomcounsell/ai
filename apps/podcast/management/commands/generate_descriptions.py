"""
Generate episode descriptions from report_text for episodes missing descriptions.

Extracts the first content paragraph from report_text, strips markdown formatting,
and truncates to 250 characters at a sentence boundary.

Usage:
    # Preview without saving
    uv run python manage.py generate_descriptions --dry-run

    # Generate descriptions for all eligible episodes
    uv run python manage.py generate_descriptions

Idempotent: skips episodes that already have descriptions or lack report_text.
"""

import re

from django.core.management.base import BaseCommand

from apps.podcast.models import Episode


def extract_description(report_text: str, max_length: int = 250) -> str:
    """Extract a short description from report_text.

    Algorithm:
    1. Split into lines
    2. Skip leading markdown headers (lines starting with #) and blank lines
    3. Take first paragraph (text up to next blank line or end)
    4. Strip inline markdown formatting (bold, italic, links)
    5. Truncate to max_length at nearest sentence boundary
    """
    lines = report_text.split("\n")

    # Skip headers and blank lines to find first content paragraph
    paragraph_lines: list[str] = []
    in_paragraph = False
    for line in lines:
        stripped = line.strip()
        if not in_paragraph:
            # Skip blank lines and markdown headers
            if not stripped or stripped.startswith("#"):
                continue
            # Found start of first content paragraph
            in_paragraph = True
            paragraph_lines.append(stripped)
        else:
            # In paragraph -- stop at blank line
            if not stripped:
                break
            paragraph_lines.append(stripped)

    if not paragraph_lines:
        return ""

    text = " ".join(line for line in paragraph_lines if line)

    # Strip inline markdown formatting
    text = _strip_markdown(text)

    # Truncate at sentence boundary
    text = _truncate_at_sentence(text, max_length)

    return text


def _strip_markdown(text: str) -> str:
    """Remove inline markdown formatting from text."""
    # Replace markdown links [text](url) with just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Replace bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Replace italic *text* or _text_ (single markers)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    return text


def _truncate_at_sentence(text: str, max_length: int = 250) -> str:
    """Truncate text at sentence boundary within max_length.

    If no sentence boundary found within max_length, truncate at word
    boundary and append ellipsis.
    """
    if len(text) <= max_length:
        return text

    # Find the last sentence boundary (". ") within max_length.
    # Note: naive split — abbreviations like "Dr. " or "U.S. " may cause
    # early truncation. Acceptable for AI-generated report text which uses
    # clean sentence boundaries.
    truncated = text[:max_length]
    last_period = truncated.rfind(". ")
    if last_period > 0:
        return truncated[: last_period + 1]

    # Check if truncated ends with a period
    if truncated.endswith("."):
        return truncated

    # No sentence boundary -- truncate at word boundary and add ellipsis
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space] + "..."

    # No space found -- hard truncate with ellipsis
    return truncated + "..."


class Command(BaseCommand):
    help = "Generate episode descriptions from report_text for episodes missing descriptions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview descriptions without saving to database",
        )

    def handle(self, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("[DRY RUN] No changes will be made\n")

        stats = {
            "generated": 0,
            "skipped_no_report": 0,
            "skipped_has_description": 0,
        }

        episodes = Episode.objects.select_related("podcast").order_by(
            "podcast__title", "episode_number"
        )

        for episode in episodes:
            label = f"  {episode.podcast.title} - {episode}"

            if episode.description:
                stats["skipped_has_description"] += 1
                self.stdout.write(f"{label}: skipped (already has description)")
                continue

            if not episode.report_text:
                stats["skipped_no_report"] += 1
                self.stdout.write(f"{label}: skipped (no report_text)")
                continue

            description = extract_description(episode.report_text)
            if not description:
                stats["skipped_no_report"] += 1
                self.stdout.write(f"{label}: skipped (no extractable content)")
                continue

            if dry_run:
                self.stdout.write(f"{label}: [DRY RUN] would set description to:")
                self.stdout.write(f"    {description}")
            else:
                episode.description = description
                episode.save(update_fields=["description", "modified_at"])
                self.stdout.write(f"{label}: description generated")

            stats["generated"] += 1

        # Summary
        self.stdout.write(
            f"\n{stats['generated']} descriptions generated, "
            f"{stats['skipped_no_report']} skipped (no report_text), "
            f"{stats['skipped_has_description']} skipped (already has description)"
        )
        if dry_run:
            self.stdout.write("[DRY RUN] No changes were made")
