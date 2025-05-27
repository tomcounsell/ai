# tools/image_generation_tool.py
"""
PydanticAI function tool for image generation using DALL-E.
Creates images from text prompts for use in conversations.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

# Ensure environment variables are loaded
load_dotenv()


def generate_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "natural",
    save_directory: str | None = None,
) -> str:
    """
    Generate an image using DALL-E 3 and save it locally.

    Args:
        prompt: Text description of the image to generate
        size: Image size - "1024x1024", "1792x1024", or "1024x1792"
        quality: Image quality - "standard" or "hd"
        style: Image style - "natural" or "vivid"
        save_directory: Optional directory to save image (defaults to /tmp)

    Returns:
        Local path to the generated image file, or error message
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration."

    try:
        client = OpenAI(api_key=api_key, timeout=180)

        # Generate image using DALL-E 3
        response = client.images.generate(
            prompt=prompt, model="dall-e-3", size=size, quality=quality, style=style, n=1
        )

        # Get the image URL
        image_url = response.data[0].url

        # Download the image
        image_response = requests.get(image_url, timeout=180)
        image_response.raise_for_status()

        # Determine save path
        if save_directory:
            save_path = Path(save_directory)
        else:
            save_path = Path("/tmp")

        save_path.mkdir(parents=True, exist_ok=True)

        # Create filename from prompt (cleaned up)
        safe_filename = "".join(
            c for c in prompt[:50] if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        safe_filename = safe_filename.replace(" ", "_")
        image_path = save_path / f"generated_{safe_filename}.png"

        # Save the image
        with open(image_path, "wb") as f:
            f.write(image_response.content)

        return str(image_path)

    except Exception as e:
        return f"🎨 Image generation error: {str(e)}"


# Async wrapper for compatibility with existing async patterns
async def generate_image_async(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "standard",
    style: str = "natural",
    save_directory: str | None = None,
) -> str:
    """Async wrapper for the image generation tool function."""
    return generate_image(prompt, size, quality, style, save_directory)


def create_image_with_feedback(prompt: str, save_directory: str | None = None) -> tuple[str, str]:
    """
    Generate an image and return both the path and a user-friendly message.

    Args:
        prompt: Text description of the image to generate
        save_directory: Optional directory to save image

    Returns:
        Tuple of (image_path, user_message)
    """
    image_path = generate_image(prompt, save_directory=save_directory)

    if image_path.startswith("🎨"):
        # Error case
        return "", image_path
    else:
        # Success case
        user_message = f"🎨 **Generated Image**\n\nPrompt: {prompt}\nSaved to: {image_path}"
        return image_path, user_message
