# do-merge addendum — this repo only
<!-- Do not duplicate content from the global merge skill (.claude/skills-global/do-merge/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Stage/Verdict Substrate (the generic body defers these to here)

This repo provides the `sdlc-tool` substrate. It maps onto the global skill's
generic steps as follows:

- **PR-number resolution (Variables).** When PR_ARG is empty, recover it from
  pipeline state: `sdlc-tool stage-query --issue-number N` → `_meta.pr_number`.
- **Step 0 stage marker.** Probe the substrate and write the in_progress marker.
  `{run_id}` is the run identity emitted by the invoking supervisor's
  `sdlc-tool session-ensure` output — stage-marker is
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
  enforces at the choke point, so skill and hook cannot drift:
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
  - **(b) DOCS stage gate**: `stages.DOCS ==
    completed` passes; `in_progress` hard-fails (the sole affirmative "DOCS
    unfinished" signal); `pending`/empty stages degrade
    to a `docs/features/{slug}.md` existence check, slug derived from the PR
    head ref (main/master/HEAD/empty → no usable slug → FAIL).
  - **(c) REVIEW verdict freshness**: a recorded verdict must
    exist, contain `APPROVED` (case-insensitive), and be fresh against the PR's
    latest commit — via the head SHA the verdict attributes to
    (`head_sha_of_record()`: the record's `head_sha` field, else a
    `REVIEW_CONTEXT head_sha=` trailer in the verdict text) when resolvable,
    else recorded-at timestamp vs latest-commit committer date. A stale
    APPROVED verdict FAILS with `REVIEW verdict predates PR head commit`. The
    PR's current head SHA is resolved git-first via
    `tools/pr_head_resolver.py::resolve_pr_head_sha` (`git ls-remote
    refs/pull/N/head`, no shared cache with `gh`), so a stale `gh` head read
    cannot match the trailer and pass a stale approval.
  - **(d) Single-owner MERGE lease**: the merge actor's
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
  - **Tracked-issue resolution for (b)/(c).** Groups (b) and (c) key
    on the SDLC-tracked issue looked up in the durable `PipelineLedger` by PR
    number (`PipelineLedger.query.filter(pr_number=...)`, scoped to the repo
    resolved by `gh repo view`), not the first `Closes #N` in the PR body. A PR
    that closes several sub-issues under an umbrella tracking issue records its
    DOCS marker and REVIEW verdict on the umbrella; keying on the first-match
    body issue false-fails the gate for that shape. `pr_number` is written by
    `sdlc-tool meta-set --key pr_number` at PR creation, so it is populated long
    before the gate runs. When no ledger resolves for the PR number, groups
    (b)/(c) fall back to the first-match body issue — single-issue PRs are
    unaffected. When more than one distinct tracked issue is found for the PR
    number, the predicate **fails closed** with an explicit
    `tracked-issue lookup ambiguous` entry in `failed_checks` rather than
    guessing. Group (a)'s body-link presence check always uses the raw
    first-match body issue, unchanged.
- **Step 4 merge-authorization guard.** The merge-guard hook
  (`.claude/hooks/validators/validate_merge_guard.py`) evaluates the SAME live
  predicate (`tools.merge_predicate`) when the merge command runs. On the happy
  path `/do-merge` does NOT create or delete any authorization file — the hook
  allows the merge because the predicate passes. The
  `data/merge_authorized_{PR}` file is used only as an explicit **break-glass
  override** for a human operator when the substrate is down: it must contain a
  line `override: <reason>` (non-empty reason). Empty touch-files are
  ignored (treated as absent), and so is a **spent** override — one whose PR is
  already merged or closed — so a file left behind after use cannot authorize
  anything later. Every accepted override is logged at WARNING and
  emits the `merge_guard.override_used` metric, so uses surface on the
  dashboard. Delete the override file immediately after use anyway.
- **Step 5 completion marker.** Same run identity as Step 0:
  ```bash
  sdlc-tool stage-marker --stage MERGE --status completed --issue-number {issue_number} --run-id {run_id}
  ```

## PRs With No Plan (the global skill's "did not originate in the pipeline")

The global skill defers the *not applicable* recording command to here. For a
hand-authored fix, a review-derived follow-up, or a dependabot bump there is no
plan document, so PLAN and CRITIQUE were never dispatched and no truthful
CRITIQUE verdict can exist.

**There is nothing extra to run.** `sdlc-tool verdict finalize` writes the REVIEW
completion marker, and that marker's predecessor backfill records the two stages
as `skipped` when it verifies they never ran and do not apply. The ordinary
review-then-merge sequence works unchanged on these PRs.

To state the disposition deliberately instead, before REVIEW runs:

```bash
sdlc-tool stage-marker --stage PLAN     --status skipped --issue-number {issue_number} --run-id {run_id}
sdlc-tool stage-marker --stage CRITIQUE --status skipped --issue-number {issue_number} --run-id {run_id}
```

Both paths run the same verified predicate and reach the same ledger state. It
verifies rather than accepts the claim: refused with `PLAN_EXISTS_NOT_SKIPPABLE`
when a plan document resolves for the issue, and with `STAGE_RAN_NOT_SKIPPABLE`
when the stage already carries a verdict, a recorded dispatch, or a
non-`pending`/`ready` status. `--stage REVIEW --status skipped` is refused
unconditionally with `STAGE_NOT_SKIPPABLE` — REVIEW, DOCS and MERGE are the
stages the predicate reads, so none of them is ever skippable. Everything else
is the ordinary gate: a posted review artifact, a finalized APPROVED verdict, a
DOCS completion marker, `Closes #N` in the body. See
[`docs/features/off-pipeline-merge-path.md`](../features/off-pipeline-merge-path.md).

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
guarded `git mv` into `docs/archive/plans-completed/`. The plan stays on `main` (not the
branch) throughout the lifecycle — migrate it on `main` post-merge via this
command (not a hand `git mv`).

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

### Busy Guard

`post_merge_cleanup.py` refuses to delete a worktree while a non-terminal `AgentSession` still references it as `working_dir`. This protects against the macOS cwd-vanished wedge: deleting a directory out from under a live SDK subprocess does not signal that subprocess; `getcwd(3)` returns ENOENT, the harness hangs forever in `proc.communicate()`, and the session row sits at `status=running` for hours.

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
layers two additional deterministic gates on top: the Ruff Gates (section
above) and the Lockfile Sync Check below. They each emit `GATES_FAILED` on
failure; if any prints `GATES_FAILED`, report the specific blocker and do NOT
merge.

**The merge gate runs no tests.** Every gate command completes in
seconds and cannot wedge. Test responsibility lives elsewhere:

- The **TEST stage** owns the final full-suite run before REVIEW (see
  `docs/sdlc/do-test.md`) — `baseline-verifier` classifies pre-existing
  failures against main there, where the pipeline can iterate and patch.
- The **nightly regression run** (`scripts/nightly_regression_tests.py`) is
  the backstop for anything that slips through. It collects the default
  test collection (`tests/`, not just `tests/unit/`) through the sanctioned
  wrapper, validates that the run actually executed before trusting its
  result, and installs on any machine that owns a project (worker-role, not
  bridge-role) — see `docs/features/nightly-regression-tests.md`.

  The wrapper is referred to obliquely on purpose: a guard forbids the
  runner's literal name anywhere in this section, so that merge-time test
  execution cannot creep back in even as prose. The bright line covers the
  whole section, not just live commands.

Do not add a pytest invocation to this gate stack. A merge-time
full-suite gate (shape classifier, per-SHA verdict cache, categorised
baseline comparison) wedges routinely — xdist bringup deadlocks, worker
crashes, Redis DB pollution from concurrent suites — so the gate stack
carries no such step.

### Review Verdict Freshness

The stale-approval protection (an APPROVED verdict that predates a force-push
or new commits) is enforced as group (c) of the
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

### Verification Outcomes

Group (e) of the same predicate refuses a merge when the lane's recorded
verification aggregate carries a blocking row or cannot be shown fresh against
the PR head. A `FAIL` row says the code is wrong; an `UNEVALUATED` row says the
grader could not answer, and both hold the PR (owner ruling, `ba092a06d`). A
lane whose plan declares no verification table has declared no gate, and the
predicate stands down rather than refusing.

Recovery differs by which line you got, and none of it is guesswork —
**`docs/sdlc/merge-troubleshooting.md`, "Verification Outcomes Hold the PR"**
names each refusal string and its remedy, including the `--record-outcomes`
re-run that re-anchors a stale aggregate. Read that section rather than
re-deriving the fix here.

### Lockfile Sync Check

```bash
if uv lock --locked >/dev/null 2>&1; then
  echo "LOCKFILE: PASS"
else
  echo "LOCKFILE: FAIL — uv.lock is out of sync with pyproject.toml"
  echo "GATES_FAILED"
fi
```
