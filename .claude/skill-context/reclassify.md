# reclassify context — this repo (ai)

This repo's plan-document conventions, enforced by hooks. The global skill's generic
defaults happen to match this repo because the convention originated here; this file makes
the enforcement explicit.

## Plan documents

- Location: `docs/plans/*.md`, YAML frontmatter with `status:`, `type:`, `appetite:`,
  `owner:`, `created:`, `tracking:` fields. Created by `/do-plan`.

## Allowed `type:` values

`bug` | `feature` | `chore` — this is a convention, not currently enforced by any
registered hook (legacy plans predating the convention may carry other values, but
new writes should use these three).

## Status gate

Only `status: Planning` permits a type change. Once status reaches any of
`Ready`, `In Progress`, or `Complete`, the `type:` field should be treated as
**immutable** by convention — this is not currently enforced by any registered hook.
To reclassify an approved plan, first set status back to `Planning`, then run
`/reclassify`.
