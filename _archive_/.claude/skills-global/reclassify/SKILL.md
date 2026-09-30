---
name: reclassify
description: "Change a plan's type (bug/feature/chore) during the Planning phase. Use when the initial classification was wrong or scope changed; only works before plan approval."
allowed-tools: Read, Edit, Glob, Bash
disable-model-invocation: true
---

# Reclassify Plan Type

Changes the `type:` field in a plan document's YAML frontmatter. Only allowed while the plan is still in its planning phase — after approval, type is immutable.

## Repo Context Probe

If `.claude/skill-context/reclassify.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its plan-document conventions: where plan files live, the allowed `type:` values, which `status:` values still permit reclassification, and any hooks that enforce these. Generic defaults: plans are markdown files under `docs/plans/` with `type:` and `status:` frontmatter, allowed types are `bug`, `feature`, `chore`, and only `status: Planning` permits the change. If the repo has no plan documents matching this shape, report that there is nothing to reclassify.

## Process

Argument: the new type (e.g. `/reclassify bug`). If missing or not an allowed value, show usage and stop.

1. **Find the active plan.** Exactly one plan in a planning status → use it. Multiple → list them and ask which. None → report it.
2. **Gate on status.** If the plan's status does not permit reclassification, reject with:
   ```
   Cannot reclassify: plan status is '{status}'. Type can only be changed during Planning phase.
   To change type after approval, first change status back to Planning.
   ```
3. **Edit the `type:` field** in the frontmatter, then confirm:
   ```
   Reclassified {plan_file} from '{old_type}' to '{new_type}'.
   ```
4. **Commit** the change: `git add {plan_file} && git commit -m "Reclassify {plan_file} as {new_type}"`.
