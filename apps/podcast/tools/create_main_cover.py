#!/usr/bin/env python3
"""
Create the main podcast cover art by compositing headshot with branding.

Usage:
    python create_main_cover.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Paths
SCRIPT_DIR = Path(__file__).parent
PODCAST_DIR = SCRIPT_DIR.parent
HEADSHOT_PATH = PODCAST_DIR / "valor-headshot.png"
LOGO_PATH = PODCAST_DIR / "yudame-logo.png"
OUTPUT_PATH = PODCAST_DIR / "cover.png"

# Design spec colors
CREAM = (245, 241, 232)  # #F5F1E8
BLACK = (0, 0, 0)
DARK_GRAY = (58, 58, 58)  # #3A3A3A
MEDIUM_GRAY = (107, 107, 107)  # #6B6B6B

# Canvas size (Apple Podcasts requirement)
CANVAS_SIZE = 3000

# Font paths
PLAYFAIR_SEMIBOLD_PATHS = [
    "~/Library/Fonts/playfair-display-v40-latin-600.ttf",
    "/Library/Fonts/playfair-display-v40-latin-600.ttf",
    "~/Library/Fonts/PlayfairDisplay-SemiBold.ttf",
]
PLAYFAIR_ITALIC_PATHS = [
    "~/Library/Fonts/playfair-display-v40-latin-italic.ttf",
    "/Library/Fonts/playfair-display-v40-latin-italic.ttf",
    "~/Library/Fonts/PlayfairDisplay-Italic.ttf",
]


def load_font(font_paths, size):
    """Try to load font from list of paths."""
    for font_path in font_paths:
        try:
            return ImageFont.truetype(Path(font_path).expanduser(), size)
        except:
            continue
    return ImageFont.load_default()


def create_cover():
    print(f"Creating {CANVAS_SIZE}x{CANVAS_SIZE} podcast cover...")

    # Create canvas with cream background
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), CREAM)

    # Load headshot
    print(f"Loading headshot: {HEADSHOT_PATH}")
    headshot = Image.open(HEADSHOT_PATH).convert("RGBA")
    hs_w, hs_h = headshot.size
    print(f"  Headshot size: {hs_w}x{hs_h}")

    # Crop headshot to remove white background areas on bottom and right
    # Crop percentages from each edge
    crop_bottom_pct = 0.12  # Remove 12% from bottom
    crop_right_pct = 0.18  # Remove 18% from right
    crop_box = (
        0,  # left
        0,  # top
        int(hs_w * (1 - crop_right_pct)),  # right
        int(hs_h * (1 - crop_bottom_pct)),  # bottom
    )
    headshot_cropped = headshot.crop(crop_box)
    crop_w, crop_h = headshot_cropped.size
    print(f"  Cropped to: {crop_w}x{crop_h}")

    # Scale to fit canvas - make it larger to fill more space
    target_height = int(CANVAS_SIZE * 1.12)  # 112% of canvas height (will overflow)
    scale = target_height / crop_h
    new_w = int(crop_w * scale)
    new_h = int(crop_h * scale)
    headshot_scaled = headshot_cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)
    print(f"  Scaled to: {new_w}x{new_h}")

    # Position headshot - move left so more face is visible
    # X: position so right edge aligns with canvas right, with overflow
    # Y: push down so white background at bottom overflows off canvas
    hs_x = CANVAS_SIZE - int(new_w * 0.92)  # Show 92% of width (moved more left)
    hs_y = int(CANVAS_SIZE * 0.05)  # Push down a bit more

    print(f"  Position: ({hs_x}, {hs_y})")
    canvas.paste(headshot_scaled, (hs_x, hs_y), headshot_scaled)

    # Load logo
    print(f"Loading logo: {LOGO_PATH}")
    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo_size = int(CANVAS_SIZE * 0.09)  # 9% of canvas
    logo = logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)

    # Position logo at top left
    padding = int(CANVAS_SIZE * 0.04)  # 4% padding
    logo_x = padding
    logo_y = padding
    canvas.paste(logo, (logo_x, logo_y), logo)

    # Draw text
    draw = ImageDraw.Draw(canvas)

    # Load fonts
    brand_font_size = int(CANVAS_SIZE * 0.055)  # Brand name
    tagline_font_size = int(CANVAS_SIZE * 0.06)  # Tagline (larger, italics)
    name_font_size = int(CANVAS_SIZE * 0.05)  # Author name

    brand_font = load_font(PLAYFAIR_SEMIBOLD_PATHS, brand_font_size)
    tagline_font = load_font(PLAYFAIR_ITALIC_PATHS, tagline_font_size)
    name_font = load_font(PLAYFAIR_ITALIC_PATHS, name_font_size)

    # Brand name - next to logo
    brand_text = "Yudame Research"
    brand_x = logo_x + logo_size + int(CANVAS_SIZE * 0.02)
    # Vertically center with logo
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_text_height = brand_bbox[3] - brand_bbox[1]
    brand_y = logo_y + (logo_size - brand_text_height) // 2 - brand_bbox[1]
    draw.text((brand_x, brand_y), brand_text, fill=BLACK, font=brand_font)
    print(f"  Brand text at: ({brand_x}, {brand_y})")

    # Tagline - below logo, multi-line
    tagline_lines = ["Be the most", "prepared person", "in the room."]
    tagline_y = logo_y + logo_size + int(CANVAS_SIZE * 0.06)
    line_spacing = int(tagline_font_size * 1.2)

    for line in tagline_lines:
        draw.text((logo_x, tagline_y), line, fill=BLACK, font=tagline_font)
        tagline_y += line_spacing
    print(f"  Tagline positioned (aligned with logo at x={logo_x})")

    # Author name - halfway between tagline bottom and canvas bottom
    name_text = "Valor\nEngels"
    # tagline_y is now at the bottom of the last tagline line
    tagline_bottom = tagline_y  # This is where tagline ended
    # Position name halfway between tagline bottom and canvas bottom
    name_y = (
        tagline_bottom + (CANVAS_SIZE - tagline_bottom) // 2 - int(CANVAS_SIZE * 0.04)
    )
    draw.text((logo_x, name_y), name_text, fill=BLACK, font=name_font)
    print(f"  Name at bottom left")

    # Save
    canvas_rgb = canvas.convert("RGB")
    canvas_rgb.save(OUTPUT_PATH, "PNG", quality=95)
    print(f"\n✓ Cover saved to: {OUTPUT_PATH}")

    # Verify
    result = Image.open(OUTPUT_PATH)
    print(f"  Final size: {result.size[0]}x{result.size[1]}")


if __name__ == "__main__":
    create_cover()
