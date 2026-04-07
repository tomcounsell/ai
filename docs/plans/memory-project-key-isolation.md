---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/811
last_comment_id:
---

# Memory project_key Isolation

## Problem

All 2,376 Memory records in Redis have `project_key="dm"` instead of the correct project key (`"valor"` for the `~/src/ai` repo). Memory recall is completely broken for Claude Code sessions — queries look up the `"valor"` partition and find nothing, while all accumulated knowledge sits unreachable in the `"dm"` partition.

**Current behavior:**
- `memory_bridge._get_project_key()` falls through to `DEFAULT_PROJECT_KEY = "dm"` because: (1) `~/Desktop/Valor/projects.json` was missing on this machine, and (2) `recall()`, `ingest()`, and `extract()` call `_get_project_key()` with no `cwd` argument, so the cwd-match branch is skipped entirely.
- Every memory created by Claude Code hooks since launch (2026-03-24) has `project_key="dm"`.
- `python -m tools.memory_search inspect --stats` shows all records under `"dm"` partition.
- Memory thoughts are never injected during PostToolUse — `retrieve_memories(project_key="valor")` returns empty.

**Desired outcome:**
- `DEFAULT_PROJECT_KEY` is not `"dm"` (that value is semantically reserved for Telegram DMs).
- Hook entry points pass `cwd` from the hook input payload to `_get_project_key(cwd)`.
- All 2,376 existing `"dm"` memories from agent/hook sources are migrated to `"valor"`.
- Telegram DM memories remain as `"dm"`.
- Memory recall works in Claude Code sessions.

## Prior Art

- **Issue #514**: Subconscious Memory initial implementation — greenfield, no prior project_key handling.
- **Issue #518**: Memory search tool — added `tools/memory_search.py` inspect/stats, no project_key fix.

No prior attempts to fix this specific isolation bug found.

## Data Flow

The bug exists in two code paths that both call `_get_project_key()` with no `cwd`:

**Recall path (PostToolUse hook):**
1. `post_tool_use.py` calls `recall(session_id, tool_name, tool_input)` — no `cwd` passed
2. `memory_bridge.recall()` calls `_get_project_key()` — no `cwd` argument
3. `_get_project_key()` falls through to `DEFAULT_PROJECT_KEY = "dm"`
4. `retrieve_memories(project_key="dm")` returns records, but the Claude Code session accumulates memories under `"dm"` rather than `"valor"`

**Ingest path (UserPromptSubmit hook):**
1. `user_prompt_submit.py` calls `ingest(prompt)` — no `cwd` passed
2. `memory_bridge.ingest()` calls `_get_project_key()` — no `cwd` argument
3. Falls through to `DEFAULT_PROJECT_KEY = "dm"`
4. Memory saved with `project_key="dm"`

**Note:** `user_prompt_submit.py` already reads `cwd = hook_input.get("cwd", "")` and passes it to `_get_project_key(cwd)` when creating `AgentSession`. That pattern just needs to be extended to the `ingest()` call.

**Extract path (Stop hook):**
1. `stop.py` calls `extract(session_id, transcript_path)` — no `cwd`
2. `memory_bridge.extract()` calls `extract_observations_async(session_id, text)` with no `project_key`
3. `agent/memory_extraction.py:134` falls through to `DEFAULT_PROJECT_KEY` from env or config

## Architectural Impact

- **Interface changes**: `ingest(content)` gains optional `cwd: str | None = None` parameter; `extract(session_id, transcript_path)` gains optional `cwd: str | None = None` parameter; `recall(session_id, tool_name, tool_input)` gains optional `cwd: str | None = None` parameter.
- **New dependencies**: None.
- **Coupling**: No new coupling — the cwd is available in hook_input and just needs to be threaded through.
- **Data ownership**: No change — Memory model owns data, project_key partitioning logic unchanged.
- **Reversibility**: Fully reversible — migration script has dry-run mode; DEFAULT_PROJECT_KEY change has no cascading effects.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import popoto; popoto.redis_db.get_REDIS_DB().ping()"` | Memory model access |
| `~/Desktop/Valor/projects.json` exists | `test -f ~/Desktop/Valor/projects.json && echo ok` | cwd-match resolution |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-project-key-isolation.md`

## Solution

### Key Elements

- **Fix 1 — DEFAULT_PROJECT_KEY**: Change `"dm"` → `"default"` in `config/memory_defaults.py:42`. The value `"dm"` is semantically wrong as a fallback; `"default"` makes silent fallbacks visible and distinguishable.
- **Fix 2 — Thread cwd through hook entry points**: `recall()`, `ingest()`, and `extract()` each gain an optional `cwd` parameter. The caller (hook scripts) reads `cwd` from `hook_input` and passes it through. `_get_project_key(cwd)` already handles the resolution correctly once given a cwd.
- **Fix 3 — Migration script**: `scripts/migrate_memory_project_key.py` scans all `Memory:*` Redis keys where `project_key="dm"`, classifies each as agent-sourced (migrate to `"valor"`) or Telegram DM-sourced (keep as `"dm"`), renames keys via `Redis RENAME`, updates field values, rebuilds Popoto indexes. Dry-run flag required.

### Flow

`post_tool_use.py reads cwd from hook_input` → `passes cwd to recall(session_id, tool_name, tool_input, cwd)` → `_get_project_key(cwd) resolves "valor"` → `retrieve_memories(project_key="valor")` returns correct memories → thoughts injected

`user_prompt_submit.py reads cwd from hook_input` → `passes cwd to ingest(prompt, cwd)` → `Memory saved with project_key="valor"`

`stop.py reads cwd from hook_input` → `passes cwd to extract(session_id, transcript_path, cwd)` → `extract_observations_async(session_id, text, project_key="valor")` → observations saved under `"valor"`

`migrate script runs` → scans `Memory:*:dm:*` keys → classifies by source field → renames agent-sourced keys to `"valor"` → rebuilds indexes → before/after stats printed

### Technical Approach

- Add `cwd: str | None = None` to `recall()`, `ingest()`, `extract()` in `memory_bridge.py`
- Inside each function, replace the no-arg `_get_project_key()` call with `_get_project_key(cwd)`
- In `post_tool_use.py::_run_memory_recall()`, extract `cwd = hook_input.get("cwd", "")` and pass to `recall()`
- In `user_prompt_submit.py::main()`, pass the already-read `cwd` to `ingest(prompt, cwd)`
- In `stop.py::_run_memory_extraction()`, extract `cwd` from `hook_input` and pass to `extract()`
- Change `DEFAULT_PROJECT_KEY = "dm"` → `DEFAULT_PROJECT_KEY = "default"` in `config/memory_defaults.py`
- Migration script classifies records: `source == SOURCE_AGENT or source == SOURCE_SYSTEM` → migrate; `source == SOURCE_HUMAN and agent_id` looks like a Telegram user (not a project key) → keep as `"dm"`; rely on the `source` field as the primary discriminator since Claude Code hooks save with `SOURCE_HUMAN` for prompts and `SOURCE_AGENT` for observations — but all are currently mislabeled `"dm"`.

**Migration classification strategy**: Since ALL current `"dm"` records came from Claude Code hooks (no Telegram bridge on this machine), they can all safely be migrated to `"valor"`. The migration script will scan for `source` field and treat all records as needing migration on this machine, with a note in the script that future cross-machine runs should filter by `agent_id` / `source` patterns.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `memory_bridge.recall()`, `ingest()`, `extract()` all wrap in `except Exception` and return None/False — existing tests cover these; new tests add `cwd` parameter variants
- [ ] Migration script: Redis connection failure is caught and logged; RENAME failure per-record is caught and counted in `stats["errors"]`

### Empty/Invalid Input Handling
- [ ] `_get_project_key(cwd=None)` already handles None cwd → falls to DEFAULT_PROJECT_KEY — still valid after the change
- [ ] `_get_project_key(cwd="")` should be treated same as None — verify empty string doesn't cause `Path("").name` to return an empty string that becomes the project key

### Error State Rendering
- [ ] Migration script prints before/after counts and per-record RENAME log lines — human can verify
- [ ] `python -m tools.memory_search inspect --stats` shows partition counts after migration

## Test Impact

- [ ] `tests/unit/test_memory_bridge.py::TestIngest::test_ingest_success` — UPDATE: pass `cwd` parameter to `ingest()`, assert `_get_project_key` called with correct cwd
- [ ] `tests/unit/test_memory_bridge.py::TestIngest::test_ingest_returns_false_on_exception` — UPDATE: pass `cwd` to match new signature
- [ ] `tests/unit/test_memory_bridge.py::TestRecall::test_recall_returns_none_before_window` — UPDATE: pass `cwd` to match new signature
- [ ] `tests/unit/test_memory_bridge.py::TestRecall::test_recall_novel_territory_signal` — UPDATE: pass `cwd` to match new signature
- [ ] `tests/unit/test_memory_bridge.py::TestRecallCategoryReranking::test_recall_calls_apply_category_weights` — UPDATE: already mocks `_get_project_key`, verify cwd threading
- [ ] New test: `tests/unit/test_memory_bridge.py::TestGetProjectKey::test_get_project_key_uses_cwd` — ADD: verify that cwd is passed through from caller to `_get_project_key`
- [ ] New test: `tests/unit/test_memory_bridge.py::TestGetProjectKey::test_default_project_key_is_not_dm` — ADD: assert `DEFAULT_PROJECT_KEY != "dm"`
- [ ] New test: `tests/unit/test_memory_bridge.py::TestGetProjectKey::test_empty_cwd_treated_as_none` — ADD: `_get_project_key("")` should not return `""`
- [ ] New test: `tests/unit/test_memory_bridge.py::TestExtract::test_extract_passes_cwd_to_project_key` — ADD: verify cwd threading
- [ ] New script test: `tests/unit/test_migrate_memory_project_key.py` — ADD: dry-run mode with mock Redis, verify rename logic, verify Telegram DM records preserved

## Rabbit Holes

- **Retroactively identifying Telegram DM records**: On this machine there are no Telegram DM memories (no bridge running). Attempting to build complex source-attribution logic wastes time — migrate all records to `"valor"` and move on.
- **Fixing `agent/memory_extraction.py` DEFAULT_PROJECT_KEY fallback**: `memory_extraction.py:134` also falls through to `DEFAULT_PROJECT_KEY`. On the SDK path this is covered by `VALOR_PROJECT_KEY` env var. Don't refactor the extraction module — it's a separate concern.
- **Fixing `projects.json` propagation via the update system**: The update system already handles `projects.json` via `scripts/remote-update.sh`. Don't embed machine-specific config repair into this plan.

## Risks

### Risk 1: Migration corrupts Popoto indexes
**Impact:** Memory records unreachable after migration; queries return nothing.
**Mitigation:** Migration script calls `Memory.rebuild_indexes()` after all RENAME operations, following the established pattern from `scripts/migrate_session_type_chat_to_pm.py`. Dry-run flag lets operator verify before committing.

### Risk 2: `Path("").name` returns empty string as project_key
**Impact:** Records saved with `project_key=""` instead of falling through to default.
**Mitigation:** `_get_project_key()` should guard: `if cwd and cwd.strip()` before the `Path(cwd).name` fallback. Add a unit test.

### Risk 3: `DEFAULT_PROJECT_KEY = "default"` breaks existing code that hard-codes `"dm"` as expected default
**Impact:** Any code checking `project_key == "dm"` as a sentinel would break.
**Mitigation:** Search codebase for `"dm"` string literals in non-DM contexts before changing. Known: `agent/memory_extraction.py` imports `DEFAULT_PROJECT_KEY` from config — safe since we're changing the source. No other hard-coded `"dm"` checks exist.

## Race Conditions

No race conditions identified — all hook operations are synchronous and single-process within a Claude Code session. Migration script is a one-shot batch operation run with the bridge/worker stopped.

## No-Gos (Out of Scope)

- Fixing the `VALOR_PROJECT_KEY` env var propagation to hook subprocesses (separate concern; SDK path already works).
- Adding project_key to the `extract_observations_async` call chain in `agent/memory_extraction.py` beyond what's needed for the hook path.
- Retroactive detection or classification of Telegram DM records — on this machine there are none.
- Fixing `projects.json` distribution via the update system (already handled).

## Update System

The migration script (`scripts/migrate_memory_project_key.py`) must be run once on any machine that had memories accumulate under `"dm"` due to the missing `projects.json`. This is a one-time per-machine operation.

The update script (`scripts/remote-update.sh`) does not need changes. The code fixes (DEFAULT_PROJECT_KEY, cwd threading) are deployed automatically via `git pull`.

**Migration step for existing machines**: After pulling the fix, run:
```bash
python scripts/migrate_memory_project_key.py --dry-run  # verify counts
python scripts/migrate_memory_project_key.py            # apply migration
python -m tools.memory_search inspect --stats           # verify "valor" partition
```

## Agent Integration

No MCP server changes required. The memory recall system operates via Claude Code hooks (`.claude/hooks/`), not via MCP tools. The fix is entirely within:
- `config/memory_defaults.py` — constant change
- `.claude/hooks/hook_utils/memory_bridge.py` — parameter threading
- `.claude/hooks/post_tool_use.py` — cwd extraction and pass-through
- `.claude/hooks/user_prompt_submit.py` — cwd pass-through to ingest
- `.claude/hooks/stop.py` — cwd extraction and pass-through to extract
- `scripts/migrate_memory_project_key.py` — new migration script

No `.mcp.json` changes, no MCP server changes, no bridge changes.

## Documentation

- [ ] Update `docs/features/claude-code-memory.md` to document the `_get_project_key()` resolution chain and the `cwd` threading fix
- [ ] Add a note to `docs/features/subconscious-memory.md` explaining `project_key` partitioning and the `DEFAULT_PROJECT_KEY` fallback value (`"default"`)
- [ ] Add migration note to `docs/features/claude-code-memory.md`: migration script for machines that accumulated `"dm"` memories

## Success Criteria

- [ ] `DEFAULT_PROJECT_KEY` no longer evaluates to `"dm"` for non-DM contexts (`config/memory_defaults.py:42` reads `"default"`)
- [ ] New memories created in `~/src/ai` have `project_key="valor"` (verified via `python -m tools.memory_search inspect --stats`)
- [ ] Migration script runs cleanly: `python scripts/migrate_memory_project_key.py --dry-run` shows 2000+ records to migrate; live run completes with 0 errors
- [ ] `python -m tools.memory_search inspect --stats` shows records under `"valor"` partition after migration
- [ ] Memory recall works in Claude Code sessions (PostToolUse thoughts injected — verifiable by running a few tool calls and seeing `<thought>` blocks in session context)
- [ ] Telegram DM memories remain under `"dm"` (N/A on this machine; migration script preserves `source=SOURCE_HUMAN` records with DM-pattern `agent_id`)
- [ ] All new and updated tests pass (`pytest tests/unit/test_memory_bridge.py tests/unit/test_migrate_memory_project_key.py -v`)
- [ ] `_get_project_key("")` returns `"default"` not `""` (empty cwd guard)

## Team Orchestration

### Team Members

- **Builder (memory-fix)**
  - Name: memory-fix-builder
  - Role: Implement the three code fixes (DEFAULT_PROJECT_KEY, cwd threading, migration script) and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (memory-fix)**
  - Name: memory-fix-validator
  - Role: Verify fixes, run migration dry-run, confirm stats show "valor" partition
  - Agent Type: validator
  - Resume: true

- **Documentarian (memory-fix)**
  - Name: memory-fix-documentarian
  - Role: Update docs/features/claude-code-memory.md and docs/features/subconscious-memory.md
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Fix DEFAULT_PROJECT_KEY and thread cwd through hook entry points
- **Task ID**: build-memory-fix
- **Depends On**: none
- **Validates**: tests/unit/test_memory_bridge.py, tests/unit/test_hook_user_prompt_submit.py, tests/unit/test_stop_hook.py
- **Assigned To**: memory-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `DEFAULT_PROJECT_KEY = "dm"` → `DEFAULT_PROJECT_KEY = "default"` in `config/memory_defaults.py:42`
- Add `cwd: str | None = None` parameter to `recall()`, `ingest()`, `extract()` in `.claude/hooks/hook_utils/memory_bridge.py`; pass `cwd` to `_get_project_key(cwd)` inside each function
- Guard against empty string cwd: in `_get_project_key()`, treat `cwd = cwd.strip() if cwd else None` before use
- In `post_tool_use.py::_run_memory_recall()`, extract `cwd = hook_input.get("cwd", "")` and pass to `recall(..., cwd=cwd)`
- In `user_prompt_submit.py::main()`, pass already-read `cwd` to `ingest(prompt, cwd=cwd)`
- In `stop.py::_run_memory_extraction()`, extract `cwd = hook_input.get("cwd", "")` and pass to `extract(..., cwd=cwd)`
- Update existing tests and add new tests per Test Impact section

### 2. Write migration script
- **Task ID**: build-migration
- **Depends On**: build-memory-fix
- **Validates**: tests/unit/test_migrate_memory_project_key.py (create)
- **Assigned To**: memory-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/migrate_memory_project_key.py` following the pattern in `scripts/migrate_session_type_chat_to_pm.py`
- SCAN all `Memory:*` keys (skip index keys)
- For each key containing `:dm:` segment: RENAME to replace `:dm:` with `:valor:`; update `project_key` hash field
- Call `Memory.rebuild_indexes()` after all renames
- Support `--dry-run` flag; print before/after counts
- Idempotent: skip keys already having `:valor:`
- Write unit test with mocked Redis verifying rename logic and dry-run mode

### 3. Validate and run migration
- **Task ID**: validate-migration
- **Depends On**: build-migration
- **Assigned To**: memory-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/migrate_memory_project_key.py --dry-run` and verify expected counts
- Run `python scripts/migrate_memory_project_key.py` (live)
- Run `python -m tools.memory_search inspect --stats` and confirm `"valor"` partition has records
- Run `pytest tests/unit/test_memory_bridge.py tests/unit/test_migrate_memory_project_key.py -v` and confirm all pass

### 4. Documentation
- **Task ID**: document-memory-fix
- **Depends On**: validate-migration
- **Assigned To**: memory-fix-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/claude-code-memory.md` with cwd threading explanation and migration note
- Update `docs/features/subconscious-memory.md` with project_key partitioning note and DEFAULT_PROJECT_KEY change

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-memory-fix
- **Assigned To**: memory-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify `python -m tools.memory_search inspect --stats` shows `"valor"` partition
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_bridge.py tests/unit/test_migrate_memory_project_key.py -v` | exit code 0 |
| DEFAULT_PROJECT_KEY not dm | `python -c "from config.memory_defaults import DEFAULT_PROJECT_KEY; assert DEFAULT_PROJECT_KEY != 'dm', DEFAULT_PROJECT_KEY"` | exit code 0 |
| valor partition has records | `python -m tools.memory_search inspect --stats 2>&1 \| grep -i valor` | output contains valor |
| Empty cwd guard | `python -c "from .claude.hooks.hook_utils.memory_bridge import _get_project_key; r = _get_project_key(''); assert r != ''"` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — root cause is fully understood, scope is narrow, all decisions are straightforward.
