# SDLC Stage Handoff via GitHub Issue Comments

## Overview

SDLC stages post structured comments to the tracking GitHub issue on completion and read prior stage comments on startup. This turns the GitHub issue into the living record of stage-by-stage progress, giving each stage context about what previous stages discovered.

## Problem

Without stage handoff, each Dev session starts with only the plan document and its task prompt. Discoveries, decisions, and blockers from prior stages are lost between sessions. The Test stage does not know that Build hit a tricky edge case; the Review stage cannot see what Test already validated.

## How It Works

Two execution paths write stage tracking records: the **dev-session path** (for DevSession Agent tool calls) and the **Skill path** (for PM Skill tool calls). Both write to `AgentSession.stage_states` via `PipelineStateMachine`, enabling the dashboard to show real progress for all session types.

### Skill Tool Path (PM Sessions)

When a PM session invokes `Skill(skill="do-build")` (or any other SDLC skill), the `pre_tool_use` and `post_tool_use` hooks in `agent/hooks/` intercept the call:

**On skill start (`pre_tool_use.py`):**
1. Detects `tool_name == "Skill"` in the hook input
2. Looks up the skill name in `_SKILL_TO_STAGE` (e.g., `"do-build"` → `"BUILD"`)
3. Resolves the bridge session ID via `session_registry.resolve(claude_uuid)`
4. Calls `_start_pipeline_stage(session_id, stage)` to mark the stage `in_progress`
5. Silently no-ops for unknown skills (non-SDLC skills like `do-discover-paths`)

**On skill completion (`post_tool_use.py`):**
1. Detects `tool_name == "Skill"` and checks if the skill is in `_SKILL_TO_STAGE`
2. Resolves the session ID the same way
3. Calls `_complete_pipeline_stage(session_id)` which reads `current_stage()` from Redis and calls `complete_stage()`
4. Avoids storing state between pre and post hooks — reads the in_progress stage from Redis directly

**`_SKILL_TO_STAGE` mapping** (in `agent/hooks/pre_tool_use.py`):

```python
_SKILL_TO_STAGE = {
    "do-plan": "PLAN",
    "do-plan-critique": "CRITIQUE",
    "do-build": "BUILD",
    "do-test": "TEST",
    "do-patch": "PATCH",
    "do-pr-review": "REVIEW",
    "do-docs": "DOCS",
    "do-merge": "MERGE",
}
```

All errors are swallowed with `logger.warning` — hooks never crash the PM session.

### On Stage Completion (SubagentStop Hook — Dev-Session Path)

When a Dev session completes, the `subagent_stop_hook` in `agent/hooks/subagent_stop.py`:

1. Resolves the tracking issue number from `SDLC_TRACKING_ISSUE` env var, `SDLC_ISSUE_NUMBER` env var, or plan frontmatter (`tracking:` field)
2. Extracts the current stage name from the PipelineStateMachine stage states
3. Extracts an output tail (last ~500 chars) via `_extract_output_tail()` for outcome classification
4. Calls `classify_outcome(stage, stop_reason, output_tail)` on the PipelineStateMachine to determine success/fail/ambiguous
5. Routes to `complete_stage()` (success or ambiguous) or `fail_stage()` (fail or partial) based on the classification
6. Calls `post_stage_comment()` from `utils/issue_comments.py` to post a structured markdown comment
7. All of this is wrapped in try/except -- classification and comment posting never crash the hook

### On Stage Start (PM session Prompt Enrichment)

The PM session PM instructions in `agent/sdk_client.py` include a step to gather prior stage context before spawning a Dev session:

1. PM session fetches issue comments via `gh api repos/{owner}/{repo}/issues/{number}/comments`
2. Filters for comments matching the structured stage format (identified by `<!-- sdlc-stage-comment -->` marker)
3. Appends a "Prior stage findings" section to the Dev session prompt
4. Limited to the last 5 stage comments to prevent context bloat

### Comment Format

Each stage comment follows a standardized markdown template:

```markdown
<!-- sdlc-stage-comment -->
## Stage: BUILD
**Outcome:** All tasks completed, 3 files created

### Key Findings
- Auth middleware required special handling for token refresh
- Added retry logic for flaky API calls

### Files Modified
- `utils/issue_comments.py`
- `agent/hooks/subagent_stop.py`

### Notes for Next Stage
Focus testing on error handling paths -- the happy path is straightforward.
```

The `<!-- sdlc-stage-comment -->` HTML comment marker enables reliable filtering of stage comments from regular human comments on the issue.

## Outcome Classification and Failure Routing

As of PR #601, every dev-session completion triggers outcome classification before stage routing. This wires the previously-dead `classify_outcome()` and `fail_stage()` code paths into production.

### Classification Flow

```
Dev session completes
  -> _extract_output_tail(input_data)    # last ~500 chars from transcript
  -> sm.classify_outcome(stage, stop_reason, output_tail)
      |
      +-- Tier 0: Parse <!-- OUTCOME {...} --> contract -> "success" / "fail" / "partial"
      +-- Tier 1: SDK stop_reason != "end_turn" -> "fail"
      +-- Tier 2: Deterministic tail patterns scoped by stage -> "success" / "fail"
      +-- Default: "ambiguous"
  -> Route:
      "success" or "ambiguous" -> sm.complete_stage()
      "fail" or "partial"      -> sm.fail_stage()
```

Tier 0 parses structured OUTCOME contracts that skills emit (e.g., `/do-pr-review` emits `<!-- OUTCOME {"status":"partial",...} -->`). This enables the `("REVIEW", "partial") -> PATCH` routing edge for tech debt and nit findings. See `docs/features/pipeline-state-machine.md` for the full three-tier classification reference.

### Failure Routing via Pipeline Graph

When `fail_stage()` fires, the pipeline graph (`PIPELINE_EDGES` in `bridge/pipeline_graph.py`) determines the next stage. Key failure edges:

| Current Stage | Outcome | Next Stage |
|---------------|---------|------------|
| TEST | fail | PATCH |
| REVIEW | fail | PATCH |
| REVIEW | partial | PATCH |
| PATCH | success | TEST |
| PATCH | fail | TEST |

This enables automatic PATCH cycles: a REVIEW that finds issues routes to PATCH, which routes back to TEST, which routes back to REVIEW. The cycle is bounded by `MAX_PATCH_CYCLES` (default 3) -- when reached, `get_next_stage()` returns None and the pipeline escalates to human review.

### Safe Defaults

- `classify_outcome()` errors default to `complete_stage()` (never crashes)
- "ambiguous" classification defaults to success (avoids false PATCH triggers)
- `SubagentStopHookInput` lacks a `stop_reason` field, so `stop_reason=None` is passed by default -- classification relies primarily on output tail pattern matching

## Key Components

### `utils/issue_comments.py`

Utility module with four functions:

- `fetch_stage_comments(issue_number, repo)` -- Fetches and parses stage-formatted comments from a GitHub issue. Returns a list of dicts with `stage`, `outcome`, and `body` keys. Returns empty list on any failure.
- `post_stage_comment(issue_number, stage, outcome, findings, files, notes, repo)` -- Formats and posts a structured comment. Returns True on success, False on failure. Never raises.
- `format_stage_comment(stage, outcome, findings, files, notes)` -- Formats the markdown comment body without posting it.
- `format_prior_context(comments, max_comments)` -- Formats fetched comments into a context string suitable for prompt injection.

All GitHub interactions use the `gh` CLI via subprocess with a 10-second timeout. No new Python dependencies.

### `agent/hooks/subagent_stop.py`

Extended with these functions:

- `_resolve_tracking_issue()` -- Resolves the tracking issue number from environment variables (`SDLC_TRACKING_ISSUE`, `SDLC_ISSUE_NUMBER`) or by parsing plan frontmatter. Returns int or None.
- `_extract_output_tail(input_data, max_chars=500)` -- Reads the last ~500 chars from the agent transcript file for outcome classification. Falls back to `_extract_outcome_summary()` if the transcript is unavailable.
- `_record_stage_on_parent(parent_session_id, stop_reason, output_tail)` -- Loads the parent PM session, calls `classify_outcome()` on its PipelineStateMachine, and routes to `complete_stage()` or `fail_stage()` based on the result. Errors default to `complete_stage()`.
- `_post_stage_comment_on_completion(input_data, current_stage)` -- Called after `_register_dev_session_completion()`. Posts the stage comment, wrapped in try/except so failures are non-fatal.

### `agent/sdk_client.py`

- Propagates `SDLC_TRACKING_ISSUE` environment variable when launching sessions with a tracking issue
- PM instructions updated to include prior stage context gathering step

## Design Decisions

- **GitHub as single source of truth** -- No dual-write to Redis or any other storage. The issue timeline is the canonical record.
- **Append-only comments** -- New comments per stage, never edit old ones. Duplicate comments from stage re-runs are informative, not harmful.
- **Graceful degradation** -- All comment operations fail silently. Missing tracking issue, gh CLI failures, and timeouts all result in logged warnings but never crash the pipeline.
- **No LLM extraction** -- Uses the existing `_extract_outcome_summary()` pattern for outcome extraction rather than adding LLM-powered extraction.

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| No tracking issue configured | Comment posting skipped silently |
| `gh` CLI not available or not authenticated | Returns False / empty list, logs warning |
| `gh` API timeout (>10s) | Returns False / empty list, logs warning |
| Non-existent issue number | `gh` returns error, logged as warning |
| Empty findings list | Posts comment with "No notable findings" placeholder |
| Rate limiting | `gh` CLI handles rate limit retries automatically |

## Related

- [Pipeline Graph](pipeline-graph.md) -- Canonical graph defining stage transitions and failure edges
- [Pipeline State Machine](pipeline-state-machine.md) -- Stage tracking that the hook reads from
- [Observer Agent](observer-agent.md) -- SDLC routing that triggers stage transitions
- [Skill Context Injection](skill-context-injection.md) -- Environment variable propagation pattern
- GitHub Issue: [#520](https://github.com/tomcounsell/ai/issues/520) (stage handoff), [#563](https://github.com/tomcounsell/ai/issues/563) (graph wiring), [#782](https://github.com/tomcounsell/ai/issues/782) (Skill tool path)
- PR: [#523](https://github.com/tomcounsell/ai/pull/523) (stage handoff), [#601](https://github.com/tomcounsell/ai/pull/601) (graph wiring)
