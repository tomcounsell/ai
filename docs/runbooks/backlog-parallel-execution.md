# Runbook: parallel backlog execution on a high-capacity host

**Audience:** an agent session on a fleet machine with more RAM and cores than
`Valor the Cowboy`, taking over systematic bugfixing of the `ai` repo backlog.

**Status of this document:** it carries a snapshot of the backlog taken
**2026-08-06**. Treat every issue number, classification and count below as a
*claim to re-verify*, not as ground truth. Step 0 exists because this snapshot
will be stale by the time you read it.

**How to use this:** read it top to bottom once before acting. Execute Step 0
through Step 3 in order. The waves in Step 4 are ordered by dependency, not by
severity, and the ordering is load-bearing — see the rationale under each.

---

## 1. Hard safety rails

These are not style preferences. Each one has an incident behind it.

### 1.1 Do NOT start the bridge or worker on this machine

Every bridge-contact identifier in `~/Desktop/Valor/projects.json` is owned by
exactly **one** machine (`projects.<key>.machine`). If this host starts the
Telegram bridge or the email bridge for a project it does not own, two machines
answer the same inbound message. Enforced by
`bridge/config_validation.py::validate_projects_config`, but only at config
level — it cannot stop you running the process.

**This is a build-only lane.** Do not run `./scripts/valor-service.sh start`,
`worker-start`, or `email-start`. If a step in some skill wants to restart
services, skip it and note the skip in your report. `/update` on this machine is
fine and expected after merges; it self-heals plists without taking ownership.

### 1.2 Never work an issue the other machine is working

`models/session_lifecycle.py:933` (`_lock_owner_is_live`):

```python
if hostname != socket.gethostname():
    return True
```

A SDLC issue lock acquired on **any other host** is unconditionally reported as
live here, and vice versa. The comment is explicit: *"a foreign-host pid cannot
be checked locally — fails TOWARD True (assume live); the lock TTL is the
ultimate backstop."*

Consequence for this handover: if both machines drive SDLC on the same issue,
neither can reclaim the other's stale lock early, and the run strands for the
full TTL. This is issue **#2537** viewed from a different trigger.

**Rule:** claim issues explicitly and exclusively. Before starting a lane, post a
comment on the issue naming this host. Before starting *any* wave, confirm with
the operator which issues the origin machine still holds. Do not infer
availability from lock state, because lock state cannot tell you.

### 1.3 Redis discipline

- Never use raw Redis on Popoto-managed keys, for reads (`hgetall`, `hget`,
  `scan_iter`) or writes (`delete`, `srem`, `sadd`, `zrem`). Go through the ORM
  (`Model.query.filter()`, `instance.save()`, `instance.delete()`). Enforced by
  `.claude/hooks/validators/validate_no_raw_redis_delete.py`.
- Any AgentSession you create for testing gets a `test-` or `dbg-` `project_key`
  prefix, and you delete it afterward via the ORM scoped by that prefix.
- Never run a bulk Redis operation unscoped.

### 1.4 Testing

- Use `scripts/pytest-clean.sh`, never bare `pytest`. The wrapper reaps xdist
  workers and takes the suite lock. Interrupted bare runs leave orphan workers
  eating memory.
- Never dismiss a failure as "pre-existing" without reproducing it on `main`
  yourself, in a clean checkout. Record the evidence in the PR body.

### 1.5 Landing work

- Plans and `.md` docs commit directly to `main`. Code goes on the branch.
- Run `/update` after **every** merge. Merging only moves the git ref.
- Never leave work uncommitted at the end of a task.

---

## 2. Step 0 — verify state before trusting anything below

Run these and reconcile against the snapshot in Step 4. Do not skip.

```bash
gh pr view 2538 --json state,mergedAt        # Wave 0 gate: merged?
gh issue list --label bug --state open --limit 100 --json number,title
git log --oneline -30 main                   # what landed since 2026-08-06
git worktree list                            # existing lanes, see 2.1
```

Specifically check:

1. **Is PR #2538 merged?** Waves 1+ assume it is. If it is still open, stop and
   ask the operator rather than working around it.
2. **Which of the snapshot's issues are now closed?** Anything closed since
   2026-08-06 drops out of its wave.
3. **Do lanes already exist?** As of the snapshot, `.worktrees/` already
   contained `durability-m1-fence-phase-b` and `fix-test-isolation-cluster`,
   which correspond to Wave 0 Task 13 and Wave 2 respectively. **Do not spawn a
   duplicate lane for work that already has a worktree.** Inspect the branch and
   its commits first.

### 2.1 Housekeeping before adding lanes

The snapshot found **96 registered worktrees**, most of them dead (`sdlc-1249`,
`sdlc-1413`, `sdlc-1469`, `sdlc-1915/1916/1920/1997`, `pr-2008-review`, and a
long tail of `dev-*` / `agent-*` orphans).

```bash
python -m tools.disk_reclaim                        # dry-run by default
DISK_RECLAIM_APPLY=true python -m tools.disk_reclaim --apply
scripts/reap-xdist.sh                               # same dry-run/--apply convention
```

The reclaim sweep refuses on its own to touch a lane with uncommitted work, a
live session, a live process, an open PR, or an unmerged branch, and it skips
everything if it cannot reach `gh` to ask. It also honors a 14-day floor, so a
lane created this week is never a candidate no matter how idle it looks.

Read the dry-run output anyway. The guards decide what is *safe* to remove; only
you know what is *wanted*.

---

## 3. Step 1 — calibrate parallelism to *this* machine

Do not copy the origin machine's numbers. Measure:

```bash
sysctl -n hw.memsize | awk '{print $1/1073741824" GB"}'
sysctl -n hw.ncpu
```

### 3.1 The binding constraint is the suite lock, not merge conflicts

`scripts/pytest-clean.sh` no longer takes a machine-global lock — it was deleted
(#2535 Problem 1: it judged full-suite-ness from the pytest args, so any run
naming a path below `tests/` skipped it, and the serialization it advertised did
not exist for most runs).

So worktrees parallelize **building**, and verification is no longer serialized
by a lock. What still bites is real resource contention: concurrent `-n auto`
runs oversubscribe cores, and concurrent runs share Redis state unless keys are
namespaced. Prefer focused runs per lane and reserve the full suite for the
final pre-merge gate.

Two properties you must respect:

- Default pytest config is `-n auto`, i.e. one xdist worker per core. One
  full-suite run already saturates the machine.
- A waiter that exceeds `PYTEST_SUITE_LOCK_TIMEOUT` (default 1800s) **proceeds
  unlocked** rather than deadlocking. Queue too many lanes into the suite at once
  and the overflow runs unlocked and starves everything. The lock exists because
  this produced a load average of 79-82 on a 10-core machine during PR #1956
  (issue #1967).

### 3.2 Sizing rule

Let `C` = cores, `M` = GB RAM.

- **Concurrent full-suite runs: 1.** Always. More cores does not change this;
  `-n auto` already claims them all.
- **Concurrent build lanes:** `min(C / 4, M / 4, 6)`. Each lane carries a
  `claude -p` subprocess plus a checkout. Cap at 6 regardless of hardware —
  beyond that, review and merge-conflict resolution becomes the bottleneck and
  you lose more to rework than you gain.
- Prefer **focused** test runs (`tests/unit/...`, a node id, `-n0`) inside lanes.
  Narrow runs finish fast and contend for less. Reserve the full suite for the
  final pre-merge gate.

Note that serial pytest runs leak 100-200 MB each when the parent shell dies.
Budget headroom; do not size to the ceiling.

---

## 4. Step 2 — the waves

### Snapshot: audit findings, 2026-08-06

All 29 then-open `bug`-labeled issues were audited against source. Result: **1
closed** (#2479, obsoleted by deletion in hotfix `9fe58f45d`), **27 valid**, **1
uncertain** (#2475, not settleable by code reading — needs live log analysis).
**Zero were invalidated by the durability work.**

Two corrections that came out of that audit and are already applied:

- **#2497** was retitled and its body given a retraction header. Its original
  headline claim (that the relay guards only a missing `chat_id`, not the
  placeholder `"0"`) was **false when filed** — the zero-guard landed in
  `83f301f9c` six weeks earlier at `bridge/telegram_relay.py:385-390`. The real
  defect is upstream-only. **Do not "fix" the relay for #2497.**
- **#2540** was filed for the process gap the audit exposed: hotfixes landing
  directly on `main` never close the issues they resolve, because the PR path
  enforces a `Closes #N` trailer and the hotfix path has no equivalent gate.

### Wave 0 — close the durability M1 loop

**Gate. Nothing else starts until this is done.** May already be complete; verify
in Step 0.

| Item | Note |
|---|---|
| #2518 Task 13 | **Urgent.** `_tier2_reprieve_signal` currently ships log-only behind `# PHASE A — DELETE IN PHASE B`. Because `pid is not None` has been permanently true since #2494 stopped clearing the fence, dead sessions are reprieved indefinitely in production *right now*. Requires human review of the Phase A shadow log before enforcing. Lane may already exist at `.worktrees/durability-m1-fence-phase-b`. |
| #2518 Jobs 2+5 | Live canary: multi-turn steered session (fence persistence + steering drain), and short SDLC job lifecycle. **Deploy-and-observe. Origin machine or operator, not this one** — this host does not own the bridge. |
| #2518 Task 14 | Fleet rollout via `/update`. Operator-gated. |
| #2518 Task 15 | E2E fence-stamping integration test driving a real runner turn. Current coverage is unit-level against `FakeSession`. Parallelizable. |
| #2524 | Generalize the migration zero-record guard and guarded index repair to `migrate_strip_pty_fields.py:161` and `migrate_schema_diet_fields.py:230`, both still calling unguarded `rebuild_indexes()`. |
| #2536 | Phantom index metadata causing `rebuild_indexes()` to fail with `unpack(b) received extra data`. **Investigate, do not blind-purge.** PR #2538 only routes around this via `clean_indexes()`; the latent corruption is untouched. |

Parallel: #2524 + #2536 + #2518 Task 15 are disjoint. Three lanes.

### Wave 1 — triage the loss cluster (do this before fixing any of it)

**Highest parallel yield in the entire backlog, and it is nearly free:** pure
read-and-decide, no file writes, no worktrees, no test runs, no lock contention.

For each issue below, rule explicitly **dissolved by #2494 M2 (Room/Job)** vs
**independently real**, write the ruling into the issue as a comment with
evidence, and only then schedule the independents for fixing.

#2495, #2496, #2497, #2458, #2477, #2473, #2490, #2423, #2421, #2420

Precedent: #2494 already does this for #2420 ("stays independently closeable").
Do the same for the rest.

**Why first:** several of these are exactly what #2494 M2 exists to eliminate.
Fixing them individually risks doing the work twice. This wave tells you whether
the cluster is ten issues or three.

Run all ten concurrently. They need no worktree.

### Wave 2 — repair the measuring instrument

**One lane. Sequential. Do not fan this out.**

#2429, #2430, #2462, #2469, #2488, #2532

Plus one issue that does not exist yet and should be filed first: PR #2538
verified that `tests/unit/test_reap_killlist.py` fails only when run alongside
`test_session_lifecycle.py` / `test_recovery_ownership.py`, passes alone, and
**fails identically on `origin/main`**. That finding currently lives only in a PR
body. File it via `/do-issue`.

**Why one lane:** these all converge on `tests/conftest.py` and shared Redis
fixtures; they are diagnostically entangled (#2532 explicitly corrects #2488;
#2429/#2430/#2462 all re-list the same watchdog node that `9fe58f45d` already
killed, and none can close on it); and every one needs full-suite runs to verify,
so parallel lanes would spend their lives queued on the same lock. Fanning this
out maximizes both contention and merge pain.

**Why early:** while these are open, every other fix lands with ambiguous test
signal, and you are forced to re-litigate "is this pre-existing?" by hand each
time.

**Deliverable beyond the fixes:** these clusters are multi-root-cause bundles,
which is why they never close. Split them by root cause as you go. An issue that
can only ever be half-fixed accumulates stale content indefinitely.

### Wave 3 — the silent-no-op defect class

**Sequential, then parallel.** These are not four bugs; they are one bug with
four instances: *a detector that emits silence, and silence is indistinguishable
from health* (#2494's phrasing).

| Issue | Instance |
|---|---|
| #2499 | `tools/impact_finder_core.py:160-181` — blast-radius step degrades to zero results and exits 0. The SDLC blast-radius check is a no-op. |
| #2527 | `scripts/update/hardlinks.py:778` — non-blocking hooks get `\|\| true` appended, swallowing the deny path. |
| #2540 | No sweep connects hotfix commits to the issues they resolve. |

**Sequence:** one lane first generalizes the reusable primitive, then 2-3 lanes
apply it. Three lanes inventing three variants of the same check *is* the
collision, even though the files are disjoint.

The primitive already exists and is proven. PR #2538 shipped
`tools/check_fence_census.py`: a **per-site, function-scoped adjacency check**
that reports `file:line` failures, with a **red-state proof fixture**
(`tests/fixtures/fence_census_violator/`) demonstrating it can actually fail.
The design rationale is the part to carry forward: a *threshold count* goes green
on a change that adds one unguarded site while removing one guarded site, which
is precisely the #2516 failure mode. Whatever you build must fail loudly, and you
must prove it fails.

### Wave 4 — remaining tooling

Disjoint files, mostly focused tests rather than full-suite. Three at a time.

#2537 (`models/session_lifecycle.py:933`, and see rail 1.2 — this one directly
affects cross-machine work, consider promoting it), #2521 (prune orphaned
pre-manifest hook files under `~/.claude/hooks/`), #2523 (missing `SKILL.md` in
`.claude/skills-global/do-skills-audit/`), #2422 (merge-guard cross-repo blind
spot).

### Wave 5 — #2494 M2

Room / Job / AgentRun models. Then #2498 (granite as Job router).

**Do not start this while Waves 1-3 are open.** Wave 1's rulings define M2's
scope, and Wave 2 is what lets you believe M2's test results.

---

## 5. Per-lane protocol

Every lane follows the same path. Do not shortcut it.

1. **Claim the issue** — comment on it naming this host. See rail 1.2.
2. **`/do-plan {slug}`** — this is what creates the durable slug-scoped task list
   and the worktree at `.worktrees/{slug}/` on branch `session/{slug}`.
   **Creating an AgentSession directly without a prior `/do-plan` skips tier-2
   worktree isolation and contaminates the main checkout (#887).**
3. **`/do-plan-critique`** — for anything structural. Skip for one-line fixes.
4. **`/do-build`** — the Definition of Done lives in
   `.claude/skills-global/do-build/SKILL.md` and is enforced there.
5. **`/do-test`** — focused runs inside the lane; the full suite only as the
   final gate, and only one lane at a time holds it.
6. **`/do-pr-review`**, then **`/do-docs`**, then **`/do-merge`**.
7. **`/update`** immediately after the merge.

Between stages, invoke `/sdlc` as a single-stage router: assess state, dispatch
ONE sub-skill, return. Never write code, run tests, or create plans directly from
the router.

---

## 6. Stop conditions

Stop and escalate to the operator rather than working around any of these:

- PR #2538 is not merged and a wave depends on it.
- An issue you were about to claim shows recent activity from the origin machine.
- The suite lock has been held for longer than its timeout by a process you did
  not start.
- A migration wants to run against live Redis. Every migration in this backlog is
  dry-run-first by design; an `--apply` against production Redis is an operator
  decision.
- Anything that would restart a service, take bridge ownership, or touch
  `projects.json`.
- A test failure you cannot reproduce on `main`. Do not label it flaky and move
  on — that is the exact habit Wave 2 exists to correct.

---

## 7. Reporting back

Per merged lane, report: issue number, PR number, what changed, test evidence
(command run and result, not a summary), and anything you deliberately left out
of scope.

Per wave, report: what closed, what remains open and why, and any issue whose
stated diagnosis you found to be wrong. That last category is the highest-value
output of this whole exercise — the 2026-08-06 audit found one issue (#2497)
whose headline claim was false at filing time, and nothing but a fresh read
against current source will ever surface that class.

If you find that a wave's premise is wrong, say so and stop. The ordering here is
a claim about dependencies, and claims can be wrong.
