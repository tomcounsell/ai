---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1034
last_comment_id:
revision_applied: true
---

# docs_auditor auth fix: circuit-break on missing/invalid API key, prevent worker heartbeat stale

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
- Docs auditor detects missing OR invalid API key at startup (before iterating any docs), logs
  ONE warning, and exits cleanly.
- Worker heartbeat stays healthy regardless of auditor state.
- If auth is available and valid, auditor continues to work correctly.
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
  auditor simply never hooks into it. Integrating into the circuit breaker is explicitly deferred
  to Rabbit Holes — this fix is narrower (auth probe + cascade cap).

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

- **Auth probe in `DocsAuditor.run()`**: A single `_check_auth()` method handles BOTH missing and
  invalid keys. It checks env var presence first (fast path, no network), then makes a minimal API
  test call (`client.models.list()`) to confirm the key is actually valid. If auth is unavailable
  OR the key is invalid, log ONE `logger.warning(...)` and return
  `AuditSummary(skipped=True, skip_reason="...", skip_type="auth")` immediately.
- **Consolidated approach — no per-call auth handling**: Fix 2 from the previous plan revision
  (re-raising auth exceptions inside `_call_llm_for_verdict` + catching in `analyze_doc`) is
  removed. The probe at `run()` start exits before any doc is processed, so Fix 2 is unreachable
  in the missing-key case. The invalid-key case is also caught at the probe stage. Per-call
  auth handling adds complexity without adding safety (re-raised exceptions from `_call_llm_for_verdict`
  are swallowed by `run()`'s outer `except Exception` at line 560-563 anyway).
- **Consecutive error circuit break**: A `consecutive_errors` counter in `run()`'s doc loop
  breaks early at ≥3 consecutive failures. This caps cascade for non-auth API failures (rate
  limits, transient network errors) that the auth probe cannot catch at startup.
- **Observability distinction**: `AuditSummary.skip_type` field ("auth" vs "schedule") lets
  `run_documentation_audit()` return `{"status": "disabled", ...}` for auth-skip vs `{"status": "ok", ...}`
  for schedule-skip. Dashboards and monitoring can distinguish permanently-disabled from
  temporarily-skipped.
- **Heartbeat protection**: Auth probe short-circuits before any doc iteration, so the
  `asyncio.to_thread` call completes in milliseconds. Thread pool stays free; event loop
  stays responsive; heartbeat written on schedule.

### Flow

Worker starts → ReflectionScheduler fires `documentation-audit` → `run_documentation_audit()` →
`asyncio.to_thread(auditor.run)` → `DocsAuditor.run()` calls `_check_auth()`:

- Key absent or sentinel ("None", "null", etc.) → log WARNING → return `AuditSummary(skipped=True, skip_type="auth", skip_reason="ANTHROPIC_API_KEY not set")`
- Key present, `client.models.list()` raises AuthenticationError → log WARNING → return `AuditSummary(skipped=True, skip_type="auth", skip_reason="ANTHROPIC_API_KEY invalid or expired")`
- Key present, probe passes → proceed to doc loop
  - Per-doc: if `consecutive_errors >= 3` → break loop early
- Thread completes → event loop free → heartbeat written on schedule

### Technical Approach

**Fix 1 — Consolidated auth probe at `DocsAuditor.run()` start (primary fix, covers missing AND invalid keys):**

Add an `_check_auth()` private method:

```python
def _check_auth(self) -> tuple[bool, str]:
    """Check ANTHROPIC_API_KEY presence and validity.

    Returns (ok: bool, reason: str). Performs a minimal API probe to catch
    rotated/expired keys at startup rather than per-doc.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.lower() in ("none", "null", "false", "0"):
        return False, "ANTHROPIC_API_KEY not set"
    try:
        client = _anthropic_module.Anthropic(api_key=key)
        client.models.list()  # minimal probe — no tokens consumed
        return True, ""
    except Exception as e:
        err_str = str(e).lower()
        if "authentication" in err_str or "api_key" in err_str or "auth_token" in err_str:
            return False, f"ANTHROPIC_API_KEY invalid or expired: {e}"
        # Non-auth error during probe (network, etc.) — treat as transient; allow run to proceed
        logger.warning("Auth probe encountered non-auth error: %s — proceeding", e)
        return True, ""
```

In `run()`, before the frequency gate, call `_check_auth()`:

```python
auth_ok, auth_reason = self._check_auth()
if not auth_ok:
    logger.warning("Docs auditor skipping: %s", auth_reason)
    return AuditSummary(skipped=True, skip_type="auth", skip_reason=auth_reason)
```

**Fix 2 — Add `skip_type` to `AuditSummary` and `Verdict.auth_failure`:**

Extend the `AuditSummary` dataclass:
```python
@dataclass
class AuditSummary:
    skipped: bool = False
    skip_reason: str = ""
    skip_type: str = ""  # "auth" | "schedule" | "" (normal run)
    ...
```

Extend the `Verdict` dataclass:
```python
@dataclass
class Verdict:
    action: str
    rationale: str
    corrections: list[str] = field(default_factory=list)
    low_confidence: bool = False
    auth_failure: bool = False  # True when no LLM was consulted due to auth error
```

`auth_failure` is reserved for future use (e.g., if a per-call auth fallback is ever added).
It is not set by this fix — the consolidated probe eliminates the need for per-call auth
tracking. Including the field now avoids a breaking dataclass change later.

**Fix 3 — Consecutive error circuit break in `run()` doc loop:**

Add a `consecutive_errors` counter to `run()`'s doc loop:

```python
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 3

for path in docs:
    if self._api_call_count >= self.max_api_calls:
        ...break...

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        logger.warning(
            "Stopping audit after %d consecutive errors to prevent cascade",
            consecutive_errors,
        )
        break

    try:
        verdict = self.analyze_doc(path)
        consecutive_errors = 0  # reset on success
        ...
    except Exception as e:
        consecutive_errors += 1
        msg = f"Error auditing {path}: {e}"
        logger.error(msg)
        summary.errors.append(msg)
        summary.kept.append(str(path))
```

**Fix 4 — Observability in `run_documentation_audit()` (`reflections/auditing.py`):**

Update `run_documentation_audit()` to use `skip_type` for distinct status:

```python
if summary_obj.skipped:
    if summary_obj.skip_type == "auth":
        return {"status": "disabled", "findings": [f"Docs audit disabled: {summary_obj.skip_reason}"], "summary": "..."}
    else:
        return {"status": "ok", "findings": [f"Docs audit skipped: {summary_obj.skip_reason}"], "summary": "..."}
```

NOTE: `run_documentation_audit()` at `reflections/auditing.py:263` already creates a fresh
`DocsAuditor` instance per call (`auditor = DocsAuditor(...)`), which keeps `_api_call_count`
isolated across reflection runs. This per-call isolation is correct and must be preserved
if the function is ever refactored.

**Fix 5 — Document the auth requirement in `docs/features/reflections.md`:**

Add a section explaining:
- The docs auditor uses the Anthropic Python SDK directly (not OAuth subprocess)
- It requires `ANTHROPIC_API_KEY` in the worker environment
- If absent or invalid, the auditor silently skips with a single warning (correct behavior after this fix)
- Contrast with AgentSessions which use `CLAUDE_CODE_OAUTH_TOKEN`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_check_auth()` with key missing: returns `(False, "ANTHROPIC_API_KEY not set")`
- [ ] `_check_auth()` with key="": returns `(False, ...)`
- [ ] `_check_auth()` with key="None": returns `(False, ...)` (sentinel guard)
- [ ] `_check_auth()` with key=valid: `client.models.list()` succeeds; returns `(True, "")`
- [ ] `_check_auth()` with key=invalid: `client.models.list()` raises AuthenticationError; returns `(False, "ANTHROPIC_API_KEY invalid...")`
- [ ] `_check_auth()` non-auth error during probe (network error): returns `(True, "")` and logs WARNING
- [ ] `run()` returns `AuditSummary(skipped=True, skip_type="auth")` when `_check_auth()` returns False
- [ ] `run()` proceeds normally when `_check_auth()` returns True
- [ ] Consecutive errors break at ≥3: `run()` stops doc loop early and logs WARNING

### Empty/Invalid Input Handling
- [ ] `_check_auth()` handles `ANTHROPIC_API_KEY=""` (empty string)
- [ ] `_check_auth()` handles `ANTHROPIC_API_KEY=" "` (whitespace-only, strips to empty)
- [ ] `_check_auth()` handles `ANTHROPIC_API_KEY=None` (string "None", lowercased sentinel check)

### Error State Rendering
- [ ] `run_documentation_audit()` returns `{"status": "disabled", ...}` when `skip_type == "auth"`
- [ ] `run_documentation_audit()` returns `{"status": "ok", ...}` when `skip_type == "schedule"`

## Test Impact

- [ ] `tests/unit/test_docs_auditor.py` — UPDATE: add tests for `_check_auth()` (all cases above),
  add test that `run()` returns `skipped=True, skip_type="auth"` when auth missing, add timing
  assertion (run() completes within 1 second when key absent), add test that consecutive errors
  trigger early loop break, add test that `AuditSummary.skip_type` propagates correctly
- [ ] `tests/unit/test_reflections_package.py::test_run_documentation_audit_*` — UPDATE: mock
  `DocsAuditor.run` to return `AuditSummary(skipped=True, skip_type="auth", skip_reason="ANTHROPIC_API_KEY not set")`
  and assert the wrapper returns `{"status": "disabled", ...}` (not "ok")

## Rabbit Holes

- **Switching docs auditor to OAuth subprocess model** — This would work but significantly
  increases complexity and latency. The auditor makes 2-50 sequential Haiku calls; running each
  via `claude -p` subprocess would add 1-3s startup overhead per call. Not worth it; the
  `ANTHROPIC_API_KEY` env path is correct.
- **Propagating `ANTHROPIC_API_KEY` to the worker launchd plist** — Tempting, but this changes
  deployment configuration rather than making the code resilient. The fix must work whether or
  not the key is present.
- **Caching auth probe result across runs** — Unnecessary; the probe is O(1) env lookup + one
  `models.list()` call per audit run (not per doc).
- **Integrating with `bridge.resilience.CircuitBreaker`** — The existing circuit breaker tracks
  AgentSession API failures at the queue level (PR #502/#842). Wiring the docs auditor into it
  adds coupling between a low-priority reflection and the session queue circuit state. Overkill
  for a simple auth guard. The consecutive error counter in Fix 3 provides equivalent protection
  without that coupling.
- **Dashboard status entry for docs_auditor** — The `{"status": "disabled"}` return from
  `run_documentation_audit()` is readable by the dashboard's reflection status display. Wiring a
  dedicated docs_auditor tile into the dashboard is a separate dashboard feature.

## Risks

### Risk 1: `client.models.list()` probe adds startup latency
**Impact:** The probe makes a real API call on each audit run start. If the Anthropic API is
slow (rare), this adds latency before the frequency gate check.
**Mitigation:** The probe is only made when a key IS present; missing-key path skips it immediately.
The audit is a once-daily background job — a sub-second probe delay is acceptable.

### Risk 2: `reflections-modular` plan (#1028) modifies `reflections/auditing.py`
**Impact:** The `run_documentation_audit()` function or `DocsAuditor` integration may be refactored
before or after this PR lands. Merge conflict likely.
**Mitigation:** This fix is confined to `scripts/docs_auditor.py` (the `DocsAuditor` class itself)
and a targeted change in `reflections/auditing.py`. The modularization plan should carry forward
the auth guard if it moves the invocation.

### Risk 3: Non-auth API failures (rate-limits, timeouts) still produce errors per-doc
**Impact:** If the API is temporarily unavailable (rate limit, outage) after the probe passes,
the auth probe succeeds but individual `messages.create()` calls fail. These are caught by the
existing `except Exception` in `_call_llm_for_verdict` and log ERRORs per doc.
**Mitigation:** Fix 3 (consecutive error counter) caps cascade at 3 consecutive failures. After
3 failures, `run()` breaks the loop and returns partial results. This is a best-effort guard,
not a full circuit breaker — non-auth cascades are an acknowledged residual risk.

## Race Conditions

No race conditions — the auth probe is a synchronous operation at the start of `run()`. The fix
does not introduce any shared mutable state. `_api_call_count` and the new `consecutive_errors`
counter are instance-local and reset per `DocsAuditor` instance.

## No-Gos (Out of Scope)

- Propagating `ANTHROPIC_API_KEY` to the worker environment (deployment concern, not code fix)
- Making the docs auditor use OAuth subprocess harness (separate architectural decision)
- Fixing other reflections that may have similar auth issues (separate issues if they exist)
- Changing the worker heartbeat interval or stale threshold (different problem)
- Implementing a full circuit breaker integration with `bridge.resilience.CircuitBreaker` (overkill)

## Update System

No update system changes required — this fix is purely internal to `scripts/docs_auditor.py`
and `docs/features/reflections.md`. No new dependencies, config files, or environment variables
are added. The fix makes the existing behavior more resilient, not different.

## Agent Integration

No agent integration required — `docs_auditor.py` runs as a scheduled reflection inside the
worker, not via any MCP tool or bridge invocation. No `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/reflections.md` to document the auth requirement for the docs auditor:
  explain that it uses `ANTHROPIC_API_KEY` (direct SDK), not OAuth, and that absence/invalidity
  of the key causes clean skip with one warning log.
- [ ] Verify `docs/features/README.md` index entry for `reflections.md` is current (no new entry
  needed — existing entry covers the reflections feature; confirm it links correctly after the
  auth section is added).

## Success Criteria

- [ ] After `worker-restart`, zero `Could not resolve authentication method` errors in
  `logs/worker.log` within 5 minutes (regardless of `ANTHROPIC_API_KEY` presence)
- [ ] `DocsAuditor.run()` returns `AuditSummary(skipped=True, skip_type="auth")` when
  `ANTHROPIC_API_KEY` is unset, with one `WARNING` log line and zero `ERROR` log lines
- [ ] `DocsAuditor.run()` returns `AuditSummary(skipped=True, skip_type="auth")` when
  `ANTHROPIC_API_KEY` is present but invalid (rotated/expired), with one `WARNING` and zero `ERROR`
- [ ] When `ANTHROPIC_API_KEY` IS set and valid, `DocsAuditor.run()` proceeds normally and produces
  real verdicts (at least one `KEEP` / `UPDATE` / `DELETE`)
- [ ] `run_documentation_audit()` returns `{"status": "disabled", ...}` when skip_type is "auth"
  (distinct from `{"status": "ok", ...}` for schedule-skip)
- [ ] `DocsAuditor.run()` completes within 1 second when `ANTHROPIC_API_KEY` is absent (timing
  assertion in unit test)
- [ ] After 3 consecutive doc-audit errors, `run()` breaks the loop early with a WARNING log
- [ ] Worker heartbeat stays under 360s threshold across 30 minutes of operation after fix
- [ ] `tests/unit/test_docs_auditor.py` — all new auth tests pass
- [ ] `docs/features/reflections.md` documents the auth requirement

## Team Orchestration

### Team Members

- **Builder (auth-fix)**
  - Name: auth-fix-builder
  - Role: Implement auth probe, consecutive error guard, observability changes, and doc update
  - Agent Type: builder
  - Resume: true

- **Validator (auth-fix)**
  - Name: auth-fix-validator
  - Role: Verify auth probe logic, test coverage, no regression in existing tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend `Verdict` and `AuditSummary` dataclasses in `scripts/docs_auditor.py`
- **Task ID**: extend-dataclasses
- **Depends On**: none
- **Validates**: tests/unit/test_docs_auditor.py
- **Informed By**: Technical Approach (Fix 2)
- **Assigned To**: auth-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `auth_failure: bool = False` field to `Verdict` dataclass (reserved for future use;
  not set by this fix — documents the field so downstream code can key off it cleanly)
- Add `skip_type: str = ""` field to `AuditSummary` dataclass (values: `"auth"`, `"schedule"`, `""`)

### 2. Implement consolidated auth probe in `scripts/docs_auditor.py`
- **Task ID**: build-auth-probe
- **Depends On**: extend-dataclasses
- **Validates**: tests/unit/test_docs_auditor.py
- **Informed By**: Technical Approach (Fix 1)
- **Assigned To**: auth-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_check_auth()` method to `DocsAuditor` that:
  - Reads `os.environ.get("ANTHROPIC_API_KEY", "").strip()`
  - Returns `(False, "ANTHROPIC_API_KEY not set")` if empty or sentinel string ("none", "null", "false", "0")
  - Constructs `_anthropic_module.Anthropic(api_key=key)` and calls `client.models.list()`
  - On `AuthenticationError` (check error string for "authentication", "api_key", "auth_token"):
    returns `(False, "ANTHROPIC_API_KEY invalid or expired: {e}")`
  - On non-auth exception: logs WARNING "Auth probe non-auth error: {e} — proceeding"; returns `(True, "")`
  - On success: returns `(True, "")`
- In `DocsAuditor.run()`, call `_check_auth()` BEFORE the frequency gate check:
  - If `(False, reason)`: log `logger.warning("Docs auditor skipping: %s", reason)`;
    return `AuditSummary(skipped=True, skip_type="auth", skip_reason=reason)`
- Add tests to `tests/unit/test_docs_auditor.py`:
  - `test_check_auth_key_missing` — no env var; assert returns `(False, "ANTHROPIC_API_KEY not set")`
  - `test_check_auth_key_empty` — `ANTHROPIC_API_KEY=""`; assert `(False, ...)`
  - `test_check_auth_key_sentinel_none` — `ANTHROPIC_API_KEY="None"`; assert `(False, ...)`
  - `test_check_auth_key_sentinel_null` — `ANTHROPIC_API_KEY="null"`; assert `(False, ...)`
  - `test_check_auth_key_valid` — mock `client.models.list()` success; assert `(True, "")`
  - `test_check_auth_key_invalid` — mock `client.models.list()` raising `AuthenticationError`; assert `(False, "...invalid...")`
  - `test_check_auth_non_auth_error` — mock `client.models.list()` raising `ConnectionError`; assert `(True, "")` and WARNING logged
  - `test_run_skips_when_api_key_missing` — mock env without key; assert `run()` returns `AuditSummary(skipped=True, skip_type="auth")`
  - `test_run_skips_when_api_key_invalid` — mock `_check_auth` returning `(False, "invalid")`; assert skipped with `skip_type="auth"`
  - `test_run_completes_fast_when_key_absent` — assert `run()` completes within 1 second when `ANTHROPIC_API_KEY` absent (timing assertion using `time.time()`)
  - `test_run_proceeds_when_auth_ok` — mock `_check_auth` returning `(True, "")`; assert `run()` does NOT return skipped summary

### 3. Add consecutive error circuit break in `scripts/docs_auditor.py`
- **Task ID**: build-error-cap
- **Depends On**: extend-dataclasses
- **Validates**: tests/unit/test_docs_auditor.py
- **Informed By**: Technical Approach (Fix 3)
- **Assigned To**: auth-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `consecutive_errors = 0` and `MAX_CONSECUTIVE_ERRORS = 3` (module-level constant) to `run()`
- In the doc loop `except Exception as e:` handler: increment `consecutive_errors`
- At the start of each loop iteration (after the API cap check): if `consecutive_errors >= MAX_CONSECUTIVE_ERRORS`:
  log `logger.warning("Stopping audit after %d consecutive errors...", consecutive_errors)` and `break`
- On successful `analyze_doc()` result: reset `consecutive_errors = 0`
- Add test `test_run_breaks_on_consecutive_errors` — mock `analyze_doc` to raise 3 consecutive
  exceptions; assert `run()` breaks after 3 failures and logs WARNING

### 4. Update `reflections/auditing.py` for `skip_type` observability
- **Task ID**: build-observability
- **Depends On**: extend-dataclasses
- **Validates**: tests/unit/test_reflections_package.py
- **Informed By**: Technical Approach (Fix 4)
- **Assigned To**: auth-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- In `run_documentation_audit()`, branch on `summary_obj.skip_type`:
  - `skip_type == "auth"`: return `{"status": "disabled", "findings": [f"Docs audit disabled: {summary_obj.skip_reason}"], "summary": "..."}`
  - Other skip types (schedule, etc.): return `{"status": "ok", "findings": [f"Docs audit skipped: {summary_obj.skip_reason}"], "summary": "..."}`
- Add comment: `# NOTE: A fresh DocsAuditor instance must be created per call to isolate _api_call_count`
- Update test `test_run_documentation_audit_*` in `tests/unit/test_reflections_package.py`:
  assert mock returning `skip_type="auth"` produces `status="disabled"`

### 5. Update `docs/features/reflections.md`
- **Task ID**: document-auth-req
- **Depends On**: build-auth-probe
- **Assigned To**: auth-fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add section "Docs Auditor Authentication" to `docs/features/reflections.md` explaining:
  - Docs auditor uses `Anthropic()` SDK directly (not OAuth subprocess)
  - Requires `ANTHROPIC_API_KEY` in worker environment
  - If absent or invalid, auditor skips with one WARNING (correct behavior)
  - Contrast with AgentSessions using `CLAUDE_CODE_OAUTH_TOKEN`
  - Note: to enable docs auditing, add `ANTHROPIC_API_KEY` to the worker's launchd env or `.env`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-auth-probe, build-error-cap, build-observability, document-auth-req
- **Assigned To**: auth-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_docs_auditor.py -v` — all tests pass including new auth tests
- Run `pytest tests/unit/test_reflections_package.py -v` — no regression
- Verify `docs/features/reflections.md` has the new auth section
- Confirm no `except Exception: pass` (bare swallow) was introduced
- Run `python -m black scripts/docs_auditor.py reflections/auditing.py` and verify no format issues

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_docs_auditor.py -v -q` | exit code 0 |
| Reflections tests pass | `pytest tests/unit/test_reflections_package.py -v -q` | exit code 0 |
| No auth error logged when key absent | `python -c "import os; os.environ.pop('ANTHROPIC_API_KEY', None); from pathlib import Path; from scripts.docs_auditor import DocsAuditor; s = DocsAuditor(Path('.')).run(); print(s.skipped, s.skip_type, s.skip_reason)"` | `True auth ANTHROPIC_API_KEY not set` |
| Disabled status when key absent | Check `run_documentation_audit()` return when key absent | `{"status": "disabled", ...}` |
| Format clean | `python -m black --check scripts/docs_auditor.py reflections/auditing.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consistency Auditor, Skeptic | B1: `Verdict(low_confidence=False)` on auth error corrupts verdict semantics | extend-dataclasses (Fix 2) | Added `Verdict.auth_failure` field; removed per-call auth catch from `analyze_doc` entirely — probe at `run()` start replaces it |
| BLOCKER | Skeptic, Simplifier, Consistency Auditor | B2: Fix 2 redundant AND leaves gap for invalid keys | build-auth-probe (Fix 1) | Consolidated into `_check_auth()` with `client.models.list()` probe; covers missing AND invalid keys at startup; removed per-call re-raise/catch entirely |
| CONCERN | Adversary | C1: Auth probe passes on `"None"` string value | build-auth-probe (Fix 1) | Added `key.lower() in ("none", "null", "false", "0")` sentinel guard |
| CONCERN | Operator | C2: No observability distinction between auth-skip and schedule-skip | build-observability (Fix 4) + extend-dataclasses | Added `AuditSummary.skip_type`; `run_documentation_audit()` returns `status="disabled"` for auth-skip |
| CONCERN | Operator, User | C3: Worker heartbeat validation absent from test plan | build-auth-probe task | Added `test_run_completes_fast_when_key_absent` timing assertion (1 second limit) |
| CONCERN | Archaeologist | C4: No Prior Art section | Prior Art section | Added PR #502/#842 references; deferred CircuitBreaker integration documented in Rabbit Holes |
| CONCERN | Archaeologist | C5: Non-auth API failures still cascade | build-error-cap (Fix 3) | Added `consecutive_errors` counter; breaks loop at ≥3 failures |
| CONCERN | Consistency Auditor, Skeptic | C6: Desired outcome overstates Fix 1 scope | build-auth-probe (Fix 1) | Extended `_check_auth()` to make a probe API call — desired outcome now correctly says "missing OR invalid" |
| NIT | Adversary | N2: `_api_call_count` isolation undocumented | build-observability | Added inline comment to `run_documentation_audit()` noting fresh-instance requirement |
| NIT | User | N3: No dashboard signal for disabled auditor | build-observability | `status="disabled"` propagates to reflection status tracking; full dashboard tile deferred to Rabbit Holes |

---

## Open Questions

None — root cause is confirmed, fix is scoped, all critique blockers addressed.
