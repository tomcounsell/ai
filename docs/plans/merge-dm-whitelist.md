---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/568
last_comment_id:
---

# Merge DM Whitelist into projects.json

## Problem

DM access control is split across three sources: a separate `dm_whitelist.json` file, the `dms` section in `projects.json`, and a `TELEGRAM_DM_WHITELIST` env var fallback. The per-user permission system (`full` vs `qa_only`) is dead code since all users are `qa_only`. This scattered config adds complexity for no benefit.

**Current behavior:**

1. `~/Desktop/Valor/dm_whitelist.json` stores user IDs, names, and per-user permission levels
2. `projects.json` has a `dms` section with persona and working_directory (never actually read by the bridge)
3. `TELEGRAM_DM_WHITELIST` env var serves as fallback if the JSON file is missing
4. `DM_WHITELIST_CONFIG` dict is propagated to three modules for per-user permission lookups
5. `get_user_permissions()` exists in both `routing.py` and `context.py` but always returns `qa_only`

**Desired outcome:**

A single `dms.whitelist` array in `projects.json` with simple user entries (id, name, username). No per-user permission field. All DM users get uniform qa_only treatment. The separate file, env var, and permission-lookup code are removed.

## Prior Art

- **PR #4**: Enhanced Telegram Security and Development Workflow Integration (merged 2025-05-31) -- early security work that predates the current whitelist system
- **Issue #416**: Config consolidation -- umbrella issue for scattered config cleanup (completed)
- **Issue #447**: Move projects.json to ~/Desktop/Valor/ -- moved main config to iCloud (completed)
- **Issue #556**: Config-driven chat mode -- added DM permission levels and chat mode config (completed)
- **Issue #398**: Consolidate per-project config into projects.json (completed)

No prior attempts to merge the DM whitelist specifically. This is the natural next step after the config consolidation work.

## Data Flow

Current DM whitelist data flow (to be simplified):

1. **Entry point**: Bridge startup in `telegram_bridge.py` line 588
2. **Load**: Reads `~/Desktop/Valor/dm_whitelist.json`, parses users dict, builds `DM_WHITELIST` set and `DM_WHITELIST_CONFIG` dict
3. **Fallback**: If file missing, parses `TELEGRAM_DM_WHITELIST` env var into the same structures
4. **Propagate**: Sets module-level globals on `routing` module (lines 619-620) and `context` module (line 632)
5. **Check membership**: `routing.py:should_respond_sync()` checks `sender_id in DM_WHITELIST` (line 682)
6. **Check permissions**: `context.py:build_context_prefix()` calls `get_user_permissions(sender_id)` which looks up `DM_WHITELIST_CONFIG[sender_id].get("permissions")` (line 133-135)
7. **Output**: If `qa_only`, injects restriction prompt into agent context (line 149-154)

After this change:

1. **Entry point**: Bridge startup reads `CONFIG["dms"]["whitelist"]` from projects.json (already loaded)
2. **Build set**: Extracts user IDs into `DM_WHITELIST` set (simple membership check)
3. **Propagate**: Sets `DM_WHITELIST` on routing module only (no config dict needed)
4. **Check membership**: Same as before -- `sender_id in DM_WHITELIST`
5. **QA restriction**: Hardcoded for all DMs (no per-user lookup)

## Architectural Impact

- **Data ownership**: DM whitelist ownership moves from a standalone file to the existing projects.json config, reducing config surface area
- **Coupling**: Decreases coupling -- removes `DM_WHITELIST_CONFIG` propagation to routing and context modules
- **Interface changes**: `get_user_permissions()` removed from both routing.py and context.py; `build_context_prefix()` simplified to always apply qa_only for DMs
- **Reversibility**: Easy -- revert the code changes and restore the separate file

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Straightforward config consolidation with clear acceptance criteria. All changes are in well-understood bridge code.

## Prerequisites

No prerequisites -- this work has no external dependencies. The `projects.json` file already exists at `~/Desktop/Valor/projects.json` and is loaded by the bridge.

## Solution

### Key Elements

- **Config schema update**: Add `whitelist` array to existing `dms` section in `projects.json`
- **Bridge loader rewrite**: Replace file-based whitelist loading with config-based loading
- **Permission simplification**: Remove per-user permission lookup, hardcode qa_only for all DMs
- **Tool update**: Update `tools/telegram_users.py` to read from projects.json

### Flow

**Bridge startup** -> Read `CONFIG["dms"]["whitelist"]` -> Build `DM_WHITELIST` set -> Propagate to routing -> **DM arrives** -> Check membership in set -> Apply uniform qa_only restriction -> **Process message**

### Technical Approach

- Read whitelist from `CONFIG.get("dms", {}).get("whitelist", [])` during bridge startup
- Build `DM_WHITELIST` as `set(entry["id"] for entry in whitelist)`
- Remove `DM_WHITELIST_CONFIG` entirely -- no longer needed
- Stop propagating config dict to routing and context modules
- In `context.py:build_context_prefix()`, replace `get_user_permissions()` check with a simple `if is_dm:` check that always applies the qa_only restriction
- In `routing.py`, remove `DM_WHITELIST_CONFIG` and `get_user_permissions()`
- Update `tools/telegram_users.py` to load from `projects.json` instead of `dm_whitelist.json`
- Backward compatibility: missing `whitelist` key means empty set (no DMs allowed)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `try/except` block around whitelist loading in `telegram_bridge.py` (line 590-605) will be replaced with simpler config access that uses `.get()` with defaults -- test that malformed config degrades gracefully to empty whitelist
- [ ] No other exception handlers in scope

### Empty/Invalid Input Handling
- [ ] Test that missing `dms` key in config results in empty whitelist (no DMs allowed)
- [ ] Test that missing `whitelist` key in `dms` section results in empty whitelist
- [ ] Test that whitelist entries without `id` field are skipped gracefully

### Error State Rendering
- [ ] Not applicable -- no user-visible rendering changes. DM rejection is already silent (debug log only)

## Test Impact

- [ ] `tests/e2e/test_message_pipeline.py::TestResponseDecision::test_dm_responds_when_enabled` -- UPDATE: still manipulates `routing_mod.DM_WHITELIST` set, should work as-is since the set interface is unchanged
- [ ] `tests/unit/test_config_consolidation.py::TestNoConfigSecrets::test_no_config_dm_whitelist` -- UPDATE: keep this test (it checks `config/dm_whitelist.json` does not exist in repo, still valid)
- [ ] `tests/unit/test_config_consolidation.py::TestBridgeNoFallback::test_no_legacy_path_reference` -- UPDATE: verify assertion still passes after removing the separate file path reference

## Rabbit Holes

- Per-user working directory routing for DMs -- mentioned in the `dms` schema concept but never implemented, out of scope
- Migrating to a more sophisticated ACL system -- the simple whitelist is sufficient for current needs
- Adding username-based lookup to the whitelist check (currently ID-only) -- would add complexity without clear benefit

## Risks

### Risk 1: Bridge fails to start after config change
**Impact:** No Telegram connectivity until fixed
**Mitigation:** Use `.get()` with empty defaults at every level so missing config degrades to "no DMs allowed" rather than crashing. Test with both present and absent config.

### Risk 2: Existing DM users lose access during rollout
**Impact:** Whitelisted users temporarily unable to DM
**Mitigation:** Update `projects.json` on all machines before deploying the code change. The `/update` skill handles code deployment; config is iCloud-synced.

## Race Conditions

No race conditions identified -- whitelist loading happens once at bridge startup (synchronous, single-threaded). The resulting `DM_WHITELIST` set is read-only after initialization.

## No-Gos (Out of Scope)

- Per-user permission differentiation (all users are qa_only, no plans to change)
- Per-user working directory routing for DMs
- Username-based DM authentication (only user ID is checked)
- Changes to group chat routing or permissions

## Update System

The update skill (`scripts/remote-update.sh`) needs no code changes. However, the `projects.json` on each machine must have the `whitelist` array added to its `dms` section before (or simultaneously with) deploying the code change. Since `projects.json` is iCloud-synced across machines, updating it once propagates automatically. The backward-compatible default (missing whitelist = no DMs) ensures safe ordering if code deploys before config syncs.

## Agent Integration

No agent integration required -- this is a bridge-internal config change. The agent does not directly read the DM whitelist. The `tools/telegram_users.py` module is used by MCP tools but its public interface (`get_whitelisted_users()`, `resolve_username()`) will be preserved with the same return types, just reading from a different source.

## Documentation

- [ ] Update `CLAUDE.md` to remove reference to `dm_whitelist.json` if any exists
- [ ] Update `docs/guides/setup.md` to remove `TELEGRAM_DM_WHITELIST` env var from setup instructions (line 175)
- [ ] Update `.claude/skills/setup/SKILL.md` to remove `TELEGRAM_DM_WHITELIST` from env var table (line 98)
- [ ] Update `.env.example` to remove `TELEGRAM_DM_WHITELIST` lines (lines 58-59)
- [ ] Add inline code comments in `telegram_bridge.py` documenting the new whitelist loading

## Success Criteria

- [ ] `projects.json` `dms` section contains a `whitelist` array with all current users (id, name, username)
- [ ] Bridge loads DM whitelist from `projects.json` instead of `dm_whitelist.json`
- [ ] `dm_whitelist.json` file is deleted from `~/Desktop/Valor/`
- [ ] `TELEGRAM_DM_WHITELIST` env var fallback is removed from bridge code and `.env.example`
- [ ] `DM_WHITELIST_CONFIG` and `get_user_permissions()` are removed from routing.py and context.py
- [ ] `tools/telegram_users.py` reads whitelist from the merged config
- [ ] All DM users get uniform qa_only access (hardcoded, no per-user lookup)
- [ ] Missing `whitelist` key in config gracefully degrades to empty set
- [ ] Existing DM routing tests pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-config)**
  - Name: bridge-builder
  - Role: Implement config migration, update bridge loading code, simplify permission logic
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-config)**
  - Name: bridge-validator
  - Role: Verify whitelist loading, DM routing, backward compatibility
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update projects.json schema
- **Task ID**: build-config
- **Depends On**: none
- **Validates**: manual verification of JSON structure
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `whitelist` array to `dms` section in `~/Desktop/Valor/projects.json` with all current users from `dm_whitelist.json`
- Each entry: `{"id": <int>, "name": "<string>", "username": "<string>"}`

### 2. Rewrite bridge whitelist loading
- **Task ID**: build-bridge
- **Depends On**: build-config
- **Validates**: tests/e2e/test_message_pipeline.py, tests/unit/test_config_consolidation.py
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- In `telegram_bridge.py`: replace lines 582-611 with code that reads `CONFIG.get("dms", {}).get("whitelist", [])`
- Build `DM_WHITELIST` set from whitelist entries
- Remove `DM_WHITELIST_CONFIG` dict entirely
- Remove env var fallback (`TELEGRAM_DM_WHITELIST`)
- Update propagation to routing module: only propagate `DM_WHITELIST` set (remove `DM_WHITELIST_CONFIG`)
- Remove propagation of `DM_WHITELIST_CONFIG` to context module

### 3. Simplify routing.py
- **Task ID**: build-routing
- **Depends On**: build-bridge
- **Validates**: tests/e2e/test_message_pipeline.py
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `DM_WHITELIST_CONFIG = {}` module-level global
- Remove `get_user_permissions()` function
- Keep `DM_WHITELIST = set()` module-level global (still needed for membership checks)

### 4. Simplify context.py
- **Task ID**: build-context
- **Depends On**: build-bridge
- **Validates**: tests/unit/test_persona_loading.py
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true (with build-routing)
- Remove `DM_WHITELIST_CONFIG = {}` module-level global
- Remove `get_user_permissions()` function
- In `build_context_prefix()`: replace per-user permission check with simple `if is_dm:` that always applies qa_only restriction

### 5. Update telegram_users.py
- **Task ID**: build-tools
- **Depends On**: build-config
- **Validates**: manual verification
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true (with build-bridge)
- Change `WHITELIST_PATH` to read from `~/Desktop/Valor/projects.json`
- Update `get_whitelisted_users()` to parse `config["dms"]["whitelist"]` array
- Preserve return type: `dict[str, int]` mapping lowercase names to user IDs
- Preserve `resolve_username()` interface

### 6. Clean up env var references
- **Task ID**: build-cleanup
- **Depends On**: build-bridge
- **Validates**: grep confirms no remaining references
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `TELEGRAM_DM_WHITELIST` from `.env.example`
- Remove from `.claude/skills/setup/SKILL.md`
- Remove from `docs/guides/setup.md`

### 7. Delete dm_whitelist.json
- **Task ID**: build-delete
- **Depends On**: build-bridge, build-tools
- **Validates**: file does not exist
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `~/Desktop/Valor/dm_whitelist.json` after confirming all code reads from projects.json

### 8. Validation
- **Task ID**: validate-all
- **Depends On**: build-routing, build-context, build-tools, build-cleanup, build-delete
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/e2e/test_message_pipeline.py tests/unit/test_config_consolidation.py tests/unit/test_persona_loading.py -x -q`
- Verify no remaining references to `dm_whitelist.json` in Python files
- Verify no remaining references to `TELEGRAM_DM_WHITELIST` in codebase
- Verify no remaining references to `DM_WHITELIST_CONFIG` in codebase
- Verify no remaining references to `get_user_permissions` in codebase

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: bridge-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update docs per Documentation section above

### 10. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dm_whitelist.json refs | `grep -rn "dm_whitelist.json" bridge/ tools/ --include="*.py"` | exit code 1 |
| No DM_WHITELIST_CONFIG refs | `grep -rn "DM_WHITELIST_CONFIG" bridge/ tools/ --include="*.py"` | exit code 1 |
| No get_user_permissions refs | `grep -rn "get_user_permissions" bridge/ --include="*.py"` | exit code 1 |
| No env var refs | `grep -rn "TELEGRAM_DM_WHITELIST" bridge/ --include="*.py"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue is well-scoped with clear acceptance criteria and all recon items have been validated.
