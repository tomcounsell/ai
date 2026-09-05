---
name: reclassify
description: "Change a plan classification among bug, feature, and chore while it is still in Planning status."
---

# Reclassify

Resolve the active plan from the request or its tracking issue. If several plans fit, ask which rather than editing an arbitrary one. Read the complete YAML frontmatter and any repository status conventions.
The default permitted state is `status: Planning`, and types are `bug`, `feature`, `chore`. If the plan is already approved, explain that reclassification requires reopening planning; do not silently reset its status. Respect explicit user authorization to reopen it.
Edit only the type and any directly invalidated classification text. Preserve tracking, ownership, and unrelated metadata. Report the old and new values and the plan path.
