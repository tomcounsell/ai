#!/usr/bin/env python3
"""
Add podcast branding overlays to episode cover art.

Redesigned for light cream backgrounds per design spec:
- Auto-detects background brightness for text color
- Uses Playfair Display (brand) + Inter (body) typography
- Clean editorial aesthetic

Usage:
    python add_logo_watermark.py <cover_image> --series "Series Name" --episode "Episode Title"
    python add_logo_watermark.py cover.png --series "Series" --episode "Topic" --quiet

Requirements:
    pip install pillow
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat
except ImportError:
    print("Error: pillow package not installed. Run: pip install pillow")
    sys.exit(1)


# Design spec colors
COLORS = {
    "black": (0, 0, 0),
    "dark_gray": (58, 58, 58),  # #3A3A3A
    "medium_gray": (107, 107, 107),  # #6B6B6B
    "salmon": (232, 180, 168),  # #E8B4A8
    "cream": (245, 241, 232),  # #F5F1E8
    "white": (255, 255, 255),
}


def get_background_brightness(image, sample_region="top"):
    """
    Detect average brightness of image background.

    Args:
        image: PIL Image object
        sample_region: 'top' samples top third, 'full' samples entire image

    Returns:
        Float 0-255, higher = brighter
    """
    if sample_region == "top":
        # Sample top third where text will be placed
        box = (0, 0, image.width, image.height // 3)
        region = image.crop(box)
    else:
        region = image

    # Convert to grayscale and get mean
    gray = region.convert("L")
    stat = ImageStat.Stat(gray)
    return stat.mean[0]


def load_font(font_paths, size, fallback_paths=None):
    """Try to load font from list of paths, with fallbacks."""
    all_paths = font_paths + (fallback_paths or [])

    for font_path in all_paths:
        try:
            return ImageFont.truetype(Path(font_path).expanduser(), size)
        except:
            continue

    return ImageFont.load_default()


# Font paths used by branding
PLAYFAIR_SEMIBOLD_PATHS = [
    "~/Library/Fonts/playfair-display-v40-latin-600.ttf",
    "/Library/Fonts/playfair-display-v40-latin-600.ttf",
    "~/Library/Fonts/PlayfairDisplay-SemiBold.ttf",
    "/Library/Fonts/PlayfairDisplay-SemiBold.ttf",
]
PLAYFAIR_ITALIC_PATHS = [
    "~/Library/Fonts/playfair-display-v40-latin-italic.ttf",
    "/Library/Fonts/playfair-display-v40-latin-italic.ttf",
    "~/Library/Fonts/PlayfairDisplay-Italic.ttf",
    "/Library/Fonts/PlayfairDisplay-Italic.ttf",
]


def check_fonts():
    """
    Check if required Playfair Display fonts are installed.
    Returns True if all fonts are available, False otherwise.
    """
    print("Checking required fonts...\n")

    results = []

    # Check Playfair Display SemiBold (600)
    semibold_found = False
    for path in PLAYFAIR_SEMIBOLD_PATHS:
        try:
            font = ImageFont.truetype(Path(path).expanduser(), 48)
            name = font.getname()
            print(f"✓ Playfair Display SemiBold")
            print(f"  Path: {path}")
            print(f"  Font: {name[0]} {name[1]}")
            semibold_found = True
            break
        except:
            continue

    if not semibold_found:
        print("✗ Playfair Display SemiBold - NOT FOUND")
        print("  Searched paths:")
        for path in PLAYFAIR_SEMIBOLD_PATHS:
            print(f"    - {path}")
    results.append(("Playfair Display SemiBold", semibold_found))

    print()

    # Check Playfair Display Italic
    italic_found = False
    for path in PLAYFAIR_ITALIC_PATHS:
        try:
            font = ImageFont.truetype(Path(path).expanduser(), 48)
            name = font.getname()
            print(f"✓ Playfair Display Italic")
            print(f"  Path: {path}")
            print(f"  Font: {name[0]} {name[1]}")
            italic_found = True
            break
        except:
            continue

    if not italic_found:
        print("✗ Playfair Display Italic - NOT FOUND")
        print("  Searched paths:")
        for path in PLAYFAIR_ITALIC_PATHS:
            print(f"    - {path}")
    results.append(("Playfair Display Italic", italic_found))

    print()

    # Summary
    all_found = all(r[1] for r in results)
    if all_found:
        print("✓ All required fonts are installed!")
    else:
        print("✗ Some fonts are missing. Install with:")
        print()
        print("  mkdir -p ~/Library/Fonts && cd ~/Library/Fonts")
        print(
            '  curl -L -o playfair.zip "https://gwfh.mranftl.com/api/fonts/playfair-display?download=zip&subsets=latin&variants=600,italic"'
        )
        print("  unzip -o playfair.zip")

    return all_found


def add_branding(
    cover_path,
    series_text=None,
    episode_text=None,
    show_logo=True,
    logo_path=None,
    verbose=True,
    log_file=None,
):
    """
    Add clean branding overlays to cover image.

    Design approach:
    - Auto-detect background brightness
    - Black text on light backgrounds, white on dark
    - Playfair Display for brand, Inter for content
    - Clean editorial typography hierarchy
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    cover_path = Path(cover_path)

    if not cover_path.exists():
        log(f"Error: Cover image not found: {cover_path}")
        return None

    log(f"Loading cover: {cover_path}")

    # Load image
    cover = Image.open(cover_path).convert("RGBA")
    width, height = cover.size

    # Detect background brightness
    brightness = get_background_brightness(cover, "top")
    is_light_bg = brightness > 128
    log(
        f"Background brightness: {brightness:.0f} ({'light' if is_light_bg else 'dark'})"
    )

    # Choose text colors based on background
    if is_light_bg:
        text_primary = COLORS["black"]
        text_secondary = COLORS["dark_gray"]
        text_tertiary = COLORS["medium_gray"]
        use_shadow = False
    else:
        text_primary = COLORS["white"]
        text_secondary = (220, 220, 220)
        text_tertiary = (180, 180, 180)
        use_shadow = True

    # Typography sizing (matching website header proportions)
    # Logo is larger, text sized independently
    logo_size = int(width * 0.064)  # ~66px at 1024 - 1.6x larger
    brand_size = int(width * 0.04)  # ~41px at 1024 - keep text size as is
    series_size = int(brand_size * 0.9)
    episode_size = int(brand_size * 0.8)

    # Spacing (8px base from design spec, scaled)
    base_unit = width / 128  # 8px at 1024
    padding = int(base_unit * 5)  # 40px at 1024
    line_gap = int(base_unit * 1)  # 8px at 1024
    section_gap = int(base_unit * 1.5)  # 12px at 1024

    # Load fonts (using module-level constants)
    fallback_paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    fallback_italic_paths = [
        "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
        "/System/Library/Fonts/Times.ttc",
    ]

    brand_font = load_font(PLAYFAIR_SEMIBOLD_PATHS, brand_size, fallback_paths)
    series_font = load_font(
        PLAYFAIR_ITALIC_PATHS, series_size, fallback_italic_paths + fallback_paths
    )
    episode_font = load_font(
        PLAYFAIR_ITALIC_PATHS, episode_size, fallback_italic_paths + fallback_paths
    )

    # Create drawing context
    img = cover.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Current Y position for text layout
    current_y = padding
    current_x = padding

    # Draw logo + brand name (like header)
    brand_text = "Yudame Research"
    logo_height_target = logo_size  # Logo is the anchor, text sized relative to it

    # Load and place logo if available
    if show_logo and logo_path:
        logo_file = Path(logo_path)
        if logo_file.exists():
            logo_img = Image.open(logo_file).convert("RGBA")
            # Scale logo to match brand text height
            logo_aspect = logo_img.width / logo_img.height
            logo_h = logo_height_target
            logo_w = int(logo_h * logo_aspect)
            logo_img = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

            # Paste logo
            img.paste(logo_img, (current_x, current_y), logo_img)
            current_x += logo_w + int(base_unit * 1.5)  # Gap after logo

    # Draw brand text - vertically centered with logo
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_text_height = brand_bbox[3] - brand_bbox[1]
    # Center text with logo, accounting for font metrics (top of bbox is offset from origin)
    logo_center_y = current_y + logo_height_target // 2
    text_center_offset = (
        brand_text_height // 2 + brand_bbox[1]
    )  # Account for bbox top offset
    brand_y = logo_center_y - text_center_offset

    if use_shadow:
        shadow_offset = max(2, int(base_unit * 0.25))
        draw.text(
            (current_x + shadow_offset, brand_y + shadow_offset),
            brand_text,
            fill=(0, 0, 0, 128),
            font=brand_font,
        )
    draw.text((current_x, brand_y), brand_text, fill=text_primary, font=brand_font)

    # Move down for series/episode
    current_y += logo_height_target + section_gap
    current_x = padding  # Reset X

    # Draw series name (if provided)
    if series_text:
        if use_shadow:
            draw.text(
                (current_x + shadow_offset, current_y + shadow_offset),
                series_text,
                fill=(0, 0, 0, 100),
                font=series_font,
            )
        draw.text(
            (current_x, current_y), series_text, fill=text_secondary, font=series_font
        )

        bbox = draw.textbbox((0, 0), series_text, font=series_font)
        current_y += (bbox[3] - bbox[1]) + line_gap

    # Draw episode text (if provided)
    if episode_text:
        if use_shadow:
            draw.text(
                (current_x + shadow_offset, current_y + shadow_offset),
                episode_text,
                fill=(0, 0, 0, 80),
                font=episode_font,
            )
        draw.text(
            (current_x, current_y), episode_text, fill=text_tertiary, font=episode_font
        )

    # Save
    output_path = cover_path.parent / f"{cover_path.stem}_branded{cover_path.suffix}"
    img.convert("RGB").save(output_path, "PNG", quality=95)
    log(f"✓ Branded cover saved to: {output_path}")

    # Replace original
    cover_path.unlink()
    output_path.rename(cover_path)
    log(f"✓ Original replaced: {cover_path}")

    return cover_path


def main():
    parser = argparse.ArgumentParser(
        description="Add podcast branding to episode cover art",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python add_logo_watermark.py cover.png --series "Algorithms for Life" --episode "Spaced Repetition"
    python add_logo_watermark.py cover.png --episode "Standalone Episode" --no-logo
    python add_logo_watermark.py --check-fonts
        """,
    )
    parser.add_argument("cover", nargs="?", help="Path to cover image")
    parser.add_argument("--series", help="Series name (e.g., 'Algorithms for Life')")
    parser.add_argument("--episode", help="Episode title (e.g., 'Spaced Repetition')")
    parser.add_argument("--logo", help="Path to logo (default: ../yudame-logo.png)")
    parser.add_argument("--no-logo", action="store_true", help="Don't add logo")
    parser.add_argument("--log-dir", help="Directory for log files")
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    parser.add_argument(
        "--check-fonts",
        action="store_true",
        help="Check if required fonts are installed",
    )

    args = parser.parse_args()

    # Handle --check-fonts
    if args.check_fonts:
        success = check_fonts()
        return 0 if success else 1

    # Require cover path for branding operations
    if not args.cover:
        parser.error("cover path is required (or use --check-fonts)")
        return 1

    # Default logo path
    if not args.logo and not args.no_logo:
        script_dir = Path(__file__).parent
        args.logo = script_dir.parent / "yudame-logo.png"

    # Set up logging
    log_file = None
    if args.log_dir:
        log_dir = Path(args.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(log_dir / f"branding_log_{timestamp}.txt")

    result = add_branding(
        args.cover,
        series_text=args.series,
        episode_text=args.episode,
        show_logo=not args.no_logo,
        logo_path=args.logo if not args.no_logo else None,
        verbose=not args.quiet,
        log_file=log_file,
    )

    if not result:
        print("Branding failed")
        return 1

    if not args.quiet:
        print("\n✓ Done! Branding applied.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
