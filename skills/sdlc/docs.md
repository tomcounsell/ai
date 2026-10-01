# Stage: docs

**Goal.** No doc says something the candidate made untrue.

You read the request, the plan, and the diff, and change only doc paths:
Markdown files and the paths the plan names as docs. Your commits sit on
top of the candidate.

**Exit evidence.** A verdict: `updated` (with your commits), `no_change`,
or `changes` (a doc states something the code should still honor and the
candidate breaks it, or a doc cannot be made true without a code change),
and the governance answer over your own diff, since a doc can add a rule.
