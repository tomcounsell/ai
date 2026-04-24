# SDLC Stage Tracking

## Overview

Pipeline stage completion is tracked exclusively through `PipelineStateMachine.stage_states` stored in Redis. There is no artifact inference — a stage is considered complete only if it was explicitly dispatched and completed.

## How Stage State Is Written

Two complementary paths write stage markers:

### 1. Bridge hooks (primary path — bridge-initiated sessions)

- `pre_tool_use.py` detects dev-session Agent tool use, extracts the stage name from the prompt, and calls `PipelineStateMachine.start_stage()` on the parent PM session.
- The worker post-completion handler `_handle_dev_session_completion()` in `agent/agent_session_queue.py` fires on dev-session completion, calls `classify_outcome()` then `complete_stage()` or `fail_stage()`. (Prior to the Phase 5 harness migration this logic lived in the SDK `SubagentStop` hook at `agent/hooks/subagent_stop.py`, which was stripped to logging-only and then deleted in issue #1024.)

This path fires automatically for all sessions initiated through the Telegram bridge.

### 2. Skill stage markers (belt-and-suspenders — local Claude Code sessions)

Each SDLC skill writes explicit in_progress/completed markers using `tools/sdlc_stage_marker.py`:

```bash
python -m tools.sdlc_stage_marker --stage DOCS --status in_progress --issue-number {issue_number} 2>/dev/null || true
# ... skill work ...
python -m tools.sdlc_stage_marker --stage DOCS --status completed --issue-number {issue_number} 2>/dev/null || true
```

Skills with markers:
- `do-issue` → ISSUE stage
- `do-plan` → PLAN stage
- `do-plan-critique` → CRITIQUE stage
- `do-pr-review` → REVIEW stage
- `do-docs` → DOCS stage

The tool resolves the PM session in priority order: `--session-id` argument, `VALOR_SESSION_ID` env var, `AGENT_SESSION_ID` env var, then `--issue-number` argument (which finds the PM session tracking that issue via `find_session_by_issue()`). For local Claude Code sessions, `--issue-number` is the primary resolution path since env vars don't persist across bash blocks. It fails silently (exit 0, empty JSON `{}`) if no session is found — skill execution is never interrupted by marker failures.

## `tools/sdlc_stage_marker.py` CLI

```bash
python -m tools.sdlc_stage_marker --stage <STAGE> --status <in_progress|completed>
python -m tools.sdlc_stage_marker --stage REVIEW --status completed --session-id <ID>
python -m tools.sdlc_stage_marker --stage PLAN --status completed --issue-number 941
```

Exit code is always 0. Returns `{}` on error, `{"stage": "DOCS", "status": "completed"}` on success.

## `get_display_progress()` — Stored State Only

`PipelineStateMachine.get_display_progress()` returns stored stage states only. It does NOT:

- Infer stages from plan files on disk
- Query GitHub PRs to infer BUILD/TEST/REVIEW/DOCS completion
- Accept a `slug=` parameter (the keyword was dropped in #729)

This was intentional. Artifact inference caused stage skipping (#723, #729): a plan file created by `/do-build` as a deliverable would incorrectly satisfy DOCS without `/do-docs` ever running.

## do-merge Gate

The merge gate (`/do-merge`) reads stored state only via `get_display_progress()`. When stages show as pending/unrecorded:

- **All stages pending** (cold start / Redis cleared): Shows a warning that no pipeline state was found. Requires explicit acknowledgment before proceeding.
- **Specific stages skipped**: Shows a strong warning listing every skipped stage. Requires explicit acknowledgment of each. Emergency hotfixes may proceed after acknowledgment.

The gate is a reminder, not a hard blocker. The agent can choose to proceed, but only after explicit on-record acknowledgment.

## Local Session Creation

For local Claude Code sessions, the SDLC router ensures a trackable session exists before dispatching sub-skills via `tools/sdlc_session_ensure.py`. This creates an `AgentSession` keyed by `sdlc-local-{issue_number}` so that downstream markers have a session to write to. The operation is idempotent — running it multiple times for the same issue reuses the same session.

Inside a bridge-initiated session (where `VALOR_SESSION_ID` is set), the call short-circuits: it returns the already-active bridge PM session without creating a new `sdlc-local-{N}` record. This prevents the zombie-duplicate dashboard entries that used to appear when SDLC was driven over Telegram. See the "Bridge short-circuit" subsection in `docs/features/sdlc-pipeline-state.md` for the full gate conditions and the `--kill-orphans` operator tool for cleaning up pre-existing zombies.

```bash
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
python -m tools.sdlc_session_ensure --issue-number 941 --issue-url "https://github.com/$SDLC_REPO/issues/941"
```

The `SDLC_REPO` value is shell-derived to avoid hardcoding the repo owner/name. `tools/sdlc_session_ensure.py` resolves `project_key` dynamically via `resolve_project_key(cwd)`, then derives `working_dir` from `projects.json[project_key].working_directory` (not `os.getcwd()`) via `_resolve_project_working_directory()` — enforcing the immutable project→repo pairing from issue #1158. If resolution fails, the function returns `{}` (idempotent no-op) rather than creating a mis-scoped session.

See `docs/features/sdlc-pipeline-state.md` for the full local session tracking design.

## SDLC Router Fallback

When `stage_states` is unavailable (local Claude Code with no session created yet), the SDLC router uses conversation dispatch history to determine what has already been dispatched in the current session. It does not infer from artifacts. If nothing has been dispatched, it starts from the beginning of the pipeline.

## Why Artifact Inference Was Removed

Artifact inference was deleted in PR #733 (issue #729) because:

1. Build artifacts can satisfy stages that were never dispatched. A `docs/features/` file created by `/do-build` would mark DOCS as completed, skipping `/do-docs`.
2. The inference was unreliable (GitHub API timeouts, plan file format edge cases).
3. Stored state is the single source of truth. Any deviation from that creates two conflicting signals.

See `docs/plans/sdlc-stage-skip-prevention.md` for the full problem analysis and fix rationale.
