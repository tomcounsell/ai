---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1835
last_comment_id:
---

# Sentry Orphan-Noise Filter for Popoto `VALOR-S`

## Problem

Sentry issue `VALOR-S` has accumulated **68,487 events** (Apr 9 to present) from a single Popoto `logger.error()`:

> one or more redis keys points to missing objects. Debug with Model.query.keys(clean=True)

This fires from `popoto/models/query.py` whenever `AgentSession.query.all()` (or any model query) hits an orphaned index entry — a Redis SET member pointing at an expired/deleted hash. Three prior fixes (#860, #1459, #1874) reduced the orphan count but cannot eliminate transient orphans inherent to the Popoto+TTL design (Redis SETs have no per-member TTL). The remaining noise is benign-transient (the `if redis_hash` guard in `get_many_objects` already silently skips ghosts), but it floods Sentry at `error` level and drowns out real signal.

**Current behavior:**
- The worker process polls `AgentSession.query.all()` in a tight loop, hitting orphan entries and emitting `logger.error()` on every poll cycle
- Sentry's default `LoggingIntegration` captures every `logger.error()` as an event
- The worker initializes Sentry with `before_send=None` (no filtering at all)
- The bridge has `_sentry_before_send` but it only filters hibernation events, not the orphan message

**Desired outcome:**
- The orphan-keys diagnostic no longer reaches Sentry as an `error` event
- A unit test asserts the filter drops the orphan-keys message
- The underlying orphan-index churn is confirmed benign-transient (already covered by existing cleanup infrastructure)

## Freshness Check

**Baseline commit:** `b6a3efa7`
**Issue filed at:** 2026-07-01T07:12:32Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/telegram_bridge.py:55-71` — `_sentry_before_send` still only handles hibernation — still holds
- `worker/__main__.py:1055` — `configure_sentry("worker", before_send=None)` still has no filter — still holds
- `monitoring/sentry_config.py:59-65` — `sentry_sdk.init()` still uses default integrations — still holds
- `popoto/models/query.py:2677,3068` — error message still emitted at `logger.error()` level — still holds

**Cited sibling issues/PRs re-checked:**
- #860 — closed Apr 10 (reflection wiring fix)
- #1459 — closed May 26 (clean_indexes addition)
- #1874 — merged Jul 2 (ghost_reconcile.py). This merged one day AFTER #1835 was filed. It adds rate-limited reconcile-on-read but does NOT address the Sentry noise — the filter approach is still needed.

**Commits on main since issue was filed (touching referenced files):**
- `6e846f0d` (Jul 2) — added `models/ghost_reconcile.py` and reconcile-on-read. Partially addresses orphan churn but does not filter Sentry noise. Does not change the plan's premise.
- `d9cb76b1` (Jul 2) — session lifecycle notification gaps. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** #1874's `ghost_reconcile.py` is an additional prior fix to document. It reduces orphan creation rate but does not eliminate transient orphans, so the Sentry filter is still needed.

## Prior Art

- **#860** (closed Apr 10): `popoto-index-cleanup` reflection registered in YAML but never dispatched. Fixed by wiring into `ReflectionRunner`. Reduced events from 82/14d but root cause persisted.
- **#1459** (closed May 26, commit `2c45d4fa`): Added `AgentSession.clean_indexes()` and `Memory.clean_indexes()` to worker startup and cleanup reflection. Covers class set (`$Idx:`) orphans, not just field indexes (`$IndexF:`). Reduced 28k to lower but 68k total still accumulated over time.
- **#1874** (merged Jul 2): Added `models/ghost_reconcile.py` — rate-limited reconcile-on-read from hot paths (dedup lookups, subject-coalescing). Most recent orphan-reduction effort. Reduces orphan lifetime but transients remain.
- `docs/features/popoto-index-hygiene.md` documents the full cleanup infrastructure.
- `docs/plans/done/popoto-redis-hygiene.md` is the completed plan from the #1459 workstream.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #860 | Wired the `popoto-index-cleanup` reflection into `ReflectionRunner` | Fixed the dispatch wiring but orphans still accumulated between daily sweeps |
| #1459 | Added `clean_indexes()` to worker startup and cleanup reflection | Reduced orphan volume but transients between sweeps still fire the error. The error fires on EVERY poll cycle that hits an orphan, not just once per orphan. |
| #1874 | Added reconcile-on-read from hot paths | Reduces orphan lifetime but cannot prevent the first hit after TTL expiry. Rate-limited (60s min interval) so rapid polls still hit orphans. |

**Root cause pattern:** All three prior fixes tried to eliminate orphan entries faster. But the orphan lifecycle is inherent to Popoto+TTL: Redis SETs have no per-member TTL, so hash expiry always leaves a ghost until the next sweep. The error fires on every query that touches a ghost, not just once. The remaining noise after three reduction attempts is benign-transient — the fix should filter the noise, not chase the last orphan.

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

- **Shared orphan-noise filter function** in `monitoring/sentry_config.py`: a `before_send` compatible callable that drops Sentry events matching the Popoto orphan-keys message
- **Bridge filter composition**: update `_sentry_before_send` in `bridge/telegram_bridge.py` to also call the orphan-noise filter after the existing hibernation check
- **Worker filter**: pass the orphan-noise filter as `before_send` to `configure_sentry("worker", ...)` in `worker/__main__.py`

### Flow

Worker/bridge emits `logger.error("one or more redis keys...")` → Sentry `LoggingIntegration` captures it as event → `before_send` hook checks event for orphan message → match: drop (return `None`) → no Sentry event created

### Technical Approach

1. **Add `_ORPHAN_NOISE_SUBSTRING` and `drop_orphan_noise(event, hint)` to `monitoring/sentry_config.py`**:
   - The substring `"one or more redis keys points to missing objects"` is the match target
   - Check both `event.get("logentry", {}).get("formatted", "")` and `event.get("logentry", {}).get("message", "")` — Sentry encodes logged errors as `logentry` objects
   - Also check `event.get("message", "")` as a fallback for non-`logentry` event shapes
   - Return `None` on match (drop event), return `event` unchanged on no match
   - Never raise — wrap in try/except so a filter crash never suppresses real errors (same safety-net pattern as the hibernation filter)

2. **Update `_sentry_before_send` in `bridge/telegram_bridge.py`**:
   - After the hibernation check, call `drop_orphan_noise(event, hint)`
   - If it returns `None`, return `None` (drop)
   - Otherwise return `event` (pass through)
   - Import `drop_orphan_noise` from `monitoring.sentry_config`

3. **Update worker Sentry init in `worker/__main__.py`**:
   - Change `configure_sentry("worker", before_send=None)` to `configure_sentry("worker", before_send=drop_orphan_noise)`
   - Import `drop_orphan_noise` from `monitoring.sentry_config`

4. **Confirm orphan churn is benign-transient**:
   - The `if redis_hash` guard in `popoto/models/query.py:get_many_objects` (line 2685) already silently skips empty hashes — no stale data is ever returned
   - The existing cleanup infrastructure (`agent-session-cleanup` reflection, `ghost_reconcile.py`, worker startup `clean_indexes()`) continues to reduce orphan count
   - No new cleanup code is needed — this plan only addresses the Sentry noise

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `drop_orphan_noise` filter wraps its matching logic in try/except — a filter crash must never suppress real errors. Test: verify event passes through when the matching logic raises.

### Empty/Invalid Input Handling
- [ ] `drop_orphan_noise` handles events with no `logentry` key, no `message` key, or empty strings — all must pass through unchanged
- [ ] `drop_orphan_noise` handles `hint=None` without crashing

### Error State Rendering
- [ ] Not applicable — this is a Sentry filtering change, no user-visible output

## Test Impact

- [ ] `tests/unit/test_sentry_hibernation_filter.py` — UPDATE: add tests for orphan message filtering (both bridge combined filter and standalone `drop_orphan_noise`)
- [ ] `tests/unit/test_worker_sentry_init.py` — UPDATE: the test `test_configure_sentry_inits_when_dsn_set_and_no_guard` currently asserts `kwargs["before_send"] is None` for the worker. Update to assert `before_send` is the `drop_orphan_noise` function.

## Rabbit Holes

- **Modifying Popoto's source to downgrade the logger level**: Popoto is a pip-installed library. Monkey-patching its logger would be fragile and break on upgrades. The Sentry `before_send` filter is the correct layer — it intercepts after Popoto logs but before Sentry captures.
- **Adding a custom `logging.Filter` to the `POPOTO.Query` logger**: This would prevent the log line from reaching Sentry's `LoggingIntegration`, but it would also hide the diagnostic from bridge/worker logs entirely. The `before_send` approach preserves log visibility while dropping Sentry events.
- **Attempting to eliminate all transient orphans**: Three prior fixes tried this. The orphan lifecycle is inherent to Popoto+TTL. The filter approach accepts this and addresses the noise, not the churn.

## Risks

### Risk 1: Over-filtering — dropping a real error that happens to contain the substring
**Impact:** A genuine non-orphan error containing the substring "one or more redis keys points to missing objects" would be silently dropped from Sentry
**Mitigation:** The substring is highly specific (47 chars, Popoto-specific diagnostic). It is only emitted from `popoto/models/query.py:2677` and `:3068`. No other code path produces this exact message. The filter matches on the full substring, not just "redis keys".

### Risk 2: Worker before_send change breaks existing test assertions
**Impact:** `tests/unit/test_worker_sentry_init.py::test_configure_sentry_inits_when_dsn_set_and_no_guard` asserts `before_send is None` for the worker
**Mitigation:** Update the test to assert `before_send is drop_orphan_noise` — this is a planned, straightforward test update.

## Race Conditions

No race conditions identified — the `before_send` filter is a pure function that inspects event metadata. It does not access shared mutable state or perform I/O.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal. The `before_send` filter is applied at process startup in `monitoring/sentry_config.py` and `worker/__main__.py`, both of which are already on the update path. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a Sentry filtering change internal to the bridge and worker processes. The agent does not invoke `_sentry_before_send` or `drop_orphan_noise` directly. The filter runs automatically as part of Sentry's event pipeline.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/popoto-index-hygiene.md` with a section documenting the Sentry orphan-noise filter and why it is needed despite the cleanup infrastructure
- [ ] Add entry to `docs/features/README.md` index table if a new doc is created (likely just updating the existing popoto-index-hygiene entry)

### Inline Documentation
- [ ] Docstring on `drop_orphan_noise` explaining what it filters and why
- [ ] Comment in `worker/__main__.py` explaining why the worker now passes a `before_send` filter

## Success Criteria

- [ ] `drop_orphan_noise` function exists in `monitoring/sentry_config.py` and drops events containing the Popoto orphan substring
- [ ] Bridge `_sentry_before_send` drops orphan-message events (in addition to hibernation events)
- [ ] Worker passes `drop_orphan_noise` as `before_send` to `configure_sentry`
- [ ] Unit test asserts `_sentry_before_send` drops the orphan-keys message
- [ ] Unit test asserts `drop_orphan_noise` passes through non-orphan events
- [ ] Unit test asserts `drop_orphan_noise` never raises (safety net)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -c "drop_orphan_noise" worker/__main__.py` confirms worker wires the filter
- [ ] `grep -c "drop_orphan_noise" bridge/telegram_bridge.py` confirms bridge composes with the filter

## Team Orchestration

### Team Members

- **Builder (sentry-filter)**
  - Name: sentry-filter-builder
  - Role: Implement the orphan-noise filter, update bridge and worker, write tests
  - Agent Type: builder
  - Resume: true

- **Validator (sentry-filter)**
  - Name: sentry-filter-validator
  - Role: Verify filter logic, test coverage, and that existing tests still pass
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)

## Step by Step Tasks

### 1. Implement orphan-noise filter and wire it into bridge + worker
- **Task ID**: build-sentry-filter
- **Depends On**: none
- **Validates**: tests/unit/test_sentry_hibernation_filter.py, tests/unit/test_worker_sentry_init.py
- **Assigned To**: sentry-filter-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `drop_orphan_noise(event, hint)` function to `monitoring/sentry_config.py` with the orphan substring match and try/except safety net
- Update `_sentry_before_send` in `bridge/telegram_bridge.py` to call `drop_orphan_noise` after the hibernation check
- Update `worker/__main__.py` to pass `before_send=drop_orphan_noise` to `configure_sentry`
- Add unit tests in `tests/unit/test_sentry_hibernation_filter.py` for: orphan message dropped, non-orphan message passed through, filter never raises
- Update `tests/unit/test_worker_sentry_init.py` to assert worker `before_send` is `drop_orphan_noise`

### 2. Validate implementation
- **Task ID**: validate-sentry-filter
- **Depends On**: build-sentry-filter
- **Assigned To**: sentry-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `drop_orphan_noise` drops events with the orphan substring in `logentry.formatted`, `logentry.message`, and `message` fields
- Verify non-orphan events pass through unchanged
- Verify the bridge's combined filter handles both hibernation and orphan noise
- Run `pytest tests/unit/test_sentry_hibernation_filter.py tests/unit/test_worker_sentry_init.py -v`
- Run `python -m ruff check monitoring/sentry_config.py bridge/telegram_bridge.py worker/__main__.py`
- Report pass/fail status

### 3. Documentation
- **Task ID**: document-filter
- **Depends On**: validate-sentry-filter
- **Assigned To**: sentry-filter-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/popoto-index-hygiene.md` with a section on the Sentry orphan-noise filter
- Add docstring to `drop_orphan_noise` explaining what it filters and why

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-filter
- **Assigned To**: sentry-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sentry_hibernation_filter.py tests/unit/test_worker_sentry_init.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/sentry_config.py bridge/telegram_bridge.py worker/__main__.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/sentry_config.py bridge/telegram_bridge.py worker/__main__.py` | exit code 0 |
| Filter exists | `grep -c "drop_orphan_noise" monitoring/sentry_config.py` | output > 0 |
| Worker wires filter | `grep -c "drop_orphan_noise" worker/__main__.py` | output > 0 |
| Bridge composes filter | `grep -c "drop_orphan_noise" bridge/telegram_bridge.py` | output > 0 |
| No orphan events pass | `grep -c "one or more redis keys" monitoring/sentry_config.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

1. Should the filter also check `event.get("exception", ...)` for the orphan message? The error is a `logger.error()` call (captured as a `logentry` event by `LoggingIntegration`), not a raised exception, so this is likely unnecessary — but worth confirming during build.