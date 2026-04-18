---
status: Planning
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1025
last_comment_id:
---

# Unify Parent FK on AgentSession — Remove Aliases

## Problem

Three spellings for the same FK relationship coexist in the codebase, adding indirection and confusing readers:

- `parent_agent_session_id` — real `KeyField` at `models/agent_session.py:238` (canonical)
- `parent_session_id` — `@property` alias at `models/agent_session.py:609-617`
- `parent_chat_session_id` — `@property` alias at `models/agent_session.py:619-627`

Plus kwarg normalization at `models/agent_session.py:442-456` that silently accepts all three spellings on construction. Call sites are inconsistent: `scripts/steer_child.py:99` reads via the deprecated property, `create_child()` and `create_dev()` accept the alias as parameter names, and tests pass kwargs using the deprecated names.

**Current behavior:** Any of the three names works, but callers don't know which is authoritative.

**Desired outcome:** Single canonical name (`parent_agent_session_id`) across all production code and tests. Aliases and kwarg normalization removed from `models/agent_session.py`. Redis data normalized so no records retain stale field names.

## Freshness Check

**Baseline commit:** `25879a78ab676d81b6f958d59d067a3162a8e73b`
**Issue filed at:** 2026-04-17T08:43:13Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py:238` — `parent_agent_session_id = KeyField(null=True)` — still holds exactly
- `models/agent_session.py:586-603` (now 609-627) — `@property` aliases `parent_session_id` and `parent_chat_session_id` — still present, line numbers drifted by ~23 lines due to prior changes
- `models/agent_session.py:413-432` (now 442-456) — kwarg normalization block — still present, line numbers drifted
- `scripts/steer_child.py:99` — `child.parent_session_id != parent_id` — still uses alias at this line
- `models/agent_session.py:1105, 1156, 1179` (now 1129, 1162, 1180) — `create_child()` / `create_dev()` signatures — `parent_session_id` still the parameter name in both

**Cited sibling issues/PRs re-checked:**
- #1022 — still open (PM orchestration audit umbrella, recon complete)
- #757 — closed 2026-04-07, resolved by PR #764 (established canonical field, demoted aliases)
- PR #764 — merged 2026-04-07 — created the current state: canonical field established, aliases added as temporary shims. This issue is the planned cleanup.

**Commits on main since issue was filed (touching referenced files):**
- `57b7eb76` chore(#1024): delete orphan SubagentStop hook — irrelevant to parent FK
- `350df702` feat(health): two-tier no-progress detector — irrelevant to parent FK

**Active plans in `docs/plans/` overlapping this area:** none — no active plan touches `models/agent_session.py` parent field aliases.

**Notes:** Line numbers drifted ~23 lines from the issue body, but all claims still hold. The PR #764 notes confirm the aliases were deliberately kept for "one release cycle" — that cycle is now past.

## Prior Art

- **Issue #757 / PR #764**: "AgentSession dual parent fields (parent_session_id vs parent_agent_session_id) are never synced" / "Unify AgentSession parent field on parent_agent_session_id" — PR #764 established the canonical `KeyField` and demoted `parent_session_id` to a `@property` alias. Explicitly deferred alias removal and call-site cleanup. This issue (#1025) is the deferred cleanup.
- **`scripts/migrate_unify_parent_session_field.py`**: Existing idempotent migration for copying `parent_session_id` Redis hash fields into `parent_agent_session_id` and removing stale fields. Handles `parent_session_id` only (not `parent_chat_session_id` — that alias was never a Redis field, only a Python property).

## Research

No relevant external findings — this is a purely internal code cleanup with no external library or API dependency.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "import popoto; popoto.redis_db.get_REDIS_DB().ping()"` | Migration script needs Redis |

Run all checks: `python scripts/check_prerequisites.py docs/plans/unify-parent-fk.md`

## Solution

### Key Elements

- **Redis migration**: Run `scripts/migrate_unify_parent_session_field.py --apply` to normalize any Redis records still carrying a `parent_session_id` field
- **Code call-site update**: Rewrite production call sites that read `parent_session_id` property to use `parent_agent_session_id` directly
- **Factory method rename**: Change `create_child()` and `create_dev()` parameter name from `parent_session_id` to `parent_agent_session_id`; update all call sites
- **Alias deletion**: Delete `@property` aliases and their setters from `models/agent_session.py`
- **Kwarg normalization deletion**: Delete the two kwarg-normalization blocks in `_normalize_kwargs` that silently remap the deprecated names
- **Test update**: Rewrite test call sites to use the canonical name
- **Migration script archival**: Move `scripts/migrate_unify_parent_session_field.py` to `scripts/archive/` after migration

### Technical Approach

Ordered sequence — each step must complete before the next:

1. **Run migration first** against the local Redis (and document that production must run the same migration before code is deployed). Migration is idempotent and safe.
2. **Rename `create_child()` parameter** from `parent_session_id` → `parent_agent_session_id`. This is a public API change — update all callers in one commit.
3. **Rename `create_dev()` parameter** from `parent_session_id` → `parent_agent_session_id`. Also remove the internal `parent_chat_session_id` fallback in its body.
4. **Fix `scripts/steer_child.py:99`** — read `child.parent_agent_session_id` directly.
5. **Delete `@property` aliases** (lines 609-627 current main) and their comment block.
6. **Delete kwarg normalization** for `parent_chat_session_id` and `parent_session_id` in `_normalize_kwargs` (lines 442-456). Keep `parent_job_id` normalization (that's a different legacy alias and still used).
7. **Update all tests** — change `parent_session_id=...` kwargs and property reads to `parent_agent_session_id`.
8. **Archive migration script**.

Note: `pre_tool_use.py`'s `_start_pipeline_stage(parent_session_id: str, ...)` uses `parent_session_id` as a **local function parameter name** — this is NOT a reference to the deprecated property alias. Rename it to `parent_agent_session_id_` or `pm_session_id` for clarity, but it is not a bug today.

## Failure Path Test Strategy

### Exception Handling Coverage
- The `create_child()` and `create_dev()` rename is a parameter-level change. Callers passing the old keyword will get a Python `TypeError` immediately — no silent failure. Tests will catch this during the update pass.
- No new exception handlers are introduced by this work.

### Empty/Invalid Input Handling
- `parent_agent_session_id=None` is already a valid value (null=True on KeyField). No behavior change here.
- `create_child()` currently accepts `parent_session_id=""` via normalization. After the alias removal, passing an empty string to the renamed parameter is still valid behavior.

### Error State Rendering
- No user-visible output changes. This is a pure internal cleanup.

## Test Impact

- [ ] `tests/e2e/test_context_propagation.py` (lines 82, 88, 111, 118, 133, 149, 185, 231, 252) — UPDATE: replace `parent_session_id=...` kwarg and `.parent_session_id` property read with `parent_agent_session_id`
- [ ] `tests/unit/test_steer_child.py` (lines 31, 154) — UPDATE: replace `.parent_session_id` property set/read with `.parent_agent_session_id`
- [ ] `tests/integration/test_agent_session_queue_session_type.py:47` — UPDATE: replace `parent_session_id=...` kwarg with `parent_agent_session_id`
- [ ] `tests/integration/test_parent_child_round_trip.py:125` — UPDATE: method name `test_parent_session_id_none_without_parent` is cosmetic; rename to `test_no_parent_when_not_set` or keep as-is (not a functional issue)
- [ ] `tests/unit/test_hook_user_prompt_submit.py:239` — UPDATE: method name only; confirm the test body doesn't reference the property
- [ ] `tests/unit/test_summarizer.py:1995,2021` — UPDATE: docstring/comment only; confirm the test bodies don't reference the property

## Rabbit Holes

- **Full rename of `_start_pipeline_stage` local variable** in `pre_tool_use.py` — this is a cosmetic rename of a function parameter, not an alias. Worth doing for clarity but must not block the core work.
- **Renaming `parent_job_id` normalization** — separate legacy alias that has its own lifecycle; leave it untouched.
- **Updating historical doc references** — `docs/plans/completed/`, `docs/plans/parent-child-steering.md`, etc. mention the old names in historical context. Do NOT update those — the issue explicitly allows historical doc mentions.
- **`create_dev()` deprecation** — the method itself is already marked deprecated. Do not remove it as part of this work; only update its internals.

## Risks

### Risk 1: In-flight sessions at deploy time
**Impact:** A session created before migration (with `parent_session_id` in Redis) read by code after alias removal will miss the parent link.
**Mitigation:** Migration script runs before code changes are deployed. On this machine, Redis is local and there are no live sessions to worry about during development.

### Risk 2: Missed call site
**Impact:** A caller passing `parent_session_id=...` gets a Python `TypeError` at runtime.
**Mitigation:** Grep confirms exactly 3 Python source files outside `models/agent_session.py` use the old name: `scripts/steer_child.py`, `agent/hooks/pre_tool_use.py` (local var only), and test files. All are updated by this plan.

## Race Conditions

No race conditions identified — all operations are synchronous Redis reads/writes and Python module changes. The migration script is idempotent so concurrent runs produce the same result.

## No-Gos (Out of Scope)

- Removing the `create_dev()` factory method itself (it's deprecated but callers exist)
- Renaming `parent_job_id` kwarg normalization (different legacy alias)
- Updating historical doc references in completed plans or design docs
- Any changes to the Popoto ORM or index structure beyond running the existing migration
- Schema changes (canonical `KeyField` already exists)

## Update System

No update system changes required — this is a purely internal code cleanup. The migration script (`scripts/migrate_unify_parent_session_field.py`) may need to be run on any deployed machine's Redis before deploying the updated code. Document this in the migration script header or a DEPLOY.md note.

## Agent Integration

No agent integration required — `parent_agent_session_id` is an internal ORM field not exposed through any MCP server or agent tool. The Telegram bridge and worker reference `AgentSession` objects directly; the field rename propagates automatically once call sites are updated.

## Documentation

- [ ] Update `docs/features/agent-session-model.md` — remove references to deprecated alias names, update the field table to show only `parent_agent_session_id` as the FK
- [ ] Update module-level docstring in `models/agent_session.py` (lines ~25, 105-108) — remove mentions of deprecated aliases after they are deleted

## Success Criteria

- [ ] Migration script runs cleanly with `--apply` and reports 0 remaining records to migrate on re-run
- [ ] `grep -rn 'parent_session_id\|parent_chat_session_id' --include="*.py"` returns only: `scripts/archive/migrate_unify_parent_session_field.py`, historical doc plans, and the `migrate_agent_session_keyfield_rename.py` archive script
- [ ] `@property` aliases deleted from `models/agent_session.py`
- [ ] Kwarg normalization blocks for the aliases deleted from `_normalize_kwargs`
- [ ] `create_child()` signature uses `parent_agent_session_id` as parameter name
- [ ] `create_dev()` internal `parent_chat_session_id` fallback removed
- [ ] `scripts/steer_child.py:99` reads `child.parent_agent_session_id`
- [ ] All affected tests updated and `pytest tests/unit/ tests/integration/ tests/e2e/ -x -q` passes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: alias-cleanup-builder
  - Role: Run migration, update call sites, delete aliases, update tests
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: alias-cleanup-validator
  - Role: Verify no alias references remain in production code, confirm tests pass
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Run Redis Migration
- **Task ID**: build-migrate
- **Depends On**: none
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `python scripts/migrate_unify_parent_session_field.py --apply`
- Re-run in dry-run mode to confirm 0 pending changes

### 2. Update Production Call Sites
- **Task ID**: build-callsites
- **Depends On**: build-migrate
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `create_child()` parameter `parent_session_id` → `parent_agent_session_id`
- Rename `create_dev()` parameter `parent_session_id` → `parent_agent_session_id`; remove internal `parent_chat_session_id` fallback
- Fix `scripts/steer_child.py:99` to read `child.parent_agent_session_id`
- Rename local variable in `pre_tool_use.py::_start_pipeline_stage` for clarity (cosmetic)
- Delete `@property` aliases and comment block from `models/agent_session.py`
- Delete kwarg normalization for `parent_chat_session_id` and `parent_session_id` from `_normalize_kwargs`
- Update module-level docstring in `models/agent_session.py` to remove alias mentions

### 3. Update Tests
- **Task ID**: build-tests
- **Depends On**: build-callsites
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/e2e/test_context_propagation.py` — all `parent_session_id` refs
- Update `tests/unit/test_steer_child.py` — property set/read refs
- Update `tests/integration/test_agent_session_queue_session_type.py:47`
- Confirm `tests/integration/test_parent_child_round_trip.py` and `test_hook_user_prompt_submit.py` and `test_summarizer.py` — only method name/docstring usage, update if needed

### 4. Archive Migration Script
- **Task ID**: build-archive
- **Depends On**: build-tests
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Move `scripts/migrate_unify_parent_session_field.py` to `scripts/archive/`
- Add a header comment noting the migration was completed and when

### 5. Update Feature Documentation
- **Task ID**: build-docs
- **Depends On**: build-archive
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` to remove deprecated alias references

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-docs
- **Assigned To**: alias-cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn 'parent_session_id\|parent_chat_session_id' --include="*.py"` — confirm only archive/historical files remain
- Run `pytest tests/unit/ tests/integration/ -x -q` to confirm all tests pass
- Confirm `docs/features/agent-session-model.md` updated

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No alias in production code | `grep -rn 'parent_session_id\|parent_chat_session_id' models/ agent/ bridge/ worker/ tools/ scripts/ --include="*.py" \| grep -v 'scripts/archive/'` | exit code 1 (no matches) |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/ -x -q` | exit code 0 |
| Format clean | `python -m black --check models/agent_session.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| — | — | — | — | — |

---

## Open Questions

None — scope is clear, all call sites identified, prior art reviewed.
