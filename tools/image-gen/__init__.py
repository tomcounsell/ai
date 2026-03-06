"""
Image Generation Tool

AI image generation using OpenRouter API.
"""

import base64
import os
from datetime import datetime
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.0-flash-exp:free"
DEFAULT_OUTPUT_DIR = "./generated_images"


def generate_image(
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "1:1",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> dict:
    """
    Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate
        model: OpenRouter model ID (default: gemini-2.0-flash-exp:free)
        aspect_ratio: Image aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4)
        output_dir: Directory to save generated images

    Returns:
        dict with keys:
            - path: Path to saved image file
            - prompt: The prompt used
            - model: Model used
            - error: Error message (if failed)
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY environment variable not set"}

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
                "messages": [
                    {"role": "user", "content": f"Generate an image: {prompt}"}
                ],
            },
            timeout=120,
        )

        response.raise_for_status()
        result = response.json()

        if "choices" not in result or len(result["choices"]) == 0:
            return {"error": "No response from model", "prompt": prompt, "model": model}

        message = result["choices"][0].get("message", {})

        # Extract images from response
        raw_images = message.get("images", [])
        if not raw_images:
            # Check content for inline images
            content = message.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url:
                            raw_images.append(url)

        if not raw_images:
            return {
                "error": "No image generated",
                "prompt": prompt,
                "model": model,
                "response": message.get("content", ""),
            }

        # Save the first image
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_data = raw_images[0]

        if isinstance(img_data, dict):
            img_url = img_data.get("image_url", {}).get("url", "")
        else:
            img_url = img_data

        if img_url.startswith("data:"):
            # Parse data URL
            header, b64_data = img_url.split(",", 1)
            ext = "png" if "png" in header else "jpg"
            filename = output_path / f"image_{timestamp}.{ext}"
            image_bytes = base64.b64decode(b64_data)
            filename.write_bytes(image_bytes)
        else:
            # Download from URL
            img_response = requests.get(img_url, timeout=30)
            img_response.raise_for_status()
            ext = "png"  # Default extension
            filename = output_path / f"image_{timestamp}.{ext}"
            filename.write_bytes(img_response.content)

        return {
            "path": str(filename),
            "prompt": prompt,
            "model": model,
        }

    except requests.exceptions.Timeout:
        return {"error": "Request timed out", "prompt": prompt, "model": model}
    except requests.exceptions.RequestException as e:
        return {
            "error": f"API request failed: {str(e)}",
            "prompt": prompt,
            "model": model,
        }
    except Exception as e:
        return {
            "error": f"Unexpected error: {str(e)}",
            "prompt": prompt,
            "model": model,
        }


def generate_images(
    prompts: list[str],
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "1:1",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> list[dict]:
    """
    Generate multiple images from a list of prompts.

    Args:
        prompts: List of text prompts
        model: OpenRouter model ID
        aspect_ratio: Image aspect ratio
        output_dir: Directory to save generated images

    Returns:
        List of result dicts, one per prompt
    """
    results = []
    for prompt in prompts:
        result = generate_image(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            output_dir=output_dir,
        )
        results.append(result)
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.image_gen 'your prompt here'")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    print(f"Generating image for: {prompt}")

    result = generate_image(prompt)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"Image saved to: {result['path']}")
