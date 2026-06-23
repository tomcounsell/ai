# Narrated Deck Video

Tracking issue: [#1726](https://github.com/tomcounsell/ai/issues/1726)

## What it is

A compositing pipeline that renders a narrated MP4 from a Marp deck: each slide is held on screen for the duration of its spoken narration, the voiceover is muxed in, and the result is exported as a single `deck.mp4` next to the source deck.

The pipeline reuses existing infrastructure — Marp for PNG export, `valor-tts` (Kokoro local primary, OpenAI tts-1 cloud fallback) for synthesis, and ffmpeg for compositing — with no new cloud speech provider, no animation engine, and no extra Node/Playwright dependencies.

## Invocation surfaces

### `/do-presentation --video`

The user-facing surface. Invoke it the same way as a regular presentation, adding `--video`:

```
/do-presentation <topic> --video
```

The skill authors the deck (Marp markdown with per-slide narration blocks) and shells out to `valor-deck-video` to run the full compositing pipeline. The skill does not re-implement compositing.

### `valor-deck-video` CLI

The implementation surface. Takes a Marp markdown file and produces `deck.mp4` next to it:

```bash
valor-deck-video deck.md              # output: deck.mp4
valor-deck-video deck.md -o out.mp4   # explicit output path
```

Registered as `valor-deck-video = "tools.deck_video.cli:main"` in `pyproject.toml [project.scripts]`.

The CLI guards prerequisites before doing any work: if `ffmpeg`, `ffprobe`, the Marp CLI, or `valor-tts` is missing it emits an actionable message naming the missing tool and exits non-zero.

## Per-slide narration schema

Each slide carries its narration in a per-slide HTML comment block placed anywhere within that slide's content (between the `---` slide separators):

```markdown
---
marp: true
---

# Slide One

Revenue grew 25% this quarter.

<!-- narration: Revenue grew 25% this quarter, driven by the new onboarding flow. -->

---

# Slide Two

Key drivers...

<!-- narration: The three key drivers were faster checkout, better mobile, and the referral campaign. -->
```

### Why HTML comments

Marp ignores HTML comments in rendered output. Adding `<!-- narration: ... -->` blocks leaves static PDF, HTML, and PPTX export completely unaffected — the same source file produces both the static deck and the narrated video.

### Empty or missing narration

A slide with an empty or missing narration block is not dropped. It holds on screen for the configurable default duration (`DECK_VIDEO_DEFAULT_HOLD_SECS`, provisional `4.0s`, env-overridable via the environment variable of the same name) with silence. The CLI warns to stderr if no narration blocks are found in the entire deck (a likely authoring oversight) but still produces the video.

## Compositing pipeline

```
deck.md
  |
  |-- parse_narration_blocks()
  |     Extract one narration string per slide (document order).
  |     Empty string for un-narrated slides.
  |
  |-- Marp PNG export (npx @marp-team/marp-cli --images png)
  |     One PNG per slide; zero-padded sequence filenames (deck.001.png ...).
  |     Sorted numerically by parsed sequence number (not lexicographically).
  |     Parity assertion: len(pngs) == total_slide_count == len(narration_blocks).
  |
  |-- Per-slide synthesis (valor-tts, narrated slides only)
  |     synthesize() checked for error FIRST (returns {"error": ...} with no
  |     path/duration keys on failure). Clean DeckVideoError on any error.
  |     After confirming no error: read duration; floor against zero-length guard
  |     (re-probe via ffprobe; raise if still <= 0.0 after re-probe).
  |
  |-- Per-slide holds computed
  |     Narrated slide:  hold = floored clip duration (> 0.0)
  |     Silent slide:    hold = DECK_VIDEO_DEFAULT_HOLD_SECS
  |
  |-- Total runtime
  |     sum(floored narrated durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)
  |
  |-- ffmpeg compositing (two-pass)
  |     Pass 1: concat PNG images via the concat demuxer with per-image duration
  |             directives and a trailing final-frame repeat -> intermediate MP4.
  |             No framerate flag (correct PTS, no trailing-frame over-count).
  |     Pass 2: normalize to 30fps CFR (fps filter), cap at total_runtime (-t),
  |             plus audio mux (if any narrated slides exist).
  |
  |-- deck.mp4
```

All intermediate artifacts (PNGs, per-slide audio clips, concat lists, audio segments) are written under one temp directory that is deleted in a `try/finally` block covering both success and failure paths. Only `deck.mp4` survives.

## All-silent deck behavior

If every slide has empty narration (zero audio clips), the compositor takes the video-only branch: it produces a playable MP4 with no audio stream (`-c:v libx264 -pix_fmt yuv420p`, no `-c:a`, no `-shortest`). Constructing an ffmpeg mux command that references a non-existent audio input would produce a broken stream or non-zero exit.

The CLI warns to stderr in this case:

```
Warning: No narration blocks found; producing a video-only slideshow
-- add <!-- narration: ... --> comments to narrate.
```

## Total runtime formula

For a deck that mixes narrated and silent slides:

```
total_runtime = sum(floored narrated durations) + (count_silent * DECK_VIDEO_DEFAULT_HOLD_SECS)
```

This definition is unambiguous regardless of how many silent slides exist. The end-to-end test asserts the MP4's measured duration matches this formula within tolerance using a fixture deck that contains both narrated and silent slides.

## Narration and TTS path

The `valor-deck-video` CLI is an approved direct consumer of `valor-tts`. It calls `valor-tts` per slide because it needs the structured `{path, duration}` return value that the conversational `/do-voice-recording` skill does not return. This is a deliberate carve-out from the "defer to `/do-voice-recording`" rule, which continues to govern the manual narration path (a standalone voiceover track not tied to a video).

`valor-tts` itself has a Kokoro-local primary with an OpenAI tts-1 cloud fallback. "Single TTS surface" means one tool (`valor-tts`), not a single backend.

## Prerequisites

| Tool | Check | Purpose |
|------|-------|---------|
| `ffmpeg` | `command -v ffmpeg` | Video encode and audio mux |
| `ffprobe` | `command -v ffprobe` | Per-clip duration measurement (re-probe guard) |
| Marp CLI | `npx --yes @marp-team/marp-cli --version` | PNG-per-slide export |
| `valor-tts` | Python import (`tools.tts.synthesize`) | Per-slide narration synthesis |

`ffmpeg` and `ffprobe` are already required by `valor-tts` (ffprobe) and present on dev machines. Marp is invoked via `npx --yes @marp-team/marp-cli` (no global binary required). `valor-tts` is available after `pip install -e .`.

## Usage

```bash
# Via the skill (primary path)
/do-presentation "Q4 Revenue" --video

# Via the CLI directly
valor-deck-video my-deck.md
valor-deck-video my-deck.md --output /tmp/presentation.mp4

# Override the default silent-slide hold (seconds)
DECK_VIDEO_DEFAULT_HOLD_SECS=6.0 valor-deck-video my-deck.md
```

On success the CLI prints:

```
OK -> /path/to/deck.mp4
```

## Error handling

| Scenario | Behavior |
|----------|----------|
| Missing prerequisite (ffmpeg, ffprobe, Marp, valor-tts) | CLI emits actionable message naming the tool, exits 1 |
| `synthesize()` returns `{"error": ...}` (text too long, both backends fail, missing API key) | `DeckVideoError` raised with slide index and error string — never a `TypeError` or `KeyError` |
| Narrated clip duration `<= 0.0` after synthesis | Re-probe via ffprobe; raise `DeckVideoError` if still `<= 0.0` (slide index + clip path surfaced) |
| All slides silent (zero audio clips) | Video-only MP4 produced; warning to stderr |
| PNG count != slide count | `DeckVideoError` raised immediately before compositing |
| Partial pipeline failure | Temp directory cleaned up; no orphaned scratch files |

## Related files

- `tools/deck_video/__init__.py` — compositor: narration parser, Marp export, synthesis loop, ffmpeg compositing, `build_deck_video()` entrypoint
- `tools/deck_video/cli.py` — `valor-deck-video` entry point
- `.claude/skills-global/do-presentation/SKILL.md` — skill definition, `--video` mode, narration schema, version history
- `tests/unit/test_deck_video.py` — unit tests (narration parser, per-slide synthesis, error handling)
- `tests/integration/test_deck_video_e2e.py` — E2E test (2-slide fixture deck, real ffmpeg + valor-tts, playable MP4 assertion)
- `docs/features/tts.md` — `valor-tts` reference (dual backend, voice catalog, duration field)
