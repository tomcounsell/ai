# do-presentation context — this repo (ai)

The `/do-presentation` body is already generic (Marp, `npx`, Simple Icons, Google Favicons —
all portable). The one repo-specific capability is the **narrated `--video` mode**, which this
repo powers with `valor-deck-video` (and, beneath it, `valor-tts`). This file declares that
pipeline.

## Narrated deck video (`--video` mode)

`/do-presentation <topic> --video` produces a **narrated MP4** of the deck: each slide held on
screen for the length of its spoken narration, voiceover muxed in, exported as a single
`deck.mp4` next to the deck.

**Pipeline:**

1. **Author the deck with per-slide narration blocks** (schema below) — the Marp markdown is written exactly as in the static flow, plus one narration comment per slide carrying the speaker text.
2. **Marp exports one PNG per slide**: `npx --yes @marp-team/marp-cli "<source>.md" --images png --allow-local-files` (the same Marp invocation as static export, with `--images png`). Filenames are zero-padded sequence suffixes (`deck.001.png`, ...) so document order is preserved.
3. **`valor-tts` synthesizes one clip per narrated slide**: each slide's narration text becomes one OGG/Opus clip. Each clip's measured `duration` is that slide's on-screen hold time.
4. **ffmpeg muxes** the PNGs and audio clips into `deck.mp4` (concat demuxer, per-image duration list, `-c:v libx264 -pix_fmt yuv420p`, audio re-encoded to AAC).

**Implementation surface:** the `valor-deck-video` CLI owns the full pipeline (Marp PNG export →
per-slide synthesis → ffmpeg mux). The skill orchestrates by invoking it:

```bash
valor-deck-video "<source>.md"
```

The skill does not re-implement the compositing; it authors the deck (with narration blocks) and
shells out to `valor-deck-video`.

### Narration schema

Each slide carries its narration in a per-slide HTML comment block in the Marp markdown:

```markdown
<!-- narration: Revenue grew 25% this quarter, driven by the new onboarding flow. -->
```

- **One `<!-- narration: ... -->` block per slide.** Place it anywhere within that slide's content (between the `---` slide separators).
- **Marp ignores HTML comments in rendered output**, so adding narration blocks leaves static PDF/HTML/PPTX export completely unaffected — the same source produces both the static deck and the video.
- **Empty or missing narration** → the slide holds for a configurable default duration (`DECK_VIDEO_DEFAULT_HOLD_SECS`, provisional `4.0s`, env-overridable) with silence, rather than being dropped. A slide is never skipped just because it has no narration.

### Manual vs automated narration paths

There are two narration paths, and they use different surfaces:

- **Manual narration (standalone voiceover track):** if the user wants a spoken voiceover or narration track as its own audio file, **defer to `/do-voice-recording`** — it is the canonical text-to-speech step. Feed it the per-slide speaker notes. Do not hand-roll synthesis for this path.
- **Automated `--video` pipeline (approved exception):** the `valor-deck-video` CLI is an **approved direct consumer of `valor-tts`**. It calls `valor-tts` per slide because it needs the structured per-clip `duration` that `valor-tts` returns and the conversational `/do-voice-recording` skill does not. This is a deliberate carve-out from the "defer to `/do-voice-recording`" rule, which continues to govern the manual narration path above.

Both paths **reuse the existing `valor-tts` surface** — there is no second TTS tool.
