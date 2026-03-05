---
status: In Review
type: chore
appetite: Medium
owner: Valor
created: 2026-03-05
tracking: https://github.com/yudame/cuttlefish/issues/118
---

# Replace NotebookLM Enterprise API with notebooklm-py

## Problem

The podcast audio generation pipeline currently depends on `notebooklm-mcp-cli`, a library used in the `local_audio_worker` management command. Additionally, a large block of archived/unused Enterprise API code (`notebooklm_api.py`, `audio.py::generate_audio`) remains in the codebase, creating confusion about which code path is active.

**Current behavior:**
- `local_audio_worker` imports `notebooklm_mcp_cli.core.NotebookLMClient` for audio generation
- `apps/podcast/services/audio.py::generate_audio()` imports from `apps/podcast/tools/notebooklm_api.py` (archived Enterprise API) but is not called in production
- The archived code (~530 lines across two files) adds maintenance burden and confusion
- `notebooklm-mcp-cli` is not listed in `pyproject.toml` (installed manually)

**Desired outcome:**
- Single, clean audio generation path using `notebooklm-py` (community SDK with broader feature set)
- Archived Enterprise API code removed
- All NotebookLM interactions go through the `notebooklm-py` client
- Dependency properly declared in `pyproject.toml`

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (scope alignment on what to keep vs delete)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `notebooklm-py` available on PyPI | `pip index versions notebooklm-py` | Library must be installable |
| NotebookLM auth configured | `test -f ~/.notebooklm/storage_state.json` | Browser auth state for notebooklm-py (run `notebooklm login`) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/notebooklm_py_migration.md`

## Solution

### Key Elements

- **Dependency swap**: Replace `notebooklm-mcp-cli` with `notebooklm-py` in `pyproject.toml`
- **Audio worker update**: Rewrite `_generate_audio_nlm()` to use `notebooklm-py` async API
- **Dead code removal**: Delete archived Enterprise API code and its service layer wrapper
- **Reference cleanup**: Update docstrings and comments that mention old libraries

### Flow

**Episode queued** -> local_audio_worker polls -> `_generate_audio_nlm()` called -> notebooklm-py creates notebook, uploads sources, generates audio, downloads MP3 -> upload to storage -> callback to production

(Same flow as today, only the internal library call changes.)

### Technical Approach

#### 1. Replace `_generate_audio_nlm()` in `local_audio_worker.py`

The current method uses `notebooklm_mcp_cli.core.NotebookLMClient` (synchronous). The `notebooklm-py` library is async-only, so the method needs an async wrapper.

**Current API (notebooklm-mcp-cli):**
```python
from notebooklm_mcp_cli.core import NotebookLMClient
client = NotebookLMClient()
notebook_id = client.create_notebook(title)
client.upload_source(notebook_id, name, content)
client.generate_audio(notebook_id)
client.wait_for_audio(notebook_id, timeout_minutes=30)
client.download_audio(notebook_id, output_path)
client.delete_notebook(notebook_id)
```

**New API (notebooklm-py):**
```python
from notebooklm import NotebookLMClient
import asyncio

async def _generate_audio_async(source_dir, title, output_path):
    async with await NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(title)
        try:
            for source_file in source_dir.iterdir():
                if source_file.is_file() and source_file.suffix == ".md":
                    await client.sources.add_text(
                        nb.id, source_file.name,
                        source_file.read_text(encoding="utf-8"),
                        wait=True,
                    )
            status = await client.artifacts.generate_audio(
                nb.id, instructions=episode_focus_prompt
            )
            await client.artifacts.wait_for_completion(
                nb.id, status.task_id, timeout=1800.0
            )
            await client.artifacts.download_audio(nb.id, str(output_path))
        finally:
            await client.notebooks.delete(nb.id)
```

Key differences:
- Async-only API requires `asyncio.run()` or similar wrapper in the sync `_generate_audio_nlm` method
- Auth via Playwright storage state file (`~/.notebooklm/storage_state.json`) instead of separate `nlm login`
- `add_text()` replaces `upload_source()` for raw text content
- `generate_audio()` returns a `GenerationStatus` with `task_id` for polling
- `wait_for_completion()` replaces `wait_for_audio()` with explicit task tracking

#### 2. Delete archived Enterprise API code

- Delete `apps/podcast/tools/notebooklm_api.py` entirely (530 lines, archived since 2026-02-19)
- Remove `generate_audio()` from `apps/podcast/services/audio.py` (the Enterprise API wrapper that imports from the deleted file)
- Keep `transcribe_audio()` and `generate_episode_chapters()` in `audio.py` (these are still active)

#### 3. Handle `notebooklm_prompt.py`

This file generates episode focus prompts for manual use. It imports `episode_config` which is not part of the Django project. However, the same prompt generation logic exists in `notebooklm_api.py::generate_episode_focus()`. Decision: **Delete** `notebooklm_prompt.py` as it's a standalone CLI script that references external modules not in the codebase. The episode focus prompt template already lives in the `local_audio_worker` flow.

#### 4. Update `audio.py` service layer

Remove the `generate_audio()` function and its imports from `notebooklm_api.py`. The remaining functions (`transcribe_audio`, `generate_episode_chapters`) stay untouched.

#### 5. Update references in docstrings/comments

Files with NotebookLM references to update:
- `apps/podcast/services/plan_episode.py` - `NotebookLMGuidance` model name (keep as-is, it's a domain concept)
- `apps/podcast/services/prompts/plan_episode.md` - NotebookLM prompt instructions (keep, still relevant)
- `apps/podcast/services/synthesis.py` - Description references (update comment)
- `apps/podcast/services/workflow_progress.py` - Step description (update string)
- `apps/podcast/models/podcast_config.py` - Help text (update reference)

## Rabbit Holes

- **Exposing new notebooklm-py features (video, quizzes, research agents)**: The library offers much more than audio, but this migration should only swap the audio generation path. New capabilities are a separate project.
- **Building a sync wrapper around the async API**: Use `asyncio.run()` simply. Don't build elaborate sync/async bridge patterns.
- **Rewriting the prompt template system**: The episode focus prompt works fine. Don't refactor prompt generation as part of this migration.
- **Changing the worker polling architecture**: The worker's poll-generate-callback pattern is sound. Only the internal `_generate_audio_nlm` call changes.

## Risks

### Risk 1: notebooklm-py uses undocumented Google APIs
**Impact:** Audio generation could break if Google changes internal APIs (same risk as notebooklm-mcp-cli)
**Mitigation:** Same risk profile as current library. Both use unofficial APIs. notebooklm-py has broader community support and more active maintenance.

### Risk 2: Async API in synchronous worker context
**Impact:** Threading interactions with `asyncio.run()` in the `ThreadPoolExecutor` used by `local_audio_worker`
**Mitigation:** Each thread gets its own event loop via `asyncio.run()`, which creates and destroys the loop. This is safe in a thread pool. Test thoroughly.

### Risk 3: Authentication mechanism change
**Impact:** Workers need `~/.notebooklm/storage_state.json` instead of `nlm login` state
**Mitigation:** Document the new auth setup (`notebooklm login` command from notebooklm-py CLI). The auth mechanism is similar (browser cookies) but stored differently.

## No-Gos (Out of Scope)

- Adding new notebooklm-py features (video overviews, quizzes, etc.)
- Changing the worker polling architecture or API callback mechanism
- Modifying the episode focus prompt content
- Updating the `notebooklm-audio` or `notebooklm-enterprise-api` skills (documentation-only changes for /do-docs)
- Running migrations (no model changes in this PR)

## Update System

No update system changes required -- this is a dependency swap in the Django project. The `notebooklm-py` package is added via `pyproject.toml` and installed with `uv sync`.

## Agent Integration

No agent integration required -- audio generation is triggered by the local_audio_worker management command, not by MCP tools or the Telegram bridge.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/local-audio-worker.md` to reference notebooklm-py
- [ ] Update `docs/features/podcast-services.md` to remove archived generate_audio reference

### Inline Documentation
- [ ] Updated docstrings in `audio.py` (remove archived function, update module docstring)
- [ ] Updated docstrings in `local_audio_worker.py` (reference notebooklm-py)
- [ ] Update CLAUDE.md references to notebooklm-mcp-cli

## Success Criteria

- [ ] `notebooklm-py` added to `pyproject.toml` dependencies
- [ ] `notebooklm-mcp-cli` references removed from codebase
- [ ] `apps/podcast/tools/notebooklm_api.py` deleted
- [ ] `apps/podcast/tools/notebooklm_prompt.py` deleted
- [ ] `apps/podcast/services/audio.py::generate_audio()` removed; `transcribe_audio` and `generate_episode_chapters` preserved
- [ ] `local_audio_worker._generate_audio_nlm()` uses `notebooklm-py` async API
- [ ] Tests updated to mock `notebooklm` instead of `notebooklm_mcp_cli`
- [ ] All NotebookLM docstring/comment references updated
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (migration)**
  - Name: migration-builder
  - Role: Swap dependency, rewrite audio generation, delete archived code
  - Agent Type: builder
  - Resume: true

- **Validator (migration)**
  - Name: migration-validator
  - Role: Verify all code paths work, no broken imports
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add notebooklm-py dependency
- **Task ID**: build-dependency
- **Depends On**: none
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `notebooklm-py` to `pyproject.toml` dependencies
- Run `uv sync --all-extras` to verify installation

### 2. Rewrite _generate_audio_nlm in local_audio_worker
- **Task ID**: build-audio-worker
- **Depends On**: build-dependency
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `notebooklm_mcp_cli` import with `notebooklm`
- Rewrite method to use async API with `asyncio.run()`
- Update error messages and import error handling
- Update module docstring to reference notebooklm-py

### 3. Delete archived Enterprise API code
- **Task ID**: build-cleanup
- **Depends On**: build-dependency
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `apps/podcast/tools/notebooklm_api.py`
- Delete `apps/podcast/tools/notebooklm_prompt.py`
- Remove `generate_audio()` function from `apps/podcast/services/audio.py`
- Remove imports from `notebooklm_api` in `audio.py`
- Update `audio.py` module docstring

### 4. Update references and docstrings
- **Task ID**: build-references
- **Depends On**: build-cleanup
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `apps/podcast/services/synthesis.py` docstring
- Update `apps/podcast/services/workflow_progress.py` step description
- Update `apps/podcast/models/podcast_config.py` help text
- Grep for remaining `notebooklm-mcp-cli` or `notebooklm_mcp_cli` references and update

### 5. Update tests
- **Task ID**: build-tests
- **Depends On**: build-audio-worker, build-cleanup
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `test_local_audio_worker.py` to mock `notebooklm` instead of `notebooklm_mcp_cli`
- Update `GenerateAudioNLMTestCase` for new async API
- Verify no test imports reference deleted modules

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-tests, build-references
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_local_audio_worker.py -v`
- Run `DJANGO_SETTINGS_MODULE=settings pytest` (full suite)
- Verify no import errors: `python -c "from apps.podcast.services.audio import transcribe_audio, generate_episode_chapters"`
- Verify deleted files don't exist
- Grep for stale references

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: migration-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/local-audio-worker.md`
- Update `docs/features/podcast-services.md`
- Update CLAUDE.md references

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_local_audio_worker.py -v` - Tests pass for audio worker
- `DJANGO_SETTINGS_MODULE=settings pytest` - Full test suite passes
- `python -c "from apps.podcast.services.audio import transcribe_audio, generate_episode_chapters"` - Remaining audio services importable
- `! test -f apps/podcast/tools/notebooklm_api.py` - Archived file deleted
- `! test -f apps/podcast/tools/notebooklm_prompt.py` - Prompt tool deleted
- `grep -r "notebooklm_mcp_cli" apps/ --include="*.py" | wc -l` - Zero references to old library

---

## Resolved Questions

1. **Episode focus prompt in worker**: **Resolved -- pass instructions.** The worker now extracts the `content_plan.md` source from the API response, isolates the NotebookLM Guidance section via regex, and passes it as the `instructions` parameter to `generate_audio()`. Falls back to the full content plan if the section header isn't found, or `None` if no content plan is available (NotebookLM uses its default behavior).

2. **Auth storage location**: **Resolved -- env var for Render.** Use `NOTEBOOKLM_AUTH_JSON` environment variable on Render (paste the JSON content of `~/.notebooklm/storage_state.json`). For local development, use `notebooklm login` to generate the file directly. The `notebooklm-py` client checks the env var first, falling back to the file. Documented in `docs/features/local-audio-worker.md`.

3. **prompts.py file**: **Resolved -- updated references.** The `apps/podcast/tools/prompts.py` file contains episode focus prompt templates that are still used by standalone CLI tools. NotebookLM references in the file were updated to say `notebooklm-py` as part of the documentation cleanup. No functional changes needed.
