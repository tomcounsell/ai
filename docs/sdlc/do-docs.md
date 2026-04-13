# do-docs addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-docs/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## docs/features/ is the Primary Index

All feature documentation lives in `docs/features/`. When creating a new feature doc, also add an entry to `docs/features/README.md` index table. The index is the canonical list of features; missing entries cause discoverability gaps.

## CLAUDE.md Quick Reference

If the feature adds a new CLI command, script, or tool, add it to the appropriate table in `CLAUDE.md`. The quick reference table is the first place devs look — keep it current.

## docs/plans/ Commit-on-Main

Plan files in `docs/plans/` must always stay on `main`. Never include plan file changes in a feature branch PR. If a docs update requires plan changes, make them directly on `main` in a separate commit.

## docs/sdlc/ Addenda

If the feature changes how an SDLC stage works for this repo, update the relevant `docs/sdlc/do-X.md` addendum. These files inform future SDLC skill runs. Keep them under 300 lines and never duplicate content from the global skill.

## No Stale References

Before finalizing docs, verify all file paths, command examples, and feature names still exist. Stale references (pointing to renamed files, removed commands) are worse than no docs — remove or update them.

## Commit Docs on Feature Branch

All `docs/features/` content is committed on the feature branch (`session/{slug}`), not on `main`. The merge brings docs in with the code.
