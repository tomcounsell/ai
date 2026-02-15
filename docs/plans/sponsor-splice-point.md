---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/52
---

# Sponsor Break Splice Point in Podcast Audio

## Problem

The podcast needs a reliable way to insert sponsor audio into episodes during post-production. Currently, `PodcastConfig.sponsor_break` tells the episodeFocus prompt to include a vague "natural transition point," but there's no specific trigger phrase, no timestamp detection, and no automated splicing.

**Current behavior:**
The manual `notebooklm_prompt.py` includes a loose SPONSOR BREAK section ("Include a natural transition point around the 10-12 minute mark"), but the API-side `generate_episode_focus()` has no sponsor instructions at all. Even when NotebookLM does produce a transition, nothing in the pipeline detects it or acts on it.

**Desired outcome:**
A scripted trigger phrase is embedded in the episodeFocus prompt. After transcription (Phase 10), the trigger is detected in the transcript and its timestamp is recorded. An optional splicing step uses ffmpeg to insert sponsor audio at that timestamp, producing a final episode audio file with the sponsor message seamlessly integrated.

## Appetite

**Size:** Small

**Team:** Solo dev. Prompt update, one new service function for detection, one for splicing, integration into the existing Audio Processing phase. No new workflow steps.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Design Decisions

### Trigger Phrase: Fixed, Not AI-Generated

The trigger phrase is a **fixed, deterministic string** configured on `PodcastConfig`: default `"Now, let's dive in."` NotebookLM hosts are instructed to say it verbatim after the intro. This is far more reliable than asking an AI to "include a natural transition" and then trying to detect what it said.

The phrase must be:
- Short (5-7 words) — less room for Whisper transcription variance
- Conversational — doesn't sound like an ad marker to listeners
- Unique within the episode — won't match other dialogue

### Timestamp Detection: Whisper Word-Level Timestamps

The current `transcribe_audio()` uses Whisper's basic transcription (text only). We need to switch to **word-level timestamps** (`timestamp_granularities=["word"]`) which returns each word with start/end times. Then we fuzzy-match the trigger phrase against the word sequence to find the splice timestamp.

This is more robust than searching for an exact substring in the plain transcript, because Whisper may transcribe "let's" as "let's" or "lets" or capitalize differently. Word-level matching with fuzzy tolerance handles this.

### Splicing: ffmpeg, Conditional on Sponsor Audio

Audio splicing is conditional:
1. If `PodcastConfig.sponsor_break` is False → skip entirely
2. If no active `Sponsor` exists for the podcast → skip (log warning)
3. If trigger phrase not found in transcript → skip (log warning, store unspliced)
4. If all conditions met → splice sponsor audio at the detected timestamp

The original unspliced audio is preserved (stored as `audio-raw.mp3`). The spliced version becomes the published `audio.mp3`.

### No New Workflow Step

Splice detection and insertion happen **within the existing Audio Processing phase** (Phase 10), after transcription and before chapter generation. This avoids disrupting the 12-step workflow — it's an enhancement to `step_transcribe_audio`, not a new phase.

## Solution

### Data Flow

```
episodeFocus prompt (includes trigger phrase instruction)
    ↓
NotebookLM generates audio with trigger phrase spoken
    ↓
Phase 9: generate_audio() → uploads raw audio to storage
    ↓
Phase 10: transcribe_audio() → Whisper with word timestamps
    ↓
detect_splice_point() → finds trigger phrase timestamp
    ↓
splice_sponsor_audio() → ffmpeg inserts sponsor clip at timestamp
    ↓
Episode.audio_url updated to spliced version
    ↓
step_generate_chapters → continues on spliced audio
```

### File Changes

#### 1. `apps/podcast/models/podcast_config.py` — Add trigger phrase field

```python
# Sponsor splice point
sponsor_trigger_phrase = models.CharField(
    max_length=100,
    default="Now, let's dive in.",
    blank=True,
    help_text="Exact phrase hosts say at the sponsor splice point. "
              "Must be short, conversational, and unique within the episode.",
)
```

Add to `to_dict()` export.

#### 2. `apps/podcast/tools/notebooklm_api.py` — Update `generate_episode_focus()`

Add `sponsor_trigger_phrase` parameter and inject splice point instructions:

```python
def generate_episode_focus(
    episode_title: str,
    series_name: str = "",
    sponsor_trigger_phrase: str = "",
) -> str:
```

When `sponsor_trigger_phrase` is provided, add to the prompt:

```
SPONSOR SPLICE POINT - IMPORTANT:
- After the opening section (hook + intro + structure preview), say EXACTLY this phrase: "{sponsor_trigger_phrase}"
- This phrase must be spoken verbatim — do not paraphrase or modify it
- It should sound natural, as a transition into the main content
- Do NOT reference sponsors or ads — this is just a natural pause point
- Timing: approximately 3-5 minutes into the episode, after the opening is complete
```

#### 3. `apps/podcast/tools/notebooklm_prompt.py` — Update manual prompt version

Replace the existing loose SPONSOR BREAK section with the same trigger phrase instruction. Read `sponsor_trigger_phrase` from config.

#### 4. `apps/podcast/services/audio.py` — Update `generate_audio()` to pass trigger phrase

```python
# Load podcast config for sponsor settings
try:
    config = episode.podcast.config
    trigger = config.sponsor_trigger_phrase if config.sponsor_break else ""
except PodcastConfig.DoesNotExist:
    trigger = ""

episode_focus = generate_episode_focus(
    episode.title, episode.podcast.title, sponsor_trigger_phrase=trigger
)
```

Also: after `store_file()`, store the raw audio under a separate key (`audio-raw.mp3`) before any splicing happens in the transcription step.

#### 5. `apps/podcast/services/audio.py` — Update `transcribe_audio()` for word timestamps

Change the Whisper API call to request word-level timestamps:

```python
transcription = client.audio.transcriptions.create(
    model="whisper-1",
    file=audio_file,
    response_format="verbose_json",
    timestamp_granularities=["word"],
)
```

Store the full transcript JSON (with word timestamps) in an `EpisodeArtifact`:

```python
EpisodeArtifact.objects.update_or_create(
    episode=episode,
    title="transcript-words",
    defaults={
        "content": json.dumps(transcription.words),
        "description": "Word-level Whisper transcript with timestamps.",
        "workflow_context": "Audio Processing",
    },
)
```

Continue saving `Episode.transcript` as plain text for downstream consumers (chapters, metadata).

#### 6. `apps/podcast/services/splice.py` — New module: detection + splicing

Two public functions:

**`detect_splice_point(episode_id: int) -> float | None`**

```python
def detect_splice_point(episode_id: int) -> float | None:
    """Find the timestamp of the sponsor trigger phrase in the transcript.

    Reads the transcript-words artifact (word-level Whisper output),
    searches for a fuzzy match of the trigger phrase, and returns the
    start timestamp in seconds. Returns None if not found.

    Stores the result in Episode metadata or an artifact for audit.
    """
```

Implementation:
- Load `transcript-words` artifact (list of `{word, start, end}` dicts)
- Load `PodcastConfig.sponsor_trigger_phrase`, split into words
- Slide a window of len(trigger_words) across the transcript words
- For each window, compute similarity (e.g., `SequenceMatcher` ratio on joined text)
- If best match exceeds threshold (0.8) → return `start` timestamp of first word in the window
- Store detected timestamp in a `splice-point` artifact for debugging

**`splice_sponsor_audio(episode_id: int) -> str | None`**

```python
def splice_sponsor_audio(episode_id: int) -> str | None:
    """Insert sponsor audio at the detected splice point.

    Steps:
        1. Check PodcastConfig.sponsor_break is True.
        2. Find active Sponsor for the podcast (from #53).
        3. Load splice point timestamp from detect_splice_point().
        4. Download episode audio and sponsor audio.
        5. Use ffmpeg to split episode at timestamp and concatenate:
           [episode_part1] + [sponsor_audio] + [episode_part2]
        6. Upload spliced audio to storage, update Episode.audio_url.

    Returns:
        The new audio URL if spliced, None if skipped.
    """
```

Implementation:
- Uses `subprocess.run()` with ffmpeg for audio manipulation
- ffmpeg commands: `ffmpeg -i input.mp3 -t {timestamp} part1.mp3` and `ffmpeg -i input.mp3 -ss {timestamp} part2.mp3`
- Concatenation: `ffmpeg -i concat:part1.mp3|sponsor.mp3|part2.mp3 -c copy output.mp3`
- All done in a `tempfile.TemporaryDirectory`
- Stores spliced audio at the same storage key (overwrites), raw preserved at `audio-raw.mp3`
- Updates `Episode.audio_url` and `Episode.audio_file_size_bytes`

#### 7. `apps/podcast/tasks.py` — Wire splicing into `step_transcribe_audio`

Update `step_transcribe_audio` to call splicing after transcription:

```python
@task
def step_transcribe_audio(episode_id: int) -> None:
    """Transcribe audio via Whisper API, then detect and splice sponsor break."""
    _acquire_step_lock(episode_id, "Audio Processing")
    try:
        audio.transcribe_audio(episode_id)

        # Sponsor splice (conditional — skips gracefully if not configured)
        from apps.podcast.services.splice import (
            detect_splice_point,
            splice_sponsor_audio,
        )

        splice_ts = detect_splice_point(episode_id)
        if splice_ts is not None:
            splice_sponsor_audio(episode_id)

        step_generate_chapters.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Audio Processing", str(exc))
        raise
```

#### 8. `apps/podcast/tests/test_splice.py` — Tests

- Test `detect_splice_point()` with mocked transcript-words artifact containing the trigger phrase
- Test detection with slight Whisper transcription variance (e.g., missing apostrophe)
- Test detection returns None when trigger phrase is absent
- Test `splice_sponsor_audio()` with mocked ffmpeg subprocess and storage
- Test that splicing is skipped when `sponsor_break` is False
- Test that splicing is skipped when no active Sponsor exists
- Test that raw audio is preserved at `audio-raw.mp3` key

### What Does NOT Change

- **Workflow steps** — No new phases. Splicing is part of existing Audio Processing.
- **Episode model** — No new fields. Splice metadata lives in artifacts.
- **Sponsor model** — That's #53. This issue assumes `Sponsor` exists with `effective_audio_url`.
- **Chapter generation** — Runs on the spliced audio, so chapters include the sponsor segment. This is correct — the published audio includes the sponsor.
- **`content_plan` / `plan_episode`** — No changes. The trigger phrase is a prompt-level concern, not a planning concern.

### Migration

One migration: add `sponsor_trigger_phrase` CharField to `PodcastConfig`. Non-destructive — has a default value.

**Note:** Migration will be created but not applied per project guidelines.

## Dependencies

- **#53 (Sponsor Message Audio)**: `splice_sponsor_audio()` reads `Sponsor.effective_audio_url` to get the sponsor clip. If #53 isn't built yet, the splicing step gracefully skips (no active sponsor found). The detection step works independently.
- **ffmpeg**: Must be available on the server. Already commonly available on Render. Add `ffmpeg` to the Render build if not present (`apt-get install ffmpeg` in build script).

## Rabbit Holes

- **Multiple splice points**: Supporting 2+ sponsor breaks (mid-roll, post-roll). Doable by making the trigger phrase a list, but unnecessary for now. One splice point is the standard for 30-40 minute episodes.
- **Loudness normalization**: Matching sponsor audio loudness (LUFS) to episode audio. Important for production quality but adds ffmpeg filter complexity. Can be a follow-up.
- **Fade in/out**: Cross-fading between episode and sponsor audio for smoother transitions. Nice to have, not essential. Can be added to the ffmpeg command later.
- **Dynamic ad insertion (DAI)**: Serving different sponsor audio to different listeners at playback time. Completely different architecture (requires a DAI-capable podcast host). Out of scope.

## No-Gos

- No multiple splice points per episode
- No dynamic/per-listener ad insertion
- No loudness normalization (follow-up)
- No audio crossfade (follow-up)
- No new workflow phases

## Acceptance Criteria

1. `PodcastConfig.sponsor_trigger_phrase` field exists with default `"Now, let's dive in."`
2. `generate_episode_focus()` includes verbatim trigger phrase instruction when sponsor_break is enabled
3. `transcribe_audio()` uses Whisper word-level timestamps and stores `transcript-words` artifact
4. `detect_splice_point()` fuzzy-matches trigger phrase in word timestamps, returns timestamp or None
5. `splice_sponsor_audio()` uses ffmpeg to insert sponsor audio at detected timestamp
6. Raw (unspliced) audio preserved at `audio-raw.mp3` storage key
7. Splicing gracefully skips when: sponsor_break is False, no active Sponsor, trigger phrase not found
8. Existing `step_transcribe_audio` task calls detection and splicing after transcription
9. Tests cover detection (exact match, fuzzy match, not found) and splicing (happy path, skip conditions)
10. Migration created (not applied)
11. Pre-commit hooks pass
