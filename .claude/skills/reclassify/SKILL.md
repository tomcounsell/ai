---
name: reclassify
description: Reclassify a plan's type (bug/feature/chore) during the Planning phase. Use when the initial classification was wrong or the scope changed. Only works before plan is approved (status must be Planning).
allowed-tools: Read, Edit, Glob, Bash
---

# Reclassify Plan Type

Changes the `type:` field in a plan's frontmatter. Only allowed during `status: Planning`.

## Arguments

The skill takes a single argument: the new type. Must be one of: `bug`, `feature`, `chore`.

Example: `/reclassify bug`

## Process

### Step 1: Validate the argument

The argument must be one of: `bug`, `feature`, `chore`. If missing or invalid, show usage and exit.

### Step 2: Find the active plan

Search for plan files in `docs/plans/*.md`. If there's exactly one plan with `status: Planning`, use it. If there are multiple, list them and ask the user to specify which one. If none, report that no plans are in Planning status.

### Step 3: Check plan status

Read the plan's frontmatter. If the status is NOT `Planning`, reject with:
```
Cannot reclassify: plan status is '{status}'. Type can only be changed during Planning phase.
To change type after approval, first change status back to Planning.
```

### Step 4: Update the type field

Use the Edit tool to change the `type:` field in the plan's YAML frontmatter to the new value.

### Step 5: Confirm the change

Report:
```
Reclassified {plan_file} from '{old_type}' to '{new_type}'.
```

### Step 6: Commit the change

```bash
git add {plan_file}
git commit -m "Reclassify {plan_file} as {new_type}"
```
