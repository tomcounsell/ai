---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-03-01
tracking: https://github.com/tomcounsell/ai/issues/223
---

# Fix Top 5 Bridge Error Log Issues

## Problem

The bridge error log (`logs/bridge.error.log`) has grown to 32MB / 121K lines. Analysis via `scripts/analyze_error_log.py` identified 5 categories of recurring errors that account for nearly all log noise. The dominant issue -- Redis duplicate key errors from the session watchdog -- represents 98% of all errors (22,421 occurrences).

**Current behavior:**
- Watchdog spams 22K+ duplicate key errors by trying to save sessions that trigger popoto unique constraint violations on the `session_id` field
- Job IDs generated outside UUID4 format cause 4,176 validation failures per field
- 5,970 errors from orphaned Redis keys pointing to deleted objects
- 126 UTF-8 decode failures when recovering corrupted orphan session data
- 10 Perplexity API 401 errors from expired/invalid credentials
- No log rotation on `bridge.error.log`, so it grows unbounded

**Desired outcome:**
- Error rate drops by >95% (from ~33K errors to <500)
- `bridge.error.log` stays manageable with rotation
- Remaining errors are genuine, actionable issues

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work is fast -- the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites -- this work has no external dependencies. All fixes are internal to the bridge and monitoring code.

## Solution

### Key Elements

- **Watchdog duplicate key guard**: Add existence check before session save in `fix_unhealthy_session()` and wrap ModelException at the correct granularity
- **Job ID validation fix**: Trace where non-UUID4 job IDs are created and fix the source, or relax the validation to match actual ID format
- **Orphan recovery hardening**: Add try/except around UTF-8 decode in `_recover_orphaned_jobs()` to skip corrupted entries gracefully
- **Perplexity error handling**: Add graceful degradation for 401 errors with clear logging (credential refresh is manual)
- **Log rotation**: Add `RotatingFileHandler` for `bridge.error.log` or configure logrotate

### Flow

**Watchdog cycle** -> Check session health -> Attempt fix -> **Guard: session already exists?** -> Skip if yes -> Log at DEBUG -> **Next session**

**Orphan recovery** -> Load orphan data -> **Guard: decodable?** -> Skip if corrupt -> Log warning -> **Next orphan**

### Technical Approach

1. **Watchdog (`monitoring/session_watchdog.py`)**:
   - The existing `ModelException` catch in `check_all_sessions()` (line 110-126) already handles the duplicate key case but only catches it after the error is raised. The fix is to also catch `ModelException` inside `fix_unhealthy_session()` around the `session.save()` calls (lines 499, 533, 553) so the error is handled locally rather than propagating up to the loop.
   - Additionally, before calling `session.save()`, check if the session still exists in Redis (it may have been cleaned up by another process between assessment and fix).

2. **Job ID validation (`models/agent_session.py` / `agent/job_queue.py`)**:
   - The `job_id = AutoKeyField()` uses popoto's UUID4 strategy which generates 32-char hex IDs. The 60-char IDs suggest something is setting `job_id` explicitly instead of letting `AutoKeyField` auto-generate. Trace the source via the error log stack traces and fix the caller.
   - If the issue is in the delete-and-recreate pattern (where fields are extracted and a new session is created), ensure `job_id` is excluded from `_extract_job_fields()` -- it already is, but verify no path bypasses this.

3. **Orphan recovery (`agent/job_queue.py`, `_recover_orphaned_jobs()`)**:
   - Line 467: `key_str = key.decode()` can fail on corrupted data. Wrap in try/except with `errors='replace'` fallback.
   - Line 477-478: `data.get(b"project_key", b"").decode()` can also fail. Add explicit UTF-8 decode with replace.
   - Line 486: `decode_popoto_model_hashmap()` can fail on corrupted fields. The existing try/except on line 490 handles this but doesn't log the specific decode error. Add the byte content to the warning.

4. **Perplexity provider (`tools/web/providers/perplexity.py`)**:
   - The bare `except Exception` on line 121 swallows all errors silently. Add specific handling for `httpx.HTTPStatusError` to log 401s as warnings with a clear message about credential refresh.
   - Return `None` for auth errors (already happens via the generic catch, but make it explicit).

5. **Log rotation (`bridge/telegram_bridge.py` or `scripts/valor-service.sh`)**:
   - Since `bridge.error.log` is stderr redirected by the shell script (`2>> bridge.error.log`), Python's `RotatingFileHandler` won't help. Instead, add a stderr handler with `RotatingFileHandler` in the bridge's logging setup, AND add a periodic cleanup task (or use system logrotate).
   - Simplest approach: add a `logrotate` config file or a startup truncation in `valor-service.sh` that rotates `bridge.error.log` when it exceeds a size threshold.

## Rabbit Holes

- **Redesigning the popoto KeyField indexing** -- the duplicate key issue stems from popoto's index semantics. Fixing popoto itself is out of scope; we work around it.
- **Full Redis data integrity audit** -- the orphan cleanup already exists. We just need to harden its error handling, not redesign it.
- **Perplexity credential rotation automation** -- the 401 fix is about graceful error handling, not building an auto-rotation system.
- **Migrating away from stderr for error logging** -- would be cleaner but is a larger change than warranted for this fix.

## Risks

### Risk 1: Swallowing real errors in watchdog
**Impact:** A genuine session corruption issue gets silently ignored
**Mitigation:** Log caught ModelExceptions at WARNING level (not DEBUG), so they appear in bridge.log but don't spam bridge.error.log. Monitor for any new patterns in daydream reports.

### Risk 2: Orphan recovery skipping valid but oddly-encoded sessions
**Impact:** Legitimate sessions with non-UTF8 data in Redis fields get skipped
**Mitigation:** Use `errors='replace'` rather than `errors='ignore'` so data is preserved (with replacement characters). Log the original bytes at WARNING level for forensic analysis.

## No-Gos (Out of Scope)

- SDLC stop hook violations (940 errors) -- expected behavior during direct-to-main commits
- Telegram connection drops -- normal, already handled by reconnect logic
- Memory warnings -- monitoring only, no fix needed
- Redesigning popoto's KeyField index system
- Automated Perplexity credential rotation

## Update System

No update system changes required -- all fixes are internal to bridge and monitoring code. The changes will propagate via normal git pull during updates. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The fixes affect monitoring code (`session_watchdog.py`), queue internals (`job_queue.py`), a tool provider (`perplexity.py`), and logging configuration. None of these are exposed through MCP servers or need to be callable by the agent.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` with notes on the watchdog duplicate key guard
- [ ] Add entry to `docs/features/README.md` index table if a new doc is created
- [ ] Add inline code comments on the new guard clauses explaining why they exist

## Success Criteria

- [ ] Watchdog duplicate key errors eliminated (< 10 per day instead of 22K)
- [ ] job_id validation errors fixed at source (0 occurrences)
- [ ] Orphan recovery handles corrupted UTF-8 data gracefully (no crashes)
- [ ] Perplexity 401 errors logged with clear message about credential refresh
- [ ] Log rotation configured for bridge.error.log (max 10MB, 3 backups)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (error-fixes)**
  - Name: error-fixer
  - Role: Implement all 5 error fixes and log rotation
  - Agent Type: builder
  - Resume: true

- **Validator (error-fixes)**
  - Name: error-validator
  - Role: Verify fixes reduce error rates and don't suppress real errors
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix watchdog duplicate key errors
- **Task ID**: build-watchdog-guard
- **Depends On**: none
- **Assigned To**: error-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `ModelException` catch around `session.save()` calls in `fix_unhealthy_session()`
- Add existence check before save (query session by ID, skip if gone)
- Log caught exceptions at WARNING level in bridge.log

### 2. Fix job_id validation errors
- **Task ID**: build-jobid-fix
- **Depends On**: none
- **Assigned To**: error-fixer
- **Agent Type**: builder
- **Parallel**: true
- Trace source of 60-char job IDs from error log stack traces
- Fix the caller that sets explicit job_id values
- Verify `_extract_job_fields()` properly excludes `job_id`

### 3. Harden orphan recovery
- **Task ID**: build-orphan-recovery
- **Depends On**: none
- **Assigned To**: error-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `errors='replace'` to `.decode()` calls in `_recover_orphaned_jobs()`
- Log corrupted byte content at WARNING level for forensics
- Add try/except around `decode_popoto_model_hashmap()` with better error message

### 4. Fix Perplexity error handling
- **Task ID**: build-perplexity-fix
- **Depends On**: none
- **Assigned To**: error-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add specific `httpx.HTTPStatusError` handling for 401 errors
- Log warning with clear credential refresh message
- Keep existing fallback behavior (return None)

### 5. Add log rotation
- **Task ID**: build-log-rotation
- **Depends On**: none
- **Assigned To**: error-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add stderr RotatingFileHandler in bridge logging setup OR add rotation logic to valor-service.sh
- Configure: max 10MB per file, keep 3 backups
- Add startup check in valor-service.sh to rotate if > 10MB

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-watchdog-guard, build-jobid-fix, build-orphan-recovery, build-perplexity-fix, build-log-rotation
- **Assigned To**: error-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no regressions in existing tests
- Review code for suppressed real errors
- Verify log rotation config works

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: error-fixer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with watchdog guard info
- Add inline code comments on guard clauses

## Validation Commands

- `pytest tests/unit/test_session_watchdog.py -v` - Verify watchdog tests pass
- `pytest tests/ -v` - Full test suite
- `black . && ruff check .` - Code quality
- `grep -c "ModelException" monitoring/session_watchdog.py` - Verify guard is in place
- `grep -c "errors=" agent/job_queue.py` - Verify UTF-8 decode hardening
- `grep -c "HTTPStatusError\|401" tools/web/providers/perplexity.py` - Verify Perplexity error handling
