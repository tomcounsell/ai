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
  bool, "notes": [...]}`; exit 0 iff allowed. **Always pass `--run-id {run_id}`**:
  without it the single-owner lease leg is silently skipped. The legs:
  - **(a) PR state**: OPEN, MERGEABLE, `CLEAN`, CI green (pending is not
    green), and a `Closes/Fixes/Resolves #N` issue link.
  - **(b) DOCS**: `stages.DOCS == completed`; `in_progress` fails; an
    unreadable or `pending` marker degrades to a `docs/features/{slug}.md`
    existence check.
  - **(c) REVIEW**: `stages.REVIEW == completed` plus a recorded `APPROVED`
    verdict fresh against the PR head (the verdict's `head_sha`, else
    recorded-at vs the latest commit's `committer.date`). Drift from the
    reviewed SHA is tolerated only when `tools/sdlc_review_drift.py` classifies
    it `docs_only` (see
    [`docs/features/sdlc-review-drift-classifier.md`](../features/sdlc-review-drift-classifier.md));
    the head is read git-first via `tools/pr_head_resolver.py`, never a stale
    `gh` read.
  - **(d) Single-owner lease**: the merge actor's `run_id` must hold the
    issue's SDLC lease.

  Legs (b)/(c) key on the tracked issue the `PipelineLedger` records for the PR
  number (an umbrella issue may differ from the first `Closes #N`); an
  ambiguous lookup fails closed.

  `allowed: false` → report every `failed_checks` entry verbatim, emit
  `GATES_FAILED`, and route: DOCS leg → `/do-docs`; REVIEW marker not completed
  → re-run `sdlc-tool verdict finalize`; stale or missing verdict (`REVIEW
  verdict predates PR head commit`) → a fresh `/do-pr-review`, never a
  `finalize` re-run; findings → `/do-patch`. Never re-implement a leg inline;
  the parity test (`tests/unit/test_do_merge_docs_gate.py`) breaks on drift.
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
`Verdict: dirty-tree-skip`,
`Verdict: fetch-failed-skip`,
`Verdict: stale-main-skip`,
`Verdict: mutation-failed-skip`,
`Verdict: rolled-back-skip`, or
`Verdict: rollback-refused-skip`
when the primitive took its report-only fallback, couldn't reach `origin` to
compare/fast-forward, refused to mutate a diverged local `main`, failed its own
`git mv`/`git commit` after the preconditions passed, rolled back a migration
commit that couldn't land, or could not safely drop that commit, instead of
moving the plan. Do not silently retry or swallow this — surface it in the
merge report so a human knows the primary path did not migrate this plan and
the daily reflection backstop is the only thing that will (within its next
cycle, not immediately). `rollback-refused-skip` in particular needs a human to
look at the shared `main` checkout directly. It covers four shapes: git
refused to drop the migration commit (a peer's uncommitted edits or commit are
in the way, or the index was locked) and that commit is still stranded on local
`main`; the ahead-set could not be determined at all; commits are ahead but
none of them is the migration commit; or nothing is ahead yet the migration is
absent on `origin/main`. In the last shape nothing is stranded, but the state
is still unexplained and left untouched.

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

`post_merge_cleanup.py` exits **2** when a non-terminal `AgentSession` still uses
the worktree as `working_dir` (deleting a live session's cwd wedges it
permanently); exit 1 is a generic git error. On exit 2, check the named session
with `valor-session status --id <id>`; if it is wedged or dead,
`valor-session kill --id <id>` and re-run. Do not force-remove a worktree under
a live session; the only override is programmatic (`force=True` to
`remove_worktree`) and is logged for audit.

## Bridge/Worker Restart After Merge

If the merged PR touched `bridge/`, `agent/`, `worker/`, `models/`, `tools/`, `mcp_servers/`, or `config/` (the same path set `scripts/remote-update.sh` and `scripts/update/service.py` use to decide a restart), run:
```bash
./scripts/valor-service.sh restart
```
Confirm with `tail -5 logs/bridge.log` showing "Connected to Telegram". On a worker-only machine use `worker-restart` instead. A PR that touched `reflections/` needs `scripts/install_reflection_worker.sh` (or `/update` with service restart enabled) as well: the reflection worker is a separate launchd service that `valor-service.sh restart` does not cycle.

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
  `docs/sdlc/do-test.md`) — its baseline script classifies pre-existing
  failures against main there, where the pipeline can iterate and patch.
- The **nightly regression run** (`scripts/nightly_regression_tests.py`) is
  the backstop for anything that slips through. It collects the default
  test collection (`tests/`, not just `tests/unit/`) through the sanctioned
  wrapper, validates that the run actually executed before trusting its
  result, and installs on any machine that owns a project (worker-role, not
  bridge-role) — see `docs/features/nightly-regression-tests.md`.

Do not add a pytest invocation to this gate stack. A merge-time
full-suite gate (shape classifier, per-SHA verdict cache, categorised
baseline comparison) wedges routinely — xdist bringup deadlocks, worker
crashes, Redis DB pollution from concurrent suites — so the gate stack
carries no such step.

### Lockfile Sync Check

```bash
if uv lock --locked >/dev/null 2>&1; then
  echo "LOCKFILE: PASS"
else
  echo "LOCKFILE: FAIL — uv.lock is out of sync with pyproject.toml"
  echo "GATES_FAILED"
fi
```
