# Completed plans (archive)

Shipped plan documents, moved here from `docs/plans/completed/` by issue #2878.
`git log --follow <file>` traverses the rename, so per-file history is intact.

## What these are

Point-in-time working documents, not descriptions of the current status quo.
A plan describes the code as it was when the plan shipped. Treat every path,
symbol, and line number in this directory as historical: correct-as-written,
frequently stale-as-read. If a plan and a `docs/features/` doc disagree, the
feature doc wins.

## Why the archive is a sibling of `docs/plans/`, not a child

The `docs/plans/` prefix means "live plan" to real machinery, and the prefix
test is the entire mechanism separating live work from history:

| Consumer | What the prefix means to it |
|---|---|
| `.claude/hooks/validators/validate_documentation_section.py` and three sibling validators | A written `.md` under the prefix must carry `## Documentation`, `## Test Impact`, `## No-Gos`, `## Success Criteria` |
| `reflections/docs_auditor.py` (`NON_AUDITED_DOC_PREFIXES`) | Excluded from the neighborhood grep and the PR-changed-files scan |
| `scripts/check_issue_disposition.py` (`EXEMPT_PREFIXES`) | Commits touching only these paths need no issue disposition |

Under the old layout every archived plan matched the live-plan prefix, so
editing one fired the plan-structure validators and repo-wide greps read 547
historical files as current.

## Adding to this directory

Nothing lands here by hand. `migrate_plan_to_completed()` in
`scripts/migrate_completed_plan.py` is the single authoritative mover, and its
`COMPLETED_PLANS_DIR` constant is the only definition of this path. Two call
sites share it: `/do-merge`'s post-merge `--issue` invocation and the
`merged-branch-cleanup` reflection backstop.

Adding a second mover is the specific failure
[`docs/features/plan-migration-invariant.md`](../../features/plan-migration-invariant.md)
exists to prevent.
