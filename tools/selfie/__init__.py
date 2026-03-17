"""
Selfie Tool

Generate AI selfies of Valor Engels using OpenAI's gpt-image-1 model.
Valor's appearance is defined canonically from his profile photo.
"""

import base64
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Canonical appearance description derived from Valor's profile photo
VALOR_APPEARANCE = (
    "slim white male in his mid-30s, short brown hair slightly longer on top, "
    "short brown stubble, blue-grey eyes, lean build, casual but professional style"
)

# Base selfie prompt — context/scene gets appended
SELFIE_BASE_PROMPT = (
    f"Photorealistic selfie photo of {VALOR_APPEARANCE}. "
    "Shot from a slight below-eye-level angle as if holding a phone, "
    "natural lighting, sharp focus on face, shallow depth of field background. "
    "Candid, authentic feel — not posed or stock-photo stiff."
)

DEFAULT_SCENES = {
    "office": "sitting at a desk with monitors and code on screen behind him, daytime office",
    "coffee": "at a coffee shop, laptop open on table, warm ambient light",
    "outdoors": "outdoors in a city, natural daylight, slight breeze",
    "evening": "in a dimly lit space, warm evening light, relaxed expression",
    "working": "focused, in front of a computer screen, soft screen glow on face",
}


def take_selfie(
    scene: str | None = None,
    custom_context: str | None = None,
    output_dir: str | Path | None = None,
    size: str = "1024x1024",
) -> dict:
    """
    Generate a selfie of Valor Engels.

    Args:
        scene: Named scene preset ('office', 'coffee', 'outdoors', 'evening', 'working').
               Defaults to 'office' if neither scene nor custom_context is given.
        custom_context: Custom scene/context description (overrides scene preset).
        output_dir: Directory to save image (default: generated_images/selfies/).
        size: Image size — '1024x1024', '1536x1024' (landscape), '1024x1536' (portrait).

    Returns:
        dict with:
            - path: Saved file path
            - prompt: Full prompt used
            - scene: Scene label used
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY environment variable not set"}

    # Build context
    if custom_context:
        context = custom_context
        scene_label = "custom"
    else:
        scene_label = scene or "office"
        if scene_label not in DEFAULT_SCENES:
            valid = list(DEFAULT_SCENES.keys())
            return {"error": f"Unknown scene '{scene_label}'. Valid: {valid}."}
        context = DEFAULT_SCENES[scene_label]

    full_prompt = f"{SELFIE_BASE_PROMPT} Scene: {context}."

    client = OpenAI(api_key=api_key)

    try:
        response = client.images.generate(
            model="gpt-image-1",
            prompt=full_prompt,
            n=1,
            size=size,
            output_format="png",
        )
    except Exception as e:
        return {"error": f"OpenAI API error: {e}"}

    # Decode and save
    image_data = response.data[0]

    if output_dir is None:
        output_dir = Path("generated_images/selfies")
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output_dir / f"valor_selfie_{scene_label}_{timestamp}.png"

    if hasattr(image_data, "b64_json") and image_data.b64_json:
        filename.write_bytes(base64.b64decode(image_data.b64_json))
    elif hasattr(image_data, "url") and image_data.url:
        import requests

        r = requests.get(image_data.url, timeout=30)
        r.raise_for_status()
        filename.write_bytes(r.content)
    else:
        return {"error": "No image data in response"}

    return {
        "path": str(filename),
        "prompt": full_prompt,
        "scene": scene_label,
    }


def list_scenes() -> dict:
    """Return available scene presets."""
    return DEFAULT_SCENES.copy()


def main():
    """CLI entry point: valor-selfie [scene] [--context '...'] [--size 1024x1024]"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a selfie of Valor Engels using OpenAI gpt-image-1."
    )
    parser.add_argument(
        "scene",
        nargs="?",
        default="office",
        help=f"Scene preset: {', '.join(DEFAULT_SCENES.keys())} (default: office)",
    )
    parser.add_argument("--context", "-c", help="Custom scene/context (overrides scene preset)")
    parser.add_argument(
        "--size",
        "-s",
        default="1024x1024",
        choices=["1024x1024", "1536x1024", "1024x1536"],
        help="Image size (default: 1024x1024)",
    )
    parser.add_argument(
        "--output", "-o", help="Output directory (default: generated_images/selfies/)"
    )
    parser.add_argument("--list-scenes", action="store_true", help="List available scene presets")
    args = parser.parse_args()

    if args.list_scenes:
        print("Available scenes:")
        for name, desc in DEFAULT_SCENES.items():
            print(f"  {name}: {desc}")
        return

    print(f"Taking selfie (scene: {args.context or args.scene})...")
    result = take_selfie(
        scene=args.scene,
        custom_context=args.context,
        output_dir=args.output,
        size=args.size,
    )

    if "error" in result:
        print(f"Error: {result['error']}")
        raise SystemExit(1)

    print(f"Saved: {result['path']}")
    print(f"Scene: {result['scene']}")


if __name__ == "__main__":
    main()
