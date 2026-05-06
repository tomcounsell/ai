---
name: do-deploy-example
description: "Template for creating a repo-specific /do-deploy skill. Copy this directory to do-deploy/ and customize DEPLOYMENT_PROCESS.md and HEALTH_CHECKS.md for your repo's production deployment process."
argument-hint: "<pr-number-or-branch>"
context: fork
disable-model-invocation: true
---

# Deploy to Production (Template)

**This is a template.** Copy this directory to `.claude/skills/do-deploy/` and customize it for your repo. See the bottom of this file for what to change.

You are the **production deployment operator**. You verify a merge is complete, execute the production deployment process, and confirm the deployment succeeded. You do not write code, run tests, or create PRs.

This skill is **not part of the SDLC pipeline**. The SDLC pipeline ends at merge, which already handles dev/staging deployment as a side effect. This skill is invoked separately when the team is ready to promote merged changes to production.

## What this skill does

1. Verifies the PR was merged to the target branch
2. Executes the repo-specific production deployment process
3. Runs post-deployment health checks against production
4. Reports deployment status with evidence

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `DEPLOYMENT_PROCESS.md` | Starting the deploy (repo-specific production steps, rollback) |
| `HEALTH_CHECKS.md` | After production deployment completes (verification commands, expected outputs) |

## Variables

DEPLOY_ARG: $ARGUMENTS

**If DEPLOY_ARG is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Look at the user's original message in the conversation -- they invoked this as `/do-deploy <argument>`. Extract whatever follows `/do-deploy` as the value of DEPLOY_ARG. Do NOT stop or report an error; just use the argument from the message.

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

When `SDLC_TARGET_REPO` is set, use it for all local filesystem and git operations.

## Step 1: Resolve What to Deploy

**Detect argument type:**
- If `DEPLOY_ARG` starts with `#` or is a pure number: treat as PR number
- If `DEPLOY_ARG` is a branch name: find the associated merged PR
- If empty: find the most recently merged PR

```bash
# By PR number:
gh pr view $PR_NUMBER --json number,title,state,mergedAt,mergeCommit,headRefName

# Most recent merge:
gh pr list --state merged --limit 1 --json number,title,mergedAt,mergeCommit,headRefName
```

**Verify the PR is merged.** If `state` is not `MERGED`, stop and report:
```
Deploy blocked: PR #N is not merged (state: {state}).
Merge the PR first, then re-run /do-deploy.
```

Record:
- `PR_NUMBER`: The PR number
- `PR_TITLE`: The PR title
- `MERGE_COMMIT`: The merge commit SHA
- `MERGE_BRANCH`: The base branch the PR was merged into

## Step 2: Pre-Deploy Verification

Before deploying, verify the environment is ready:

```bash
REPO="${SDLC_TARGET_REPO:-.}"

# 1. Confirm local repo is on the merge target branch and up to date
git -C "$REPO" checkout main && git -C "$REPO" pull

# 2. Verify the merge commit exists locally
git -C "$REPO" log --oneline -1 $MERGE_COMMIT

# 3. Check for deployment blockers (customize per repo)
# Examples: active incidents, deploy freezes, dependency issues
```

If the merge commit is not present locally after pull, stop and report the discrepancy.

## Step 3: Execute Deployment

**This step is repo-specific.** Load `DEPLOYMENT_PROCESS.md` for the actual deployment commands.

If `DEPLOYMENT_PROCESS.md` does not exist, use this fallback template:

```
No DEPLOYMENT_PROCESS.md found. This skill needs to be customized for this repo.

Create .claude/skills/do-deploy/DEPLOYMENT_PROCESS.md with:
1. Production environment details (URLs, infrastructure, access)
2. Production deployment commands
3. Rollback procedure
4. Required credentials or access

See /do-deploy-example for a complete template.
```

**Production deployment rules:**
- This is production -- dev/staging was already validated by the SDLC pipeline and merge
- Capture all deployment output for the report
- Record the deployment timestamp
- If deployment fails, do NOT retry automatically -- report the failure with logs

## Step 4: Post-Deploy Health Checks

**This step is repo-specific.** Load `HEALTH_CHECKS.md` for verification commands.

If `HEALTH_CHECKS.md` does not exist, use basic checks:

```bash
# Generic health checks (customize per repo)
# 1. Service is responding
# 2. No new errors in logs since deployment
# 3. Key endpoints return expected status codes
```

**Health check rules:**
- Run ALL checks, do not stop at first failure
- Collect evidence (response codes, log snippets, timestamps)
- Compare against pre-deployment baseline when possible

## Step 5: Report

Report deployment status with structured evidence:

```
## Deploy Report: PR #{PR_NUMBER}

**PR**: {PR_TITLE}
**Commit**: {MERGE_COMMIT}
**Environment**: Production
**Status**: {SUCCESS | FAILED | PARTIAL}
**Timestamp**: {deployment timestamp}

### Health Checks
- [ ] Service responding: {status}
- [ ] Error rate normal: {status}
- [ ] Key flows verified: {status}

### Evidence
{deployment output, health check results, log snippets}

### Rollback
{If failed: rollback steps. If succeeded: "No rollback needed."}
```

## Hard Rules

1. **NEVER deploy unmerged code** -- verify merge state first
2. **NEVER skip health checks** -- always verify after deployment
3. **NEVER auto-retry failed deployments** -- report and let human decide
4. **NEVER deploy during an active incident** -- check for blockers first
5. **NEVER modify code during deployment** -- this skill deploys, it does not fix
6. **Capture evidence** -- every deployment needs a paper trail
7. **Rollback plan ready** -- know how to undo before you start

## How to Customize This Template

### Step 0: Define what "deploy" means for this repo

Before writing any config, have a conversation with your team (or with the PM session) to answer these questions. Every repo's deploy is different -- there is no universal answer.

**Questions to answer:**
1. **Where does production run?** (Cloud platform, self-hosted machines, serverless, edge, etc.)
2. **What triggers a production deploy?** (Merge to main auto-deploys? Manual promotion? Tagged release? Cron job picks it up?)
3. **What is the deploy mechanism?** (Platform CLI, SSH, API call, git push to deploy branch, container registry, etc.)
4. **Are there multiple machines/instances?** (Single server, fleet, regional replicas, etc.)
5. **How do you know it worked?** (Health endpoint, log check, smoke test, monitoring dashboard, etc.)
6. **How do you roll back?** (Platform rollback, git revert, redeploy previous image, etc.)
7. **Are there deploy freezes or gates?** (Incident check, approval required, time-of-day restrictions, etc.)

Write the answers into `DEPLOYMENT_PROCESS.md` and `HEALTH_CHECKS.md`. The SKILL.md orchestration (Steps 1-5 above) stays the same across repos -- only the sub-files change.

### Step 1: Copy and configure

1. Copy this directory: `cp -r .claude/skills/do-deploy-example .claude/skills/do-deploy`
2. Update `SKILL.md` frontmatter:
   - Change `name:` to `do-deploy`
   - Write a `description:` specific to your repo's production deployment
   - Remove `disable-model-invocation: true`
   - Remove this "How to Customize" section and the "(Template)" from the title
3. Create `DEPLOYMENT_PROCESS.md` with your production deployment commands, rollback procedure, and any deploy freeze checks
4. Create `HEALTH_CHECKS.md` with your production health verification commands
5. Test: invoke `/do-deploy` after your next merge
