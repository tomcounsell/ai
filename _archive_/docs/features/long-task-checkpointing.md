# Long-Task Checkpointing — PROGRESS.md and Commit-Frequency Guidance

## What and Why

Long-running dev sessions (multi-hour builds for Medium/Large plans) routinely cross context-compaction boundaries. When compaction fires, the SDK replaces the raw conversation history with a compacted summary — and that summary rarely preserves high-fidelity working state such as "I was mid-edit of `process_batch()` at line 214; next step was to add the `num_turns` snapshot before the `await`."

The infrastructure side of compaction was hardened by [Compaction Hardening](compaction-hardening.md) (#1127): a PreCompact hook writes a JSONL backup, a 5-minute cooldown prevents thrashing, and a 30-second nudge-guard prevents the nudge loop from racing the compaction. That prevents a mid-compact crash from losing the session. It does **not** prevent semantic drift across a compaction.

This feature closes the remaining gap at the **prompt level**: dev sessions now externalize their working state in two mutually-reinforcing channels:

1. **Frequent git commits** — every meaningful unit of work is committed, not only at failure boundaries.
2. **A top-level `PROGRESS.md` file** in the worktree — a plain-text scratchpad tracking Done / In-progress / Left, re-read on session start and updated at checkpoints.

## The PROGRESS.md Convention

`PROGRESS.md` is a **working-memory scratchpad only** — not ground truth. It lives at the worktree root and is gitignored (never committed). It is intentionally ephemeral: if the worktree is recreated, the scratchpad goes with it, and the agent creates a fresh one on the next session start.

### Example PROGRESS.md skeleton

```markdown
## Done
- [x] Add PROGRESS.md to .gitignore
- [x] Edit builder.md — Working-state externalization section

## In progress
- [ ] Write integration test (test_long_task_checkpointing.py)

## Left
- [ ] Final validation — run pytest, ruff check, verify git check-ignore
```

### Three-section structure

| Section | Content |
|---------|---------|
| `## Done` | Completed tasks — moved here from In-progress when a commit lands |
| `## In progress` | Single active task — what the agent is working on right now |
| `## Left` | Remaining tasks from the plan — consumed top-to-bottom |

## Source of Truth Hierarchy

| What you want to know | Authoritative source |
|----------------------|----------------------|
| What is in scope? | The plan doc (`.worktrees/{slug}/docs/plans/…`) |
| What progress has been made? | `git log --oneline main..HEAD` |
| What am I working on right now? | `PROGRESS.md` (if present and fresh) |

`PROGRESS.md` supplements but never replaces the plan doc and git log. If they conflict, trust the plan doc and git log.

## How It Works

### Dev session start

1. Agent reads the plan, identifies the task list.
2. Checks for `PROGRESS.md` at the worktree root. If absent, creates it with the three-section structure — `## Done` empty, `## In progress` set to the first task, `## Left` populated with remaining tasks.
3. `PROGRESS.md` is gitignored — it will not appear in `git status` output and cannot be accidentally committed.

### During work

After each meaningful unit of work (completed task, passing test, validated sub-step):
- Agent commits **code only** to the session branch.
- Agent updates `PROGRESS.md` in the same turn (moves the completed item to `## Done`, advances `## In progress`).
- The `git add -A` in the commit silently omits `PROGRESS.md` — this is intentional and expected.

`[WIP]` commit prefix is encouraged for partial steps. Frequent small commits are preferred over large batched ones.

### After context compaction

1. Compaction fires: the PreCompact hook writes the JSONL backup (compaction-hardening feature).
2. Compacted summary replaces conversation history — this may be lossy.
3. Agent's next turn runs:
   ```bash
   cat PROGRESS.md 2>/dev/null || echo "PROGRESS.md absent"
   git log --oneline main..HEAD
   ```
4. Agent re-orients from the scratchpad + git log, not the compacted summary.
5. If `PROGRESS.md` is absent (e.g., worktree recreated), agent falls back to the plan doc and `git log` — both are directly accessible and authoritative.

## Soft-Check in /do-build

After the Definition of Done check (Step 5) and before the Documentation Gate (Step 6), `/do-build` runs a soft check:

```bash
[ -f $TARGET_REPO/.worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at worktree root — not blocking, but recovery from compaction may be degraded next run."
```

This check is **warn-only** — it never blocks PR creation. It is instrumentation for adoption monitoring. If sessions consistently complete without a PROGRESS.md, the prompt guidance can be tightened in a follow-up.

## See Also

- [Compaction Hardening](compaction-hardening.md) — crash-safe compaction via JSONL backup, cooldown, and nudge guard. This feature is the complementary prompt-level layer.
- `.claude/agents/builder.md` — the `## Working-state externalization` section contains the imperative prompt instructions for builder sub-agents.
- `~/Desktop/Valor/personas/developer.md` — the developer persona overlay contains consistent working-state externalization guidance for top-level dev sessions.
- Issue #1139 (backlog) — PostCompact hook auto-injecting "re-read PROGRESS.md" at the exact moment compaction fires. Tracked separately; this feature is prompt-instruction-only.
