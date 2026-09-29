# SDLC Addendum: Plan-Revising Lock (`_meta.plan_revising`)

## Overview

`_meta.plan_revising` is a bool flag stored in `stage_states["_plan_revising"]` on the PM session. It signals that a critique-driven revision pass is pending — the critique has identified issues that require plan edits before build can proceed.

The flag activates guard G7 in `agent/sdlc_router.py`, which blocks `/do-build` dispatch and redirects the pipeline to `/do-plan`.

## Set and Clear Contract

| Who | When | Action |
|-----|------|--------|
| `/do-plan-critique` Step 5.6 | Verdict is NEEDS REVISION, MAJOR REWORK, or READY TO BUILD (with concerns) — verdict kind and nothing else | `sdlc-tool meta-set --key plan_revising --value true --issue-number N --run-id "$RUN_ID"` |
| `/do-plan` Phase 4 Step 2b | After committing the revised plan and writing `revision_applied: true` to frontmatter | `sdlc-tool meta-set --key plan_revising --value false --issue-number N --run-id "$RUN_ID"` |

Run identity: every state-mutating `sdlc-tool` call on this page
carries `--run-id "$RUN_ID"` — supplied by the invoking supervisor (`/do-sdlc`
or `/sdlc` carries it from `session-ensure`). When operating standalone (no
supervisor, e.g. the manual recovery below), run
`sdlc-tool session-ensure --issue-number N` once and use the emitted `run_id`
(`ISSUE_LOCKED` means another live run owns the issue — stop and report).
Read-only calls (`stage-query`) take no run-id.

**Important:** if the lock-clear step is skipped (e.g. a skill crash after the revised plan was committed), G7 self-heals automatically — but on an **event-scoped** test, not the sticky boolean: gate 3 releases the lock only once a `/do-plan` revision has landed *after* the latest CRITIQUE verdict. Keying the release on `revision_applied` alone leaves the boolean permanently true after a plan's first revision, so the lock never binds again on plans that have been round the loop.

## G7 Guard Logic

```
guard_g7_plan_revising(stage_states, meta, context):
  1. pr_number is set → None (G3/G6 own PR-stage routing)
  2. plan_revising is falsy → None (lock not set)
  3. plan_revising AND a revision landed since the latest CRITIQUE verdict → None (self-heal, event-scoped)
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

`/do-build` records the plan's commit hash at build start and aborts before opening the PR if the
plan changed mid-build (a no-op when no hash was recorded). The commands live in
[`do-build.md`](do-build.md).
