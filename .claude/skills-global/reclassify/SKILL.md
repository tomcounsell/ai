---
name: reclassify
description: "Change a plan's type (bug/feature/chore) while it is still in Planning. Use when the classification was wrong or scope changed."
allowed-tools: Read, Edit, Glob, Bash
disable-model-invocation: true
argument-hint: "<bug|feature|chore>"
model: haiku
effort: low
---

# Reclassify Plan Type

Change the `type:` frontmatter field of the active plan document to the type given in
`$ARGUMENTS` (e.g. `/reclassify bug`), and commit that one-line change.

Done when: the plan's `type:` holds the new value, the change is committed on its own, and you
have reported `Reclassified {plan_file} from '{old}' to '{new}'.` If the argument is missing or
not an allowed type, show usage and stop.

## Repo context

If `.claude/skill-context/reclassify.md` exists, read it for plan location, allowed types, and
the status gate. Defaults: plans are `docs/plans/*.md`, types are `bug`, `feature`, `chore`,
and only `status: Planning` permits the change. If no plan document has this shape, report
that there is nothing to reclassify.

## Constraints

- Type is immutable once the plan leaves Planning. If it has, refuse with the plan's current
  status, and say that the status must be set back to Planning first.
- If exactly one plan is in Planning, use it. If there are several, ask which one. If there are
  none, report that.
- Edit only the `type:` line. Commit only that file:
  `git add {plan_file} && git commit -m "Reclassify {plan_file} as {new_type}" -- {plan_file}`.
