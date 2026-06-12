# Image Generation

## Overview

AI text-to-image generation with a provider switch. Pick the best model per image:

| Provider | Model | Transport | Key |
|----------|-------|-----------|-----|
| `openai` (default) | `gpt-image-1` | OpenAI Images API | `OPENAI_API_KEY` |
| `gemini` | `google/gemini-3-pro-image-preview` | OpenRouter | `OPENROUTER_API_KEY` |

OpenAI gpt-image-1 is the default. Provider/model constants live in `config/models.py` (`IMAGE_GEN_PROVIDERS`, `OPENAI_IMAGE_GEN`, `OPENROUTER_GEMINI_IMAGE_GEN`).

## CLI

```bash
valor-image-gen 'a cat in space'                       # default: openai gpt-image-1, 1:1
valor-image-gen 'sunset over mountains' 16:9           # aspect ratio
valor-image-gen 'a clean logo' --provider gemini       # gemini via OpenRouter
valor-image-gen 'a clean logo' --model gpt-image-1     # explicit model override
valor-image-gen --help                                 # full usage + aspect ratios
```

## Python

```python
from tools.image_gen import generate_image

result = generate_image(
    prompt="a futuristic cityscape",
    aspect_ratio="16:9",
    # provider="openai",   # default — gpt-image-1 at quality=high
    # provider="gemini",   # alternate — google/gemini-3-pro-image-preview via OpenRouter
    output_dir="./images",  # default: ./generated_images
)
# result -> {
#   "images": ["./images/image_20260612_055518_1.png"],  # saved file paths
#   "text": None,                # any text the model returned (Gemini may include some)
#   "provider": "openai",
#   "model": "gpt-image-1",
#   "aspect_ratio": "16:9",
#   "dimensions": (1344, 768),
#   "prompt": "a futuristic cityscape",
# }
# On failure: {"error": "..."}
```

## Aspect Ratios

Eight ratios are supported (`1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`, `21:9`). Gemini honors each one natively. `gpt-image-1` only accepts a fixed size set, so ratios are mapped to the nearest supported size (square / landscape `1536x1024` / portrait `1024x1536`) via `OPENAI_IMAGE_SIZES` in `config/models.py`.

Run `valor-image-gen --help` for the full ratio table with pixel dimensions and use cases.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `OPENAI_API_KEY ... not set` | Set it (OpenAI provider, default) |
| `OPENROUTER_API_KEY ... not set` | Set it (Gemini provider) |
| Timeout | Simplify the prompt or retry |
| Content filtered | Rephrase to avoid restricted content |
| Rate limited | Wait and retry |
