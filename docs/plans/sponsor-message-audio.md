---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/53
---

# Sponsor Message Audio Generation

## Problem

The podcast workflow has a `sponsor_break` flag on `PodcastConfig` and issue #52 will handle splice point detection in episode audio. But there is no system for creating the sponsor audio clips themselves — the actual 15-30 second messages that get inserted at those splice points.

**Current behavior:**
No sponsor message infrastructure exists. `PodcastConfig.sponsor_break` is a boolean that tells the episodeFocus prompt to include a natural transition point, but nothing produces the audio that goes there.

**Desired outcome:**
A `Sponsor` model to manage sponsor relationships and messaging, a TTS-based audio generation service to produce sponsor clips, and integration with the file storage service for persistent sponsor audio. Clips are produced independently of episode production and can be reused, rotated, or regenerated.

## Appetite

**Size:** Small

**Team:** Solo dev. One new model, one service function, one management command. No workflow changes.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Design Decisions

### TTS-Generated, Not Pre-Recorded

The issue asks: pre-recorded reads, TTS, or a mix? **TTS-generated via OpenAI's speech API.** Rationale:
- We already depend on OpenAI (Whisper for transcription, GPT for research). No new vendor.
- Sponsor messages change frequently (new copy, seasonal campaigns). Regeneration should be instant, not require booking a voice actor.
- OpenAI TTS voices (alloy, echo, fable, onyx, nova, shimmer) are production-quality at 128kbps MP3.
- Pre-recorded uploads can be supported later as a simple URL override on the model — no architecture change needed.

### Static Per-Sponsor, Not Dynamic Per-Episode

The issue asks: static per sponsor or dynamic per episode? **Static per sponsor.** Each sponsor has one active message that gets spliced into any episode. Rationale:
- Simpler to manage and audit. The sponsor approves one message, it goes everywhere.
- Episode-aware messages ("This episode about sleep science is brought to you by...") sound good but add fragile coupling — if episode titles change, sponsor audio is stale.
- Can evolve to per-episode later by adding an optional `episode` FK and a generation service that takes episode context. Not needed now.

### Audio Specs

- **Format:** MP3, 128kbps, 44.1kHz mono — matches NotebookLM episode output
- **Duration:** 15-30 seconds (enforced by copy length, not hard audio trimming)
- **Voice:** Configurable per sponsor via `voice` field (OpenAI voice name)

## Solution

### Overview

```
Sponsor model (name, copy, voice, audio_url, is_active)
       ↓
generate_sponsor_audio() service
       ↓ OpenAI TTS API
MP3 bytes → store_file() → Supabase/S3
       ↓
Sponsor.audio_url updated
```

The splice point system (#52) reads `Sponsor.audio_url` when assembling final episode audio. This issue only covers producing and storing the clip.

### File Changes

#### 1. `apps/podcast/models/sponsor.py` — New model

```python
class Sponsor(Timestampable):
    """A podcast sponsor with generated audio message."""

    podcast = models.ForeignKey(
        "podcast.Podcast",
        on_delete=models.CASCADE,
        related_name="sponsors",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField()
    message_copy = models.TextField(
        help_text="The sponsor message script. Keep to 2-4 sentences for 15-30 second read."
    )
    voice = models.CharField(
        max_length=20,
        default="nova",
        help_text="OpenAI TTS voice: alloy, echo, fable, onyx, nova, shimmer",
    )
    audio_url = models.URLField(
        blank=True,
        help_text="Generated audio URL. Auto-populated by generate_sponsor_audio.",
    )
    audio_override_url = models.URLField(
        blank=True,
        help_text="Optional pre-recorded audio URL. When set, overrides TTS generation.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only active sponsors are eligible for episode insertion.",
    )

    class Meta:
        ordering = ["name"]
        unique_together = [("podcast", "slug")]

    def __str__(self):
        return self.name

    @property
    def effective_audio_url(self) -> str:
        """Return override URL if set, otherwise generated URL."""
        return self.audio_override_url or self.audio_url
```

Key design choices:
- `message_copy` is the script text. Regenerating audio is just re-running TTS on this field.
- `audio_override_url` allows pre-recorded uploads without changing the generation flow. If set, it takes priority.
- `effective_audio_url` property abstracts this for consumers (#52 splice system).
- Scoped to a `Podcast` — different podcasts can have different sponsors.

#### 2. `apps/podcast/models/__init__.py` — Export new model

Add `Sponsor` to imports and `__all__`.

#### 3. `apps/podcast/admin.py` — Register Sponsor admin

Simple admin registration with list display (name, podcast, is_active, voice) and a "Regenerate Audio" admin action.

#### 4. `apps/podcast/services/sponsor_audio.py` — TTS generation service

```python
def generate_sponsor_audio(sponsor_id: int) -> str:
    """Generate TTS audio for a sponsor message and upload to storage.

    Steps:
        1. Load Sponsor by id.
        2. If audio_override_url is set, skip generation (return override).
        3. Call OpenAI TTS API with message_copy and voice.
        4. Upload MP3 bytes to storage via store_file().
        5. Update Sponsor.audio_url.

    Args:
        sponsor_id: Primary key of the Sponsor.

    Returns:
        The public URL of the sponsor audio file.
    """
```

Implementation details:
- Uses `openai.OpenAI().audio.speech.create(model="tts-1", voice=sponsor.voice, input=sponsor.message_copy, response_format="mp3")`
- Storage key: `podcast/{podcast_slug}/sponsors/{sponsor_slug}.mp3`
- Idempotent — regenerating overwrites the same storage key
- Returns early if `audio_override_url` is set (no API call)

#### 5. `apps/podcast/management/commands/generate_sponsor_audio.py` — CLI command

```bash
# Generate audio for a specific sponsor
uv run python manage.py generate_sponsor_audio --sponsor-id 1

# Regenerate all active sponsors for a podcast
uv run python manage.py generate_sponsor_audio --podcast-slug yudame-research

# Regenerate all active sponsors across all podcasts
uv run python manage.py generate_sponsor_audio --all
```

Calls `generate_sponsor_audio()` service for each matching sponsor. Reports success/failure per sponsor.

#### 6. `apps/podcast/tests/test_sponsor_audio.py` — Tests

- Test `Sponsor` model creation and `effective_audio_url` property
- Test `generate_sponsor_audio()` with mocked OpenAI client
- Test that `audio_override_url` short-circuits TTS generation
- Test storage key format
- Test management command with `--sponsor-id` and `--podcast-slug` flags
- Test that inactive sponsors are skipped with `--all` flag

### What Does NOT Change

- **Episode model** — No sponsor fields. Sponsor selection is #52's responsibility.
- **Podcast workflow / tasks.py** — Sponsor audio is produced independently, not as a pipeline step.
- **PodcastConfig** — `sponsor_break` boolean stays as-is. It controls whether splice points are created in episodes; this issue controls whether sponsor audio exists to fill them.
- **episodeFocus prompt** — No changes. Sponsor instructions in the prompt are #52's domain.
- **`services/audio.py`** — Episode audio generation is untouched. Splicing is #52.

### Migration

One new migration: `apps/podcast/migrations/XXXX_add_sponsor.py` for the `Sponsor` model. Standard `makemigrations` output — no data migration needed.

**Note:** Migration will be created but not applied per project guidelines. Tom runs migrations.

## Rabbit Holes

- **Per-episode dynamic messages**: "This episode about sleep science is brought to you by..." sounds great but couples sponsor audio to episode content. If we want this later, add an optional `episode` FK and a prompt template on Sponsor — but don't build it now.
- **Multiple messages per sponsor**: Rotation between 3-4 messages per sponsor. Could be a `SponsorMessage` child model. Not needed until we have enough sponsors to warrant it.
- **Audio normalization/loudness matching**: Ensuring sponsor audio loudness matches episode audio. Important for production quality but belongs in #52's splice pipeline (it has the episode audio context). This issue just produces the raw clip.
- **Billing/scheduling**: Sponsor campaign dates, impression tracking, invoicing. Way out of scope — this is audio generation, not an ad platform.

## No-Gos

- No episode-aware sponsor messages (static per sponsor only)
- No sponsor rotation logic (first active sponsor wins; rotation is a future concern)
- No audio splicing (that's #52)
- No billing, scheduling, or campaign management
- No custom TTS models or voice cloning

## Acceptance Criteria

1. `Sponsor` model exists with name, slug, podcast FK, message_copy, voice, audio_url, audio_override_url, is_active fields
2. `effective_audio_url` property returns override when set, generated URL otherwise
3. `generate_sponsor_audio()` service calls OpenAI TTS, uploads to storage, updates `Sponsor.audio_url`
4. `generate_sponsor_audio` management command works with `--sponsor-id`, `--podcast-slug`, and `--all` flags
5. Pre-recorded override skips TTS generation (no API call)
6. Audio stored at `podcast/{podcast_slug}/sponsors/{sponsor_slug}.mp3`
7. Tests cover model, service, override behavior, and management command
8. Migration created (not applied)
9. Pre-commit hooks pass (black, ruff, flake8)
