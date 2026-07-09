# SDLC Pipeline State Tracking

Stage progress for the SDLC pipeline is tracked in Redis via `PipelineStateMachine` on the Eng session's `stage_states` field.

## How It Works

Each SDLC sub-skill (do-plan, do-build, do-docs, etc.) writes stage markers at start and completion:

```bash
sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number 941
sdlc-tool stage-marker --stage PLAN --status completed --issue-number 941
```

The SDLC router queries current state before dispatching:

```bash
sdlc-tool stage-query --issue-number 941
```

## Session Resolution

The stage marker resolves the Eng session in this order:

1. `--session-id` argument (explicit)
2. `VALOR_SESSION_ID` env var (bridge-injected)
3. `AGENT_SESSION_ID` env var (alternative)
4. `--issue-number` argument (local Claude Code sessions)

For local sessions, `--issue-number` is the primary path since env vars don't persist across Claude Code bash blocks.

## Local Session Creation

Before dispatching sub-skills, the SDLC router ensures a local session exists:

```bash
sdlc-tool session-ensure --issue-number 941 --issue-url "https://github.com/owner/repo/issues/941"
```

This creates an `AgentSession` with `session_id="sdlc-local-941"` and `session_type="eng"`. It's idempotent — running it again returns the existing session.

### Bridge short-circuit

Inside a bridge-initiated session (where `VALOR_SESSION_ID` is exported by `agent/sdk_client.py`), `ensure_session` short-circuits immediately:

1. Read `VALOR_SESSION_ID` (or `AGENT_SESSION_ID`) from the environment.
2. Resolve the session via `tools._sdlc_utils.find_session(session_id=...)`.
3. Confirm `session_type == "eng"` and `status not in TERMINAL_STATUSES`.
4. Return the bridge session id with `created: false` — no `sdlc-local-{N}` record is created.

The short-circuit falls through to the issue-number lookup and create path when:

- The env var is unset or empty.
- The env-resolved session does not exist in Redis (stale env).
- The env-resolved session has `session_type != "eng"` (e.g., a teammate session).
- The env-resolved session has a terminal status (completed, killed, abandoned, failed, cancelled).

The message-text fallback inside `find_session_by_issue` is a secondary defense for degraded scenarios where `VALOR_SESSION_ID` is missing but a bridge session exists with `issue_url=None` and `message_text="SDLC issue {N}"`. It matches the issue number inside `message_text` using a word-boundary regex (`\bissue\s*#?\s*{N}\b`, case-insensitive) so `tissue 1147` does not false-match.

### Orphan cleanup

Stale zombie `sdlc-local-{N}` sessions (running status, no heartbeats, **and no activity for over 10 minutes**) can be listed and finalized with:

```bash
# Preview without modifying (exits 0, prints JSON list)
sdlc-tool session-ensure --kill-orphans --dry-run

# Finalize each via models.session_lifecycle.finalize_session
sdlc-tool session-ensure --kill-orphans
```

The CLI always exits 0. Per-session finalize failures are reported inside the JSON payload's `failures` count and per-session `result` list — they never raise. When non-zero zombies are detected, a single stderr line (`[sdlc_session_ensure] found N zombie sdlc-local session(s)`) surfaces the count to scheduled-cleanup operators while stdout stays machine-parseable.

**Liveness is measured by last activity, not creation age (#1676).** On a skills-only (worker-less) machine, no worker writes `last_heartbeat_at`, so a *live* CLI-driven `/do-sdlc` pipeline used to match the zombie criteria by construction once its `created_at` aged past 10 minutes — and `--kill-orphans` would then `finalize(killed)` it mid-run, destroying its `stage_states` (the durable dispatch trail and verdicts the router depends on). The reaper now treats a session as a zombie only when it has BOTH no heartbeat AND no recent activity: last activity = `updated_at` (falling back to `started_at`, then `created_at`). Because every dispatch/verdict write goes through `tools.stage_states_helpers.update_stage_states` → `session.save()`, which stamps `updated_at`, a pipeline that advanced any stage within the last 10 minutes is exempt regardless of whether a worker heartbeat exists. Genuinely-dead orphans (created long ago, never advanced a stage) are still reaped on the `created_at` fallback.

## Verdict Storage and Normalization

Verdicts stored in `stage_states._verdicts[stage]["verdict"]` are always in canonical form (uppercase, underscores replaced by spaces, internal whitespace collapsed). `record_verdict()` in `tools/sdlc_verdict.py` normalizes at the write boundary, so records created before this fix may still have non-canonical forms in Redis — those are handled by read-side normalization in `agent/sdlc_router.py`. See [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) for the full normalization contract.

## `_meta` Keys

`sdlc-tool stage-query --issue-number N` returns an enriched `_meta` dict alongside the stage statuses. Current keys:

| Key | Type | Description |
|-----|------|-------------|
| `patch_cycle_count` | `int` | Number of PATCH cycles run so far |
| `critique_cycle_count` | `int` | Number of CRITIQUE cycles run so far |
| `latest_critique_verdict` | `str \| null` | Normalized critique verdict, e.g. `"NEEDS REVISION"` |
| `latest_review_verdict` | `str \| null` | Normalized review verdict, e.g. `"APPROVED"` |
| `revision_applied` | `bool` | Whether `revision_applied` frontmatter flag is set on the plan |
| `pr_number` | `int \| null` | Open PR number for this issue, if any |
| `pr_merge_state` | `str \| null` | `mergeStateStatus` from `gh pr view` (e.g. `"CLEAN"`) |
| `ci_all_passing` | `bool \| null` | `True` when all CI status checks are `SUCCESS` |
| `same_stage_dispatch_count` | `int` | Consecutive dispatches to the same stage without state change |
| `last_dispatched_skill` | `str \| null` | The most recently dispatched skill name |
| `plan_exists` | `bool` | `True` if a plan file is present on disk for the issue (added #1640) |
| `issue_number` | `int \| null` | Resolved issue number (added #1640) |

`plan_exists` and `issue_number` are computed by `_compute_meta()` in `tools/sdlc_stage_query.py`. They allow the router's `_rule_no_plan` to distinguish a genuine bootstrap (`PLAN=="ready"` with no plan file) from a completed plan whose status string survived a Redis flush.

## Key Files

| File | Purpose |
|------|---------|
| `tools/sdlc_stage_marker.py` | Write stage markers (in_progress/completed) |
| `tools/sdlc_stage_query.py` | Query current stage states; computes enriched `_meta` |
| `tools/sdlc_session_ensure.py` | Create/find local SDLC sessions |
| `tools/_sdlc_utils.py` | Shared `find_session_by_issue()` and `normalize_verdict()` helpers |
| `tools/sdlc_verdict.py` | Record/read verdicts; normalizes at write boundary |
| `agent/pipeline_state.py` | `PipelineStateMachine` reads/writes `stage_states` |

## Bridge vs Local

- **Bridge sessions**: Worker injects `VALOR_SESSION_ID` env var. Markers resolve via env var. `sdlc_session_ensure` short-circuits and does not create an `sdlc-local-{N}` record. Hooks also fire.
- **Local sessions**: No env var available. `--issue-number` resolves via `find_session_by_issue()` scanning Eng sessions by `issue_url` suffix and (fallback) by `message_text` regex.

Both paths write to the same `stage_states` field on the Eng session, so the merge gate and stage query work identically.
