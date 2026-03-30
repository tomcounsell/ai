---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-02-18
tracking: https://github.com/yudame/cuttlefish/issues/75
---

# MVP: End-to-End Podcast Production

## Problem

The podcast production pipeline has been built piece by piece (#60, #62, #63, #70, #71, #73, #86) but has never run end-to-end. No episode has been produced through the full automated flow. Several operational and code gaps remain that prevent a complete run from episode creation through to a published RSS feed entry.

**Current behavior:**
All 12 phases are coded and the task pipeline exists, but no episode has been pushed through the full flow. The background worker isn't deployed, production API keys aren't configured, and `audio_duration_seconds` is never populated — causing the RSS feed to show `00:00:00` for every episode.

**Desired outcome:**
One real episode successfully published through the full pipeline, appearing in a valid RSS feed at `/podcast/<slug>/feed.xml` with correct duration, audio URL, description, and show notes.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. One check-in for API key provisioning (needs Tom's credentials), one review when first episode publishes.

**Interactions:**
- PM check-ins: 1-2 (API key provisioning, NotebookLM access verification)
- Review rounds: 1 (validate feed output after first successful run)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` in `.env.local` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('ANTHROPIC_API_KEY')"` | Claude research + AI tools |
| `OPENAI_API_KEY` in `.env.local` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('OPENAI_API_KEY')"` | Whisper transcription |
| `PERPLEXITY_API_KEY` in `.env.local` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('PERPLEXITY_API_KEY')"` | Perplexity research |
| `GEMINI_API_KEY` in `.env.local` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('GEMINI_API_KEY')"` | Gemini research |
| `notebooklm-py` installed locally | `python -c "import notebooklm" 2>/dev/null && echo OK || pip show notebooklm-py` | Local audio worker |
| Supabase credentials | `python -c "from dotenv import dotenv_values; e=dotenv_values('.env.local'); assert e.get('SUPABASE_PROJECT_URL') and e.get('SUPABASE_SERVICE_ROLE_KEY')"` | File storage |

## Solution

Five blockers, shipped in dependency order:

### 1. Fix `audio_duration_seconds` (code fix)

`transcribe_audio()` in `apps/podcast/services/audio.py` downloads audio bytes but never calculates duration. Add MP3 duration calculation using `mutagen` (already handles MP3 headers reliably) and save to `Episode.audio_duration_seconds` alongside the transcript.

**Files:** `apps/podcast/services/audio.py` (modify), `pyproject.toml` (add `mutagen`)

### 2. Create background worker on Render (infra)

The `cuttlefish-worker` service is defined in `render.yaml` but doesn't exist on Render — IaC only applies at initial creation. Create the worker service via Render MCP or dashboard:
- Type: Background Worker
- Start command: `python manage.py db_worker`
- Same build, repo, branch, region, and env group as the web service
- Plan: Starter

**Note:** The worker shares the `cuttlefish` env group, so all env vars set for the web service are inherited.

### 3. Configure production API keys (infra)

Add to the `cuttlefish` environment group on Render:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `PERPLEXITY_API_KEY`
- `GEMINI_API_KEY`
- `TOGETHER_API_KEY` (optional — Together research)

These are needed by both the web service and the worker. Using the env group means setting them once.

### 4. Configure Supabase storage (infra, ties to #85)

Production already sets `STORAGE_BACKEND = "supabase"` in `settings/production.py`. Two buckets exist:
- `cuttlefish-public` — public podcast audio, cover art (permanent URLs)
- `cuttlefish-private` — private podcast audio (signed URLs)

Ensure the `cuttlefish` env group has:
- `SUPABASE_PROJECT_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_PUBLIC_BUCKET_NAME` = `cuttlefish-public`
- `SUPABASE_PRIVATE_BUCKET_NAME` = `cuttlefish-private` (optional, for private podcasts)
- `SUPABASE_USER_ACCESS_TOKEN` = secret token (for private feed auth)

**Note:** Old `SUPABASE_BUCKET_NAME` setting still works as a fallback for `SUPABASE_PUBLIC_BUCKET_NAME`.

Verify by calling `check_storage_config()` from a production shell.

### 5. Audio generation via local worker (operational)

The task pipeline pauses at step 9 (`pause_for_human("audio_generation")`). A local machine runs `manage.py local_audio_worker`, which polls the API, generates audio via `notebooklm-py`, uploads it, and resumes the workflow.

### Flow

**Admin** → Create draft Episode → **CLI** → `start_episode` → **Task pipeline** → 12 phases auto-chain → **Feed** → episode at `/<slug>/feed.xml`

## Rabbit Holes

- **Cover art generation** — The stub in `generate_cover_art()` is handled gracefully (placeholder artifact, podcast-level fallback). Don't try to wire up the CLI tool now.
- **Public episode creation UI** — Admin is sufficient for MVP. A web form is a separate feature.
- **Whisper alternatives** — Don't evaluate other transcription services. Whisper-1 works.
- **Multi-podcast support** — Verify with one podcast first. Don't test edge cases with multiple feeds.

## Risks

### Risk 1: Local audio worker availability
**Impact:** No one running `local_audio_worker` means pipeline stalls at phase 9 indefinitely.
**Mitigation:** The workflow UI shows `paused_for_human` status clearly. Worker can be started anytime — no data loss from the pause.

### Risk 2: Supabase storage misconfiguration
**Impact:** Audio upload fails after successful generation — wasted API credits.
**Mitigation:** Test storage independently first (`store_file` with a small test file). The backend falls back to local storage if config is missing — we'll see it in logs.

### Risk 3: Task pipeline failures in production
**Impact:** Any phase can fail, leaving the workflow in `failed` state.
**Mitigation:** The workflow tracks `current_step` and `history`. Failed steps can be retried via the workflow UI buttons. Run locally first (ImmediateBackend) to catch bugs before production.

## No-Gos (Out of Scope)

- Persona story framing (#51)
- Sponsor splice points (#52)
- Sponsor message audio (#53)
- Per-episode cover art generation
- Public-facing episode creation form
- Automated feed submission to Apple/Spotify

## Update System

No update system changes required — this is a production deployment + one code fix.

## Agent Integration

No agent integration required — this work is infrastructure configuration plus one small code change.

## Documentation

### Inline Documentation
- [ ] Update docstring on `transcribe_audio()` to mention duration calculation

No other documentation changes needed until the MVP is validated.

## Success Criteria

- [ ] `audio_duration_seconds` populated during transcription (code fix merged)
- [ ] `cuttlefish-worker` running on Render and processing tasks
- [ ] All required API keys set in Render env group
- [ ] Supabase storage configured and verified
- [ ] One episode successfully produced through the full pipeline
- [ ] Episode appears in RSS feed with correct duration, audio URL, and metadata
- [ ] Feed validates (no critical RSS spec violations)
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (audio-duration)**
  - Name: audio-fix-builder
  - Role: Add MP3 duration calculation to `transcribe_audio()`
  - Agent Type: builder
  - Resume: true

- **Validator (audio-duration)**
  - Name: audio-fix-validator
  - Role: Verify duration fix with unit test
  - Agent Type: validator
  - Resume: true

- **Infra (render-worker)**
  - Name: render-deployer
  - Role: Create worker service, configure env vars, verify deployment
  - Agent Type: integration-specialist
  - Resume: true

- **Validator (e2e)**
  - Name: e2e-validator
  - Role: Run episode through pipeline, validate feed output
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `mutagen` dependency and fix `audio_duration_seconds`
- **Task ID**: build-audio-duration
- **Depends On**: none
- **Assigned To**: audio-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `mutagen` to `pyproject.toml` via `uv add mutagen`
- In `transcribe_audio()` (`apps/podcast/services/audio.py`), after downloading `audio_bytes`, parse MP3 duration using `mutagen.mp3.MP3` from a `BytesIO` wrapper
- Save `episode.audio_duration_seconds = int(audio.info.length)` alongside `episode.transcript`
- Update `save(update_fields=...)` to include `audio_duration_seconds`
- Write a test in `apps/podcast/tests/` that verifies duration is populated

### 2. Validate audio duration fix
- **Task ID**: validate-audio-duration
- **Depends On**: build-audio-duration
- **Assigned To**: audio-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v -k duration`
- Verify the feed template renders non-zero `<itunes:duration>` when `audio_duration_seconds` is set

### 3. Create background worker on Render
- **Task ID**: create-render-worker
- **Depends On**: none
- **Assigned To**: render-deployer
- **Agent Type**: integration-specialist
- **Parallel**: true
- Create `cuttlefish-worker` background worker via Render MCP `create_web_service` or dashboard
- Runtime: python, Build: `./build.sh`, Start: `python manage.py db_worker`
- Region: oregon, Plan: starter, Branch: main
- Env vars: `DEPLOYMENT_TYPE=PRODUCTION` + `fromGroup: cuttlefish`
- Verify the worker deploys and starts successfully via `list_deploys` + `list_logs`

### 4. Configure production API keys
- **Task ID**: configure-api-keys
- **Depends On**: create-render-worker (so worker picks them up)
- **Assigned To**: render-deployer
- **Agent Type**: integration-specialist
- **Parallel**: false
- Add keys to `cuttlefish` env group via `update_environment_variables`: ANTHROPIC_API_KEY, OPENAI_API_KEY, PERPLEXITY_API_KEY, GEMINI_API_KEY
- Verify both web service and worker have the keys via logs

### 5. Configure Supabase storage
- **Task ID**: configure-supabase
- **Depends On**: none
- **Assigned To**: render-deployer
- **Agent Type**: integration-specialist
- **Parallel**: true
- Add SUPABASE_PROJECT_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_PUBLIC_BUCKET_NAME, SUPABASE_PRIVATE_BUCKET_NAME (optional), SUPABASE_USER_ACCESS_TOKEN (optional) to env group
- Verify via production health check or shell: `check_storage_config()` returns `{"ok": true}`
- Note: Old SUPABASE_BUCKET_NAME setting is backwards compatible with SUPABASE_PUBLIC_BUCKET_NAME

### 6. End-to-end validation
- **Task ID**: validate-e2e
- **Depends On**: validate-audio-duration, configure-api-keys, configure-supabase
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Create a draft Episode in admin
- Run `start_episode` to kick off pipeline
- Monitor workflow progress through all 12 phases
- Verify episode publishes and appears in feed.xml
- Validate RSS feed structure

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-e2e
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Confirm all success criteria met
- Close issue #75

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` — podcast tests pass
- `DJANGO_SETTINGS_MODULE=settings pytest -v` — full suite passes
- `curl -s https://app.bwforce.ai/podcast/<slug>/feed.xml | xmllint --noout -` — feed is valid XML
- `curl -s https://app.bwforce.ai/health/deep/` — production health check passes

---

## Open Questions

1. **API key provisioning** — Do all required keys (ANTHROPIC, OPENAI, PERPLEXITY, GEMINI) already exist in the Render env group, or do they need to be added? The Render MCP doesn't expose env var listing, so this needs manual verification via the dashboard.
