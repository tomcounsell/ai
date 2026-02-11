#!/usr/bin/env python3
"""
Generate podcast episode cover art using AI image generation.

Usage:
    python generate_cover.py <episode_dir> --prompt "Your image prompt"
    python generate_cover.py <episode_dir> --auto  # Auto-generate from report.md
    python generate_cover.py <episode_dir> --auto --quiet --log-dir logs/

Requirements:
    - OpenRouter API key in environment variable OPENROUTER_API_KEY (can be in .env)
    - pip install requests python-dotenv
"""

import argparse
import base64
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    print("Error: requests package not installed. Run: pip install requests")
    sys.exit(1)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-pro-image-preview"


def read_report(episode_dir):
    """Read the episode's report.md file."""
    report_path = Path(episode_dir) / "report.md"
    if not report_path.exists():
        return None
    return report_path.read_text()


def generate_prompt_from_report(report_text, episode_title):
    """
    Generate an image prompt by analyzing the report.
    This is a simple extraction - could be enhanced with AI analysis.
    """
    # Extract first few paragraphs for context
    lines = [
        l.strip()
        for l in report_text.split("\n")
        if l.strip() and not l.startswith("#")
    ]
    summary = " ".join(lines[:3])[:500]

    # Create focused prompt with design spec colors
    # Primary: Cream (#F5F1E8) background, Black (#000000), Salmon accent (#E8B4A8)
    prompt = f"""Modern podcast episode cover art for "{episode_title}":

Style: Clean, professional, abstract visualization
Layout: Bold visual elements suitable for square format
Color palette: Light warm cream/off-white (#F5F1E8) background with black (#000000) and warm salmon/coral (#E8B4A8) accents
Concept: {summary[:200]}

Design as square format (1024x1024px) with space for text overlay.
Professional, minimalist aesthetic suitable for Apple Podcasts.
No text in the image - pure visual design."""

    return prompt


def generate_image(
    prompt,
    output_path,
    model_id=DEFAULT_MODEL,
    aspect_ratio="1:1",
    verbose=True,
    log_file=None,
):
    """
    Generate image using OpenRouter API with Gemini.

    Args:
        prompt: Image generation prompt
        output_path: Where to save the image
        model_id: Model to use for generation
        aspect_ratio: Image aspect ratio (1:1, 16:9, 9:16, etc.)
        verbose: Print progress messages
        log_file: Optional log file path

    Returns:
        Tuple of (output_path, enhanced_prompt) or (None, None) on error
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        log("Error: OPENROUTER_API_KEY environment variable not set")
        log(
            "Set it in .env file or environment: export OPENROUTER_API_KEY='your-api-key'"
        )
        return None, None

    # Append explicit instructions to avoid text/icons and ensure consistent brand colors
    # Design spec colors: Cream (#F5F1E8) background, Black (#000000), Salmon (#E8B4A8) accents
    enhanced_prompt = f"""{prompt}

IMPORTANT VISUAL REQUIREMENTS:
- The ENTIRE canvas from edge to edge must be warm cream/off-white (#F5F1E8) - a light, warm background
- Light cream background fills the complete image area - not just a section or inner frame
- Use black (#000000) and warm salmon/coral (#E8B4A8) as accent colors on the cream background
- Color palette should feel warm, sophisticated, and editorial - like a premium research publication
- Pure abstract visualization only
- Absolutely no text, no numbers, no labels, no annotations, no icons, no logos, no symbols, no letterforms of any kind
- Clean visual design without any typography or graphic elements

COMPOSITION:
- Visual interest and detail should be concentrated in the LOWER 2/3 of the image
- Keep the TOP 1/3 relatively simple and uncluttered for text overlay placement
- Main graphic elements should flow from center to bottom
- Avoid placing busy patterns or focal points in the upper third"""

    log(f"Generating image with {model_id}...")
    log(f"Aspect ratio: {aspect_ratio}")
    log(f"Enhanced prompt: {enhanced_prompt[:150]}...")

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://research.yuda.me",
                "X-Title": "Yudame Research Podcast Cover Generator",
            },
            json={
                "model": model_id,
                "modalities": ["text", "image"],
                "n": 1,
                "image_config": {"aspect_ratio": aspect_ratio},
                "messages": [
                    {"role": "user", "content": f"Generate an image: {enhanced_prompt}"}
                ],
            },
            timeout=120,
        )

        response.raise_for_status()
        result = response.json()

        if "choices" in result and len(result["choices"]) > 0:
            message = result["choices"][0].get("message", {})

            # Images are returned in the 'images' field as data URLs
            raw_images = message.get("images", [])

            if not raw_images:
                log("Error: No images returned from API")
                return None, None

            # Get first image
            img = raw_images[0]
            image_url = None

            if isinstance(img, dict):
                image_url = img.get("image_url", {}).get("url", "")
            elif isinstance(img, str):
                image_url = img

            if not image_url:
                log("Error: Could not extract image URL from response")
                return None, None

            # Save the image
            if image_url.startswith("data:"):
                # Parse data URL and save to file
                try:
                    header, b64_data = image_url.split(",", 1)
                    image_data = base64.b64decode(b64_data)
                    Path(output_path).write_bytes(image_data)
                    log(f"✓ Cover art saved to: {output_path}")
                except Exception as e:
                    log(f"Error saving image: {e}")
                    return None, None
            else:
                # URL to download (shouldn't happen with Gemini but handle it)
                import urllib.request

                log(f"Downloading from URL...")
                urllib.request.urlretrieve(image_url, output_path)
                log(f"✓ Cover art saved to: {output_path}")

            return output_path, enhanced_prompt

        log("Error: No valid response from API")
        return None, None

    except requests.exceptions.Timeout:
        log("Error: Request timed out. Please try again.")
        return None, None
    except requests.exceptions.RequestException as e:
        log(f"Error: API request failed: {str(e)}")
        if hasattr(e.response, "text"):
            log(f"Response: {e.response.text}")
        return None, None
    except Exception as e:
        log(f"Error: An unexpected error occurred: {str(e)}")
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Generate podcast episode cover art")
    parser.add_argument("episode_dir", help="Path to episode directory")
    parser.add_argument("--prompt", help="Custom image generation prompt")
    parser.add_argument(
        "--auto", action="store_true", help="Auto-generate prompt from report.md"
    )
    parser.add_argument(
        "--aspect-ratio", default="1:1", help="Image aspect ratio (default: 1:1)"
    )
    parser.add_argument("--output", help="Output filename (default: cover.png)")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to use for image generation (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--log-dir",
        help="Directory for output and log files (default: episode directory)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output (suppress progress messages)",
    )

    args = parser.parse_args()

    episode_dir = Path(args.episode_dir)
    if not episode_dir.exists():
        if not args.quiet:
            print(f"Error: Episode directory not found: {episode_dir}")
        return 1

    # Set up log directory
    log_dir = Path(args.log_dir) if args.log_dir else episode_dir
    if args.log_dir and not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)

    # Set up log file
    log_file = None
    if args.log_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(log_dir / f"cover_generation_log_{timestamp}.txt")

    def log(msg):
        if not args.quiet:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    # Determine output path - always save cover to episode directory
    output_filename = args.output or "cover.png"
    output_path = episode_dir / output_filename

    # Get or generate prompt
    if args.auto:
        log("Auto-generating prompt from report.md...")
        report = read_report(episode_dir)
        if not report:
            log(f"Error: report.md not found in {episode_dir}")
            return 1

        # Try to extract title from directory name
        episode_title = episode_dir.name.replace("-", " ").title()
        prompt = generate_prompt_from_report(report, episode_title)
        log(f"\nGenerated prompt:\n{prompt}\n")
    elif args.prompt:
        prompt = args.prompt
    else:
        log("Error: Must provide either --prompt or --auto")
        return 1

    # Generate image
    image_path, enhanced_prompt = generate_image(
        prompt,
        output_path,
        model_id=args.model,
        aspect_ratio=args.aspect_ratio,
        verbose=not args.quiet,
        log_file=log_file,
    )

    if not image_path:
        log("Image generation failed")
        return 1

    # Save metadata to log file
    if log_file:
        import json

        metadata_file = (
            Path(log_file).parent
            / f"cover_generation_metadata_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(metadata_file, "w") as f:
            json.dump(
                {
                    "prompt": prompt,
                    "enhanced_prompt": enhanced_prompt,
                    "model": args.model,
                    "aspect_ratio": args.aspect_ratio,
                    "output_filename": output_filename,
                    "timestamp": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )
        log(f"✓ Metadata saved to: {metadata_file}")

    # Also save prompt to prompts.md if it exists (for backwards compatibility)
    prompts_file = episode_dir / "prompts.md"
    if prompts_file.exists():
        with open(prompts_file, "a") as f:
            f.write(f"\n\n## Cover Art Generation\n\n")
            f.write(f"**Tool Used:** OpenRouter - {args.model}\n\n")
            f.write(f"**Original Prompt:**\n```\n{prompt}\n```\n\n")
            f.write(f"**Enhanced Prompt:**\n```\n{enhanced_prompt}\n```\n\n")
            f.write(f"**Aspect Ratio:** {args.aspect_ratio}\n\n")
            f.write(f"**Output:** {output_filename}\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n")
        log(f"✓ Prompt logged to prompts.md")

    log(f"\n✓ Done! Cover art ready at: {image_path}")

    if log_file:
        log(f"✓ Log saved to: {log_file}")

    # Print next steps (only if not quiet)
    if not args.quiet:
        log(f"\nNext step: Add branding with add_logo_watermark.py")
        log(f"\nTo use in feed.xml, add this line to the episode <item>:")
        log(
            f'  <itunes:image href="https://research.yuda.me/podcast/episodes/{episode_dir.name}/{output_filename}?v=1"/>'
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
