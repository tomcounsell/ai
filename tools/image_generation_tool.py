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
    """Generate an image using DALL-E 3 and save it locally.
    
    This function creates custom images from text descriptions using OpenAI's
    DALL-E 3 model. Generated images are downloaded and saved to the specified
    directory for use in conversations or applications.

    Args:
        prompt: Text description of the image to generate.
        size: Image size - "1024x1024", "1792x1024", or "1024x1792".
        quality: Image quality - "standard" or "hd".
        style: Image style - "natural" (realistic) or "vivid" (dramatic/artistic).
        save_directory: Optional directory to save image (defaults to /tmp).

    Returns:
        str: Local path to the generated image file, or error message if generation fails.
        
    Example:
        >>> path = generate_image("a cat wearing a wizard hat", style="vivid")
        >>> path.endswith(".png")
        True
        
        >>> generate_image("sunset over mountains", size="1792x1024", quality="hd")
        '/tmp/generated_sunset_over_mountains.png'
        
    Note:
        Requires OPENAI_API_KEY environment variable to be set.
        Generated filenames are sanitized versions of the prompt.
    """
    # Add input validation to match agent implementation
    if not prompt or not prompt.strip():
        return "🎨 Image generation error: Please provide a description for the image."
    
    if len(prompt) > 1000:
        return "🎨 Image generation error: Description too long (maximum 1000 characters)."

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
    """Async wrapper for the image generation tool function.
    
    Provides an asynchronous interface for image generation functionality
    to maintain compatibility with async codebases.
    
    Args:
        prompt: Text description of the image to generate.
        size: Image size specification.
        quality: Image quality setting.
        style: Image style preference.
        save_directory: Optional directory to save image.
        
    Returns:
        str: Same result as generate_image() function.
        
    Note:
        This is a compatibility wrapper. The underlying generate_image()
        function is synchronous but wrapped for async contexts.
    """
    return generate_image(prompt, size, quality, style, save_directory)


def create_image_with_feedback(prompt: str, save_directory: str | None = None) -> tuple[str, str]:
    """Generate an image and return both the path and a user-friendly message.
    
    This function combines image generation with user feedback formatting,
    making it suitable for conversational interfaces that need both the
    file path and a displayable message.

    Args:
        prompt: Text description of the image to generate.
        save_directory: Optional directory to save image.

    Returns:
        tuple[str, str]: Tuple of (image_path, user_message).
                        If generation fails, image_path will be empty string.
                        
    Example:
        >>> path, message = create_image_with_feedback("a red car")
        >>> path.endswith(".png") if path else True
        True
        >>> "Generated Image" in message
        True
    """
    image_path = generate_image(prompt, save_directory=save_directory)

    if image_path.startswith("🎨"):
        # Error case
        return "", image_path
    else:
        # Success case
        user_message = f"🎨 **Generated Image**\n\nPrompt: {prompt}\nSaved to: {image_path}"
        return image_path, user_message
