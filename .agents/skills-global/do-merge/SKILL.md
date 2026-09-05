---
name: do-merge
description: "Merge a requested pull request after verifying current-head approval, checks, mergeability, and repository gates."
---

# Do Merge

Resolve the target repo/PR and existing merge authorization. Read live PR state, head, mergeability, branch-protection status, checks, and review. A closed or already merged PR should be reported accurately, not merged again. Require the repository's actual green checks and current-head approval; an absent review decision is not approval.
In an active Valor lane, read `docs/sdlc/do-merge.md`, use `tools/pr_head_resolver.py` for gating SHA reads, and verify required REVIEW/DOCS state and fresh recorded verdicts. Do not invent an issue or a lane for a foreign PR solely to satisfy tooling; follow the documented external-PR path.
Verify issue disposition and any concrete authorization gate. Once the user has requested merge and checks pass, execute without redundant confirmation. Use the repo's merge method, normally squash here, and bind the merge to the verified head where supported. If the head moves, revalidate rather than force-merging.
Read back merged state and commit. Apply only documented, scoped plan/worktree cleanup; preserve worktrees with uncommitted or foreign work. Clean up temporary authorization artifacts on success or failure and record the real outcome. Deployment is separate unless included in the request.
