---
name: do-deploy
description: "Use when deploying merged changes to production across bridge machines. Triggered by 'deploy to prod', 'ship it', 'push to prod', or 'do-deploy'."
argument-hint: "<pr-number>"
context: fork
---

# Deploy to Production

You are the **production deployment operator** for the Valor AI system.

This skill is **not part of the SDLC pipeline**. The SDLC pipeline ends at merge, and merge already handles the local dev environment.

## How Production Deployment Works

For this repo, production deployment is simple:

1. **Merge to main** -- this is the deploy trigger
2. **Other machines auto-update** -- each machine runs a cron job (`remote-update.sh`) that pulls main, syncs deps, and restarts the bridge

There is no manual promotion step, no container registry, no platform CLI. Merging IS deploying.

### Per-machine setup not covered by auto-update

A few things are **one-time, per-machine human steps** that the auto-update cron cannot do (they need a browser or interactive consent). These are not part of a deploy, but if a machine is misbehaving after picking up changes, check whether it was ever set up:

- **`gws` (Google Workspace CLI) auth** — installed by `/update` but ships unauthenticated. Authenticate by copying the shared vault OAuth client to `~/.config/gws/client_secret.json` and running `gws auth login` (skips the broken gcloud path). Full procedure: `docs/features/gws-cli-auth.md` (in the `ai` repo). The shared credential `~/Desktop/Valor/google_credentials.json` is identical on every machine (iCloud-synced), so the steps are the same everywhere.

## What This Skill Does

1. Verifies the PR was merged to main
2. Confirms the local machine has the merge commit
3. Checks that the bridge and worker are healthy on the local machine
4. Reports deployment status and what other machines will pick up

## Variables

DEPLOY_ARG: $ARGUMENTS

**If DEPLOY_ARG is empty or literally `$ARGUMENTS`**: Extract from the user's message.

## Step 1: Resolve What Was Deployed

```bash
# By PR number (if provided):
gh pr view $PR_NUMBER --json number,title,state,mergedAt,mergeCommit,headRefName

# Or find most recent merge:
gh pr list --state merged --limit 1 --json number,title,mergedAt,mergeCommit,headRefName
```

**Verify the PR is merged.** If not, stop:
```
Deploy blocked: PR #N is not merged. Run /do-merge first.
```

## Step 2: Confirm Local Machine Is Current

```bash
# Pull latest main
git checkout main && git pull

# Verify merge commit is present
git log --oneline -1 $MERGE_COMMIT
```

## Step 3: Verify Local Service Health

```bash
# Check bridge and worker are running
./scripts/valor-service.sh status
./scripts/valor-service.sh worker-status

# Check recent bridge errors
tail -20 logs/bridge.log | grep -c ERROR

# Verify Telegram connection
tail -5 logs/bridge.log | grep "Connected to Telegram"
```

If the bridge is not running or has errors, restart it:
```bash
./scripts/valor-service.sh restart
sleep 5
./scripts/valor-service.sh status
```

## Step 4: Check Remote Machine Status

Read the machine list from projects.json and report which machines will auto-update:

```bash
python -c "
import json, os
config = json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json')))
machines = set()
for proj in config.get('projects', {}).values():
    m = proj.get('machine', '')
    if m:
        machines.add(m)
import subprocess
local = subprocess.check_output(['scutil', '--get', 'ComputerName']).decode().strip()
print(f'Local machine: {local}')
print(f'All machines: {sorted(machines)}')
remote = sorted(machines - {local})
if remote:
    print(f'Remote machines that will auto-update: {remote}')
    print('These machines will pick up the changes on their next cron cycle.')
else:
    print('No remote machines configured. Local-only deployment.')
"
```

## Step 5: Report

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
{list of machines that will auto-update on next cron cycle}

### Rollback
If needed: `git revert HEAD --no-edit && git push origin main`
Then all machines will pick up the revert on their next cron cycle.
```

## Hard Rules

1. **NEVER deploy unmerged code** -- verify merge state first
2. **NEVER skip the bridge health check** -- always verify after merge
3. **NEVER force-update remote machines** -- let the cron handle it
4. **Capture evidence** -- log the bridge status and any errors
