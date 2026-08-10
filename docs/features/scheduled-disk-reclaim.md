# Scheduled Disk Reclaim

Three categories of on-disk state grew without bound because their teardown code
had no scheduled caller. `tools/disk_reclaim.py` is that caller. It reports by
default and deletes only when explicitly armed.

Issue #2517.

## What it sweeps

| Path | Teardown it calls | Default window |
|---|---|---|
| `.worktrees/{slug}/` | `cleanup_after_merge()` (`agent/worktree_manager.py`) | 14 days idle |
| `~/.claude/projects/*/<uuid>.jsonl` and `*/<uuid>/` | `unlink` / `shutil.rmtree` | 30 days idle |
| `logs/sessions/*` | `cleanup_old_snapshots()` (`agent/session_logs.py`) | 168 hours |

The 30-day transcript window is chosen so it sits clear of the 14-day worktree
window: a lane's transcript outlives the lane, so a merged lane can still be
read back after its directory is gone. Transcripts are what back `claude
--resume`, and past a month the surviving record of that work is the PR.

### The transcript sweep never removes a project directory

A `~/.claude/projects/<project>/` directory is a container, not garbage. It
holds `memory/` — the durable per-project memory store, a `MEMORY.md` index plus
one file per memory — alongside `.timelines/`, `sessions-index.json`, and the
disposable transcripts.

So the sweep works at **file granularity inside** each project directory:

| Entry | Disposition |
|---|---|
| `<uuid>.jsonl` | candidate, aged on its own mtime |
| `<uuid>/` | candidate, aged on its newest contained mtime |
| `memory/` | **never touched, at any age** |
| everything else (`.timelines/`, `sessions-index.json`, symlinks, unknown names) | preserved |

The classifier is an allow-list of the `<uuid>[.jsonl]` shape, not a deny-list
of known-durable names, so a file Claude Code starts writing in a future release
is preserved by default rather than reaped by default.

Judging entries individually is what makes the 30-day window mean anything.
Aging a whole project directory couples two unrelated lifetimes — curated
permanent memory and disposable transcripts — and the directory's recency is
driven almost entirely by transcript writes, so either a live project's
month-old transcripts are kept forever or a quiet project's memory is deleted
along with them. The second is what actually happens: measured on this machine,
one project directory read 22.2 days idle while its memory subtree read 30.6
days idle, so a directory-level check selects precisely the projects whose
memory is oldest and most curated. `CLAUDE_PROJECTS_DIR` is global and
independent of `--repo-root`, so that blast radius crosses repositories.

`removed` names are reported as `<project>/<entry>`; each project also reports a
one-line skip reason counting what it kept (`too_young:N, preserved:N`).

## Arming

Dry-run is the default and is the only mode reachable without operator intent.

```bash
python -m tools.disk_reclaim                                  # report only
DISK_RECLAIM_APPLY=true python -m tools.disk_reclaim --apply  # remove
```

`--repo-root` scopes the **worktree sweep only** — it selects the checkout whose
`.worktrees/` lanes are considered and whose open PRs `gh` is asked about (the
query runs with `cwd=repo_root`, so it can never answer about a different
repository). The transcript sweep always reads `~/.claude/projects/`, and the
snapshot sweep always reads the module-relative `SESSION_LOGS_DIR` of the
`agent` package that was imported.

`--apply` alone is refused with exit 2. Both the flag and the environment
variable are required, so a destructive sweep cannot happen from shell history
or a stray `params:` edit. This mirrors `memory-decay-prune`'s tier-1 gate:
`DISK_RECLAIM_APPLY` is read inside `tools/disk_reclaim.py` and is deliberately
not exposed as a reflection parameter.

## Guards

Every guard fails **closed**. A check that cannot answer skips the candidate.
Keeping a stale worktree one more day costs a directory; guessing wrong costs
someone's unpushed work.

A worktree lane is removed only when all of these hold:

| Guard | Skip reason when it trips |
|---|---|
| Newest mtime older than the age floor | `too_young` |
| `git status --porcelain` is empty | `uncommitted_changes` |
| ...and git could answer at all | `git_status_unavailable` |
| No live OS process with cwd inside it | `live_process:<pid>` |
| No non-terminal `AgentSession` claims it | `live_session:<id>` |
| ...and the ORM could answer at all | `busy_check_error:<reason>` |
| Its branch has no open PR | `open_pr` |
| ...and `gh` could answer at all | `pr_state_unavailable` (skips *every* lane) |
| Its branch has landed on main | `unmerged` |

Removal then delegates to `cleanup_after_merge`, which re-checks the busy guard
(#1357), preserves uncommitted changes (#2137), refuses to delete an unmerged
branch (#1646), and enforces path containment (#880). `force=True` is never
passed.

### The busy-check posture, and why there are two functions

`worktree_busy_check()` is **fail-open**: an unreachable Redis reads the same as
"no session is using this lane". That is correct for interactive and post-merge
removal, where a human is present to notice, and refusing every removal on a
Redis hiccup would cause more pain than the guard prevents.

It is wrong for an unattended reaper, which would delete every lane during an
outage. `worktree_busy_probe()` returns `clear` / `busy` / `error` so the sweep
can skip on `error`. Both wrap one scan; only the failure posture differs.

## Registering the reflection

`config/reflections.yaml` is gitignored and vault-synced (`~/Desktop/Valor/`),
so it is per-machine state that a PR cannot change. Add the entry by hand on
each host:

```yaml
  - name: disk-reclaim
    description: "Age out merged worktree lanes, old Claude transcripts, and session snapshots"
    every: 86400s # daily
    priority: low
    execution_type: function
    callable: "reflections.housekeeping.disk_reclaim.run"
    enabled: true
```

Registering it is safe on its own: the reflection reports and deletes nothing
until `DISK_RECLAIM_APPLY=true` is in the host environment. Read a few days of
dry-run findings in `logs/reflections.log` before arming.

## What this replaces

`scripts/worktree-gc.sh` is deleted. It selected candidates on PR state alone
and then ran `git worktree remove --force` plus an unguarded `git branch -D`,
with no check for uncommitted changes, live sessions, live processes, branch
merged-ness, or age. A failed `gh` call collapsed to an empty string, so an auth
blip made *every* worktree a prune candidate.

A plan critique flagged this in 2026-05 (`docs/plans/completed/dev_session_cleanup_unmerged_branch_guard.md`)
and the remedy was deferred as an out-of-scope follow-up. Meanwhile
`docs/runbooks/backlog-parallel-execution.md` recommended running it during
parallel execution — the one situation where the machine is fullest of live
lanes. On 2026-08-07, with six agents working, its dry-run listed **10 active
worktrees** as prune candidates and reported zero as locked.

## Note on the disk numbers

`du` over `.worktrees/` is an upper bound, not a disk-pressure figure. On
macOS/APFS uv clones packages copy-on-write from its global cache, so a worktree
`.venv` reports full size while sharing blocks. Measured 2026-08-07 across a real
`uv sync`: `du` 541 MB, actual `df` delta **9 MB**.

So the headline "4.4 GB of worktrees" is roughly 90% clone illusion, and the real
per-lane reclaim is the source checkout (~50 MB) plus a few MB of venv. The
reason to reap lanes is inode pressure and `git worktree list` legibility, not
gigabytes. The largest genuine reclaim of the three categories is
`~/.claude/projects/` (~900 MB of real files, no clones), essentially all of it
transcripts — the preserved `memory/` stores are text files measured in KB.

## See also

- `docs/features/worktree-venv-isolation.md` — how lanes get their env, and the measurement
- `docs/features/adding-reflection-tasks.md` — the reflection contract
- `agent/worktree_manager.py` — `cleanup_after_merge`, `worktree_busy_probe`
