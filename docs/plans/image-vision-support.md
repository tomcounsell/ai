# Image Vision Support

**Status**: Implemented
**Priority**: P0
**Created**: 2026-01-20

## Problem

When users send images to Valor via Telegram, the bridge downloads the image but only passes a text description like `[User sent an image: /path/to/file.jpg]` to clawdbot. Clawdbot CLI doesn't support image attachments, so Claude never actually sees the image.

## Current Flow (Broken)

```
User sends image
    ↓
Bridge downloads to data/media/photo_YYYYMMDD_HHMMSS_ID.jpg
    ↓
Creates text: "[User sent an image: /path/to/file.jpg]"
    ↓
Passes to clawdbot as text message
    ↓
Claude receives text description, cannot see image
```

## Proposed Solution

Use a vision model to describe the image first, then pass that description to clawdbot.

### Option A: Ollama with LLaVA (Recommended)

- Local, free, fast
- Already have Ollama infrastructure
- LLaVA model handles image understanding

```python
async def describe_image(image_path: Path) -> str:
    """Use Ollama LLaVA to describe an image."""
    import ollama

    response = ollama.chat(
        model='llava',
        messages=[{
            'role': 'user',
            'content': 'Describe this image in detail. What do you see?',
            'images': [str(image_path)]
        }]
    )
    return response['message']['content']
```

### Option B: Claude API Direct

- Higher quality descriptions
- Costs money per image
- Would bypass clawdbot for image analysis

### Option C: OpenAI Vision API

- GPT-4V for image understanding
- Costs money per image
- Already have OpenAI API key for Whisper

## Implementation Plan

1. **Install LLaVA model**: `ollama pull llava`
2. **Add image description function** to `bridge/telegram_bridge.py`
3. **Update `process_incoming_media()`** to call vision model for photos/images
4. **Include description in message** instead of just file path

## New Flow

```
User sends image
    ↓
Bridge downloads to data/media/
    ↓
Ollama LLaVA describes image: "A screenshot of a terminal showing Python code..."
    ↓
Creates text: "[User sent an image]\nImage description: A screenshot of..."
    ↓
Passes to clawdbot with full context
    ↓
Claude can understand and respond to image content
```

## Files to Modify

- `bridge/telegram_bridge.py`: Add `describe_image()` function, update `process_incoming_media()`
- Possibly create `tools/image_vision/` tool for reuse

## Testing

1. Send image with no text → Valor should describe what's in it
2. Send screenshot of code → Valor should be able to discuss the code
3. Send meme → Valor should understand the humor/context
4. Send photo of whiteboard → Valor should transcribe/summarize

## Dependencies

- Ollama with LLaVA model
- OR OpenAI API key (already have)
- OR Anthropic API key (already have)

## Estimated Effort

2-3 hours for basic implementation with Ollama LLaVA
