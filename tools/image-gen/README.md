# Image Generation

## Overview

AI image generation using OpenRouter API, supporting multiple models including Gemini, DALL-E, and Flux.

**Current Implementation**: OpenRouter API with configurable model selection.

**Capabilities**: `generate`

## Installation

Ensure `OPENROUTER_API_KEY` is set in your environment:

```bash
export OPENROUTER_API_KEY="your-api-key"
```

Get an API key at [openrouter.ai](https://openrouter.ai/).

## Quick Start

```python
from tools.image_gen import generate_image

# Generate an image
result = generate_image("a serene mountain landscape at sunset")
print(f"Image saved to: {result['path']}")
```

Or use the CLI wrapper:

```bash
python -m tools.image_gen "a cat wearing a space helmet"
```

## Workflows

### Basic Generation

```python
from tools.image_gen import generate_image

result = generate_image(
    prompt="a futuristic cityscape",
    aspect_ratio="16:9",
    output_dir="./images"
)
# Returns: {'path': './images/image_20240119_123456.png', 'prompt': '...'}
```

### With Model Selection

```python
from tools.image_gen import generate_image

# Use DALL-E 3
result = generate_image(
    prompt="an oil painting of a sunset",
    model="openai/dall-e-3"
)

# Use Flux
result = generate_image(
    prompt="photorealistic portrait",
    model="black-forest-labs/flux-schnell"
)
```

### Batch Generation

```python
from tools.image_gen import generate_images

prompts = [
    "a red apple",
    "a green apple",
    "a golden apple"
]
results = generate_images(prompts, output_dir="./apples")
```

## API Reference

### `generate_image(prompt, **kwargs)`

Generate a single image from a text prompt.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | str | required | Text description of the image |
| `model` | str | gemini-2.0-flash | Model to use for generation |
| `aspect_ratio` | str | "1:1" | Aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4) |
| `output_dir` | str | "./generated_images" | Directory to save output |

**Returns**: `dict` with keys:
- `path`: Path to saved image file
- `prompt`: The prompt used
- `model`: Model used
- `error`: Error message (if failed)

### `generate_images(prompts, **kwargs)`

Generate multiple images from a list of prompts.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompts` | list[str] | required | List of text prompts |
| `**kwargs` | | | Same as `generate_image` |

**Returns**: `list[dict]` - Results for each prompt

## Supported Models

| Model | ID | Notes |
|-------|-----|-------|
| Gemini 2.0 Flash | `google/gemini-2.0-flash-exp:free` | Free, supports aspect ratios |
| DALL-E 3 | `openai/dall-e-3` | High quality, paid |
| Flux Schnell | `black-forest-labs/flux-schnell` | Fast, good quality |

## Aspect Ratios

| Ratio | Use Case |
|-------|----------|
| 1:1 | Square, social media posts |
| 16:9 | Landscape, desktop wallpapers |
| 9:16 | Portrait, phone wallpapers, stories |
| 4:3 | Classic photo ratio |
| 3:4 | Portrait photos |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| API key error | Ensure `OPENROUTER_API_KEY` is set |
| Timeout | Try a smaller model or simpler prompt |
| Content filtered | Rephrase prompt to avoid restricted content |
| Rate limited | Wait and retry, or upgrade API plan |
