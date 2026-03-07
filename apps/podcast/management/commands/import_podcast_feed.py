import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.podcast.models import Episode, Podcast

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
DEFAULT_FEED_URL = "https://research.yuda.me/podcast/feed.xml"


def parse_duration(duration_str: str | None) -> int | None:
    """Parse iTunes duration format to seconds. Handles HH:MM:SS, MM:SS, or raw seconds."""
    if not duration_str:
        return None
    parts = duration_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    else:
        try:
            return int(parts[0])
        except ValueError:
            return None


class _HTMLStripper(HTMLParser):
    """Simple HTML tag stripper that collects text content."""

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []

    def handle_data(self, data: str) -> None:
        self._pieces.append(data)

    def get_text(self) -> str:
        return "".join(self._pieces).strip()


def strip_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _find_itunes(element: ET.Element, tag: str) -> ET.Element | None:
    """Find an iTunes-namespaced child element."""
    return element.find(f"{{{ITUNES_NS}}}{tag}")


def _find_itunes_all(element: ET.Element, tag: str) -> list[ET.Element]:
    """Find all iTunes-namespaced child elements."""
    return element.findall(f"{{{ITUNES_NS}}}{tag}")


def _text(element: ET.Element | None) -> str:
    """Safely extract text from an element, returning empty string if None."""
    if element is None:
        return ""
    return (element.text or "").strip()


def _collect_categories(channel: ET.Element) -> list[str]:
    """Collect all iTunes category names (including subcategories)."""
    categories: list[str] = []
    for cat in _find_itunes_all(channel, "category"):
        name = cat.get("text", "").strip()
        if name:
            categories.append(name)
        # Check for subcategories
        for subcat in cat.findall(f"{{{ITUNES_NS}}}category"):
            subname = subcat.get("text", "").strip()
            if subname:
                categories.append(subname)
    return categories


class Command(BaseCommand):
    help = "Import podcast episodes from an RSS feed XML"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "url",
            nargs="?",
            default=DEFAULT_FEED_URL,
            help=f"URL to fetch feed.xml from (default: {DEFAULT_FEED_URL})",
        )
        parser.add_argument(
            "--file",
            type=str,
            help="Local file path as alternative to URL",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without creating records",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        file_path: str | None = options.get("file")
        url: str = options["url"]

        # Fetch or read the XML
        xml_content = self._load_xml(url, file_path)
        if xml_content is None:
            return

        # Parse XML
        try:
            root = ET.fromstring(xml_content)  # nosec B314
        except ET.ParseError as e:
            self.stderr.write(self.style.ERROR(f"Failed to parse XML: {e}"))
            return

        channel = root.find("channel")
        if channel is None:
            self.stderr.write(self.style.ERROR("No <channel> element found in feed"))
            return

        # Extract and create/update podcast
        podcast = self._process_channel(channel, dry_run)
        if podcast is None:
            return

        # Extract and create episodes
        self._process_items(channel, podcast, dry_run)

    def _load_xml(self, url: str, file_path: str | None) -> str | None:
        """Load XML content from a URL or local file."""
        if file_path:
            self.stdout.write(f"Reading feed from file: {file_path}")
            try:
                with open(file_path, encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
                return None
            except OSError as e:
                self.stderr.write(self.style.ERROR(f"Error reading file: {e}"))
                return None
        else:
            self.stdout.write(f"Fetching feed from URL: {url}")
            try:
                with urllib.request.urlopen(url) as response:  # nosec B310
                    return response.read().decode("utf-8")
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error fetching URL: {e}"))
                return None

    def _process_channel(self, channel: ET.Element, dry_run: bool) -> Podcast | None:
        """Extract channel-level metadata and create/update a Podcast record."""
        title = _text(channel.find("title"))
        if not title:
            self.stderr.write(self.style.ERROR("No <title> found in channel"))
            return None

        description = _text(channel.find("description")) or _text(
            _find_itunes(channel, "summary")
        )
        author_name = _text(_find_itunes(channel, "author"))

        # Extract owner email
        author_email = ""
        owner = _find_itunes(channel, "owner")
        if owner is not None:
            author_email = _text(owner.find(f"{{{ITUNES_NS}}}email"))

        # Extract cover image
        cover_image_url = ""
        itunes_image = _find_itunes(channel, "image")
        if itunes_image is not None:
            cover_image_url = itunes_image.get("href", "")

        language = _text(channel.find("language")) or "en"
        categories = _collect_categories(channel)
        website_url = _text(channel.find("link"))
        slug = slugify(title)

        self.stdout.write(self.style.SUCCESS(f"Podcast: {title}"))
        self.stdout.write(f"  Slug: {slug}")
        self.stdout.write(f"  Author: {author_name}")
        self.stdout.write(f"  Language: {language}")
        self.stdout.write(f"  Categories: {categories}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("[DRY RUN] Would create/update podcast")
            )
            # Return a transient Podcast object for episode processing
            podcast = Podcast(
                title=title,
                slug=slug,
                description=description,
                author_name=author_name,
                author_email=author_email,
                cover_image_url=cover_image_url,
                language=language,
                categories=categories,
                website_url=website_url,
                privacy="public",
            )
            podcast.pk = -1  # Sentinel for dry run
            return podcast

        podcast, created = Podcast.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "description": description,
                "author_name": author_name,
                "author_email": author_email,
                "cover_image_url": cover_image_url,
                "language": language,
                "categories": categories,
                "website_url": website_url,
            },
            create_defaults={
                "title": title,
                "description": description,
                "author_name": author_name,
                "author_email": author_email,
                "cover_image_url": cover_image_url,
                "language": language,
                "categories": categories,
                "website_url": website_url,
                "privacy": "public",
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} podcast: {title}"))
        return podcast

    def _process_items(
        self, channel: ET.Element, podcast: Podcast, dry_run: bool
    ) -> None:
        """Extract items from channel and create Episode records."""
        items = channel.findall("item")
        if not items:
            self.stdout.write(self.style.WARNING("No <item> elements found"))
            return

        # Parse items with their pubDate for sorting
        parsed_items: list[tuple[str | None, ET.Element]] = []
        for item in items:
            pub_date_text = _text(item.find("pubDate"))
            parsed_items.append((pub_date_text, item))

        # Sort by pubDate chronologically (oldest first)
        def sort_key(pair: tuple[str | None, ET.Element]) -> str:
            pub_date_text = pair[0]
            if pub_date_text:
                try:
                    dt = parsedate_to_datetime(pub_date_text)
                    return dt.isoformat()
                except (ValueError, TypeError):
                    pass
            return ""

        parsed_items.sort(key=sort_key)

        imported = 0
        skipped = 0
        warnings = 0

        for episode_number, (pub_date_text, item) in enumerate(parsed_items, start=1):
            result = self._process_item(
                item, podcast, episode_number, pub_date_text, dry_run
            )
            if result == "imported":
                imported += 1
            elif result == "skipped":
                skipped += 1
            elif result == "warning":
                warnings += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {imported} imported, "
                f"{skipped} skipped, {warnings} warnings"
            )
        )

    def _process_item(
        self,
        item: ET.Element,
        podcast: Podcast,
        episode_number: int,
        pub_date_text: str | None,
        dry_run: bool,
    ) -> str:
        """Process a single feed item. Returns 'imported', 'skipped', or 'warning'."""
        title = _text(item.find("title"))
        if not title:
            self.stdout.write(
                self.style.WARNING(f"  [{episode_number}] Skipping item with no title")
            )
            return "warning"

        # Audio URL from enclosure
        enclosure = item.find("enclosure")
        audio_url = ""
        audio_file_size_bytes = None
        if enclosure is not None:
            audio_url = enclosure.get("url", "")
            length_str = enclosure.get("length", "")
            if length_str:
                import contextlib

                with contextlib.suppress(ValueError):
                    audio_file_size_bytes = int(length_str)

        if not audio_url:
            self.stdout.write(
                self.style.WARNING(
                    f"  [{episode_number}] WARNING: No audio URL for '{title}'"
                )
            )
            return "warning"

        # Idempotency check: skip if audio_url already exists for this podcast
        if (
            not dry_run
            and audio_url
            and (
                Episode.objects.filter(podcast=podcast, audio_url=audio_url)
                .exclude(audio_url="")
                .exists()
            )
        ):
            self.stdout.write(f"  [{episode_number}] Skipped (exists): {title}")
            return "skipped"

        # Description: raw HTML for show_notes, stripped for description
        raw_description = _text(item.find("description"))
        itunes_summary = _text(_find_itunes(item, "summary"))
        show_notes = raw_description or itunes_summary
        description = strip_html(raw_description) if raw_description else itunes_summary

        # Duration
        duration_el = _find_itunes(item, "duration")
        audio_duration_seconds = parse_duration(_text(duration_el))

        # Cover image
        cover_image_url = ""
        item_image = _find_itunes(item, "image")
        if item_image is not None:
            cover_image_url = item_image.get("href", "")

        # Explicit flag
        explicit_text = _text(_find_itunes(item, "explicit")).lower()
        is_explicit = explicit_text in ("true", "yes")

        # Published date
        published_at = None
        if pub_date_text:
            try:
                published_at = parsedate_to_datetime(pub_date_text)
            except (ValueError, TypeError):
                self.stdout.write(
                    self.style.WARNING(
                        f"  [{episode_number}] Could not parse pubDate: {pub_date_text}"
                    )
                )

        # Slug: slugify title, truncate to 50 chars
        slug = slugify(title)[:50]
        # Remove trailing hyphens from truncation
        slug = slug.rstrip("-")

        # Ensure slug is unique within this podcast (for dry run we skip this)
        if not dry_run:
            base_slug = slug
            counter = 2
            while Episode.objects.filter(podcast=podcast, slug=slug).exists():
                suffix = f"-{counter}"
                slug = base_slug[: 50 - len(suffix)] + suffix
                slug = slug.rstrip("-")
                counter += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"  [{episode_number}] [DRY RUN] Would import: {title}"
                )
            )
            return "imported"

        Episode.objects.create(
            podcast=podcast,
            title=title,
            slug=slug,
            episode_number=episode_number,
            description=description,
            show_notes=show_notes,
            audio_url=audio_url,
            audio_duration_seconds=audio_duration_seconds,
            audio_file_size_bytes=audio_file_size_bytes,
            cover_image_url=cover_image_url,
            is_explicit=is_explicit,
            published_at=published_at,
        )
        self.stdout.write(self.style.SUCCESS(f"  [{episode_number}] Imported: {title}"))
        return "imported"
