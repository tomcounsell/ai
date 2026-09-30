# Cache-immune PR head resolution for the verdict-staleness gate (#2404)

## The hole

The SDLC verdict-staleness gate (#2062) decides whether a recorded REVIEW
approval still applies to the code in front of it by comparing the head SHA
the verdict attributes to against the PR's **current** head commit. If the
current-head read is stale in the fail-open direction — it returns the
*pre-push* value, which is exactly the value the verdict already records — the gate sees a match and passes a verdict that predates newly pushed
code. A path deliberately designed fail-closed
([`sdlc-verdict-fail-closed-persistence.md`](sdlc-verdict-fail-closed-persistence.md))
silently becomes fail-open.

## Root cause: what was ruled out, and what remains undetermined

The issue hypothesized a local `gh` disk-cache hit. Direct measurement rules
that out as the mechanism, but does **not** establish what the mechanism *was*.
Ruled out (gh 2.89.0, this machine):

- **Not the disk cache.** `~/.cache/gh` is written **only** by
  `gh api --cache <ttl>`. Bare `gh api`, `gh pr view --json`, `gh pr list`,
  `gh issue list`, `gh pr status`, and `gh pr checks` each produced zero new
  cache files and read none (a control `gh api --cache 60s` wrote exactly one).
  The repo passes `--cache` **nowhere** (`grep -rn -- --cache` over all
  `*.py`/`*.sh`/`*.md`), so every SDLC gating read already bypasses the disk
  cache by construction.
- **Not a `gh` shim/wrapper.** `which gh` → `/opt/homebrew/bin/gh`, a real
  Mach-O arm64 binary, not a script that could inject a caching layer.
- **Not an HTTP proxy / MITM configured locally.** No `HTTP(S)_PROXY` /
  `NO_PROXY` env, no `http_unix_socket` in `gh config`. (A transparent
  network-level VPN/DNS cache cannot be ruled out from the host, but nothing is
  configured in the process environment.)
- **Not a PAT-vs-OAuth backend split.** `gh` authenticates with a classic PAT
  (`ghp_…`) from the keyring, the same token class as `GITHUB_PAT`; both hit
  `api.github.com`, so there is no separate backend with different caching.

What is **not** explained: the ~20-hour magnitude. GitHub list/search index lag
is normally seconds to minutes, and what was observed was a coherent,
internally-consistent point-in-time snapshot — not a partially-updated index.
Eventual consistency is a plausible-sounding story but the magnitude strains it,
and the re-run-returned-correct-minutes-later observation is consistent with
several mechanisms. **Root cause of the 20-hour stale `gh issue list` snapshot
remains undetermined.** The honest state is: the local disk cache is eliminated,
so the SDLC gating helpers were never actually exposed via it, but the vector
behind the observed backlog staleness is unknown.

## The fix: resolve the head SHA from git — defense-in-depth, not a diagnosed-cache patch

This fix is **not** justified by a diagnosed cache defeating the gate today (the
disk cache was ruled out). It is defense-in-depth against an *unidentified*
staleness vector: whatever made a `gh issue list` snapshot 20 hours stale,
`git` **cannot** be stale for ref/merge state, and it needs no per-machine
config. That property holds regardless of which staleness vector bites, which is
exactly why it is the right lever when the vector is unknown.

`git ls-remote origin refs/pull/{N}/head` returns the PR's head commit over the
**git** transport — a different transport from `gh`'s HTTP layer, sharing no
response cache, and authoritative for the ref. Verified: for PR #2412 it returns
the identical SHA as both `gh api …/pulls/2412 --jq .head.sha` and
`gh pr view --json headRefOid`. This is the same "reconcile against observable
git ground truth" direction #2395's recovery path took, and it is correct
**without per-machine configuration** — no `GH_*` env var to drift out of place
across bridge / worker / launchd / local / subagent contexts.

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

## Two predicates over the head signal (#3260)

Resolving the head correctly is only half the gate; the other half is what a
*missing* head signal means. The router splits that question in two, because the
safe answer differs by consumer:

| Predicate | Absent `pr_head_sha` | No recorded REVIEW verdict | Consumers |
|-----------|----------------------|----------------------------|-----------|
| `_review_verdict_head_is_stale` | `False` — **inert**, the row does not claim the state | `False` — the no-verdict recovery rows own it | non-terminal: G3 leg 3, dispatch row 8f |
| `_review_verdict_head_is_verified_fresh` | `False` — **no evidence, no merge** | `False` | terminal only: G3 leg 1, G6, dispatch row 10 |

The two return `False` on the same inputs but mean opposite things by it. For a
row that re-dispatches `/do-pr-review`, "no signal" should not fire the row —
something downstream will answer. For a dispatch that *ends the lane in a merge*,
"no signal" must not be read as "fresh enough": a terminal site requires
**positive evidence** that the `APPROVED` verdict judged the PR's live head.

**All three terminal `/do-merge` dispatches carry it** — G3 leg 1, G6's
fast-path, and dispatch row 10. Closing it at only one or two of them relocates
the hole rather than closing it; row 10 in particular is the last rule in the
table, so a gap there is reached by every state the guards decline.

An absent `pr_head_sha` on an otherwise merge-ready lane therefore escalates to
`Blocked(guard_id='NO_RULE')` **by design**: row 8f is inert on an absent key,
row 9 declines once DOCS is complete, and row 10 now declines too, so no rule
owns the state. That is the intended fail-closed landing — a human is asked
rather than an unverified merge being taken. Row 8f was deliberately **not**
widened to catch it; making the re-review row fire on an absent signal would
trade a fail-closed halt for a silent re-review loop.

In production the key is never actually absent: `tools/sdlc_next_skill.py::_build_context`
sets `pr_head_sha` unconditionally whenever `pr_number` is set and a REVIEW
verdict is recorded — a real SHA, or `""` plus `pr_head_sha_lookup_failed` on
lookup failure. The empty-string sentinel is itself fail-closed and lands on row
8f. Requiring presence costs live lanes nothing and closes the hole against
non-CLI and future callers.

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
- `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py` —
  the two-predicate split: all three terminal sites decline on an absent
  `pr_head_sha` (G3 leg 1, G6, and row 10 in isolation with G6 monkeypatched out
  of `GUARDS`), the merge-ready absent-key state lands on
  `Blocked(guard_id='NO_RULE')`, and the negative controls pin that a fresh head
  still merges, the empty sentinel still lands on row 8f, and
  `_review_verdict_head_is_stale` plus row 8f are unchanged.
