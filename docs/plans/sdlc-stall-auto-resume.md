---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2696
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-10T04:15:01Z
---

# SDLC Stall Auto-Resume (steer the eng session, escalate once)

## Problem

`sdlc-progress-check` (`reflections/sdlc_progress.py`) detects a stalled SDLC pipeline every 30
minutes and reacts by sending a Telegram message to a human. It never touches the pipeline. Because
a stalled PR produces no new commits and the dedup key is `(slug, last-commit-sha)` with a 6-hour
TTL, the identical message re-fires every 6 hours forever.

Observed on 2026-08-09/10 in `Eng: Valor`: four copies of the same fact about PR #2688, six hours
apart, none of which moved the PR. The human's verdict: *"Don't send me messages like this. I'm not
your log file."*

Three defects, stacked:

1. **No action.** `_check_project_stalls` composes a string and calls `_send_alert`
   (`reflections/sdlc_progress.py:300-307`). It never steers, resumes, or creates a session.
2. **Escalating repeats.** `_dedup_set` (`:229-242`) writes `sdlc:stall:alert:{slug}:{sha}` with
   `SET NX EX` at `_DEFAULT_COOLDOWN_HOURS = 6` (`:43`). The key expires, the sha has not moved
   because the PR is stalled, the alert re-fires. No cap. No "a human was already told" state.
3. **Gate 5 is structurally blind.** `_has_active_session` (`:188-208`) runs
   `AgentSession.query.filter(slug=slug)`, but `tools/sdlc_session_ensure.py:837-843` creates local
   SDLC anchors via `create_local(...)` with **no** `slug`. Verified live: `sdlc-local-2517`,
   `sdlc-local-2664`, `sdlc-local-2682`, `sdlc-local-2696` all carry `slug=None`.

**Desired outcome.** A stalled pipeline resumes itself. A human hears about it exactly once per
stalled head sha, and only when the system tried to act and could not.

## Freshness Check

Baseline: `main` @ `96b0f65dd`, verified 2026-08-10. Issue filed the same day.

| Claim | Verification | Disposition |
|---|---|---|
| `_send_alert`-only reaction at `:300-307` | Read; confirmed verbatim | Unchanged |
| `_DEFAULT_COOLDOWN_HOURS = 6` at `:43`, dedup at `:229` | Read; confirmed | Unchanged |
| Gate 5 queries by `slug` at `:203` | Read; confirmed | Unchanged |
| `create_local` omits `slug` at `sdlc_session_ensure.py:837-843` | Read; confirmed | Unchanged |
| Three stalled lanes have zero sessions for their slug | Live query: all four `sdlc-local-*` anchors exist with `slug=None`, `status=running` | **Corrected** — the sessions exist, they are just invisible to a slug query, and they are ledger rows |
| Alert target drift (`Eng: Valor` in code, `Dev: Valor` in doc `:276`) | Read both | Unchanged |

`git log --since=2026-08-01 -- reflections/sdlc_progress.py docs/features/pm-session-liveness.md`
returns nothing touching either file. No active plan in `docs/plans/` overlaps this area.

**Disposition: Minor drift.** One issue claim (zero `AgentSession` records for the stalled slugs) is
corrected above and the correction materially changes the design — see Spike Results S2.

## Research

No external research performed. This work is entirely internal: it changes one reflection module,
reuses existing in-repo primitives (`agent.steering`, `tools.valor_session.resume_session`,
`models.session_lifecycle.touch_issue_lock`), and adds no dependency, service, or external API.

## Prior Art

The repo already contains two reflections that act on a stall. Neither needs to be invented here;
both are shape templates.

| Source | What it establishes | Where |
|---|---|---|
| `reflections/crash_recovery.py` (auto-resume reflection) | Machine-ownership gate, per-session attempt cap, per-run budget, and the C1 lesson that a **failed** attempt must still consume budget or the guard never converges | `:99-126`, `:412-503` |
| `reflections/stall_advisory.py` (act-on-stall reflection) | Cross-tick consecutive-observation counter, run budget, per-session budget — all plain Redis keys with TTLs; plus the `_is_ledger` skip | `:251-417`, `:130-139` |
| PR #2106 — "stall-advisory: skip is_ledger sessions (#2105)" | Killing a `sdlc-local-{N}` ledger orphans its issue lock and deadlocks the router | merged 2026-07-15 |
| PR #1411 — "sdlc-1395: stalled-pipeline reflection" | The v1 that built this detector notification-only | merged 2026-05-22 |
| `tools/valor_session.py::resume_session` | Pushes the steering message **before** the `pending` transition, closing the two-write race; requires `claude_session_uuid` | `:786-870` |
| Issue #2620 / `_lock_renewal_is_fresh` | Lease liveness is renewal recency, not pid liveness and never an AgentSession status | `models/session_lifecycle.py:949-979` |

### Why Previous Fixes Failed

v1 (PR #1411) did not fail — it was correctly scoped for its moment and explicitly declared
notification-only. What changed is the surrounding system: the pipeline is now autonomous enough
that "a human notices a Telegram message and hand-restarts the lane" is the least reliable link in
the chain. The failure this plan fixes is a **scope decision that expired**, not a bug in the
implementation.

The one genuine implementation defect (gate 5's slug query) failed for a familiar reason: it
inferred liveness from the existence of an `AgentSession` row. That inference is the same
"liveness mirage" `_lock_owner_is_live` was written to kill (`models/session_lifecycle.py:982-992`).
The fix below does not repair the inference; it replaces it with the lock's own evidence.

## Spike Results

All spikes were resolved by direct code reads and live Redis queries against production state during
recon. No prototypes were required.

### spike-1: Can a `sdlc-local-{N}` anchor be steered?
- **Assumption**: "Steering the stalled lane's own session is a viable strategy."
- **Method**: code-read
- **Result**: **No — it is hard-rejected.** `sdlc_session_ensure.py:810` sets `is_ledger=True`, and
  `agent/session_executor.py::steer_session` refuses ledger rows outright (`:841-849`, issue #2495)
  because no worker ever drains `steering:{session_id}` for them; accepting would be silent loss
  behind a success return.
- **Confidence**: high
- **Impact if false**: n/a — this is a hard code path, not a heuristic.

### spike-2: Is matching `issue_number` a valid gate-5 fix?
- **Assumption**: "Widening `_has_active_session` to match `sdlc-local-{issue}` fixes the blind spot."
- **Method**: live query
- **Result**: **No — it would blind the detector.** All three stalled lanes had a ledger anchor
  sitting at `status=running` for the entire 22-40h stall. Matching on it suppresses the stall
  verdict permanently. This is strictly worse than today's false-negative-free/false-positive-prone
  behavior.
- **Confidence**: high
- **Impact if false**: n/a — verified against the exact reported incident.

### spike-3: Does the issue lock give a trustworthy liveness signal?
- **Assumption**: "`touch_issue_lock(issue, None, peek=True)` distinguishes a live lane from a dead one."
- **Method**: live query
- **Result**: **Yes.** All four live lanes returned
  `IssueLockResult(acquired=False, owner_session_id='sdlc-local-{N}', owner_run_id=..., orphaned_lock=False, target_repo='tomcounsell/ai')`.
  `orphaned_lock` is computed from `renewed_at` recency (`_lock_renewal_is_fresh`,
  `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS`, default 20 min), refreshed by the heartbeat
  (`tools/sdlc_lease_heartbeat.py`) and the worker tick — never from an `AgentSession` status. A
  dead run stops renewing and the lease self-frees within `ISSUE_LOCK_TTL_SECONDS` (30 min).
- **Confidence**: high
- **Impact if false**: the gate degrades to "unknown" and the reflection declines to act — the
  fail-safe posture, not a duplicate lane.

### spike-4: Does the reflection framework already enforce single-machine ownership?
- **Assumption**: "`run_per_project_audit` only yields projects this machine owns."
- **Method**: code-read
- **Result**: **No.** `load_local_projects` (`reflections/utilities.py:43-71`) filters only on
  `working_directory` existing on disk. Two machines with the same checkout would both auto-resume
  the same lane. A `projects.<key>.machine` gate is therefore mandatory, per CLAUDE.md's strict
  single-machine ownership rule.
- **Confidence**: high
- **Impact if false**: n/a.

### spike-5: Is there a steerable eng session to target?
- **Assumption**: "A live or resumable non-ledger eng session exists for the project."
- **Method**: live query
- **Result**: **Partially.** For `project_key=valor` there are 9 eng sessions: 4 are `sdlc-local-*`
  ledger anchors (unsteerable), 5 are Telegram-bound (`tg_valor_*`, `0_*`) with
  `claude_session_uuid` set. At the sampled moment all five were terminal but **resumable**. So the
  primary happy path is `resume_session`, and a live-steer path is the opportunistic fast case.
- **Confidence**: medium — session population varies by time of day.
- **Impact if false**: with no steerable and no resumable target, the ladder falls to
  escalate-once. See Open Question Q1.

## Appetite

**Medium.** One reflection module rewritten in place, one shared helper extracted, one feature doc
section rewritten, one test file substantially extended. No new service, no schema change, no
migration.

## Solution

### Shape

Replace the notification-only tail of `_check_project_stalls` with an **action ladder**, and
re-ground gate 5 on the issue lock instead of on session rows.

```
per open SDLC PR:
  gates 1-4 (branch shape, not draft, issue open, commit age)   [unchanged]
  gate 5': issue lock says a live run owns this issue?          [REPLACED]
      lock held & not orphaned  -> live lane, skip silently
      lock free or orphaned     -> continue
      lock read failed          -> unknown, skip silently (fail-safe)
  gate 6': this machine owns the project?                       [NEW]
      not owner -> skip silently
  action ladder, keyed on (slug, head-sha):
      attempts >= MAX          -> escalate once, stop
      action cooldown live     -> skip this tick
      pick rung:
          rung 1  live non-ledger eng session for project -> steer_session(...)
          rung 2  most recent resumable eng session       -> resume_session(...)
          rung 3  no target                               -> create_session(...)
          rung 4  creation refused/failed                 -> escalate once, stop
      classify the outcome:
          benign race (another actor got there first) -> no attempt, no escalation
          otherwise -> charge an attempt (success OR failure)
      on non-benign failure    -> escalate once
```

### Gate 5' — liveness from the lock, not from session rows

`_has_active_session(slug)` is deleted. Its replacement:

```python
def _lane_is_live(issue_number: int) -> bool | None:
    """True if a live run owns this issue's lock, False if free/orphaned, None if unknown."""
```

implemented over `models.session_lifecycle.touch_issue_lock(issue_number, None, peek=True)`. Passing
`run_id=None` with `peek=True` never mutates (`session_lifecycle.py:1092-1095`, `:1122-1127`), so
the reflection can never accidentally claim or renew a lease.

Mapping:

| Peek result | Meaning | Gate |
|---|---|---|
| `acquired=True` | lock unheld | not live → continue |
| `acquired=False, orphaned_lock=False` | live owner, renewals fresh | **live → skip silently** |
| `acquired=False, orphaned_lock=True` | owner died, TTL not yet lapsed | not live → continue |
| exception | unknown | **None → skip silently** |

This also produces a happy coincidence that the design leans on: by the time the gate lets us
through, the stalled lane's lock is free or self-freeing, so the eng session we steer will not hit
`ISSUE_LOCKED` when it runs `/sdlc`.

A secondary, cheap sanity check is retained, and its tiebreak is stated explicitly because the two
signals can disagree.

**Tiebreak rule: the two signals are OR-ed for "live", never AND-ed.** A lane is live if the lock
peek says live **OR** a non-ledger non-terminal `AgentSession` carries this `issue_number`. The row
check can only ever *add* liveness; it must never turn a `orphaned_lock=True` result into "not
live". That inversion is the permanent-false-negative mode spike-2 already ruled out for ledger
anchors, and it is the single way this secondary check could make the detector worse.

Stated as code, so the builder cannot get the polarity wrong:

```python
if lock_live is None:
    return None            # unknown -> caller skips
if lock_live:
    return True            # lock is authoritative for "live"
return _nonledger_row_live(issue_number) or False   # row can only add liveness
```

The row check costs one indexed query and catches a lane running without a lock (a Redis flap
during acquire). Ledger rows are excluded via `agent.session_health._is_ledger`, exactly as
`stall_advisory.py:130-139` does. If the row query itself raises, it contributes nothing (`False`)
rather than `None` — a failed *secondary* signal must not veto a successful primary one.

### Gate 6' — machine ownership

`_machine_owns_project` currently lives in `reflections/crash_recovery.py:99-126` and resolves
`config/projects.json` directly rather than the path `load_local_projects` uses
(`PROJECTS_CONFIG_PATH` → `~/Desktop/Valor/projects.json` → repo fallback). Extract it to
`reflections/utilities.py::machine_owns_project(project_key)`, fix the path resolution to match
`load_local_projects`, and import it from both reflections. Per CLAUDE.md principle 1 the old
private copy is deleted, not left behind.

Fail-soft posture is preserved: unresolvable or unknown `project_key` → `False` → do not act.

### The steer-target ladder

New helper `_pick_steer_target(project_key)` returns `(kind, session)` where `kind` is
`"steer" | "resume" | "create"`:

1. **Live steer.** Non-terminal, `session_type == "eng"`, not `_is_ledger`, matching `project_key`.
   Most recently `updated_at` wins. → `agent.session_executor.steer_session(session_id, message)`.
2. **Resume.** Status in `RESUMABLE_STATUSES`, `session_type == "eng"`, not `_is_ledger`, matching
   `project_key`, and `claude_session_uuid` present (required by `resume_session`). Most recently
   `updated_at` wins. → `tools.valor_session.resume_session(session, message, source="sdlc-stall")`,
   which pushes the steer before the `pending` transition and so has no two-write race.
3. **Create.** No live and no resumable eng session for the project →
   `tools.valor_session.create_session(...)` (see below). Only if creation is refused or fails does
   the ladder escalate.

### The create rung

Creation is the widest-blast-radius capability in this change: it provisions a worktree, enqueues a
session, and wakes the worker. It gets its own guards and its own telemetry.

**Mechanism — extract a programmatic core, do not shell out.** `resume_session` already exists as a
"programmatic core … shared by `cmd_resume` (CLI) and the auto-resume reflection"
(`tools/valor_session.py:786-790`). Creation has no such core: `cmd_create`
(`tools/valor_session.py:423-645`) interleaves argparse reads, `print` calls, and `sys.exit` codes
with the actual work, which is only two steps — `agent.worktree_manager.get_or_create_worktree` and
`agent.agent_session_queue._push_agent_session`. Extract
`tools.valor_session.create_session(...) -> CreateResult` around exactly those two steps, mirroring
`ResumeResult`'s shape (`success`, `session_id`, `error`), and rewrite `cmd_create` as
argparse-parsing and printing over it. Both reflection rungs then call programmatic cores rather
than one calling a core and the other shelling out to a CLI contract.

The created session is an `eng` session with `slug = "sdlc-{issue}"` — the stalled lane's own slug,
so it lands in the lane's existing worktree and on the lane's existing branch rather than starting a
parallel one. `get_or_create_worktree` is idempotent for an existing worktree, which is the normal
case for a stalled lane.

**Guards, all mandatory:**

| Guard | Why |
|---|---|
| Gate 6' machine ownership must have passed | Two machines must never both create a lane. |
| Re-peek the issue lock immediately before creating | Gate 5' ran earlier in the tick; the lock can be re-acquired in between. A held lock here is a **benign race** — no attempt charged, no escalation. |
| Charged against the same per-`(slug, head-sha)` attempt budget | A create-loop is impossible: three creates for one stalled sha exhausts the budget and escalates once. |
| `SDLC_STALL_CREATE_MAX_PER_TICK` (default 1) | Per-project-tick brake, enforced by a plain local counter in `_check_project_stalls` — see Budgets. |
| Distinct `kind="create"` in logs, `findings`, and the summary counters | The rungs must be tellable apart in telemetry; the create rung is the one to watch. |

The rung is self-limiting across ticks: once a session is created for a project it is non-terminal,
so the next tick's rung 1 finds it and steers it instead of creating another.

### Benign races are not attempts

Stated once here and applied to every rung, rather than repeated per rung.

The action ladder is not the only actor that resumes or creates sessions. `crash_recovery` runs
every 300s and can `resume_session` the same terminal eng session this reflection selects; a lane
can re-acquire its own issue lock at any moment. When another actor gets there first, the loser's
call returns `success=False` — which is **indistinguishable from a genuine dead end** unless the
outcome is classified. Left unclassified, another actor's *legitimate* recovery marches this ladder
toward the human escalation the whole change exists to eliminate.

**Rule: an outcome that is explained by another actor having acted charges no attempt, fires no
escalation, and returns early.** The lane is being handled; there is nothing to report.

Classification is by re-reading state after the failure, mirroring `steer_session`'s
re-read-before-reject pattern (`agent/session_executor.py:827-832`):

| Rung | Failure | Benign iff |
|---|---|---|
| steer | `steer_session` returns failure | re-read row exists and is **non-terminal** with a fresher `updated_at` than the one selected — someone else is driving it |
| resume | `resume_session` returns failure | re-read row exists and is **not terminal** — it was resumed by another actor (typically `crash_recovery`) |
| create | `create_session` refused | the issue lock is now held by a live owner — the lane restarted itself |

Anything else is a real failure: charge the attempt, escalate per the escalation rules. Benign-race
classification is recorded in `findings` (`"benign-race: resume"`) so a persistently racing pair of
reflections is visible rather than silent.

### The steer message

Short, actionable, addressed to an agent, never a log line:

```
SDLC lane {slug} is stalled: PR #{pr} on issue #{issue}, last commit {H}h ago, no live run holds
the issue lock. Resume the pipeline: invoke /sdlc for issue #{issue}. Route one stage, then return.
```

Per CLAUDE.md principle 9 the receiving eng session drives one stage at a time; the message says so
explicitly rather than inviting a full unsupervised run.

The same text is the created session's `message_text` on the create rung, so a created session's
goal anchor and a steered session's instruction are one string with one meaning.

### Redis state — three keys, three lifetimes

All are plain (non-Popoto-managed) keys under the existing `sdlc:stall:` namespace, the same
exception `_DEDUP_PREFIX` already claims (`sdlc_progress.py:33-36`).

| Key | Purpose | Lifetime |
|---|---|---|
| `sdlc:stall:resume:cooldown:{slug}:{sha}` | Action cooldown — retrying an action is cheap, so this is short | `SDLC_STALL_RESUME_COOLDOWN_HOURS`, default **1** |
| `sdlc:stall:resume:attempts:{slug}:{sha}` | Attempt budget — `INCR`, TTL refreshed on each bump | 24 h |
| `sdlc:stall:escalated:{slug}:{sha}` | Human escalation, `SET NX` — the anti-ladder key | `SDLC_STALL_ESCALATION_TTL_DAYS`, default **30** |

Separating the action cooldown from the human-escalation key is the whole fix for defect 2. The old
`sdlc:stall:alert:{slug}:{sha}` 6-hour key is **deleted**, not repurposed: a 6-hour human key is
exactly the ladder.

`_DEFAULT_COOLDOWN_HOURS` and `SDLC_STALL_COOLDOWN_HOURS` are removed. Because a stalled PR's sha
never changes, a 30-day escalation TTL means one message per stalled head sha in practice; a new
commit changes the sha and legitimately re-arms every key at once.

Per the *provisional magic numbers* convention, every threshold is a named module constant with an
env override and a comment marking it tunable.

### Budgets

| Knob | Default | Meaning |
|---|---|---|
| `SDLC_STALL_RESUME_MAX_ATTEMPTS` | 3 | Per `(slug, sha)` action attempts before escalate-once-and-stop |
| `SDLC_STALL_CREATE_MAX_PER_TICK` | 1 | Max **creations** per project per tick — a burst brake on the widest rung |
| `SDLC_STALL_RESUME_ENABLED` | `true` | Break-glass; `false` restores notification-only, still with the anti-ladder escalation key |

There is deliberately **no cross-project run budget**. `run_per_project_audit` takes
`audit_one: Callable[[dict], dict]` (`reflections/utilities.py:74-79`) and calls it once per project
with no shared state threaded between calls, and reflections run under a subprocess-isolated
scheduler so a module-level global cannot carry state either. A cross-project cap would therefore
need its own run-tick Redis key, and it would buy nothing: the per-`(slug, sha)` attempt cap already
bounds every lane independently, and the live incident had three concurrent stalled lanes total. A
knob no code enforces is worse than no knob.

`SDLC_STALL_CREATE_MAX_PER_TICK` is enforceable precisely because it is scoped to one project:
`_check_project_stalls` loops over that project's stalled PRs within a single call, so a plain local
counter is real shared state. It brakes the one rung whose blast radius justifies a brake.

**An attempt is charged on success as well as failure** — except for benign races, which charge
nothing (see "Benign races are not attempts"). This is the direct lesson of
`crash_recovery.py:486-503` (critique C1): a counter that only advances on one branch never
converges. Here the success branch matters more — a steer that lands but does not move the pipeline
is precisely the steer-storm case the budget exists to bound. Since the key is scoped by head sha, a
steer that *does* move the pipeline changes the sha and resets everything for free.

### Escalation

`_send_alert` is retained but fires only when the system tried and could not:

- `create_session` was refused or failed (the ladder's true dead end);
- `steer_session` / `resume_session` returned a **non-benign** failure;
- attempts exhausted.

It never fires merely because no existing session was steerable — that case is now rung 3, not an
escalation — and never on a benign race.

Guarded by `SET NX` on `sdlc:stall:escalated:{slug}:{sha}`; if the `SET NX` returns false, the human
was already told and nothing is sent. Redis unavailable for the escalation write → **do not send**,
preserving the existing "under-alert during a flap beats spam during one" posture
(`sdlc_progress.py:229-242`).

The message changes voice: it reports the attempt and its failure, not the fact of the stall.

```
[{project}] SDLC lane {slug} (PR #{pr}, issue #{issue}) stalled {H}h and auto-resume failed after
{n} attempt(s): {reason}. Needs a human.
```

### Data Flow

```
reflection tick (1800s)
  └─ run_per_project_audit -> _check_project_stalls(project)
       ├─ gh pr list                      -> open non-draft session/sdlc-<N> PRs
       ├─ gh issue view <N>               -> issue open?
       ├─ git log origin/session/sdlc-<N> -> (sha, ts) -> age >= threshold?
       ├─ touch_issue_lock(N, None, peek) -> Redis session:issuelock:{N} -> live?
       ├─ AgentSession.query(issue_number)-> non-ledger non-terminal rows?  (secondary)
       ├─ machine_owns_project(key)       -> projects.json
       ├─ Redis: attempts / cooldown      -> act or skip
       ├─ AgentSession.query(project_key, session_type=eng)
       │    ├─ live     -> steer_session -> agent.steering RPUSH steering:{sid}
       │    ├─ terminal -> resume_session -> RPUSH then transition_status(pending)
       │    └─ neither  -> create_session -> get_or_create_worktree(.worktrees/sdlc-N)
       │                                    -> _push_agent_session(slug=sdlc-N, eng)
       │                                    -> worker pops -> `claude -p` turn
       │                                    -> /sdlc routes one stage -> commit -> sha moves
       ├─ classify outcome: benign race -> return early, charge nothing
       └─ on non-benign failure only: SET NX escalated key -> valor-telegram send
```

The sha moving is what closes the loop: it invalidates all three Redis keys, so a lane that stalls
again later is treated as a fresh incident rather than a budget-exhausted one.

### Race Conditions

| Race | Hazard | Mitigation |
|---|---|---|
| Lane restarts between the lock peek and the steer | Duplicate work in the target eng session | The steer is advisory; the eng session runs `/sdlc`, which itself short-circuits on `ISSUE_LOCKED` against a live foreign run. Two layers, both fail-closed. |
| Two reflection ticks overlap | Double steer | `SET NX` action-cooldown key is atomic; the loser skips. |
| Two machines both own the checkout | Duplicate lanes | Gate 6' machine-ownership. |
| `resume_session` transition loses to a worker pickup | Steer lands, transition fails | `resume_session` already pushes the steer first by design (`valor_session.py:837-856`); the message waits in the list and is drained at the next turn boundary either way. |
| Target session goes terminal between selection and steer | Steer rejected | `steer_session` re-reads status and rejects terminal (`session_executor.py:827-832`); the failure charges an attempt and falls through to escalation. |
| **`crash_recovery` resumes the same session first** | `resume_session` returns `success=False`; charging it would march a *successful* recovery toward a false human escalation — the original complaint through a side door | Benign-race classification: re-read the row; non-terminal means another actor resumed it. No attempt, no escalation, return early. See "Benign races are not attempts". |
| **Lane re-acquires its issue lock between gate 5' and the create rung** | A duplicate lane on top of a live one — the worst outcome in this change | Re-peek the lock immediately before creating; a live owner is a benign race, not a failure. |
| **Two stalled lanes in one project both fall to create in one tick** | Two session creations in a single tick | `SDLC_STALL_CREATE_MAX_PER_TICK` local counter (default 1); the second lane waits for the next tick, by which time rung 1 finds the created session. |

## No-Gos

- **Session creation is bounded to the stalled lane.** The create rung creates exactly one `eng`
  session, for the stalled lane's own slug (`sdlc-{issue}`), in that lane's existing worktree and
  branch. It never creates a session for a different project, never creates a slugless session
  (which would inherit the worker's branch state — issues #1109, #1272), never creates more than
  `SDLC_STALL_CREATE_MAX_PER_TICK` per project per tick, and never creates while a live owner holds
  the issue lock.
- **No steering `sdlc-local-{N}` anchors.** Structurally impossible (spike-1); no code should try.
- **No killing anything.** This reflection never terminates a session. `stall_advisory` owns kill
  authority; overlapping it would risk killing a ledger and orphaning its issue lock (PR #2106).
- **No setting `slug` on SDLC anchors.** The issue floated it as the "cleaner fix" for gate 5. It is
  not: it has a wide blast radius across every consumer that queries by slug, and it would not help
  anyway, because the anchor's `status=running` is exactly the misleading signal (spike-2).
- **No lowering `SDLC_STALL_THRESHOLD_HOURS`.** Out of scope; 4h stays.
- **No change to gates 1-4.** Branch shape, draft, issue-open, commit-age are all correct today.

## Update System

No update-system changes required. This is a single-module change to an already-registered
reflection; `config/reflections.yaml` keeps the same `name`, `callable`, and `every: 1800s`. No new
dependency, config file, secret, or migration. `/update` propagates it by pulling the merged commit
and restarting services, as it does for any reflection change.

One operational note for the runbook: the new knobs are env-overridable and default-on, so no
per-machine action is needed to enable the behavior.

## Agent Integration

No new CLI entry point and no bridge import required. The reflection is already registered in
`config/reflections.yaml` and dispatched by `agent/reflection_scheduler.py` as
`execution_type: function` → `reflections.sdlc_progress.run_sdlc_progress_check`; that entry point
is unchanged.

The change *is* an agent-integration surface in the other direction: the reflection now writes into
the steering inbox the worker already drains (`agent/steering.py`, the sole steering writer API).
Integration coverage is the E2E test in Test Impact below, which asserts a real steering message
lands on `steering:{session_id}` and is readable by `peek_steering_messages`.

## Success Criteria

- [ ] A stalled SDLC PR passing all gates results in a steering message delivered to a non-ledger
      eng session, and **zero** Telegram messages on the happy path.
- [ ] Gate 5' suppresses the stall verdict while a live local pipeline holds the issue lock; a live
      lane is never reported as "no active session".
- [ ] Repeated ticks over the same `(slug, head-sha)` produce **at most one** human-facing message,
      regardless of elapsed time. The 6-hour ladder is reproducible against `main` in a test and
      gone afterward.
- [ ] When no eng session is live or resumable, the reflection **creates** one bound to the stalled
      lane's slug, and still sends no Telegram message. Escalation happens only if creation fails.
- [ ] Creation never happens while a live owner holds the issue lock, never on a machine that does
      not own the project, and never more than `SDLC_STALL_CREATE_MAX_PER_TICK` times per project
      per tick.
- [ ] A benign race — another actor resumed the target, or the lane re-took its lock — charges no
      attempt and produces no Telegram message.
- [ ] Auto-resume attempts are bounded per `(slug, head-sha)`; on exhaustion the system escalates
      exactly once and stops acting.
- [ ] Every new external boundary (lock peek, session query, steer, resume, create, Redis) logs a
      warning and continues; the reflection never raises. Unknown state declines to act, and every
      gate that returns unknown leaves a marker in `findings` so degradation is visible.
- [ ] `valor-session create` behaves identically after the `create_session` extraction.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] `docs/features/pm-session-liveness.md` describes only the new status quo, including the
      corrected `Eng: Valor` alert target.

## Failure Path Test Strategy

Each failure boundary gets a test that asserts *both* halves of the posture: no crash, and no
action.

| Boundary | Injected failure | Asserted |
|---|---|---|
| `touch_issue_lock` | raises | gate returns `None`; no steer, no alert; reflection returns `status="ok"` |
| `AgentSession.query` | raises | no steer, no alert |
| `steer_session` | returns `success=False`, row re-reads terminal | attempt charged; escalation fires once |
| `steer_session` | returns `success=False`, row re-reads **non-terminal** | benign race: no attempt, no escalation, `findings` marker |
| `resume_session` | returns `success=False`, row re-reads terminal | attempt charged; escalation fires once |
| `resume_session` | returns `success=False`, row re-reads **non-terminal** (the `crash_recovery` race) | benign race: no attempt, no escalation, `findings` marker |
| `create_session` | raises / returns `success=False` | attempt charged; escalation fires once |
| Issue lock re-peek before create | live owner returned | benign race: no create, no attempt, no escalation |
| `get_or_create_worktree` | raises | surfaced as `create_session` failure; no partial session enqueued |
| Redis `INCR` (attempts) | raises | no action this tick (fail-safe: cannot prove we are under budget); `findings` marker |
| Redis `SET NX` (escalation) | raises / returns falsey | no Telegram send |
| `valor-telegram` | `FileNotFoundError` | swallowed and logged; reflection still returns `ok` |
| `machine_owns_project` | raises | treated as not-owner; no action |

## Test Impact

The existing suite encodes v1's notification-only contract. Several tests assert exactly the
behavior this plan reverses and must change rather than be added around.

- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_happy_path_emits_single_alert` —
      REPLACE: the happy path now emits a steering message and **no** alert.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_no_alert_when_session_active_or_unknown`
      — REPLACE: rewrite against the lock peek (`live` / `free` / `unknown`) instead of
      `_has_active_session`.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_alert_when_only_terminal_sessions` —
      UPDATE: terminal sessions now mean "resume target available", not "alert".
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — any case asserting "no session → alert"
      — REPLACE: no session is now the create rung, not an escalation.
- [ ] `tests/**` covering `tools/valor_session.py::cmd_create` — UNCHANGED, but re-run as the
      regression check for the `create_session` extraction. Grep at build time rather than working
      from an enumerated list.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_dedup_suppresses_second_alert` —
      REPLACE with the ladder regression: N ticks over an unchanged sha, spanning more than the old
      6-hour cooldown, produce exactly one human message.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_dedup_redis_unavailable_skips_alert`
      — UPDATE: retarget from the alert key to the escalation key.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_has_active_session_handles_redis_failure`
      — DELETE: `_has_active_session` is removed; superseded by the lock-peek failure test.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — tests for draft PRs, closed issues,
      missing branches, gh/git failures, cwd threading, and the canonical return shape are UNCHANGED
      and must keep passing.
- [ ] `tests/unit/test_stall_advisory_ledger_skip.py` — UNCHANGED, but re-run: the shared
      `_is_ledger` import path is touched by the helper extraction.
- [ ] Any test importing `reflections.crash_recovery._machine_owns_project` — UPDATE to the
      extracted `reflections.utilities.machine_owns_project`. (Grep at build time; none found in the
      current tree, but the extraction makes this a required sweep, not an enumerated list.)

New coverage to add:

- [ ] Live-local-lane suppression: lock peek reports a live owner → no steer, no alert.
- [ ] Gate 5' tiebreak polarity: lock says orphaned + a live non-ledger row for the issue → **live**
      (suppressed). Lock says live + no row → **live**. Row query raises + lock says live → still
      live. This is the inversion guard; it is the most important new unit test.
- [ ] Steer-target selection order: live eng session preferred over resumable, resumable preferred
      over create; ledger anchors never selected; sessions from another `project_key` never
      selected.
- [ ] Create rung: no live and no resumable eng session → `create_session` called once with
      `slug="sdlc-{issue}"`, `session_type="eng"`, and no Telegram message sent.
- [ ] Create rung lock re-peek: lock becomes held between gate 5' and create → no create, no
      attempt charged, no escalation.
- [ ] Benign-race classification: `resume_session` fails but the row re-reads non-terminal → no
      attempt charged, no escalation. Same for `steer_session`.
- [ ] Attempt-budget exhaustion: attempts reach the cap → one escalation, then silence across
      further ticks.
- [ ] Machine-ownership gate: non-owner machine takes no action.
- [ ] Create brake: two stalled lanes in one project both falling to create → only
      `SDLC_STALL_CREATE_MAX_PER_TICK` creations in that tick.
- [ ] Degradation markers: a gate returning unknown appends a `findings` marker rather than
      returning a summary identical to a healthy zero-stall tick.
- [ ] E2E integration (`tests/integration/`): a real `AgentSession` + real Redis; assert the
      steering message actually lands on `steering:{session_id}` and reads back via
      `peek_steering_messages`. Test sessions use a `test-` `project_key` prefix and are deleted via
      the ORM afterward, per CLAUDE.md testing hygiene.

## Rabbit Holes

- **Making a session query the *primary* liveness authority.** Every variant of this (match `slug`,
  match `issue_number`, match `session_id` prefix) re-derives liveness from a row's existence, which
  is exactly the mirage spike-2 documents. The lock is the authority; the `issue_number` row query
  survives only as an OR-ed secondary signal that can add liveness and never remove it (see Gate
  5'). Do not promote it, do not add a second row query, and do not let a row result veto the lock.
  Time-box: if the lock peek proves insufficient, escalate as an Open Question rather than
  reintroducing row inference as the authority.
- **Making the steer conversational.** The steer is one message with a `/sdlc` instruction. It does
  not negotiate, ask questions, or carry pipeline state. Anything richer belongs in `/sdlc` itself.
- **Unifying the three stall reflections.** `sdlc-progress-check`, `stall-advisory`, and
  `crash-recovery` overlap conceptually. A merge is a separate, larger piece of work; here we only
  share `machine_owns_project`.
- **Fixing `_machine_owns_project`'s projects.json path resolution repo-wide.** Fix it in the
  extracted helper; do not audit every other `config/projects.json` reader in this change.
- **Dashboard surfacing of auto-resume attempts.** Tempting and out of scope. Findings already flow
  into the reflection's return value, which the dashboard renders.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Steering the wrong eng session (a human conversation in progress) | Medium | The steer is a short, explicit instruction naming the issue; the eng session's own routing decides. Bounded by the run budget so at most a couple land per tick. |
| A steered eng session ignores the message | Medium | Attempt budget converges to escalate-once; success charges an attempt too, so an ignored steer cannot loop. |
| Lock peek misreads a live lane as dead during a Redis flap | Medium | Peek exceptions → `None` → skip. A false "free" requires Redis to return a *wrong* payload, not to fail. |
| 30-day escalation TTL hides a genuinely new problem | Low | The key is scoped by head sha; any commit re-arms it. A stalled sha is by definition the same incident. |
| Helper extraction breaks `crash_recovery` | Low | Same signature, same fail-soft semantics; covered by re-running its existing tests. |
| The create rung spawns a lane on top of a live one | **High** | Three independent guards: gate 5' lock peek, a second lock peek immediately before create, and `/sdlc`'s own `ISSUE_LOCKED` short-circuit inside the created session. All three fail closed. |
| `create_session` extraction regresses `valor-session create` | Medium | The extraction moves two calls (`get_or_create_worktree`, `_push_agent_session`) behind a core and leaves argparse/printing in `cmd_create`; existing `cmd_create` tests are re-run unchanged as the regression check. |
| A created session burns tokens on an already-finished lane | Low | The lane is only reached after gates 1-4 confirm an open issue, an open non-draft PR, and a commit older than the threshold. A finished lane fails gate 1 or 3. |
| Two machines both act despite the gate | Low | `projects.<key>.machine` is the repo's declared source of truth and is validated by `bridge/config_validation.py`. |

## Step by Step Tasks

1. **Extract the ownership helper.** Move `_machine_owns_project` from
   `reflections/crash_recovery.py:99-126` to `reflections/utilities.py::machine_owns_project`,
   resolving projects.json the same way `load_local_projects` does (`PROJECTS_CONFIG_PATH` →
   Desktop → repo fallback). Update `crash_recovery` to import it and delete the private copy. Grep
   for any other reference and sweep.
2. **Replace gate 5.** Delete `_has_active_session`. Add `_lane_is_live(issue_number)` over
   `touch_issue_lock(..., peek=True)` with the three-way result mapping, plus the secondary
   non-ledger `issue_number` session check. Fail-soft to `None`.
3. **Add gate 6.** Call `machine_owns_project(project["slug"])` in `_check_project_stalls`; skip
   silently when not the owner.
4. **Add the Redis key helpers.** `_action_cooldown_set`, `_bump_attempts`, `_escalation_set` —
   named module constants with env overrides and provisional-tunable comments. Delete
   `_dedup_set`, `_DEDUP_PREFIX`'s 6-hour alert key, `_cooldown_seconds`, and
   `_DEFAULT_COOLDOWN_HOURS`.
5. **Add `_pick_steer_target(project_key)`.** Live non-ledger eng session first, then most recent
   resumable eng session with a `claude_session_uuid`. Returns `(kind, session)` or `(None, None)`.
6. **Add `_attempt_resume(...)`.** Compose the steer message, dispatch via `steer_session` or
   `resume_session`, charge the attempt on both branches, return a structured outcome.
7. **Rewrite the tail of `_check_project_stalls`.** Wire the ladder: budget check → cooldown →
   target → attempt → escalate-on-failure. Keep the canonical
   `{status, findings, summary, duration}` return shape and extend the summary with steer counts,
   mirroring `stall_advisory`'s `run_state` accounting.
8. **Rewrite `_send_alert`'s caller contract.** New message text (attempt-and-failure voice), fired
   only from the escalation path, `SET NX`-guarded. Keep the `Eng: Valor` target.
9. **Update the module docstring.** Delete "Notification-only for v1 — never creates or resumes PM
   sessions"; describe the ladder and list every env knob.
10. **Update the existing tests** per Test Impact, then add the new coverage listed there.
11. **Add the E2E integration test** against real Redis with a `test-`-prefixed project key and ORM
    cleanup.
12. **Rewrite `docs/features/pm-session-liveness.md`** per the Documentation section.
13. **Run `python -m ruff check` and `python -m ruff format`**, then the targeted test files via
    `scripts/pytest-clean.sh`.

## Documentation

- [ ] Rewrite `docs/features/pm-session-liveness.md` §"State-layer detection (`sdlc-progress-check`)":
      replace gate 5's description with the lock-peek liveness rule, add gate 6 (machine ownership),
      and document the action ladder.
- [ ] Delete the "Not auto-recovery… Recovery is a human decision after seeing the alert" bullet in
      §"What this is NOT" and replace it with the new boundary: *this reflection resumes pipelines
      but never creates sessions and never kills anything.* Describe only the new status quo
      (CLAUDE.md principle 1) — no "formerly notification-only" note.
- [ ] Correct the alert-target drift in the same file: the target is `Eng: Valor`, not `Dev: Valor`.
- [ ] Replace the Tunables table with the full new knob set (`SDLC_STALL_THRESHOLD_HOURS`,
      `SDLC_STALL_RESUME_ENABLED`, `SDLC_STALL_RESUME_MAX_ATTEMPTS`,
      `SDLC_STALL_RESUME_RUN_BUDGET`, `SDLC_STALL_RESUME_COOLDOWN_HOURS`,
      `SDLC_STALL_ESCALATION_TTL_DAYS`), and drop the removed `SDLC_STALL_COOLDOWN_HOURS`.
- [ ] Add a cross-reference to `docs/features/session-steering.md` — steering is now a consumer
      surface of this reflection.
- [ ] Check `docs/features/README.md` for an index entry needing a description refresh.

## Open Questions

1. **When no eng session is steerable at all, is escalate-once acceptable, or should the reflection
   create a session?** The plan says escalate-once (No-Gos). Spike-5 shows the target population is
   healthy today, but it varies. Creating a session from a reflection is the single widest-blast-
   radius option in this change; deferring it keeps the appetite Medium. **Recommendation: ship
   escalate-once, revisit if the branch fires.**
2. **Should the steer prefer an eng session already conversing about this issue?** The current
   ladder picks the most recently updated eng session for the project, which is a proxy. A stricter
   match (a session whose `issue_number` equals the stalled issue) would be more precise but is
   usually empty. **Recommendation: most-recent, as planned — precision here buys little.**
3. **Is a 30-day escalation TTL right, or should the key be TTL-less?** TTL-less is the purest "tell
   a human once" but leaks keys for abandoned branches. 30 days is a compromise.
   **Recommendation: 30 days, env-overridable.**

## Critique Results

### Round 1 (FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Four findings: 1 BLOCKER,
2 CONCERNs, 1 NIT. The blocker is the run-budget knob, flagged independently by all three critics.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (all 3) | `SDLC_STALL_RESUME_RUN_BUDGET` is specified as "Max steers per reflection tick across all projects", but `_check_project_stalls` is invoked once per project by `reflections/utilities.py::run_per_project_audit`, whose `audit_one` parameter is `Callable[[dict], dict]` (utilities.py:589) with no shared state threaded between calls. The "three keys, three lifetimes" Redis table lists no run-tick-scoped key either, so the cross-project cap has no specified mechanism, and the Test Impact item "Run-budget brake: more stalled lanes than `SDLC_STALL_RESUME_RUN_BUDGET` → only the budgeted number are steered in one tick" cannot be satisfied as written. | pending | Pick ONE and make the Budgets table, the Redis-state table, Step 7, and the Test Impact bullet agree. (a) Drop the knob: the per-`(slug, sha)` attempt cap already bounds the worst case, and the live incident showed at most 3 concurrent stalled lanes; remove it from Budgets, from Step 7's "mirroring `stall_advisory`'s `run_state` accounting", and from the Test Impact bullet. (b) Keep it and thread run state explicitly: `run_state = {"steered": 0}; bound = functools.partial(_check_project_stalls, run_state=run_state); return run_per_project_audit(bound, ...)`, with `_check_project_stalls` checking `run_state["steered"] >= budget` before acting and incrementing on every charged attempt. A module-level global is NOT an option — reflections run in a subprocess-isolated scheduler. (c) Re-scope the knob per-project and reword both tables. |
| CONCERN | Scope & Value | Gate 5' retains "a secondary, cheap sanity check … skip if any non-ledger non-terminal `AgentSession` carries this `issue_number`" (plan:211-214), but Rabbit Holes bans that exact variant by name — "match `issue_number` … re-derives liveness from a row's existence. Do not go down this road; the lock is the authority" (plan:459-462). The plan tells the builder both to build it and never to build it, and specifies no rule for which signal wins when the lock and the row disagree. | pending | If the secondary check is kept, `_lane_is_live` must OR the two signals for "live" (lock-live OR a non-terminal non-ledger row with this `issue_number`) and never let the row check override a lock-says-orphaned result into "not live" — that inversion is the permanent-false-negative mode spike-2 already ruled out for ledger anchors. Then amend the Rabbit Holes wording so the ban reads "never as the primary authority" rather than an absolute prohibition. Otherwise delete the secondary check: a Redis flap during lock acquire already fails soft to `None` (skip), which is the same posture. |
| CONCERN | Risk & Robustness | The Race Conditions table omits the cross-reflection resume race: `crash_recovery` runs every 300s and can call `resume_session` on the same terminal eng session this reflection selects. The loser of the atomic `transition_status` gets `ResumeResult(success=False)`, which under "an attempt is charged on success as well as failure" is indistinguishable from a genuine dead end — so another reflection's legitimate resume can drive this ladder to a false human escalation, the exact outcome the plan exists to prevent. | pending | In `_attempt_resume`, before charging the attempt on a `resume_session` failure, re-read the row: `fresh = AgentSession.query.filter(session_id=session.session_id).first()`; if `fresh` exists and its status is NOT terminal, classify the outcome as "already-resumed-elsewhere" — no attempt charged, no escalation, return early. Mirror `steer_session`'s re-read-before-reject pattern in `agent/session_executor.py`. Add the race to the Race Conditions table and a failure-path test row for it. |
| NIT | Risk & Robustness | Every fail-safe branch (lock-peek exception, `AgentSession.query` exception, `machine_owns_project` exception, Redis `INCR` exception) declines to act and logs only a `logger.warning`; none appends to the reflection's `findings`/`summary`. A sustained Redis or `gh` degradation would leave the ladder silently inert while the dashboard summary reads identically to a healthy day with zero stalls (`errored` stays 0 because nothing raises). | pending | Optional observability improvement: append a short marker finding (e.g. `"gate-unknown: lock-peek"`) whenever a gate returns `None`, so degradation trends visibly instead of looking like quiet. |
| NIT | Structural check | The plan shipped without a `## Critique Results` section. `tools/sdlc_verdict.py` requires that section to be present and populated before a non-READY verdict can be recorded, so its absence would have failed the `CRITIQUE_FINDINGS_MISSING` gate. | pending | This section was created by the critique run itself; the plan template should carry the header from creation. |

