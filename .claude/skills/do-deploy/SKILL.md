---
name: do-deploy
description: "Use when deploying merged changes to production across bridge machines. Triggered by 'deploy to prod', 'ship it', 'push to prod', or 'do-deploy'."
argument-hint: "<pr-number>"
context: fork
model: sonnet
effort: low
---

# Deploy to Production

Confirm a merged PR is live on this machine and report what the rest of the fleet will pick up. In this repo, merging to main is the deploy: every other machine runs `remote-update.sh` on a cron that fast-forwards main, syncs deps, and restarts the bridge. There is no manual promotion step. This skill is not part of the SDLC pipeline, which ends at merge.

DEPLOY_ARG: $ARGUMENTS (a PR number; if empty or literally `$ARGUMENTS`, take it from the user's message; if none, use the most recently merged PR).

**Done when** the report below is filled with evidence: the PR is confirmed merged, this machine has the merge commit, bridge and worker are running with Telegram connected, and the remote machines are listed.

## Constraints

- **Never deploy unmerged code.** Resolve the PR (`gh pr view <N> --json number,title,state,mergedAt,mergeCommit` or `gh pr list --state merged --limit 1 --json ...`). If it is not merged, stop: `Deploy blocked: PR #N is not merged. Run /do-merge first.`
- **Never skip the health check** after bringing this machine current.
- **Never force-update remote machines.** Let their cron pick up the change.

## Facts

- Bring this machine current with `git checkout main && git fetch origin main && git merge --ff-only origin/main`, then confirm `git log --oneline -1 <merge-commit>`. Never a bare pull (#2650): `FETCH_HEAD` is shared by every worktree, and a deploy runs during exactly the multi-lane conditions where a peer's fetch retargets it.
- Health: `./scripts/valor-service.sh status` and `worker-status`; `logs/bridge.log` should show `Connected to Telegram` and no fresh `ERROR` lines. If the bridge is down or erroring, `./scripts/valor-service.sh restart` and re-check.
- Fleet: each project's `machine` field in `~/Desktop/Valor/projects.json`, minus this machine's `scutil --get ComputerName`, is the set that will auto-update on its next cron cycle. That file reflects the fleet only on a fleet machine.
- A machine misbehaving after an update may be missing a one-time human step the cron cannot do. `gws` (Google Workspace CLI) ships unauthenticated: copy the shared vault OAuth client to `~/.config/gws/client_secret.json` and run `gws auth login` (see `docs/features/gws-cli-auth.md`).

## Report

```
## Deploy Report: PR #{PR_NUMBER}

**PR**: {PR_TITLE}
**Commit**: {MERGE_COMMIT}
**Method**: Merge to main (auto-update cron on remote machines)

### Local Machine
- Bridge status: {running/restarted/failed}
- Worker status: {running/failed}
- Telegram connected: {yes/no}
- Errors since deploy: {count}

### Remote Machines
{machines that will auto-update on next cron cycle}

### Rollback
If needed: `git revert {MERGE_COMMIT} --no-edit && git push origin main`
All machines pick up the revert on their next cron cycle.
```
