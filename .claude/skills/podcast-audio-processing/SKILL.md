---
name: podcast-audio-processing
description: "Process NotebookLM audio files: convert, transcribe, add chapters, and prepare for publishing."
---

# Podcast Audio Processing

**Skill name:** `podcast-audio-processing`

Process podcast audio from NotebookLM: convert to mp3, transcribe with Whisper, create chapters, and embed metadata.

---

## When to Use This Skill

Use after receiving audio from NotebookLM (Phase 9 in workflow):

1. User has downloaded audio from NotebookLM (typically .m4a or .wav)
2. Episode needs: conversion, transcription, chapters, metadata embedding

---

## How to Invoke

```
Use the Task tool with subagent_type="general-purpose" and prompt:

"Process the podcast audio file for this episode using the podcast-audio-processing skill.

Episode path: apps/podcast/pending-episodes/YYYY-MM-DD-topic-slug
Audio filename: [filename user provided, e.g., 'Original_Audio.m4a']
Episode slug: YYYY-MM-DD-topic-slug

Follow the podcast-audio-processing skill to:
1. Convert to mp3 if needed (m4a → mp3)
2. Get file metadata (size in bytes, duration)
3. Transcribe with local Whisper (base model)
4. Analyze transcript and create 10-15 chapter markers
5. Embed chapters into mp3

CRITICAL: Report back the file metadata when complete:
- Duration: MM:SS format
- File size: bytes"
```

---

## Workflow

### Step 1: Convert Audio Format (if needed)

Check if the audio file is .m4a or .wav format. If so, convert to mp3:

```bash
cd ~/src/cuttlefish/apps/podcast/pending-episodes/EPISODE_PATH

# Convert to mp3 (128kbps for optimal size/quality)
ffmpeg -i "AUDIO_FILENAME.m4a" -codec:a libmp3lame -b:a 128k "EPISODE_SLUG.mp3" -y
```

**Note the metadata from ffmpeg output:**
- Duration (format: HH:MM:SS or MM:SS)
- This will be needed for publishing

### Step 2: Get File Metadata

Get the file size in bytes:

```bash
ls -l EPISODE_SLUG.mp3 | awk '{print $5}'
```

**Record:**
- File size: [bytes]
- Duration: [from ffmpeg output]

### Step 3: Generate Transcript with Local Whisper

Run Whisper transcription locally (no API key needed):

```bash
cd ~/src/cuttlefish/apps/podcast/tools

# Basic transcription (uv run auto-manages dependencies)
uv run python transcribe_only.py ../pending-episodes/EPISODE_PATH/EPISODE_SLUG.mp3 --model base

# OR with organized logging (recommended for production)
mkdir -p ../pending-episodes/EPISODE_PATH/logs
uv run python transcribe_only.py ../pending-episodes/EPISODE_PATH/EPISODE_SLUG.mp3 \
  --model base \
  --log-dir ../pending-episodes/EPISODE_PATH/logs \
  --quiet
```

**Whisper model options:**
- `tiny`: Fastest (~1-2 min for 30 min audio), basic accuracy
- `base`: **[recommended]** Fast (~5-10 min), good accuracy
- `small`: Slower (~15-20 min), better accuracy
- `medium`: Slowest (~30-40 min), best accuracy

**Default to `base` model unless user specifies otherwise.**

**Output:**
- Creates: `EPISODE_SLUG_transcript.json` in the episode directory
- With `--log-dir`: Also creates timestamped log file in logs/ directory
- `--quiet`: Suppresses progress messages (useful in automated workflows)

### Step 4: Analyze Transcript and Create Chapters

Read the transcript file and analyze it to identify natural topic transitions.

**Chapter creation guidelines:**
- Aim for 10-15 chapters for a 30-40 minute episode
- Each chapter should be 2-4 minutes long
- Chapter titles should be descriptive and capture the key topic/story
- Include subtitles or key concepts after the main title when helpful
- Analyze the full transcript to identify natural topic transitions
- Look for: topic shifts, new concepts introduced, story transitions, framework changes

**Create two chapter files:**

1. **FFmpeg metadata format** (`EPISODE_SLUG_chapters.txt`):
```
;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=120000
title=Introduction: The Topic Overview

[CHAPTER]
TIMEBASE=1/1000
START=120000
END=300000
title=Historical Context: Early Development
```

2. **Podcasting 2.0 format** (`EPISODE_SLUG_chapters.json`):
```json
{
  "version": "1.2.0",
  "chapters": [
    {
      "startTime": 0,
      "title": "Introduction: The Topic Overview"
    },
    {
      "startTime": 120,
      "title": "Historical Context: Early Development"
    }
  ]
}
```

**Important format notes:**
- FFmpeg format: START/END in milliseconds (TIMEBASE=1/1000)
- Podcasting 2.0 format: startTime in seconds (decimal)
- Last chapter END time should match audio duration in milliseconds

### Step 5: Embed Chapters into MP3

Embed the chapter metadata into the mp3 file:

```bash
cd ~/src/cuttlefish/apps/podcast/pending-episodes/EPISODE_PATH

# Embed chapters using FFmpeg metadata file
ffmpeg -i EPISODE_SLUG.mp3 -i EPISODE_SLUG_chapters.txt -map_metadata 1 -codec copy temp.mp3 -y

# Replace original with chaptered version
mv temp.mp3 EPISODE_SLUG.mp3
```

**Result:**
- Chapters embedded in mp3 file
- Will appear in podcast apps that support chapters (Overcast, Pocket Casts, Apple Podcasts)
- File size remains the same

---

## First-Time Setup

```bash
# Fix SSL certificates (macOS Python - one-time)
/Applications/Python\ 3.12/Install\ Certificates.command

# Dependencies auto-managed by uv - no manual install needed
# Just use: uv run python transcribe_only.py ...
```

---

## Technical Notes

### Whisper Transcription
- **Transcription is 100% local:** No API calls, completely private, free
- **Transcript files are large:** 300-400KB - read in sections if needed
- **Whisper timing:** base model takes ~5-10 minutes for 30-minute audio

### Chapter Support
- Modern podcast apps will display chapters; older apps will ignore them

---

## Files Created

After completion, these files should exist in the episode directory:

| File | Size | Description |
|------|------|-------------|
| `EPISODE_SLUG.mp3` | ~30MB | Final audio with embedded chapters |
| `EPISODE_SLUG_transcript.json` | ~400KB | Full transcript with timestamps |
| `EPISODE_SLUG_chapters.txt` | ~2KB | FFmpeg chapter format |
| `EPISODE_SLUG_chapters.json` | ~1KB | Podcasting 2.0 format |

---

## Error Handling

### Audio Conversion Errors

**If conversion fails:**
- Check that audio file exists and path is correct
- Verify ffmpeg is installed: `ffmpeg -version`

### Transcription Errors

**If transcription fails:**
- Always use `uv run python` (not bare `python`) - this ensures correct venv
- Verify audio file exists and path is correct
- Try smaller model (tiny) if base is too slow

### Chapter Embedding Errors

**If chapter embedding fails:**
- Verify chapters.txt file exists and has correct format
- Check that START/END times don't exceed audio duration
- Ensure TIMEBASE is set correctly (1/1000 for milliseconds)

---

## Final Report

When complete, report back to main agent:

- Audio processed: `EPISODE_SLUG.mp3`
- Duration: MM:SS
- File size: [bytes]
- Transcript generated: [N] words
- Chapters created: [N] chapters
- Chapters embedded in mp3
- Files ready for publishing phase
