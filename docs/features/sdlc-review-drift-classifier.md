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

**What counts as documentation** (`is_docs_only_path`): a file under `docs/`
with a prose or image extension (`.md`, `.markdown`, `.rst`, `.txt`, `.png`,
`.jpg`, `.jpeg`, `.gif`, `.webp`), plus top-level `*.md`. Every other file
under `docs/` is code: some projects execute `docs/hooks.py` as an mkdocs hook
or keep scripts in `docs/scripts/`, and the merge gate serves every project.
SVG is absent from the list because it can carry script.

Instruction surfaces are excluded even when they look like prose, matched
case-insensitively (on macOS `claude.md` and `docs/SDLC/` are the files the
harness loads): anything under `docs/sdlc/`, and any file named `CLAUDE.md`,
`CLAUDE.local.md`, or `AGENTS.md` at any depth. `.claude/`, `.github/`,
`tests/`, and every source file are code too (a docstring edit in a `.py` file
is code drift). Erring toward re-review is the fail-closed direction.

The compare call times out after 5 seconds. The merge predicate runs inside
the PreToolUse Bash dispatcher hook, whose budget is 20 seconds
(`.claude/hooks/manifest.toml`), after its other `gh` calls. A longer compare
could let the harness kill the hook before `unknown` is returned. A timeout
reads as `unknown`, which is stale.

## Consumers

All three share one classifier and one fail-closed rule. A consumer that
cannot resolve the repo slug reads `unknown` and stays strict, so two consumers
can briefly disagree only in the safe direction:

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
