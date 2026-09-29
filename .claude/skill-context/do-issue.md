# do-issue context — this repo (ai)

This repo's additions to the `/do-issue` skill.

## Stage marker

After the issue is created, write the completion marker (there is no issue
number to mark before then):

```bash
sdlc-tool stage-marker --stage ISSUE --status completed --issue-number {issue_number} || true
```

Omit `--run-id`: an ISSUE marker without one takes the sessionless path
(`tools/sdlc_stage_marker.py::write_issue_marker_cold`), which also handles a
live run that already holds the lease.

## Cross-repo `gh`

`GH_REPO` is set for cross-project work and `gh` honors it; no `--repo` flags.

## Related-context search

```bash
grep -rl "KEYWORD" docs/features/ docs/plans/ 2>/dev/null | head -5
```

## Downstream path

`/do-plan` turns the issue into `docs/plans/{slug}.md` (kebab-case slug from the
title), which `/do-build` executes.

## Labels

`bug`, `reflections`, `memory`, `skills`, `dashboard`, `bridge`, `testing`
(see `CLAUDE.md`). There is no `feature` label; don't use one.
