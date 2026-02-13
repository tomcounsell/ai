#!/usr/bin/env python3
"""
Create episode directory structure and template files.

Usage:
    # Public episode (default: yudame-research)
    python setup_episode.py --slug "topic-name" --title "Episode Title"

    # Private podcast episode
    python setup_episode.py --podcast stablecoin --slug "overview" --title "Stablecoin Overview"

    # Legacy standalone episode (date-prefixed)
    python setup_episode.py --date 2025-12-26 --slug "topic-name" --title "Episode Title"

    # Series episode (legacy)
    python setup_episode.py --slug "lifestyle" --title "Cardiovascular Health: Ep. 1" --series "cardiovascular-health-series" --episode-num 1

    # With research context
    python setup_episode.py --slug "topic" --title "Title" --context "Research focus and key questions"

Output:
    Creates directory structure with template files ready for podcast workflow.
    Includes episode_config.json snapshot from podcast configuration.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from episode_config import DEFAULT_CONFIG, save_config


def create_prompts_template(date: str, slug: str, title: str) -> str:
    """Generate logs/prompts.md template."""
    return f"""# Prompts Used for Episode: {title}

This document tracks all prompts used during the creation of this episode for reproducibility and learning.

**Note:** If a `research-prompt.md` exists in this directory, it contains the seed research ideas and objectives. The prompts below are the actual copy-paste-ready prompts used with deep research tools.

---

## Setup Phase

**Episode Details:**
- Date: {date}
- Slug: {slug}
- Title: {title}

---

## Deep Research Phase

### Tool Configuration

**Automated tools:**
- **Perplexity:** Academic & Official Sources (Phase 1 - always used, API-based)
- **GPT-Researcher:** Industry & Technical Sources (Phase 3 - API-based, uses OpenAI GPT-5.2)
- **Gemini Deep Research:** Strategic & Policy Sources (Phase 3 - API-based)

**Manual tools (user runs these):**
- **Claude:** Comprehensive Synthesis (Phase 3 - user pastes from https://claude.ai)
- **Grok:** Real-Time & Regional Sources (Phase 3 - user pastes from https://x.com/i/grok)

### Deep Research Prompts (Copy-Paste Ready)

**IMPORTANT:** These prompts use single newlines only to prevent accidental partial submissions when pasting into Chrome-based tools.

---

<!-- Research prompts will be added as they are used -->
"""


def create_brief_template(date: str, title: str, context: str = None) -> str:
    """Generate research/p1-brief.md template."""
    context_section = (
        context
        if context
        else "[High-level description of what this episode will research]"
    )

    return f"""# Research Brief: {title}

**Date:** {date}
**Episode:** {title}

---

## Research Topic

{context_section}

## Key Questions

- [Question 1]
- [Question 2]
- [Question 3]

## Context

[Any relevant context or background for the research]

---

**Next Steps:**
1. Create Phase 1 academic research prompt for Perplexity
2. Run Perplexity research → save to research/p2-perplexity.md
3. Analyze results for question discovery
4. Create targeted Phase 3 prompts for other tools
"""


def create_sources_template(date: str, title: str) -> str:
    """Generate sources.md template."""
    return f"""# Sources for {title}

## Research Tools Used
- Perplexity (Academic & Official - automated)
- Grok (Real-Time & Regional - manual)
- GPT-Researcher (Industry & Technical - OpenAI GPT-5.2 - automated)
- Gemini Deep Research (Strategic & Policy - automated)

## Verified Sources by Tier

### Tier 1: Meta-analyses, Systematic Reviews, Official Statistics
<!-- Add after cross-validation -->

### Tier 2: RCTs, Large Studies, Government Reports
<!-- Add after cross-validation -->

### Tier 3: Case Studies, Industry Reports, News
<!-- Add after cross-validation -->

---

## Notes
- Research compiled: {date}
- Sources cross-validated across multiple tools
- Conflicting sources noted in research/p3-briefing.md
"""


def get_episode_path(
    base_dir: Path,
    date: str,
    slug: str,
    podcast: str = None,
    series: str = None,
    episode_num: int = None,
) -> Path:
    """Determine episode directory path based on podcast, series, or standalone."""
    if podcast:
        # Podcast episode: {podcast-slug}/{episode-slug}/
        return base_dir / "podcast" / "pending-episodes" / podcast / slug
    elif series:
        # Series episode: series-name/epX-slug/
        ep_dir = f"ep{episode_num}-{slug}" if episode_num else slug
        return base_dir / "podcast" / "pending-episodes" / series / ep_dir
    else:
        # Standalone episode: YYYY-MM-DD-slug/
        return base_dir / "podcast" / "pending-episodes" / f"{date}-{slug}"


def load_podcast_config(podcast_slug: str) -> dict:
    """Load config from database for the given podcast slug."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    import django

    django.setup()

    from apps.podcast.models import Podcast

    podcast = Podcast.objects.select_related("config").get(slug=podcast_slug)

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


def setup_episode(
    date: str,
    slug: str,
    title: str,
    podcast: str = None,
    series: str = None,
    episode_num: int = None,
    context: str = None,
    base_dir: Path = None,
    quiet: bool = False,
) -> Path:
    """Create episode directory structure and template files."""

    def log(msg):
        if not quiet:
            print(msg)

    # Determine base directory (repo root)
    if base_dir is None:
        # Assume we're in apps/podcast/tools/
        base_dir = Path(__file__).parent.parent.parent

    # Get episode path
    episode_dir = get_episode_path(base_dir, date, slug, podcast, series, episode_num)

    # Check if already exists
    if episode_dir.exists():
        log(f"⚠️  Directory already exists: {episode_dir}")
        log("   Skipping creation, checking for missing files...")
    else:
        log(f"Creating episode directory: {episode_dir}")

    # Create directory structure
    dirs = [
        episode_dir / "research" / "documents",
        episode_dir / "logs",
        episode_dir / "tmp",
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    log("✓ Created directories: research/, logs/, tmp/")

    # Save episode config snapshot
    config_file = episode_dir / "episode_config.json"
    if not config_file.exists():
        if podcast:
            # Load config from database
            try:
                config = load_podcast_config(podcast)
                log(f"✓ Loaded config from database for podcast: {podcast}")
            except Exception as e:
                log(f"⚠️  Could not load podcast config: {e}")
                log("   Using default public config")
                config = DEFAULT_CONFIG.copy()
        else:
            # Use default public config
            config = DEFAULT_CONFIG.copy()

        save_config(episode_dir, config)
        log("✓ Saved episode_config.json")
    else:
        log("✓ episode_config.json already exists")

    # Create template files
    files_created = []

    # logs/prompts.md
    prompts_path = episode_dir / "logs" / "prompts.md"
    if not prompts_path.exists():
        prompts_path.write_text(create_prompts_template(date, slug, title))
        files_created.append("logs/prompts.md")

    # research/p1-brief.md
    brief_path = episode_dir / "research" / "p1-brief.md"
    if not brief_path.exists():
        brief_path.write_text(create_brief_template(date, title, context))
        files_created.append("research/p1-brief.md")

    # sources.md
    sources_path = episode_dir / "sources.md"
    if not sources_path.exists():
        sources_path.write_text(create_sources_template(date, title))
        files_created.append("sources.md")

    if files_created:
        log(f"✓ Created files: {', '.join(files_created)}")
    else:
        log("✓ All template files already exist")

    # Summary
    log("")
    log(f"Episode setup complete: {episode_dir.relative_to(base_dir)}")
    log("")
    log("Directory structure:")
    log(f"  {episode_dir.name}/")
    log("  ├── research/")
    log("  │   ├── documents/")
    log("  │   └── p1-brief.md")
    log("  ├── logs/")
    log("  │   └── prompts.md")
    log("  ├── tmp/")
    log("  ├── sources.md")
    log("  └── episode_config.json")

    return episode_dir


def main():
    parser = argparse.ArgumentParser(
        description="Create podcast episode directory structure and template files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Public episode (default yudame-research)
  %(prog)s --slug "sleep-optimization" --title "Sleep Optimization for Athletes"

  # Private podcast episode
  %(prog)s --podcast stablecoin --slug "overview" --title "Stablecoin Overview"

  # Legacy standalone episode (date-prefixed)
  %(prog)s --date 2025-12-26 --slug "sleep-optimization" --title "Sleep Optimization"

  # Series episode (legacy)
  %(prog)s --slug "lifestyle" --title "Cardiovascular Health: Ep. 1" --series "cardiovascular-health-series" --episode-num 1

  # With research context
  %(prog)s --slug "burnout" --title "Educator Burnout" --context "Research early childhood educator burnout interventions"
""",
    )

    parser.add_argument(
        "--podcast",
        "-p",
        help="Podcast slug (e.g., 'stablecoin'). Loads config from database.",
    )
    parser.add_argument(
        "--date",
        "-d",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Episode date in YYYY-MM-DD format (default: today). Used for legacy standalone directories.",
    )
    parser.add_argument(
        "--slug",
        "-s",
        required=True,
        help="URL-friendly episode slug (e.g., 'sleep-optimization')",
    )
    parser.add_argument("--title", "-t", required=True, help="Full episode title")
    parser.add_argument(
        "--series", help="Series directory name (e.g., 'cardiovascular-health-series')"
    )
    parser.add_argument(
        "--episode-num", "-n", type=int, help="Episode number within series"
    )
    parser.add_argument(
        "--context", "-c", help="Research context or focus for p1-brief.md"
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")

    args = parser.parse_args()

    # Validate series arguments
    if args.series and not args.episode_num:
        parser.error("--episode-num is required when --series is specified")

    if args.episode_num and not args.series:
        parser.error("--series is required when --episode-num is specified")

    # Validate podcast vs series (mutually exclusive)
    if args.podcast and args.series:
        parser.error("--podcast and --series are mutually exclusive")

    try:
        setup_episode(
            date=args.date,
            slug=args.slug,
            title=args.title,
            podcast=args.podcast,
            series=args.series,
            episode_num=args.episode_num,
            context=args.context,
            quiet=args.quiet,
        )

        if not args.quiet:
            print("")
            print("Next steps:")
            print("  1. Run Perplexity research (Phase 2)")
            print("  2. Results go in research/p2-perplexity.md")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
