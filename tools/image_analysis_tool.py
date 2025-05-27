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
    """
    Analyze an image using vision-capable AI and return description or answer.

    Args:
        image_path: Local path to the image file
        question: Optional specific question about the image
        context: Optional chat context to make analysis more relevant

    Returns:
        AI analysis of the image, formatted for messaging
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return "👁️ Image analysis unavailable: Missing OPENAI_API_KEY configuration."

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
        return "👁️ Error: Image file not found."
    except Exception as e:
        return f"👁️ Image analysis error: {str(e)}"


# Async wrapper for compatibility with existing async patterns
async def analyze_image_async(
    image_path: str, question: str | None = None, context: str | None = None
) -> str:
    """Async wrapper for the image analysis tool function."""
    return analyze_image(image_path, question, context)
