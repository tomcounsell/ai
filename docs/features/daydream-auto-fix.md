# Daydream Auto-Fix

Daydream Auto-Fix is step 8 of the [Daydream maintenance loop](daydream-reactivation.md) that automatically opens PRs to fix high-confidence code bugs discovered during nightly reflection.

## What It Does

After the reflection step (step 7) categorizes findings, auto-fix examines each `code_bug` reflection and — when confidence is high enough — spawns `/do-plan` and `/do-build` to produce a fix PR. It never pushes directly to `main`. Human review and merge are always required.

## Flow

```
Step 7: Reflect
  └─ Step 8: Auto-Fix
       ├─ Confidence check (2-of-3 criteria)
       ├─ Ignore log check (data/daydream_ignore.jsonl)
       ├─ Dedup check (open PR already exists for this pattern?)
       ├─ Subprocess: /do-plan + /do-build (10-minute timeout)
       ├─ PR opened on feature branch
       └─ Telegram notification with PR link
```

## Confidence Criteria

Auto-fix triggers when a reflection meets **2 of 3** criteria:

| Criterion | Condition |
|-----------|----------|
| Category | `category == "code_bug"` |
| Prevention | `prevention` field is non-empty |
| Pattern length | `pattern` field is at least 10 characters |

If fewer than 2 criteria are met, the issue is logged but no action is taken.

## Ignore Log

The ignore log (`data/daydream_ignore.jsonl`) suppresses auto-fix for specific patterns for 14 days. Use this when a pattern keeps triggering but you are not ready to fix it yet.

### Adding an entry

```bash
# Suppress a pattern for 14 days
python scripts/daydream.py --ignore "pattern text here"

# With an optional reason
python scripts/daydream.py --ignore "pattern text here" --reason "Intentional design, not a bug"
```

### TTL and pruning

- Each entry expires after **14 days** from when it was added
- Expired entries are automatically pruned at the start of each daydream run
- The file is plain JSONL and can be edited by hand

### File format

```jsonl
{"pattern": "...", "reason": "...", "expires_at": "2026-03-05T00:00:00"}
```

## Dry-Run Mode

Dry-run mode runs the full loop — including confidence checks and ignore log lookups — but skips all side effects.

```bash
python scripts/daydream.py --dry-run
```

In dry-run mode: no subprocess is spawned, no PRs are opened, no Telegram notifications are sent. All decisions are logged to stdout.

**Use dry-run when testing changes to daydream itself**, such as after modifying step logic, confidence criteria, or the auto-fix flow.

## Configuration

### DAYDREAM_AUTO_FIX_ENABLED

Set to `false` to disable auto-fix globally without touching code.

```bash
# In .env
DAYDREAM_AUTO_FIX_ENABLED=false
```

Default is `true`. When disabled, reflection still runs and findings are still recorded — only the subprocess and PR creation are skipped.

## Subprocess Timeout

Each `/do-plan` + `/do-build` subprocess has a **10-minute timeout**. If it does not complete, the process is terminated, the failure is logged, and daydream continues.

## Safety Properties

- **PRs only** — Never pushes to `main`. Every fix requires human review.
- **Dedup** — If an open PR already exists for the pattern, no duplicate is created.
- **Ignore log** — Patterns can be silenced for 14 days with one CLI command.
- **Dry-run** — All logic is testable without external side effects.
- **Kill switch** — `DAYDREAM_AUTO_FIX_ENABLED=false` disables the feature entirely.

## Related

- [Daydream Reactivation](daydream-reactivation.md) — Full daydream loop overview
- [Daydream Multi-Repo](daydream-multi-repo.md) — Multi-repo daydream support
