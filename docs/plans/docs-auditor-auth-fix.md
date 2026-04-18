---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1034
last_comment_id:
---

# docs_auditor auth fix: circuit-break on missing API key, prevent worker heartbeat stale

## Problem

The docs auditor reflection silently fails on every LLM call because the worker environment does
not have `ANTHROPIC_API_KEY` set. Instead of failing fast, it spams ~2 errors per doc file
(Haiku attempt + Sonnet escalation), saturates the thread pool, and destabilizes the worker
heartbeat within 7-8 minutes of restart.

**Current behavior:**
- `DocsAuditor._call_llm_for_verdict()` constructs `Anthropic()` with no api_key, which succeeds
  at construction time but raises `AuthenticationError` on every `messages.create()` call.
- Every failed Haiku call returns `low_confidence=True`, which triggers an immediate Sonnet
  escalation — so each doc produces 2 logged ERRORs and 2 wasted API call counter increments.
- 199 docs × 2 calls = up to 398 error log lines per audit run (capped at 50 increments, so ~50
  errors per run; multiple runs produce 246+ observed in the issue).
- `asyncio.to_thread(auditor.run)` runs the full serial audit loop in a thread pool worker,
  starving the event loop when thread pool saturates, causing the worker heartbeat (written every
  300s) to miss its window before the 360s stale threshold.
- Worker health appears degraded to downstream monitoring and users.

**Desired outcome:**
- Docs auditor detects missing/invalid API key at startup (before iterating docs), logs ONE
  warning, and exits cleanly.
- Worker heartbeat stays healthy regardless of auditor state.
- If auth is available, auditor continues to work correctly.
- `docs/features/reflections.md` documents the auth requirement for the docs auditor.

## Freshness Check

**Baseline commit:** `36832b2f8c84a76b78702d84e804fbb47f6b7f96`
**Issue filed at:** 2026-04-17T10:43:18Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/docs_auditor.py:610` — `self._client = _anthropic_module.Anthropic()` — still holds at
  line 610; bare constructor, no api_key parameter
- `scripts/docs_auditor.py:641-653` — `_call_llm_for_verdict` exception handler — confirmed; on
  exception returns `Verdict(low_confidence=True)` which triggers Sonnet escalation in `analyze_doc:380`
- `reflections/auditing.py:260` — `auditor = DocsAuditor(repo_root=PROJECT_ROOT, dry_run=False)` —
  confirmed at line 259; no auth check before construction
- `config/reflections.yaml` — `documentation-audit` entry uses `reflections.auditing.run_documentation_audit`,
  `enabled: true` — confirmed; daily interval

**Cited sibling issues/PRs re-checked:**
- No sibling issues cited in the issue body.

**Commits on main since issue was filed (touching referenced files):**
- None (clean `git log` output for all referenced files since 2026-04-17T10:43:18Z)

**Active plans in `docs/plans/` overlapping this area:**
- `reflections-modular.md` (#1028) — refactors `reflections/auditing.py` into per-reflection files.
  This plan's changes to `reflections/auditing.py` and `scripts/docs_auditor.py` are additive
  (auth guard + circuit break) and will survive a modularization refactor cleanly; no coordination
  needed, but the modularization PR should carry forward the auth guard.
- `reflections-quality-pass.md` (#926) — reflections scheduler/field correctness; no overlap with
  auth or docs_auditor code paths.
- `reflections-dead-import.md` (#857) — import shim fix; no overlap.

## Prior Art

- **Issue #839 / PR #842** — "Worker hibernation: circuit-gated queue governance" — added
  `circuit_health_gate` to pause the queue on Anthropic API failures. That circuit breaker
  tracks agent session API failures via `bridge.resilience.CircuitBreaker` and Redis state. It
  does NOT cover direct SDK calls inside reflections (e.g., docs auditor). Relevant pattern but
  different code path.
- **Issue #495 / PR #502** — "Bridge resilience: circuit breaker, unified recovery, degraded mode"
  — established `CircuitBreaker` in `bridge/resilience.py`. The infrastructure exists; docs
  auditor simply never hooks into it.

## Research

No external research needed — this is a self-contained fix to an internal auth strategy. All
relevant patterns (circuit breaking, graceful degradation) already exist in the codebase.

## Data Flow

1. **Trigger**: `ReflectionScheduler.tick()` fires `documentation-audit` entry (daily interval)
2. **Dispatch**: `asyncio.create_task(run_reflection(entry, state))` starts async task
3. **Invocation**: `run_documentation_audit()` in `reflections/auditing.py` calls
   `await asyncio.to_thread(auditor.run)` — offloads to thread pool
4. **Audit loop**: `DocsAuditor.run()` iterates 199 docs serially, calling `analyze_doc(path)` per doc
5. **LLM call**: `analyze_doc` → `_call_llm_for_verdict(report, MODEL_HAIKU)` → `_get_client().messages.create()`
6. **Auth failure**: `messages.create()` raises `AuthenticationError` (no api_key in env)
7. **Error path**: Exception caught at line 651; logs ERROR; returns `Verdict(low_confidence=True)`
8. **Escalation**: `analyze_doc` sees `low_confidence=True` → calls `_call_llm_for_verdict(report, MODEL_SONNET)` → same failure
9. **Cap check**: `_api_call_count` (incremented before each call) hits `max_api_calls=50` → loop stops
10. **Thread pool starvation**: Serial loop + thread pool saturation delays asyncio event loop turns,
    preventing `_agent_session_health_loop` from writing the heartbeat within 360s

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No external prerequisites — `ANTHROPIC_API_KEY` is intentionally absent in OAuth-only environments.
The fix must work correctly whether or not the key is set.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| anthropic package installed | `python -c "import anthropic"` | SDK must be importable |

## Solution

### Key Elements

- **Auth probe in `DocsAuditor.__init__` or `run()`**: Check `ANTHROPIC_API_KEY` (or test-construct the
  client and probe auth) at the START of each run — before iterating any docs. If auth is unavailable,
  set `AuditSummary.skipped=True` with `skip_reason="no API key"` and return immediately.
- **Single warning log on auth failure**: One `logger.warning(...)` at the point of early exit — not
  per-doc, not per-model.
- **Prevent Sonnet escalation on auth errors**: When a Haiku call fails with an `AuthenticationError`
  (or any auth-related exception), do NOT escalate to Sonnet. Auth failures are deterministic — Sonnet
  will fail identically. Short-circuit immediately.
- **Heartbeat protection**: Early exit from `run()` before iterating docs means the `asyncio.to_thread`
  call completes quickly, keeping the thread pool free and the event loop responsive.

### Flow

Worker starts → ReflectionScheduler fires `documentation-audit` → `run_documentation_audit()` →
`asyncio.to_thread(auditor.run)` → `DocsAuditor.run()` probes auth → no api_key → log ONE warning →
return `AuditSummary(skipped=True, skip_reason="ANTHROPIC_API_KEY not set")` → thread completes →
event loop free → heartbeat written on schedule

### Technical Approach

**Fix 1 — Auth probe at `DocsAuditor.run()` start (primary fix):**

Add an `_check_auth()` private method that:
1. Returns `True` if `os.environ.get("ANTHROPIC_API_KEY")` is non-empty (fast path, no network call)
2. Returns `False` otherwise

In `run()`, before the doc enumeration loop, call `_check_auth()`. If it returns `False`:
- Log `logger.warning("Docs auditor skipping: ANTHROPIC_API_KEY not set — set key to enable LLM-based audit")`
- Return `AuditSummary(skipped=True, skip_reason="ANTHROPIC_API_KEY not set")`

This is the minimal correct fix: no network call, deterministic, fast.

**Fix 2 — Short-circuit Sonnet escalation on auth errors:**

In `_call_llm_for_verdict()`, detect auth-type exceptions specifically:

```python
except Exception as e:
    err_str = str(e).lower()
    if "authentication" in err_str or "api_key" in err_str or "auth_token" in err_str:
        # Re-raise so analyze_doc can detect auth failures and skip escalation
        raise
    logger.error("LLM call failed (%s): %s", model, e)
    return Verdict(action="KEEP", rationale=f"LLM error: {e}", low_confidence=True)
```

Then in `analyze_doc()`, catch auth errors separately to skip escalation:

```python
try:
    verdict = self._call_llm_for_verdict(report, MODEL_HAIKU)
except Exception as e:
    if "authentication" in str(e).lower():
        logger.error("Auth error in docs auditor — check ANTHROPIC_API_KEY")
        return Verdict(action="KEEP", rationale=f"Auth error: {e}", low_confidence=False)
    raise
if verdict.low_confidence:
    verdict = self._call_llm_for_verdict(report, MODEL_SONNET)
```

NOTE: Fix 1 (auth probe at `run()` start) makes Fix 2 largely redundant — if the probe works, auth
errors never reach `_call_llm_for_verdict`. Fix 2 is a defense-in-depth measure for cases where the
key IS set but invalid (rotated/expired). Both fixes together are correct and non-redundant.

**Fix 3 — Document the auth requirement in `docs/features/reflections.md`:**

Add a section explaining:
- The docs auditor uses the Anthropic Python SDK directly (not OAuth subprocess)
- It requires `ANTHROPIC_API_KEY` in the worker environment
- If absent, the auditor silently skips with a single warning (correct behavior after this fix)
- Contrast with AgentSessions which use `CLAUDE_CODE_OAUTH_TOKEN`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_call_llm_for_verdict` has `except Exception` at line 651 — test that auth exceptions are
  re-raised (not swallowed) when the new auth-detection logic fires
- [ ] `analyze_doc` catches the re-raised auth exception and returns `Verdict(low_confidence=False)` —
  verify Sonnet escalation does NOT happen

### Empty/Invalid Input Handling
- [ ] `_check_auth()` handles empty string `ANTHROPIC_API_KEY=""` (falsy → skip)
- [ ] `_check_auth()` handles whitespace-only key (strip → empty → skip)

### Error State Rendering
- [ ] `run_documentation_audit()` in `reflections/auditing.py` receives `AuditSummary(skipped=True)`
  and reports it as `"Docs audit skipped: ANTHROPIC_API_KEY not set"` — verify this propagates
  correctly through the reflection scheduler's status tracking

## Test Impact

- [ ] `tests/unit/test_docs_auditor.py` — UPDATE: add tests for `_check_auth()` (env set / not set /
  empty string), add test that `run()` returns skipped summary when auth missing, add test that
  auth exceptions in `_call_llm_for_verdict` are re-raised rather than swallowed, add test that
  `analyze_doc` does NOT call Sonnet when Haiku raises auth error
- [ ] `tests/unit/test_reflections_package.py::test_run_documentation_audit_*` — UPDATE: mock
  `DocsAuditor.run` to return `AuditSummary(skipped=True, skip_reason="ANTHROPIC_API_KEY not set")`
  and assert the wrapper formats it correctly

## Rabbit Holes

- **Switching docs auditor to OAuth subprocess model** — This would work but significantly
  increases complexity and latency. The auditor makes 2-50 sequential Haiku calls; running each
  via `claude -p` subprocess would add 1-3s startup overhead per call. Not worth it; the
  `ANTHROPIC_API_KEY` env path is correct.
- **Propagating `ANTHROPIC_API_KEY` to the worker launchd plist** — Tempting, but this changes
  deployment configuration rather than making the code resilient. The fix must work whether or
  not the key is present.
- **Caching auth probe result across runs** — Unnecessary; the probe is O(1) env lookup.
- **Integrating with `bridge.resilience.CircuitBreaker`** — The existing circuit breaker tracks
  AgentSession API failures at the queue level. Wiring the docs auditor into it adds coupling
  between a low-priority reflection and the session queue circuit state. Overkill for a simple
  auth guard.

## Risks

### Risk 1: Key set but invalid (rotated/expired)
**Impact:** Auth probe passes (key is non-empty), but `messages.create()` fails. Without Fix 2,
Sonnet escalation still happens on every doc.
**Mitigation:** Fix 2 (auth-exception re-raise + `analyze_doc` short-circuit) handles this case.
Additionally, the `_api_call_count` cap limits max errors to 50 regardless.

### Risk 2: `reflections-modular` plan (#1028) modifies `reflections/auditing.py`
**Impact:** The `run_documentation_audit()` function or `DocsAuditor` integration may be refactored
before or after this PR lands. Merge conflict likely.
**Mitigation:** This fix is confined to `scripts/docs_auditor.py` (the `DocsAuditor` class itself)
and a comment update in `reflections/auditing.py`. The modularization plan should carry forward
the auth guard if it moves the invocation.

## Race Conditions

No race conditions — the auth probe is a synchronous env lookup at the start of `run()`. The fix
does not introduce any shared mutable state.

## No-Gos (Out of Scope)

- Propagating `ANTHROPIC_API_KEY` to the worker environment (deployment concern, not code fix)
- Making the docs auditor use OAuth subprocess harness (separate architectural decision)
- Fixing other reflections that may have similar auth issues (separate issues if they exist)
- Changing the worker heartbeat interval or stale threshold (different problem)
- Implementing a full circuit breaker in the docs auditor (overkill for this scope)

## Update System

No update system changes required — this fix is purely internal to `scripts/docs_auditor.py`
and `docs/features/reflections.md`. No new dependencies, config files, or environment variables
are added. The fix makes the existing behavior more resilient, not different.

## Agent Integration

No agent integration required — `docs_auditor.py` runs as a scheduled reflection inside the
worker, not via any MCP tool or bridge invocation. No `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/reflections.md` to document the auth requirement for the docs auditor:
  explain that it uses `ANTHROPIC_API_KEY` (direct SDK), not OAuth, and that absence of the key
  causes clean skip with one warning log.
- [ ] Verify `docs/features/README.md` index entry for `reflections.md` is current (no new entry
  needed — existing entry covers the reflections feature; confirm it links correctly after the
  auth section is added).

## Success Criteria

- [ ] After `worker-restart`, zero `Could not resolve authentication method` errors in
  `logs/worker.log` within 5 minutes (regardless of `ANTHROPIC_API_KEY` presence)
- [ ] `DocsAuditor.run()` returns `AuditSummary(skipped=True)` when `ANTHROPIC_API_KEY` is unset,
  with one `WARNING` log line and zero `ERROR` log lines
- [ ] When `ANTHROPIC_API_KEY` IS set, `DocsAuditor.run()` proceeds normally and produces real
  verdicts (at least one `KEEP` / `UPDATE` / `DELETE`)
- [ ] When api_key is set but invalid, Sonnet escalation is NOT triggered after a Haiku auth error
- [ ] Worker heartbeat stays under 360s threshold across 30 minutes of operation after fix
- [ ] `tests/unit/test_docs_auditor.py` — all new auth tests pass
- [ ] `docs/features/reflections.md` documents the auth requirement

## Team Orchestration

### Team Members

- **Builder (auth-fix)**
  - Name: auth-fix-builder
  - Role: Implement auth probe, exception short-circuit, and doc update
  - Agent Type: builder
  - Resume: true

- **Validator (auth-fix)**
  - Name: auth-fix-validator
  - Role: Verify auth probe logic, test coverage, no regression in existing tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement auth probe and exception short-circuit in `scripts/docs_auditor.py`
- **Task ID**: build-auth-fix
- **Depends On**: none
- **Validates**: tests/unit/test_docs_auditor.py
- **Informed By**: Technical Approach (Fixes 1 and 2)
- **Assigned To**: auth-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_check_auth()` method to `DocsAuditor` that checks `os.environ.get("ANTHROPIC_API_KEY")`
  (strip whitespace; return False if empty/absent)
- In `DocsAuditor.run()`, call `_check_auth()` before the frequency gate check; if False, log one
  WARNING and return `AuditSummary(skipped=True, skip_reason="ANTHROPIC_API_KEY not set")`
- In `_call_llm_for_verdict()`, detect authentication-type exceptions (check `"authentication"`,
  `"api_key"`, `"auth_token"` in lowercased error string) and re-raise them instead of returning
  `low_confidence=True`
- In `analyze_doc()`, wrap the Haiku `_call_llm_for_verdict` call to catch re-raised auth
  exceptions and return `Verdict(action="KEEP", low_confidence=False)` without escalating to Sonnet
- Add tests to `tests/unit/test_docs_auditor.py`:
  - `test_run_skips_when_api_key_missing` — mock env without key; assert `run()` returns skipped
  - `test_run_skips_when_api_key_empty` — mock `ANTHROPIC_API_KEY=""`; assert skipped
  - `test_check_auth_returns_true_when_key_set` — mock key present; assert `_check_auth()` True
  - `test_auth_error_not_escalated_to_sonnet` — mock `messages.create` raising auth error; assert
    `analyze_doc` does NOT call Sonnet (mock call count == 1)
  - `test_call_llm_reraises_auth_exceptions` — verify re-raise behavior

### 2. Update `docs/features/reflections.md`
- **Task ID**: document-auth-req
- **Depends On**: build-auth-fix
- **Assigned To**: auth-fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add section "Docs Auditor Authentication" to `docs/features/reflections.md` explaining:
  - Docs auditor uses `Anthropic()` SDK directly (not OAuth subprocess)
  - Requires `ANTHROPIC_API_KEY` in worker environment
  - If absent, auditor skips with one WARNING (correct behavior)
  - Contrast with AgentSessions using `CLAUDE_CODE_OAUTH_TOKEN`
  - Note: to enable docs auditing, add `ANTHROPIC_API_KEY` to the worker's launchd env or `.env`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-auth-fix, document-auth-req
- **Assigned To**: auth-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_docs_auditor.py -v` — all tests pass including new auth tests
- Run `pytest tests/unit/test_reflections_package.py -v` — no regression
- Verify `docs/features/reflections.md` has the new auth section
- Confirm no `except Exception: pass` (bare swallow) was introduced
- Run `python -m black scripts/docs_auditor.py` and verify no format issues

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_docs_auditor.py -v -q` | exit code 0 |
| Reflections tests pass | `pytest tests/unit/test_reflections_package.py -v -q` | exit code 0 |
| No auth error logged when key absent | `python -c "import os; os.environ.pop('ANTHROPIC_API_KEY', None); from pathlib import Path; from scripts.docs_auditor import DocsAuditor; s = DocsAuditor(Path('.')).run(); print(s.skipped, s.skip_reason)"` | output contains `True` |
| Format clean | `python -m black --check scripts/docs_auditor.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — root cause is confirmed, fix is scoped, no human input needed before building.
