# Cover Art Preview and Selection in Workflow

**Issue**: #135
**Branch**: `session/cover-art-preview`

## Problem

Cover art is generated in phase 11 but invisible until episode is published. No regeneration or custom upload option.

## Solution

Add cover art preview, regenerate, and upload controls to workflow step 11.

## Implementation

### 1. Add Preview Display
- **File**: `apps/podcast/templates/podcast/_workflow_step_content.html`
- Show `episode.cover_image_url` as `<img>` in step 11 when exists
- Use same card layout pattern as other workflow steps

### 2. Add Regenerate Button
- **File**: `apps/podcast/workflow.py`
- Add POST handler for regenerate action
- Call existing cover art service (identify in `apps/podcast/services/`)
- Update `episode.cover_image_url` and return success

### 3. Add Custom Upload
- **File**: `apps/podcast/workflow.py`, template
- Add file upload field similar to audio upload in step 9
- Accept image file, validate, store, update `episode.cover_image_url`
- Use Django's file handling pattern from audio upload

### 4. Template Updates
- **File**: `apps/podcast/templates/podcast/_workflow_step_content.html`
- Step 11 section: preview image, two buttons (regenerate, upload)
- Use Alpine.js for file upload preview if needed

## Files to Read
- `apps/podcast/workflow.py` - workflow view handlers
- `apps/podcast/templates/podcast/_workflow_step_content.html` - step 11 UI
- `apps/podcast/models/episode.py` - Episode model fields
- `apps/podcast/services/` - cover art generation service

## No-Gos
- No changes to cover art generation algorithm
- No migrations (fields already exist)
- No changes to publishing flow

## Documentation
- No new documentation required - UI feature is self-explanatory

## Update System
- No update system changes required - pure UI enhancement

## Agent Integration
- No agent integration required - Django web UI only
