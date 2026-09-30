# do-issue context — this repo (ai)

This repo's nuances for the `/do-issue` skill. The global skill body runs a
generic `git`/`gh` baseline; this file layers the ai-repo SDLC automation back
in. Read top to bottom and honor every declaration.

## Stage Marker (wraps the whole skill)

At the very start of the skill, write an `in_progress` marker:

```bash
sdlc-tool stage-marker --stage ISSUE --status in_progress --issue-number {issue_number} 2>/dev/null || true
```

After the issue is created (Step 7), write the completion marker:

```bash
sdlc-tool stage-marker --stage ISSUE --status completed --issue-number {issue_number} 2>/dev/null || true
```

## Cross-Repo `gh` Targeting

For cross-project work, the `GH_REPO` environment variable is set automatically
by `sdk_client.py`. The `gh` CLI natively respects it, so all `gh` commands
target the correct repository — no `--repo` flags or manual parsing needed.

## Canonical Doc Locations (Step 2 related-context search)

When searching for related context before writing, scan this repo's doc
locations:

```bash
grep -rl "KEYWORD" docs/features/ docs/plans/ 2>/dev/null | head -5
```

## Plan-Doc Path Convention (Downstream context)

State the concrete downstream path: the issue will be consumed by `/do-plan` to
produce a plan document at `docs/plans/{slug}.md`, then executed by `/do-build`.
The slug is kebab-case, derived from the issue title.

## Issue Labels

Use this repo's label set consistently (see `CLAUDE.md` → GitHub Issue Labels):
`bug`, `reflections`, `memory`, `skills`, `dashboard`, `bridge`, `testing`. Do
NOT use a `feature` label — it adds no signal here.
