---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1958
last_comment_id:
---

# Fix stale `AgentSession.create_pm` test references

## Problem

During the PM/Dev-role-merge (issue #1691/#1900 era) the session-creation API was
consolidated: `AgentSession.create_pm(...)` and the `is_pm` property were removed in
favor of `AgentSession.create_eng(...)` and `is_eng`. Eighteen tests across five files
were never migrated and still call the removed classmethod.

**Current behavior:**
Every affected test fails at construction time:
```
AttributeError: type object 'AgentSession' has no attribute 'create_pm'. Did you mean: 'create'?
```
Two tests additionally reference the removed `is_pm` property, and one integration test
filters sessions on the retired `session_type == "pm"` value — so even a mechanical
`create_pm` → `create_eng` rename leaves three residual failures behind.

**Desired outcome:**
All 18 tests construct sessions via `AgentSession.create_eng(...)`, assert against the
current `is_eng` / `session_type == "eng"` surface, and pass on `main`.

## Freshness Check

**Baseline commit:** `30bfab66`
**Issue filed at:** 2026-07-09T04:41:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py` — `create_pm` claimed removed — still absent (grep returns no match).
- `models/agent_session.py:1529` — `create_eng` is the replacement — confirmed; keyword signature is identical to the old `create_pm` (`session_id`, `project_key`, `working_dir`, `chat_id`, `telegram_message_id`, `message_text`, `sender_name`, `sender_id`, `chat_title`).
- `models/agent_session.py:1436` — `is_eng` property exists; no `is_pm` property exists anywhere.
- 24 `AgentSession.create_pm(...)` call sites confirmed across the 5 named files (error_boundaries 5, nudge_loop 3, queue_isolation 8, session_lifecycle 7, sdlc_session_ensure 1) = 18 test functions.

**Cited sibling issues/PRs re-checked:**
- #1691 / #1900 (role merge) — historical context, already merged; no re-check needed.

**Commits on main since issue was filed (touching referenced files):**
- `0f33567e` SDLC issue ownership lock — added 10 lines to `models/agent_session.py`, unrelated to `create_pm`. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Recon surfaced two drift items beyond the pure rename (documented in the issue's Recon Summary): the `is_pm` assertions and the `session_type == "pm"` filter. Both are folded into scope below.

## Prior Art

No prior issues or PRs found attempting to migrate these specific call sites. The API
removal itself landed in the role-merge work (#1691/#1900); this issue is the follow-up
test-drift cleanup that the merge left behind.

## Data Flow

Not applicable — this change only edits test-side construction calls and assertions. No
production data flow is touched.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The full test suite already
runs locally.

## Solution

### Key Elements

- **Mechanical rename**: replace all 24 `AgentSession.create_pm(...)` call sites with
  `AgentSession.create_eng(...)`. The keyword signature is identical, so no argument
  changes are needed.
- **`is_pm` → `is_eng`**: update the two `assert session.is_pm` lines in
  `test_session_lifecycle.py` (the property no longer exists).
- **`session_type` filter**: update the `session_type == "pm"` comparison in
  `test_sdlc_session_ensure_integration.py` to `"eng"`, plus the cosmetic "PM session"
  docstring/comment references in the same file.

### Technical Approach

- Confirm `create_eng` is the correct replacement (not `create`) because the call sites
  pass Telegram-shaped kwargs (`telegram_message_id`, `sender_name`, `chat_title`), which
  is exactly what `create_eng` / `_create_session_with_telegram` expects.
- Do NOT touch `tests/integration/test_steering.py::_create_pm_session` — it is a local
  helper using the direct `AgentSession(...)` constructor with `session_type="eng"`, not
  the removed classmethod.
- Do NOT touch `tests/unit/test_pm_session_factory.py:72` — it asserts
  `not hasattr(AgentSession, "create_pm")` and must stay green.
- After edits, run the five files to confirm green.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. This change edits test call sites and assertions only;
  it adds no `try/except` blocks.

### Empty/Invalid Input Handling
- No new or modified production functions. The edited tests already exercise the session
  lifecycle (error status, empty-output nudge, queue isolation) against the real
  `create_eng` path, so failure-path coverage is preserved, not added.

### Error State Rendering
- No user-visible output changes. `test_error_boundaries.py` already asserts the
  error-status rendering path; it will now exercise it through `create_eng`.

## Test Impact

- [ ] `tests/e2e/test_error_boundaries.py` (5 call sites, `TestSessionErrorIsolation` — 3 tests) — UPDATE: `create_pm` → `create_eng`.
- [ ] `tests/e2e/test_nudge_loop.py` (3 call sites, `TestNudgeLoopOutcomes` — 3 tests) — UPDATE: `create_pm` → `create_eng`.
- [ ] `tests/e2e/test_queue_isolation.py` (8 call sites, `TestPerChatQueueIsolation` — 4 tests) — UPDATE: `create_pm` → `create_eng`.
- [ ] `tests/e2e/test_session_lifecycle.py` (7 call sites, 7 tests) — UPDATE: `create_pm` → `create_eng` AND `assert session.is_pm` → `assert session.is_eng` at lines 45 and 68.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` (1 call site, 1 test) — UPDATE: `create_pm` → `create_eng`, `session_type == "pm"` → `"eng"` (line 104), and "PM session" docstring/comment references (lines 8, 55).
- [ ] `tests/integration/test_steering.py::_create_pm_session` — NO CHANGE: local constructor helper, already on the current API.
- [ ] `tests/unit/test_pm_session_factory.py::test_create_pm_does_not_exist` — NO CHANGE: guard test asserting `create_pm` stays removed; must remain green.

## Rabbit Holes

- Do not "improve" or refactor the affected tests beyond the API migration. The goal is
  restoring green, not rewriting test logic.
- Do not chase every string containing "pm" — `_create_pm_session` (a helper name) and
  historical comments are not defects.
- Do not add a compatibility shim / alias for `create_pm` on the model. The removal is
  intentional and guarded by a unit test; re-adding it would break that guard.

## Risks

### Risk 1: Wrong replacement factory (`create` vs `create_eng`)
**Impact:** Using bare `create(...)` would skip the Telegram-session setup that
`_create_session_with_telegram` performs, silently changing test semantics.
**Mitigation:** All call sites pass Telegram-shaped kwargs; `create_eng` is the exact
match. Recon confirmed the signatures are identical. Verification runs the full affected
suite.

### Risk 2: Residual `is_pm` / `session_type == "pm"` references left behind
**Impact:** Tests still error/false-negative after the rename.
**Mitigation:** Explicitly enumerated in Test Impact and enforced by a Verification
anti-criterion (`is_pm` grep count == 0 across the affected files).

## Race Conditions

No race conditions identified — all edits are synchronous test-side changes with no
concurrency or shared mutable state.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a test-only change with no new dependencies,
config, or migration steps.

## Agent Integration

No agent integration required — this change touches only test files. No MCP surface,
`.mcp.json` entry, or bridge import is affected.

## Documentation

No documentation changes needed — this is a test-suite drift fix that migrates test call
sites to the current `create_eng` API with no user-facing, feature, or public-interface
change. The session-creation API this restores compatibility with is already documented
in `docs/features/eng-session-architecture.md`.

## Success Criteria

- [x] All 24 `create_pm` call sites across the 5 files replaced with `create_eng`.
- [x] `is_pm` assertions in `test_session_lifecycle.py` replaced with `is_eng`.
- [x] `session_type == "pm"` filter in `test_sdlc_session_ensure_integration.py` replaced with `"eng"`.
- [x] The 18 previously-failing tests pass.
- [x] `tests/unit/test_pm_session_factory.py::test_create_pm_does_not_exist` still passes (guard intact).
- [x] Tests pass (`/do-test`).
- [x] Format clean (`python -m ruff format`).

## Team Orchestration

### Team Members

- **Builder (test-migration)**
  - Name: test-migrator
  - Role: Migrate all `create_pm` call sites and residual `is_pm` / `session_type` references to the current API across the 5 files.
  - Agent Type: builder
  - Resume: true

- **Validator (test-migration)**
  - Name: test-validator
  - Role: Confirm the 5 files pass and the guard test stays green.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Migrate test call sites and assertions
- **Task ID**: build-test-migration
- **Depends On**: none
- **Validates**: tests/e2e/test_error_boundaries.py, tests/e2e/test_nudge_loop.py, tests/e2e/test_queue_isolation.py, tests/e2e/test_session_lifecycle.py, tests/integration/test_sdlc_session_ensure_integration.py
- **Assigned To**: test-migrator
- **Agent Type**: builder
- **Parallel**: false
- Replace every `AgentSession.create_pm(` with `AgentSession.create_eng(` in the 5 files (24 sites).
- In `test_session_lifecycle.py`, change `assert session.is_pm` → `assert session.is_eng` (lines 45, 68).
- In `test_sdlc_session_ensure_integration.py`, change `session_type == "pm"` → `"eng"` (line 104) and update the "PM session" docstring/comment wording (lines 8, 55).
- Do NOT touch `test_steering.py::_create_pm_session` or `test_pm_session_factory.py`.
- Run `python -m ruff format` on changed files.

### 2. Validate
- **Task ID**: validate-test-migration
- **Depends On**: build-test-migration
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the 5 affected files plus the guard test; confirm all green.
- Confirm no residual `create_pm(` / `is_pm` / `session_type == "pm"` in the 5 files.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Affected e2e tests pass | `pytest tests/e2e/test_error_boundaries.py tests/e2e/test_nudge_loop.py tests/e2e/test_queue_isolation.py tests/e2e/test_session_lifecycle.py -q -n0` | exit code 0 |
| Affected integration test passes | `pytest tests/integration/test_sdlc_session_ensure_integration.py::test_bridge_short_circuit_produces_no_duplicate -q -n0` | exit code 0 |
| Guard test still green | `pytest tests/unit/test_pm_session_factory.py::TestFactoryMethodsExist::test_create_pm_does_not_exist -q -n0` | exit code 0 |
| No `create_pm(` calls remain | `grep -rn 'AgentSession.create_pm(' tests/e2e/test_error_boundaries.py tests/e2e/test_nudge_loop.py tests/e2e/test_queue_isolation.py tests/e2e/test_session_lifecycle.py tests/integration/test_sdlc_session_ensure_integration.py` | exit code 1 |
| No `is_pm` references remain | `grep -rc 'is_pm' tests/e2e/test_session_lifecycle.py` | match count == 0 |
| Format clean | `python -m ruff format --check tests/e2e/test_error_boundaries.py tests/e2e/test_nudge_loop.py tests/e2e/test_queue_isolation.py tests/e2e/test_session_lifecycle.py tests/integration/test_sdlc_session_ensure_integration.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Nothing blocking. The replacement factory (`create_eng`), the residual-assertion fixes,
and the out-of-scope exclusions are all evidence-backed from recon. Ready for critique.
