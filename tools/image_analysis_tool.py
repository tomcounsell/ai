# tools/image_analysis_tool.py
"""
PydanticAI function tool for image analysis using vision-capable LLMs.
Processes images from Telegram messages and provides AI analysis based on context.
"""

import base64
import os

from dotenv import load_dotenv
from openai import OpenAI

# Ensure environment variables are loaded
load_dotenv()


def analyze_image(image_path: str, question: str | None = None, context: str | None = None) -> str:
    """Analyze an image using vision-capable AI and return description or answer.
    
    This function uses OpenAI's GPT-4 Vision model to analyze images and provide
    detailed descriptions or answer specific questions about image content.
    It supports OCR, object recognition, and contextual analysis.

    Args:
        image_path: Local path to the image file.
        question: Optional specific question about the image.
        context: Optional chat context to make analysis more relevant.

    Returns:
        str: AI analysis of the image, formatted for messaging.
             Returns error message if API key is missing or analysis fails.
             
    Example:
        >>> analyze_image("/path/to/photo.jpg", "What's in this image?")
        '👁️ **Image Analysis**\n\nI can see a sunset over mountains...'
        
        >>> analyze_image("/path/to/screenshot.png")
        '👁️ **What I see:**\n\nThis appears to be a code editor...'
        
    Note:
        Requires OPENAI_API_KEY environment variable to be set.
        Supports common image formats (JPEG, PNG, GIF, WebP).
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "👁️ Image analysis unavailable: Missing OPENAI_API_KEY configuration."

    # Validate inputs
    if not image_path or not image_path.strip():
        return "👁️ Image analysis error: Image path cannot be empty."
    
    # Validate image format first (before file operations)
    from pathlib import Path
    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    file_extension = Path(image_path).suffix.lower()
    if file_extension not in valid_extensions:
        return f"👁️ Image analysis error: Unsupported format '{file_extension}'. Supported: {', '.join(valid_extensions)}"

    try:
        # Read and encode image
        with open(image_path, "rb") as image_file:
            image_data = base64.b64encode(image_file.read()).decode("utf-8")

        client = OpenAI(api_key=api_key)

        # Build system prompt based on context
        if question:
            system_content = (
                "You are an AI assistant with vision capabilities. "
                "Analyze the provided image and answer the specific question about it. "
                "Be detailed and accurate in your response. "
                "Keep responses under 400 words for messaging platforms."
            )
            user_content = f"Question about this image: {question}"
        else:
            system_content = (
                "You are an AI assistant with vision capabilities. "
                "Describe what you see in the image in a natural, conversational way. "
                "Focus on the most interesting or relevant aspects. "
                "Keep responses under 300 words for messaging platforms."
            )
            user_content = "What do you see in this image?"

        # Add context if provided
        if context:
            user_content += f"\n\nChat context: {context}"

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                    },
                ],
            },
        ]

        response = client.chat.completions.create(
            model="gpt-4o",  # Vision-capable model
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )

        answer = response.choices[0].message.content

        if question:
            return f"👁️ **Image Analysis**\n\n{answer}"
        else:
            return f"👁️ **What I see:**\n\n{answer}"

    except FileNotFoundError:
        return "👁️ Image analysis error: Image file not found."
    except OSError as e:
        return f"👁️ Image file error: Failed to read image file - {str(e)}"
    except Exception as e:
        error_type = type(e).__name__
        if "API" in str(e) or "OpenAI" in str(e):
            return f"👁️ OpenAI API error: {str(e)}"
        if "base64" in str(e).lower() or "encoding" in str(e).lower():
            return f"👁️ Image encoding error: Failed to process image format - {str(e)}"
        return f"👁️ Image analysis error ({error_type}): {str(e)}"


# Async wrapper for compatibility with existing async patterns
async def analyze_image_async(
    image_path: str, question: str | None = None, context: str | None = None
) -> str:
    """Async wrapper for the image analysis tool function.
    
    Provides an asynchronous interface for image analysis functionality
    to maintain compatibility with async codebases.
    
    Args:
        image_path: Local path to the image file.
        question: Optional specific question about the image.
        context: Optional chat context to make analysis more relevant.
        
    Returns:
        str: Same result as analyze_image() function.
        
    Note:
        This is a compatibility wrapper. The underlying analyze_image()
        function is synchronous but wrapped for async contexts.
    """
    return analyze_image(image_path, question, context)
