# SDLC Review Drift Classifier (#3228)

A REVIEW verdict is pinned to the head SHA the reviewer inspected. `/do-docs`
is a mandatory stage that runs after REVIEW and commits, so by MERGE the PR's
live head has always moved past that SHA. Strict SHA equality therefore marked
every approval stale and turned routine merges into human authorizations.

`tools/sdlc_review_drift.py::classify_head_drift(reviewed, head, repo)`
classifies the gap between the two SHAs with one GitHub compare API call
(`gh api repos/{repo}/compare/{reviewed}...{head}`), so it needs no local
checkout holding either commit.

| Result | Meaning | Treated as |
|--------|---------|------------|
| `identical` | Same SHA | fresh |
| `docs_only` | Compare status `ahead` and every changed path is documentation | fresh |
| `code` | Any non-documentation path, or status `behind` / `diverged` (force-push, rebase) | stale |
| `unknown` | Any error, timeout, unparseable payload, or a file list at the 300-entry cap | stale |

**What counts as documentation** (`is_docs_only_path`): anything under `docs/`
except `docs/sdlc/`, plus top-level `*.md` except `CLAUDE.md` and `AGENTS.md`.
The excluded paths are instruction surfaces that agents load at runtime, as are
`.claude/`, `.github/`, `tests/`, and every source file (a docstring edit in a
`.py` file is code drift). Erring toward re-review is the fail-closed direction.

## Consumers

All three share the one classifier, so they cannot disagree:

- **Merge predicate, group (c)** (`tools/merge_predicate.py::_check_verdict_freshness`):
  a trailer mismatch passes only on `docs_only`. See `docs/sdlc/do-merge.md`.
- **`sdlc-tool verdict selfcheck` / `finalize`** (`tools/sdlc_review_finalize.py::check_review_persistence`):
  reports the result as `head_drift` and sets `trailer_matches_head` for
  `identical` and `docs_only`; anything else is `REVIEW_TRAILER_MISSING`.
- **Router row 8f** (`agent/sdlc_router.py::_review_head_drift_is_docs_only`):
  `tools/sdlc_next_skill.py` computes the drift in context assembly and the
  router honours it only when the SHA pair matches its own view, keeping the
  router free of `tools/` imports.

## Pinning the reviewed head

`sdlc-tool verdict finalize --reviewed-head SHA` records the verdict against
the commit the reviewer actually read, not the live head (the review's own
plan-checkbox commit has already moved the branch). If the live head moved by
anything other than documentation, `finalize` refuses with `REVIEW_HEAD_DRIFT`
and records nothing.

## Remedy for code drift

Re-run `/do-pr-review`. Never re-run `finalize` against an uninspected head:
minting APPROVED for code nobody read is self-clearing.

See also [SDLC Verdict Fail-Closed Persistence](sdlc-verdict-fail-closed-persistence.md)
and [Cache-immune PR head resolution](gh-stale-state-verdict-gate.md).
