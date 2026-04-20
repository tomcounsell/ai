---
status: docs_complete
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
| Worker stopped | `./scripts/valor-service.sh worker-status \| grep -q "not running"` | Migration calls `AgentSession.rebuild_indexes()` which is not transactional; a concurrent worker may observe a partially migrated record (brief window, but avoidable) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/unify-parent-fk.md`

## Solution

### Key Elements

- **Redis migration**: Run `scripts/migrate_unify_parent_session_field.py --apply` to normalize any Redis records still carrying a `parent_session_id` field
- **Code call-site update**: Rewrite production call sites that read `parent_session_id` property to use `parent_agent_session_id` directly
- **Factory method rename**: Change `create_child()` and `create_dev()` parameter name from `parent_session_id` to `parent_agent_session_id`; update all call sites
- **Alias deletion**: Delete `@property` aliases and their setters from `models/agent_session.py`
- **Kwarg normalization deletion**: Delete the two kwarg-normalization blocks in `_normalize_kwargs` that silently remap the deprecated names
- **Test update**: Rewrite test call sites to use the canonical name
- **Migration script archival**: Create `scripts/archive/` directory and move all three parent-FK migration scripts there after migration: `scripts/migrate_unify_parent_session_field.py`, `scripts/migrate_parent_session_field.py` (predecessor migration — hash field rename), and `scripts/migrate_agent_session_keyfield_rename.py` (KeyField rename). All three contain alias references that must be removed from grep scope once code deletions ship.

### Technical Approach

Ordered sequence — each step must complete before the next:

1. **Pre-flight audit** — grep `parent_session_id|parent_chat_session_id` across `*.json`, `*.yaml`, `*.yml`, `*.toml`, `.mcp.json`, `.claude/` — confirm only ephemeral session memory buffers (`data/sessions/*/memory_buffer.json`) reference the names. Verified clean at plan time; re-run before code edits to catch drift.
2. **Stop worker** — `./scripts/valor-service.sh worker-stop` — then run migration. Migration calls `AgentSession.rebuild_indexes()` which is not transactional, and a running worker could briefly observe a partially migrated record.
3. **Run migration** against the local Redis (and document that production must run the same migration before code is deployed). Migration is idempotent and safe.
4. **Rename `create_child()` parameter** from `parent_session_id` → `parent_agent_session_id`. This is a public API change — update all callers in one commit.
5. **Rename `create_dev()` parameter** from `parent_session_id` → `parent_agent_session_id`. Also remove the internal `parent_chat_session_id` fallback in its body.
6. **Fix `scripts/steer_child.py:99`** — read `child.parent_agent_session_id` directly.
7. **Delete `@property` aliases** (lines 609-627 current main) and their comment block.
8. **Delete kwarg normalization** for `parent_chat_session_id` and `parent_session_id` in `_normalize_kwargs` (lines 442-456). Keep `parent_job_id` normalization (that's a different legacy alias and still used).
9. **Update all tests** — change `parent_session_id=...` kwargs and property reads to `parent_agent_session_id`; rename test method names and update docstrings that embed the deprecated name (required, not cosmetic — needed for the grep Success Criterion).
10. **Archive all three parent-FK migration scripts** to `scripts/archive/` (new directory).

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
- [ ] `tests/integration/test_parent_child_round_trip.py:125` — UPDATE (required): rename `test_parent_session_id_none_without_parent` → `test_no_parent_when_not_set` so the Success Criterion grep stays clean
- [ ] `tests/unit/test_hook_user_prompt_submit.py:239` — UPDATE (required): rename `test_main_creates_session_when_parent_session_id_set` → `test_main_creates_session_when_parent_set`; confirm test body uses `parent_agent_session_id`
- [ ] `tests/unit/test_summarizer.py:1995,2021` — UPDATE (required): rewrite docstrings to use `parent_agent_session_id` / "parent link" phrasing so the grep stays clean

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
**Mitigation:** Grep confirms exactly 3 Python source files outside `models/agent_session.py` use the old name: `scripts/steer_child.py`, `agent/hooks/pre_tool_use.py` (local var only), and test files. All are updated by this plan. Pre-flight audit (Task 2) also sweeps `*.json`, `*.yaml`, `*.yml`, `*.toml`, `.mcp.json`, and `.claude/` for config-level references; verified clean at plan time, only ephemeral `data/sessions/*/memory_buffer.json` shows matches (transient runtime state, not code).

## Race Conditions

The migration script calls `AgentSession.rebuild_indexes()` which is not transactional — a running worker could read a record mid-migration and briefly observe the old field name or a missing parent link. Mitigation: stop the worker before running migration (added as a Prerequisite check and as Step 2 in Technical Approach). Concurrent migration invocations are safe: the script is idempotent, so two runs produce the same result.

## No-Gos (Out of Scope)

- Removing the `create_dev()` factory method itself (it's deprecated but callers exist)
- Renaming `parent_job_id` kwarg normalization (different legacy alias)
- Updating historical doc references in completed plans or design docs
- Any changes to the Popoto ORM or index structure beyond running the existing migration
- Schema changes (canonical `KeyField` already exists)

## Update System

This change ships a Redis schema normalization that MUST run on every bridge-enabled machine before the new code is deployed. Otherwise, a sibling machine running the updated code against un-migrated Redis will read `.parent_agent_session_id` as `None` on records that only have `parent_session_id` hash fields — silently orphaning child sessions.

- **Add a migration step to `scripts/remote-update.sh`**: invoke `scripts/migrate_unify_parent_session_field.py --apply` after `git pull` and `uv sync`, but before `valor-service.sh restart`. The migration is idempotent — a no-op on already-migrated machines.
- **Archival timing**: archival of migration scripts (Task 4) is split into a **follow-up PR** after all sibling machines confirm successful migration. Archiving in the same PR as alias deletion is an operational footgun — if a sibling machine pulls the code before running the updater, and the migration script path has moved, the remote-update invocation in its old shell state can fail. Safer to keep the scripts at their current paths until every machine has pulled at least once with the migration wired into `remote-update.sh`.
- **Follow-up PR** (separate from this plan's scope, tracked by #1025 closeout): move the three migration scripts to `scripts/archive/` and update `remote-update.sh` to skip the (now-archived) migration step.

## Agent Integration

No agent integration required — `parent_agent_session_id` is an internal ORM field not exposed through any MCP server or agent tool. The Telegram bridge and worker reference `AgentSession` objects directly; the field rename propagates automatically once call sites are updated.

## Documentation

- [x] Update `docs/features/agent-session-model.md` — remove references to deprecated alias names, update the field table to show only `parent_agent_session_id` as the FK
- [x] Update module-level docstring in `models/agent_session.py` (lines ~25, 105-108) — remove mentions of deprecated aliases after they are deleted

## Success Criteria

- [x] Migration script runs cleanly with `--apply` and reports 0 remaining records to migrate on re-run
- [x] `grep -rn 'parent_session_id\|parent_chat_session_id' --include="*.py"` on THIS PR returns only: the three migration scripts at their current `scripts/` paths (`scripts/migrate_unify_parent_session_field.py`, `scripts/migrate_parent_session_field.py`, `scripts/migrate_agent_session_keyfield_rename.py`) and historical doc plans under `docs/plans/completed/` or `docs/plans/parent-child-steering.md`. Archival to `scripts/archive/` is deferred to the follow-up PR per the Update System section.
- [x] `@property` aliases deleted from `models/agent_session.py`
- [x] Kwarg normalization blocks for the aliases deleted from `_normalize_kwargs`
- [x] `create_child()` signature uses `parent_agent_session_id` as parameter name
- [x] `create_dev()` internal `parent_chat_session_id` fallback removed
- [x] `scripts/steer_child.py:99` reads `child.parent_agent_session_id`
- [x] All affected tests updated and `pytest tests/unit/ tests/integration/ tests/e2e/ -x -q` passes
- [x] Tests pass (`/do-test`)
- [x] Documentation updated

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

### 1. Pre-flight Audit + Wire Migration into remote-update
- **Task ID**: build-preflight
- **Depends On**: none
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Grep `parent_session_id|parent_chat_session_id` across `*.json`, `*.yaml`, `*.yml`, `*.toml`, `.mcp.json`, and `.claude/` — confirm only ephemeral `data/sessions/*/memory_buffer.json` matches
- Add migration invocation (`python scripts/migrate_unify_parent_session_field.py --apply`) to `scripts/remote-update.sh` between `uv sync` and `valor-service.sh restart`
- Stop local worker: `./scripts/valor-service.sh worker-stop`

### 2. Run Redis Migration
- **Task ID**: build-migrate
- **Depends On**: build-preflight
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `python scripts/migrate_unify_parent_session_field.py --apply`
- Re-run in dry-run mode to confirm 0 pending changes

### 3. Update Production Call Sites
- **Task ID**: build-callsites
- **Depends On**: build-migrate
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `create_child()` parameter `parent_session_id` → `parent_agent_session_id`
- Rename `create_dev()` parameter `parent_session_id` → `parent_agent_session_id`; remove internal `parent_chat_session_id` fallback
- Fix `scripts/steer_child.py:99` to read `child.parent_agent_session_id`
- Rename local variable in `pre_tool_use.py::_start_pipeline_stage` for clarity (cosmetic — not an alias reference; see Rabbit Holes)
- Delete `@property` aliases and comment block from `models/agent_session.py`
- Delete kwarg normalization for `parent_chat_session_id` and `parent_session_id` from `_normalize_kwargs`
- Update module-level docstring in `models/agent_session.py` to remove alias mentions

### 4. Update Tests
- **Task ID**: build-tests
- **Depends On**: build-callsites
- **Assigned To**: alias-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/e2e/test_context_propagation.py` — all `parent_session_id` refs
- Update `tests/unit/test_steer_child.py` — property set/read refs
- Update `tests/integration/test_agent_session_queue_session_type.py:47`
- Rename `tests/integration/test_parent_child_round_trip.py::test_parent_session_id_none_without_parent` → `test_no_parent_when_not_set` (required for grep cleanliness)
- Rename `tests/unit/test_hook_user_prompt_submit.py::test_main_creates_session_when_parent_session_id_set` → `test_main_creates_session_when_parent_set` (required for grep cleanliness)
- Rewrite docstrings in `tests/unit/test_summarizer.py:1995,2021` to use `parent_agent_session_id` / "parent link" (required for grep cleanliness)

### 5. Update Feature Documentation
- **Task ID**: build-docs
- **Depends On**: build-tests
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
- Run `grep -rn 'parent_session_id\|parent_chat_session_id' --include="*.py"` — confirm matches are limited to the three migration scripts at their current `scripts/` paths plus historical doc plans
- Run `pytest tests/unit/ tests/integration/ -x -q` to confirm all tests pass
- Confirm `docs/features/agent-session-model.md` updated
- Confirm `scripts/remote-update.sh` invokes the migration

### 7. Archival Follow-up (separate PR, tracked after merge)
- **Task ID**: followup-archive
- **Depends On**: main PR merged and propagated to all bridge machines
- **Assigned To**: (deferred — not executed in this plan)
- **Agent Type**: builder
- **Parallel**: false
- Out of scope for this plan's build; documented here so it is not forgotten
- Create `scripts/archive/` directory
- Move `scripts/migrate_unify_parent_session_field.py`, `scripts/migrate_parent_session_field.py`, `scripts/migrate_agent_session_keyfield_rename.py`
- Remove migration invocation from `scripts/remote-update.sh`
- Update Success Criterion exemption in any future work to reference `scripts/archive/` paths

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No alias in production code | `grep -rn 'parent_session_id\|parent_chat_session_id' models/ agent/ bridge/ worker/ tools/ --include="*.py"` | exit code 1 (no matches in live code — migration scripts under `scripts/` are excluded from this check for this PR and removed via the follow-up archival PR) |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/ -x -q` | exit code 0 |
| Format clean | `python -m black --check models/agent_session.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | C2 Consistency Auditor | Test method names/docstrings keep `parent_session_id` → Success Criterion grep fails deterministically | Test Impact items 4–6 + Task 4 | Moved from "optional/cosmetic" to required renames: `test_no_parent_when_not_set`, `test_main_creates_session_when_parent_set`, and rewritten docstrings in `test_summarizer.py` |
| CONCERN | S1 Skeptic + A2 Archaeologist | `scripts/migrate_parent_session_field.py` (predecessor migration) has 12 alias refs, not in archival list | Key Elements + Task 7 | All three parent-FK migration scripts enumerated; archival deferred to a follow-up PR |
| CONCERN | C1 Consistency Auditor | `scripts/archive/` doesn't exist; Success Criterion references nonexistent paths | Success Criteria rewrite + Task 7 | Success Criterion now references scripts at their **current** `scripts/` paths; archival PR creates `scripts/archive/` later |
| CONCERN | O1 Operator | Update System claims no changes, but migration MUST run on every bridge machine or `.parent_agent_session_id` reads silently return None | Update System section rewrite | `scripts/remote-update.sh` gains migration invocation between `uv sync` and service restart (Task 1) |
| CONCERN | O2 Operator | Archiving migration in the same PR as alias deletion is an ops footgun for sibling machines | Update System section + Task 7 | Archival split into a **follow-up PR** once all machines have pulled at least once with migration wired into remote-update |
| CONCERN | AD1 Adversary | Risk 2's "exactly 3 files" grep is Python-only; JSON/YAML/TOML configs not audited | Task 1 pre-flight + Risks Mitigation | Plan now explicitly sweeps `*.json`, `*.yaml`, `*.yml`, `*.toml`, `.mcp.json`, `.claude/` (verified clean; only ephemeral session memory buffers match) |
| CONCERN | AD2 Adversary | Migration calls `rebuild_indexes()` non-transactionally; concurrent worker may observe partial state | Prerequisites row + Technical Approach Step 2 + Race Conditions rewrite | Worker must be stopped before migration; added as a Prerequisite and as an ordered step |
| NIT | S2 Skeptic | Risk 1 leans on local-dev simplicity ("no live sessions to worry about") | Update System section | Addressed in the broader Update System rewrite — remote-update hook covers multi-machine case |
| NIT | A1 Archaeologist | Prior Art missing direct PR #764 quote | Prior Art section (line 43) | Confirmed the existing wording already states PR #764's intent ("one release cycle"); no further edit needed — treated as nit |
| NIT | SI1 Simplifier | Two parallel ordered task lists (Technical Approach vs. Step-by-Step Tasks) | Technical Approach + Step by Step Tasks | Technical Approach now mirrors the step-by-step ordering exactly, with Task 7 explicitly marked as follow-up |
| NIT | SI2 Simplifier | `_start_pipeline_stage` rename listed both in Task 2 and Rabbit Holes | Task 3 parenthetical + Rabbit Holes | Clarified in Task 3: "cosmetic — not an alias reference; see Rabbit Holes" |

---

## Open Questions

None — scope is clear, all call sites identified, prior art reviewed.
