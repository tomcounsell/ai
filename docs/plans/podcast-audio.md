# PRD: Yudame Research Podcast Audio Engine

---

## Status Update (2026-02-05)

### Implementation Assessment

This document originally proposed a **Gemini TTS-based audio pipeline** with full script control for generating podcast audio. Since then, the actual podcast workflow has evolved to use **NotebookLM** as the primary audio generation method, which takes a fundamentally different approach.

### What Was Implemented (Different Approach)

The production system uses NotebookLM (via Enterprise API or manual web interface) instead of Gemini TTS:

| Original Plan | Current Implementation | Notes |
|---------------|------------------------|-------|
| Claude generates ~5,200 word script | NotebookLM generates conversational audio from research docs | Two-host format vs. single narrator |
| Gemini TTS converts script to audio | NotebookLM creates AI-hosted discussion | More dynamic, less controlled |
| Embedded TTS directives | `content_plan.md` provides structural guidance | Different control mechanism |
| Section-by-section generation | Single audio generation pass | Simpler pipeline |
| 128kbps MP3 @ ~30MB | 128kbps MP3 @ ~30-40MB | Similar output specs |

**Implemented tools and skills:**
- `.claude/skills/podcast-audio-processing/SKILL.md` - Audio conversion, Whisper transcription, chapter creation
- `.claude/skills/notebooklm-enterprise-api/SKILL.md` - Automated NotebookLM audio generation (requires paid subscription)
- `.claude/skills/notebooklm-audio/SKILL.md` - Manual NotebookLM fallback workflow
- `apps/podcast/tools/notebooklm_api.py` - Discovery Engine API integration
- `apps/podcast/tools/notebooklm_prompt.py` - Prompt generation for manual workflow
- `apps/podcast/tools/transcribe_only.py` - Local Whisper transcription

### Items Achieved (Via Different Methods)

- **Audio Duration Control** - Achieved via `content_plan.md` structure guidance (not script word count)
- **Voice Identity** - NotebookLM maintains consistent two-host personalities
- **Chapter Support** - Implemented in `podcast-audio-processing` skill (FFmpeg metadata + Podcasting 2.0 JSON)
- **128kbps MP3 Output** - Standard across all workflows
- **Quality Assurance** - Implemented via Episode Quality Scorecard (Wave 1 validated)

### Items Not Implemented (Gemini TTS Specific)

These items are specific to the Gemini TTS approach and were not pursued:

- **Script-based generation** - NotebookLM doesn't use pre-written scripts
- **TTS directive embedding** (`[VOICE: warm]`, `[PAUSE: 0.8s]`, etc.) - Not applicable to NotebookLM
- **Section-by-section WAV generation** - NotebookLM generates complete audio
- **PCM-to-WAV-to-MP3 pipeline** - NotebookLM outputs MP3 directly
- **Parallel section processing** - Single-pass generation used instead

### Valuable Future Work

These concepts remain valuable and could be implemented for scenarios where NotebookLM doesn't meet needs:

1. **Single-narrator episodes** - Gemini TTS approach would be ideal for solo host format
2. **Fine-grained prosody control** - When specific emotional delivery is required
3. **Script-first workflow** - For episodes requiring exact word-for-word delivery
4. **Hybrid approach** - Use NotebookLM for conversation, Gemini TTS for intros/outros

### Recommendation

**Archive this document as a reference design** rather than an active plan. The NotebookLM-based workflow has proven effective (Wave 1 validated with +16 point improvement). If Gemini TTS becomes desirable for specific use cases (single narrator, precise control), this PRD provides a solid starting point.

---

## Original Document (Archived for Reference)

> **Note:** The content below represents the original PRD for a Gemini TTS-based approach. It has not been implemented but is preserved as a reference design.

---

This document outlines the technical and creative requirements for generating podcast audio using **Gemini TTS API** with full script control.

---

## 1. Executive Summary

**Target Duration:** 35 Minutes (30-40 acceptable)

**Approach:** Text-first with embedded directives -> Gemini TTS

**Model:** `gemini-2.5-flash-preview-tts` or `gemini-2.5-pro-preview-tts`

**Primary Voice:** Alnilam

The system generates a complete ~5,200 word script with embedded TTS directives, then converts to audio via Gemini's TTS endpoint. This provides full duration control (script length = audio length) unlike the Live API approach.

---

## 2. Architecture Overview

```
+---------------------------------------------------------------------+
|                     TEXT-FIRST AUDIO PIPELINE                        |
+---------------------------------------------------------------------+
|                                                                      |
|  +--------------+    +--------------+    +--------------+          |
|  |  report.md   |    | p3-briefing  |    |  sources.md  |          |
|  |  (~18KB)     |    |  (~60KB)     |    |  (~10KB)     |          |
|  +------+-------+    +------+-------+    +------+-------+          |
|         |                   |                   |                   |
|         +-------------------+-------------------+                   |
|                             |                                       |
|                             v                                       |
|                   +-----------------+                               |
|                   |   LLM (Claude)  |                               |
|                   | Script Generator|                               |
|                   +--------+--------+                               |
|                            |                                        |
|                            v                                        |
|                   +-----------------+                               |
|                   |   script.md     |  <-- ~5,200 words             |
|                   |  + Directives   |      ~35 min spoken           |
|                   +--------+--------+                               |
|                            |                                        |
|         +------------------+------------------+                    |
|         v                  v                  v                     |
|  +-------------+    +-------------+    +-------------+             |
|  |  Section 1  |    |  Section 2  |    |  Section 3  |             |
|  | Foundation  |    |  Evidence   |    | Application |             |
|  | (~12 min)   |    |  (~12 min)  |    |  (~11 min)  |             |
|  +------+------+    +------+------+    +------+------+             |
|         |                  |                  |                     |
|         v                  v                  v                     |
|  +-------------+    +-------------+    +-------------+             |
|  | Gemini TTS  |    | Gemini TTS  |    | Gemini TTS  |             |
|  |   API       |    |   API       |    |   API       |             |
|  +------+------+    +------+------+    +------+------+             |
|         |                  |                  |                     |
|         v                  v                  v                     |
|  +-------------+    +-------------+    +-------------+             |
|  |  WAV Audio  |    |  WAV Audio  |    |  WAV Audio  |             |
|  |  part_1.wav |    |  part_2.wav |    |  part_3.wav |             |
|  +------+------+    +------+------+    +------+------+             |
|         |                  |                  |                     |
|         +------------------+------------------+                    |
|                            |                                        |
|                            v                                        |
|                   +-----------------+                               |
|                   |    STITCHER     |                               |
|                   |  + Room Tone    |                               |
|                   +--------+--------+                               |
|                            |                                        |
|                            v                                        |
|                   +-----------------+                               |
|                   |   Final MP3     |                               |
|                   |   128kbps       |                               |
|                   |   ~30MB/35min   |                               |
|                   +-----------------+                               |
|                                                                      |
+---------------------------------------------------------------------+
```

---

## 3. Technical Stack & Configuration

### 3.1 TTS API Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Model** | `gemini-2.5-flash-preview-tts` | Low latency, cost-effective |
| **Voice** | `Alnilam` | Matches Yudame Research voice identity |
| **Output Format** | PCM 16-bit @ 24kHz (Mono) | Highest fidelity for post-processing |
| **Response Modality** | `["AUDIO"]` | Pure audio output |

### 3.2 API Configuration Example

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

response = client.models.generate_content(
    model="gemini-2.5-flash-preview-tts",
    contents=script_text_with_directives,
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name='Alnilam'
                )
            )
        )
    )
)

# Extract PCM audio data
audio_data = response.candidates[0].content.parts[0].inline_data.data
```

### 3.3 Available Voices

| Voice | Notes |
|-------|-------|
| **Alnilam** | Primary - matches voice identity |
| Kore | Female alternative |
| Charon | Male alternative |
| Puck | Lighter tone |
| Zephyr | -- |
| Aoede | Female |
| Achernar | Female |

Full list: Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager, Sulafat

---

## 4. Script Generation with Embedded Directives

### 4.1 Script Specifications

| Metric | Target | Notes |
|--------|--------|-------|
| **Word count** | ~5,200 words | At 150 wpm speaking pace |
| **Duration** | ~35 minutes | Predictable from word count |
| **Sections** | 3 | Foundation, Evidence, Application |
| **File** | `script.md` | New output file |

### 4.2 Directive Syntax

Directives are embedded inline in the script text. Gemini TTS interprets natural language style cues.

```markdown
[VOICE: warm, authoritative]
[PACE: measured]

Welcome to Yudame Research.

[PAUSE: 0.8s]

Today, we're examining something that challenges everything you thought
you knew about cardiovascular health.

[PACE: building energy]
[VOICE: curious, leaning in]

Here's where it gets interesting...

[EMPHASIS: strong]
The effect size was 0.8--that's substantial.

[PAUSE: 1.2s]
[VOICE: matter-of-fact, precise]

Now let's look at the evidence...
```

### 4.3 Directive Categories

| Category | Options | Usage |
|----------|---------|-------|
| **VOICE** | warm, authoritative, curious, skeptical, emphatic, reflective, matter-of-fact, precise, excited, grave | Set emotional tone |
| **PACE** | measured, slightly faster, slower, building energy, deliberate | Control speaking speed |
| **PAUSE** | 0.3s, 0.5s, 0.8s, 1.2s, 2.0s | Insert silence |
| **EMPHASIS** | strong, subtle, none | Word-level stress |
| **TRANSITION** | new section, callback, revelation, conclusion | Structural markers |

### 4.4 Voice Identity Mapping

From `docs/design/VOICE-IDENTITY.md`:

| Context | Directive |
|---------|-----------|
| Introducing a topic | `[VOICE: curious, inviting]` |
| Explaining methodology | `[VOICE: precise, matter-of-fact]` |
| Revealing key findings | `[VOICE: energized, emphatic]` |
| Challenging assumptions | `[VOICE: direct, confident]` |
| Synthesizing conclusions | `[VOICE: thoughtful, assured]` |
| Call to action | `[VOICE: warm, encouraging]` |

### 4.5 Characteristic Patterns with Directives

**Opening a topic:**
```markdown
[VOICE: curious, inviting]
[PACE: measured]
Now, this is where it becomes fascinating...
[PAUSE: 0.5s]
```

**Building an argument:**
```markdown
[VOICE: precise, building confidence]
You see, the evidence suggests...
[PAUSE: 0.3s]
Let us be precise about this.
```

**Delivering insights:**
```markdown
[VOICE: emphatic]
[PACE: slower, deliberate]
And this is consequential.
[PAUSE: 0.8s]
Once you see it, you cannot unsee it.
```

**Transitions:**
```markdown
[TRANSITION: new section]
[PAUSE: 1.2s]
[VOICE: shifting energy]
But here is where it gets interesting.
```

**Conclusions:**
```markdown
[VOICE: warm, encouraging]
[PACE: measured]
So here is the takeaway.
[PAUSE: 0.5s]
The evidence is compelling, the mechanism is clear, and the applications are practical.
```

---

## 5. Script Structure Template

### 5.1 Complete Script Format

```markdown
# [Episode Title] - Full Script

[VOICE: warm, authoritative]
[PACE: measured]

## Opening Hook (90 seconds)

[Hook content - counterintuitive finding, surprising statistic, or provocative question]

[PAUSE: 1.2s]

## Brand + Mission (30 seconds)

[VOICE: confident, welcoming]
Welcome to Yudame Research. Our mission today is [specific goal]: by the end
of this episode, you'll understand [what listener will know/be able to do].

[PAUSE: 0.5s]

## Research Process Summary (30 seconds)

[VOICE: matter-of-fact, grounding]
To answer this question, we synthesized [N] peer-reviewed studies, [N]
meta-analyses, and consulted primary sources from [institutions]. Here's
what the evidence actually shows.

[PAUSE: 0.8s]

## Roadmap (60 seconds)

[VOICE: clear, organized]
We'll move through this in three parts. First, [Section 1 preview].
Second, [Section 2 preview]. Third, [Section 3 preview].

[TRANSITION: new section]
[PAUSE: 1.5s]

---

## Section 1: Foundation (~12 minutes)

[VOICE: curious, building understanding]
[PACE: measured, educational]

### Core Concept 1

[Content with embedded directives...]

[PAUSE: 0.5s]

### Core Concept 2

[VOICE: precise]
[Content...]

### Section 1 Synthesis

[VOICE: connecting ideas]
[Brief synthesis of concepts...]

[TRANSITION: new section]
[PAUSE: 1.2s]

---

## Section 2: Evidence (~12 minutes)

[VOICE: analytical, building credibility]
[PACE: slightly faster, engaged]

### Section Hook

[Re-engage listener...]

### Evidence Cluster A

[VOICE: citing with authority]
According to a 2023 meta-analysis published in Nature...

[PAUSE: 0.3s]

[VOICE: translating significance]
To put that in perspective, that's [tangible comparison].

### Evidence Cluster B

[Content...]

### Evidence Synthesis

[VOICE: balanced, honest]
Where the evidence agrees: [points of consensus].
Where it conflicts: [points of disagreement].
Why: [explanation].

[TRANSITION: new section]
[PAUSE: 1.2s]

---

## Section 3: Application (~11 minutes)

[VOICE: practical, actionable]
[PACE: clear, instructional]

### Section Hook

[VOICE: shifting to action]
Now let's translate this into practice...

### Protocol 1

[VOICE: specific, instructional]
[Specific parameters: timing, duration, frequency, dosage...]

### Protocol 2

[Content...]

### Caveats and Context

[VOICE: honest, nuanced]
Who this applies to: [target population].
Who should modify: [exceptions].
What we don't know: [limitations].

### Episode Synthesis

[VOICE: bringing it together]
[PAUSE: 0.8s]

Here's what we know: [What].
This matters because: [So What].
What to do about it: [Now What].

[PAUSE: 1.0s]

### Callback to Opening

[VOICE: completing the arc]
We opened with [reference to hook]. Now you understand why [resolution].

[PAUSE: 0.8s]

### Closing

[VOICE: warm, encouraging]
[PACE: measured, final]

Find the full research and sources at research dot yuda dot me--that's
Y-U-D-A dot M-E.

[PAUSE: 0.5s]

Until next time.

[PAUSE: 2.0s]
```

---

## 6. Audio Generation Pipeline

### 6.1 Workflow Phase

```
PHASE 9: AUDIO GENERATION (Gemini TTS)

ENTRY REQUIREMENTS:
- report.md created (Phase 7)
- sources.md available (validated links)
- script.md created (Phase 8 - Script Generation)

INPUT FILES:
1. script.md (complete spoken script with directives, ~5,200 words)

WORK TO DO:
1. Split script.md into 3 sections at [TRANSITION: new section] markers
2. Generate audio for each section via Gemini TTS API
3. Save sections as WAV files
4. Stitch sections with room tone transitions
5. Export final MP3 (128kbps)
6. Generate transcript from script (strip directives)

EXIT CRITERIA:
- YYYY-MM-DD-slug.mp3 exists (~30MB for 35 min)
- Duration: 30-40 minutes (target ~35 min)
- Natural prosody with emotional variation
- All sections stitched seamlessly
```

### 6.2 Single-Call vs Split Generation

**Single-call approach** (recommended):

The TTS API has a 32k token context limit. A 5,200-word script is ~7k tokens, so it fits comfortably in a single API call. This is simpler and avoids stitching artifacts.

**When to split into sections:**

1. **Error recovery** - If generation fails, can retry individual sections
2. **Very long scripts** - If script exceeds 25k tokens
3. **Quality issues** - Can regenerate problematic sections only

### 6.3 Implementation

```python
#!/usr/bin/env python3
"""
Yudame Research TTS Audio Generator

Generates podcast audio from script.md using Gemini TTS API.

Usage:
    python generate_audio_tts.py <episode_dir>
    python generate_audio_tts.py ../episodes/2025-12-24-topic-slug/

Environment:
    GOOGLE_API_KEY - Required (stored in /Users/valorengels/.env)
"""

import os
import re
import wave
from pathlib import Path

from google import genai
from google.genai import types
from pydub import AudioSegment


# Configuration
TTS_MODEL = "gemini-2.5-flash-preview-tts"
VOICE = "Alnilam"
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
OUTPUT_BITRATE = "128k"


def generate_audio(client: genai.Client, script_text: str) -> bytes:
    """Generate audio for the full script using Gemini TTS."""

    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=script_text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=VOICE
                    )
                )
            )
        )
    )

    return response.candidates[0].content.parts[0].inline_data.data


def pcm_to_wav(pcm_data: bytes, output_path: Path):
    """Convert raw PCM data to WAV file."""
    with wave.open(str(output_path), 'wb') as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)


def strip_directives(script_text: str) -> str:
    """Remove directives to create plain transcript."""
    text = re.sub(r'\[(?:VOICE|PACE|PAUSE|EMPHASIS|TRANSITION):[^\]]+\]', '', script_text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def generate_episode(episode_dir: str):
    """Generate complete podcast audio from script.md."""

    episode_path = Path(episode_dir).resolve()
    script_path = episode_path / "script.md"

    if not script_path.exists():
        raise FileNotFoundError(f"Missing script.md in {episode_path}")

    print(f"Generating audio for: {episode_path.name}")
    print("=" * 60)

    # Initialize client
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    # Load script
    print("\n[1/3] Loading script...")
    script_text = script_path.read_text()
    word_count = len(script_text.split())
    print(f"  Words: {word_count} (~{word_count/150:.1f} min estimated)")

    # Generate audio (single call)
    print("\n[2/3] Generating audio...")
    audio_data = generate_audio(client, script_text)

    # Save as WAV then convert to MP3
    tmp_dir = episode_path / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    wav_path = tmp_dir / "full_audio.wav"
    pcm_to_wav(audio_data, wav_path)

    # Convert to MP3
    episode_slug = episode_path.name
    output_path = episode_path / f"{episode_slug}.mp3"

    audio = AudioSegment.from_wav(str(wav_path))
    audio.export(
        str(output_path),
        format="mp3",
        bitrate=OUTPUT_BITRATE,
        tags={
            "artist": "Yudame Research",
            "album": "Yudame Research Podcast",
        }
    )

    duration_seconds = len(audio) / 1000
    file_size_mb = output_path.stat().st_size / (1024 * 1024)

    print(f"  Duration: {duration_seconds/60:.1f} minutes")
    print(f"  File size: {file_size_mb:.1f} MB")

    # Generate transcript
    print("\n[3/3] Generating transcript...")
    transcript = strip_directives(script_text)
    transcript_path = episode_path / f"{episode_slug}_transcript.txt"
    transcript_path.write_text(transcript)
    print(f"  Saved: {transcript_path.name}")

    # Cleanup
    wav_path.unlink()
    tmp_dir.rmdir()

    print("\n" + "=" * 60)
    print("Audio generation complete!")
    print(f"Output: {output_path}")

    return output_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python generate_audio_tts.py <episode_dir>")
        sys.exit(1)

    if not os.environ.get("GOOGLE_API_KEY"):
        print("Error: GOOGLE_API_KEY not set")
        sys.exit(1)

    generate_episode(sys.argv[1])
```

---

## 7. Quality Assurance

### 7.1 Script Quality Checks

Before TTS generation:

- [ ] Word count ~5,200 (+/-500)
- [ ] Three sections clearly marked with `[TRANSITION: new section]`
- [ ] Opening hook present
- [ ] Brand + mission statement included
- [ ] Research process summary included
- [ ] Closing with website URL
- [ ] Directives distributed throughout (not clustered)
- [ ] Technical terms defined on first use
- [ ] Protocols include specific parameters

### 7.2 Audio Quality Checks

After generation:

- [ ] Duration 30-40 minutes (target 35)
- [ ] Natural prosody (no robotic sections)
- [ ] Emotional variation matches directives
- [ ] Pauses feel natural (not too long/short)
- [ ] Section transitions smooth
- [ ] No audio artifacts or glitches
- [ ] File size ~25-35MB

---

## 8. Cost Estimation

| Component | Tokens/Cost | Notes |
|-----------|-------------|-------|
| Script Generation | ~50k tokens | Claude/GPT |
| TTS Generation (3 sections) | ~20k tokens | Gemini TTS |
| **Total per Episode** | ~70k tokens | ~$0.10-0.20 |

**Comparison:**
- NotebookLM: Free but no control
- Live API: ~$0.50 but unpredictable duration
- TTS API: ~$0.15 with full control

---

## 9. File Structure

```
episode-directory/
|-- research/
|   |-- p1-brief.md
|   |-- p2-*.md
|   +-- p3-briefing.md
|-- report.md              # Research synthesis
|-- sources.md             # Validated links
|-- script.md              # NEW: Full TTS script with directives
|-- YYYY-MM-DD-slug.mp3    # Final audio
|-- YYYY-MM-DD-slug_transcript.txt  # Plain text (directives stripped)
+-- tmp/
    |-- section_1.wav      # Intermediate (deleted after stitch)
    |-- section_2.wav
    +-- section_3.wav
```

---

## 10. Environment Setup

### 10.1 Dependencies

```bash
# requirements.txt
google-genai>=1.0.0
pydub>=0.25.1
numpy>=1.24.0
```

### 10.2 System Dependencies

```bash
# macOS
brew install ffmpeg

# Verify
ffmpeg -codecs | grep -E "pcm_s16le|libmp3lame"
```

### 10.3 API Key

```bash
# API keys stored in /Users/valorengels/.env (auto-loaded via ~/.zshenv)
grep GOOGLE_API_KEY /Users/valorengels/.env
```

---

## 11. Comparison: TTS vs Live API

| Aspect | TTS API (New) | Live API (Previous) |
|--------|---------------|---------------------|
| **Duration control** | Full (script length = audio length) | Unpredictable |
| **Model decides when done** | No | Yes (problem!) |
| **Session management** | None needed | Complex resumption tokens |
| **Error recovery** | Per-section | Per-session |
| **Parallel processing** | Yes | No |
| **Cost** | Lower (~$0.15) | Higher (~$0.50) |
| **Latency** | Higher (batch) | Lower (streaming) |
| **Use case** | Long-form content | Real-time conversation |

---

## 12. References

- [Gemini Speech Generation Docs](https://ai.google.dev/gemini-api/docs/speech-generation)
- [Cloud TTS Gemini Documentation](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts)
- [Gemini 2.5 TTS Announcement](https://blog.google/technology/developers/gemini-2-5-text-to-speech/)
- [Voice Identity](/docs/design/VOICE-IDENTITY.md)

---

*Original Document: 2025-12-24*
*Status Update: 2026-02-05*
*Version: 3.0 - TTS-based approach (archived - NotebookLM implemented instead)*
