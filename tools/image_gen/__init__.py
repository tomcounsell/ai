"""
Image Generation Tool

Generate images from text prompts using AI models.
Uses Gemini 3 Pro via OpenRouter for native image generation.
"""

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import requests

from config.models import IMAGE_ASPECT_RATIOS, MODEL_IMAGE_GEN

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ImageGenError(Exception):
    """Image generation operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


AspectRatio = Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]


def generate_image(
    prompt: str,
    aspect_ratio: AspectRatio = "1:1",
    output_dir: str | Path | None = None,
    model: str = MODEL_IMAGE_GEN,
) -> dict:
    """
    Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate
        aspect_ratio: Image aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 21:9)
        output_dir: Directory to save generated images (default: generated_images/)
        model: OpenRouter model to use for generation

    Returns:
        dict with:
            - images: List of saved image file paths
            - text: Any text response from the model
            - aspect_ratio: The aspect ratio used
            - dimensions: (width, height) tuple
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY environment variable not set"}

    if not prompt or not prompt.strip():
        return {"error": "Prompt cannot be empty"}

    if aspect_ratio not in IMAGE_ASPECT_RATIOS:
        return {
            "error": f"Invalid aspect ratio. Choose from: {list(IMAGE_ASPECT_RATIOS.keys())}"
        }

    dimensions = IMAGE_ASPECT_RATIOS[aspect_ratio]

    try:
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
                "messages": [
                    {"role": "user", "content": f"Generate an image: {prompt}"}
                ],
            },
            timeout=120,
        )

        response.raise_for_status()
        result = response.json()

        if "choices" not in result or len(result["choices"]) == 0:
            return {"error": "No response from model"}

        message = result["choices"][0].get("message", {})
        content = message.get("content", "")

        # Extract images from response
        raw_images = message.get("images", [])
        image_urls = []
        if raw_images:
            img = raw_images[0]
            if isinstance(img, dict):
                url = img.get("image_url", {}).get("url", "")
                if url:
                    image_urls.append(url)
            elif isinstance(img, str):
                image_urls.append(img)

        # Extract text from content
        text_parts = []
        if isinstance(content, str):
            if content:
                text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)

        # Save images to disk
        saved_paths = []
        if image_urls:
            if output_dir is None:
                output_dir = Path("generated_images")
            else:
                output_dir = Path(output_dir)
            output_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            for i, url in enumerate(image_urls, 1):
                if url.startswith("data:"):
                    try:
                        header, b64_data = url.split(",", 1)
                        # Detect format from header
                        if "jpeg" in header or "jpg" in header:
                            ext = "jpg"
                        elif "png" in header:
                            ext = "png"
                        else:
                            ext = "jpg"

                        filename = output_dir / f"image_{timestamp}_{i}.{ext}"
                        image_data = base64.b64decode(b64_data)
                        filename.write_bytes(image_data)
                        saved_paths.append(str(filename))
                    except Exception as e:
                        return {"error": f"Failed to save image: {e}"}
                else:
                    # URL - would need to download, for now just return it
                    saved_paths.append(url)

        return {
            "images": saved_paths,
            "text": "\n".join(text_parts) if text_parts else None,
            "aspect_ratio": aspect_ratio,
            "dimensions": dimensions,
            "prompt": prompt,
        }

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


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.image_gen 'your prompt here' [aspect_ratio]")
        print("\nAvailable aspect ratios:")
        for ratio, info in list_aspect_ratios().items():
            dims = info["dimensions"]
            print(f"  {ratio}: {dims[0]}x{dims[1]} - {info['description']}")
        sys.exit(1)

    prompt = sys.argv[1]
    aspect_ratio = sys.argv[2] if len(sys.argv) > 2 else "1:1"

    print(f"Generating image: {prompt}")
    print(f"Aspect ratio: {aspect_ratio}")

    result = generate_image(prompt, aspect_ratio=aspect_ratio)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nGenerated {len(result['images'])} image(s):")
        for path in result["images"]:
            print(f"  - {path}")
        if result.get("text"):
            print(f"\nModel response: {result['text']}")
