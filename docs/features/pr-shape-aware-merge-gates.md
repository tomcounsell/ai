# PR-Shape-Aware Merge Gates

The `/do-merge` skill classifies each pull request's diff into a **shape**
and runs a gate set proportional to its blast radius. Docs-only typo fixes
no longer pay the full pytest tax; lockfile-only regen runs the same gates
as today; small patches get a targeted pytest scope; everything else flows
through the original full-stack gate.

This is a relief valve, not a bypass. Any ambiguity falls through to the
`feature` shape (full gates). The contract for genuine feature work is
unchanged.

Tracks issue [#1283](https://github.com/tomcounsell/ai/issues/1283).

## Shape Taxonomy

| Shape           | Allowlist                                        | Lockfile Sync | Full Suite               | Stale-Review Filter             |
|-----------------|--------------------------------------------------|---------------|--------------------------|---------------------------------|
| `docs-only`     | `docs/**`, `**/*.md`, `CHANGELOG*`, `README*`    | SKIP          | SKIP                     | Safe-shape exempt               |
| `lockfile-only` | strictly the literal file `uv.lock`              | RUN           | RUN (full)               | Safe-shape exempt               |
| `small-patch`   | <=20 net lines, no new/deleted files, every touched `*.py` maps to >=1 existing test | RUN | RUN (targeted: touched-file -> `tests/**/test_{stem}.py`) | Safe-shape exempt |
| `mixed`         | claimed safe shape with disqualifiers (>=50% match + >=1 violation) | RUN | RUN (full)               | NOT exempt                      |
| `feature`       | default (anything ambiguous)                     | RUN           | RUN (full)               | NOT exempt                      |

**Always-on cheap gates** (ruff lint, ruff format, syntax checks) run on
every PR regardless of shape. The shape only affects expensive gates.

## Defect Detection Contract

The classifier is **defensive by default**. Any condition that cannot be
verified pushes the PR back to `feature` (full gates):

- Missing `gh` CLI or failed `gh pr diff` -> `feature`
- Empty/whitespace-only file list -> `feature`
- A touched file with no exact-name test match AND a stem shorter than
  4 characters -> `feature` (short stems over-match wildly)
- A touched file whose substring test glob returns more than 8 matches
  -> `feature` (over-match cap rejects unbounded test sets)
- A touched `__init__.py` or `_helper.py` (private/package files) -> `feature`
- A diff that mostly looks like a safe shape (>=50% files match) but has
  >=1 disqualifier -> `mixed`, full gate stack runs, the disqualifier list
  is logged to stderr as `SHAPE: mixed -- claimed safe shape '<X>' touched
  non-allowlisted paths: <list>` so the developer can see exactly why
  their PR was bumped.

The 50% threshold prevents two failure modes:
- A 1-file Python change isn't classified as "claimed docs-only" just
  because the file isn't a doc.
- A single doc edit attached to a 50-file refactor doesn't look like a
  "claimed docs-only" -- the docs are the minority.

## Gate Matrix Visualised

```
PR diff ---> classifier ---> shape
                                |
              .-----------------+--------------+--------------+----------.
              v                                v                          v
        docs-only                     lockfile-only / small-patch / mixed / feature
        |                                                                  |
        skip Lockfile Sync, skip Full Suite                                run all gates per matrix
        ruff lint + ruff format only                                       (cache may skip pytest re-run)
```

## Per-SHA Verdict Cache

To avoid re-running pytest on the same tree + same baseline, the gate
caches its verdict at `data/pr_shape_verdict_cache.json`. The cache key is::

    {pr_number}:{commit_sha}:{baseline_hash[:12]}

where `baseline_hash` is the first 12 chars of `sha256(data/main_test_baseline.json)`.
Any baseline change invalidates all cached entries for that baseline (the
key simply mismatches and a fresh compute runs). See
[`merge-gate-baseline.md`](merge-gate-baseline.md) for the baseline
contract and refresh tool.

### Cache invariants

- **Schema-versioned**: `{"schema_version": 1, "entries": {...}}`. Unknown
  schema -> file is reset to empty + warning logged.
- **LRU at 100 entries**: when the cache exceeds the cap, the entry with the
  oldest `last_used_at` is dropped on the next write.
- **Atomic writes**: every write goes through tmpfile + `os.replace` so a
  crashed write never leaves a partial file. Tmpfiles in the cache directory
  are cleaned up on any error.
- **Corrupt-file recovery**: a malformed JSON file is treated as empty and
  re-initialised on the next write. `get_cached_verdict` returns `None`.
- **Concurrent-write serialization**: every read-modify-write goes through
  an `fcntl.flock(LOCK_EX)` on the sidecar `data/pr_shape_verdict_cache.lock`.
  The lock is advisory but every cache writer goes through
  `pr_shape_cache.write_verdict()`. Lock timeout is 10 seconds; on timeout
  the writer logs a warning and skips the write (cache miss next time is
  acceptable -- this is an optimization, not correctness state).

Both the cache file and the lock file are gitignored under the existing
`data/` rule at `.gitignore:181`. Verified by `git check-ignore`.

## Stale-Review Safe-Shape Exemption

The merge gate's existing stale-review filter (shipped via #1155) drops any
`## Review: Approved` comment that pre-dates the latest commit. This makes
it impossible to merge a PR after a follow-up commit without re-running the
review.

The safe-shape exemption **narrows** that filter: when no current review
exists but a prior approval exists AND the diff between the approval-commit
and HEAD classifies as a safe shape, the prior approval is re-admitted.

The approval-commit SHA is extracted from the
`<!-- REVIEW_CONTEXT head_sha=<SHA> pr_body_hash=<HASH> -->` HTML comment
that `/do-pr-review` already emits at the end of every review body (see
`.claude/skills/do-pr-review/sub-skills/post-review.md`). The trailer is
the canonical anchor for "what code state was approved" -- the SHA the
reviewer actually evaluated.

### Why the trailer, not commit history

- PR commit history can be force-pushed.
- Reviews can be replayed idempotently (re-emitting the prior verdict on
  the same HEAD SHA + body hash).
- The trailer's `head_sha` is stable under both.

### Fail-closed on missing trailer

Reviews authored before the trailer existed (or human-authored reviews
that bypassed the skill) lack the anchor SHA. The exemption logs::

    REVIEW_COMMENT: SKIP -- prior approval has no REVIEW_CONTEXT trailer;
                            cannot anchor safe-shape diff. Falling through
                            to require fresh review.

and falls through. A `feature`-shape follow-up still invalidates a prior
approval. A safe-shape follow-up on a pre-trailer approval also still
invalidates (defensive). The exemption ONLY admits a verifiable safe shape
on a verifiable approval.

### Fetch-on-demand for unfetchable SHAs

`gh pr checkout` only fetches the PR's HEAD ref; older approval SHAs may
not be in the local objects database when `/do-merge` runs from a fresh
clone or after a worktree switch. The exemption tries::

    git -C $REPO cat-file -e $APPROVAL_COMMIT_SHA \
      || git -C $REPO fetch origin $APPROVAL_COMMIT_SHA

and skips the exemption if both fail.

## Targeted-Test Mapping (small-patch)

The `small-patch` shape requires every touched `*.py` file to map to >=1
existing test. The mapping is two-tiered:

1. **Tier 1 -- exact-name match**: `tests/**/test_{stem}.py`. Always tried first.
2. **Tier 2 -- substring match**: `tests/**/test_*{stem}*.py`. Only used as
   fallback when Tier 1 finds nothing AND the stem is >=4 characters.

If any touched file fails to map, the classifier returns `feature` (the
safe direction). Per-file substring-match cap of 8 prevents unbounded
test sets from a poorly-specific stem.

### Defended constants

| Constant                  | Value | Rationale                                                                 |
|---------------------------|-------|---------------------------------------------------------------------------|
| `SMALL_PATCH_LINE_BUDGET` | 20    | Conservative based on sample of recent merged PRs                         |
| `SHORT_STEM_THRESHOLD`    | 4     | Stems >=4 chars are specific enough that substring matches are meaningful |
| `SUBSTRING_MATCH_CAP`     | 8     | Upper bound of "targeted" before dispatch cost approaches the full suite  |
| `MAX_ENTRIES` (cache)     | 100   | Cache footprint stays sub-100kB even at full capacity                     |
| `LOCK_TIMEOUT_SECS`       | 10    | Tolerates pathological concurrent-write contention; cache is best-effort  |

## Relationship to merge-gate-baseline.md

This feature is layered on top of [`merge-gate-baseline.md`](merge-gate-baseline.md):

- The baseline file (`data/main_test_baseline.json`) is unchanged. The
  schema-v2 contract from #1084 is preserved end-to-end.
- The verdict cache stores the *output* of `compute_gate_verdict()`, not
  the baseline itself. No schema changes required.
- The baseline-content hash in the cache key means any baseline change
  (post-merge reset, manual refresh, schema migration) silently
  invalidates all cached entries for that baseline -- no manual cache
  busting needed.
- The classifier never reads the baseline; only the cache does.

## Files

| File                                   | Purpose                                                        |
|----------------------------------------|----------------------------------------------------------------|
| `scripts/pr_shape_classify.py`         | Pure-function classifier + CLI for `--pr` and `--diff-from`/`--diff-to` modes |
| `scripts/pr_shape_cache.py`            | Per-SHA verdict cache with `fcntl.flock` serialization         |
| `.claude/commands/do-merge.md`         | Shape-routing block + cache hooks + safe-shape exemption       |
| `tests/unit/test_pr_shape_classify.py` | Unit tests for every shape, mixed defect paths, mapping safety |
| `tests/unit/test_pr_shape_cache.py`    | Unit tests for hit/miss, LRU, atomic writes, concurrent serialization, lock timeout |
| `tests/unit/test_do_merge_review_filter.py` | Safe-shape exemption regression tests (markdown shape) |
| `tests/integration/test_do_merge_shape_routing.sh` | End-to-end shape routing on synthetic diffs |
| `data/pr_shape_verdict_cache.json`     | Per-machine cache file (gitignored under `data/` rule)         |
| `data/pr_shape_verdict_cache.lock`     | Sidecar lock file for `fcntl.flock` (gitignored)               |

## Observability

The gate emits structured one-line log entries you can grep::

    SHAPE: docs-only (3 file(s))
    SHAPE_CACHE: HIT -- verdict reused from 2026-05-05T12:34:56Z
    SHAPE: mixed -- claimed safe shape 'docs-only' touched non-allowlisted paths: ['agent/foo.py']
    REVIEW_COMMENT: PASS -- Prior approval at abc1234 preserved (post-approval diff is docs-only)
    REVIEW_COMMENT: SKIP -- prior approval has no REVIEW_CONTEXT trailer; ...
    LOCKFILE: SKIP -- docs-only shape cannot affect lockfile
    FULL_SUITE: SKIP -- docs-only shape (no Python files changed)
    FULL_SUITE: PASS (cached; pytest re-run skipped)
    SHAPE_CACHE: WROTE verdict for 1283:abc1234

## Out of Scope

The plan ([§No-Gos](../plans/pr-shape-aware-merge-gates.md#no-gos-out-of-scope))
explicitly excludes:

- Removing any existing gate from the `feature` shape
- Custom per-shape gate matrices configurable via CLI flags or env vars
- Shape routing for non-`/do-merge` skills (e.g. `/do-pr-review`)
- Cross-PR shape memoization
- Parallelising gates within a shape
- Adopting `pytest-testmon` (per-machine state conflict)
- `pyproject.toml` as `lockfile-only` (settled by Open Question 2)

## See Also

- [`merge-gate-baseline.md`](merge-gate-baseline.md) -- the baseline file the cache key hashes
- [`docs/sdlc/do-merge.md`](../sdlc/do-merge.md) -- repo addendum for the merge skill
- [`docs/sdlc/merge-troubleshooting.md`](../sdlc/merge-troubleshooting.md) -- "Why was my PR classified as `mixed`?" guide
- Plan: [`docs/plans/pr-shape-aware-merge-gates.md`](../plans/pr-shape-aware-merge-gates.md)
- Issue: [#1283](https://github.com/tomcounsell/ai/issues/1283)
