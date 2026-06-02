"""
Image Generation Tool

Generate images from text prompts using AI models.

Two providers are supported behind one interface:
  - "gemini" (default): google/gemini-3-pro-image-preview via OpenRouter
  - "openai": gpt-image-1 via the OpenAI Images API directly

Pick the best model per image with the `provider` argument (or `--provider` on
the CLI). Gemini remains the default so existing callers are unchanged.
"""

import base64
import os
from pathlib import Path
from typing import Literal

import requests

from bridge.utc import utc_now
from config.models import (
    IMAGE_ASPECT_RATIOS,
    IMAGE_GEN_PROVIDERS,
    OPENAI_IMAGE_SIZES,
    OPENROUTER_URL,
)


class ImageGenError(Exception):
    """Image generation operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


AspectRatio = Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]
Provider = Literal["gemini", "openai"]


def _generate_via_openrouter(
    prompt: str, aspect_ratio: str, model: str
) -> tuple[list[bytes], str | None]:
    """Generate via Gemini (or any OpenRouter image model). Returns (image_bytes, text)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ImageGenError("OPENROUTER_API_KEY environment variable not set", "config")

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Valor Image Gen",
        },
        json={
            "model": model,
            "modalities": ["text", "image"],
            "n": 1,
            "image_config": {"aspect_ratio": aspect_ratio},
            "messages": [{"role": "user", "content": f"Generate an image: {prompt}"}],
        },
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()

    if not result.get("choices"):
        raise ImageGenError("No response from model", "execution")

    message = result["choices"][0].get("message", {})

    # Extract image bytes from the OpenRouter data-URL shape.
    image_bytes: list[bytes] = []
    for img in message.get("images", []):
        url = ""
        if isinstance(img, dict):
            url = img.get("image_url", {}).get("url", "")
        elif isinstance(img, str):
            url = img
        if url.startswith("data:"):
            _, b64_data = url.split(",", 1)
            image_bytes.append(base64.b64decode(b64_data))

    # Extract any text response.
    content = message.get("content", "")
    text_parts: list[str] = []
    if isinstance(content, str):
        if content:
            text_parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif isinstance(part, str):
                text_parts.append(part)

    return image_bytes, ("\n".join(text_parts) if text_parts else None)


def _generate_via_openai(
    prompt: str, aspect_ratio: str, model: str
) -> tuple[list[bytes], str | None]:
    """Generate via the OpenAI Images API (gpt-image-1). Returns (image_bytes, text)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ImageGenError("OPENAI_API_KEY environment variable not set", "config")

    from openai import OpenAI

    size = OPENAI_IMAGE_SIZES.get(aspect_ratio, "1024x1024")
    client = OpenAI(api_key=api_key)
    result = client.images.generate(model=model, prompt=prompt, size=size, n=1)

    image_bytes: list[bytes] = []
    for item in result.data or []:
        if getattr(item, "b64_json", None):
            image_bytes.append(base64.b64decode(item.b64_json))

    if not image_bytes:
        raise ImageGenError("No image returned by OpenAI", "execution")

    return image_bytes, None


def generate_image(
    prompt: str,
    aspect_ratio: AspectRatio = "1:1",
    output_dir: str | Path | None = None,
    provider: Provider = "gemini",
    model: str | None = None,
) -> dict:
    """
    Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate
        aspect_ratio: Image aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 21:9)
        output_dir: Directory to save generated images (default: generated_images/)
        provider: Which model family to use — "gemini" (default) or "openai"
        model: Explicit model string override. When None, resolved from `provider`.

    Returns:
        dict with:
            - images: List of saved image file paths
            - text: Any text response from the model
            - provider: The provider used
            - model: The resolved model string
            - aspect_ratio: The aspect ratio used
            - dimensions: (width, height) tuple
    """
    if not prompt or not prompt.strip():
        return {"error": "Prompt cannot be empty"}

    if aspect_ratio not in IMAGE_ASPECT_RATIOS:
        return {"error": f"Invalid aspect ratio. Choose from: {list(IMAGE_ASPECT_RATIOS.keys())}"}

    if provider not in IMAGE_GEN_PROVIDERS:
        return {"error": f"Invalid provider. Choose from: {list(IMAGE_GEN_PROVIDERS.keys())}"}

    resolved_model = model or IMAGE_GEN_PROVIDERS[provider]
    dimensions = IMAGE_ASPECT_RATIOS[aspect_ratio]

    try:
        if provider == "openai":
            image_bytes, text = _generate_via_openai(prompt, aspect_ratio, resolved_model)
        else:
            image_bytes, text = _generate_via_openrouter(prompt, aspect_ratio, resolved_model)

        # Shared save-to-disk path for both providers.
        saved_paths: list[str] = []
        if image_bytes:
            out = Path(output_dir) if output_dir else Path("generated_images")
            out.mkdir(exist_ok=True)
            timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
            for i, data in enumerate(image_bytes, 1):
                ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
                filename = out / f"image_{timestamp}_{i}.{ext}"
                filename.write_bytes(data)
                saved_paths.append(str(filename))

        return {
            "images": saved_paths,
            "text": text,
            "provider": provider,
            "model": resolved_model,
            "aspect_ratio": aspect_ratio,
            "dimensions": dimensions,
            "prompt": prompt,
        }

    except ImageGenError as e:
        return {"error": e.message}
    except requests.exceptions.Timeout:
        return {"error": "Image generation request timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def list_aspect_ratios() -> dict:
    """
    List available aspect ratios with their dimensions.

    Returns:
        dict mapping aspect ratio strings to (width, height) tuples
    """
    return {
        ratio: {"dimensions": dims, "description": _get_ratio_description(ratio)}
        for ratio, dims in IMAGE_ASPECT_RATIOS.items()
    }


def _get_ratio_description(ratio: str) -> str:
    """Get human-readable description for an aspect ratio."""
    descriptions = {
        "1:1": "Square - social media posts, profile pictures",
        "16:9": "Landscape wide - YouTube thumbnails, presentations",
        "9:16": "Portrait tall - Instagram/TikTok stories, mobile wallpapers",
        "4:3": "Classic landscape - traditional photos, slides",
        "3:4": "Classic portrait - traditional portrait photos",
        "3:2": "Photo landscape - DSLR standard ratio",
        "2:3": "Photo portrait - DSLR portrait orientation",
        "21:9": "Ultrawide/cinematic - movie posters, banners",
    }
    return descriptions.get(ratio, "")


def main():
    """CLI entry point for image generation."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="valor-image-gen",
        description="Generate images from text prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Aspect ratios:\n"
            + "\n".join(
                f"  {r}: {i['dimensions'][0]}x{i['dimensions'][1]} - {i['description']}"
                for r, i in list_aspect_ratios().items()
            )
            + "\n\nExamples:\n"
            "  valor-image-gen 'a cat in space'\n"
            "  valor-image-gen 'sunset over mountains' 16:9\n"
            "  valor-image-gen 'a logo' --provider openai\n"
            "  valor-image-gen 'a logo' --model gpt-image-1"
        ),
    )
    parser.add_argument("prompt", help="Text description of the image to generate")
    parser.add_argument(
        "aspect_ratio",
        nargs="?",
        default="1:1",
        choices=list(IMAGE_ASPECT_RATIOS.keys()),
        help="Aspect ratio (default: 1:1)",
    )
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=list(IMAGE_GEN_PROVIDERS.keys()),
        help="Model family: gemini (default) or openai",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Explicit model string override (bypasses --provider default)",
    )
    args = parser.parse_args()

    print(f"Generating image: {args.prompt}")
    print(f"Provider: {args.provider}  Aspect ratio: {args.aspect_ratio}")

    result = generate_image(
        args.prompt,
        aspect_ratio=args.aspect_ratio,
        provider=args.provider,
        model=args.model,
    )

    if "error" in result:
        print(f"Error: {result['error']}")
        raise SystemExit(1)

    print(f"\nGenerated {len(result['images'])} image(s) via {result['model']}:")
    for path in result["images"]:
        print(f"  - {path}")
    if result.get("text"):
        print(f"\nModel response: {result['text']}")


if __name__ == "__main__":
    main()
