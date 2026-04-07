# Transcription

## Overview

Audio and voice transcription using local Whisper models. Optimized for speed on Apple Silicon using `insanely-fast-whisper`.

**Current Implementation**: [insanely-fast-whisper](https://github.com/Vaibhavs10/insanely-fast-whisper) - uses Flash Attention 2 and batching for 10x+ faster transcription.

**Capabilities**: `transcribe`, `analyze`

## Installation

```bash
# Install via pipx (recommended)
pipx install insanely-fast-whisper

# Or via pip
pip install insanely-fast-whisper
```

Verify installation:

```bash
insanely-fast-whisper --help
```

### Apple Silicon Optimization

For best performance on Mac:

```bash
# Install with MPS (Metal Performance Shaders) support
pip install torch torchvision torchaudio
```

## Quick Start

```bash
# Transcribe an audio file
insanely-fast-whisper --file-name audio.mp3

# Output to JSON
insanely-fast-whisper --file-name audio.mp3 --transcript-path output.json
```

## Workflows

### Basic Transcription

```bash
# Transcribe with default settings (whisper-large-v3)
insanely-fast-whisper --file-name meeting.mp3

# Use a smaller/faster model
insanely-fast-whisper --file-name meeting.mp3 --model-name openai/whisper-small
```

### Telegram Voice Messages

```bash
# Telegram voice messages are .ogg format
insanely-fast-whisper --file-name voice_message.ogg
```

### With Timestamps

```bash
# Get word-level timestamps
insanely-fast-whisper --file-name podcast.mp3 --timestamp word
```

### Batch Processing

```bash
# Process multiple files
for f in audio/*.mp3; do
  insanely-fast-whisper --file-name "$f" --transcript-path "${f%.mp3}.json"
done
```

## Command Reference

| Option | Description |
|--------|-------------|
| `--file-name` | Path to audio file (required) |
| `--model-name` | Whisper model (default: openai/whisper-large-v3) |
| `--transcript-path` | Output file path (JSON) |
| `--timestamp` | Timestamp granularity: `chunk` or `word` |
| `--device-id` | GPU device ID (default: 0, use "mps" for Mac) |
| `--batch-size` | Batch size for processing (default: 24) |
| `--language` | Language code (auto-detected if not set) |

## Supported Formats

- MP3 (`.mp3`)
- WAV (`.wav`)
- M4A (`.m4a`)
- OGG (`.ogg`) - Telegram voice messages
- FLAC (`.flac`)
- WebM (`.webm`)

## Models

| Model | Size | Speed | Accuracy | Use Case |
|-------|------|-------|----------|----------|
| whisper-large-v3 | 1.5GB | Slow | Best | Final transcripts |
| whisper-medium | 769MB | Medium | Good | Balanced |
| whisper-small | 244MB | Fast | OK | Quick drafts |
| whisper-tiny | 39MB | Fastest | Basic | Testing |

## Python Integration

```python
import subprocess
import json

def transcribe(audio_path: str, model: str = "openai/whisper-large-v3") -> str:
    """Transcribe audio file to text."""
    result = subprocess.run(
        [
            "insanely-fast-whisper",
            "--file-name", audio_path,
            "--model-name", model,
            "--transcript-path", "/tmp/transcript.json"
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(f"Transcription failed: {result.stderr}")

    with open("/tmp/transcript.json") as f:
        data = json.load(f)

    return data.get("text", "")
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Out of memory | Use smaller model or reduce batch size |
| Slow on Mac | Ensure MPS is available (`--device-id mps`) |
| Wrong language | Specify `--language` explicitly |
| Format not supported | Convert to MP3/WAV first with ffmpeg |
| Module not found | Reinstall with `pipx install --force insanely-fast-whisper` |
