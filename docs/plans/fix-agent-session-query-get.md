---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/765
last_comment_id:
---

# Fix systemic AgentSession.query.get(string) silent failures

## Problem

Twelve call sites across the codebase invoke `AgentSession.query.get(some_string_id)`, expecting Popoto to look up a session by id. Popoto's `query.get()` does not accept a positional string — it requires `db_key=`, `redis_key=`, or full KeyField kwargs. Every call site is wrapped in `try/except` that silently swallows the resulting `AttributeError` and treats the lookup as "not found", falling through to fallback paths.

**Current behavior:**
- Worker nudge guard logs `'str' object has no attribute 'redis_key'` and silently completes sessions instead of preserving nudge state.
- Parent/child session lookups silently return None even when the session exists, causing fallback paths to fire incorrectly.
- The bug is invisible to logs and tests because exceptions are swallowed without warning.

**Desired outcome:**
- All 12 call sites use the canonical Popoto pattern and successfully resolve sessions.
- A reusable `AgentSession.get_by_id(string)` classmethod exists for raw-string lookups.
- Silent `except Exception: pass` blocks around these lookups log warnings so future regressions surface immediately.
- A test guards the lookup pattern, and a lint scan blocks new positional-string `query.get()` calls in CI.

## Prior Art

- **PR #760** ("Fix 6 broken integration tests: dead job_queue imports + AgentSession.get production bug") — fixed the surface symptom (commit 68d25ae6) and the followup nudge-guard patch (dc60529e). Did not sweep the rest of the codebase. This issue is the systemic followup.
- **`ui/data/sdlc.py:558`** — already carries an inline comment acknowledging the bug ("AgentSession.query.get() requires a Popoto key object, not a raw string"). Local fix only; never propagated.
- **Issue #617** ("Popoto ORM hygiene: refactor raw Redis ops + orphaned index cleanup reflection") — adjacent ORM hygiene work. Not the same bug.

## Spike Results

### spike-1: Verify `AgentSession.query.filter(id=string)` works with a single KeyField
- **Assumption**: Popoto's `filter(id=...)` returns matching sessions even though `id` is one of several KeyFields.
- **Method**: code-read + prototype
- **Finding**: Confirmed. Running `list(AgentSession.query.filter(id='nonexistent'))` returns `[]` without raising. The implementation in `models/agent_session.py` declares `id = AutoKeyField()` and the helper pattern works as expected.
- **Confidence**: high
- **Impact on plan**: `get_by_id(cls, agent_session_id)` can be implemented as a thin wrapper over `cls.query.filter(id=agent_session_id)` returning the first result or `None`. No secondary index needed.

## Data Flow

1. **Caller** (worker, scheduler, steer script, model method) holds an `agent_session_id` string — e.g., from a parent reference, a CLI argument, or a Redis hash field.
2. **Lookup** invokes `AgentSession.query.get(agent_session_id)` (broken) or `AgentSession.get_by_id(agent_session_id)` (canonical, this plan).
3. **Popoto** resolves the session via the KeyField filter and returns either the model instance or `None`.
4. **Caller** uses the resulting session to read state, route output, or steer execution.

The current break happens at step 2: Popoto raises `AttributeError: 'str' object has no attribute 'redis_key'`, which the surrounding `try/except` swallows, causing step 4 to misbehave with `None`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #760 (68d25ae6) | Patched the production symptom for one site | Did not search the codebase for other call sites with the same anti-pattern |
| Commit dc60529e | Patched the nudge guard | Same — local fix, not systemic |
| `ui/data/sdlc.py:558` inline comment | Documented the gotcha | Inline comments don't propagate; no test or lint enforced the lesson |

**Root cause pattern:** Each fix addressed a single instance instead of (a) creating a reusable helper, (b) sweeping all call sites, and (c) adding lint/test guardrails to prevent regressions. The silent `except Exception: pass` wrapping around every site let the bug stay invisible after the local fixes.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: New `AgentSession.get_by_id(agent_session_id: str) -> AgentSession | None` classmethod. Touches 12 call sites; all callers gain a more reliable lookup.
- **Coupling**: Decreases coupling — callers no longer need to know Popoto's KeyField mechanics.
- **Data ownership**: Unchanged.
- **Reversibility**: Fully reversible. The classmethod is additive; call-site edits revert cleanly.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a focused, mechanical sweep with one new helper, one test, and one lint rule.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`AgentSession.get_by_id(string)` classmethod**: Wraps `cls.query.filter(id=...)` and returns the first match or `None`. The single canonical entry point for raw-string lookups.
- **Call-site sweep**: Replace all 12 broken `AgentSession.query.get(positional_string)` invocations with `AgentSession.get_by_id(...)`.
- **Logging upgrade**: Replace silent `except Exception: pass` (or equivalent) around the affected lookups with `except Exception as exc: logger.warning(...)`. Preserve the fallback behavior but make failures observable.
- **Regression guard**: Add a unit test that exercises `get_by_id` (positive + negative cases) and a lint scan (pytest-based or pre-commit) that fails CI if a new `AgentSession.query.get(<positional non-kwarg>)` is introduced.

### Flow

Caller has string id → calls `AgentSession.get_by_id(id)` → receives session or None → proceeds with normal logic. No more silent AttributeErrors.

### Technical Approach

- Add `get_by_id` as a `@classmethod` on `AgentSession` in `models/agent_session.py`.
- For each of the 12 call sites, replace the broken call with `AgentSession.get_by_id(...)`.
- Where the surrounding handler is `except Exception: pass`, change it to `except Exception as exc: logger.warning("AgentSession lookup failed for %s: %s", agent_session_id, exc)`.
- Add `tests/unit/test_agent_session_lookup.py` covering both `get_by_id` paths.
- Add a regex-based lint check (either a pytest collection-time scan or a pre-commit grep hook) that fails on `AgentSession\.query\.get\(\s*[a-zA-Z_]` (positional non-kwarg arg).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Identify each `except Exception: pass` (or similar silent handler) around the 12 affected sites and convert to `logger.warning` calls. Add a test asserting the warning is logged when the lookup misses.
- [ ] No new exception handlers introduced — only existing ones modified.

### Empty/Invalid Input Handling
- [ ] `get_by_id("")` returns `None` (test case).
- [ ] `get_by_id(None)` returns `None` without raising (test case).
- [ ] Whitespace-only ids return `None` (test case).

### Error State Rendering
- [ ] Lookups that fail now log a structured warning with the id and reason. Test asserts the warning is emitted via `caplog`.

## Test Impact

- [ ] `tests/unit/test_agent_session_lookup.py` — CREATE: covers `get_by_id` positive and negative cases plus warning emission.
- [ ] `tests/integration/` — No existing integration tests assert the broken behavior; sweep is mechanical. If a test relies on the silent-fail fallback, it should be updated to assert the new logged-warning behavior.

No other existing tests are expected to break — the call-site changes preserve return semantics (session-or-None), only making the lookup actually succeed when the session exists.

## Rabbit Holes

- **Refactoring all of Popoto's query API ergonomics.** Out of scope. Issue #617 tracks broader Popoto hygiene.
- **Adding a generic `Model.get_by_id` to all models.** Tempting, but only `AgentSession` is in scope here. A generic helper can come later if other models exhibit the same pattern.
- **Rewriting the nudge guard or session lifecycle code.** This plan only fixes the lookup pattern. Behavior changes belong in their own issues.
- **Switching the silent handlers to raise instead of warn.** Out of scope — that's a behavior change with downstream implications. Logging is the minimum viable observability fix.

## Risks

### Risk 1: A `get_by_id` lookup returns the wrong session due to KeyField ambiguity
**Impact:** Wrong session is acted on, potentially routing output to the wrong chat or steering the wrong worker.
**Mitigation:** `id` is an `AutoKeyField` (UUID-like), so collisions are vanishingly unlikely. The unit test asserts that lookups by id are unique. If this is a concern, the helper can additionally assert `len(results) <= 1` and log a warning on collision.

### Risk 2: A call site relied on the silent failure behavior to work correctly
**Impact:** Fixing the lookup changes downstream behavior in unexpected ways.
**Mitigation:** Each call site is reviewed individually during the sweep. The build agent must read the surrounding context for every replacement and confirm the fallback path was a bug, not a feature.

## Race Conditions

No race conditions identified — `get_by_id` is a synchronous Redis read with no shared mutable state. The fix does not change concurrency semantics.

## No-Gos (Out of Scope)

- Refactoring Popoto's query API.
- Generic `get_by_id` helper for all Popoto models.
- Behavior changes to the nudge guard, scheduler, or steering logic beyond making the lookup work.
- Migration tooling — there is no data migration; the helper is a code-only change.

## Update System

No update system changes required — this fix is purely internal. Existing installations pick up the change on the next `git pull`.

## Agent Integration

No agent integration required — `AgentSession` is not exposed via MCP and the agent does not call `query.get()` directly. This is an internal model and infrastructure change.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` (or the closest existing AgentSession doc) with a short note on the canonical lookup pattern: "Use `AgentSession.get_by_id(string)` for raw-string lookups; `query.get()` requires Popoto key objects."
- [ ] If no AgentSession-specific feature doc exists, add a note to `docs/features/README.md` pointing to the model.

### Inline Documentation
- [ ] Docstring on `AgentSession.get_by_id` explaining when to use it vs. `query.get(redis_key=...)`.
- [ ] Remove the stale inline comment at `ui/data/sdlc.py:558` once the call site is fixed.

## Success Criteria

- [ ] `AgentSession.get_by_id(string)` classmethod exists and is unit-tested.
- [ ] All 12 broken call sites listed in issue #765 use the canonical pattern.
- [ ] Silent `except Exception: pass` blocks around the affected lookups log warnings instead.
- [ ] New unit test in `tests/unit/test_agent_session_lookup.py` covers positive, negative, and warning paths.
- [ ] Lint/test scan fails CI when `AgentSession.query.get(<positional>)` is introduced.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Worker dogfooding: nudge guard no longer logs `'str' object has no attribute 'redis_key'`.

## Team Orchestration

### Team Members

- **Builder (lookup-helper-and-sweep)**
  - Name: `lookup-builder`
  - Role: Add `get_by_id` classmethod, sweep all 12 call sites, upgrade silent handlers to logged warnings.
  - Agent Type: builder
  - Resume: true

- **Builder (regression-guard)**
  - Name: `guard-builder`
  - Role: Add the unit test and the lint/test scan that blocks new positional `query.get()` calls.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (full-sweep)**
  - Name: `sweep-validator`
  - Role: Verify zero remaining `AgentSession.query.get(<positional>)` calls, all tests pass, warning emission works.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `get_by_id` helper and sweep call sites
- **Task ID**: build-lookup-helper-and-sweep
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_lookup.py (created in task 2), integration tests still green
- **Informed By**: spike-1 (filter(id=) confirmed working)
- **Assigned To**: lookup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `AgentSession.get_by_id(cls, agent_session_id: str) -> "AgentSession | None"` classmethod in `models/agent_session.py`, wrapping `cls.query.filter(id=agent_session_id)`.
- Handle `None`/empty/whitespace input by returning `None` immediately.
- Replace all 12 broken call sites listed in issue #765 with `AgentSession.get_by_id(...)`.
- For each surrounding `except Exception: pass` (or equivalent), replace with `except Exception as exc: logger.warning("AgentSession lookup failed for %s: %s", agent_session_id, exc)`.
- Remove the stale inline comment at `ui/data/sdlc.py:558`.

### 2. Add regression guard
- **Task ID**: build-regression-guard
- **Depends On**: build-lookup-helper-and-sweep
- **Assigned To**: guard-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_agent_session_lookup.py` covering: positive lookup, missing id returns None, empty string returns None, None input returns None, warning emission on lookup failure (caplog).
- Add a CI-enforced scan that fails on any new `AgentSession.query.get(<positional>)`. Prefer a pytest-collected scan (e.g., `tests/unit/test_no_positional_query_get.py` that walks the source tree with grep) so it runs in the existing test suite without new tooling.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-lookup-helper-and-sweep, build-regression-guard
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "AgentSession\.query\.get(" --include="*.py" | grep -v "redis_key=\|db_key=" | grep -v "tests/"` and confirm zero matches outside intentional tests.
- Run `pytest tests/unit/test_agent_session_lookup.py -v` and confirm it passes.
- Run `pytest tests/` and confirm no regressions.
- Run `python -m ruff check . && python -m ruff format --check .` and confirm clean.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lookup unit test passes | `pytest tests/unit/test_agent_session_lookup.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| No more broken call sites | `grep -rn "AgentSession\.query\.get(" --include="*.py" \| grep -v "redis_key=\|db_key=\|get_by_id\|tests/"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Should the regression guard be a pytest scan (runs with the test suite) or a pre-commit hook (runs before commit)? The pytest approach is simpler and runs in CI without extra setup; the pre-commit approach catches issues earlier but adds another tool to the install path. **Default: pytest scan** unless you prefer pre-commit.
2. Should `get_by_id` log a warning itself when no session is found, or stay silent and let callers decide? **Default: stay silent** — callers already know when "not found" is expected vs. an error.
3. Are there any of the 12 call sites where the silent fallback was intentional (e.g., truly optional lookups)? The build agent will flag any case where the surrounding logic looks deliberate; the human reviews before merging.
