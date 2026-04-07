# Test Suite

Organized by test level and tagged by feature. Run `pytest --collect-only -q` for current counts.

## Running Tests

```bash
# By level
pytest tests/unit/               # Unit tests (~60s)
pytest tests/unit/ -n auto       # Unit tests in parallel
pytest tests/integration/        # Integration tests (needs Redis)
pytest -m e2e                    # E2E tests
pytest -m slow                   # Performance benchmarks

# By feature (works across all levels)
pytest -m sdlc                   # All SDLC pipeline tests (516)
pytest -m messaging              # All messaging tests (327)
pytest -m sessions               # All session tests (293)
pytest -m "sdlc or sessions"     # Combine features

# Targeted
pytest tests/unit/test_observer.py           # Single file
pytest tests/unit/test_observer.py::TestX    # Single class
```

## Feature Markers

Every test is auto-tagged by filename via `tests/conftest.py`. When a feature changes, run its marker to find tests that may need updating.

| Marker | What it covers | Example command |
|--------|----------------|-----------------|
| `sdlc` | Pipeline stages, observer, steering, hooks, state machine | `pytest -m sdlc` |
| `messaging` | Telegram routing, delivery, dedup, markdown, media | `pytest -m messaging` |
| `sessions` | Lifecycle, watchdog, stall detection, recovery, goals | `pytest -m sessions` |
| `summarizer` | Response summarization, nudge feedback, message formatting | `pytest -m summarizer` |
| `classifiers` | Intake, work requests, message quality, auto-continue | `pytest -m classifiers` |
| `validation` | Commit messages, plan sections, build checks, docs audit | `pytest -m validation` |
| `reflections` | Learning system, bug detection, scheduling, reports | `pytest -m reflections` |
| `tools` | Search, code execution, link analysis, image analysis | `pytest -m tools` |
| `jobs` | Job scheduling, queue priority, health monitoring | `pytest -m jobs` |
| `git` | Branch management, worktrees, workspace safety | `pytest -m git` |
| `models` | Redis/Popoto model relationships and persistence | `pytest -m models` |
| `monitoring` | Health checks, telemetry, watchdog, benchmarks | `pytest -m monitoring` |
| `impact` | Code and documentation impact analysis | `pytest -m impact` |
| `context` | Context modes, session tags, enrichment | `pytest -m context` |
| `config` | Configuration loading, settings, remote updates | `pytest -m config` |
| `sdk` | Claude SDK client, permissions, SDLC enforcement | `pytest -m sdk` |

Check counts with: `pytest -m <marker> --collect-only -q`

## Directory Structure

```
tests/
├── conftest.py              # Root fixtures + feature auto-tagging
├── unit/                    # Pure logic, no external deps
├── integration/             # Requires Redis and/or network
├── tools/                   # Tool-specific tests (may need API keys)
├── e2e/                     # Full-stack synthetic flows
├── performance/             # Benchmarks and endurance
├── ai_judge/                # AI judge validation
```

## Test Index by Feature

### `messaging` — Telegram message handling

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_bridge_logic.py` | 40 | Group-to-project mapping, routing |
| unit | `test_valor_telegram.py` | 17 | Telegram command handling |
| unit | `test_media_handling.py` | 17 | Media attachment handling |
| unit | `test_transcript_liveness.py` | 12 | Transcript state management |
| unit | `test_messenger.py` | 11 | Message formatting and delivery |
| unit | `test_duplicate_delivery.py` | 7 | Duplicate message prevention |
| unit | `test_file_extraction.py` | 20 | File extraction from messages |
| integration | `test_message_routing.py` | 21 | Message routing end-to-end |
| integration | `test_reply_delivery.py` | — | Reply delivery flow |
| integration | `test_unthreaded_routing.py` | 7 | Unthreaded message routing |
| e2e | `test_message_pipeline.py` | 37 | Full routing → context → response flow |

### `sdlc` — Pipeline stages and observer

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_observer.py` | 81 | Stage detection, routing, progression |
| unit | `test_skills_audit.py` | 53 | Skills directory structure validation |
| unit | `test_pipeline_integrity.py` | 31 | Pipeline state preservation |
| unit | `test_post_tool_use_sdlc.py` | 31 | Post-tool SDLC hook execution |
| unit | `test_pipeline_graph.py` | 29 | Pipeline graph visualization |
| unit | `test_observer_early_return.py` | 18 | Early return optimization |
| unit | `test_pipeline_state.py` | 15 | Pipeline state transitions |
| unit | `test_observer_message_for_user.py` | 11 | Observer user messaging |
| unit | `test_sdlc_env_vars.py` | 10 | SDLC environment variable injection |
| unit | `test_stop_reason_observer.py` | 7 | Stop reason classification |
| unit | `test_sdlc_mode.py` | 6 | SDLC mode enforcement |
| unit | `test_pre_tool_use_hook.py` | 6 | Pre-tool hook validation |
| unit | `test_stop_hook.py` | 12 | Stop hook enforcement |
| unit | `test_sdlc_reminder.py` | — | SDLC reminder messaging |
| integration | `test_steering.py` | 32 | Steering queue push/pop/clear |
| integration | `test_cross_repo_build.py` | 8 | Cross-repo build flow |
| integration | `test_artifact_inference.py` | 15 | Artifact-based pipeline stage inference (real gh CLI + filesystem) |

### `sessions` — Session lifecycle and health

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_stall_detection.py` | 49 | Stall detection, backoff, retry |
| unit | `test_goal_gates.py` | 37 | Goal gate evaluation |
| unit | `test_session_watchdog.py` | 35 | Health assessment, error cascades |
| unit | `test_open_question_gate.py` | 32 | Open question detection |
| unit | `test_pending_recovery.py` | 21 | Pending stall recovery (consolidated) |
| unit | `test_escape_hatch.py` | 18 | Escape hatch for stuck sessions |
| unit | `test_session_status.py` | 15 | Session status tracking |
| unit | `test_worker_entry.py` | 24 | Worker entry point startup, config loading, argument parsing |
| integration | `test_agent_session_lifecycle.py` | 58 | Session lifecycle, history, summarizer |
| integration | `test_lifecycle_transition.py` | 16 | Session state transitions |
| e2e | `test_session_continuity.py` | 11 | Session creation, resume, transcript |

### `summarizer` — Response processing

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_summarizer.py` | 158 | Response summarization, classification |

### `classifiers` — Message classification

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_message_quality.py` | 30 | Message quality scoring |
| unit | `test_intake_classifier.py` | 23 | Message classification |
| unit | `test_auto_continue.py` | 22 | Auto-continue logic |
| unit | `test_work_request_classifier.py` | 16 | Work request classification |
| integration | `test_stage_aware_auto_continue.py` | 39 | Stage-aware auto-continue |
| tools | `test_classifier.py` | 17 | Classifier tool tests |

### `validation` — Quality checks and parsing

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_docs_auditor.py` | 62 | Documentation reference validation |
| unit | `test_features_readme_sort.py` | 27 | README table sorting |
| unit | `test_verification_parser.py` | 20 | Verification section parsing |
| unit | `test_validate_test_impact.py` | 20 | Test impact section validation |
| unit | `test_validate_commit_message.py` | 16 | Commit message format |
| unit | `test_validate_sdlc_on_stop.py` | 12 | SDLC stop validation |
| unit | `test_validate_verification_section.py` | 9 | Verification validation |
| unit | `test_build_validation.py` | 6 | Build process validation |

### `reflections` — Learning system

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_reflections.py` | 39 | LLM reflection, bug detection |
| unit | `test_reflection_scheduler.py` | 35 | Cron-based scheduling |
| unit | `test_reflections_multi_repo.py` | 21 | Multi-repo reflections |
| unit | `test_reflections_report.py` | 20 | Reflection reports |
| unit | `test_reflections_scheduling.py` | 19 | Launchd infrastructure |
| integration | `test_reflections_redis.py` | 20 | Reflection persistence |

### `tools` — Individual tool tests

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| tools | `test_telegram_history.py` | 47 | Message storage, search, links |
| tools | `test_link_analysis.py` | 25 | URL extraction, metadata |
| tools | `test_code_execution.py` | 24 | Code execution, safety checks |
| tools | `test_test_judge.py` | 16 | AI judge validation |
| tools | `test_doc_summary.py` | 14 | Document summarization |
| tools | `test_image_analysis.py` | 12 | Image analysis |
| tools | `test_knowledge_search.py` | 11 | Knowledge base search |
| tools | `test_search.py` | 11 | Web search |
| ai_judge | `test_ai_judge.py` | 24 | AI judge prompts, evaluation |

### `jobs` — Job scheduling and queue

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_job_hierarchy.py` | 22 | Job priority and hierarchy |
| unit | `test_agent_session_queue_revival_helper.py` | 7 | Queue revival prompt helper, cooldown tracking |
| integration | `test_enqueue_continuation.py` | 29 | Continuation job enqueuing |
| integration | `test_job_scheduler.py` | 21 | 4-tier priority, FIFO |
| integration | `test_job_health_monitor.py` | 20 | Job health monitoring |
| integration | `test_job_queue_race.py` | 13 | Race condition prevention |

### `git` — Version control operations

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_worktree_manager.py` | 28 | Worktree management |
| unit | `test_git_state_guard.py` | 21 | Git state validation |
| unit | `test_workspace_safety.py` | 18 | Workspace safety checks |
| unit | `test_branch_manager.py` | 11 | Branch creation/deletion |
| unit | `test_symlinks.py` | 6 | Symlink handling |

### `models` — Data persistence

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_model_relationships.py` | 30 | Redis model relationships |
| unit | `test_memory_model.py` | 151 | Memory model (decay, confidence, write filter, bloom) |
| unit | `test_memory_hook.py` | 135 | PostToolUse thought injection, sliding window |
| unit | `test_memory_extraction.py` | 107 | Post-session Haiku extraction, outcome detection |
| unit | `test_memory_ingestion.py` | 89 | Telegram message memory ingestion |
| integration | `test_redis_models.py` | 30 | Popoto model CRUD |

### `monitoring` — Observability

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_telemetry.py` | 27 | Telemetry data collection |
| unit | `test_health_check.py` | 12 | Health monitoring |
| unit | `test_bridge_watchdog.py` | — | Bridge watchdog |
| integration | `test_connectivity_gaps.py` | 12 | Connectivity failure handling |
| integration | `test_silent_failures.py` | 7 | Silent failure detection |
| performance | `test_benchmarks.py` | 16 | Latency, throughput, memory |

### `impact` — Code/doc change analysis

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_doc_impact_finder.py` | 21 | Documentation impact analysis |
| unit | `test_code_impact_finder.py` | 19 | Code file impact analysis |
| unit | `test_cross_repo_gh_resolution.py` | 11 | Cross-repo GitHub resolution |
| unit | `test_cross_wire_fixes.py` | 7 | Cross-wire fix application |
| integration | `test_doc_impact_finder_sdk.py` | 13 | Doc impact with SDK |

### `context` — Context and tagging

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_session_tags.py` | 33 | Session tagging |
| unit | `test_context_modes.py` | 27 | Context mode selection |
| unit | `test_pm_channels.py` | 19 | PM channel routing |

### `config` — Configuration and deployment

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| integration | `test_remote_update.py` | 29 | Remote update execution |
| e2e | `test_config_bootstrap.py` | 13 | Config loading, health checks |

### `sdk` — Claude SDK integration

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_sdk_client_sdlc.py` | 32 | SDK SDLC enforcement |
| unit | `test_sdk_client.py` | 7 | SDK client basics |
| unit | `test_sdk_permissions.py` | 7 | SDK permissions |
| unit | `test_workflow_sdk_integration.py` | 6 | Workflow SDK integration |

### Other

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| e2e | `test_telegram_flow.py` | — | Live Telegram flow stubs |

## Fixtures

| Fixture | Scope | Source | Purpose |
|---------|-------|--------|---------|
| `mock_claude_sdk_cleanup` | autouse | `conftest.py` | SDK mock cleanup between tests |
| `redis_test_db` | autouse | `conftest.py` | Per-worker Redis db isolation |
| `sample_config` | function | `conftest.py` | 3-project sample configuration |
| `valor_project` | function | `conftest.py` | Single project config |
| `mock_telegram_client` | function | `e2e/conftest.py` | AsyncMock Telethon client |
| `make_telegram_event` | function | `e2e/conftest.py` | Telegram event factory |
| `mock_agent_response` | function | `e2e/conftest.py` | Canned agent response |
| `e2e_config` | function | `e2e/conftest.py` | Config with test overrides |
| `perplexity_api_key` | function | `tools/conftest.py` | Perplexity API key (skip if missing) |
| `anthropic_api_key` | function | `tools/conftest.py` | Anthropic API key (skip if missing) |

## Adding Tests for New Features

1. **Pick the right level**: Unit for pure logic, integration for Redis/network, e2e for multi-component flows
2. **Name the file** with a keyword from `FEATURE_MAP` in `tests/conftest.py` so it auto-tags
3. **Or add a new entry** to `FEATURE_MAP` if creating a new feature area
4. **Add to this index** under the appropriate feature section

### Naming Convention

```
test_{feature_keyword}[_detail].py
```

The `{feature_keyword}` must match a key in `FEATURE_MAP` (in `tests/conftest.py`) for auto-tagging. Examples:
- `test_pipeline_new_stage.py` → auto-tagged `sdlc`
- `test_session_timeout.py` → auto-tagged `sessions` (matches "session_")
- `test_bridge_rate_limit.py` → auto-tagged `messaging` (matches "bridge")

### Feature Marker Registration

New markers must be added in two places:
1. `pyproject.toml` → `[tool.pytest.ini_options]` markers list
2. `tests/conftest.py` → `FEATURE_MAP` dictionary

## Known Blind Spots

Source modules with no test coverage. Priority targets for new tests.

| Priority | Module | Lines | Risk |
|----------|--------|------:|------|
| Critical | `bridge/telegram_bridge.py` | 1,655 | Main entry point |
| Critical | `agent/hooks/` | ~150 | Dev session lifecycle hooks |
| Critical | `bridge/context.py` | 557 | Context building |
| Critical | `bridge/response.py` | 579 | Response formatting |
| Critical | `config/loader.py` | 432 | Config initialization |
| Critical | `scripts/update/` (6 files) | 2,140 | Deployment system |
| High | `monitoring/` (5 of 6 modules) | 1,200+ | Reliability |
| High | `agent/completion.py` | 316 | Auto-continue |
| High | `tools/job_scheduler.py` | 705 | Async work execution |
| Medium | Hook validators (29 files) | ~1,500 | Config enforcement |
