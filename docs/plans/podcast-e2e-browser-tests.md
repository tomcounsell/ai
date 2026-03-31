# Podcast Creation E2E Browser Test Suite

**Issue:** #194
**Status:** PLAN
**Branch:** `session/podcast-e2e-browser-tests`

---

## Goal

Composable Playwright E2E browser tests for the podcast creation UI, organized in 5 layers that build on each other. The full happy path (Layer 5) chains all layers into a single flow. Tests run against a local Django dev server via `browser_test_runner.py` and a read-only subset can target production.

---

## Architecture Decisions

### Test framework: Playwright sync API (not async)

The existing production pages test (`apps/public/tests/test_e2e_production_pages.py`) uses `playwright.sync_api` with pytest. The patterns file (`test_e2e_patterns.py`) uses async `browser-use` but that is overly complex and has import issues. All new tests will follow the simpler sync Playwright pattern from `test_e2e_production_pages.py`.

### Test runner: `browser_test_runner.py`

Each test file is independently runnable:
```bash
python tools/testing/browser_test_runner.py apps/podcast/tests/test_e2e_*.py
```

The runner starts a Django dev server on port 8000 (or reuses one already running), then runs pytest.

### Server URL: environment variable

Tests use `BASE_URL = os.environ.get("PRODUCTION_URL", "http://localhost:8000")` so they work against both local dev and production. Production-targeted tests skip any write operations.

### No real AI pipeline calls

Tests never trigger the actual AI task pipeline. Workflow states are set up via Django ORM fixtures that run inside a management command or a setup script before the test server starts. For the local dev server approach, we use a `conftest.py` that calls Django ORM setup via `django.setup()`.

---

## Fixture & Factory Strategy

### New file: `apps/podcast/tests/e2e_fixtures.py`

A standalone module that creates test data via Django ORM. Called from `conftest.py` at session scope. Does NOT use Django TestCase (which wraps in transactions) -- it creates real database rows that persist for the test server process.

**Required fixture objects:**

| Object | Purpose | Key fields |
|--------|---------|------------|
| `staff_user` | Login for all write tests | `username=e2e_staff`, `password=e2e_pass_123`, `is_staff=True` |
| `owner_user` | Owner access tests | `username=e2e_owner`, `password=e2e_pass_123`, `is_staff=False` |
| `regular_user` | Access denial tests | `username=e2e_regular`, `password=e2e_pass_123`, `is_staff=False` |
| `podcast` | The podcast under test | `title=E2E Test Podcast`, `slug=e2e-test-podcast`, `owner=owner_user`, published |
| `draft_episode` | For create/edit tests | `status=draft`, step 1 workflow, no artifacts |
| `mid_pipeline_episode` | For workflow display tests | `status=in_progress`, workflow at step 5, several artifacts |
| `paused_episode` | For audio upload tests | `status=in_progress`, workflow `paused_for_human` at Audio Generation |
| `publishable_episode` | For publish flow tests | `status=in_progress`, workflow at Publish step, has audio_url, metadata |
| `published_episode` | For detail page tests | `status=complete`, `published_at` set, audio_url, report_text, sources_text |

**Workflow state helpers** (functions in `e2e_fixtures.py`):

```python
def create_workflow_at_step(episode, step_name, status="running", history=None):
    """Create an EpisodeWorkflow at a specific step with optional history."""

def create_artifact(episode, artifact_type, content="..."):
    """Create an EpisodeArtifact with given type and content."""
```

### conftest.py: `apps/podcast/tests/conftest.py`

```python
@pytest.fixture(scope="session")
def e2e_data(django_db_setup):
    """Create all E2E fixture data once per test session."""
    from apps.podcast.tests.e2e_fixtures import setup_e2e_data
    return setup_e2e_data()
```

Individual test files receive fixture data through this session-scoped fixture.

### Production-safe approach

When `PRODUCTION_URL` is set, fixture setup is skipped entirely. Tests that need specific data are marked `@pytest.mark.local_only` and skipped in production mode. Read-only tests (episode detail, podcast list) run against whatever data exists in production.

---

## Mocking the AI Pipeline

Tests never call the real AI pipeline. Instead:

1. **Workflow states are pre-created via ORM fixtures.** Each test scenario gets an episode with the workflow already at the desired step and status. No need to "run" the pipeline.

2. **For polling tests (Layer 2):** A lightweight Django view override or direct DB update simulates workflow progression. The test:
   - Creates episode with workflow at step N, status `running`
   - Uses `page.evaluate()` or a second thread to update the workflow status in DB
   - Verifies the HTMX polling endpoint reflects the new state

3. **No external API keys required.** Tests work with zero AI service configuration.

---

## Layer-by-Layer Test Plan

### Layer 1: Foundation (no workflow state needed)

**File:** `apps/podcast/tests/test_e2e_podcast_auth.py`

| Test | What it verifies |
|------|-----------------|
| `test_staff_can_login` | Login with staff credentials, verify redirect to dashboard |
| `test_owner_can_login` | Login with owner credentials |
| `test_anonymous_redirected_from_workflow` | GET workflow URL without login -> redirect to login page |
| `test_regular_user_denied_workflow_access` | Login as regular user, GET workflow URL -> 403 |
| `test_staff_can_access_workflow` | Login as staff, GET workflow URL -> 200 |
| `test_owner_can_access_create_form` | Login as owner, GET new episode URL -> 200 |

**File:** `apps/podcast/tests/test_e2e_podcast_navigation.py`

| Test | What it verifies |
|------|-----------------|
| `test_podcast_list_loads` | `/podcast/` returns page with podcast title visible |
| `test_podcast_list_links_to_detail` | Click podcast title -> navigates to `/podcast/{slug}/` |
| `test_podcast_detail_shows_new_episode_button` | Owner sees "New Episode" button on detail page |
| `test_podcast_detail_hides_new_episode_for_anon` | Anonymous user does not see "New Episode" |
| `test_breadcrumb_navigation` | Podcast detail has breadcrumb link back to `/podcast/` |

**File:** `apps/podcast/tests/test_e2e_episode_create.py`

| Test | What it verifies |
|------|-----------------|
| `test_create_form_loads` | `/podcast/{slug}/new/` shows form with title and description fields |
| `test_submit_creates_episode` | Fill title + description, submit -> redirected to workflow step 1 |
| `test_created_episode_title_matches` | After create, workflow page shows the entered title |
| `test_empty_title_shows_validation_error` | Submit with empty title -> form error (no redirect) |

**Shared helper module:** `apps/podcast/tests/e2e_helpers.py`

```python
def login_as(page, base_url, username, password):
    """Navigate to login page, fill credentials, submit, verify redirect."""

def is_production():
    """Return True if PRODUCTION_URL is set."""
```

### Layer 2: Workflow UI (needs episodes in various states)

**File:** `apps/podcast/tests/test_e2e_workflow_display.py`

| Test | What it verifies |
|------|-----------------|
| `test_workflow_shows_12_phases` | Phase sidebar contains 12 phase entries |
| `test_current_phase_highlighted` | Active phase has distinct visual state (CSS class) |
| `test_phase_click_navigates` | Clicking a phase in sidebar navigates to that step URL |
| `test_step_content_area_loads` | Main content area is non-empty for steps 1-12 |
| `test_htmx_partial_vs_full_page` | Direct GET returns full page; HTMX request returns partial |

**File:** `apps/podcast/tests/test_e2e_workflow_fields.py`

| Test | What it verifies |
|------|-----------------|
| `test_title_field_editable_on_step1` | Step 1 has editable title input |
| `test_description_field_editable_on_step1` | Step 1 has editable description textarea |
| `test_inline_field_save_via_htmx` | Edit title via HTMX, verify DB update (check page reload shows new value) |

**File:** `apps/podcast/tests/test_e2e_workflow_polling.py`

| Test | What it verifies |
|------|-----------------|
| `test_status_endpoint_returns_json_or_partial` | GET status polling URL returns valid response |
| `test_polling_reflects_workflow_change` | Update workflow status in DB, re-poll, verify new status shown |

### Layer 3: Key Interactions

**File:** `apps/podcast/tests/test_e2e_audio_upload.py`

| Test | What it verifies |
|------|-----------------|
| `test_step9_shows_upload_form_when_paused` | Episode paused at Audio Generation shows file input |
| `test_step9_shows_audio_player_when_audio_exists` | Episode with `audio_url` shows `<audio>` element |
| `test_upload_form_accepts_file` | Upload a small `.mp3` test file, verify form submission succeeds |

**File:** `apps/podcast/tests/test_e2e_artifact_viewer.py`

| Test | What it verifies |
|------|-----------------|
| `test_artifact_content_loads` | Artifact URL returns content for a populated artifact |
| `test_artifact_section_expandable` | Click artifact section, verify content appears (HTMX lazy load) |

**File:** `apps/podcast/tests/test_e2e_publish_flow.py`

| Test | What it verifies |
|------|-----------------|
| `test_step12_shows_publish_confirmation` | Step 12 has confirmation UI with "publish" and "confirm" text |
| `test_publish_marks_episode_complete` | After publish action, episode detail page is accessible |
| `test_post_publish_shows_view_episode_link` | After publish, "View Episode" link appears |

### Layer 4: Published Episode

**File:** `apps/podcast/tests/test_e2e_episode_detail.py`

| Test | What it verifies |
|------|-----------------|
| `test_episode_detail_loads` | `/podcast/{slug}/{ep_slug}/` returns 200 |
| `test_audio_player_present` | Page contains `<audio>` element with correct `src` |
| `test_download_button_present` | Page has "Download" link |
| `test_report_link_works` | "View Report" link navigates to report page |
| `test_sources_link_works` | "View Sources" link navigates to sources page |
| `test_platform_links_present` | Spotify and Apple Podcasts links visible |
| `test_rss_link_present` | RSS feed link visible |
| `test_back_navigation_to_podcast` | "Back to" link navigates to podcast detail |

This layer is fully production-compatible (read-only).

### Layer 5: Full Happy Path (composition)

**File:** `apps/podcast/tests/test_e2e_podcast_happy_path.py`

One long test that chains the entire flow:

```
1. Login as staff
2. Navigate to podcast detail
3. Click "New Episode"
4. Fill creation form (title + description)
5. Submit -> verify redirect to workflow step 1
6. Verify editable fields on step 1
7. Navigate through workflow steps (click sidebar phases)
8. At step 9, verify audio section (upload form or player)
9. Navigate to step 12, verify publish confirmation
10. View published episode detail (using pre-created published_episode)
11. Verify audio player, resources, platform links
```

Steps 1-9 use a freshly created episode. Steps 10-11 use the pre-created `published_episode` fixture (since we cannot actually run the pipeline in tests).

The happy path test is marked `@pytest.mark.local_only` since it creates data.

---

## File Structure

```
apps/podcast/tests/
    conftest.py                          # Session-scoped E2E fixtures (NEW)
    e2e_fixtures.py                      # ORM-based fixture creation (NEW)
    e2e_helpers.py                       # Shared login/navigation helpers (NEW)
    test_e2e_podcast_auth.py             # Layer 1: Auth (NEW)
    test_e2e_podcast_navigation.py       # Layer 1: Navigation (NEW)
    test_e2e_episode_create.py           # Layer 1: Create form (NEW)
    test_e2e_workflow_display.py         # Layer 2: Workflow display (NEW)
    test_e2e_workflow_fields.py          # Layer 2: Inline fields (NEW)
    test_e2e_workflow_polling.py         # Layer 2: Status polling (NEW)
    test_e2e_audio_upload.py             # Layer 3: Audio upload (NEW)
    test_e2e_artifact_viewer.py          # Layer 3: Artifact viewer (NEW)
    test_e2e_publish_flow.py             # Layer 3: Publish flow (NEW)
    test_e2e_episode_detail.py           # Layer 4: Published episode (NEW)
    test_e2e_podcast_happy_path.py       # Layer 5: Full composition (NEW)
```

Total: 14 new files (3 support + 11 test files).

---

## Test Audio File

A minimal valid MP3 file is needed for the audio upload test. Create a 1-second silent MP3:
```bash
# Generate with ffmpeg (one-time, check into repo)
ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 1 -q:a 9 apps/podcast/tests/fixtures/silent.mp3
```

Store at `apps/podcast/tests/fixtures/silent.mp3` (approximately 2KB).

---

## Existing Code to Reference/Reuse

| File | What to reuse |
|------|---------------|
| `apps/public/tests/test_e2e_production_pages.py` | Sync Playwright pattern, `page` fixture, `PRODUCTION_URL` env var |
| `apps/podcast/tests/test_ui_episode_editor.py` | All 30+ test scenarios as the "what to verify" reference; setUp patterns for Podcast/Episode/EpisodeWorkflow creation |
| `apps/podcast/tests/test_workflow_views.py` | Workflow URL construction, access control patterns |
| `tools/testing/browser_test_runner.py` | Server lifecycle management (use as-is) |
| `apps/common/tests/factories.py` | `UserFactory` for user creation pattern |
| `apps/podcast/models/episode_workflow.py` | Workflow status choices and step names |

---

## CI Integration Plan

### Phase 1: Local-only (current)

All E2E tests run locally via `browser_test_runner.py`. Developer runs:
```bash
python tools/testing/browser_test_runner.py apps/podcast/tests/test_e2e_podcast_auth.py
# or all at once:
python tools/testing/browser_test_runner.py apps/podcast/tests/test_e2e_*.py
```

### Phase 2: GitHub Actions (future)

Add a CI job that:
1. Starts PostgreSQL service container
2. Runs `uv sync --all-extras`
3. Runs `playwright install chromium`
4. Runs `python manage.py migrate`
5. Runs `python tools/testing/browser_test_runner.py apps/podcast/tests/test_e2e_*.py`

Configuration in `.github/workflows/e2e.yml`. Runs on PR to `main` only (not every push) since E2E tests are slower.

### Phase 3: Production smoke tests (future)

A scheduled GitHub Action runs the production-compatible subset (Layer 4 tests) against `https://ai.yuda.me` nightly. Uses `PRODUCTION_URL` env var. Skips all `@pytest.mark.local_only` tests.

---

## Implementation Order (for BUILD stage)

1. **Support files first:** `e2e_helpers.py`, `e2e_fixtures.py`, `conftest.py`
2. **Layer 1:** `test_e2e_podcast_auth.py`, `test_e2e_podcast_navigation.py`, `test_e2e_episode_create.py`
3. **Layer 2:** `test_e2e_workflow_display.py`, `test_e2e_workflow_fields.py`, `test_e2e_workflow_polling.py`
4. **Layer 3:** `test_e2e_audio_upload.py`, `test_e2e_artifact_viewer.py`, `test_e2e_publish_flow.py`
5. **Layer 4:** `test_e2e_episode_detail.py`
6. **Layer 5:** `test_e2e_podcast_happy_path.py`
7. **Test audio fixture:** `apps/podcast/tests/fixtures/silent.mp3`

Each layer can be merged independently. Layer 1 is the minimum viable PR.

---

## Dependencies

- `playwright` (already in dev dependencies)
- No new packages required
- No migrations required
- No AI API keys required

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| E2E fixtures leak between test runs | Use unique slugs with `e2e-` prefix; add teardown that deletes `e2e-*` data |
| Database not clean for test server | `conftest.py` checks for existing e2e data and cleans up before creating |
| HTMX polling tests are flaky | Use explicit `page.wait_for_selector()` with generous timeouts; retry once on failure |
| Audio upload test needs real file | Use minimal silent.mp3 fixture (2KB, checked in) |
| Production tests find no data | Layer 4 production tests are lenient -- skip if expected podcast/episode not found |
