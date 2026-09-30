---
name: do-build
description: "Implement a development plan through working code, relevant tests, review, documentation, and a reviewable change."
---

# Do Build

Resolve the plan by supplied path or its `tracking:` issue; derive the target repository from the plan's location. Read the whole plan, latest issue feedback, and existing progress before starting. Resume completed work rather than rebuilding it.
Use the user's chosen worktree or create an isolated `codex/` branch/worktree when needed. Respect any established managed-lane identity. Validate meaningful prerequisites and acceptance criteria, then implement tasks in dependency order. Work directly unless independent delegation is useful and authorized; Claude Task tools and mandatory builder/validator teams are not requirements.
Run relevant tests and quality checks, investigate actual failures, review the diff against the plan, and update affected docs. Detect plan changes during implementation before relying on stale requirements. Bound repeated attempts; after recurring failures revisit the diagnosis rather than looping unchanged.
Definition of done: requested behavior works, meaningful checks pass, review finds no unresolved blockers, docs describe the resulting behavior, and artifacts are reviewable. In Valor use `scripts/pytest-clean.sh` and the correct pinned checkout environment; consult `docs/sdlc/do-build.md` only for applicable managed-lane integration.
Commit logical changes with hooks intact. If the workflow includes a PR, verify commits exist, push the correct target branch, and open the PR with concrete validation evidence. Keep the plan until the repo's merge procedure retires it. Do not deploy or merge merely because implementation was requested. Report artifacts, verification, and any remaining blocker.
