#!/usr/bin/env python3
"""
Update podcast feed.xml with a new episode.

Reads logs/metadata.md from episode directory and generates XML <item> block.
Handles both standalone and series episodes.

Usage:
    python update_feed.py ../pending-episodes/2025-12-26-topic-slug/
    python update_feed.py ../pending-episodes/cardiovascular-health/ep6-lifestyle/
    python update_feed.py ../pending-episodes/2025-12-26-topic-slug/ --dry-run

Prerequisites:
    - logs/metadata.md exists with required fields
    - Audio file (.mp3) exists in episode directory
    - Optional: cover.png, *_chapters.json

The script will:
    1. Parse logs/metadata.md for episode metadata
    2. Auto-detect audio file, cover art, chapters
    3. Verify file size matches metadata (or read from file)
    4. Generate XML <item> block
    5. Insert into feed.xml after channel metadata
    6. Update <lastBuildDate>
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


def parse_metadata_md(metadata_path: Path) -> dict:
    """Parse logs/metadata.md into a dictionary."""
    content = metadata_path.read_text()
    metadata = {}

    # Extract title
    title_match = re.search(
        r"^## Title\s*\n+(.+?)(?=\n#|\n\n#|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if title_match:
        metadata["title"] = title_match.group(1).strip()

    # Extract publication date
    pub_match = re.search(
        r"^## Publication Date\s*\n+(.+?)(?=\n#|\n\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if pub_match:
        metadata["pub_date"] = pub_match.group(1).strip()

    # Extract series info
    series_match = re.search(r"\*\*Series Name:\*\*\s*(.+)", content)
    if series_match:
        series_name = series_match.group(1).strip()
        if series_name and series_name.lower() not in ["none", "n/a", ""]:
            metadata["series_name"] = series_name

    season_match = re.search(r"\*\*Season Number:\*\*\s*(\d+)", content)
    if season_match:
        metadata["season"] = int(season_match.group(1))

    episode_match = re.search(r"\*\*Episode Number:\*\*\s*(\d+)", content)
    if episode_match:
        metadata["episode"] = int(episode_match.group(1))

    # Extract audio info
    duration_match = re.search(r"\*\*Duration:\*\*\s*(\S+)", content)
    if duration_match:
        metadata["duration"] = duration_match.group(1).strip()

    size_match = re.search(r"\*\*File Size:\*\*\s*(\d+)", content)
    if size_match:
        metadata["file_size"] = int(size_match.group(1))

    # Extract description (plain text)
    desc_match = re.search(
        r"^## Description \(Plain Text\)\s*\n+(.+?)(?=\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if desc_match:
        metadata["description"] = desc_match.group(1).strip()

    # Extract key sources
    sources_match = re.search(
        r"^## Key Sources.*?\n(.+?)(?=\n#|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if sources_match:
        sources_text = sources_match.group(1).strip()
        sources = []
        for line in sources_text.split("\n"):
            line = line.strip()
            if line.startswith("-"):
                line = line[1:].strip()
            if ":" in line and "http" in line:
                # Format: "Source Name: URL" or "[Source Name](URL)"
                if line.startswith("["):
                    # Markdown link format
                    link_match = re.match(r"\[(.+?)\]\((.+?)\)", line)
                    if link_match:
                        sources.append(
                            {"name": link_match.group(1), "url": link_match.group(2)}
                        )
                else:
                    # "Name: URL" format
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        name = parts[0].strip()
                        url = parts[1].strip()
                        if url.startswith("http"):
                            sources.append({"name": name, "url": url})
                        elif ":" in parts[1]:
                            # URL has protocol, reconstruct
                            url = ":".join(parts[1:]).strip()
                            sources.append({"name": name, "url": url})
        metadata["sources"] = sources

    # Extract keywords
    keywords_match = re.search(
        r"^## Keywords.*?\n+(.+?)(?=\n#|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if keywords_match:
        metadata["keywords"] = keywords_match.group(1).strip()

    # Extract "What You'll Learn" section
    learn_match = re.search(
        r"^## What You'll Learn.*?\n+(.+?)(?=\n---|\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if learn_match:
        learn_text = learn_match.group(1).strip()
        bullets = []
        for line in learn_text.split("\n"):
            line = line.strip()
            if line.startswith("-"):
                bullet = line[1:].strip()
                if bullet and not bullet.startswith("**Format"):
                    bullets.append(bullet)
        if bullets:
            metadata["what_youll_learn"] = bullets

    # Extract Key Timestamps section
    timestamps_match = re.search(
        r"^## Key Timestamps.*?\n+(.+?)(?=\n---|\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if timestamps_match:
        ts_text = timestamps_match.group(1).strip()
        timestamps = []
        for line in ts_text.split("\n"):
            line = line.strip()
            if line.startswith("-") and "[" in line:
                ts_match = re.match(r"-\s*\*?\*?\[(\d+:\d+)\]\*?\*?\s*-?\s*(.*)", line)
                if ts_match:
                    timestamps.append(
                        {
                            "time": ts_match.group(1),
                            "description": ts_match.group(2).strip(),
                        }
                    )
        if timestamps:
            metadata["timestamps"] = timestamps

    # Extract Call-to-Action section
    cta_match = re.search(
        r"^### Primary CTA\s*\n+(.+?)(?=\n###|\n---|\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if cta_match:
        cta_text = cta_match.group(1).strip()
        if cta_text and not cta_text.startswith("["):
            metadata["cta"] = cta_text

    return metadata


def get_audio_duration(audio_path: Path) -> str:
    """Get audio duration using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            seconds = float(result.stdout.strip())
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            if hours > 0:
                return f"{hours}:{minutes:02d}:{secs:02d}"
            else:
                return f"{minutes}:{secs:02d}"
    except Exception as e:
        print(f"Warning: Could not get duration: {e}")
    return None


def get_file_size(file_path: Path) -> int:
    """Get file size in bytes."""
    return file_path.stat().st_size


def format_rfc2822(dt: datetime = None) -> str:
    """Format datetime as RFC 2822 string."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def parse_rfc2822(date_str: str) -> datetime:
    """Parse RFC 2822 date string to datetime."""
    # Try various formats
    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    # If all fail, return now
    return datetime.now(timezone.utc)


def build_episode_url(episode_dir: Path, filename: str) -> str:
    """Build the full URL for an episode file."""
    # Get path relative to apps/podcast/pending-episodes/
    try:
        rel_path = episode_dir.relative_to(episode_dir.parent.parent)
    except ValueError:
        rel_path = episode_dir.name

    return f"https://research.yuda.me/podcast/episodes/{rel_path}/{filename}"


def generate_content_encoded(metadata: dict, report_url: str) -> str:
    """Generate enhanced HTML content for content:encoded field."""
    description = metadata.get("description", "")
    sources = metadata.get("sources", [])
    what_youll_learn = metadata.get("what_youll_learn", [])
    timestamps = metadata.get("timestamps", [])
    cta = metadata.get("cta", "")

    html_parts = []

    # Overview
    html_parts.append("<h2>Overview</h2>")
    paragraphs = description.split("\n\n")
    for para in paragraphs:
        para = para.strip()
        if para and not para.startswith("Full research report:"):
            html_parts.append(f"<p>{escape(para)}</p>")

    # What You'll Learn
    if what_youll_learn:
        html_parts.append("<h2>What You'll Learn</h2>")
        html_parts.append("<ul>")
        for bullet in what_youll_learn:
            html_parts.append(f"<li>{escape(bullet)}</li>")
        html_parts.append("</ul>")

    # Key Timestamps
    if timestamps:
        html_parts.append("<h2>Key Timestamps</h2>")
        html_parts.append("<ul>")
        for ts in timestamps:
            html_parts.append(
                f'<li><strong>[{escape(ts["time"])}]</strong> - {escape(ts["description"])}</li>'
            )
        html_parts.append("</ul>")

    # Sources grouped
    if sources:
        html_parts.append("<h2>Resources &amp; Sources</h2>")
        html_parts.append("<ul>")
        for source in sources:
            html_parts.append(
                f'<li><a href="{escape(source["url"])}">{escape(source["name"])}</a></li>'
            )
        html_parts.append("</ul>")

    # Full research report link
    html_parts.append(f"<h2>Full Research Report</h2>")
    html_parts.append(
        f'<p>Read the complete research synthesis with all citations at: <a href="{report_url}">research.yuda.me</a></p>'
    )

    # Call-to-action
    if cta:
        html_parts.append(f"<p><em>{escape(cta)}</em></p>")

    return "<![CDATA[" + "".join(html_parts) + "]]>"


def generate_item_xml(metadata: dict, episode_dir: Path, audio_file: Path) -> str:
    """Generate the <item> XML block."""

    # Build URLs
    audio_url = build_episode_url(episode_dir, audio_file.name)
    report_url = build_episode_url(episode_dir, "report.md")

    # Check for cover art
    cover_path = episode_dir / "cover.png"
    cover_url = None
    if cover_path.exists():
        # Add cache-busting version
        version = datetime.now().strftime("%Y-%m-%d")
        cover_url = build_episode_url(episode_dir, f"cover.png?v={version}")

    # Check for chapters
    chapters_files = list(episode_dir.glob("*_chapters.json"))
    chapters_url = None
    if chapters_files:
        chapters_url = build_episode_url(episode_dir, chapters_files[0].name)

    # Get file size (verify or read)
    file_size = metadata.get("file_size") or get_file_size(audio_file)

    # Get duration (verify or read)
    duration = metadata.get("duration") or get_audio_duration(audio_file)

    # Parse publication date
    pub_date_str = metadata.get("pub_date", format_rfc2822())
    if not pub_date_str.startswith(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        # Convert from ISO or other format
        pub_dt = parse_rfc2822(pub_date_str)
        pub_date_str = format_rfc2822(pub_dt)

    # Build description (plain text)
    description = metadata.get("description", "")

    # Generate content:encoded (HTML)
    content_encoded = generate_content_encoded(metadata, report_url)

    # Build the XML
    lines = []
    lines.append("    <item>")
    lines.append(f"      <title>{escape(metadata['title'])}</title>")

    if cover_url:
        lines.append(f'      <itunes:image href="{cover_url}"/>')

    lines.append(f"      <description>{escape(description)}</description>")
    lines.append(f"      <content:encoded>{content_encoded}</content:encoded>")
    lines.append(f"      <author>valor@yuda.me (Valor Engels)</author>")
    lines.append(f"      <pubDate>{pub_date_str}</pubDate>")
    lines.append(f'      <enclosure url="{audio_url}"')
    lines.append(f'                 length="{file_size}"')
    lines.append(f'                 type="audio/mpeg"/>')
    lines.append(f"      <guid>{audio_url}</guid>")
    lines.append(f"      <itunes:author>Valor Engels</itunes:author>")
    lines.append(f"      <itunes:duration>{duration}</itunes:duration>")
    lines.append(f"      <itunes:explicit>no</itunes:explicit>")
    lines.append(f"      <itunes:episodeType>full</itunes:episodeType>")

    if "season" in metadata:
        lines.append(f"      <itunes:season>{metadata['season']}</itunes:season>")

    if "episode" in metadata:
        lines.append(f"      <itunes:episode>{metadata['episode']}</itunes:episode>")

    if "keywords" in metadata:
        lines.append(
            f"      <itunes:keywords>{escape(metadata['keywords'])}</itunes:keywords>"
        )

    if "series_name" in metadata:
        lines.append(
            f"      <research:series>{escape(metadata['series_name'])}</research:series>"
        )

    if chapters_url:
        lines.append(
            f'      <podcast:chapters url="{chapters_url}" type="application/json+chapters"/>'
        )

    # Add podcast:transcript tag if transcript.txt exists
    transcript_path = episode_dir / "transcript.txt"
    if transcript_path.exists():
        transcript_url = build_episode_url(episode_dir, "transcript.txt")
        lines.append(
            f'      <podcast:transcript url="{transcript_url}" type="text/plain"/>'
        )

    lines.append("    </item>")

    return "\n".join(lines)


def update_feed_xml(feed_path: Path, item_xml: str, episode_title: str) -> str:
    """Insert new item into feed.xml and update lastBuildDate."""
    content = feed_path.read_text()

    # Update lastBuildDate
    new_build_date = format_rfc2822()
    content = re.sub(
        r"<lastBuildDate>.*?</lastBuildDate>",
        f"<lastBuildDate>{new_build_date}</lastBuildDate>",
        content,
    )

    # Find insertion point (after channel metadata, before first <item>)
    # Look for the first <item> or end of channel setup
    first_item_match = re.search(r"(\n\s*)(<!--.*?-->)?\s*\n\s*<item>", content)

    if first_item_match:
        # Insert before first item, with a comment
        insert_pos = first_item_match.start()
        # Create episode comment
        short_title = (
            episode_title.split(":")[0] if ":" in episode_title else episode_title
        )
        episode_comment = f"\n\n    <!-- Episode: {short_title} -->\n"

        new_content = (
            content[:insert_pos] + episode_comment + item_xml + content[insert_pos:]
        )
    else:
        # No existing items, insert before </channel>
        insert_pos = content.rfind("</channel>")
        if insert_pos == -1:
            raise ValueError("Could not find </channel> in feed.xml")

        short_title = (
            episode_title.split(":")[0] if ":" in episode_title else episode_title
        )
        episode_comment = f"\n    <!-- Episode: {short_title} -->\n"

        new_content = (
            content[:insert_pos]
            + episode_comment
            + item_xml
            + "\n\n  "
            + content[insert_pos:]
        )

    return new_content


def main():
    parser = argparse.ArgumentParser(
        description="Update podcast feed.xml with a new episode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add standalone episode
  %(prog)s ../pending-episodes/2025-12-26-topic-slug/

  # Add series episode
  %(prog)s ../pending-episodes/cardiovascular-health/ep6-lifestyle/

  # Preview without writing
  %(prog)s ../pending-episodes/2025-12-26-topic-slug/ --dry-run
""",
    )

    parser.add_argument(
        "episode_dir", help="Path to episode directory containing logs/metadata.md"
    )
    parser.add_argument(
        "--feed",
        "-f",
        default=None,
        help="Path to feed.xml (default: auto-detect from repo)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview changes without modifying feed.xml",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")

    args = parser.parse_args()

    def log(msg):
        if not args.quiet:
            print(msg)

    # Resolve paths
    episode_dir = Path(args.episode_dir).resolve()

    if not episode_dir.exists():
        print(f"Error: Episode directory not found: {episode_dir}", file=sys.stderr)
        return 1

    # Find metadata.md
    metadata_path = episode_dir / "logs" / "metadata.md"
    if not metadata_path.exists():
        print(f"Error: logs/metadata.md not found in {episode_dir}", file=sys.stderr)
        return 1

    # Find audio file
    audio_files = list(episode_dir.glob("*.mp3"))
    if not audio_files:
        print(f"Error: No .mp3 file found in {episode_dir}", file=sys.stderr)
        return 1
    audio_file = audio_files[0]

    # Find feed.xml
    if args.feed:
        feed_path = Path(args.feed).resolve()
    else:
        # Auto-detect: look for podcast/feed.xml relative to episode
        feed_path = episode_dir.parent.parent / "feed.xml"
        if not feed_path.exists():
            # Try one more level up (for series episodes)
            feed_path = episode_dir.parent.parent.parent / "feed.xml"

    if not feed_path.exists():
        print(f"Error: feed.xml not found at {feed_path}", file=sys.stderr)
        return 1

    log(f"Episode: {episode_dir.name}")
    log(f"Metadata: {metadata_path}")
    log(f"Audio: {audio_file.name}")
    log(f"Feed: {feed_path}")
    log("")

    # Parse metadata
    metadata = parse_metadata_md(metadata_path)

    if "title" not in metadata:
        print("Error: Could not find title in metadata.md", file=sys.stderr)
        return 1

    log(f"Title: {metadata['title']}")
    log(f"Duration: {metadata.get('duration', 'auto-detect')}")
    log(f"File size: {metadata.get('file_size', 'auto-detect')} bytes")
    log(f"Keywords: {len(metadata.get('keywords', '').split(','))} keywords")
    log(f"Sources: {len(metadata.get('sources', []))} sources")
    log("")

    # Generate item XML
    item_xml = generate_item_xml(metadata, episode_dir, audio_file)

    if args.dry_run:
        log("=== DRY RUN: Generated XML ===")
        print(item_xml)
        log("")
        log("=== No changes written ===")
        return 0

    # Update feed.xml
    new_feed_content = update_feed_xml(feed_path, item_xml, metadata["title"])

    # Write back
    feed_path.write_text(new_feed_content)

    log(f"✓ Updated {feed_path}")
    log(f"✓ Added episode: {metadata['title']}")
    log(f"✓ Updated lastBuildDate: {format_rfc2822()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
