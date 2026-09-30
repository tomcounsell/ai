# Plan: gh stale-state cannot defeat the verdict-staleness gate (#2404)

## Root cause (established empirically, gh 2.89.0)

The issue hypothesized a local `gh` disk-cache hit. Direct measurement on this
machine contradicts that as the mechanism for the gating helpers:

- `~/.cache/gh` is written **only** by `gh api --cache <ttl>`. Bare `gh api`,
  `gh pr view --json`, `gh pr list`, `gh issue list`, `gh pr status`, and
  `gh pr checks` each produced **zero** new cache files and read none
  (before/after file counts identical; a control `gh api --cache 60s` wrote
  exactly one).
- The repo passes `--cache` **nowhere** (`grep -rn -- --cache` over all
  `*.py`/`*.sh`/`*.md`). So every SDLC gating read already bypasses the disk
  cache by construction. The `_fetch_pr_head_sha` / `_fetch_pr_state`
  "live" docstrings are therefore mechanically accurate for the disk-cache
  dimension — they never touch it.
- The pre-existing `merge-info-preview` cache files were written by some other
  path (interactive `gh pr merge`, older gh behavior), not by the gating
  helpers.

So the observed ~20h-stale `gh issue list` was **not** a local-disk-cache hit.
It is consistent with GitHub's server-side eventual consistency (list/search
index lag), which self-corrected minutes later exactly as reported. This is a
transient, TTL-bounded staleness in GitHub's own edge, not something a local
cache flag can address.

**Why the issue is still valid.** The fail-open risk is real for *any*
staleness vector, including the seconds-to-minutes window of GitHub API
read-staleness right after a push. The `pr_head_sha` verdict-staleness gate
(#2062) compares the recorded REVIEW verdict's `head_sha` trailer against the
PR's *current* head. If the current-head read returns the pre-push value (the
same value the trailer carries), the gate sees a match and passes a stale
approval — a fail-closed design silently becomes fail-open. The remedy must be
robust to the staleness vector regardless of which layer produced it.

## Fix: resolve the gating head SHA from git, which shares no cache with gh

`git ls-remote origin refs/pull/{N}/head` returns the PR's head commit over the
git transport — a different transport from `gh`'s HTTP layer, with no shared
response cache, and authoritative for the ref. Verified: for PR #2412 it
returns `61c2f57b…`, identical to both `gh api …/pulls/2412 --jq .head.sha`
and `gh pr view --json headRefOid`. This is the same "reconcile against
observable git ground truth" direction #2395's recovery path took, and it is
correct **without per-machine configuration** — no `GH_*` env var to drift out
of place across bridge / worker / launchd / local / subagent contexts.

### New shared resolver — `tools/pr_head_resolver.py` (stdlib-only)

```
resolve_pr_head_sha(pr, repo=None, repo_root=None, *, cross_check=True) -> str | None
```

- **Primary (authoritative):** `git ls-remote origin refs/pull/{pr}/head` run
  in `repo_root`; parse the leading 40-hex token. Immune to gh's HTTP cache and
  authoritative for the PR head.
- **Fallback:** `gh pr view {pr} [--repo R] --json headRefOid` when ls-remote
  yields nothing (no `origin`, or cross-repo `GH_REPO` checkout where `origin`
  is a different repo). No worse than today, and gh does not disk-cache this
  read anyway.
- **cross_check:** when both sources resolve and disagree, return the git value
  (authoritative) and log a WARNING naming both SHAs — this is the fail-open
  signal made visible.

Stdlib-only so `tools/merge_predicate.py` (deliberately stdlib-only for the
merge-guard hook — it must load under any interpreter and avoids
`tools._sdlc_utils` because that pulls in `models`) can import it at module
level.

### Wiring (the three fail-open-critical head-SHA sites)

1. `tools/merge_predicate.py::_gh_latest_commit` — the merge gate
   (`_check_verdict_freshness`). Resolve head via `resolve_pr_head_sha`; keep
   the committer-date fallback for the no-trailer branch.
2. `tools/sdlc_next_skill.py::_fetch_pr_head_sha` — the router's
   `context["pr_head_sha"]` signal.
3. `tools/sdlc_review_finalize.py::_fetch_pr_head_sha` — records the trailer;
   use the same authoritative source so record and check agree.

Docstrings updated to state the authoritative git-first resolution instead of
claiming a bare `gh pr view` is "live".

### Left as-is, with justification (no over-engineering)

Other gh state reads (`gh pr view --json state`, `gh pr list`,
`gh issue view`, `statusCheckRollup`, `gh pr diff`) drive control flow but:
they do not disk-cache on gh 2.89.0 (already cache-proof), and their
staleness is either fail-safe (a stale-OPEN read re-runs a stage rather than
skipping a gate) or self-correcting. Adding git cross-checks there would be
speculative complexity the issue explicitly warns against. They are enumerated
in the docs as "cache-proof, no git cross-check required" so the sweep is on
record.

## Interaction with #2305

#2305 closes a *second, independent* hole in the same gate:
`pipeline_state.py::_backfill_predecessors()` force-sets `REVIEW="completed"`
with no verdict check (defeats verdict *presence*; mine defeats verdict
*freshness*). Current `_backfill_predecessors` does **not** read a head SHA
(confirmed by grep), so there is no code collision today. If #2305's
verdict-aware backfill starts validating a `head_sha` trailer, it must resolve
the current head through `resolve_pr_head_sha` — the single source of truth this
plan introduces — or it will re-open the freshness hole. The PR body will state
this explicitly.

## Durable guidance (acceptance criterion)

Record in `CLAUDE.md` (and the sdlc skill-context) the rule: **agent gating
reads of PR head state must resolve through `resolve_pr_head_sha` (git-first),
never a bare `gh` read**, so the guidance lives where agents encounter it, not
only in this issue.

## Tests (targeted, via scripts/pytest-clean.sh)

- `resolve_pr_head_sha`: git-primary success; gh fallback when git empty;
  cross-check disagreement returns git value + warns; both empty → None.
- **Regression (the acceptance bar):** `_check_verdict_freshness` with a
  trailer carrying the OLD sha while the authoritative git head is the NEW sha
  → gate **blocks** (`failed` gains "predates PR head"). Proves a stale gh read
  cannot make the gate pass, because the authoritative git head wins.
- Docstring/enumeration assertions kept light.

## Acceptance criteria mapping

- [x] Root cause documented (this doc + PR body).
- [x] Gating gh reads enumerated; head-SHA gate made git-authoritative, rest
      justified as cache-proof.
- [x] Regression test: stale SHA → gate blocks.
- [x] `sdlc_next_skill` docstrings accurate.
- [x] Durable guidance in `CLAUDE.md` + skill-context.
