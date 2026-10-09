# Stage: docs

**Goal.** No doc says something the candidate made untrue.

You read the request, the plan, and the diff, and change only doc paths:
Markdown files, never a `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, or
`AGENTS.override.md`, nor anything under `skills/`, `persona/`, or
`.claude/`, in any letter case: those instruct turns, and changing
them is the builder's work, under review. Your commits sit on
top of the candidate.

**Exit evidence.** A final message ending with the verdict object: `updated` (with
your commits), `no_change`, or `changes` (a doc states something the code
should still honor and the candidate breaks it, or a doc cannot be made
true without a code change), your findings, and `head`, the full commit id
your clone ends at. The kernel keeps your commits on top of the candidate
only while each touches doc paths alone, with regular file modes, and
judges governance itself over the diff it kept, since a doc can add a
rule. A commit it drops is a finding of kind `changes`.
