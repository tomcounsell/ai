# reclassify context — this repo (ai)

This repo's plan-document conventions, enforced by hooks. The global skill's generic
defaults happen to match this repo because the convention originated here; this file makes
the enforcement explicit.

## Plan documents

- Location: `docs/plans/*.md`, YAML frontmatter with `status:`, `type:`, `appetite:`,
  `owner:`, `created:`, `tracking:` fields. Created by `/do-plan`.

## Allowed `type:` values

`bug` | `feature` | `chore` — enforced by
`.claude/hooks/validators/validate_plan_label.py` (plans with other values fail validation;
legacy plans predating the hook may carry other values, but new writes must use these three).

## Status gate

Only `status: Planning` permits a type change. Once status reaches any of
`Ready`, `In Progress`, or `Complete`, the `type:` field is **immutable** — enforced by
`.claude/hooks/validators/validate_type_immutability.py`, which diffs the field against git
HEAD and blocks the edit. To reclassify an approved plan, first set status back to
`Planning`, then run `/reclassify`.
