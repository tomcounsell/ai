# Image Tagging Tool

Tag and categorize images with AI for organization and search.

## Overview

This tool provides AI-powered image tagging:
- Generate descriptive tags
- Categorize by content type
- Detect dominant colors
- Identify image type

## Installation

Configure your API key:

```bash
export OPENROUTER_API_KEY=your_api_key
```

## Quick Start

```python
from tools.image_tagging import tag_image

# Tag an image
result = tag_image("photo.jpg")
for tag in result["tags"]:
    print(f"{tag['tag']}: {tag['confidence']}")
```

## API Reference

### tag_image()

```python
def tag_image(
    image_source: str,
    tag_categories: list[str] | None = None,
    max_tags: int = 10,
    confidence_threshold: float = 0.5,
    custom_taxonomy: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict
```

**Parameters:**
- `image_source`: File path, URL, or base64 image
- `tag_categories`: Categories (default: objects, scene, activity, style, mood)
- `max_tags`: Max tags per category (default: 10)
- `confidence_threshold`: Minimum confidence (0-1)
- `custom_taxonomy`: Custom tag vocabulary
- `model`: OpenRouter model ID

**Returns:**
```python
{
    "tags": [
        {"tag": str, "category": str, "confidence": float}
    ],
    "categories": {
        "category_name": [tags...]
    },
    "dominant_colors": list[str],
    "image_type": str,
    "tag_count": int
}
```

### batch_tag_images()

```python
def batch_tag_images(
    image_sources: list[str],
    tag_categories: list[str] | None = None,
    max_tags: int = 10,
) -> dict
```

Tag multiple images at once.

## Workflows

### Basic Tagging
```python
result = tag_image("vacation.jpg")
print(f"Type: {result['image_type']}")
print(f"Colors: {result['dominant_colors']}")
```

### Filtered by Category
```python
result = tag_image(
    "photo.jpg",
    tag_categories=["objects", "scene"]
)
```

### With Custom Tags
```python
result = tag_image(
    "product.jpg",
    custom_taxonomy=["furniture", "electronics", "clothing"]
)
```

### High Confidence Only
```python
result = tag_image(
    "photo.jpg",
    confidence_threshold=0.8
)
```

## Error Handling

```python
result = tag_image(image_path)

if "error" in result:
    print(f"Tagging failed: {result['error']}")
else:
    for tag in result["tags"]:
        print(tag["tag"])
```

## Troubleshooting

### API Key Not Set
Set OPENROUTER_API_KEY in environment.

### Image Not Found
Verify the file path or URL is accessible.

### Low Quality Results
Try a different model or increase max_tags.
