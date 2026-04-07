# SDLC Pipeline Integrity

Three targeted fixes addressing session context loss, URL validation, and unauthorized merges in the autonomous SDLC pipeline.

## Problem

The pipeline had three integrity gaps:

1. **Session metadata lost during continuation**: When `_enqueue_continuation` couldn't find the AgentSession in Redis, it fell back to `enqueue_agent_session()` which only passed a subset of fields, losing `context_summary`, `expectations`, `issue_url`, `pr_url`, and stage history.

2. **Unvalidated URLs from worker output**: The Observer's `update_session` tool accepted URLs verbatim from worker text, allowing wrong-repo URLs to propagate through status messages.

3. **No merge guard**: Workers could execute `gh pr merge` without human authorization, violating the SDLC principle that MERGE is a human decision.

## Solution

### A. Session Continuation Hardening

**File**: `agent/agent_session_queue.py`

The fallback path in `_enqueue_continuation` now uses `_extract_agent_session_fields(session._rj)` to preserve ALL metadata from the underlying AgentSession, including session-phase fields that `enqueue_agent_session()` doesn't accept as parameters.

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

### D. SubagentStop Stage State Injection

**File**: `agent/hooks/subagent_stop.py`

When a dev-session subagent completes, the SubagentStop hook now injects the current SDLC pipeline state back into the PM (PM session) context. This prevents the PM from fabricating stage completion claims (e.g., claiming "review passed" without running `/do-pr-review`).

The hook:
1. Detects `agent_type == "dev-session"` completions
2. Reads `stage_states` from the AgentSession in Redis (the legacy `sdlc_stages` field was removed in PR #490)
3. Returns `{"reason": "Pipeline state: {dict}"}` so the PM sees which stages are actually complete vs still pending

This creates a feedback loop: the PM dispatches a dev-session to run a stage, the dev-session updates `stage_states` during execution, and the SubagentStop hook feeds the updated state back to the PM before it decides the next action.

**Related commit**: `c7e5a55d` (simplified from initial `9829690d`)

## Known Tech Debt

### ~~`r.keys()` in `_diagnose_missing_session`~~ (Resolved)

Previously used raw `r.keys(f"*{session_id}*")` which is O(N) across the entire Redis keyspace. Refactored in [Popoto Index Hygiene](popoto-index-hygiene.md) (PR #650) to use Popoto-native queries (`AgentSession.query.filter()`) and targeted `POPOTO_REDIS_DB.exists()` checks instead of raw Redis operations.

## Testing

- 28 new tests in `tests/unit/test_pipeline_integrity.py`
- Updated existing tests in `test_pipeline_graph.py`, `test_observer.py`, `test_summarizer.py`
- 30 tests in `tests/unit/test_health_check.py` covering watchdog unhealthy flag and stage injection

## Tracking

- Issue: #417
- PR: #419
- Related: #400 (session metadata loss), #409 (merge guard), #489 (stage injection + watchdog kill)
