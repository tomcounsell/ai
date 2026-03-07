# Audio Upload UI Fallback

**Issue**: #134
**Status**: Planning
**Created**: 2026-03-07

## Problem

Audio generation (phase 9) relies on `local_audio_worker` running locally. If the worker isn't running, workflow gets stuck at `paused_for_human` with no manual upload option. Users cannot progress the pipeline.

## Solution

Add manual audio file upload UI to workflow step 9 when status is `paused_for_human`.

### Changes Required

1. **Template (`_workflow_step_content.html`)**:
   - Add file upload form when step=9 AND status=`paused_for_human` AND no `audio_url`
   - Show HTML5 audio preview when `audio_url` exists
   - Use Django form POST (not HTMX for file upload)

2. **View (`EpisodeWorkflowView`)**:
   - Handle POST with uploaded file at step 9
   - Save file using `apps.common.services.storage.store_file()`
   - Set `Episode.audio_url` to returned URL
   - Keep existing "Resume Pipeline" button behavior

3. **Storage**:
   - Use existing Supabase storage via `store_file()` API
   - Store in public bucket: `podcast/{podcast.slug}/{episode.slug}/audio.mp3`
   - Content type: `audio/mpeg` or detect from upload

### File Structure

```
Workflow Step 9 UI:
├── Phase header (existing)
├── Pipeline button (existing "Resume Pipeline")
├── [NEW] Audio upload form (if paused + no audio)
│   ├── File input (accept=".mp3,.wav")
│   └── Upload button
├── [NEW] Audio player (if audio_url exists)
└── Sub-steps checklist (existing)
```

### No-Gos

- No new migrations
- No changes to workflow state machine
- No changes to local_audio_worker logic
- No file size validation (Supabase handles this)

## Testing

Manual testing only (per project rules):
1. Create episode, run pipeline to step 9
2. Ensure `local_audio_worker` is NOT running
3. Verify upload form appears
4. Upload .mp3 file
5. Verify `audio_url` is set
6. Verify audio player appears
7. Click "Resume Pipeline" — should proceed to step 10
