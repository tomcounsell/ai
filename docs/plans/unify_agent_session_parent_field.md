---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/757
last_comment_id:
---

# Unify AgentSession Parent Field (parent_session_id -> parent_agent_session_id)

## Problem

`AgentSession` has two independent KeyFields for the parent link: `parent_session_id`
(line 206 of `models/agent_session.py`) and `parent_agent_session_id` (line 213). They
overlap in purpose but are never synchronized, producing a split-brain hierarchy.

**Current behavior:**
- `create_child()` / `create_dev()` set only `parent_session_id`.
- `enqueue_session()`, scheduler CLI, and `valor_session` CLI set only
  `parent_agent_session_id`.
- `scheduling_depth`, `get_parent()`, `get_children()`, the zombie health check
  (`agent/agent_session_queue.py:1195+`), `session_lifecycle.py`, and the dashboard UI
  (`ui/sdlc.py`, `ui/app.py`) all read `parent_agent_session_id` exclusively. Bridge-
  spawned Dev sessions therefore appear as orphaned roots to every hierarchy walker.
- Conversely `get_parent_session()` / `get_child_sessions()` read `parent_session_id`, so
  worker-enqueued children are invisible to bridge-side callers.

**Desired outcome:**
A single canonical field (`parent_agent_session_id`) set by all creation paths and read
by all hierarchy walkers. `parent_session_id` remains as a deprecated property alias for
one release cycle, mirroring the existing `parent_chat_session_id` alias pattern
(`models/agent_session.py:397-405`).

## Prior Art

- **#631** — Renamed `parent_job_id` -> `parent_agent_session_id` and
  `parent_chat_session_id` -> `parent_session_id`. Closed 2026-04-02. Did not
  consolidate; it is the source of the current split-brain.
- **#634** — Added `role` field and generalized parent-child naming. Closed 2026-04-03.
  Preserved both parent fields as-is.
- The `parent_chat_session_id` alias pattern introduced in #631 is the template we will
  copy: property getter/setter + `_normalize_kwargs` mapping + `create_dev()` kwarg
  handling (see lines 306-314, 397-405, 965 of `models/agent_session.py`).
- `scripts/migrate_parent_session_field.py` exists and is the template for the data
  migration.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #631 | Renamed fields but kept both KeyFields | Did not consolidate — created the current split-brain because call sites were not migrated to a single field |
| #634 | Added `role`, generalized naming | Orthogonal concern (role specialization); left parent-field duplication untouched |

**Root cause pattern:** prior passes treated the two fields as semantically distinct
(`parent_session_id` = "logical FK", `parent_agent_session_id` = "hierarchy chain") but
in practice every caller wants the same thing: "who spawned me?" The semantic split was
never load-bearing.

## Data Flow

1. **Bridge spawns Dev session**: `pre_tool_use.py:175` -> `AgentSession.create_child()`
   -> sets `parent_session_id` only -> saved to Redis.
2. **Scheduler reads hierarchy**: `agent_session_queue.py` `scheduling_depth`,
   concurrency/depth check, zombie health check all walk `parent_agent_session_id` ->
   find `None` -> treat child as orphan root.
3. **Worker enqueues child**: `enqueue_session()` -> sets `parent_agent_session_id` only.
4. **Bridge response routing**: `bridge/response.py`, `stop.py` hook, `steer_child.py`
   call `get_parent_session()` / `get_child_sessions()` which read `parent_session_id`
   -> find `None` -> cannot route.
5. **Dashboard UI**: walks `parent_agent_session_id` -> missing bridge-spawned children.

After the fix, all five paths resolve to the same canonical `parent_agent_session_id`
field via direct writes (creators) or the backward-compat alias (legacy readers).

## Architectural Impact

- **New dependencies**: none
- **Interface changes**: `parent_session_id` changes from a `KeyField` to a
  `@property` alias; public read/write API preserved via the alias getter/setter.
- **Coupling**: decreases — one field instead of two to keep in sync.
- **Data ownership**: unchanged.
- **Reversibility**: fully reversible for one release cycle — the alias keeps legacy
  callers working. The KeyField removal is the only irreversible step and happens after
  migration is confirmed.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a focused refactor with a well-understood template (the `parent_chat_session_id`
alias). Bottleneck is careful migration of call sites and Redis data, not design.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Required for migration script dry-run |

## Solution

### Key Elements

- **Canonical field**: `parent_agent_session_id` remains the only `KeyField`.
- **Alias property**: `parent_session_id` becomes a `@property` with getter/setter that
  delegate to `parent_agent_session_id`.
- **Kwarg normalization**: extend `_normalize_kwargs` to map `parent_session_id` kwarg
  -> `parent_agent_session_id` (mirrors lines 306-314 for `parent_chat_session_id`).
- **Creation paths**: `create_child()` and `create_dev()` write
  `parent_agent_session_id` directly.
- **Reader consolidation**: `get_parent_session()` and `get_child_sessions()` read
  `parent_agent_session_id` internally; public API preserved.
- **Redis migration**: script copies `parent_session_id` -> `parent_agent_session_id`
  for any session where the former is set and the latter is not.
- **Alias chain**: `parent_chat_session_id` -> `parent_session_id` ->
  `parent_agent_session_id`. The existing alias keeps working because
  `parent_session_id` is still a readable/writable attribute, now backed by a property.

### Flow

`create_child()` call -> `parent_agent_session_id=X` saved to Redis ->
`scheduling_depth` reads `parent_agent_session_id=X` -> finds parent -> correct depth.

### Technical Approach

- Remove the `parent_session_id = KeyField(null=True)` declaration on line 206.
- Add `parent_session_id` as a `@property` (get/set) delegating to
  `parent_agent_session_id`, placed near the existing `parent_chat_session_id` alias
  (~line 397).
- Update `create_child()` signature internals: keep the `parent_session_id` kwarg name
  on the classmethod for call-site compatibility, but write it to
  `parent_agent_session_id` when constructing the session.
- Update `_normalize_kwargs` to map incoming `parent_session_id` -> `parent_agent_session_id`.
- Update `get_parent_session()` / `get_child_sessions()` queries to use
  `parent_agent_session_id`.
- Write `scripts/migrate_unify_parent_session_field.py` following the template of
  `scripts/migrate_parent_session_field.py`. Dry-run default; `--apply` to commit.
- Run the migration against local Redis during build; document the command for
  production rollout in `docs/features/subconscious-memory.md`-style sibling doc.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No broad `except Exception: pass` blocks in the touched scope. The `scheduling_depth`
  walker catches exceptions per-step — add a test that a missing parent terminates the
  walk cleanly.

### Empty/Invalid Input Handling
- [ ] Test: `parent_session_id=None` kwarg stays `None` on both fields.
- [ ] Test: setting `parent_session_id` to empty string is coerced to `None` (current
  behavior of `KeyField`).

### Error State Rendering
- No user-visible rendering surface in this change — confined to model internals and
  hierarchy walkers.

## Test Impact

- [ ] `tests/unit/test_agent_session_hierarchy.py` — UPDATE: add cases asserting that
  `create_child()` populates `parent_agent_session_id` and that the `parent_session_id`
  alias reads through.
- [ ] `tests/unit/test_steer_child.py` — UPDATE: verify `get_child_sessions()` finds
  children created via both `create_child()` and `enqueue_session()`.
- [ ] `tests/integration/test_session_zombie_health_check.py` — UPDATE: add a case where
  a `create_child()`-spawned session is correctly recognized as non-orphan.
- [ ] Any test that constructs `AgentSession(parent_session_id=...)` directly — VERIFY
  still works via the kwarg alias mapping.

## Rabbit Holes

- **Do NOT** refactor `session_type` vs `role` semantics — out of scope.
- **Do NOT** touch `parent_chat_session_id` — its alias chain continues to work.
- **Do NOT** rename `parent_agent_session_id` — it is the canonical name.
- **Do NOT** delete `scripts/migrate_parent_session_field.py` — it is historical.

## Risks

### Risk 1: Hidden callers still write `parent_session_id` directly on saved instances
**Impact:** Writes would go to the alias property and update `parent_agent_session_id`,
which is the desired behavior — but a caller that inspects the old field by name (e.g.,
reflection, `__dict__`, `model_dump`) could see unexpected output.
**Mitigation:** grep the full codebase (`agent/`, `bridge/`, `worker/`, `ui/`, `tools/`,
`scripts/`, `tests/`) for `parent_session_id` before removing the KeyField. Convert any
direct `__dict__` / `model_dump` consumers explicitly.

### Risk 2: Redis data migration incomplete at deploy time
**Impact:** Sessions with only `parent_session_id` set remain invisible to hierarchy
walkers.
**Mitigation:** The alias getter reads from `parent_agent_session_id` only, so post-
migration is critical. Migration script runs as part of `/do-build` verification and
must report zero remaining unmigrated records.

### Risk 3: Popoto `KeyField` -> `@property` swap breaks ORM introspection
**Impact:** `AgentSession.query.filter(parent_session_id=...)` would fail silently
because the field is no longer indexed.
**Mitigation:** grep for `.filter(parent_session_id=` and `.query.get(parent_session_id`
call sites. Convert them to `parent_agent_session_id`. Add a test that exercises query-
by-parent on the canonical field.

## Race Conditions

No race conditions identified — this is a schema consolidation. All operations are
synchronous Redis reads/writes. The migration script is idempotent and can be re-run.

## No-Gos (Out of Scope)

- Removing the `parent_session_id` property alias entirely (deferred to next release
  cycle per issue constraints).
- Touching `parent_chat_session_id` semantics.
- Refactoring `scheduling_depth` caching / safety cap.
- Changing the zombie health check algorithm.

## Update System

No update system changes required — this is a purely internal model refactor. The Redis
migration script is one-shot and runs as part of the build verification; no ongoing
`/update` skill changes are needed. New installations create fresh Redis data that
already uses the canonical field.

## Agent Integration

No agent integration required — this is an internal model / worker / bridge change.
No MCP server, `.mcp.json`, or bridge imports are affected. The agent never references
`parent_session_id` directly.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/chat-dev-session-architecture.md` to note that
  `parent_agent_session_id` is the single canonical parent field and that
  `parent_session_id` is a deprecated alias.
- [ ] Add a short entry to the AgentSession model section explaining the alias chain
  (`parent_chat_session_id` -> `parent_session_id` -> `parent_agent_session_id`).

### Inline Documentation
- [ ] Docstring on the new `parent_session_id` property noting it is a deprecated alias.
- [ ] Update the `create_child()` docstring to note it writes `parent_agent_session_id`.

## Success Criteria

- [ ] `AgentSession` has exactly one `KeyField` for parent reference:
  `parent_agent_session_id`.
- [ ] `parent_session_id` exists as a `@property` alias with getter + setter.
- [ ] `create_child()` and `create_dev()` populate `parent_agent_session_id`.
- [ ] `get_parent_session()` / `get_child_sessions()` read `parent_agent_session_id`.
- [ ] `scheduling_depth` correctly reports non-zero depth for bridge-spawned Dev
  sessions.
- [ ] The zombie health check recognizes bridge-spawned children as non-orphan.
- [ ] Dashboard UI shows parent-child relationships for both `create_child()`- and
  `enqueue_session()`-spawned sessions.
- [ ] `scripts/migrate_unify_parent_session_field.py` exists and reports zero
  unmigrated records after `--apply`.
- [ ] `tests/unit/test_agent_session_hierarchy.py`, `tests/unit/test_steer_child.py`,
  and `tests/integration/test_session_zombie_health_check.py` pass.
- [ ] `grep -rn 'parent_session_id = KeyField' models/` returns nothing.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model)**
  - Name: model-builder
  - Role: Remove `parent_session_id` KeyField, add property alias, update
    `_normalize_kwargs`, `create_child`, `create_dev`, `get_parent_session`,
    `get_child_sessions`.
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: migration-builder
  - Role: Author `scripts/migrate_unify_parent_session_field.py` following the
    existing migration template; dry-run default, `--apply` to commit.
  - Agent Type: migration-specialist
  - Resume: true

- **Builder (call-sites)**
  - Name: callsite-builder
  - Role: grep and convert any `.filter(parent_session_id=...)` /
    `__dict__`/`model_dump` consumers in `agent/`, `bridge/`, `worker/`, `ui/`,
    `tools/`, `scripts/`, `tests/`.
  - Agent Type: builder
  - Resume: true

- **Validator (hierarchy)**
  - Name: hierarchy-validator
  - Role: Run the three impacted test files, verify new assertions pass, verify
    migration script reports zero remaining records.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/chat-dev-session-architecture.md` alias chain docs.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Refactor model
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_hierarchy.py
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `parent_session_id = KeyField(null=True)` at line 206.
- Add `@property` and `@parent_session_id.setter` that delegate to
  `parent_agent_session_id`, placed next to the `parent_chat_session_id` alias.
- Extend `_normalize_kwargs` to map `parent_session_id` -> `parent_agent_session_id`.
- Update `create_child()` to write `parent_agent_session_id` directly.
- Update `create_dev()` similarly.
- Update `get_parent_session()` / `get_child_sessions()` queries to use
  `parent_agent_session_id`.

### 2. Convert call sites
- **Task ID**: build-callsites
- **Depends On**: build-model
- **Validates**: tests/unit/test_steer_child.py
- **Assigned To**: callsite-builder
- **Agent Type**: builder
- **Parallel**: false
- grep for `parent_session_id` across the tree. Replace any direct Popoto query usages
  (e.g., `.filter(parent_session_id=`) with `parent_agent_session_id`.
- Leave kwarg-style usages alone; the normalization layer handles them.

### 3. Migration script
- **Task ID**: build-migration
- **Depends On**: build-model
- **Validates**: (manual) dry-run output shows zero unmigrated after apply
- **Assigned To**: migration-builder
- **Agent Type**: migration-specialist
- **Parallel**: true
- Create `scripts/migrate_unify_parent_session_field.py` following the template of
  `scripts/migrate_parent_session_field.py`.
- Walk all `AgentSession` records; where `parent_session_id` raw field is set but
  `parent_agent_session_id` is not, copy the value.
- Print a summary (migrated count, already-correct count, skipped count).
- Support `--dry-run` (default) and `--apply`.

### 4. Update tests
- **Task ID**: build-tests
- **Depends On**: build-model
- **Validates**: the three impacted test files
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add hierarchy test cases asserting `create_child()` populates
  `parent_agent_session_id` and that the alias reads through.
- Add a zombie health check test case asserting bridge-spawned children are not
  orphaned.
- Add a `get_child_sessions()` test case spanning both creation paths.

### 5. Validate
- **Task ID**: validate-hierarchy
- **Depends On**: build-model, build-callsites, build-migration, build-tests
- **Assigned To**: hierarchy-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_hierarchy.py tests/unit/test_steer_child.py tests/integration/test_session_zombie_health_check.py -x -q`.
- Run migration script dry-run, then apply, then dry-run again — confirm idempotent
  and reports zero remaining records.
- Verify `grep -rn 'parent_session_id = KeyField' models/` is empty.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hierarchy
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/chat-dev-session-architecture.md` with the unified field and
  the alias chain note.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hierarchy-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the three test files.
- Confirm ruff format clean.
- Confirm docs updated.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hierarchy tests | `pytest tests/unit/test_agent_session_hierarchy.py tests/unit/test_steer_child.py tests/integration/test_session_zombie_health_check.py -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| KeyField removed | `grep -n 'parent_session_id = KeyField' models/agent_session.py` | exit code 1 |
| Alias property present | `grep -n 'def parent_session_id' models/agent_session.py` | exit code 0 |
| Migration script exists | `test -f scripts/migrate_unify_parent_session_field.py` | exit code 0 |
| Migration idempotent | `python scripts/migrate_unify_parent_session_field.py --apply && python scripts/migrate_unify_parent_session_field.py --dry-run` | output contains "0 to migrate" |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Should the deprecated `parent_session_id` property emit a `DeprecationWarning` on
   write, or stay silent to avoid log spam during the transition cycle?
2. Should the migration script run automatically on worker startup (one-shot gated on a
   Redis flag) or remain manual via `/do-build` verification?
