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

The branch `session/{slug}` is deleted automatically by GitHub on merge if "delete branch on merge" is enabled.

## Bridge/Worker Restart After Merge

If the merged PR touched `bridge/`, `agent/`, or `worker/`, run:
```bash
./scripts/valor-service.sh restart
```
Confirm with `tail -5 logs/bridge.log` showing "Connected to Telegram".
