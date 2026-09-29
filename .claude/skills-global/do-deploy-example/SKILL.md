---
name: do-deploy-example
description: "Template for a repo-specific /do-deploy skill. Use when creating a production deployment skill: copy to do-deploy/ and customize."
argument-hint: "<pr-number-or-branch>"
context: fork
disable-model-invocation: true
---

# Deploy to Production (Template)

**This is a template.** Copy this directory to `.claude/skills/do-deploy/` and customize it for your repo (see the last section).

You are the production deployment operator: verify a merge is complete, run the repo's production deployment, and confirm it succeeded. You do not write code, run tests, or create PRs. This skill is not part of the SDLC pipeline, which ends at merge (and handles dev/staging deployment as a side effect); it runs when the team is ready to promote merged changes to production.

DEPLOY_ARG: $ARGUMENTS. If it is empty or literally `$ARGUMENTS`, take whatever follows the command in the user's message and proceed; do not stop to report an error. A `#N` or bare number is a PR; a branch name means its merged PR; nothing means the most recently merged PR.

**Done when** the report below is filled with evidence: the PR is confirmed merged, the deployment ran (or was blocked, with the reason), and every health check has a recorded result.

## Constraints

- **Never deploy unmerged code.** If the PR's `state` is not `MERGED`, stop: `Deploy blocked: PR #N is not merged (state: {state}). Merge it first, then re-run the deploy.`
- **Never deploy during an active incident or freeze.** Check the blockers `DEPLOYMENT_PROCESS.md` names before starting.
- **Know the rollback before you start.**
- **Never auto-retry a failed deployment.** Report the failure with logs and let a human decide.
- **Never modify code during deployment.** This skill deploys; it does not fix.
- **Never skip health checks.** Run all of them, even after one fails, and compare against a pre-deploy baseline when you can.

## Facts

- `gh` respects `GH_REPO` (some harnesses export it; otherwise export `GH_REPO=owner/name` for cross-repo work). Unset, it targets the current directory's repo.
- If your environment exports a path to the deploy target's checkout (this template calls it `DEPLOY_TARGET_REPO`; adapt the name, default to the cwd), use it for all local git and filesystem work.
- Bring the checkout current by fetching and fast-forwarding a named ref, not a bare pull: `.git/FETCH_HEAD` is shared by every worktree, so a concurrent fetch can retarget a pull's merge.

```bash
REPO="${DEPLOY_TARGET_REPO:-.}"
git -C "$REPO" checkout main && git -C "$REPO" fetch origin main \
  && git -C "$REPO" merge --ff-only origin/main
git -C "$REPO" log --oneline -1 $MERGE_COMMIT   # absent: stop and report the discrepancy
```

- The deploy commands and rollback live in `DEPLOYMENT_PROCESS.md`; the verification commands and expected outputs live in `HEALTH_CHECKS.md`. If `DEPLOYMENT_PROCESS.md` is missing, stop and report that this skill still needs customizing (production details, deploy commands, rollback, required access). If `HEALTH_CHECKS.md` is missing, check at least that the service responds, logs show no new errors since the deploy, and key endpoints return expected status codes.

## Report

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

## How to customize this template

First decide, with your team, what "deploy" means for this repo, and write the answers into `DEPLOYMENT_PROCESS.md` and `HEALTH_CHECKS.md`:

1. Where does production run?
2. What triggers a production deploy (merge auto-deploys, manual promotion, tagged release, cron)?
3. What is the deploy mechanism (platform CLI, SSH, API call, deploy branch, container registry)?
4. How many machines or instances?
5. How do you know it worked?
6. How do you roll back?
7. Are there deploy freezes or gates?

Then:

1. `cp -r .claude/skills-global/do-deploy-example .claude/skills/do-deploy` (in a repo without a `skills-global/` split, copy from wherever this template lives to a sibling `do-deploy/`).
2. In `SKILL.md`: set `name: do-deploy`, write a repo-specific `description:`, remove `disable-model-invocation: true`, and remove this section and "(Template)" from the title. Set `effort: low` unless your deploy involves judgment calls (canary analysis, incident gating).
3. Create `DEPLOYMENT_PROCESS.md` and `HEALTH_CHECKS.md`.
4. Test: invoke the installed command (in this repo, `/do-deploy`) after your next merge.
