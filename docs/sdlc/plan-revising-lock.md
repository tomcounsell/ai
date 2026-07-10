# SDLC Addendum: Plan-Revising Lock (`_meta.plan_revising`)

**Introduced in:** issue #1302 — "SDLC state machine: build must block on in-flight plan critique"

## Overview

`_meta.plan_revising` is a bool flag stored in `stage_states["_plan_revising"]` on the PM session. It signals that a critique-driven revision pass is pending — the critique has identified issues that require plan edits before build can proceed.

The flag activates guard G7 in `agent/sdlc_router.py`, which blocks `/do-build` dispatch and redirects the pipeline to `/do-plan`.

## Set and Clear Contract

| Who | When | Action |
|-----|------|--------|
| `/do-plan-critique` Step 5.6 | Verdict is NEEDS REVISION, MAJOR REWORK, or READY TO BUILD (with concerns) AND `revision_applied` not yet true | `sdlc-tool meta-set --key plan_revising --value true --issue-number N --run-id "$RUN_ID"` |
| `/do-plan` Phase 4 Step 2b | After committing the revised plan and writing `revision_applied: true` to frontmatter | `sdlc-tool meta-set --key plan_revising --value false --issue-number N --run-id "$RUN_ID"` |

Run identity (#2003): every state-mutating `sdlc-tool` call on this page
carries `--run-id "$RUN_ID"` — supplied by the invoking supervisor (`/do-sdlc`
or `/sdlc` carries it from `session-ensure`). When operating standalone (no
supervisor, e.g. the manual recovery below), run
`sdlc-tool session-ensure --issue-number N` once and use the emitted `run_id`
(`ISSUE_LOCKED` means another live run owns the issue — stop and report).
Read-only calls (`stage-query`) take no run-id.

**Important:** `plan_revising` and `revision_applied` must move together. Both reflect "the plan is settled." If the lock-clear step is skipped (e.g. skill crash after `revision_applied: true` was written), G7 self-heals automatically via the `revision_applied` conjunction.

## G7 Guard Logic

```
guard_g7_plan_revising(stage_states, meta, context):
  1. pr_number is set → None (G3/G6 own PR-stage routing)
  2. plan_revising is falsy → None (lock not set)
  3. plan_revising AND revision_applied → None (self-heal)
  4. plan_revising AND last_skill == /do-plan-critique → Dispatch(/do-plan)
  5. plan_revising AND no /do-plan in recent MAX+1 history → Blocked(G7)
  6. otherwise → None (plan dispatch already in recent history)
```

## Manual Recovery

If the lock is stuck (critique set it but no plan dispatch occurred):

```bash
# Clear the lock manually
sdlc-tool meta-set --key plan_revising --value false --issue-number N --run-id "$RUN_ID"

# Verify it was cleared
sdlc-tool stage-query --issue-number N | python -c "import sys,json; d=json.load(sys.stdin); print(d['_meta']['plan_revising'])"
```

## Related Meta Field: `plan_hash_at_build_start`

A companion field, `_plan_hash_at_build_start` (str|None), stores the git commit hash of the plan document at the moment `/do-build` begins. This enables a defense-in-depth check at Step 21 (pre-PR) that aborts the build if the plan was revised mid-execution.

The hash is set by `/do-build` Step 7 and verified at Step 21:

```bash
# Step 7: record hash
PLAN_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
git -C "$PLAN_REPO" fetch origin main 2>/dev/null || true
PLAN_HASH=$(git -C "$PLAN_REPO" log -1 --format=%H origin/main -- "$PLAN_REL")
sdlc-tool meta-set --key plan_hash_at_build_start --value "$PLAN_HASH" --issue-number N --run-id "$RUN_ID"

# Step 21: verify (aborts if changed)
CURRENT_HASH=$(git -C "$PLAN_REPO" log -1 --format=%H origin/main -- "$PLAN_REL")
STORED_HASH=$(sdlc-tool stage-query --issue-number N | python -c "...")
if [ -n "$STORED_HASH" ] && [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
  echo "BUILD ABORT: plan revised mid-build"
  sdlc-tool stage-marker --stage BUILD --status failed --issue-number N --run-id "$RUN_ID"
  exit 1
fi
```

The check is a no-op when `STORED_HASH` is empty (pre-#1302 sessions, or untracked plan files).
