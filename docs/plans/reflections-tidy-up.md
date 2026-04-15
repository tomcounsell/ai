---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/978
last_comment_id: none
revision_applied: true
---

# Reflections Tidy-Up: Merge Overlapping Health Gates/Drips, Rename to {subject}-{verb} Standard

## Problem

Two pairs of reflections have overlapping scope — they read identical data sources and execute nearly identical logic on every tick, creating maintenance overhead and confusion. Eight additional reflections have names that violate the project's `{subject}-{verb}` naming standard.

**Current behavior:**
- `api-health-gate` (in `agent/sustainability.py`) and `worker-health-gate` (in `agent/hibernation.py`) both call `get_health().get("anthropic")` every 60s. They manage different Redis flags (`queue_paused` vs `worker:hibernating`) but share identical branch structure and circuit-read duplication.
- `recovery-drip` (`agent/sustainability.py`) and `session-resume-drip` (`agent/hibernation.py`) both drip one paused session → `pending` per 30s tick, differing only in which status they drain (`paused_circuit` vs `paused`) and which recovery flag they check (`recovery:active` vs `worker:recovering`).
- Eight reflection names fail `{subject}-{verb}` standard: `health-check` (no subject), `popoto-index-cleanup` (leaks ORM library name), `sustainability-digest` (leaks module name), `legacy-code-scan` (vague adjective), `redis-data-quality` (no verb), `log-review` (missing scope), `task-management` (no verb), `branch-plan-cleanup` (ambiguous compound).

**Desired outcome:**
- `circuit-health-gate` replaces both health gates, reading `get_health().get("anthropic")` once and managing all circuit-related flags atomically.
- `session-recovery-drip` replaces both drip reflections, handling both `paused_circuit` and `paused` session statuses (priority order: `paused_circuit` first, FIFO within each group).
- All 8 renamed reflections follow `{subject}-{verb}` and are self-descriptive.
- `agent/hibernation.py` deleted (all content absorbed into `agent/sustainability.py`); `agent/agent_session_queue.py:2549` import updated.
- `config/reflections.yaml` has 31 entries (was 33).

## Freshness Check

**Baseline commit:** `c98873df`
**Issue filed at:** 2026-04-14
**Disposition:** Unchanged

**File:line references re-verified:**
- `config/reflections.yaml:29` — `health-check` — still present
- `config/reflections.yaml:53` — `popoto-index-cleanup` — still present
- `config/reflections.yaml:66` — `api-health-gate` calling `agent.sustainability.api_health_gate` — still present
- `config/reflections.yaml:90` — `recovery-drip` calling `agent.sustainability.recovery_drip` — still present
- `config/reflections.yaml:100` — `worker-health-gate` calling `agent.hibernation.worker_health_gate` — still present
- `config/reflections.yaml:108` — `session-resume-drip` calling `agent.hibernation.session_resume_drip` — still present
- `config/reflections.yaml:116` — `sustainability-digest` — still present
- `config/reflections.yaml:164` — `legacy-code-scan` — still present
- `config/reflections.yaml:180` — `redis-data-quality` — still present
- `config/reflections.yaml:188` — `branch-plan-cleanup` — still present
- `config/reflections.yaml:214` — `log-review` — still present
- `config/reflections.yaml:264` — `task-management` — still present
- `agent/sustainability.py` — `api_health_gate()`, `recovery_drip()` confirmed at file top; Redis keys `{project}:sustainability:queue_paused`, `{project}:recovery:active`
- `agent/hibernation.py` — `worker_health_gate()`, `session_resume_drip()`, `send_hibernation_notification()` confirmed present; Redis keys `{project}:worker:hibernating`, `{project}:worker:recovering`

**Cited sibling issues/PRs re-checked:** None cited in issue body.

**Commits on main since issue was filed (touching referenced files):** No commits on `agent/sustainability.py`, `agent/hibernation.py`, or `config/reflections.yaml` since issue was filed.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/reflections-quality-pass.md` (issue #926) — addresses scheduler correctness (`next_due`, `log_path`, model split, field conventions). Different concern; no overlap with naming or merge work.

**Notes:** `docs/plans/worker_hibernation.md` (issue #839, status: Shipped) is the origin of `agent/hibernation.py` — that plan is complete and is the direct predecessor to the delete-after-merge work here.

## Prior Art

- **Issue #839 / `docs/plans/worker_hibernation.md`** (Shipped): Added `worker_health_gate` and `session_resume_drip` to `agent/hibernation.py`. Created the duplication this plan resolves. Outcome: successfully shipped, but created structural overlap with `agent/sustainability.py`.
- **Issue #773** (referenced in `reflections.yaml` comment): Created `agent/sustainability.py` with `api_health_gate` and `recovery_drip`. Established the sustainability pattern. Worker hibernation was added independently after, creating the sibling modules.

## Research

No relevant external findings — this is a purely internal refactor of Python callables, YAML config, and test fixtures with no external library changes.

## Data Flow

**Reflection tick → Redis flag management:**

1. **Entry:** `agent/reflection_scheduler.py` fires `circuit-health-gate` every 60s (and `session-recovery-drip` every 30s)
2. **Circuit read:** `get_health().get("anthropic")` — single read for both former reflections
3. **Flag management (health gate):**
   - OPEN/HALF_OPEN → set `{project}:sustainability:queue_paused` (TTL 3600s) AND `{project}:worker:hibernating` (TTL 600s); log notification trigger
   - CLOSED → delete both flags, set `{project}:recovery:active` (TTL 3600s) AND `{project}:worker:recovering` (TTL 3600s); enqueue wake notification on first transition
4. **Drip (session-recovery-drip):** checks `recovery:active` OR `worker:recovering` flag → queries `paused_circuit` sessions first, then `paused` sessions, FIFO within each group → transitions one to `pending` per tick → clears both flags when combined queue empty
5. **Worker reads both flags:** `_pop_agent_session()` blocks when either `queue_paused` OR `worker:hibernating` is set (existing OR logic, unchanged)

## Architectural Impact

- **New dependencies:** None — all imports already exist in `agent/sustainability.py`
- **Interface changes:** `agent/hibernation` module deleted; callables path in `config/reflections.yaml` changes from `agent.hibernation.*` to `agent.sustainability.*`; function names change in `agent/sustainability.py`
- **Coupling:** Decreases — removes cross-module dependency between two sustainability-related modules
- **Data ownership:** Unified — `agent/sustainability.py` now owns all circuit-related Redis flags
- **Reversibility:** Medium — Redis keys and YAML names are the stable interface; reverting requires re-splitting the module

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`circuit-health-gate`**: Single reflection in `agent/sustainability.py` that reads `get_health().get("anthropic")` once and manages `queue_paused`, `worker:hibernating`, `recovery:active`, and `worker:recovering` flags atomically. Absorbs `send_hibernation_notification` helper from `hibernation.py`.
- **`session-recovery-drip`**: Single reflection that checks `recovery:active OR worker:recovering`, queries `paused_circuit` sessions first then `paused` sessions FIFO, drips one per tick, and clears both flags when the combined queue is empty.
- **8 renames in `config/reflections.yaml`**: Update `name:` field and `description:` for each renamed reflection. No callable path changes for these 8 — only the YAML `name:` key and any log prefixes in the Python callable.
- **`agent/hibernation.py` deletion**: After content is absorbed into `agent/sustainability.py`.

### Rename Table

| Current name | New name | Callable (unchanged) |
|---|---|---|
| `health-check` | `session-liveness-check` | `agent.agent_session_queue._agent_session_health_check` |
| `popoto-index-cleanup` | `redis-index-cleanup` | `scripts.popoto_index_cleanup.run_cleanup` |
| `sustainability-digest` | `system-health-digest` | agent (no callable) |
| `legacy-code-scan` | `tech-debt-scan` | `reflections.maintenance.run_legacy_code_scan` |
| `redis-data-quality` | `redis-quality-audit` | `reflections.maintenance.run_redis_data_quality` |
| `log-review` | `daily-log-review` | `reflections.auditing.run_log_review` |
| `task-management` | `task-backlog-check` | `reflections.task_management.run_task_management` |
| `branch-plan-cleanup` | `merged-branch-cleanup` | `reflections.maintenance.run_branch_plan_cleanup` |

### Technical Approach

**Step 1 — Merge `circuit-health-gate` into `agent/sustainability.py`:**
- Add `circuit_health_gate()` function that reads the circuit once, then:
  - OPEN/HALF_OPEN: renew `queue_paused` (TTL 3600s) AND `worker:hibernating` (TTL 600s); send hibernation notification on first entry (was_both_clear)
  - CLOSED: delete both flags; if either was set, set `recovery:active` AND `worker:recovering` (TTL 3600s), call `send_hibernation_notification("waking")`
- Copy `send_hibernation_notification()` from `hibernation.py` into `sustainability.py`
- Update `agent/agent_session_queue.py:2549`: change `from agent.hibernation import send_hibernation_notification` → `from agent.sustainability import send_hibernation_notification` (the hibernating-event caller is the only caller of the `"hibernating"` variant; `circuit_health_gate` calls the `"waking"` variant)
- Update module docstring

**Step 2 — Merge `session-recovery-drip` into `agent/sustainability.py`:**
- Add `session_recovery_drip()` function that:
  - Checks `recovery:active OR worker:recovering` — no-op if neither set
  - Queries `paused_circuit` sessions first, then `paused` sessions, sorts each group FIFO
  - Drips one per tick, logs with `[session-recovery-drip]` prefix
  - Clears BOTH `recovery:active` AND `worker:recovering` when combined queue empty

**Step 3 — Update `config/reflections.yaml`:**
- Replace `api-health-gate` + `worker-health-gate` entries with single `circuit-health-gate` entry pointing to `agent.sustainability.circuit_health_gate`
- Replace `recovery-drip` + `session-resume-drip` entries with single `session-recovery-drip` entry pointing to `agent.sustainability.session_recovery_drip`
- Rename the 8 entries in the rename table above (update `name:` and `description:` fields only)

**Step 4 — Delete `agent/hibernation.py`**

**Step 5 — Update tests:**
- `tests/unit/test_sustainability.py`: add tests for `circuit_health_gate` (covers both old gate paths) and `session_recovery_drip` (covers both drip statuses); remove direct imports of old `api_health_gate` / `recovery_drip` functions
- `tests/unit/test_hibernation.py`: delete file (all tests replaced by sustainability tests)
- `tests/unit/test_reflection_scheduler.py`: update reflection name constants (`worker-health-gate` → `circuit-health-gate`, `session-resume-drip` → `session-recovery-drip`)
- `tests/unit/test_reflections_package.py`: update `run_legacy_code_scan`, `run_redis_data_quality`, `run_log_review`, `run_task_management` references (callables unchanged, only YAML names change — these tests test the callables, not the names, so impact is minimal)

**Log prefix strings to update in `agent/sustainability.py` callables:**
- `[api-health-gate]` → `[circuit-health-gate]`
- `[recovery-drip]` → `[session-recovery-drip]`

**Log prefix strings absorbed from `agent/hibernation.py`:**
- `[worker-health-gate]` → `[circuit-health-gate]`
- `[session-resume-drip]` → `[session-recovery-drip]`

## Failure Path Test Strategy

### Exception Handling Coverage
- Both `api_health_gate` and `worker_health_gate` have top-level `except Exception: logger.exception(...)` guards — the merged `circuit_health_gate` must preserve this guard. Test: assert that an exception raised inside does not propagate out.
- Both drips have the same guard pattern — `session_recovery_drip` must also preserve it.

### Empty/Invalid Input Handling
- When no sessions are in `paused_circuit` or `paused` status: both recovery flags must be cleared. Test this explicitly.
- When circuit is None (not registered): `circuit_health_gate` must no-op and return, not raise. Test this.

### Error State Rendering
- `send_hibernation_notification` failure must not crash `circuit_health_gate` — it is already wrapped in try/except in `worker_health_gate`. Preserve that guard in the merged function.

## Test Impact

- [ ] `tests/unit/test_sustainability.py` — UPDATE: replace `api_health_gate` / `recovery_drip` imports and tests with `circuit_health_gate` / `session_recovery_drip` tests; add tests for combined flag management and combined session drip
- [ ] `tests/unit/test_hibernation.py` — DELETE: all tests are superseded by updated test_sustainability.py tests for circuit_health_gate and session_recovery_drip
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: change `worker-health-gate` → `circuit-health-gate` and `session-resume-drip` → `session-recovery-drip` in any registration assertions; update hardcoded `"health-check"` strings at lines 49, 376, 397, 480, 489, 506 → `"session-liveness-check"`
- [ ] `tests/unit/test_reflections_package.py` — UPDATE: no callable changes for the 8 renames; verify no reflection `name:` strings are hardcoded in these tests (if found, update to new names)
- [ ] `tests/unit/test_hibernation.py::test_worker_health_gate_registered` — DELETE: superseded by `circuit-health-gate` registration test in test_sustainability.py
- [ ] `tests/unit/test_hibernation.py::test_session_resume_drip_registered` — DELETE: superseded by `session-recovery-drip` registration test in test_sustainability.py

## Rabbit Holes

- **Changing reflection intervals or priorities** — out of scope; the merge does not change scheduling behavior
- **Splitting `branch-plan-cleanup` into two separate reflections** — out of scope; rename only
- **Refactoring logic inside the 8 renamed reflections** — only the YAML `name:` field and log prefix strings change; no behavior changes
- **Migrating existing Redis keys** — `queue_paused` and `worker:hibernating` key paths are unchanged; no migration needed
- **Updating the `Reflection` model's `name` field in Redis** — Popoto models store the name, but the scheduler populates it from YAML on each load; no Redis migration needed for the rename

## Risks

### Risk 1: Both flags not cleared atomically on circuit close
**Impact:** Worker resumes dequeuing but hibernation flag still set (or vice versa), causing a missed recovery window.
**Mitigation:** `circuit_health_gate` deletes both flags in the same function body before returning; no async gap between the two deletes.

### Risk 2: Combined drip clears flags prematurely if only one queue is empty
**Impact:** The other queue's sessions never get dripped.
**Mitigation:** `session_recovery_drip` only clears `recovery:active` AND `worker:recovering` when BOTH `paused_circuit` AND `paused` queues are simultaneously empty.

### Risk 3: Hibernation notification double-sent on circuit close
**Impact:** Two "waking" notifications sent to Telegram.
**Mitigation:** Guard with `was_either_flag_set` check before calling `send_hibernation_notification("waking")` — only fire if at least one flag was active before the delete.

## Race Conditions

### Race 1: Flag check and delete between health-gate ticks
**Location:** `agent/sustainability.py::circuit_health_gate()`
**Trigger:** Two scheduler ticks fire in close succession (e.g., after restart)
**Data prerequisite:** `worker:hibernating` and `queue_paused` must reflect current circuit state before any drip fires
**State prerequisite:** Circuit state must be CLOSED before drip resumes sessions
**Mitigation:** Health gate and drip are separate reflections running on their own tick schedule; Redis writes are atomic. Double-clearing a flag that's already cleared is a safe no-op.

No other race conditions identified — all operations are synchronous and run in the reflection scheduler's thread executor.

## No-Gos (Out of Scope)

- Changing reflection intervals or priorities
- Splitting any reflection into multiple reflections
- Refactoring logic inside the 8 renamed callables (rename surface only)
- Changing any Redis key schemas (`queue_paused`, `worker:hibernating`, etc.)
- Adding new circuit breaker states or health gate logic

## Update System

No update system changes required — this is a purely internal refactor. No new config keys, dependencies, or deployment steps. The renamed Redis keys do not exist (`recovery:active` and `worker:recovering` are transient TTL keys that will expire naturally).

## Agent Integration

No agent integration required — reflections are internal scheduled functions. The agent does not call them directly via MCP tools. No `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/worker-hibernation.md` to reference `circuit-health-gate` and `session-recovery-drip` instead of the old names
- [ ] Update `docs/features/sustainable-self-healing.md` to reference `circuit-health-gate` instead of `api-health-gate` and `recovery-drip`
- [ ] Add entry to `docs/features/README.md` if either feature doc is new (check current index)

## Success Criteria

- [ ] `config/reflections.yaml` has exactly 31 reflections (was 33)
- [ ] `api-health-gate`, `worker-health-gate`, `recovery-drip`, `session-resume-drip` are gone from YAML and Python
- [ ] `circuit-health-gate` and `session-recovery-drip` exist in `config/reflections.yaml` and `agent/sustainability.py`
- [ ] All 8 renamed reflections appear under their new names in `config/reflections.yaml`
- [ ] No old reflection names appear in log strings, Redis key values, test fixtures, or Python callables
- [ ] `agent/hibernation.py` deleted; all content in `agent/sustainability.py`
- [ ] `pytest tests/unit/test_sustainability.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_package.py` passes
- [ ] `tests/unit/test_hibernation.py` deleted (content replaced)
- [ ] `python -m ruff check . && python -m ruff format --check .` passes

## Team Orchestration

### Team Members

- **Builder (sustainability)**
  - Name: sustainability-builder
  - Role: Merge health gates, merge drips, update reflections.yaml, delete hibernation.py, update all tests
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify YAML count, test suite, no old names in codebase
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Merge and rename callables in agent/sustainability.py
- **Task ID**: build-sustainability
- **Depends On**: none
- **Validates**: `tests/unit/test_sustainability.py`
- **Informed By**: Technical Approach above
- **Assigned To**: sustainability-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `circuit_health_gate()` function that reads circuit once, manages both `queue_paused` and `worker:hibernating` flags atomically, sends hibernation notification on state transitions
- Add `session_recovery_drip()` function that checks `recovery:active OR worker:recovering`, drips `paused_circuit` sessions first then `paused` FIFO, clears both flags when both queues empty
- Copy `send_hibernation_notification()` from `agent/hibernation.py` into `agent/sustainability.py`
- Update module docstring to reflect new functions
- Update all log prefix strings: `[api-health-gate]` → `[circuit-health-gate]`, `[recovery-drip]` → `[session-recovery-drip]`

### 2. Update config/reflections.yaml
- **Task ID**: build-yaml
- **Depends On**: build-sustainability
- **Validates**: YAML count = 31
- **Assigned To**: sustainability-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `api-health-gate` + `worker-health-gate` entries with single `circuit-health-gate` entry (callable: `agent.sustainability.circuit_health_gate`, interval: 60, priority: high)
- Replace `recovery-drip` + `session-resume-drip` entries with single `session-recovery-drip` entry (callable: `agent.sustainability.session_recovery_drip`, interval: 30, priority: high)
- Update names for all 8 renames per the rename table in Technical Approach

### 3. Delete agent/hibernation.py and update tests
- **Task ID**: build-cleanup
- **Depends On**: build-yaml
- **Validates**: `pytest tests/unit/test_sustainability.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_package.py`
- **Assigned To**: sustainability-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `agent/hibernation.py`
- Delete `tests/unit/test_hibernation.py`
- Update `tests/unit/test_sustainability.py`: add `circuit_health_gate` tests (OPEN path, CLOSED path, not-registered path, combined flag management, notification guard); add `session_recovery_drip` tests (paused_circuit-first priority, both-empty clears both flags, flag-not-set no-op)
- Update `tests/unit/test_reflection_scheduler.py`: replace `worker-health-gate` with `circuit-health-gate`, `session-resume-drip` with `session-recovery-drip`
- Scan `tests/unit/test_reflections_package.py` for any hardcoded reflection name strings; update if found

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-cleanup
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `config/reflections.yaml` has exactly 31 entries
- Verify no references to `api-health-gate`, `worker-health-gate`, `recovery-drip`, `session-resume-drip` anywhere in codebase (grep)
- Run `pytest tests/unit/test_sustainability.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_package.py -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Confirm `agent/hibernation.py` and `tests/unit/test_hibernation.py` are deleted

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reflection count | `python -c "import yaml; r=yaml.safe_load(open('config/reflections.yaml')); print(len(r['reflections']))"` | output contains 31 |
| No old names | `grep -r "api-health-gate\|worker-health-gate\|recovery-drip\|session-resume-drip" config/ agent/ tests/ reflections/ --include="*.py" --include="*.yaml"` | exit code 1 |
| Tests pass | `pytest tests/unit/test_sustainability.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_package.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| hibernation deleted | `test ! -f agent/hibernation.py` | exit code 0 |
| No hibernation imports | `grep -r "from agent.hibernation" . --include="*.py"` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Archaeologist | `agent_session_queue.py:2549` imports `send_hibernation_notification` from `agent.hibernation` — deleting `agent/hibernation.py` without updating this line causes `ModuleNotFoundError` at runtime on circuit-open errors | Add to Task 1: update import to `from agent.sustainability import send_hibernation_notification`; add grep scan to Task 4 and Verification | Change `agent/agent_session_queue.py:2549` from `from agent.hibernation import send_hibernation_notification` to `from agent.sustainability import send_hibernation_notification` — function signature unchanged: `(event: str, project_key: str | None = None)` |
| CONCERN | Operator, Skeptic | Reflection count claim is stale — plan says 24 (was 26) but current YAML has 33 entries; merge yields 31, not 24; Task 4 validator will fail its own check | Update success criteria, Task 4, and Verification table to expect 31 (= 33 − 4 + 2) | Lines 240, 285, 312, 322–323: replace all `24` with `31` and `was 26` with `was 33` |
| CONCERN | Adversary, Skeptic | `circuit_health_gate` design omits that `agent_session_queue.py:2549` is a second independent caller of `send_hibernation_notification("hibernating")` — plan says notification is "absorbed from hibernation.py" but does not name this call site | Add explicit mention of `agent_session_queue.py` caller to Step 1 task description | In Step 1, add: "Update `agent/agent_session_queue.py:2549`: `from agent.hibernation import send_hibernation_notification` → `from agent.sustainability import send_hibernation_notification`" |
| CONCERN | Skeptic | `test_reflection_scheduler.py` lines 49 and 506 hardcode `"health-check"` — rename to `session-liveness-check` will break these assertions; missing from Test Impact checklist | Add to Test Impact: `test_reflection_scheduler.py` lines 49 and 506 — UPDATE: `"health-check"` → `"session-liveness-check"` | Line 49: `assert "health-check" in all_names` → `assert "session-liveness-check" in all_names`; line 506: update `expected` set |
| NIT | Simplifier | `health-digest` lacks a subject — violates the `{subject}-{verb}` standard the plan itself establishes; `system-health-digest` would be more consistent | Optional: rename to `system-health-digest` | No blocker — `health-digest` is still an improvement |

---

## Open Questions

None — scope is fully defined by the issue and confirmed by freshness check.
