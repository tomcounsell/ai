# do-docs addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-docs/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

Operational specifics (markers, doc locations, indexes, substrate, push guard)
live in `.claude/skill-context/do-docs.md`.

## docs/plans/ Commit-on-Main

Plan files in `docs/plans/` must always stay on `main`. Never include plan file changes in a feature branch PR. If a docs update requires plan changes, make them directly on `main` in a separate commit.

## docs/sdlc/ Addenda

If the feature changes how an SDLC stage works for this repo, update the relevant `docs/sdlc/do-X.md` addendum. Keep each under 300 lines and never duplicate content from the global skill.

## Commit Docs on Feature Branch

All `docs/features/` content is committed on the feature branch (`session/{slug}`), not on `main`. The merge brings docs in with the code.
