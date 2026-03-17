---
status: Ready
type: chore
appetite: Large
owner: Valor
created: 2026-03-15
tracking: https://github.com/tomcounsell/ai/issues/416
last_comment_id:
---

# Config Consolidation

## Problem

Project and system configuration is scattered across 13+ categories of tech debt: hardcoded paths, duplicate config files, undocumented env vars, inconsistent session names, and config loading patterns that conflict with each other.

**Current behavior:**
- `/Users/valorengels` hardcoded in 30+ locations across Python, shell, JSON, and plist files
- 4 different Telegram session names (`valor_bridge`, `ai_rebuild_session`) with 4 stale session files
- `~/Desktop/claude_code/` used as a shadow config directory for Google auth, DM whitelist, calendar config
- `config/settings.py` is a comprehensive Pydantic Settings model that almost nothing actually uses
- 3 versions of projects config: `projects.json`, `projects.example.json`, `projects.json.example`
- `config/telegram_groups.json` and `config/workspace_config.json` vestigial but still referenced in code
- Redis URL, model names, log paths all hardcoded in various places
- `data/` directory has 46MB of undocumented runtime state with no cleanup policy
- 6+ env vars used in code but missing from `.env.example`

**Desired outcome:**
- One config architecture with clear ownership: `config/settings.py` as the single source of truth for runtime config
- Zero hardcoded absolute paths — all derived from `Path.home()` or `Path(__file__)`
- One canonical Telegram session name
- `~/Desktop/claude_code/` contents migrated to `config/` or `data/` with env var overrides
- `.env.example` complete and accurate
- `data/` directory documented with cleanup policy
- Vestigial config files removed

## Prior Art

- **PR #382**: "Patch tech debt: hardcoded paths and deprecated APIs" — Replaced `/Users/valorengels/src/ai` in `workflow_state.py`, `job_scheduler.py`, `telegram_history/cli.py` with `Path(__file__)` resolution. Also fixed Pydantic v1 deprecation and `datetime.utcnow()`. **Merged, partially addresses section G.**
- **Issue #398**: "Consolidate per-project config into projects.json" — The original comprehensive audit that identified all 13 categories (A-M). **Closed, consolidated into #416.**
- **PR #146**: "feat: daydream multi-repo support per machine config" — Established `projects.json` as the canonical project config. Relevant as prior art for the config-as-source-of-truth pattern.

## Data Flow

Config is loaded at multiple entry points with no unified path:

1. **Bridge startup** (`bridge/telegram_bridge.py`): Reads `TELEGRAM_SESSION_NAME` env var (default `valor_bridge`), loads DM whitelist from `~/Desktop/claude_code/dm_whitelist.json`
2. **SDK client** (`agent/sdk_client.py`): Hardcodes `allowed_root = Path("/Users/valorengels/src")`, reads `projects.json` for working directories
3. **Settings singleton** (`config/settings.py`): Pydantic Settings with `.env` support — defines session name as `ai_rebuild_session` (conflicts with bridge)
4. **Google auth** (`tools/google_workspace/auth.py`): Hardcodes `~/Desktop/claude_code/` for token/credential paths
5. **Redis** (`bridge/dedup.py`): Reads `REDIS_URL` env var with `redis://localhost:6379/0` fallback
6. **Models** (`config/models.py`): Centralizes most model constants, but `bridge/media.py` hardcodes `llama3.2-vision:11b`
7. **Launchd plists**: All paths absolute to `/Users/valorengels/src/ai/`
8. **Calendar tools**: Derive config from `~/Desktop/claude_code/calendar_config.json` independently of `projects.json`

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #382 | Replaced hardcoded paths in 3 Python files with `Path(__file__)` | Only addressed 3 of 30+ hardcoded path locations. Tests and config files untouched. |

**Root cause pattern:** Each fix addresses individual symptoms. No fix has established the *architectural pattern* that prevents new hardcoded values from being introduced. The codebase needs a centralized config module that other code imports, plus lint rules to catch regressions.

## Architectural Impact

- **New dependencies**: None — uses existing Pydantic Settings infrastructure
- **Interface changes**: `config/settings.py` gains new sections (Redis, Google auth, models, paths). Code that hardcodes values migrates to `from config.settings import settings`
- **Coupling**: Decreases — scattered inline config replaced by single import
- **Data ownership**: `config/settings.py` becomes the canonical owner of all runtime configuration. `projects.json` remains the canonical owner of per-project config.
- **Reversibility**: High — each subsection (paths, session names, Redis, etc.) can be reverted independently

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on `~/Desktop/claude_code/` migration, session name choice)
- Review rounds: 1 (code review for config architecture)

This is high-volume but low-risk refactoring. Most changes are mechanical find-and-replace. The risk is in getting the architecture right upfront and not breaking the bridge or launchd services.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge not running | `pgrep -f telegram_bridge \|\| echo "not running"` | Avoid config changes while bridge is active |

Run all checks: `python scripts/check_prerequisites.py docs/plans/config_consolidation.md`

## Solution

### Key Elements

- **Settings expansion**: Add Redis, Google auth, paths, and model sections to `config/settings.py`
- **Path resolver**: Utility that derives all paths from `Path.home()` and `Path(__file__).resolve()` — no hardcoded usernames
- **Session name unification**: Single `TELEGRAM_SESSION_NAME` with `valor_bridge` as canonical default everywhere
- **Config migration**: Move `~/Desktop/claude_code/` contents into `config/secrets/` (gitignored) with env var overrides
- **Vestigial cleanup**: Remove `telegram_groups.json`, `workspace_config.json`, `projects.json.example` (keep `projects.example.json` as the canonical example)
- **Env var documentation**: Update `.env.example` with all env vars actually used in code
- **Data directory policy**: Add `data/README.md` documenting each file/directory and cleanup schedule

### Flow

**Developer adds config** -> Adds to `config/settings.py` -> Uses `from config.settings import settings` -> Env var override via `.env` -> Documented in `.env.example`

### Technical Approach

- Expand `Settings` class with new Pydantic models: `RedisSettings`, `GoogleAuthSettings`, `PathSettings`, `ModelSettings`
- Create `config/paths.py` with `PROJECT_ROOT = Path(__file__).resolve().parent.parent` and derived paths
- Replace all `/Users/valorengels` references with path resolver calls
- Unify session name to `valor_bridge` in `config/settings.py`, `telegram_bridge.py`, `telegram_login.py`, `test_emoji_reactions.py`
- Move Google auth tokens from `~/Desktop/claude_code/` to `config/secrets/` with `GOOGLE_CREDENTIALS_DIR` env var override
- Move DM whitelist to `config/dm_whitelist.json` with env var override
- Add `REDIS_URL` to settings.py and update `bridge/dedup.py` to use it
- Add `OLLAMA_VISION_MODEL` to `config/models.py` and update `bridge/media.py`
- Generate launchd plists from template using `$HOME` instead of hardcoded paths
- Add ruff custom rule or grep-based CI check to catch new `/Users/` hardcoding

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `config/settings.py` validation errors must raise clear `ValidationError` with field names — test with invalid `.env` values
- [ ] Google auth path resolution must fail gracefully if credentials don't exist at new location — test with missing file

### Empty/Invalid Input Handling
- [ ] `REDIS_URL=""` should fall back to default, not crash
- [ ] `TELEGRAM_SESSION_NAME=""` should fall back to `valor_bridge`
- [ ] Missing `config/secrets/` directory should be auto-created on first access

### Error State Rendering
- [ ] Bridge startup with missing config should log clear error messages indicating which config is missing
- [ ] Settings validation failure should print which env var to set

## Test Impact

- [ ] `tests/unit/test_cross_repo_gh_resolution.py` — UPDATE: replace hardcoded `/Users/valorengels/src` with `Path.home() / "src"` in working_directory fixtures
- [ ] `tests/unit/test_workflow_sdk_integration.py` — UPDATE: replace hardcoded paths with derived paths from `config/paths.py`
- [ ] `tests/unit/test_sdk_client.py` — UPDATE: replace hardcoded paths with derived paths
- [ ] `tests/integration/test_job_scheduler.py` — UPDATE: replace hardcoded paths with derived paths
- [ ] `tests/unit/test_summarizer.py` — UPDATE: `_linkify_references` tests need to verify URLs are built from project config, not LLM output
- [ ] `tests/integration/test_agent_session_lifecycle.py` — UPDATE: `set_link` test fixtures use hardcoded `tomcounsell/ai` URLs that should come from project config

## Rabbit Holes

- **Rewriting projects.json schema**: The current schema works. Don't redesign it — just add missing per-project fields (testing credentials, calendar mappings)
- **Abstracting launchd plist generation**: A simple sed/envsubst template is fine. Don't build a plist generator framework.
- **Multi-machine config sync**: Out of scope. Each machine has its own `.env` and `config/secrets/`. The `/update` skill handles code sync.
- **Migrating away from `~/Desktop/claude_code/` for Claude OAuth**: The Claude CLI owns that path. Only migrate files *we* control (Google tokens, DM whitelist, calendar config).

## Risks

### Risk 1: Bridge downtime during session name migration
**Impact:** Telegram bridge disconnects if session file is renamed/moved incorrectly
**Mitigation:** Copy session file to new canonical name before switching config. Keep old file as backup. Test with `scripts/telegram_login.py` before restarting bridge.

### Risk 2: Google auth breaks after path migration
**Impact:** Calendar, Gmail, Drive tools stop working
**Mitigation:** Symlink old path to new location during transition. Add env var override so rollback is a single `.env` change.

### Risk 3: Launchd plists break after template conversion
**Impact:** Reflections, issue poller, watchdog stop running
**Mitigation:** Generate new plists, diff against old ones, test with `launchctl load` before removing originals.

## Race Conditions

No race conditions identified. Config is loaded at startup and cached. The bridge restart between old and new config is a brief window (~3 seconds) that's already handled by the watchdog service.

## No-Gos (Out of Scope)

- Redesigning `projects.json` schema (separate issue)
- Multi-machine config sync or central config server
- Migrating Claude CLI's own OAuth config from `~/Desktop/claude_code/`
- Cleaning up `data/pipeline/` contents (54 subdirs) — just document the policy
- Removing `doc_embeddings.json` (46MB) — just document it and add to cleanup schedule
- Changing the update/deployment architecture
- Stage tracking or Observer refactoring (addressed by [#430](https://github.com/tomcounsell/ai/issues/430))

## Interaction with #430 (State Machine)

[#430](https://github.com/tomcounsell/ai/issues/430) will delete `bridge/stage_detector.py`, `agent/skill_outcome.py`, and `agent/checkpoint.py` before or after this work. Any hardcoded paths in those files do not need fixing — they'll be gone.

More importantly: once this plan consolidates the `github.org` and `github.repo` fields in `projects.json` as the canonical source of GitHub repo identity, the summarizer's URL construction (`_linkify_references()` in `bridge/summarizer.py`) becomes fully deterministic — no LLM involved. This fixes the hallucinated repo URL problem (e.g., `valor-labs/valor-app` appearing instead of the real repo) by ensuring every URL is built from project config, never from LLM output.

## Update System

The update skill (`scripts/remote-update.sh`) needs changes:
- After pull, check if `config/secrets/` directory exists; create if missing
- If migrating from `~/Desktop/claude_code/`, copy Google tokens and DM whitelist to `config/secrets/` on first update
- Add `config/secrets/` to `.gitignore` (it holds per-machine secrets)
- Regenerate launchd plists from template after update if paths changed

## Agent Integration

No agent integration required — this is infrastructure/config refactoring. The agent's tools (`tools/google_workspace/auth.py`, `tools/valor_calendar.py`) will read from new config paths, but the MCP interface doesn't change. No new tools need to be exposed.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/config-architecture.md` describing the unified config system
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Create `data/README.md` documenting each file/directory and cleanup policy

### Inline Documentation
- [ ] Docstrings on all new Settings classes in `config/settings.py`
- [ ] Code comments explaining path resolution strategy in `config/paths.py`
- [ ] Updated `.env.example` with all env vars and descriptions

## Success Criteria

- [ ] `grep -rn '/Users/valorengels' --include='*.py' --include='*.sh' | grep -v '.git/' | grep -v node_modules | grep -v __pycache__ | grep -v .venv | grep -v tests/ | grep -v .claude/worktrees` returns 0 results (production code only; test fixtures use `Path.home()`)
- [ ] `grep -rn '/Users/valorengels' config/projects.json` returns 0 results (paths derived from `$HOME`)
- [ ] Only one session name default (`valor_bridge`) across the entire codebase
- [ ] `config/secrets/` exists with Google auth tokens and DM whitelist
- [ ] `~/Desktop/claude_code/` no longer required for system operation (symlinks OK during transition)
- [ ] `.env.example` contains `REDIS_URL`, `OPENROUTER_API_KEY`, `OLLAMA_URL`, `OLLAMA_VISION_MODEL`, `SEMANTIC_ROUTING`, `TELEGRAM_LINK_COLLECTORS`, `CLAUDE_CODE_TASK_LIST_ID`, `GOOGLE_CREDENTIALS_DIR`
- [ ] `config/telegram_groups.json` and `config/workspace_config.json` deleted
- [ ] Only one example config: `config/projects.example.json`
- [ ] `data/README.md` exists with cleanup policy
- [ ] Bridge starts and connects successfully after all changes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (config-core)**
  - Name: config-builder
  - Role: Expand settings.py, create paths.py, unify session names, update .env.example
  - Agent Type: builder
  - Resume: true

- **Builder (path-migration)**
  - Name: path-migrator
  - Role: Replace all hardcoded paths in Python/shell/JSON files, migrate Desktop/claude_code contents
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove vestigial configs, create data/README.md, generate launchd plist templates
  - Agent Type: builder
  - Resume: true

- **Validator (config)**
  - Name: config-validator
  - Role: Verify no hardcoded paths remain, all env vars documented, bridge starts correctly
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: config-docs
  - Role: Create config-architecture.md, update feature index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Expand config/settings.py
- **Task ID**: build-settings
- **Depends On**: none
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `RedisSettings` with `url` field (default `redis://localhost:6379/0`, env var `REDIS_URL`)
- Add `GoogleAuthSettings` with `credentials_dir` (default `config/secrets/`, env var `GOOGLE_CREDENTIALS_DIR`)
- Add `ModelSettings` with `ollama_vision_model` (default `llama3.2-vision:11b`, env var `OLLAMA_VISION_MODEL`)
- Add `PathSettings` with `project_root`, `data_dir`, `logs_dir` derived from `Path(__file__)`
- Fix `TelegramSettings.session_name` default from `ai_rebuild_session` to `valor_bridge`
- Create `config/paths.py` with `PROJECT_ROOT`, `DATA_DIR`, `LOGS_DIR`, `CONFIG_DIR` constants

### 2. Replace hardcoded paths in production code
- **Task ID**: build-paths
- **Depends On**: build-settings
- **Assigned To**: path-migrator
- **Agent Type**: builder
- **Parallel**: false
- Replace `/Users/valorengels/src` in `agent/sdk_client.py`, `agent/job_queue.py` with `Path.home() / "src"` or settings import
- Replace `~/Desktop/claude_code/` in `tools/google_workspace/auth.py`, `tools/valor_calendar.py`, `tools/telegram_users.py` with `settings.google_auth.credentials_dir`
- Replace hardcoded session names in `bridge/telegram_bridge.py`, `scripts/telegram_login.py`, `scripts/test_emoji_reactions.py`
- Update `bridge/dedup.py` to use `settings.redis.url`
- Update `bridge/media.py` to use `config.models.OLLAMA_VISION_MODEL`
- Update `config/projects.json` working_directory values to use `~` or remove hardcoded username

### 3. Migrate config files
- **Task ID**: build-migration
- **Depends On**: build-settings
- **Assigned To**: path-migrator
- **Agent Type**: builder
- **Parallel**: true
- Create `config/secrets/` directory (gitignored)
- Copy `~/Desktop/claude_code/google_credentials.json` and `google_token.json` to `config/secrets/`
- Copy `~/Desktop/claude_code/dm_whitelist.json` to `config/dm_whitelist.json`
- Copy `~/Desktop/claude_code/calendar_config.json` to `config/calendar_config.json`
- Create symlinks from old locations to new ones for backward compatibility

### 4. Clean up vestigial config
- **Task ID**: build-cleanup
- **Depends On**: build-paths
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `config/telegram_groups.json`
- Delete `config/workspace_config.json`
- Remove `load_workspace_config` from `config/loader.py` and `config/__init__.py`
- Delete `config/projects.json.example` (keep `projects.example.json` as canonical)
- Update `.env.example` with all missing env vars
- Create `data/README.md` documenting contents and cleanup policy
- Delete stale session files: `data/ai_rebuild_session.session.backup`, `data/telegram_session.session`, `data/test_session.session`

### 5. Update launchd plists
- **Task ID**: build-plists
- **Depends On**: build-settings
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Create plist template using `$HOME` variable substitution
- Generate actual plists from template with `envsubst` or sed
- Add plist generation to update script

### 6. Update test fixtures
- **Task ID**: build-tests
- **Depends On**: build-paths
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/unit/test_cross_repo_gh_resolution.py` to use `Path.home()` for working_directory
- Update `tests/unit/test_workflow_sdk_integration.py` to use derived paths
- Update `tests/unit/test_sdk_client.py` to use derived paths
- Update `tests/integration/test_job_scheduler.py` to use derived paths

### 7. Validate all changes
- **Task ID**: validate-config
- **Depends On**: build-cleanup, build-plists, build-tests
- **Assigned To**: config-validator
- **Agent Type**: validator
- **Parallel**: false
- Run hardcoded path grep — must return 0 results for production code
- Verify `.env.example` completeness
- Verify bridge starts and connects
- Run `pytest tests/ -x -q`
- Run `ruff check . && ruff format --check .`

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-config
- **Assigned To**: config-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/config-architecture.md`
- Create `data/README.md`
- Add entry to `docs/features/README.md` index table
- Update CLAUDE.md if config patterns section needs refresh

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: config-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Verify all documentation exists
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No hardcoded user paths (prod) | `grep -rn '/Users/valorengels' --include='*.py' --include='*.sh' \| grep -v '.git/\|node_modules\|__pycache__\|.venv\|tests/\|.claude/worktrees' \| wc -l` | output contains 0 |
| Session name unified | `grep -rn 'ai_rebuild_session' --include='*.py' \| grep -v '.venv\|.claude/worktrees' \| wc -l` | output contains 0 |
| Env vars documented | `grep -c 'REDIS_URL\|OPENROUTER_API_KEY\|OLLAMA_URL\|OLLAMA_VISION_MODEL' .env.example` | output > 3 |
| Vestigial configs gone | `ls config/telegram_groups.json config/workspace_config.json 2>&1 \| grep -c 'No such file'` | output contains 2 |
| Data README exists | `test -f data/README.md && echo exists` | output contains exists |

---

## Resolved Questions

1. **Session file migration**: Keep in `data/` — it's runtime state, not config.

2. **projects.json path format**: Store as relative to `$HOME/src/` (e.g., just `"ai"`, `"popoto"`) and resolve at load time via `config/paths.py`.

3. **`~/Desktop/claude_code/` symlink duration**: One release cycle (2 weeks), then remove.
