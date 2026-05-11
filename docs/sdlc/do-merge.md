# do-merge addendum — this repo only
<!-- Do not duplicate content from the global merge command (.claude/commands/do-merge.md). Only include what is unique to this repo. Max 300 lines. -->

## Documentation Gate

Before merging, verify `docs/features/{slug}.md` exists if the plan specified one. This is a hard gate — missing feature docs block the merge.

## Ruff Gates

The merge gate must confirm:
- `python -m ruff check .` exits 0
- `python -m ruff format --check .` exits 0

These run in the worktree, not main.

## Plan Migration

After merge, move the plan from `docs/plans/{slug}.md` to `docs/plans/completed/{slug}.md` on `main`. The plan stays on `main` (not the branch) throughout the lifecycle — migrate it on `main` post-merge.

## Post-Merge Memory Extraction

After merge, the pipeline runs post-merge learning extraction. This distills PR takeaways into memories (importance=7.0). No manual action needed — the worker handles it automatically via `_handle_merge_completion()`.

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

## Shape-Aware Routing

Each PR is classified into a shape (`docs-only`, `lockfile-only`,
`small-patch`, `mixed`, `feature`) before the gate stack runs. Safe shapes
get a lighter gate set proportional to their blast radius; the `feature`
shape is the unchanged status quo.

A per-SHA verdict cache (`data/pr_shape_verdict_cache.json`, gitignored)
lets an unchanged tree skip the full pytest re-run on the same baseline.
A safe-shape follow-up commit on a previously-approved PR preserves the
prior `## Review: Approved` (anchored to the approval SHA via the
`<!-- REVIEW_CONTEXT head_sha=... -->` trailer); pre-trailer or unsafe
follow-ups still invalidate.

See [`docs/features/pr-shape-aware-merge-gates.md`](../features/pr-shape-aware-merge-gates.md)
for the shape taxonomy, gate matrix, defect-detection contract, cache
eviction policy, and the relationship to
[`merge-gate-baseline.md`](../features/merge-gate-baseline.md).
