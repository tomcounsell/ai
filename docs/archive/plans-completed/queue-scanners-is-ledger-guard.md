---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2044
last_comment_id:
---

# Two remaining status=running scanners honor is_ledger (agent_session_queue.py)

## Problem

PR #2043 (issue #2042) introduced `AgentSession.is_ledger` and guarded five worker
surfaces so a live worker never requeues, finalizes, or picks up a CLI-created
`sdlc-local-{N}` pipeline-tracking anchor. Those anchors sit at `status="running"`
for the entire lifetime of a local `/do-sdlc` pipeline run by design — they are
passive records, not executed sessions (no subprocess, no worker task, no
transcript).

Two more `AgentSession.query.filter(status="running")` scanners in
`agent/agent_session_queue.py` were found during PR #2043's build validation but
were judged out of that plan's explicit audit scope (worker requeue/pickup only)
because neither is automatically destructive the way the five guarded surfaces were.

**Current behavior:**
1. `_check_restart_flag()` (`agent/agent_session_queue.py:1243`) counts every
   `status="running"` session — including ledger anchors — and defers the graceful
   worker restart while any exist. Because a ledger anchor stays `running` for a full
   local pipeline run, a flag-based graceful restart is deferred indefinitely
   whenever any local SDLC pipeline is mid-flight. Availability wrinkle, not data loss.
2. `_cli_flush_stuck()` (`agent/agent_session_queue.py:2515`) is a manual operator
   CLI command that recovers any `status="running"` session whose `worker_key` has no
   live worker task in `_active_workers`. A ledger anchor never has a live worker task,
   so an operator running this while a local `/do-sdlc` pipeline is active would
   recover/finalize the anchor — same bug class as #2042, but requires deliberate
   manual invocation.

**Desired outcome:**
Both loops skip `is_ledger` rows uniformly, using the same guard already applied at
the six sites in `session_health.py` / `session_pickup.py`. A ledger anchor no longer
defers graceful worker restarts, and `_cli_flush_stuck()` never finalizes an anchor.

## Freshness Check

**Baseline commit:** 2ea185c2bea8cdab1e5e7fdf18271307ad8fc035
**Issue filed at:** 2026-07-12T12:43:02Z
**Disposition:** Unchanged

**File:line references re-verified (read directly at baseline HEAD):**
- `agent/agent_session_queue.py:1243` — `_check_restart_flag()` runs
  `AgentSession.query.filter(status="running")`, returns False if any running — still
  holds, no `is_ledger` guard present.
- `agent/agent_session_queue.py:2515` — `_cli_flush_stuck()` runs the same query and
  recovers sessions whose `worker_key` has no live task — still holds, no guard.
- `agent/session_health.py:46` — canonical `_is_ledger(entry)` helper
  (`_truthy(getattr(entry, "is_ledger", False))`) — present, applied at 4 sites in
  `session_health.py` and 2 in `session_pickup.py`.
- `models/agent_session.py:359` — `is_ledger = Field(default=False)` — present.

**Cited sibling issues/PRs re-checked:**
- #2042 — CLOSED 2026-07-12T13:44:34Z; resolution shipped in PR #2043.
- PR #2043 — MERGED 2026-07-12T13:44:33Z; the `is_ledger` field + guard pattern this
  plan reuses.

**Commits on main since issue was filed (touching `agent/agent_session_queue.py`):**
- `127edc15` Widen never-started grace + subprocess-hang probe (#2069) — irrelevant to
  the two loops; neither `_check_restart_flag` nor `_cli_flush_stuck` changed.
- `e1ec8695` Centralize magic timeout literals into config/settings.py (#2047) —
  irrelevant to the two loops.

**Active plans in `docs/plans/` overlapping this area:** none. (`worker-loop-corrupted-pop-crash.md`
touches the worker pop path, not these two scanner loops.)

**Notes:** Both target loops confirmed present and unguarded at baseline HEAD. Line
numbers accurate at plan time (read directly).

## Prior Art

- **PR #2043 / issue #2042**: "Non-executable-ledger flag for CLI-created sdlc-local
  anchors" — added `AgentSession.is_ledger`, the `_is_ledger`/`_truthy` guard helpers,
  and applied the skip at five worker surfaces (`session_health.py` startup-recovery,
  health-running, health-pending, tool-timeout; `session_pickup.py` two pop sites).
  Merged successfully. This issue is the explicit follow-up for the two scanners that
  PR #2043 deliberately left out of scope. The guard pattern is proven — this plan
  extends it, it does not invent anything.

No prior failed fixes — this is the first fix for these two specific loops.

## Data Flow

Single-file change; no multi-component data flow. The guard is a per-row filter inside
two existing loops that already iterate `AgentSession.query.filter(status="running")`.
Each iteration gains one early-`continue` when the row is a ledger anchor.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The change is a two-site guard mirroring an already-shipped pattern. Bottleneck is
review confirmation that both loops skip anchors correctly, not coding time.

## Prerequisites

No prerequisites — the `is_ledger` field and the `_is_ledger`/`_truthy` helpers already
shipped in PR #2043. This work has no external dependencies.

## Solution

### Key Elements

- **Import the canonical guard**: bring `_is_ledger` into `agent_session_queue.py` from
  `agent.session_health` (extend the existing `from agent.session_health import (...)`
  block at line 72). Do not redefine a local copy — reuse the single source of truth.
- **Guard `_check_restart_flag()`**: filter ledger anchors out of the running count so
  they no longer defer a graceful restart.
- **Guard `_cli_flush_stuck()`**: skip ledger anchors in the recovery loop so an
  operator flush never finalizes an anchor.

### Flow

Local `/do-sdlc` running (anchor at status=running) → worker sees restart flag →
`_check_restart_flag()` filters out the anchor → count of real running sessions is 0 →
graceful restart proceeds instead of deferring indefinitely.

Operator runs flush-stuck CLI while pipeline active → `_cli_flush_stuck()` iterates
running sessions → hits anchor → `is_ledger` guard `continue`s → anchor untouched,
real orphaned sessions still recovered.

### Technical Approach

- Add `_is_ledger` to the existing `from agent.session_health import (...)` import block
  (it is already exported there; `agent_session_queue.py` re-exports many symbols from
  that module, so this is consistent with the file's existing convention).
- In `_check_restart_flag()`: change the running check to exclude ledger anchors, e.g.
  `running = [s for s in AgentSession.query.filter(status="running") if not _is_ledger(s)]`.
  Preserve the existing log message shape (count of deferring sessions).
- In `_cli_flush_stuck()`: inside the `for session in running:` loop, add an early
  `if _is_ledger(session): continue` (with a `print(...)`/skip note consistent with the
  existing `Skipping ... - worker still alive` branch) before the worker-liveness check.
- Match the `# ... (is_ledger, #2042)` comment/log convention used at the six existing
  guard sites for grep-ability.

## Failure Path Test Strategy

### Exception Handling Coverage
- No new exception handlers introduced. `_check_restart_flag()` already handles stale/
  malformed flags; that logic is untouched. State: the guard is a pure list filter /
  early-continue with no try/except added.

### Empty/Invalid Input Handling
- The guard reuses `_truthy(getattr(entry, "is_ledger", False))`, which already coerces
  missing attribute, `None`, `False`, and the Popoto string `"False"` to falsy (proven
  by the existing `_truthy` test class in `tests/unit/test_agent_session_queue.py:388`).
  A legacy row predating the field is treated as a normal executable session (correct).
- Empty running set: both loops already handle the empty case (`_cli_flush_stuck` prints
  "No running sessions found."); filtering to empty behaves identically.

### Error State Rendering
- `_cli_flush_stuck()` prints per-session disposition; the new ledger-skip branch adds a
  visible skip line so the operator sees the anchor was intentionally skipped, not
  silently ignored. Tested via captured stdout / recovery-not-called assertion.

## Test Impact

- [ ] `tests/unit/test_agent_session_status_cli.py::TestCliFlushStuck` — UPDATE: add a
  test asserting a `status="running"` session with `is_ledger=True` is skipped (recovery
  helper not called), alongside the existing dead-worker / live-worker cases.
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add a `_check_restart_flag()`
  test asserting that when the only running session is a ledger anchor, the flag check
  returns True (restart proceeds); and that a real running session still defers.

No existing test asserts the *un*-guarded behavior, so nothing needs DELETE/REPLACE —
the changes are additive guards that only affect ledger-anchor rows, which no current
test exercises for these two loops.

## Rabbit Holes

- Do NOT re-audit the five already-guarded sites from #2042 — they are done and tested.
- Do NOT add a query-level `is_ledger` index or a `filter(is_ledger=False)` Popoto query.
  The field is `Field(default=False)` with no index by design (see
  `models/agent_session.py:357`); an in-Python filter matching the six existing sites is
  the correct, consistent approach.
- Do NOT refactor `_check_restart_flag()` / `_cli_flush_stuck()` beyond the guard. Scope
  is two early-skips, nothing else.

## Risks

### Risk 1: Divergent guard helper (local copy drift)
**Impact:** If a local `_is_ledger`/`_truthy` were redefined in `agent_session_queue.py`
instead of imported, future changes to the canonical helper would silently not apply here.
**Mitigation:** Import `_is_ledger` from `agent.session_health` — single source of truth.

### Risk 2: Over-broad skip hides genuinely stuck sessions
**Impact:** If the guard matched non-ledger rows, `_cli_flush_stuck()` would stop
recovering real orphaned sessions.
**Mitigation:** The guard keys strictly on `is_ledger` truthiness via the proven `_truthy`
coercion; a dedicated test asserts a non-ledger running session with a dead worker is
still recovered.

## Race Conditions

No race conditions identified — both loops are synchronous. `_check_restart_flag()` runs
in the worker queue loop; `_cli_flush_stuck()` is a one-shot operator CLI invocation.
The guard adds no shared mutable state and no new async operations. `is_ledger` is set
once at anchor creation (`sdlc-tool session-ensure`) and never mutated, so reading it
during the scan cannot race a writer.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The two guard sites are
the complete fix for issue #2044; the five sites from #2042 are already shipped.

## Update System

No update system changes required — this is a purely internal worker-logic fix. No new
dependencies, config files, Popoto schema changes, or migrations. The `is_ledger` field
already shipped in PR #2043; no `scripts/update/` changes needed.

## Agent Integration

No agent integration required — both `_check_restart_flag()` and `_cli_flush_stuck()` are
internal worker/operator-CLI functions. No new MCP surface, no `.mcp.json` change, no
bridge import. `_cli_flush_stuck()` is already reachable via the existing
`python -m agent.agent_session_queue` flush CLI path; its interface is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/eng-session-architecture.md` — the #2042 is_ledger guard's
  canonical home is the `### sdlc-local session is_ledger non-executable flag (issue
  #2042)` section there (no separate `docs/features/sdlc-local-ledger-anchors.md` file
  exists). Add the two new guard sites (`_check_restart_flag`, `_cli_flush_stuck` in
  `agent/agent_session_queue.py`) to its guarded-surfaces table. No new feature doc is
  warranted for a two-site extension of an existing pattern.

### Inline Documentation
- [ ] Add the `# ... (is_ledger, #2042)` comment at both new guard sites, matching the six
  existing sites, so `grep 'is_ledger, #2042'` surfaces all eight uniformly.

## Success Criteria

- [ ] `_check_restart_flag()` excludes `is_ledger` rows from the running count; a lone
  ledger anchor no longer defers a graceful restart.
- [ ] `_cli_flush_stuck()` skips `is_ledger` rows and never calls the recovery helper on
  an anchor; real dead-worker sessions are still recovered.
- [ ] `_is_ledger` is imported from `agent.session_health` (no local redefinition).
- [ ] New unit tests cover both guarded loops (ledger-skipped + non-ledger-still-handled).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep -rn 'is_ledger' agent/agent_session_queue.py` shows the guard at both sites.

## Team Orchestration

### Team Members

- **Builder (queue-guard)**
  - Name: queue-guard-builder
  - Role: Add the `_is_ledger` import and the two guard sites; add unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator (queue-guard)**
  - Name: queue-guard-validator
  - Role: Verify both loops skip anchors, non-ledger sessions still handled, tests pass.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the guard at both scanner loops
- **Task ID**: build-queue-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_status_cli.py, tests/unit/test_agent_session_queue.py
- **Assigned To**: queue-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_is_ledger` to the existing `from agent.session_health import (...)` block (line 72).
- In `_check_restart_flag()` (line 1243), exclude `is_ledger` rows from the running list;
  keep the existing deferring-count log message.
- In `_cli_flush_stuck()` (line 2515), add `if _is_ledger(session): continue` (with a
  visible skip print) before the worker-liveness check.
- Add the `# ... (is_ledger, #2042)` comment at both sites.

### 2. Add unit tests
- **Task ID**: build-queue-guard-tests
- **Depends On**: build-queue-guard
- **Validates**: tests/unit/test_agent_session_status_cli.py, tests/unit/test_agent_session_queue.py
- **Assigned To**: queue-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `TestCliFlushStuck` with a ledger-anchor-skipped case (recovery helper not called)
  and confirm the existing dead-worker case still recovers.
- Add a `_check_restart_flag()` test: lone ledger anchor → returns True; real running
  session → returns False.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-queue-guard-tests
- **Assigned To**: queue-guard-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the #2042 is_ledger guard doc (or nearest worker/session-lifecycle doc) to list
  the two new guard sites among the guarded surfaces.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: queue-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two touched test modules and confirm pass.
- Confirm `grep 'is_ledger, #2042' agent/agent_session_queue.py` shows both sites.
- Verify all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard present at both sites | `grep -c 'is_ledger' agent/agent_session_queue.py` | output > 1 |
| Guard tests pass | `pytest tests/unit/test_agent_session_status_cli.py tests/unit/test_agent_session_queue.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| No local _is_ledger redefinition | `grep -c 'def _is_ledger' agent/agent_session_queue.py` | match count == 0 |

## Open Questions

None — the guard pattern, the field, and the helpers are all shipped and proven by
PR #2043. This plan is a mechanical two-site extension with clear success criteria.
