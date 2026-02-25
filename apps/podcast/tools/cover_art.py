#!/usr/bin/env python3
"""
Generate and brand podcast episode cover art in one step.

Combines AI image generation + branding overlay into a single command.

Usage:
    python cover_art.py ../pending-episodes/YYYY-MM-DD-slug/
    python cover_art.py ../pending-episodes/YYYY-MM-DD-slug/ --series "Series Name"

Requirements:
    - OPENROUTER_API_KEY in environment
    - Playfair Display fonts installed (see add_logo_watermark.py --check-fonts)
"""

import argparse
import subprocess
import sys
from pathlib import Path


def get_episode_metadata(episode_dir: Path) -> tuple[str, str]:
    """Extract episode title and series name from content_plan.md or directory."""
    title = ""
    series = ""

    content_plan = episode_dir / "content_plan.md"
    if content_plan.exists():
        with open(content_plan) as f:
            for line in f:
                if line.startswith("| **Title**"):
                    parts = line.split("|")
                    if len(parts) >= 3:
                        title = parts[2].strip()
                elif line.startswith("| **Series**"):
                    parts = line.split("|")
                    if len(parts) >= 3:
                        s = parts[2].strip()
                        if s and s.lower() not in ["none", "standalone", "n/a", ""]:
                            series = s

    # Fallbacks
    if not title:
        title = episode_dir.name.replace("-", " ").title()

    if not series:
        parent = episode_dir.parent.name
        if "series" in parent.lower() or parent != "episodes":
            series = parent.replace("-", " ").title()

    return title, series


def run_command(cmd: list, description: str, verbose: bool = True) -> bool:
    """Run a command and return success status."""
    if verbose:
        print(f"\n→ {description}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ✗ Failed: {result.stderr}")
        return False

    if verbose and result.stdout:
        # Print last few lines of output
        lines = result.stdout.strip().split("\n")
        for line in lines[-3:]:
            if line.strip():
                print(f"  {line}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate and brand podcast episode cover art"
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
        "--episode-text",
        type=str,
        default="",
        help="Text for episode label (e.g., 'Ep 5 - Topic')",
    )
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="Skip AI generation, only apply branding to existing cover.png",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")

    args = parser.parse_args()

    episode_dir = args.episode_dir.resolve()
    tools_dir = Path(__file__).parent

    if not episode_dir.exists():
        print(f"Error: Directory not found: {episode_dir}")
        return 1

    # Get metadata
    title, series = get_episode_metadata(episode_dir)
    if args.title:
        title = args.title
    if args.series:
        series = args.series

    # Determine episode text for branding
    episode_text = args.episode_text
    if not episode_text and series:
        # Try to extract episode number from directory name
        dir_name = episode_dir.name.lower()
        ep_num = ""
        if "episode-" in dir_name:
            parts = dir_name.split("episode-")
            if len(parts) > 1:
                num_part = parts[1].split("-")[0]
                if num_part.isdigit():
                    ep_num = num_part

        if ep_num:
            # Shorten title for episode text
            short_title = title.split(":")[0] if ":" in title else title
            if len(short_title) > 25:
                short_title = short_title[:22] + "..."
            episode_text = f"Ep {ep_num} - {short_title}"

    cover_path = episode_dir / "cover.png"
    log_dir = episode_dir / "logs"

    if not args.quiet:
        print("=" * 60)
        print("PODCAST COVER ART GENERATOR")
        print("=" * 60)
        print(f"Episode: {title}")
        if series:
            print(f"Series: {series}")
        print(f"Directory: {episode_dir}")
        print("=" * 60)

    # Step 1: Generate AI cover art (unless skipping)
    if not args.skip_generate:
        # Check report.md exists
        report_path = episode_dir / "report.md"
        if not report_path.exists():
            print(f"Error: report.md not found in {episode_dir}")
            print(
                "Generate report.md first, or use --skip-generate to brand existing cover."
            )
            return 1

        generate_cmd = [
            "uv",
            "run",
            "python",
            str(tools_dir / "generate_cover.py"),
            str(episode_dir),
            "--auto",
            "--log-dir",
            str(log_dir),
        ]
        if args.quiet:
            generate_cmd.append("--quiet")

        if not run_command(generate_cmd, "Generating AI cover art...", not args.quiet):
            return 1

    # Check cover.png exists before branding
    if not cover_path.exists():
        print(f"Error: cover.png not found at {cover_path}")
        return 1

    # Step 2: Apply branding (use uv run for pillow dependency)
    brand_cmd = [
        "uv",
        "run",
        "python",
        str(tools_dir / "add_logo_watermark.py"),
        str(cover_path),
    ]

    if series:
        brand_cmd.extend(["--series", series])

    if episode_text:
        brand_cmd.extend(["--episode", episode_text])

    brand_cmd.extend(["--log-dir", str(log_dir)])

    if args.quiet:
        brand_cmd.append("--quiet")

    if not run_command(brand_cmd, "Applying podcast branding...", not args.quiet):
        return 1

    # Verify final output
    if cover_path.exists():
        size_kb = cover_path.stat().st_size / 1024
        if not args.quiet:
            print(f"\n{'=' * 60}")
            print(f"✓ Cover art ready: {cover_path}")
            print(f"  Size: {size_kb:.0f} KB")
            print("=" * 60)
        return 0
    else:
        print("Error: Cover art generation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
