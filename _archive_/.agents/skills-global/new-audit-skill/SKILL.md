---
name: new-audit-skill
description: "Create a focused Codex audit skill with observable checks, verified findings, severity definitions, and optional mechanical fixes."
---

# New Audit Skill

Determine the audited objects, known failure examples, target users, and desired output from available context. Design checks that distinguish evidence from suspicion and produce actionable file/line or object references. Define severities by consequence, not stylistic preference.
Use $new-skill for Codex packaging. Choose a focused name and description that attracts an audit request, not every ordinary edit. Deterministic structure checks belong in a script; semantic judgments belong in the procedure. If scripted, support only needed flags such as `--json`, `--target`, and `--fix`, with documented output and explicit nonzero failure behavior.
Default to findings for an audit-only request. A request to audit and fix authorizes relevant repairs; do not convert it into a mandatory issue-filing detour. Validate both known problems and false-positive examples. Re-read each claimed defect before reporting, and distinguish incomplete checks from a clean result.
