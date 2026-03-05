---
name: notebooklm-enterprise-api
description: "[DEPRECATED - NOT IN USE] Generate podcast audio using NotebookLM Enterprise API via Google Cloud. The team decided against using this approach. Use notebooklm-audio skill (manual workflow) or local_audio_worker instead."
user-invocable: false
---

# NotebookLM Enterprise API Audio Generation

**STATUS: DEPRECATED - NOT CURRENTLY IN USE**

**Date Deprecated:** 2026-02-19

**Reason:** The team decided against using the NotebookLM Enterprise API for podcast audio generation.

**Current Approach:** The podcast production pipeline uses local audio generation via `notebooklm-py` and the `local_audio_worker` management command. See `apps/podcast/tasks.py::step_audio_generation` for implementation details.

**Fallback Option:** For manual workflow, use the `notebooklm-audio` skill (web interface approach).

---

## Original Documentation (Archived)

Generate podcast audio using the NotebookLM Enterprise API. This automates the manual NotebookLM web workflow:

1. Create notebook via API
2. Upload 5 source files (p1-brief.md, report.md, p3-briefing.md, sources.md, content_plan.md)
3. Generate audio overview with the standard Yudame Research prompt
4. Download the resulting MP3

## Prerequisites

### Google Cloud Setup

1. **Enable APIs:**
   ```bash
   gcloud services enable discoveryengine.googleapis.com
   gcloud services enable aiplatform.googleapis.com
   ```

2. **Required IAM Roles:**
   - `roles/discoveryengine.admin` (for notebook creation)
   - Or appropriate NotebookLM Enterprise permissions

3. **Authentication:**
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

4. **NotebookLM Enterprise License:**
   - Must have Gemini Enterprise subscription with NotebookLM Enterprise enabled
   - Access may be restricted to select customers

### Environment Variables

```bash
# Optional - defaults to gcloud config
export GOOGLE_CLOUD_PROJECT=your-project-id
```

## Usage

### Command Line

```bash
cd apps/podcast/tools

# Basic usage
python notebooklm_api.py ../pending-episodes/YYYY-MM-DD-slug/

# With series name
python notebooklm_api.py ../pending-episodes/cardiovascular-health/ep5-diet/ \
    --series "Cardiovascular Health"

# With custom title and cleanup
python notebooklm_api.py ../pending-episodes/YYYY-MM-DD-slug/ \
    --title "Diet and Heart Health" \
    --series "Cardiovascular Health" \
    --cleanup
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `episode_dir` | Yes | Path to episode directory |
| `--series` | No | Series name (included in audio intro) |
| `--title` | No | Episode title (defaults to directory name) |
| `--cleanup` | No | Delete notebook after generation |
| `--timeout` | No | Timeout in minutes (default: 30) |

## Required Source Files

The episode directory must contain:

```
episode-directory/
├── research/
│   ├── p1-brief.md      # Phase 1 research brief
│   └── p3-briefing.md   # Master briefing (cross-validated)
├── report.md            # Narrative synthesis
├── sources.md           # Validated source links
└── content_plan.md      # Episode structure and NotebookLM guidance
```

## What It Does

1. **Creates Notebook:** API call to create a new notebook
2. **Uploads Sources:** Uploads all 5 source files as text content
3. **Generates Audio:** Calls `audioOverviews.create` with the standard Yudame Research prompt
4. **Waits for Completion:** Polls status every 30 seconds (up to timeout)
5. **Downloads MP3:** Saves to `episode-directory/SLUG.mp3`
6. **Cleanup:** Optionally deletes the notebook

## The Episode Focus Prompt

Uses the same prompt as the manual NotebookLM workflow:

- Opening: "Yudame Research" + series name
- Core principles: Spell out acronyms, define terms, cite studies
- Tone: Intellectually rigorous but accessible
- Closing: Summary + website URL

See `generate_episode_focus()` in the script for the full prompt.

## Output

```
episode-directory/
└── YYYY-MM-DD-slug.mp3   # Generated audio (typically 20-40 min)
```

The audio is in the standard NotebookLM format (two hosts, Deep Dive style).

## Troubleshooting

### "Permission Denied"
- Check gcloud authentication: `gcloud auth print-access-token`
- Verify Discovery Engine API is enabled
- Confirm NotebookLM Enterprise license is active

### "Notebook Not Found"
- The API uses project number, not project ID
- Check `gcloud projects describe PROJECT_ID` for the number

### "Audio Generation Failed"
- Check source file sizes (must be under 100,000 tokens combined)
- Verify source files are valid text/markdown
- Check Google Cloud quotas

### Timeout
- Increase with `--timeout 60` for larger source files
- Audio generation typically takes 5-15 minutes

## Integration with Workflow

This is the **primary audio generation method** for Phase 10.

**Fallback:** Manual NotebookLM web interface (`.claude/skills/notebooklm-audio/`) when API is unavailable.

## API Reference

### Endpoints Used

| Operation | Endpoint |
|-----------|----------|
| Create Notebook | `POST /notebooks` |
| Upload Source | `POST /notebooks/{id}/sources:batchCreate` |
| Generate Audio | `POST /notebooks/{id}/audioOverviews` |
| Check Status | `GET /notebooks/{id}/audioOverviews/default` |
| Download Audio | `GET /notebooks/{id}/audioOverviews/default:download` |
| Delete Notebook | `DELETE /notebooks/{id}` |

### Documentation

- [Audio Overview API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-audio-overview)
- [Notebook API](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks)
- [Source Management](https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks-sources)
