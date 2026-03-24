# Selfie

## Overview

Generate AI selfies of Valor Engels using OpenAI's gpt-image-1 model. The tool uses a canonical appearance description derived from Valor's profile photo to produce consistent, photorealistic selfie images.

**Capabilities:** `capture`

## Quick Start

```python
from tools.selfie import take_selfie

# Use a predefined scene
result = take_selfie(scene="office")
print(f"Image saved to: {result['path']}")

# Use custom context
result = take_selfie(custom_context="at a tech conference giving a talk")
print(f"Image saved to: {result['path']}")
```

## CLI

```bash
valor-selfie               # Random scene
valor-selfie office         # Predefined scene
valor-selfie "at a beach"   # Custom context
```

## API Reference

### `take_selfie(scene=None, custom_context=None, output_dir=None, size="1024x1024") -> dict`

Generate a selfie image.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scene` | str or None | None | Predefined scene key (office, coffee, outdoors, evening, working) |
| `custom_context` | str or None | None | Custom scene description |
| `output_dir` | str/Path or None | None | Output directory (defaults to generated_images/) |
| `size` | str | "1024x1024" | Image dimensions |

**Returns:** `dict` with keys:
- `path` -- Path to saved image file
- `prompt` -- The prompt used for generation
- `error` -- Error message (if failed)

## Predefined Scenes

| Scene | Description |
|-------|-------------|
| office | At a desk with monitors and code |
| coffee | Coffee shop with laptop |
| outdoors | City, natural daylight |
| evening | Dimly lit, warm evening light |
| working | Focused, computer screen glow |

## Configuration

Requires `OPENAI_API_KEY` environment variable.
