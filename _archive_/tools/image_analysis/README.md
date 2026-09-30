# Image Analysis Tool

Multi-modal vision analysis using AI models for object detection, OCR, and scene understanding.

## Overview

This tool provides AI-powered image analysis capabilities including:
- Scene description and understanding
- Object detection and classification
- Text extraction (OCR)
- Content tagging
- Safety assessment
- Accessibility alt-text generation

## Installation

Ensure you have the OpenRouter API key configured:

```bash
export OPENROUTER_API_KEY=your_api_key
```

## Quick Start

```python
from tools.image_analysis import analyze_image

# Basic analysis
result = analyze_image("path/to/image.jpg")
print(result["description"])

# Extract text (OCR)
result = analyze_image("screenshot.png", analysis_types=["text"])
print(result["text"])
```

## API Reference

### analyze_image()

```python
def analyze_image(
    image_source: str,
    analysis_types: list[str] | None = None,
    detail_level: Literal["minimal", "standard", "detailed", "comprehensive"] = "standard",
    output_format: Literal["structured", "narrative", "technical", "accessibility"] = "structured",
    model: str = DEFAULT_MODEL,
) -> dict
```

**Parameters:**
- `image_source`: File path, URL, or base64 encoded image
- `analysis_types`: List of analysis types:
  - `description`: Natural language description
  - `objects`: Object detection
  - `text`: OCR text extraction
  - `tags`: Generate tags/labels
  - `safety`: Content safety assessment
- `detail_level`: Level of detail
- `output_format`: Output format style
- `model`: OpenRouter model ID

**Returns:**
```python
{
    "image_source": str,
    "detail_level": str,
    "analysis_types": list[str],
    "raw_analysis": str,      # Full analysis text
    "description": str,       # If requested
    "objects": list,          # If requested
    "text": str,              # If requested (OCR)
    "tags": list[str],        # If requested
    "safety_rating": str      # If requested
}
```

### extract_text()

```python
def extract_text(image_source: str, model: str = DEFAULT_MODEL) -> dict
```

Convenience function for OCR.

### generate_alt_text()

```python
def generate_alt_text(image_source: str, model: str = DEFAULT_MODEL) -> dict
```

Generate accessibility-friendly image descriptions.

## Workflows

### Basic Image Analysis
```python
result = analyze_image("photo.jpg")
print(result["raw_analysis"])
```

### Detailed OCR
```python
result = analyze_image(
    "document.png",
    analysis_types=["text"],
    detail_level="detailed"
)
```

### Comprehensive Analysis
```python
result = analyze_image(
    "complex_scene.jpg",
    analysis_types=["description", "objects", "text", "tags"],
    detail_level="comprehensive"
)
```

### URL-based Analysis
```python
result = analyze_image("https://example.com/image.jpg")
```

## Error Handling

```python
result = analyze_image("image.jpg")
if "error" in result:
    print(f"Analysis failed: {result['error']}")
else:
    print(result["description"])
```

## Troubleshooting

### API Key Not Set
```
Error: OPENROUTER_API_KEY environment variable not set
```
Set your API key in the environment or `.env` file.

### Image File Not Found
```
Error: Image file not found: path/to/image.jpg
```
Verify the file path is correct.

### Request Timeout
Large images or comprehensive analysis may timeout. Try reducing detail level or image size.
