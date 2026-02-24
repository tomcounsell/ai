---
status: Ready
type: feature
appetite: Small
owner: Tom
created: 2026-02-24
tracking: https://github.com/yudame/cuttlefish/issues/92
---

# Implement Podcast Cover Art Generation

## Problem

`generate_cover_art()` in `apps/podcast/services/publishing.py:19` raises `NotImplementedError`. During the task pipeline, `step_cover_art` catches this and creates a placeholder artifact, so episodes fall back to the podcast-level cover image. This works but means every episode shares the same artwork.

The generation logic already exists as CLI tools:
- `apps/podcast/tools/generate_cover.py` -- AI image generation via OpenRouter (Gemini)
- `apps/podcast/tools/add_logo_watermark.py` -- Yudame Research branding overlay (Pillow)
- `apps/podcast/tools/cover_art.py` -- Orchestrator that calls both in sequence

The gap is that these tools operate on filesystem paths while the service layer needs to work with database records and cloud storage.

## Appetite

**Size:** Small (half day)

This is a straightforward refactoring job. The image generation and branding logic already works. We just need to extract importable functions, wire them into the service, and upload to Supabase.

## Solution

### 1. Extract importable functions from CLI tools

**`tools/generate_cover.py`** -- Extract `generate_image()` into a function that returns image bytes instead of writing to disk:

```python
def generate_cover_image(prompt: str, api_key: str) -> bytes:
    """Generate cover art via OpenRouter. Returns PNG bytes."""
```

The existing `generate_image()` already does all the API work. The refactor is: instead of writing to `output_path`, return the decoded base64 bytes directly.

Also extract `generate_prompt_from_report()` -- this already takes text and returns a prompt string. It just needs to be importable (no changes needed, it already is).

**`tools/add_logo_watermark.py`** -- Extract `add_branding()` into a function that accepts and returns image bytes:

```python
def apply_branding(image_bytes: bytes, series_text: str | None = None) -> bytes:
    """Apply Yudame Research branding overlay. Returns branded PNG bytes."""
```

The existing `add_branding()` works on file paths. The new function wraps it using `io.BytesIO` for in-memory image processing. The `episode_text` parameter is dropped -- only the "Yudame Research" brand name and optional series name are shown on the overlay.

### 2. Install fonts via Render build script

Playfair Display fonts are required for the branding overlay. Add font installation to the Render build process so they're available in production. The build script should download the fonts to a project-local directory and the `add_logo_watermark.py` font path list should include that location.

### 3. Implement `generate_cover_art()` service

Replace the `NotImplementedError` stub in `apps/podcast/services/publishing.py` with:

1. Load Episode for metadata (title, podcast series)
2. Load report_text for AI prompt generation
3. Call `generate_cover_image()` with auto-generated prompt from report
4. Call `apply_branding()` with series name from `Episode.podcast.title`
5. Upload to Supabase via `store_file()`
6. Save URL to `Episode.cover_image_url`
7. Create `cover-art` artifact with generation metadata

The privacy-aware storage logic follows the same pattern as `audio.py:122`:
```python
storage_key = f"podcast/{episode.podcast.slug}/{episode.slug}/cover.png"
is_private = episode.podcast.uses_private_bucket
cover_url = store_file(storage_key, image_bytes, "image/png", public=not is_private)
```

### 3. Graceful degradation

Follow the same pattern as Perplexity/Together research steps: if `OPENROUTER_API_KEY` is missing, log a warning, create a placeholder artifact, and return without failing the pipeline.

### 4. Update task pipeline

Remove the `except NotImplementedError` block from `step_cover_art` in `tasks.py:569` since the stub is gone. Keep the general `except Exception` handler.

## Files to modify

| File | Change | Type |
|------|--------|------|
| `apps/podcast/tools/generate_cover.py` | Add `generate_cover_image()` returning bytes | modify |
| `apps/podcast/tools/add_logo_watermark.py` | Add `apply_branding()` accepting/returning bytes, drop `episode_text` | modify |
| `apps/podcast/services/publishing.py` | Replace stub with real implementation | modify |
| `apps/podcast/tasks.py` | Remove `NotImplementedError` catch from `step_cover_art` | modify |
| Build script (e.g. `render-build.sh` or `render.yaml`) | Add Playfair Display font installation step | modify |

## Files NOT to modify

- `tools/cover_art.py` -- CLI orchestrator, stays as-is for manual use
- Episode model -- `cover_image_url` field already exists
- Workflow/signals -- fan-in logic already handles this step

## Rabbit Holes

- **Prompt engineering** -- Don't spend time optimizing the AI prompt. The existing `generate_prompt_from_report()` is good enough. Improvements can come later.
- **Image quality validation** -- Don't add image quality checks or retry logic. If generation fails, the pipeline falls back to podcast-level cover. That's fine.
- **Logo file path** -- The branding tool expects `yudame-logo.png` relative to the tools directory. For the in-memory function, bundle the logo path lookup internally rather than requiring callers to locate it.

## No-gos

- No new models or migrations
- No changes to the Episode admin
- No changes to the RSS feed template (it already uses `effective_cover_image_url`)
- No retry/queue logic for failed generations
- No backfill of existing episodes -- published episodes already have covers, and podcast-level covers are custom

## Success Criteria

- [ ] `generate_cover_art(episode_id)` generates an image, brands it, uploads to Supabase, and saves the URL
- [ ] Pipeline runs end-to-end without `NotImplementedError`
- [ ] Missing `OPENROUTER_API_KEY` logs warning and creates placeholder (no crash)
- [ ] Episode detail page shows generated cover art
- [ ] Tests cover: successful generation, missing API key, missing report_text

## Decisions

1. **Fonts on Render** -- Install Playfair Display fonts as part of the build process. Nothing extra is pre-installed on the server.
2. **Branding overlay text** -- Show "Yudame Research" brand + optional series name from Episode model. No episode number or episode-specific text on the image.
3. **No backfill** -- Published episodes already have cover images. Podcast-level covers are custom. Only new episodes going forward get auto-generated covers.
