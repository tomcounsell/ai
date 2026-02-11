#!/usr/bin/env python3
"""
Process headshot using Gemini 2.5 Flash Image (nano banana).
Removes background, makes B&W, and dresses subject professionally.

Usage:
    python process_headshot.py ../valor-headshot-original.jpg
"""

import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from PIL import Image
except ImportError:
    print("Error: Required packages not installed.")
    print("Run: pip install google-genai pillow")
    sys.exit(1)


def process_headshot(input_path: str, output_path: str = None):
    """
    Process headshot with Gemini:
    - Remove background (make it solid black)
    - Convert to black and white / high contrast
    - Dress the subject professionally
    """
    input_path = Path(input_path)
    if not input_path.exists():
        print(f"Error: Input image not found: {input_path}")
        return None

    if output_path is None:
        output_path = input_path.parent / "valor-headshot.png"
    else:
        output_path = Path(output_path)

    print(f"Loading image: {input_path}")
    image = Image.open(input_path)

    prompt = """Transform this headshot photo:

1. BACKGROUND: Remove the entire background and replace with solid black (#000000)
2. CLOTHING: Change the grey sweatshirt to a professional dark suit jacket with a crisp white dress shirt and subtle dark tie
3. STYLE: Convert to dramatic black and white with high contrast
4. LIGHTING: Professional studio lighting effect, subtle rim light on edges
5. CROP: Keep the same framing - head and shoulders
6. QUALITY: High resolution, sharp, professional headshot suitable for a podcast cover

The final image should look like a premium professional headshot on a pure black background."""

    print("Sending to Gemini 2.5 Flash Image...")
    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt, image],
    )

    # Extract the generated image
    for part in response.parts:
        if part.text is not None:
            print(f"Model response: {part.text}")
        elif part.inline_data is not None:
            result_image = part.as_image()
            result_image.save(output_path)
            print(f"✓ Processed headshot saved to: {output_path}")
            return output_path

    print("Error: No image returned from API")
    return None


def main():
    if len(sys.argv) < 2:
        # Default path
        input_path = Path(__file__).parent.parent / "valor-headshot-original.jpg"
    else:
        input_path = sys.argv[1]

    output_path = None
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]

    result = process_headshot(input_path, output_path)

    if result:
        print(f"\n✓ Done! Update cover-art.html to use: {result.name}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
