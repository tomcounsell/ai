# SDLC Pipeline Integrity

Three targeted fixes addressing session context loss, URL validation, and unauthorized merges in the autonomous SDLC pipeline.

## Problem

The pipeline had three integrity gaps:

1. **Session metadata lost during continuation**: When `_enqueue_continuation` couldn't find the AgentSession in Redis, it fell back to `enqueue_job()` which only passed a subset of fields, losing `context_summary`, `expectations`, `issue_url`, `pr_url`, and stage history.

2. **Unvalidated URLs from worker output**: The Observer's `update_session` tool accepted URLs verbatim from worker text, allowing wrong-repo URLs to propagate through status messages.

3. **No merge guard**: Workers could execute `gh pr merge` without human authorization, violating the SDLC principle that MERGE is a human decision.

## Solution

### A. Session Continuation Hardening

**File**: `agent/job_queue.py`

The fallback path in `_enqueue_continuation` now uses `_extract_job_fields(job._rj)` to preserve ALL metadata from the underlying AgentSession, including session-phase fields that `enqueue_job()` doesn't accept as parameters.

A `_diagnose_missing_session()` helper checks Redis directly (key existence, TTL) before the fallback, providing diagnostic info in error logs for debugging session loss.

### B. Deterministic URL Construction

**File**: `bridge/observer.py`

Added `_construct_canonical_url(url, gh_repo)` which extracts the issue/PR number from worker-provided URLs via regex, then constructs the canonical URL using `GH_REPO` from the environment. This prevents wrong-repo URLs from propagating.

Applied in both:
- `_handle_update_session()` (tool-based updates from Observer LLM)
- State machine outcome classification path

Invalid URLs (no extractable number, non-GitHub URLs, empty/None) are logged at warning level and discarded.

### C. Merge Guard

**Files**:
- `.claude/hooks/validators/validate_merge_guard.py` - PreToolUse hook
- `.claude/settings.json` - Hook registration
- `.claude/commands/do-merge.md` - Gated merge skill
- `bridge/pipeline_state.py` - PipelineStateMachine manages stage transitions
- `bridge/pipeline_graph.py` - MERGE in DISPLAY_STAGES, STAGE_TO_SKILL, routing
- `models/agent_session.py` - MERGE in SDLC_STAGES

The merge guard is a PreToolUse hook that regex-matches `\bgh\s+pr\s+merge\b` in Bash commands and blocks with a message directing to `/do-merge`. It allows `gh pr merge --help` and doesn't trigger on echo/printf commands.

MERGE is now a proper pipeline stage routed after DOCS completes. The `/do-merge` skill checks prerequisites (TEST, REVIEW, DOCS completed) before presenting the PR for human merge authorization.

## Pipeline Flow

```
ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
                           |                  |
                           v                  v
                         PATCH <------------+
                           |
                           v
                          TEST (re-verify)
```

MERGE is the terminal stage, gated by human authorization.

## Known Tech Debt

### `r.keys()` in `_diagnose_missing_session` (agent/job_queue.py:1271)

The diagnostic function uses `r.keys(f"*{session_id}*")` which is O(N) across the entire Redis keyspace. Redis documentation explicitly warns against using `KEYS` in production. This is acceptable today because:

- It only runs on the error path (session not found during continuation)
- Our Redis instance is small (hundreds of keys, not millions)
- The function is diagnostic — it aids debugging, not critical path

**When to fix:** If Redis grows beyond ~10k keys, or if session-not-found errors become frequent enough that this diagnostic runs regularly. Replace with `SCAN` cursor iteration:

```python
cursor = 0
keys = []
while True:
    cursor, batch = r.scan(cursor, match=f"*{session_id}*", count=100)
    keys.extend(batch)
    if cursor == 0:
        break
```

**Tracked in:** PR #419 review comment

## Testing

- 28 new tests in `tests/unit/test_pipeline_integrity.py`
- Updated existing tests in `test_pipeline_graph.py`, `test_observer.py`, `test_summarizer.py`
- All 1752 unit tests pass

## Tracking

- Issue: #417
- PR: #419
- Related: #400 (session metadata loss), #409 (merge guard)
