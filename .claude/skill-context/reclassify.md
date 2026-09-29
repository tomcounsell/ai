# reclassify context — this repo (ai)

The global defaults match this repo. None of these rules is enforced by a hook; they are
conventions.

- Plans: `docs/plans/*.md`, created by `/do-plan`, with `status:`, `type:`, `appetite:`,
  `owner:`, `created:`, and `tracking:` frontmatter.
- Allowed `type:` values: `bug`, `feature`, `chore`. Older plans may carry other values; new
  writes use these three.
- Only `status: Planning` permits a change. `Ready`, `In Progress`, and `Complete` freeze
  `type:`.
- Commits touching only `docs/plans/` are exempt from the hotfix issue-disposition hook.
