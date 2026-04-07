# Plan: Rename SessionType.CHAT to PM and Add TEAMMATE

**Issue:** [#648](https://github.com/tomcounsell/ai/issues/648)
**Slug:** `session-type-pm-rename`
**Branch:** `session/session-type-pm-rename`

## Summary

Expand `SessionType` from two values (`CHAT`, `DEV`) to three (`PM`, `TEAMMATE`, `DEV`). The current `CHAT = "chat"` naming is a holdover that contradicts the PM persona it represents. Teammate sessions currently piggyback on `SessionType.CHAT` with `session_mode` as a secondary discriminator -- this plan promotes TEAMMATE to a first-class enum value.

This requires a Redis key migration (KeyField values are embedded in hash keys), a code-wide rename of all references, and updates to bridge routing, factory methods, dashboard, and tests.

## Prior Art

- **#599**: Eliminated `ChatMode`, standardized on `PersonaType`. Left `SessionType.CHAT` untouched.
- **#634**: Renamed `parent_chat_session_id` to `parent_session_id`. Proved the KeyField migration pattern (SCAN + RENAME + rebuild_indexes).
- **#562**: Created `SessionType` and `PersonaType` StrEnums.
- **#473**: Established the field deprecation pattern with backward-compat aliases.
- **Migration scripts**: `scripts/migrate_parent_session_field.py` (hash field rename), `scripts/migrate_persona_values.py` (session_mode value migration).

## Tasks

### Task 1: Expand SessionType Enum

**File:** `config/enums.py`

- Change `SessionType` from `{CHAT="chat", DEV="dev"}` to `{PM="pm", TEAMMATE="teammate", DEV="dev"}`
- Remove `CHAT` member entirely (no deprecation alias -- clean break, migration script handles existing data)
- Update the class docstring

### Task 2: Update AgentSession Model

**File:** `models/agent_session.py`

- Replace `SESSION_TYPE_CHAT = SessionType.CHAT` alias with `SESSION_TYPE_PM = SessionType.PM` (or remove entirely if no external consumers)
- Rename factory method `create_chat()` to `create_pm()` with same signature
- Add factory method `create_teammate()` -- mirrors `create_pm()` but sets `session_type=SessionType.TEAMMATE`
- Rename property `is_chat` to `is_pm`
- Add property `is_teammate` returning `self.session_type == SessionType.TEAMMATE`
- Update all docstrings and comments referencing "chat" session type
- Update `create_child()`, `create_dev()`, `create_local()` if they reference CHAT internally

### Task 3: Redis Key Migration Script

**File:** `scripts/migrate_session_type_chat_to_pm.py`

This is the critical migration. `session_type` is a Popoto `KeyField`, meaning its value is embedded in the Redis hash key string (e.g., `AgentSession:{id}:chat:{project_key}:...`). Changing from "chat" to "pm" or "teammate" requires key RENAME operations.

Steps:
1. SCAN all `AgentSession:*` keys, skip index keys (`_sorted_set:`, `_field_index:`)
2. For each key containing `:chat:` segment:
   a. Read the `session_mode` hash field
   b. If `session_mode == "teammate"` --> RENAME key replacing `:chat:` with `:teammate:`
   c. Otherwise --> RENAME key replacing `:chat:` with `:pm:`
   d. Update the `session_type` hash field value accordingly
3. Call `AgentSession.rebuild_indexes()` after all renames
4. Support `--dry-run` flag (log what would happen without changes)
5. Make idempotent (skip keys that already have `:pm:` or `:teammate:`)

Follow the pattern from `scripts/migrate_parent_session_field.py` but with key RENAME instead of field rename.

### Task 4: Update Bridge Routing

**Files:** `bridge/telegram_bridge.py`, `bridge/routing.py`, `agent/sdk_client.py`

- `bridge/telegram_bridge.py:1474`: Change `_session_type = SessionType.CHAT` to use the correct session type based on resolved persona. When `resolve_persona()` returns `PersonaType.TEAMMATE`, set `_session_type = SessionType.TEAMMATE`; otherwise `SessionType.PM`.
- `agent/sdk_client.py`: Update all `SessionType.CHAT` references (lines 925, 930, 1508, 1659, 1681, 1735) to `SessionType.PM`. The teammate routing at line 1508 should check both `SessionType.PM` and `SessionType.TEAMMATE` where appropriate.
- `agent/sdk_client.py:_resolve_persona()`: This function currently sets `session_mode=PersonaType.TEAMMATE` as a secondary flag on CHAT sessions. With TEAMMATE as a first-class type, the bridge should create TEAMMATE sessions directly instead of setting a secondary flag.
- `bridge/summarizer.py`: Update teammate checks that rely on `session_mode` to also check `SessionType.TEAMMATE`.

### Task 5: Update Agent Session Queue

**Files:** `agent/agent_session_queue.py`, `tools/agent_session_scheduler.py`

- `agent/agent_session_queue.py:195,1484`: Change default `session_type` parameter from `SessionType.CHAT` to `SessionType.PM`
- `agent/agent_session_queue.py:2027,2325`: Update teammate detection to check `session.session_type == SessionType.TEAMMATE` instead of (or in addition to) `session_mode == PersonaType.TEAMMATE`
- `tools/agent_session_scheduler.py:278`: Update default session type
- `tools/agent_session_scheduler.py:1040`: Update choices list to include all three values

### Task 6: Update Dashboard

**File:** `ui/data/sdlc.py`

- `_resolve_persona_display()` (line 320): Update the fallback mapping from `session_type`. Change `"chat" -> "Project Manager"` to `"pm" -> "Project Manager"`, add `"teammate" -> "Teammate"`.
- The `session_mode`-first priority logic can remain as a safety net for in-flight sessions during migration.

### Task 7: Update Pre-Tool-Use Hook

**File:** `agent/hooks/pre_tool_use.py`

- Line 57: Change `SessionType.CHAT` reference to `SessionType.PM` (and potentially add `SessionType.TEAMMATE` if teammate sessions need the same check)

### Task 8: Update Documentation

**Files:** `docs/features/standardized-enums.md`, `docs/features/pm-dev-session-architecture.md`

- Replace all references to `SessionType.CHAT`, `"chat"`, `session_type="chat"`
- Update enum tables, code examples, architecture descriptions
- Update `CLAUDE.md` session type references if any

### Task 9: Update Tests

**Test files requiring changes:**

| File | Changes |
|------|---------|
| `tests/unit/test_enums.py` | Rewrite `TestSessionType` for PM/TEAMMATE/DEV. Update backward-compat test. Update iteration count from 2 to 3. |
| `tests/unit/test_chat_session_factory.py` | Rename to `test_pm_session_factory.py`. Change all `create_chat` to `create_pm`, `SessionType.CHAT` to `SessionType.PM`. |
| `tests/unit/test_pm_session_permissions.py` | Update string pattern matching for `SessionType.PM` |
| `tests/unit/test_steer_child.py` | Change `is_chat = True` to `is_pm = True` |
| `tests/integration/test_agent_session_queue_session_type.py` | Update all `is_chat` to `is_pm`, `create_chat` to `create_pm`, add TEAMMATE tests |
| `tests/integration/test_bridge_routing.py` | Update `is_chat` assertions to `is_pm`, update default session type test |
| `tests/e2e/test_error_boundaries.py` | Change all `create_chat` to `create_pm` |
| `tests/e2e/test_context_propagation.py` | Change all `create_chat` to `create_pm`, `is_chat` to `is_pm` |
| `tests/e2e/test_session_lifecycle.py` | Change all `create_chat` to `create_pm`, `is_chat` to `is_pm` |
| `tests/e2e/test_queue_isolation.py` | Change all `create_chat` to `create_pm` |
| `tests/e2e/test_nudge_loop.py` | Change all `create_chat` to `create_pm` |
| `tests/e2e/test_session_spawning.py` | Change all `create_chat` to `create_pm` |

## No-Gos

- **Do NOT remove `session_mode` field.** Even with TEAMMATE as a first-class SessionType, `session_mode` may still carry value for finer-grained tracking. Leave for a future cleanup.
- **Do NOT add backward-compat alias for `SessionType.CHAT`.** Clean break. The migration script handles data; code references get updated atomically.
- **Do NOT live-migrate.** The bridge must be stopped before running the migration script.
- **Do NOT change the `role` field.** Role is orthogonal to session type and was just added in #634. Leave it alone.
- **Do NOT modify `PersonaType` enum.** It already has the correct values (DEVELOPER, PROJECT_MANAGER, TEAMMATE).

## Update System

No update system changes required. The migration script is a one-time operation run manually on each machine. The `scripts/remote-update.sh` and update skill pull code changes automatically; the migration script should be called out in the PR description as a post-deploy step but does not need to be wired into the automated update flow.

Post-deploy step for each machine:
1. Stop bridge: `./scripts/valor-service.sh stop`
2. Pull changes (handled by update skill)
3. Run migration: `python scripts/migrate_session_type_chat_to_pm.py --dry-run` then without `--dry-run`
4. Restart bridge: `./scripts/valor-service.sh restart`

## Agent Integration

No agent integration required. This is a model/enum rename with a migration script. No new MCP servers, no new tools, no changes to `.mcp.json` or `mcp_servers/`. The agent interacts with `AgentSession` through existing factory methods -- those methods are being renamed, not added. The bridge code changes are internal routing updates.

## Failure Path Test Strategy

1. **Migration script on empty DB**: Run with `--dry-run` on a database with no `AgentSession:*` keys -- should report zero changes gracefully.
2. **Migration idempotency**: Run the migration twice -- second run should report zero changes.
3. **Mixed keys**: Create test keys with both `:chat:` and `:pm:` segments -- migration should only touch `:chat:` keys.
4. **Teammate detection**: Create a key with `session_mode=teammate` -- migration should rename to `:teammate:` not `:pm:`.
5. **Missing session_mode**: Keys without `session_mode` field should default to `:pm:` (PM is the original CHAT behavior).
6. **Index rebuild verification**: After migration, `AgentSession.query.filter(session_type="pm")` should return the migrated records.

## Test Impact

- [x] `tests/unit/test_enums.py::TestSessionType` -- REPLACE: Rewrite all assertions for PM/TEAMMATE/DEV enum values, update member count to 3
- [x] `tests/unit/test_enums.py::TestEnvVarCompatibility::test_session_type_in_env_var_comparison` -- UPDATE: Change "chat" to "pm"
- [x] `tests/unit/test_enums.py::TestEnvVarCompatibility::test_str_enum_in_dict_key` -- UPDATE: Change dict key from "chat" to "pm"
- [x] `tests/unit/test_chat_session_factory.py` -- REPLACE: Rename file to `test_pm_session_factory.py`, update all references
- [x] `tests/unit/test_pm_session_permissions.py::test_sdk_pm_persona_config` -- UPDATE: Change string match pattern
- [x] `tests/unit/test_steer_child.py` -- UPDATE: Change `is_chat` to `is_pm`
- [x] `tests/integration/test_agent_session_queue_session_type.py` -- REPLACE: All `is_chat`/`create_chat` references, add TEAMMATE coverage
- [x] `tests/integration/test_bridge_routing.py` -- UPDATE: All `is_chat` assertions to `is_pm`
- [x] `tests/e2e/test_error_boundaries.py` -- UPDATE: All `create_chat` calls to `create_pm`
- [x] `tests/e2e/test_context_propagation.py` -- UPDATE: All `create_chat`/`is_chat` to `create_pm`/`is_pm`
- [x] `tests/e2e/test_session_lifecycle.py` -- UPDATE: All `create_chat`/`is_chat` to `create_pm`/`is_pm`
- [x] `tests/e2e/test_queue_isolation.py` -- UPDATE: All `create_chat` to `create_pm`
- [x] `tests/e2e/test_nudge_loop.py` -- UPDATE: All `create_chat` to `create_pm`
- [x] `tests/e2e/test_session_spawning.py` -- UPDATE: All `create_chat` to `create_pm`

## Rabbit Holes

- **Deprecation grace period.** The temptation to add a `CHAT = "chat"` alias "just in case" adds complexity for no gain. All code is in this repo; we can update atomically.
- **Migrating `session_mode` away.** With TEAMMATE as a first-class type, it is tempting to remove `session_mode` entirely. Resist -- it is used by the dashboard and summarizer for display, and removing it is a separate concern.
- **Renaming `create_dev()` to `create_developer()`.** Out of scope. The DEV naming is consistent and unambiguous.

## Documentation

- [x] Update `docs/features/standardized-enums.md` -- replace all SessionType.CHAT references, update enum table, update code examples
- [x] Update `docs/features/pm-dev-session-architecture.md` -- replace "PM session (session_type=SessionType.CHAT)" with PM terminology, add TEAMMATE session description
- [x] Update `CLAUDE.md` system architecture section if it references session_type="chat"

## Success Criteria

- [x] `SessionType` enum has exactly three members: `PM = "pm"`, `TEAMMATE = "teammate"`, `DEV = "dev"`
- [x] Zero occurrences of `SessionType.CHAT`, `SESSION_TYPE_CHAT`, or `== "chat"` in non-migration Python files
- [x] Redis migration script at `scripts/migrate_session_type_chat_to_pm.py` passes `--dry-run` cleanly
- [x] `AgentSession.create_pm()` and `AgentSession.create_teammate()` factory methods exist
- [x] `AgentSession.is_pm` and `AgentSession.is_teammate` properties exist
- [x] Bridge creates `SessionType.TEAMMATE` sessions directly for teammate-persona routing (no `session_mode` secondary discriminator needed)
- [x] `pytest tests/unit/test_enums.py` validates new enum values including TEAMMATE
- [x] All existing tests pass: `pytest tests/unit/ tests/integration/ tests/e2e/` green
- [x] Documentation updated: `docs/features/standardized-enums.md`, `docs/features/pm-dev-session-architecture.md`

## Execution Order

1. Task 1 (enum) + Task 2 (model) -- foundational changes
2. Task 4 (bridge) + Task 5 (queue) + Task 6 (dashboard) + Task 7 (hook) -- consumers of the enum
3. Task 9 (tests) -- update all test files
4. Task 8 (docs) -- update documentation
5. Task 3 (migration script) -- can be built at any point but run last
