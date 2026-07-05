# do-merge addendum — this repo only
<!-- Do not duplicate content from the global merge skill (.claude/skills-global/do-merge/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Stage/Verdict Substrate (the generic body defers these to here)

This repo provides the `sdlc-tool` substrate. It maps onto the global skill's
generic steps as follows:

- **PR-number resolution (Variables).** When PR_ARG is empty, recover it from
  pipeline state: `sdlc-tool stage-query --issue-number N` → `_meta.pr_number`.
- **Step 0 stage marker.** Probe the substrate and write the in_progress marker:
  ```bash
  sdlc-tool stage-marker --stage MERGE --status in_progress --issue-number {issue_number}
  ```
  Parse the JSON: `{"status": "in_progress"}` → substrate present, proceed;
  `{"status": "degraded", ...}` → announce "running in degraded mode (state not
  persisted)" and proceed (the gate depends only on `gh`); non-zero exit →
  report the stderr diagnostic and proceed.
- **Step 2 recorded REVIEW verdict (authority over `gh reviewDecision`).** Read
  the recorded verdict instead of GitHub's native decision:
  ```bash
  sdlc-tool verdict get --stage REVIEW --issue-number {issue_number}
  ```
  The verdict text must contain `APPROVED` (case-insensitive). A
  `CHANGES REQUESTED` / `NEEDS REVISION` verdict, or no verdict at all, FAILS —
  route back to `/do-pr-review` or `/do-patch`. In degraded mode the tool may
  return no data; if approval cannot be confirmed, FAIL closed.
- **Step 4 merge-authorization guard.** `gh pr merge` is blocked by the
  merge-guard hook (`.claude/hooks/validators/validate_merge_guard.py`) unless an
  authorization file exists. Create it immediately before the merge and delete it
  immediately after (success or failure):
  ```bash
  touch data/merge_authorized_{PR}    # before `gh pr merge {PR} --squash`
  rm -f data/merge_authorized_{PR}    # immediately after, every path
  ```
  A copy-pasted `touch data/merge_authorized_{PR}` without the full gate defeats
  the entire mechanism.
- **Step 5 completion marker.**
  ```bash
  sdlc-tool stage-marker --stage MERGE --status completed --issue-number {issue_number}
  ```

## Documentation Gate

Before merging, verify `docs/features/{slug}.md` exists if the plan specified one. This is a hard gate — missing feature docs block the merge.

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

`migrate_plan_to_completed()` (the primitive this command wraps, in
`scripts/migrate_completed_plan.py`) is also the single mechanism the
`merged-branch-cleanup` reflection calls. That reflection is the path-independent
backstop for merges that bypass `/do-merge` entirely — a raw-terminal `gh pr
merge`, a forked `/do-sdlc` run, or a cross-machine merge all skip this
deterministic step, so the daily reflection sweep is what eventually migrates
those plans instead. See `docs/features/plan-migration-invariant.md`.

## Post-Merge Memory Extraction

After merge, the pipeline runs post-merge learning extraction. This distills PR takeaways into memories (importance=7.0). No manual action needed — the worker's post-merge learning extraction handles it automatically.

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

The Shape Classification block MUST precede the Structured Review Comment Check
so `$SHAPE` / `$CACHED_VERDICT` are available downstream. A per-SHA verdict
cache (`data/pr_shape_verdict_cache.json`, gitignored) lets an unchanged tree
skip the full pytest re-run on the same baseline.

### Structured Review Comment Check

Scan **both** issue comments AND PR review submissions for the most recent
`## Review:` body. Stale reviews are filtered by comparing each entry's
timestamp against the PR's latest commit `committer.date` (NOT the author
date) — entries predating the latest commit are dropped as stale (a
force-push would have superseded them).

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
LATEST_COMMIT_DATE=$(gh api repos/$REPO/pulls/$ARGUMENTS/commits --jq '.[-1].commit.committer.date' 2>/dev/null)
if [ -z "$LATEST_COMMIT_DATE" ]; then
  echo "REVIEW_COMMENT: FAIL — could not fetch latest commit date for review filter"
  echo "Diagnose: gh api repos/$REPO/pulls/$ARGUMENTS/commits --jq '.[-1]'"
  echo "GATES_FAILED"
  exit 1
fi
```

On a transient API failure (`LATEST_COMMIT_DATE` empty) the gate FAILS with the
diagnostic above rather than silently regressing to unfiltered behavior — a
silent fallback would defeat the exact stale-Approved-after-force-push bug this
filter prevents.

**Safe-shape exemption:** when no current review exists but a prior
`## Review: Approved` exists AND the diff between the approval-commit and HEAD
classifies as a safe shape, the prior approval is re-admitted. The
approval-commit SHA is extracted from the
`<!-- REVIEW_CONTEXT head_sha=... -->` trailer `/do-pr-review` emits:

```bash
APPROVAL_COMMIT_SHA=$(echo "$PRIOR_BODY" | grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}' | sed 's/REVIEW_CONTEXT head_sha=//' | tail -1)
if [ -z "$APPROVAL_COMMIT_SHA" ]; then
  echo "REVIEW_COMMENT: SKIP — prior approval has no REVIEW_CONTEXT trailer; fresh review required." >&2
else
  git cat-file -e "$APPROVAL_COMMIT_SHA" 2>/dev/null || git fetch origin "$APPROVAL_COMMIT_SHA" 2>/dev/null || {
    echo "REVIEW_COMMENT: SKIP — approval SHA not fetchable; fresh review required." >&2
    APPROVAL_COMMIT_SHA=""
  }
fi
if [ -n "$APPROVAL_COMMIT_SHA" ]; then
  HEAD_SHA=$(git rev-parse HEAD)
  DIFF_SHAPE=$(python -m scripts.pr_shape_classify --diff-from "$APPROVAL_COMMIT_SHA" --diff-to "$HEAD_SHA" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('shape','feature'))")
  case " docs-only lockfile-only small-patch " in
    *" $DIFF_SHAPE "*) echo "REVIEW_COMMENT: PASS — prior approval preserved (post-approval diff is $DIFF_SHAPE)";;
    *) echo "REVIEW_COMMENT: SKIP — post-approval diff is $DIFF_SHAPE (not a safe shape); fresh review required." >&2;;
  esac
fi
```

Only `docs-only lockfile-only small-patch` post-approval diffs re-admit the
prior approval; `feature`/`mixed` shapes still require a fresh review. A prior
approval body without the trailer fails closed (SKIP → fresh review required).

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
