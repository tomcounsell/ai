# do-merge addendum — this repo only
<!-- Do not duplicate content from the global merge skill (.claude/skills-global/do-merge/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Stage/Verdict Substrate (the generic body defers these to here)

This repo provides the `sdlc-tool` substrate. It maps onto the global skill's
generic steps as follows:

- **PR-number resolution (Variables).** When PR_ARG is empty, recover it from
  pipeline state: `sdlc-tool stage-query --issue-number N` → `_meta.pr_number`.
- **Step 0 stage marker.** Probe the substrate and write the in_progress marker.
  `{run_id}` is the run identity emitted by the invoking supervisor's
  `sdlc-tool session-ensure` output (issue #2003) — stage-marker is
  state-mutating and requires it:
  ```bash
  sdlc-tool stage-marker --stage MERGE --status in_progress --issue-number {issue_number} --run-id {run_id}
  ```
  Parse the JSON: `{"status": "in_progress"}` → substrate present, proceed;
  `{"status": "degraded", ...}` → announce "running in degraded mode (state not
  persisted)" and proceed (the gate depends only on `gh`); non-zero exit →
  report the stderr diagnostic and proceed.
- **Steps 1–3 deterministic gate — the shared merge predicate.** Evaluate the
  single deterministic predicate. It is the SAME helper the merge-guard hook
  enforces at the choke point, so skill and hook cannot drift (#1944 class):
  ```bash
  python -m tools.merge_predicate --pr-number {PR} --run-id {run_id} --json
  ```
  Output shape: `{"allowed": bool, "failed_checks": [...], "substrate_present":
  bool, "notes": [...]}`; exit 0 iff allowed. **Always pass `--run-id {run_id}`**
  (the run identity from `session-ensure`) — it is required for the single-owner
  MERGE gate (group (d)) below; omitting it silently skips that gate. One call
  covers all four check groups:
  - **(a) PR state**: OPEN, MERGEABLE, mergeStateStatus CLEAN, CI green
    (FAILURE/ERROR fail; pending is not-green), and a word-boundary
    `Closes/Fixes/Resolves #N` issue link in the body.
  - **(b) DOCS stage gate** (the #1944 Step 2b semantics): `stages.DOCS ==
    completed` passes; `in_progress` hard-fails (the sole affirmative "DOCS
    unfinished" signal — cuttlefish #577 shape); `pending`/empty stages degrade
    to a `docs/features/{slug}.md` existence check, slug derived from the PR
    head ref (main/master/HEAD/empty → no usable slug → FAIL).
  - **(c) REVIEW verdict freshness** (#2003 BLOCKER 2): a recorded verdict must
    exist, contain `APPROVED` (case-insensitive), and be fresh against the PR's
    latest commit — via the `REVIEW_CONTEXT head_sha=` trailer when present,
    else recorded-at timestamp vs latest-commit committer date. A stale
    APPROVED verdict FAILS with `REVIEW verdict predates PR head commit`.
  - **(d) Single-owner MERGE lease** (issue #2026, WS1): the merge actor's
    `run_id` must hold the current per-issue SDLC lease. This refuses a
    parallel fork/lineage that never held the lease from merging past a
    supervisor's still-blocked gate (Race 2). Enforced only when `--run-id` is
    supplied — so **always pass it**; the merge-guard hook, which carries no
    run identity, skips this gate but still enforces (a)/(b)/(c). Under the
    single-owner invariant this also enforces "`run_id` matches the run that
    recorded the operative REVIEW verdict": verdict recording is itself
    lease-gated, and the supervisor holds the one lease continuously for the
    whole run. Fails **open** on a Redis error (lease confirmed), **closed** on
    a substrate-present lock-import failure. A refusal reads
    `single-owner MERGE: merge actor run_id does not hold the issue lease ...`.

  `allowed: false` → report every `failed_checks` leg, emit `GATES_FAILED`,
  and route back (`/do-docs` for the DOCS leg, `/do-pr-review`/`/do-patch` for
  verdict legs). Do NOT re-implement any of these checks inline in this file —
  the helper is the single source; the parity test
  (`tests/unit/test_do_merge_docs_gate.py`) breaks on drift.
  - **Tracked-issue resolution for (b)/(c) (#2034).** Groups (b) and (c) key
    on the SDLC-tracked issue derived from the PR's branch slug
    (`session/{slug}` → the live, project-scoped `AgentSession.issue_number`),
    not the first `Closes #N` in the PR body. A PR that closes several
    sub-issues under an umbrella tracking issue records its DOCS marker and
    REVIEW verdict on the umbrella; keying on the first-match body issue
    false-fails the gate for that shape. When no tracked issue resolves (no
    session for the slug, project unresolved, or the lookup degrades), groups
    (b)/(c) fall back to the first-match body issue — single-issue PRs are
    unaffected. When more than one distinct tracked issue is found for the
    slug, the predicate **fails closed** with an explicit
    `tracked-issue lookup ambiguous` entry in `failed_checks` rather than
    guessing. Group (a)'s body-link presence check always uses the raw
    first-match body issue, unchanged.
- **Step 4 merge-authorization guard.** The merge-guard hook
  (`.claude/hooks/validators/validate_merge_guard.py`) evaluates the SAME live
  predicate (`tools.merge_predicate`) when the merge command runs. On the happy
  path `/do-merge` does NOT create or delete any authorization file — the hook
  allows the merge because the predicate passes. The
  `data/merge_authorized_{PR}` file survives only as an explicit **break-glass
  override** for a human operator when the substrate is down: it must contain a
  line `override: <reason>` (non-empty reason). Empty or legacy touch-files are
  ignored (treated as absent). Every accepted override is logged at WARNING and
  emits the `merge_guard.override_used` metric, so uses surface on the
  dashboard. Delete the override file immediately after use.
- **Step 5 completion marker.** Same run identity as Step 0:
  ```bash
  sdlc-tool stage-marker --stage MERGE --status completed --issue-number {issue_number} --run-id {run_id}
  ```

## Documentation Gate

The authoritative check is DOCS *stage completion*, evaluated as group (b) of
the shared predicate (`tools/merge_predicate.py`): PASS when `stages.DOCS ==
completed`, hard-FAIL closed when it is `in_progress`. When the marker is
unreadable (session reaped, empty `stages`) or the stage never started
(`pending`), the gate degrades to verifying `docs/features/{slug}.md` exists —
retained as the degraded fallback rather than a separate, weaker check.
Present ⇒ PASS (degraded); absent ⇒ FAIL, missing feature docs block the merge.

## Ruff Gates

The merge gate must confirm:
- `python -m ruff check .` exits 0
- `python -m ruff format --check .` exits 0

These run in the worktree, not main.

## Plan Migration

After merge, on `main`, run the deterministic migration primitive:

```bash
python scripts/migrate_completed_plan.py --issue <closed-issue-number> --apply
```

This resolves the plan by reading its `tracking:` frontmatter (not by guessing a
filename from the branch slug — a slug≠filename mismatch never bites) and does a
guarded `git mv` into `docs/plans/completed/`. The plan stays on `main` (not the
branch) throughout the lifecycle — migrate it on `main` post-merge, the same as
before, just via this command instead of a hand `git mv`.

The command is evidence-gated in code, so it is safe to run after **every**
merge: it checks the issue's live state and prints `Verdict: skipped-open`
(exit 1) unless the tracking issue is literally closed. A multi-PR issue (PR 1
merged, issue open for PR 2) keeps its plan in root; a `gh` outage defers.

`migrate_plan_to_completed()` (the primitive this command wraps, in
`scripts/migrate_completed_plan.py`) is also the single mechanism the
`merged-branch-cleanup` reflection calls. That reflection is the path-independent
backstop for merges that bypass `/do-merge` entirely — a raw-terminal `gh pr
merge`, a forked `/do-sdlc` run, or a cross-machine merge all skip this
deterministic step, so the daily reflection sweep is what eventually migrates
those plans instead. See `docs/features/plan-migration-invariant.md`.

**A non-zero exit from this command is not a no-op to ignore.** The CLI exits
`0` only for `migrated`/`already-migrated`; it exits `1` and prints
`Verdict: dirty-tree-skip` (or `rebase-conflict-skip`) when the primitive took
its report-only fallback instead of moving the plan. Do not silently retry or
swallow this — surface it in the merge report so a human knows the primary
path did not migrate this plan and the daily reflection backstop is the only
thing that will (within its next cycle, not immediately).

## Post-Merge Memory Extraction

After merge, the pipeline runs post-merge learning extraction. This distills PR takeaways into memories (importance=7.0). No manual action needed — the worker's post-merge learning extraction handles it automatically.

## Post-Merge Site Deploy

If the merged diff touched `site/`, `wrangler.jsonc`, or `src/index.js`, redeploy the
public docs site (valorengels.com) from the merged `main` checkout:

```bash
if git diff --name-only HEAD~1 HEAD | grep -qE '^(site/|src/index\.js$|wrangler\.jsonc$)'; then
  scripts/deploy-site.sh
fi
```

`scripts/deploy-site.sh` runs `wrangler deploy` + a liveness curl and is **non-fatal to the
merge** — report its outcome, do not gate the merge on it. On a machine without `wrangler`
or the vault `CLOUDFLARE_API_TOKEN` the script exits 0 with a "redeploy needed" notice, which
is the correct behavior off the deploy machine. A liveness failure exits 1 and points at
`wrangler rollback` — surface that in the merge report. See
[`docs/features/valorengels-site.md`](../features/valorengels-site.md).

## Worktree Cleanup

After a successful merge, remove the worktree:
```bash
git worktree remove .worktrees/{slug}
```

Or use the dedicated script (preferred, since it also deletes the local branch and prunes stale worktree refs):
```bash
python scripts/post_merge_cleanup.py {slug}
```

The branch `session/{slug}` is deleted automatically by GitHub on merge if "delete branch on merge" is enabled.

### Busy Guard (issue #1357)

`post_merge_cleanup.py` refuses to delete a worktree while a non-terminal `AgentSession` still references it as `working_dir`. This protects against the macOS cwd-vanished wedge (investigation #1246): deleting a directory out from under a live SDK subprocess does not signal that subprocess; `getcwd(3)` returns ENOENT, the harness hangs forever in `proc.communicate()`, and the session row sits at `status=running` for hours.

The script's exit codes:

| Exit | Meaning |
|------|---------|
| 0 | Cleanup succeeded (or already clean) |
| 1 | Generic error — git/branch removal failed |
| 2 | **Blocked** — a live session is using the worktree |

When you see exit 2, the stderr line points to the offending session:

```text
Error: worktree .worktrees/{slug} is in use by session_id=0_LIVE.
Investigate the session (valor-session status --id 0_LIVE);
kill it if dead (valor-session kill --id 0_LIVE) and re-run.
```

Operator response, in order:
1. Run `valor-session status --id <session_id>` to verify whether the session is genuinely live or wedged.
2. If wedged or dead: `valor-session kill --id <session_id>` then re-run `post_merge_cleanup.py`.
3. If genuinely live and the cleanup must proceed anyway, override programmatically with `cleanup_after_merge(repo_root, slug)` after passing `force=True` to `remove_worktree`. **Do not make `--force` your reflex** — copy-paste `--force` defeats the protection. The WARNING log on `force=True` (`force-removing worktree ... despite live session_id=...`) is grep-able for audit.

The complementary defense at runtime is the `BackgroundTask._watchdog` cwd-vanished check: if a worktree disappears underneath a session by some other path (manual `rm -rf`, OS-level cleanup, recovery script), the watchdog cancels the work task within one heartbeat tick (~60s in production), logs `cwd_vanished session_id=...`, and increments `{project_key}:session-health:cwd_vanished`.

## Bridge/Worker Restart After Merge

If the merged PR touched `bridge/`, `agent/`, or `worker/`, run:
```bash
./scripts/valor-service.sh restart
```
Confirm with `tail -5 logs/bridge.log` showing "Connected to Telegram".

## Gate Stack (this repo's deterministic checks)

The portable `/do-merge` skill performs the generic verify-then-merge gate
(OPEN / mergeable / CI-green / REVIEW-approved / issue-linked). This repo
layers the additional deterministic gates below on top, in this order. They
each emit `GATES_FAILED` on failure; if any prints `GATES_FAILED`, report the
specific blocker and do NOT merge.

### Shape Classification

The classifier inspects the PR diff and returns one of: `docs-only`
(skip Lockfile + Full Suite), `lockfile-only`, `small-patch` (targeted
pytest), `mixed` (full stack, log disqualifiers), or `feature` (default /
full stack — the status quo). It defaults to `feature` on any ambiguity.

```bash
SHAPE_JSON=$(python -m scripts.pr_shape_classify --pr "$ARGUMENTS" 2>/dev/null || echo '{"shape":"feature"}')
SHAPE=$(echo "$SHAPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('shape','feature'))")
SHA=$(gh pr view "$ARGUMENTS" --json headRefOid -q .headRefOid 2>/dev/null || echo "")
CACHED_VERDICT=""
if [ -n "$SHA" ]; then
  CACHED_VERDICT=$(python -m scripts.pr_shape_cache get --pr "$ARGUMENTS" --sha "$SHA" 2>/dev/null || echo "")
fi
```

The Shape Classification block MUST precede the Lockfile and Full Suite gates
so `$SHAPE` / `$CACHED_VERDICT` are available downstream. A per-SHA verdict
cache (`data/pr_shape_verdict_cache.json`, gitignored) lets an unchanged tree
skip the full pytest re-run on the same baseline.

### Review Verdict Freshness (moved into the shared predicate)

The stale-approval protection (#1932/#1941 class — an APPROVED verdict left
over from before a force-push or new commits) is enforced as group (c) of the
shared predicate (`python -m tools.merge_predicate --pr-number {PR} --json`,
already run in Steps 1–3 above): the recorded REVIEW verdict must be APPROVED
AND fresh against the PR's latest commit, preferring the
`<!-- REVIEW_CONTEXT head_sha=... -->` trailer `/do-pr-review` emits (exact
head-SHA match) and falling back to recorded-at timestamp vs the latest
commit's `committer.date`. Missing latest-commit data fails closed — a silent
fallback would defeat the exact stale-Approved-after-force-push bug this check
prevents. Do not re-implement the filter inline here; the same check runs in
the merge-guard hook, so a stale approval that slips past the skill still
blocks at the choke point. A stale-but-safe diff (docs-only re-push after
approval) needs a fresh review or a matching-trailer re-record — the predicate
does not re-admit prior approvals by diff shape.

### Lockfile Sync Check

```bash
if [ "$SHAPE" = "docs-only" ]; then
  echo "LOCKFILE: SKIP — docs-only shape cannot affect lockfile"
elif uv lock --locked >/dev/null 2>&1; then
  echo "LOCKFILE: PASS"
else
  echo "LOCKFILE: FAIL — uv.lock is out of sync with pyproject.toml"
  echo "GATES_FAILED"
fi
```

### Full Suite Gate

Run the full suite on the PR branch; compare failures against the categorised
baseline (`scripts/baseline_gate.py` — see
[`merge-gate-baseline.md`](../features/merge-gate-baseline.md)). New
`real`/`hung`/`import_error` failures block; `flaky` re-occurrences are
reported but non-blocking. Shape-aware routing:

```bash
if [ "$SHAPE" = "docs-only" ]; then
  echo "FULL_SUITE: SKIP — docs-only shape (no Python files changed)"
elif [ "$SHAPE" = "small-patch" ]; then
  TARGETED_TESTS=$(echo "$SHAPE_JSON" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('tests_to_run',[])))")
  echo "FULL_SUITE: targeted pytest for small-patch -> $TARGETED_TESTS"
  pytest $TARGETED_TESTS -q --tb=no --junitxml=/tmp/pr_run.xml
else
  pytest tests/ -q --tb=no --junitxml=/tmp/pr_run.xml
fi
```

See [`docs/features/pr-shape-aware-merge-gates.md`](../features/pr-shape-aware-merge-gates.md)
for the shape taxonomy, gate matrix, defect-detection contract, and cache
eviction policy.
