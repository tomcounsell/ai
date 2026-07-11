# SDLC Stage Tracking

## Overview

Pipeline stage completion is tracked exclusively through stored stage state — there is no artifact inference; a stage is considered complete only if it was explicitly dispatched and completed.

As of issue #2012, the durable copy of that state lives in an **issue-keyed** record, `PipelineLedger` — not on the ephemeral `AgentSession` that happened to be doing the work. See [`docs/features/sdlc-issue-keyed-stage-ledger.md`](sdlc-issue-keyed-stage-ledger.md) for the full design; this document covers how markers get written day to day.

## Issue-Keyed Ledger (issue #2012)

`PipelineStateMachine` — the class every stage-transition call (`start_stage`, `complete_stage`, `fail_stage`, `get_display_progress`) goes through — now supports two backing stores, selected at construction time:

- **`PipelineStateMachine(session)`** (original) — reads/writes `AgentSession.stage_states`. Session-keyed: the record disappears the moment the session does. Still used by the in-session skill hooks (path 1 below) and retained as the reader's cold-path fallback for pre-cutover records (path 2's reader).
- **`PipelineStateMachine.for_issue(target_repo, issue_number)`** (new) — reads/writes a durable `PipelineLedger` record keyed by the composite string `f"{target_repo}:{issue_number}"` (`agent/pipeline_ledger.py`). This is the entity the pipeline is *about*, not whichever session is currently doing the work, so the record survives crashes, completions, and driver→takeover handoffs. All four `sdlc-tool` CLI writers (`stage-marker`, `verdict record`, `meta-set`, `dispatch record`) and their reader counterparts now construct via `for_issue()`.

Both paths share the exact same `StageStates` Pydantic validation and the `_load_preserved_metadata()` merge-on-save protocol (`agent/pipeline_state.py::PipelineStateMachine._save()`) — two concurrent writers for the same issue merge their `_verdicts`/`_sdlc_dispatches` rather than clobbering each other, regardless of which backing store they use.

**Write authority.** A caller may only write the issue-keyed ledger while it holds the per-issue run_id lease (`models/session_lifecycle.py::touch_issue_lock`, see [`docs/features/sdlc-issue-ownership-lock.md`](sdlc-issue-ownership-lock.md)). The lease also carries the pinned `target_repo` component of the ledger key, resolved once at lease-acquire time — writers never re-resolve it per call. Takeover of an issue is simply acquiring that same lease; the ledger itself never moves, because it never lived on either session.

## How Stage State Is Written

Two complementary paths write stage markers:

### 1. In-session Skill hooks (primary path — bridge-initiated sessions)

Stage state is written in-session as the Eng session invokes and returns from SDLC `/do-*` skills:

- `pre_tool_use.py::_start_pipeline_stage()` detects an SDLC Skill tool call, maps the skill name to a stage via `_SKILL_TO_STAGE`, and calls `PipelineStateMachine.start_stage()` to mark the stage `in_progress`.
- `post_tool_use.py::_complete_pipeline_stage()` fires when the Skill tool returns, reads the `in_progress` stage via `current_stage()`, and calls `complete_stage()`.

This path still constructs `PipelineStateMachine(session)` — the original session-keyed path, unaffected by issue #2012.

Only `complete_stage()` fires from the hook. `classify_outcome()` and `fail_stage()` remain defined in `agent/pipeline_state.py` but are orphaned — the worker post-completion handler that once called them was removed when the PM and Dev roles merged into the single `eng` role (PR #1691). Earlier still, the SDK `SubagentStop` hook (`agent/hooks/subagent_stop.py`) that originally carried stage tracking was stripped to logging-only in the Phase 5 harness migration and then deleted in issue #1024.

This path fires automatically for all sessions initiated through the Telegram bridge.

### 2. Skill stage markers (belt-and-suspenders — local Claude Code sessions)

Each SDLC skill writes explicit in_progress/completed markers using the `sdlc-tool stage-marker` wrapper (which dispatches into `tools/sdlc_stage_marker.py`):

```bash
sdlc-tool stage-marker --stage DOCS --status in_progress --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
# ... skill work ...
sdlc-tool stage-marker --stage DOCS --status completed --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
```

The wrapper resolves the `ai/` repo via `AI_REPO_ROOT` so the call works from any cwd (including target-repo cwds where a shadow `tools/` package would otherwise hijack module resolution). See `docs/features/sdlc-tool-resolver.md` for the full rationale.

Skills with markers:
- `do-issue` → ISSUE stage
- `do-plan` → PLAN stage
- `do-plan-critique` → CRITIQUE stage
- `do-pr-review` → REVIEW stage
- `do-docs` → DOCS stage

`--run-id` is now required (issue #2003) — the run identity emitted by `sdlc-tool session-ensure`. This tool no longer resolves or writes to an `AgentSession` at all: it validates `--run-id` against the per-issue lease (`resolve_ledger_lease()`/`revalidate_ledger_lease()` in `tools/_sdlc_utils.py`), reads the lease-pinned `target_repo`, and writes through `PipelineStateMachine.for_issue(target_repo, issue_number)`. A missing/foreign/repo-less lease is now a **loud** failure (stderr diagnostic, exit 1) — see the "Degradation contract" changes below.

## `sdlc-tool stage-marker` CLI

```bash
sdlc-tool stage-marker --stage <STAGE> --status <in_progress|completed> --issue-number <N> --run-id <hex>
sdlc-tool stage-marker --stage REVIEW --status completed --issue-number 941 --run-id abc123...
sdlc-tool stage-marker --stage PLAN --status completed --issue-number 941 --run-id abc123...
```

Exit codes changed under issue #2012's degradation contract, rebuilt around the run_id lease instead of a session:

- **0** — success, or Redis itself is unreachable (`{"status": "degraded", ...}` — the one case that stays quiet, since a genuine infra outage is not an owner/lease problem), or an idempotent already-completed no-op.
- **1** — the lease for this `run_id`+issue is absent, foreign (`ISSUE_LOCKED`), or carries no pinned `target_repo` (`TARGET_REPO_MISSING`); or the state-machine write itself rejects (a genuine stage misorder). There is no session left to fail to resolve to, so all of these are now loud — a stderr diagnostic plus non-zero exit, replacing the old quiet `PRESENT_NO_SESSION` no-op that previously caused issue #2012's deadlock.
- **2** — invalid arguments (missing `--run-id`).

The bare module form (`python -m tools.sdlc_stage_marker`) is the underlying entry point — runtime callers should always use the `sdlc-tool stage-marker` wrapper so the call is cwd-independent.

### Predecessor Backfill on the First Write (issue #1916)

A fresh pipeline entering at PLAN — the first marker write of the whole pipeline, with ISSUE still at `ready` — now backfills ISSUE to `completed` automatically on the `in_progress` write, rather than failing with the old `PRESENT_WRITE_FAILED` diagnostic (`sdlc_stage_marker: FAILED to write PLAN=in_progress ... State NOT persisted.`).

A `completed`-status write also backfills unrecorded predecessors, not just `in_progress` writes. This closes the asymmetry where a stage could be marked `completed` while its predecessors were never recorded, leaving ISSUE stuck at `ready` behind a completed later stage.

See "Predecessor Backfill (Opt-In)" in `docs/features/pipeline-state-machine.md` for the backfill mechanics (`start_stage(..., backfill_predecessors=True)`, `_backfill_predecessors()`, and the marker-vs-router semantics distinction).

## `get_display_progress()` — Stored State Only

`PipelineStateMachine.get_display_progress()` returns stored stage states only. It does NOT:

- Infer stages from plan files on disk
- Query GitHub PRs to infer BUILD/TEST/REVIEW/DOCS completion
- Accept a `slug=` parameter (the keyword was dropped in #729)

This was intentional. Artifact inference caused stage skipping (#723, #729): a plan file created by `/do-build` as a deliverable would incorrectly satisfy DOCS without `/do-docs` ever running.

## do-merge Gate

The merge gate (`/do-merge`) reads stored state only via `get_display_progress()`. As of issue #2012, that read resolves through `tools/sdlc_stage_query.py::_resolve_issue_record()` — the issue-keyed `PipelineLedger` first, with a retained session fallback for pre-cutover records — so the gate sees a driver's progress even after a takeover by a different session. When stages show as pending/unrecorded:

- **All stages pending** (cold start / Redis cleared): Shows a warning that no pipeline state was found. Requires explicit acknowledgment before proceeding.
- **Specific stages skipped**: Shows a strong warning listing every skipped stage. Requires explicit acknowledgment of each. Emergency hotfixes may proceed after acknowledgment.

The gate is a reminder, not a hard blocker. The agent can choose to proceed, but only after explicit on-record acknowledgment.

## Local Session Creation

For local Claude Code sessions, the SDLC router ensures a trackable session exists before dispatching sub-skills via `tools/sdlc_session_ensure.py`. This creates an `AgentSession` keyed by `sdlc-local-{issue_number}` so that downstream markers have a session to write to. The operation is idempotent — running it multiple times for the same issue reuses the same session.

Inside a bridge-initiated session (where `VALOR_SESSION_ID` is set), the call short-circuits: it returns the already-active bridge PM session without creating a new `sdlc-local-{N}` record. This prevents the zombie-duplicate dashboard entries that used to appear when SDLC was driven over Telegram. See the "Bridge short-circuit" subsection in `docs/features/sdlc-pipeline-state.md` for the full gate conditions and the `--kill-orphans` operator tool for cleaning up pre-existing zombies.

```bash
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
sdlc-tool session-ensure --issue-number 941 --issue-url "https://github.com/$SDLC_REPO/issues/941"
```

The `SDLC_REPO` value is shell-derived to avoid hardcoding the repo owner/name. `tools/sdlc_session_ensure.py` resolves `project_key` dynamically via `resolve_project_key(cwd)`, then derives `working_dir` from `projects.json[project_key].working_directory` (not `os.getcwd()`) via `_resolve_project_working_directory()` — enforcing the immutable project→repo pairing from issue #1158. If resolution fails, the function returns `{}` (idempotent no-op) rather than creating a mis-scoped session.

`session-ensure` is also where the issue-keyed ledger's `target_repo` gets pinned: `_acquire_run_lock_and_bind()` resolves it once (via `tools/_sdlc_utils.py::_resolve_target_repo()`) and passes it into every `touch_issue_lock()` call, so it lands in the run_id lease payload for every subsequent `sdlc-tool` writer/reader to consume — see [`docs/features/sdlc-issue-keyed-stage-ledger.md`](sdlc-issue-keyed-stage-ledger.md).

See `docs/features/sdlc-pipeline-state.md` for the full local session tracking design.

## SDLC Router Fallback

When `stage_states` is unavailable (local Claude Code with no session created yet), the SDLC router uses conversation dispatch history to determine what has already been dispatched in the current session. It does not infer from artifacts. If nothing has been dispatched, it starts from the beginning of the pipeline.

## Why Artifact Inference Was Removed

Artifact inference was deleted in PR #733 (issue #729) because:

1. Build artifacts can satisfy stages that were never dispatched. A `docs/features/` file created by `/do-build` would mark DOCS as completed, skipping `/do-docs`.
2. The inference was unreliable (GitHub API timeouts, plan file format edge cases).
3. Stored state is the single source of truth. Any deviation from that creates two conflicting signals.

See `docs/plans/sdlc-stage-skip-prevention.md` for the full problem analysis and fix rationale.
