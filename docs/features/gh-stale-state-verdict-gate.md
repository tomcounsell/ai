# Cache-immune PR head resolution for the verdict-staleness gate (#2404)

## The hole

The SDLC verdict-staleness gate (#2062) decides whether a recorded REVIEW
approval still applies to the code in front of it by comparing the verdict's
`REVIEW_CONTEXT head_sha=<40-hex>` trailer against the PR's **current** head
commit. If the current-head read is stale in the fail-open direction — it
returns the *pre-push* value, which is exactly the value the trailer already
carries — the gate sees a match and passes a verdict that predates newly pushed
code. A path deliberately designed fail-closed
([`sdlc-verdict-fail-closed-persistence.md`](sdlc-verdict-fail-closed-persistence.md))
silently becomes fail-open.

## Root cause (established empirically, gh 2.89.0)

The issue hypothesized a local `gh` disk-cache hit. Direct measurement
contradicts that as the mechanism for the gating helpers:

- `~/.cache/gh` is written **only** by `gh api --cache <ttl>`. Bare `gh api`,
  `gh pr view --json`, `gh pr list`, `gh issue list`, `gh pr status`, and
  `gh pr checks` each produced zero new cache files and read none (a control
  `gh api --cache 60s` wrote exactly one).
- The repo passes `--cache` **nowhere** (`grep -rn -- --cache` over all
  `*.py`/`*.sh`/`*.md`). Every SDLC gating read already bypasses the disk cache
  by construction.

So the observed ~20h-stale `gh issue list` was **not** a local-disk-cache hit;
it is consistent with GitHub's server-side eventual consistency (list/search
index lag), which self-corrects on its own. A local cache flag cannot address
that. The residual fail-open risk to the verdict gate is GitHub API
read-staleness in the seconds-to-minutes window after a push.

## The fix: resolve the head SHA from git, not gh

`git ls-remote origin refs/pull/{N}/head` returns the PR's head commit over the
**git** transport — a different transport from `gh`'s HTTP layer, sharing no
response cache, and authoritative for the ref. Verified: for PR #2412 it returns
the identical SHA as both `gh api …/pulls/2412 --jq .head.sha` and
`gh pr view --json headRefOid`. This is the same "reconcile against observable
git ground truth" direction #2395's recovery path took, and it is correct
**without per-machine configuration** — no `GH_*` env var to drift out of place
across bridge / worker / launchd / local / subagent contexts.

`tools/pr_head_resolver.py::resolve_pr_head_sha(pr, repo=, repo_root=)` is the
single source of truth:

1. **Primary (authoritative):** `git ls-remote origin refs/pull/{pr}/head`,
   guarded by `_origin_matches_repo` so a cross-repo cwd (e.g. `sdlc-tool`
   forcing cwd to `~/src/ai`) never resolves the wrong repo's head.
2. **Fallback:** `gh pr view --json headRefOid` when the git read yields
   nothing (no `origin`, or a cross-repo checkout). `gh pr view` does not
   disk-cache on gh 2.89.0.
3. **cross_check** (default on): when both resolve and disagree, the git value
   wins and a WARNING logs both SHAs — the fail-open event is surfaced, not
   hidden.

Stdlib-only, so `tools/merge_predicate.py` (stdlib-only for the merge-guard
hook) can consume it.

### Wired decision sites (the fail-open-critical head-SHA reads)

| Site | Role | Change |
|------|------|--------|
| `tools/merge_predicate.py::_gh_latest_commit` | merge gate `_check_verdict_freshness` | SHA resolved via `resolve_pr_head_sha`; gh still supplies the committer date |
| `tools/sdlc_next_skill.py::_fetch_pr_head_sha` | router `context["pr_head_sha"]` signal | delegates to `resolve_pr_head_sha` |
| `tools/sdlc_review_finalize.py::_fetch_pr_head_sha` | records the trailer | delegates to `resolve_pr_head_sha` (record and check now agree) |

### Enumerated but intentionally left as bare `gh` reads

These drive control flow but are **not** fail-open against the verdict gate and
do not disk-cache on gh 2.89.0, so they need no git cross-check (adding one
would be speculative complexity the issue warns against):

- `tools/sdlc_next_skill.py::_fetch_pr_state`, `tools/merge_predicate.py::_gh_pr_view`,
  `tools/sdlc_stage_query.py` (`gh pr list`/`gh pr view`), `agent/pipeline_state.py`
  (`statusCheckRollup`, `gh pr diff`), `agent/pipeline_complete.py` / `agent/goal_gates.py`
  (`gh pr list`).
- Rationale: a stale `state`/list read is fail-safe (a momentarily-stale OPEN
  re-runs a stage rather than skipping a gate) and self-correcting; only a stale
  head SHA flips the verdict gate from fail-closed to fail-open.

## Interaction with #2305

#2305 closes a *second, independent* hole in the same gate:
`pipeline_state.py::_backfill_predecessors()` force-setting `REVIEW="completed"`
with no verdict check (defeats verdict *presence*; this fix defeats verdict
*staleness*). The current backfill reads no head SHA, so there is no code
collision. If #2305's verdict-aware backfill starts validating a `head_sha`
trailer, it must resolve the current head through `resolve_pr_head_sha` or it
re-opens the freshness hole.

## Durable rule

Agent gating reads of a PR's head state must resolve through
`resolve_pr_head_sha` (git-first), never a bare `gh` read. Recorded in
`CLAUDE.md` so it lives where agents encounter it.

## Tests

- `tests/unit/test_pr_head_resolver.py` — git-primary wins over a stale gh read
  (the acceptance-bar case), gh fallback, both-empty → None, cross-check
  warning, and the `_origin_matches_repo` cross-repo guard.
- `tests/unit/test_merge_predicate.py` — `_gh_latest_commit` returns the
  git-authoritative SHA when gh serves a stale one; `_check_verdict_freshness`
  **blocks** when the trailer predates the authoritative head (regression), and
  passes when they match.
