# Image Vision Support

**Status**: Implemented
**Implemented**: 2026-01-20

## Overview

When users send images to Valor via Telegram, the bridge downloads the image, uses Ollama's LLaVA vision model to generate a detailed description, and includes that description in the message context. This allows Claude to understand and respond to image content intelligently.

## Features

### Supported Image Formats

Automatically processes images in these formats:
- PNG (`.png`)
- JPEG (`.jpg`, `.jpeg`)
- GIF (`.gif`)
- WebP (`.webp`)
- BMP (`.bmp`)

### Vision Model

Uses Ollama with the `llama3.2-vision:11b` model for local, free image understanding:
- Runs entirely on local machine (no API costs)
- Fast processing for most images
- Good quality descriptions for screenshots, photos, diagrams

### Media Storage

Downloaded images are stored in `data/media/` with timestamped filenames:
- Format: `{type}_{YYYYMMDD}_{HHMMSS}_{message_id}.{ext}`
- Example: `photo_20260120_143022_12345.jpg`

## Message Flow

```
User sends image (with or without caption)
    |
    v
Bridge detects photo/image media type
    |
    v
Downloads image to data/media/
    |
    v
Calls Ollama LLaVA: "Describe this image in detail"
    |
    v
[If description succeeds]
Creates enriched message:
  "[User sent an image]
   Image description: A screenshot showing a terminal window
   with Python code that defines a function called..."
    |
    v
Passes to agent with visual context
    |
    v
Claude can discuss image content intelligently
```

## Edge Case Handling

| Case | Behavior |
|------|----------|
| Ollama not installed | Falls back to basic message: "[User sent an image - saved to filename]" |
| LLaVA model not available | Falls back to basic message with filename |
| Vision model error | Logs error, falls back to basic message |
| Very large images | May be slower but still processed |
| Corrupt/invalid images | Ollama may fail, falls back gracefully |

## Implementation Files

- `bridge/telegram_bridge.py`: Core image handling
  - `get_media_type()` - Detect image vs voice vs document
  - `download_media()` - Download Telegram media to local storage
  - `describe_image()` - Use Ollama LLaVA for image description
  - `process_incoming_media()` - Orchestrate media processing pipeline

## Dependencies

### Python Packages
- `ollama` - Python client for Ollama API

### System Requirements
- **Ollama** - Local LLM runtime
  - macOS: `brew install ollama`
  - Linux: `curl -fsSL https://ollama.com/install.sh | sh`

- **LLaVA Vision Model**
  ```bash
  ollama pull llama3.2-vision:11b
  ```

### Storage
- Requires disk space in `data/media/` for downloaded images
- Images are retained for potential future reference

## Configuration

### Constants (in telegram_bridge.py)

```python
# Media storage directory
MEDIA_DIR = Path(__file__).parent.parent / "data" / "media"

# Supported vision extensions
VISION_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

# Vision model
MODEL = 'llama3.2-vision:11b'
```

## Testing

1. Send photo with no caption -> Valor should describe what's in it
2. Send screenshot of code -> Valor should be able to discuss the code
3. Send meme -> Valor should understand the humor/context
4. Send photo of whiteboard -> Valor should describe/transcribe content
5. Send image with caption -> Both description and caption should be included
6. Send image when Ollama is unavailable -> Should gracefully fall back

## Example Interactions

**User sends screenshot of error message:**
```
[User sent an image]
Image description: A terminal window showing a Python traceback.
The error is a KeyError: 'user_id' occurring in line 45 of app.py
within the get_user() function. The traceback shows the call originated
from handle_request() in routes.py.

What's causing this error?
```
Valor can now discuss the specific error without user having to type it out.

**User sends photo of whiteboard:**
```
[User sent an image]
Image description: A whiteboard with a system architecture diagram.
Shows three boxes labeled "Frontend", "API", and "Database" connected
by arrows. The Frontend connects to API via "REST", and API connects
to Database via "PostgreSQL". There's a note saying "Add Redis cache here?"

Can you help me think through this architecture?
```
Valor can discuss the diagram contents directly.
