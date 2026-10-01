# Stage: docs

**Goal.** No doc says something the candidate made untrue.

You read the request, the plan, and the diff, and change only doc paths:
Markdown files, never a `CLAUDE.md` or `AGENTS.md`, nor anything under
`skills/`, `persona/`, or `.claude/`: those instruct turns, and changing
them is the builder's work, under review. Your commits sit on
top of the candidate.

**Exit evidence.** A verdict: `updated` (with your commits), `no_change`,
or `changes` (a doc states something the code should still honor and the
candidate breaks it, or a doc cannot be made true without a code change),
and the governance answer over your own diff, since a doc can add a rule.
