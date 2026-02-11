#!/usr/bin/env python3
"""
Generate NotebookLM prompt for manual audio generation.

Reads episode metadata and outputs a ready-to-paste prompt.

Usage:
    python notebooklm_prompt.py ../pending-episodes/YYYY-MM-DD-slug/
    python notebooklm_prompt.py ../pending-episodes/YYYY-MM-DD-slug/ --series "Series Name"
"""

import argparse
import sys
from pathlib import Path


def get_episode_title(episode_dir: Path, title_override: str = "") -> str:
    """Extract episode title from directory or content_plan.md."""
    if title_override:
        return title_override

    # Try to read from content_plan.md
    content_plan = episode_dir / "content_plan.md"
    if content_plan.exists():
        with open(content_plan) as f:
            for line in f:
                if line.startswith("| **Title**"):
                    # Extract from markdown table: | **Title** | Some Title |
                    parts = line.split("|")
                    if len(parts) >= 3:
                        return parts[2].strip()

    # Fallback to directory name
    return episode_dir.name.replace("-", " ").title()


def get_series_name(episode_dir: Path, series_override: str = "") -> str:
    """Extract series name from path or content_plan.md."""
    if series_override:
        return series_override

    # Try to read from content_plan.md
    content_plan = episode_dir / "content_plan.md"
    if content_plan.exists():
        with open(content_plan) as f:
            for line in f:
                if line.startswith("| **Series**"):
                    parts = line.split("|")
                    if len(parts) >= 3:
                        series = parts[2].strip()
                        if series and series.lower() not in [
                            "none",
                            "standalone",
                            "n/a",
                            "",
                        ]:
                            return series

    # Check if path contains a series folder
    # e.g., apps/podcast/pending-episodes/stablecoin-series/episode-5-xxx
    parent = episode_dir.parent.name
    if "series" in parent.lower() or parent != "pending-episodes":
        return parent.replace("-", " ").title()

    return ""


def generate_prompt(episode_title: str, series_name: str = "") -> str:
    """Generate the NotebookLM prompt."""
    series_intro = f" from our {series_name} series" if series_name else ""
    series_open = f" from our {series_name} series" if series_name else ""

    return f"""Create a two-host podcast episode on: {episode_title}{series_intro}

IMPORTANT: Follow the structure and guidance in content_plan.md carefully. It contains:
- The opening hook to use
- Key terms to define (with pronunciations)
- Studies to emphasize
- Three-section narrative arc (Foundation → Evidence → Application)
- Episode Structure Map with modes, durations, and transitions
- Signposting language for transitions between sections
- Closing callback and sign-off

STRUCTURAL GUIDANCE:
- Follow the Episode Structure Map in content_plan.md for section order, modes, and timing
- Use the signposting language provided for transitions: preview the structure at the start ("In this episode, we'll first explore X, then Y, and finally Z"), signal mode switches clearly, and use progress markers
- Follow the Depth Budget - allocate time to themes as specified, don't let any major theme get compressed at the end
- Build toward the resolution identified in content_plan.md - each section should raise the stakes or deepen understanding

DIALOGUE DYNAMICS - CRITICAL:
- Find the "Counterpoint Moments" section in content_plan.md
- At each counterpoint moment, speakers must TAKE POSITIONS and DISAGREE respectfully
- Speaker A should argue their assigned position; Speaker B should actively push back with their assigned counter-position
- This must be genuine disagreement, NOT collaborative exploration where both hosts present views neutrally
- Use phrases like: "Wait, but doesn't that contradict...", "I disagree because...", "I see it differently - here's why...", "Let me push back on that..."
- After debating, resolve through synthesis: "Both perspectives have merit when you consider..."
- Target 2-3 counterpoint moments throughout the episode

Brand elements:
- Producer: Valor Engels
- Open with: "Welcome to Yuda Me Research{series_open} by Valor Engels..."
- Close with: "Find full research and sources at research dot yuda dot me - that's Y-U-D-A dot M-E"

EPISODE ARC:
- Opening (3-5 min): Hook with specific stat/story, define the problem, preview structure
- Middle (20-30 min): Build from foundation to evidence to application with clear mode-switching
- Closing (3-5 min): Synthesize key takeaways (state 1-3 explicitly), callback to opening hook, call-to-action

Tone: Intellectually rigorous but accessible - two experts having a genuine conversation, making complex research understandable.

Style guidelines:
- Spell out acronyms on first use: "High-Intensity Interval Training, or HIIT"
- Define technical terms before building on them
- Use specific numbers with context (sample sizes, effect sizes, percentages)
- Distinguish correlation from causation
- Make statistics meaningful through comparisons
- Include human elements when the research contains them

Avoid:
- Undefined jargon
- Fabricated examples (use only what's in the source material)
- Over-hedging that obscures findings
- Repeating context unnecessarily
- Collaborative framing of counterpoints (hosts must actually disagree)"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate NotebookLM prompt for manual audio generation"
    )
    parser.add_argument("episode_dir", type=Path, help="Path to episode directory")
    parser.add_argument(
        "--series",
        type=str,
        default="",
        help="Series name (auto-detected if not specified)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="Episode title (auto-detected if not specified)",
    )
    parser.add_argument(
        "--copy", action="store_true", help="Copy prompt to clipboard (macOS only)"
    )

    args = parser.parse_args()

    episode_dir = args.episode_dir
    if not episode_dir.exists():
        print(f"Error: Directory not found: {episode_dir}", file=sys.stderr)
        sys.exit(1)

    # Get episode metadata
    episode_title = get_episode_title(episode_dir, args.title)
    series_name = get_series_name(episode_dir, args.series)

    # Check required files
    source_files = [
        episode_dir / "research" / "p1-brief.md",
        episode_dir / "report.md",
        episode_dir / "research" / "p3-briefing.md",
        episode_dir / "sources.md",
        episode_dir / "content_plan.md",
    ]

    missing = [f for f in source_files if not f.exists()]

    # Print header
    print("=" * 60)
    print("NOTEBOOKLM MANUAL AUDIO GENERATION")
    print("=" * 60)
    print(f"\nEpisode: {episode_title}")
    if series_name:
        print(f"Series: {series_name}")
    print(f"Directory: {episode_dir}")

    # Print file checklist
    print(
        f"\n📁 Files to Upload ({len(source_files) - len(missing)}/{len(source_files)} ready):"
    )
    for f in source_files:
        status = "✓" if f.exists() else "✗ MISSING"
        print(f"  {status} {f.name}")

    if missing:
        print(f"\n⚠️  Missing {len(missing)} required file(s). Generate them first.")
        sys.exit(1)

    # Generate and print prompt
    prompt = generate_prompt(episode_title, series_name)

    print("\n" + "=" * 60)
    print("📋 NOTEBOOKLM PROMPT (copy-paste ready):")
    print("=" * 60)
    print()
    print(prompt)
    print()
    print("=" * 60)
    print("\n⚙️  Settings: Format: Deep Dive | Length: Long")
    print("\n🔗 Open: https://notebooklm.google.com/")

    # Copy to clipboard if requested
    if args.copy:
        try:
            import subprocess

            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(prompt.encode("utf-8"))
            print("\n✓ Prompt copied to clipboard!")
        except Exception as e:
            print(f"\n⚠️  Could not copy to clipboard: {e}")


if __name__ == "__main__":
    main()
