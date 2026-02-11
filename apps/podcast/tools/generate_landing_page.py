#!/usr/bin/env python3
"""
Generate episode landing page (Wave 4, Task C3.2).

Creates an HTML landing page for each episode containing:
- Episode overview and description
- Key timestamps with links
- Resources and tools mentioned
- Companion resource downloads
- Full transcript access
- Report link

Usage:
    python generate_landing_page.py ../pending-episodes/2025-12-26-topic-slug/
    python generate_landing_page.py ../pending-episodes/2025-12-26-topic-slug/ --dry-run

Prerequisites:
    - logs/metadata.md exists with required fields
    - report.md exists
    - Optional: companion/ directory with resources
    - Optional: *_transcript.json for transcript

The script will:
    1. Parse logs/metadata.md for episode metadata
    2. Read report.md for extended content
    3. Check for companion resources
    4. Generate responsive HTML landing page
    5. Save as index.html in episode directory
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
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

    # Extract audio info
    duration_match = re.search(r"\*\*Duration:\*\*\s*(\S+)", content)
    if duration_match:
        metadata["duration"] = duration_match.group(1).strip()

    # Extract description
    desc_match = re.search(
        r"^## Description \(Plain Text[^)]*\)\s*\n+(.+?)(?=\n#|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if desc_match:
        metadata["description"] = desc_match.group(1).strip()

    # Extract What You'll Learn
    learn_match = re.search(
        r"^## What You\'ll Learn.*?\n((?:[-*]\s+.+\n?)+)", content, re.MULTILINE
    )
    if learn_match:
        items = re.findall(r"[-*]\s+(.+)", learn_match.group(1))
        metadata["what_youll_learn"] = items

    # Extract Key Timestamps
    timestamps_match = re.search(
        r"^## Key Timestamps.*?\n((?:[-*]\s+.+\n?)+)", content, re.MULTILINE
    )
    if timestamps_match:
        timestamps = []
        for line in timestamps_match.group(1).split("\n"):
            match = re.match(
                r"[-*]\s+\*?\*?\[?(\d+:\d+(?::\d+)?)\]?\*?\*?\s*[-–]\s*(.+)", line
            )
            if match:
                timestamps.append(
                    {"time": match.group(1), "label": match.group(2).strip()}
                )
        metadata["timestamps"] = timestamps

    # Extract Resources
    resources_match = re.search(
        r"^## Resources.*?\n(.+?)(?=\n## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if resources_match:
        resources = []
        for line in resources_match.group(1).split("\n"):
            # Look for markdown links
            link_match = re.search(r"\[(.+?)\]\((.+?)\)", line)
            if link_match:
                resources.append(
                    {"name": link_match.group(1), "url": link_match.group(2)}
                )
        metadata["resources"] = resources

    # Extract keywords
    keywords_match = re.search(
        r"^## Keywords\s*\n+(.+?)(?=\n#|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if keywords_match:
        metadata["keywords"] = keywords_match.group(1).strip()

    return metadata


def get_companion_resources(episode_dir: Path) -> list:
    """Find companion resources in the episode directory."""
    resources = []
    companion_dir = episode_dir / "companion"

    if companion_dir.exists():
        for file in companion_dir.iterdir():
            if file.suffix in [".md", ".pdf", ".png", ".jpg"]:
                resource_type = "Document"
                if "summary" in file.name.lower():
                    resource_type = "One-Page Summary"
                elif "checklist" in file.name.lower():
                    resource_type = "Action Checklist"
                elif "framework" in file.name.lower():
                    resource_type = "Framework Guide"
                elif file.suffix in [".png", ".jpg"]:
                    resource_type = "Diagram"

                resources.append(
                    {
                        "name": resource_type,
                        "file": file.name,
                        "path": f"companion/{file.name}",
                    }
                )

    return resources


def get_transcript_preview(episode_dir: Path, max_chars: int = 500) -> str:
    """Get a preview of the transcript."""
    transcript_files = list(episode_dir.glob("*_transcript.json"))
    if not transcript_files:
        return None

    try:
        with open(transcript_files[0]) as f:
            data = json.load(f)
            if "text" in data:
                text = data["text"][:max_chars]
                return text + "..." if len(data["text"]) > max_chars else text
    except:
        pass
    return None


def generate_html(
    metadata: dict,
    episode_dir: Path,
    companion_resources: list,
    transcript_preview: str,
) -> str:
    """Generate the HTML landing page."""
    title = escape(metadata.get("title", "Episode"))
    description = escape(metadata.get("description", ""))
    duration = metadata.get("duration", "")
    pub_date = metadata.get("pub_date", "")
    series = metadata.get("series_name", "")

    # Get episode slug for URLs
    slug = episode_dir.name

    # Build base URL
    base_url = f"https://research.yuda.me/podcast/episodes/{slug}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | Yudame Research Podcast</title>
    <meta name="description" content="{description[:160]}">
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{description[:160]}">
    <meta property="og:type" content="article">
    <meta property="og:url" content="{base_url}/">
    <style>
        :root {{
            --primary: #2c3e50;
            --secondary: #3498db;
            --background: #faf8f5;
            --text: #333;
            --border: #e0e0e0;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Georgia', serif;
            line-height: 1.7;
            color: var(--text);
            background: var(--background);
            max-width: 800px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }}
        header {{
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 2px solid var(--primary);
        }}
        h1 {{
            font-size: 2rem;
            color: var(--primary);
            margin-bottom: 0.5rem;
        }}
        .meta {{
            color: #666;
            font-size: 0.9rem;
        }}
        .meta span {{ margin-right: 1rem; }}
        section {{
            margin: 2rem 0;
            padding: 1.5rem;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h2 {{
            font-size: 1.3rem;
            color: var(--primary);
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--border);
        }}
        ul {{ list-style: none; }}
        li {{ margin: 0.5rem 0; padding-left: 1.5rem; position: relative; }}
        li::before {{
            content: "•";
            color: var(--secondary);
            position: absolute;
            left: 0;
        }}
        a {{
            color: var(--secondary);
            text-decoration: none;
        }}
        a:hover {{ text-decoration: underline; }}
        .timestamps li::before {{ content: ""; }}
        .timestamps .time {{
            font-family: monospace;
            background: #f0f0f0;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            margin-right: 0.5rem;
        }}
        .cta {{
            display: inline-block;
            background: var(--secondary);
            color: white;
            padding: 0.75rem 1.5rem;
            border-radius: 4px;
            margin: 0.5rem 0.5rem 0.5rem 0;
            transition: background 0.2s;
        }}
        .cta:hover {{
            background: var(--primary);
            text-decoration: none;
        }}
        .transcript-preview {{
            background: #f9f9f9;
            padding: 1rem;
            border-left: 3px solid var(--secondary);
            font-style: italic;
            color: #555;
        }}
        footer {{
            margin-top: 3rem;
            padding-top: 1rem;
            border-top: 1px solid var(--border);
            text-align: center;
            font-size: 0.85rem;
            color: #666;
        }}
        @media (max-width: 600px) {{
            h1 {{ font-size: 1.5rem; }}
            section {{ padding: 1rem; }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>{title}</h1>
        <div class="meta">
"""

    if series:
        html += f"            <span>Series: {escape(series)}</span>\n"
    if duration:
        html += f"            <span>Duration: {escape(duration)}</span>\n"
    if pub_date:
        html += f"            <span>Published: {escape(pub_date)}</span>\n"

    html += """        </div>
    </header>

    <section>
        <h2>Overview</h2>
"""
    html += f"        <p>{description}</p>\n"
    html += """    </section>
"""

    # What You'll Learn
    if metadata.get("what_youll_learn"):
        html += """
    <section>
        <h2>What You'll Learn</h2>
        <ul>
"""
        for item in metadata["what_youll_learn"]:
            html += f"            <li>{escape(item)}</li>\n"
        html += """        </ul>
    </section>
"""

    # Key Timestamps
    if metadata.get("timestamps"):
        html += """
    <section>
        <h2>Key Timestamps</h2>
        <ul class="timestamps">
"""
        for ts in metadata["timestamps"]:
            html += f'            <li><span class="time">{escape(ts["time"])}</span> {escape(ts["label"])}</li>\n'
        html += """        </ul>
    </section>
"""

    # Resources
    if metadata.get("resources"):
        html += """
    <section>
        <h2>Resources & Tools</h2>
        <ul>
"""
        for resource in metadata["resources"]:
            html += f'            <li><a href="{escape(resource["url"])}" target="_blank">{escape(resource["name"])}</a></li>\n'
        html += """        </ul>
    </section>
"""

    # Companion Resources
    if companion_resources:
        html += """
    <section>
        <h2>Companion Downloads</h2>
        <ul>
"""
        for resource in companion_resources:
            html += f'            <li><a href="{escape(resource["path"])}">{escape(resource["name"])}</a></li>\n'
        html += """        </ul>
    </section>
"""

    # Transcript Preview
    if transcript_preview:
        html += f"""
    <section>
        <h2>Transcript Preview</h2>
        <div class="transcript-preview">
            {escape(transcript_preview)}
        </div>
        <p style="margin-top: 1rem;"><a href="{slug}_transcript.json">View full transcript</a></p>
    </section>
"""

    # CTAs
    html += f"""
    <section>
        <h2>Access the Full Episode</h2>
        <a href="{base_url}/{slug}.mp3" class="cta">Listen to Episode</a>
        <a href="{base_url}/report.md" class="cta">Read Full Report</a>
        <a href="{base_url}/sources.md" class="cta">View Sources</a>
    </section>

    <footer>
        <p>Yudame Research Podcast &copy; {datetime.now().year}</p>
        <p><a href="https://research.yuda.me/podcast/feed.xml">Subscribe via RSS</a></p>
    </footer>
</body>
</html>
"""

    return html


def main():
    parser = argparse.ArgumentParser(
        description="Generate episode landing page",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "episode_dir", help="Path to episode directory containing logs/metadata.md"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="index.html",
        help="Output filename (default: index.html)",
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Preview without writing files"
    )

    args = parser.parse_args()

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

    print(f"Episode: {episode_dir.name}")
    print(f"Metadata: {metadata_path}")

    # Parse metadata
    metadata = parse_metadata_md(metadata_path)

    if "title" not in metadata:
        print("Error: Could not find title in metadata.md", file=sys.stderr)
        return 1

    print(f"Title: {metadata['title']}")

    # Get companion resources
    companion_resources = get_companion_resources(episode_dir)
    print(f"Companion resources: {len(companion_resources)}")

    # Get transcript preview
    transcript_preview = get_transcript_preview(episode_dir)
    if transcript_preview:
        print("Transcript: Found")

    # Generate HTML
    html = generate_html(metadata, episode_dir, companion_resources, transcript_preview)

    if args.dry_run:
        print("\n=== GENERATED HTML ===\n")
        print(html[:2000] + "...\n[truncated]" if len(html) > 2000 else html)
        print("\n=== DRY RUN: No files written ===")
        return 0

    # Write output
    output_path = episode_dir / args.output
    output_path.write_text(html)
    print(f"\nCreated: {output_path}")
    print(f"URL: https://research.yuda.me/podcast/episodes/{episode_dir.name}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
