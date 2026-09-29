# Merge Troubleshooting Playbook

When the merge gate (`/do-merge`) fails on a PR that is otherwise approved,
mergeable, and green, the PM session can self-resolve the blocker using the
recipes below. The G4 oscillation guard (`.claude/skills-global/do-sdlc/SKILL.md`) caps same-category
retries at 3; anything beyond that escalates to a human.

See also:
- `.claude/skills-global/do-merge/SKILL.md` and `docs/sdlc/do-merge.md` — the gate and its shared predicate (`python -m tools.merge_predicate`).
- `config/personas/engineer.md` → **Gate-Recovery Behavior** — the
  engineer persona's dispatch table mapping blockers to remediations.
- `docs/features/self-healing-merge-gate.md` — feature-level overview.

---

## Merge Conflict

**Symptom.** `/do-merge` reports `mergeable: CONFLICTING` from the
`gh pr view` check. The PR's branch cannot fast-forward onto the base.

**Diagnose.**

```bash
gh pr view {pr} --json mergeable,mergeStateStatus
git -C .worktrees/{slug} fetch origin main
git -C .worktrees/{slug} log --oneline ^origin/main..HEAD
```

**Remediate.** Rebase the session branch onto origin/main and re-push:

```bash
git -C .worktrees/{slug} fetch origin main
git -C .worktrees/{slug} rebase origin/main
git -C .worktrees/{slug} push --force-with-lease
```

**Verify.**

```bash
gh pr view {pr} --json mergeable -q .mergeable
# Expected: MERGEABLE
```

Then re-dispatch `/do-merge {pr}`.

---

## G4 Oscillation (Same Skill Dispatched 3x)

**Symptom.** The SDLC router's G4 guard refuses to dispatch the same skill
a fourth time without a state change (`.claude/skills-global/do-sdlc/SKILL.md`). The
PM is looping on the same remediation without making progress.

**Diagnose.** Look at the last three dispatches for this issue:

```bash
python -m tools.sdlc_stage_query --session-id "$AGENT_SESSION_ID"
```

**Remediate.** Do NOT re-dispatch the same skill. Escalate to the human
with the specific blocker output. G4 is load-bearing — bypassing it
produces infinite loops that drain compute without finishing work.

**Verify.** N/A — this is the escalation path.

---

## Stale Review (Approved/Changes-Requested Predates Latest Commit)

**Symptom.** The merge predicate's `failed_checks` reports
`REVIEW verdict predates PR head commit`: commits other than docs-only changes
landed after the recorded APPROVED verdict.

**Remediate.** Dispatch a fresh `/do-pr-review {pr}` on the current head. Do not
re-run `sdlc-tool verdict finalize`; that re-records the old judgment.

**Verify.** Re-run `python -m tools.merge_predicate --pr-number {pr} --run-id {run_id} --json`
and confirm the REVIEW leg is gone from `failed_checks`, then re-dispatch
`/do-merge {pr}`.

---

## Lockfile Drift

**Symptom.** The Lockfile Sync Check reports
`LOCKFILE: FAIL -- uv.lock is out of sync with pyproject.toml`.

**Diagnose.**

```bash
uv lock --locked
# Exits non-zero with a diff summary when drift exists.
```

**Remediate.** Regenerate the lockfile on the session branch and commit:

```bash
uv --directory .worktrees/{slug} lock
git -C .worktrees/{slug} add uv.lock
git -C .worktrees/{slug} commit -m "Sync uv.lock"
git -C .worktrees/{slug} push
```

**Verify.**

```bash
uv --directory .worktrees/{slug} lock --locked && echo "LOCKFILE OK"
```

Re-dispatch `/do-merge {pr}`.

---

## Partial Pipeline State

**Symptom.** The predicate fails a stage leg (`REVIEW stage marker not
completed`, a DOCS marker `in_progress`) even though the work visibly happened,
typically after Redis state was lost mid-session.

**Diagnose.**

```bash
python -m tools.sdlc_stage_query --issue-number {N}
gh pr view {pr} --json reviews                 # REVIEW artifact present?
gh pr diff {pr} --name-only | grep ^docs/      # DOCS diff present?
```

**Remediate.** A REVIEW marker that never completed is repaired by re-running
`sdlc-tool verdict finalize` (only if the recorded verdict is still fresh); a
stage whose artifacts genuinely do not exist needs its skill dispatched
(`/do-docs` for DOCS, `/do-pr-review` for REVIEW).

**Verify.** Re-run the predicate and confirm the leg cleared, then re-dispatch
`/do-merge {pr}`.

---

## Worktree Cleanup Blocked

**Symptom.** `python scripts/post_merge_cleanup.py {slug}` exits **2** and
prints `worktree busy: in use by session_id=<id>` to stderr. The local
`session/{slug}` branch is still present because `gh pr merge --delete-branch`
cannot delete a branch referenced by an active worktree.

**Diagnose.** The exit code is the signal — exit 2 means the busy guard
fired (distinct from exit 1 generic errors). Inspect the offending session:

```bash
python -m tools.valor_session status --id <session_id>
```

**Remediate.**

1. If the session is genuinely live and still doing useful work, wait for it to
   finish, then re-run `post_merge_cleanup.py {slug}`.
2. If the session is wedged or dead but its row hasn't flipped yet:

   ```bash
   python -m tools.valor_session kill --id <session_id>
   python scripts/post_merge_cleanup.py {slug}
   ```

3. If the cleanup must proceed despite a live session, override programmatically
   by passing `force=True` to `remove_worktree()` (no CLI flag — this is
   deliberate friction). The WARNING log
   `force-removing worktree .worktrees/{slug} despite live session_id=...` is
   grep-able for audit. **Do not make `--force` your reflex.**

**Verify.**

```bash
python scripts/post_merge_cleanup.py {slug}
echo "Exit: $?"  # 0 == clean; 2 == still blocked
```

See [`docs/sdlc/do-merge.md#busy-guard`](do-merge.md#busy-guard) for the operator workflow and
[`docs/features/session-isolation.md#worktree-busy-guard-issue-1357`](../features/session-isolation.md#worktree-busy-guard-issue-1357) for the runtime invariant.

---

## Quick Reference

| Blocker category | Remediation | Command |
|------------------|-------------|---------|
| PIPELINE_STATE | Re-dispatch `/do-merge` (trusts durable fallback) | `/do-merge {pr}` |
| PARTIAL_PIPELINE_STATE | Same as PIPELINE_STATE | `/do-merge {pr}` |
| REVIEW_COMMENT | Dispatch `/do-pr-review` on session branch | See Stale Review |
| LOCKFILE | `uv lock && git add uv.lock && commit && push` | See Lockfile Drift |
| MERGE_CONFLICT | Rebase onto `origin/main` | See Merge Conflict |
| BUSY_GUARD (`post_merge_cleanup` exit 2) | Kill wedged session, re-run cleanup | See Worktree Cleanup Blocked |

After any remediation, re-dispatch `/do-merge {pr}`. If the same blocker
category recurs 3 times, escalate to the human per the G4 convergence
rule — do not loop further.

