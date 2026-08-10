---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2696
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-10T04:52:09Z
---

# SDLC Stall Auto-Resume (steer, resume, or create an eng session; escalate once)

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
| `touch_issue_lock` raises on Redis failure, so the gate can map it to "unknown" | Round-3 re-read of `models/session_lifecycle.py:1310-1323` | **Falsified** — it fails OPEN, returning `acquired=True`. Gate 5' redesigned onto a direct key read; see spike-3 |
| `_derive_sdlc_metadata` mis-classifies the reflection's steer text | Executed live against the real `valor` project config | **Corrected** — it returns `('sdlc', <issue url>)`, which is the desired result, not a defect |
| Hollow sessions (non-terminal, no heartbeat, no fence) are a live hazard | Round-3 observation: four `sdlc-local-*` rows reporting `running`, refreshed only by a bulk probe | **New** — constrains Gate 5's secondary row check (freshness bound) and the create rung (created sessions must reach a terminal status) |

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
- **Method**: live query, then code-read (round-3 re-verification against `main`)
- **Result**: **The lock payload is trustworthy; `touch_issue_lock` as the reader is not.**

  The *evidence* holds. All four live lanes' lock payloads showed a live owner, and liveness is
  computed by `_lock_owner_is_live` (`models/session_lifecycle.py:982-1052`) from
  `renewed_at` recency first (`_lock_renewal_is_fresh`, `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS`,
  default 20 min), then a same-machine pid + `create_time` check — never from an `AgentSession`
  status. A dead run stops renewing and the lease self-frees within `ISSUE_LOCK_TTL_SECONDS`
  (30 min).

  The *reader* does not hold. `touch_issue_lock` **fails open**: every Redis exception is swallowed
  and returned as `IssueLockResult(acquired=True, ...)` (`:1310-1323`, documented at `:1140-1144`).
  Under the mapping table `acquired=True` means "lock unheld → not live → continue", so a Redis flap
  during the peek would route the ladder *into* acting. The function never raises, so "unknown" is
  unreachable through it and a test that mocks it to raise would be testing a fiction.

  **Resolution (round 3, owner direction — critique option (b)):** the reflection does not call
  `touch_issue_lock`. It reads `session:issuelock:{issue_number}` directly and classifies the payload
  with `_lock_owner_is_live`, under the caller's own `except Exception -> None`. That is the only
  shape in which "unknown → skip" is actually producible. See Gate 5'.
- **Confidence**: high — both halves verified by direct code read against `main`.
- **Impact if false**: n/a — the fail-open behavior is a read fact, not an inference.

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

**Large.** Raised from Medium in the round-3 revision, deliberately and on the record: the owner's
decision to keep the create rung (Open Question Q1) added a session-provisioning capability and a
second cross-module extraction after the appetite was first set, and leaving `Medium` in place would
have been a stale number rather than a judgment.

What the work actually is:

1. `reflections/sdlc_progress.py` rewritten in place — gate 5 replaced, gate 6 added, the
   notification tail replaced by a four-rung action ladder with benign-race classification.
2. **Extraction A** — `machine_owns_project` out of `reflections/crash_recovery.py` into
   `reflections/utilities.py`, with a projects.json path-resolution fix.
3. **Extraction B** — `create_session` out of `tools/valor_session.py::cmd_create`, which rewrites an
   independently-tested CLI entry point. This is the expensive one: `cmd_create` is 220 lines of
   interleaved resolution, filesystem, and printing, and the extraction must preserve its behavior
   exactly (see "The create rung" for the boundary).
4. `docs/features/pm-session-liveness.md` rewritten; one test file substantially rewritten (several
   existing cases REPLACED, not added around) plus a new integration test.

Still no new service, no schema change, no migration, and no new dependency — the size is in the
breadth of the change, not its infrastructure.

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

**Mechanism: a direct, non-fail-open read of the lock key — NOT `touch_issue_lock`.**

`touch_issue_lock` cannot express "unknown". It swallows every Redis exception and returns
`IssueLockResult(acquired=True, ...)` (`models/session_lifecycle.py:1310-1323`), which this gate
would read as "lock unheld → not live → continue" — a Redis flap would push the ladder into acting
instead of declining, and would do so at both call sites the High-severity create-rung risk depends
on. Fail-open is correct for `touch_issue_lock`'s own callers (a Redis hiccup must not wedge the
pipeline) and exactly wrong here, where the caller wants to be *more* reluctant when blind.

So the reflection reads the key itself and classifies the payload with the same helper
`touch_issue_lock` uses:

```python
_LOCK_KEY = "session:issuelock:{issue_number}"

def _lock_says_live(issue_number: int) -> bool | None:
    try:
        from models.session_lifecycle import _lock_owner_is_live
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        raw = _R.get(f"session:issuelock:{issue_number}")
        if raw is None:
            return False                       # no lock -> nobody owns the lane
        return _lock_owner_is_live(json.loads(raw))
    except Exception:
        logger.warning(...)
        return None                            # unknown -> caller skips
```

This is read-only by construction — a `GET` cannot claim or renew a lease, which is a stronger
guarantee than the `peek=True` / `run_id=None` argument pair the earlier draft relied on.

Three notes the builder must not lose:

- `_lock_owner_is_live` is imported private-but-in-repo, deliberately. Re-deriving the
  renewal-recency + pid + `create_time` logic in the reflection would fork the liveness rule the
  moment `session_lifecycle.py` changes; this feature's whole thesis is that the lock owns liveness.
- **A malformed payload is unknown, not free.** `json.loads` raising lands in the `except` and
  returns `None`. `touch_issue_lock` maps a malformed payload to `acquired=False` (held-by-nobody);
  that is a different and, here, less safe reading.
- `_lock_owner_is_live` itself fails *toward* live on every indeterminate branch (foreign machine,
  legacy payload with no `create_time`, psutil error). That polarity is right for this gate: a lock
  we cannot adjudicate suppresses action rather than licensing a second lane.

Mapping:

| Read result | Meaning | Gate |
|---|---|---|
| key absent | lock unheld | not live → continue |
| payload present, `_lock_owner_is_live` → `True` | live owner (fresh renewal, or a pid/machine check that cannot disprove life) | **live → skip silently** |
| payload present, `_lock_owner_is_live` → `False` | owner died, TTL not yet lapsed | not live → continue |
| Redis error, malformed JSON, or any exception | unknown | **None → skip silently** |

This also produces a happy coincidence that the design leans on: by the time the gate lets us
through, the stalled lane's lock is free or self-freeing, so the eng session we steer will not hit
`ISSUE_LOCKED` when it runs `/sdlc`.

A secondary, cheap sanity check is retained, and its tiebreak is stated explicitly because the two
signals can disagree.

**Tiebreak rule: the two signals are OR-ed for "live", never AND-ed.** A lane is live if the lock
read says live **OR** a fresh non-ledger non-terminal `AgentSession` carries this `issue_number`. The
row check can only ever *add* liveness; it must never turn a lock-says-dead result into "not live".
That inversion is the permanent-false-negative mode spike-2 already ruled out for ledger anchors, and
it is the single way this secondary check could make the detector worse.

Stated as code, so the builder cannot get the polarity wrong:

```python
lock_live = _lock_says_live(issue_number)
if lock_live is None:
    return None            # unknown -> caller skips
if lock_live:
    return True            # lock is authoritative for "live"
return _nonledger_row_live(issue_number) or False   # row can only add liveness
```

Note the ordering consequence: because an unknown lock read short-circuits to `None`, the secondary
row check is never consulted during a Redis outage. That is intentional. The row query goes through
the same Redis; a degraded backend cannot be its own second opinion.

The row check costs one indexed query and catches a lane running without a lock (a Redis flap
during acquire). Ledger rows are excluded via `agent.session_health._is_ledger`, exactly as
`stall_advisory.py:130-139` does. If the row query itself raises, it contributes nothing (`False`)
rather than `None` — a failed *secondary* signal must not veto a successful primary one.

**The row check must be freshness-bounded, because hollow sessions are real.** Observed live during
round-3 recon: four `sdlc-local-*` rows reporting `status=running` with no heartbeat and no fence,
their `updated_at` refreshed only by a bulk liveness probe. Those particular rows are `is_ledger` and
so already excluded — but the general hazard is not, and this feature *creates* non-ledger eng
sessions that could become hollow the same way. A frozen non-terminal row would otherwise add false
liveness forever and re-create the permanent-false-negative mode spike-2 ruled out, this time through
the side door of a session the reflection itself minted.

So `_nonledger_row_live` requires **both** a non-terminal status **and** an `updated_at` newer than
`SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES` (default 30 — provisional, tunable; deliberately aligned
with `ISSUE_LOCK_TTL_SECONDS` so the two liveness horizons expire together). A row older than that
contributes `False`: it is not evidence of life, it is evidence of a row nobody has touched. This
keeps the secondary signal's failure mode "stops adding liveness", never "suppresses forever".

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
(`tools/valor_session.py:423-645`) interleaves argparse reads, `print` calls, and exit codes with the
actual work. Both reflection rungs must call programmatic cores rather than one calling a core and
the other shelling out to a CLI contract.

**The extraction boundary is "everything from slug resolution onward."** An earlier draft of this
plan described `cmd_create`'s real work as "only two steps — `get_or_create_worktree` and
`_push_agent_session`". That was wrong, and building to it would have silently dropped five inputs
`_push_agent_session` actually receives. Verified against `main`, `cmd_create` performs, in order:

| Step | Location | Moves into `create_session`? |
|---|---|---|
| argparse attribute reads, `--json` flag | `:423-504` | **No** — stays in `cmd_create` |
| slug auto-derive-or-reject (`_derive_slug_from_message`; eng requires a slug, #1109/#1272) | `:511-536` | **Yes** |
| three-tier `project_key` resolution (explicit > `--parent` inheritance > cwd) | `:538-568` | **Yes** |
| `_resolve_project_working_directory(project_key)` → `repo_root`, `project_config` | `:577` | **Yes** |
| `_derive_sdlc_metadata(message, project_config)` → `classification_type`, `issue_url` | `:578` | **Yes** |
| `_validate_slug` + `get_or_create_worktree` → `working_dir` | `:580-588` | **Yes** |
| `_push_agent_session(...)` with the full payload | `:592-608` | **Yes** |
| `_check_worker_health()` | `:612` | **Yes** — returned in the result, not printed by the core |
| `print` / JSON emission / exit codes | `:614-645` | **No** — stays in `cmd_create` |

`cmd_create` keeps **only** argparse reads, printing, and exit codes. Everything else is the core.

Signature, naming the full payload explicitly so nothing is dropped:

```python
@dataclass
class CreateResult:
    success: bool
    session_id: str | None = None
    error: str | None = None
    working_dir: str | None = None
    project_key: str | None = None
    worker_healthy: bool | None = None
    worker_heartbeat_age_s: float | None = None

def create_session(
    *,
    message: str,
    role: str = "eng",
    slug: str | None = None,          # None -> auto-derive-or-fail for non-teammate roles
    project_key: str | None = None,   # None -> parent inheritance, then cwd match
    parent_id: str | None = None,
    session_type: str = "eng",
    chat_id: int = 0,
    model: str | None = None,
    requires_real_chrome: bool = False,
) -> CreateResult: ...
```

`classification_type`, `issue_url`, `project_config`, and `working_dir` are **derived inside** the
core (they are not caller inputs) and passed to `_push_agent_session` exactly as `cmd_create` passes
them today; `model` and `requires_real_chrome` are caller inputs and must be threaded through. The
Success Criterion "`valor-session create` behaves identically" is checked by re-running the existing
`cmd_create` tests unchanged.

**Errors become return values, not exit codes.** `cmd_create`'s failure modes — the slugless-eng
rejection, `ProjectKeyResolutionError`, `_validate_slug`'s `ValueError`, and the blanket
`except Exception` — currently `return 1` after printing. In the core they become
`CreateResult(success=False, error=...)`, and `cmd_create` maps a falsey result back to its print +
exit-1 behavior. This is what lets the reflection's create rung distinguish "refused" from "crashed"
without parsing stderr.

**`_derive_sdlc_metadata` on the reflection's steer text — verified, and it does NOT degrade to
`(None, None)`.** The round-2 critique asked for this to be checked rather than assumed. Read against
`main` (`tools/valor_session.py:110-148`): the function returns `("sdlc", url)` when the message
contains a GitHub issue URL **or** a bare `issue #N` reference. The steer message (see "The steer
message") contains `issue #{issue}` by design, so it matches branch 2 and yields
`classification_type="sdlc"` with the issue URL built from the project's `github.org`/`github.repo`
config.

Executed live against `main` with the real `valor` project config, the steer text yields:

```
github cfg: {'org': 'tomcounsell', 'repo': 'ai'}
_derive_sdlc_metadata(...) -> ('sdlc', 'https://github.com/tomcounsell/ai/issues/2696')
```

That is the correct and desired outcome, not a mis-classification: the created session *is* SDLC work
on that issue, and the metadata is what gives it a real `issue_url`. The builder must not "fix" this
into `(None, None)`, and no test may assert `(None, None)` for this text. The only degraded case is a
project whose config lacks `github.org`/`github.repo`, where `issue_url` is `None` while
`classification_type` stays `"sdlc"` — harmless, since the message text still names the issue.

The created session is an `eng` session with `slug = "sdlc-{issue}"` — the stalled lane's own slug,
so it lands in the lane's existing worktree and on the lane's existing branch rather than starting a
parallel one. `get_or_create_worktree` is idempotent for an existing worktree, which is the normal
case for a stalled lane.

**Guards, all mandatory:**

| Guard | Why |
|---|---|
| Gate 6' machine ownership must have passed | Two machines must never both create a lane. |
| Re-read the issue lock immediately before creating | Gate 5' ran earlier in the tick; the lock can be re-acquired in between. Uses the same `_lock_says_live` direct read, so an unknown result here also declines. A live owner is a **benign race** — no attempt charged, no escalation. |
| Charged against the same per-`(slug, head-sha)` attempt budget | A create-loop is impossible: three creates for one stalled sha exhausts the budget and escalates once. |
| `SDLC_STALL_CREATE_MAX_PER_TICK` (default 1) | Per-project-tick brake, enforced by a plain local counter in `_check_project_stalls` — see Budgets. |
| Distinct `kind="create"` in logs, `findings`, and the summary counters | The rungs must be tellable apart in telemetry; the create rung is the one to watch. |

The rung is self-limiting across ticks: once a session is created for a project it is non-terminal,
so the next tick's rung 1 finds it and steers it instead of creating another.

**The created session must be able to reach a terminal status.** This is a hard requirement, not a
nicety, and it is the counterpart to the hollow-row hazard named under Gate 5'. The self-limiting
property above depends on rung 1 finding the created session — but if that session goes hollow
(non-terminal forever, no heartbeat, worktree frozen), rung 1 finds it every tick and steers a corpse
until the attempt budget escalates. Two properties keep that bounded:

- The session is created through `_push_agent_session`, the same enqueue path the bridge uses, so it
  is claimed by the worker and driven by the normal session lifecycle that ends in a terminal status.
  This reflection introduces no bespoke session-provisioning path that could sidestep it. (Contrast
  `tools/sdlc_session_ensure.py`, which uses `create_local` and mints the `is_ledger` anchors that
  legitimately never execute.)
- The per-`(slug, head-sha)` attempt budget bounds the steer-a-corpse loop to
  `SDLC_STALL_RESUME_MAX_ATTEMPTS` regardless, and the freshness bound on the secondary row check
  stops a hollow row from suppressing the gate.

The reflection deliberately does **not** try to detect or clean up a hollow session it created —
that is `stall_advisory`'s and `crash_recovery`'s territory, and reaching into it here would collide
with the "No killing anything" no-go.

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
| resume | `resume_session` returns failure | re-read row exists and is **not terminal** — it was resumed by another actor (typically `crash_recovery`) |
| create | `create_session` refused | the issue lock is now held by a live owner — the lane restarted itself |

Anything else is a real failure: charge the attempt, escalate per the escalation rules. Benign-race
classification is recorded in `findings` (`"benign-race: resume"`) so a persistently racing pair of
reflections is visible rather than silent.

**The steer rung has no benign-race branch, deliberately.** An earlier draft gave it one ("re-read row
is non-terminal with a fresher `updated_at`"). Verified against `main`, that branch is unreachable:
`steer_session`'s only `success=False` returns are empty message, session not found, terminal status
(`agent/session_executor.py:827-832`), and `is_ledger` (`:841-849`). A live non-ledger target simply
**succeeds** — being actively driven by someone else does not make a steer fail, it just queues
another message. Every real steer failure means the target is gone, terminal, or was never
steerable, all of which are genuine dead ends that should charge an attempt. The classifier therefore
covers the resume and create rungs only, and no test may mock `steer_session` into a
failure-while-non-terminal shape the function cannot emit.

**Accepted side effect on a benign-race resume.** `resume_session` pushes the steering message
unconditionally (`tools/valor_session.py:856`) *before* the `transition_status` call that can fail.
So when this reflection loses the race to `crash_recovery`, its stall instruction has already landed
on `steering:{session_id}` and will be drained by the session the other actor resumed. "No attempt,
no escalation, return early" therefore does **not** mean "no side effect": it means no budget
consumed and no human paged. The duplicated instruction is harmless — it names the issue and asks for
one `/sdlc` stage, which is what that session should be doing anyway. Making the push conditional
would require changing `resume_session`'s contract for all its callers, which is outside this plan's
boundary.

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
| `sdlc:stall:resume:attempts:{slug}:{sha}` | Attempt budget — `INCR`, TTL refreshed on each bump | `SDLC_STALL_ATTEMPTS_TTL_HOURS`, default **24** |
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
| `SDLC_STALL_ATTEMPTS_TTL_HOURS` | 24 | TTL on the attempts key, refreshed on each bump. Bounds how long an exhausted budget stays exhausted for an unchanged head sha |
| `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES` | 30 | Freshness bound on Gate 5's secondary row check, so a hollow non-terminal row cannot add liveness forever |

Every one of these is a named module constant read through an `os.environ.get(...)` override with a
comment marking it provisional and tunable, mirroring the existing `_threshold_seconds()`
float-then-int-hours pattern at `reflections/sdlc_progress.py:56-61`. No bare literal thresholds
survive anywhere in the module — the round-2 critique caught the attempts TTL sitting as a bare
"24 h" in prose, and the rule is now stated as a build-time invariant rather than an aspiration.

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
       ├─ _R.get(session:issuelock:{N}) + _lock_owner_is_live -> live? (never touch_issue_lock)
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
| Lane restarts between the lock read and the steer | Duplicate work in the target eng session | The steer is advisory; the eng session runs `/sdlc`, which itself short-circuits on `ISSUE_LOCKED` against a live foreign run. Two layers, both fail-closed. |
| Two reflection ticks overlap | Double steer | `SET NX` action-cooldown key is atomic; the loser skips. |
| Two machines both own the checkout | Duplicate lanes | Gate 6' machine-ownership. |
| `resume_session` transition loses to a worker pickup | Steer lands, transition fails | `resume_session` already pushes the steer first by design (`valor_session.py:837-856`); the message waits in the list and is drained at the next turn boundary either way. |
| Target session goes terminal between selection and steer | Steer rejected | `steer_session` re-reads status and rejects terminal (`session_executor.py:827-832`); the failure charges an attempt and falls through to escalation. |
| **`crash_recovery` resumes the same session first** | `resume_session` returns `success=False`; charging it would march a *successful* recovery toward a false human escalation — the original complaint through a side door | Benign-race classification: re-read the row; non-terminal means another actor resumed it. No attempt, no escalation, return early. See "Benign races are not attempts". |
| **Lane re-acquires its issue lock between gate 5' and the create rung** | A duplicate lane on top of a live one — the worst outcome in this change | Re-read the lock immediately before creating; a live owner is a benign race, not a failure, and an unknown read declines. |
| **`resume_session` pushes the steer before its transition can fail** | On a benign-race resume the stall instruction has already landed on `steering:{session_id}` and is drained by the session another actor resumed | **Accepted side effect**, not mitigated. The duplicate instruction names the issue and asks for one `/sdlc` stage — what that session should be doing anyway. Making the push conditional means changing `resume_session` for all callers, outside this plan's boundary. See "Benign races are not attempts". |
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
- [ ] Every new external boundary (lock read, session query, steer, resume, create, Redis) logs a
      warning and continues; the reflection never raises. Unknown state declines to act, and every
      gate that returns unknown leaves a marker in `findings` so degradation is visible.
- [ ] `valor-session create` behaves identically after the `create_session` extraction.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] `docs/features/pm-session-liveness.md` describes only the new status quo, including the
      corrected `Eng: Valor` alert target.

## Failure Path Test Strategy

Each failure boundary gets a test that asserts *both* halves of the posture: no crash, and no
action.

**One prohibition, stated up front: no test may mock `touch_issue_lock` to raise.** It does not
raise — it fails open and returns `acquired=True` on every Redis exception
(`models/session_lifecycle.py:1310-1323`). A test built on that mock would pass while asserting
behavior the production code cannot produce, which is precisely the trap the round-2 critique caught
this plan walking into. The reflection does not call `touch_issue_lock` at all; the injectable
boundary is the direct `_R.get` on `session:issuelock:{N}`, which does raise.

| Boundary | Injected failure | Asserted |
|---|---|---|
| Lock key read (`_R.get`) | raises | gate returns `None`; no steer, no create, no alert; reflection returns `status="ok"`; `findings` marker |
| Lock key read | returns malformed (non-JSON) payload | gate returns `None` — **unknown, not free**; no action |
| `AgentSession.query` (secondary row check) | raises | contributes `False`, never `None`; a lock-says-live result still suppresses |
| `AgentSession.query` (target selection) | raises | no steer, no alert |
| `steer_session` | returns `success=False` (any cause) | attempt charged; escalation fires once. **No benign-race branch exists for this rung** — see "Benign races are not attempts" |
| `resume_session` | returns `success=False`, row re-reads terminal | attempt charged; escalation fires once |
| `resume_session` | returns `success=False`, row re-reads **non-terminal** (the `crash_recovery` race) | benign race: no attempt, no escalation, `findings` marker |
| `create_session` | raises / returns `success=False` | attempt charged; escalation fires once |
| Issue lock re-read before create | live owner returned | benign race: no create, no attempt, no escalation |
| Issue lock re-read before create | read raises (`None`) | no create, no attempt, no escalation; `findings` marker |
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
      — REPLACE: rewrite against the direct lock read (`live` / `free` / `unknown`) instead of
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
      — DELETE: `_has_active_session` is removed; superseded by the lock-read failure test.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — tests for draft PRs, closed issues,
      missing branches, gh/git failures, cwd threading, and the canonical return shape are UNCHANGED
      and must keep passing.
- [ ] `tests/unit/test_stall_advisory_ledger_skip.py` — UNCHANGED, but re-run: the shared
      `_is_ledger` import path is touched by the helper extraction.
- [ ] Any test importing `reflections.crash_recovery._machine_owns_project` — UPDATE to the
      extracted `reflections.utilities.machine_owns_project`. (Grep at build time; none found in the
      current tree, but the extraction makes this a required sweep, not an enumerated list.)

New coverage to add:

- [ ] Live-local-lane suppression: the lock read reports a live owner → no steer, no alert.
- [ ] Gate 5' tiebreak polarity: lock says dead + a fresh live non-ledger row for the issue →
      **live** (suppressed). Lock says live + no row → **live**. Row query raises + lock says live →
      still live. This is the inversion guard; it is the most important new unit test.
- [ ] Gate 5' unknown-vs-free discrimination: `_R.get` raising → `None` → no action; `_R.get`
      returning malformed JSON → `None` → no action; `_R.get` returning `None` (key absent) → not
      live → continue. These three outcomes must be distinguishable — that is the whole point of the
      round-2 BLOCKER, and a test suite that cannot tell them apart has not fixed it.
- [ ] Gate 5' secondary-row freshness: a non-terminal non-ledger row whose `updated_at` is older than
      `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES` contributes **no** liveness (the hollow-session
      guard); an equivalent fresh row does.
- [ ] Steer-target selection order: live eng session preferred over resumable, resumable preferred
      over create; ledger anchors never selected; sessions from another `project_key` never
      selected.
- [ ] Create rung: no live and no resumable eng session → `create_session` called once with
      `slug="sdlc-{issue}"`, `session_type="eng"`, and no Telegram message sent.
- [ ] Create rung lock re-read: lock becomes held between gate 5' and create → no create, no
      attempt charged, no escalation.
- [ ] Benign-race classification: `resume_session` fails but the row re-reads non-terminal → no
      attempt charged, no escalation. **Resume and create rungs only** — there is no steer
      equivalent; assert instead that a `steer_session` failure always charges an attempt.
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
  Time-box: if the lock read proves insufficient, escalate as an Open Question rather than
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
| Steering the wrong eng session (a human conversation in progress) | Medium | The steer is a short, explicit instruction naming the issue; the eng session's own routing decides. Bounded per lane by the `(slug, sha)` attempt cap, and each lane steers at most once per cooldown. |
| A steered eng session ignores the message | Medium | Attempt budget converges to escalate-once; success charges an attempt too, so an ignored steer cannot loop. |
| Lock read misreads a live lane as dead during a Redis flap | Medium | The reflection reads `session:issuelock:{N}` directly under its own `except Exception -> None`, rather than through the fail-open `touch_issue_lock` (which would report a flap as `acquired=True`, i.e. "free"). Malformed JSON is also `None`. A false "free" therefore requires Redis to return a *wrong* payload, not to fail — and `_lock_owner_is_live` fails toward "live" on every indeterminate payload branch. |
| 30-day escalation TTL hides a genuinely new problem | Low | The key is scoped by head sha; any commit re-arms it. A stalled sha is by definition the same incident. |
| Helper extraction breaks `crash_recovery` | Low | Same signature, same fail-soft semantics; covered by re-running its existing tests. |
| The create rung spawns a lane on top of a live one | **High** | Three independent guards: gate 5' lock read, a second lock read immediately before create, and `/sdlc`'s own `ISSUE_LOCKED` short-circuit inside the created session. All three fail closed — and the first two are non-fail-open by construction (direct `_R.get`, not `touch_issue_lock`), which is what makes the claim true rather than aspirational. |
| `create_session` extraction regresses `valor-session create` | Medium | The extraction moves two calls (`get_or_create_worktree`, `_push_agent_session`) behind a core and leaves argparse/printing in `cmd_create`; existing `cmd_create` tests are re-run unchanged as the regression check. |
| A created session burns tokens on an already-finished lane | Low | The lane is only reached after gates 1-4 confirm an open issue, an open non-draft PR, and a commit older than the threshold. A finished lane fails gate 1 or 3. |
| Two machines both act despite the gate | Low | `projects.<key>.machine` is the repo's declared source of truth and is validated by `bridge/config_validation.py`. |

## Step by Step Tasks

1. **Extract the ownership helper.** Move `_machine_owns_project` from
   `reflections/crash_recovery.py:99-126` to `reflections/utilities.py::machine_owns_project`,
   resolving projects.json the same way `load_local_projects` does (`PROJECTS_CONFIG_PATH` →
   Desktop → repo fallback). Update `crash_recovery` to import it and delete the private copy. Grep
   for any other reference and sweep.
2. **Replace gate 5.** Delete `_has_active_session`. Add `_lock_says_live(issue_number)` as a
   **direct `_R.get("session:issuelock:{N}")` read** classified by
   `models.session_lifecycle._lock_owner_is_live`, under the caller's own `except Exception -> None`
   — do **not** route this through `touch_issue_lock`, which fails open and cannot express
   "unknown". Then add `_lane_is_live(issue_number)` layering the secondary non-ledger
   `issue_number` session check **OR-ed for liveness only**, freshness-bounded by
   `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES` — implement the polarity exactly as the code block in
   Gate 5'. A failing row query contributes `False`, never `None`.
3. **Add gate 6.** Call `machine_owns_project(project["slug"])` in `_check_project_stalls`; skip
   silently when not the owner.
4. **Add the Redis key helpers.** `_action_cooldown_set`, `_bump_attempts`, `_escalation_set` —
   named module constants with env overrides and provisional-tunable comments. Delete
   `_dedup_set`, `_DEDUP_PREFIX`'s 6-hour alert key, `_cooldown_seconds`, and
   `_DEFAULT_COOLDOWN_HOURS`.
5. **Extract `create_session` in `tools/valor_session.py`.** Boundary: **everything from slug
   resolution onward** — slug auto-derive-or-reject, three-tier `project_key` resolution,
   `_resolve_project_working_directory`, `_derive_sdlc_metadata`, `_validate_slug` +
   `get_or_create_worktree`, `_push_agent_session` with its full payload
   (`classification_type`, `issue_url`, `project_config`, `model`, `requires_real_chrome`), and
   `_check_worker_health`. Use the signature and `CreateResult` dataclass given in "The create rung";
   convert `cmd_create`'s `return 1` failure paths into `CreateResult(success=False, error=...)`.
   `cmd_create` retains only argparse reads, printing, and exit codes. Re-run the existing
   `cmd_create` tests unchanged as the regression check — behavior must be identical.
6. **Add `_pick_steer_target(project_key)`.** Live non-ledger eng session first, then most recent
   resumable eng session with a `claude_session_uuid`, else `"create"`. Returns `(kind, session)`.
7. **Add `_attempt_action(...)`.** Compose the message, dispatch via `steer_session`,
   `resume_session`, or `create_session`, classify the outcome (benign race vs. real failure per
   "Benign races are not attempts"), charge the attempt only when not a benign race, and return a
   structured outcome carrying `kind` so telemetry can tell the rungs apart.
8. **Add the create-rung guards.** Re-read the issue lock immediately before `create_session`; a
   live owner returns a benign-race outcome. Enforce `SDLC_STALL_CREATE_MAX_PER_TICK` with a local
   counter owned by `_check_project_stalls`.
9. **Rewrite the tail of `_check_project_stalls`.** Wire the ladder: attempt budget → cooldown →
   rung selection → action → escalate on non-benign failure. Keep the canonical
   `{status, findings, summary, duration}` return shape and extend the summary with per-rung counts
   (steered / resumed / created / escalated).
10. **Add degradation markers.** Every gate that returns unknown appends a short `findings` marker
    (e.g. `"gate-unknown: lock-read"`) so a Redis or `gh` degradation is visible instead of reading
    like a healthy zero-stall tick.
11. **Rewrite `_send_alert`'s caller contract.** New message text (attempt-and-failure voice), fired
    only from the escalation path, `SET NX`-guarded. Keep the `Eng: Valor` target.
12. **Update the module docstring.** Describe the ladder (steer → resume → create → escalate once),
    the benign-race rule, and every env knob.
13. **Update the existing tests** per Test Impact, then add the new coverage listed there.
14. **Add the E2E integration test** against real Redis with a `test-`-prefixed project key and ORM
    cleanup.
15. **Rewrite `docs/features/pm-session-liveness.md`** per the Documentation section.
16. **Run `python -m ruff check` and `python -m ruff format`**, then the targeted test files via
    `scripts/pytest-clean.sh`.

## Documentation

- [ ] Rewrite `docs/features/pm-session-liveness.md` §"State-layer detection (`sdlc-progress-check`)":
      replace gate 5's description with the lock-peek liveness rule, add gate 6 (machine ownership),
      and document the action ladder.
- [ ] Delete the "Not auto-recovery… Recovery is a human decision after seeing the alert" bullet in
      §"What this is NOT" and replace it with the new boundary: *this reflection steers, resumes,
      and creates eng sessions to restart a stalled lane, but never kills anything.* Describe only
      the new status quo (CLAUDE.md principle 1) — no "formerly notification-only" note.
- [ ] Correct the alert-target drift in the same file: the target is `Eng: Valor`, not `Dev: Valor`.
- [ ] Replace the Tunables table with the full new knob set (`SDLC_STALL_THRESHOLD_HOURS`,
      `SDLC_STALL_RESUME_ENABLED`, `SDLC_STALL_RESUME_MAX_ATTEMPTS`,
      `SDLC_STALL_CREATE_MAX_PER_TICK`, `SDLC_STALL_RESUME_COOLDOWN_HOURS`,
      `SDLC_STALL_ESCALATION_TTL_DAYS`, `SDLC_STALL_ATTEMPTS_TTL_HOURS`,
      `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES`), and drop the removed `SDLC_STALL_COOLDOWN_HOURS`.
      All eight must appear; the round-2 critique caught the tunables list enumerating a subset.
- [ ] Document the create rung explicitly, including its guards — it is the one capability an
      operator needs to know the reflection has.
- [ ] `docs/tools-reference.md`: **no change required.** Verified during the round-3 revision —
      `grep -n "valor-session" docs/tools-reference.md` returns nothing, so there is no entry
      describing `valor-session create`'s internals to keep in sync. Recorded as a resolved
      check rather than a build-time judgment call; do not add a new entry for an internal
      refactor that changes no CLI surface.
- [ ] Add a cross-reference to `docs/features/session-steering.md` — steering is now a consumer
      surface of this reflection.
- [ ] Check `docs/features/README.md` for an index entry needing a description refresh.

## Open Questions

All three are settled. Recorded here as decisions rather than deleted, because each one shapes a
guard the builder must not quietly drop.

1. **When no eng session is steerable at all, does the reflection escalate or create a session?**
   **DECIDED — create a session.** Owner call, 2026-08-10. Creation is a designed third rung on the
   ladder, not a fallback flag: it is bounded by the same per-`(slug, sha)` attempt budget, gated on
   machine ownership, refuses to run while a live owner holds the issue lock, capped at
   `SDLC_STALL_CREATE_MAX_PER_TICK` per project per tick, and distinguishable in telemetry. See
   "The create rung". Escalation now means only "the system tried to create and could not".
2. **Should the steer prefer an eng session already conversing about this issue?**
   **DECIDED — no; most-recently-updated eng session for the project.** A stricter `issue_number`
   match is usually empty, and the create rung now covers the empty case properly.
3. **Is a 30-day escalation TTL right, or should the key be TTL-less?**
   **DECIDED — 30 days, env-overridable** via `SDLC_STALL_ESCALATION_TTL_DAYS`. TTL-less is the
   purest "tell a human once" but leaks keys for abandoned branches.

## Critique Results

### Round 1 (FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Five findings: 1 BLOCKER,
2 CONCERNs, 2 NITs. The blocker is the run-budget knob, flagged independently by all three critics.
All five are addressed in the Round-2 revision (2026-08-10); the "Addressed By" column names where.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (all 3) | `SDLC_STALL_RESUME_RUN_BUDGET` is specified as "Max steers per reflection tick across all projects", but `_check_project_stalls` is invoked once per project by `reflections/utilities.py::run_per_project_audit`, whose `audit_one` parameter is `Callable[[dict], dict]` (utilities.py:589) with no shared state threaded between calls. The "three keys, three lifetimes" Redis table lists no run-tick-scoped key either, so the cross-project cap has no specified mechanism, and the Test Impact item "Run-budget brake: more stalled lanes than `SDLC_STALL_RESUME_RUN_BUDGET` → only the budgeted number are steered in one tick" cannot be satisfied as written. | **Round 2** — option (a): knob dropped. See Budgets, which now states why no cross-project cap is enforceable and replaces it with `SDLC_STALL_CREATE_MAX_PER_TICK`, a per-project-tick brake enforced by a local counter (real shared state within one `_check_project_stalls` call). Removed from Budgets, Step 9, Test Impact, and the Documentation tunables list. | Pick ONE and make the Budgets table, the Redis-state table, Step 7, and the Test Impact bullet agree. (a) Drop the knob: the per-`(slug, sha)` attempt cap already bounds the worst case, and the live incident showed at most 3 concurrent stalled lanes; remove it from Budgets, from Step 7's "mirroring `stall_advisory`'s `run_state` accounting", and from the Test Impact bullet. (b) Keep it and thread run state explicitly: `run_state = {"steered": 0}; bound = functools.partial(_check_project_stalls, run_state=run_state); return run_per_project_audit(bound, ...)`, with `_check_project_stalls` checking `run_state["steered"] >= budget` before acting and incrementing on every charged attempt. A module-level global is NOT an option — reflections run in a subprocess-isolated scheduler. (c) Re-scope the knob per-project and reword both tables. |
| CONCERN | Scope & Value | Gate 5' retains "a secondary, cheap sanity check … skip if any non-ledger non-terminal `AgentSession` carries this `issue_number`" (plan:211-214), but Rabbit Holes bans that exact variant by name — "match `issue_number` … re-derives liveness from a row's existence. Do not go down this road; the lock is the authority" (plan:459-462). The plan tells the builder both to build it and never to build it, and specifies no rule for which signal wins when the lock and the row disagree. | **Round 2** — secondary check kept, polarity pinned. Gate 5' now carries an explicit "Tiebreak rule" with the OR-ed logic written out as code, and Rabbit Holes is reworded to ban row inference as the *primary authority* rather than absolutely. New unit test "Gate 5' tiebreak polarity" is the inversion guard. | If the secondary check is kept, `_lane_is_live` must OR the two signals for "live" (lock-live OR a non-terminal non-ledger row with this `issue_number`) and never let the row check override a lock-says-orphaned result into "not live" — that inversion is the permanent-false-negative mode spike-2 already ruled out for ledger anchors. Then amend the Rabbit Holes wording so the ban reads "never as the primary authority" rather than an absolute prohibition. Otherwise delete the secondary check: a Redis flap during lock acquire already fails soft to `None` (skip), which is the same posture. |
| CONCERN | Risk & Robustness | The Race Conditions table omits the cross-reflection resume race: `crash_recovery` runs every 300s and can call `resume_session` on the same terminal eng session this reflection selects. The loser of the atomic `transition_status` gets `ResumeResult(success=False)`, which under "an attempt is charged on success as well as failure" is indistinguishable from a genuine dead end — so another reflection's legitimate resume can drive this ladder to a false human escalation, the exact outcome the plan exists to prevent. | **Round 2** — generalized beyond resume into the "Benign races are not attempts" rule, applied to all three rungs with a per-rung classification table. Added to Race Conditions (3 new rows), Failure Path Test Strategy (4 new rows), and Test Impact. | In `_attempt_resume`, before charging the attempt on a `resume_session` failure, re-read the row: `fresh = AgentSession.query.filter(session_id=session.session_id).first()`; if `fresh` exists and its status is NOT terminal, classify the outcome as "already-resumed-elsewhere" — no attempt charged, no escalation, return early. Mirror `steer_session`'s re-read-before-reject pattern in `agent/session_executor.py`. Add the race to the Race Conditions table and a failure-path test row for it. |
| NIT | Risk & Robustness | Every fail-safe branch (lock-peek exception, `AgentSession.query` exception, `machine_owns_project` exception, Redis `INCR` exception) declines to act and logs only a `logger.warning`; none appends to the reflection's `findings`/`summary`. A sustained Redis or `gh` degradation would leave the ladder silently inert while the dashboard summary reads identically to a healthy day with zero stalls (`errored` stays 0 because nothing raises). | **Round 2** — adopted. Step 10 adds degradation markers; Success Criteria and Test Impact both require them. | Optional observability improvement: append a short marker finding (e.g. `"gate-unknown: lock-peek"`) whenever a gate returns `None`, so degradation trends visibly instead of looking like quiet. |
| NIT | Structural check | The plan shipped without a `## Critique Results` section. `tools/sdlc_verdict.py` requires that section to be present and populated before a non-READY verdict can be recorded, so its absence would have failed the `CRITIQUE_FINDINGS_MISSING` gate. | **Round 2** — noted; the section exists now and is populated. Template fix is out of scope for this plan. | This section was created by the critique run itself; the plan template should carry the header from creation. |

### Round 2 (FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. All five round-1 findings were
independently verified as genuinely addressed in the plan body (not merely claimed in the table) by
two of the three critics; none is re-raised. Eight new findings: 2 BLOCKERs, 3 CONCERNs, 3 NITs.
Both blockers are factual errors about the two APIs the design is built on, verified directly
against `main` @ `e051e95da` during the structural check.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, structural verification | Gate 5' assumes a lock-peek failure raises so the reflection can map it to `None` (unknown → skip). `touch_issue_lock` does not raise: it **fails open**, returning `IssueLockResult(acquired=True, ...)` on any Redis exception (`models/session_lifecycle.py:1140-1144` docstring; `:1310-1323` the `except Exception` return). The plan's mapping table reads `acquired=True` as "lock unheld → not live → **continue**", so a Redis flap during peek routes the ladder into acting rather than declining. This falsifies spike-3's "Impact if false: the gate degrades to unknown and the reflection declines to act", the Risks-table claim "A false 'free' requires Redis to return a wrong payload, not to fail", and the Failure Path row "`touch_issue_lock` raises → gate returns `None`" (untestable against the real function). It also guts two of the three guards listed against the High-severity risk "The create rung spawns a lane on top of a live one", since both gate 5' and the pre-create re-peek call the same fail-open function. | **Round 3** — adopted, option (b) per owner direction. The reflection no longer calls `touch_issue_lock` at all: `_lock_says_live` does a direct `_R.get("session:issuelock:{N}")` classified by `_lock_owner_is_live`, under the caller's own `except Exception -> None`, so "unknown" is genuinely producible and a malformed payload reads as unknown rather than free. spike-3, the Gate 5' mapping table, the Risks row, the Race Conditions rows, the Failure Path table, Step 2, Step 8 and the Data Flow diagram were all rewritten to that mechanism. The Failure Path section carries an explicit prohibition on mocking `touch_issue_lock` to raise. | `_lane_is_live` cannot distinguish "genuinely free" from "Redis errored" through `touch_issue_lock`'s return value alone. Pick one and make spike-3, the mapping table, the Risks table, and the Failure Path Test Strategy agree: (a) probe Redis health independently in the same tick before trusting an `acquired=True` peek (a cheap `POPOTO_REDIS_DB.ping()` in a `try`, or reuse the `_bump_attempts` write already made that tick as the liveness proof) and map probe-failure to `None`; (b) call a non-fail-open read directly instead of `touch_issue_lock` — `_R.get(f"session:issuelock:{issue_number}")` plus `_lock_owner_is_live(json.loads(raw))`, with the caller's own `except Exception -> None`, which is what "unknown → skip" actually requires; or (c) accept the residual risk explicitly in the Risks table and rewrite the Failure Path row to the behavior the code can produce. Do NOT write a test that mocks `touch_issue_lock` to raise. |
| BLOCKER | Risk & Robustness, Scope & Value (both flagged the create rung) | The create-rung mechanism asserts `cmd_create`'s real work "is only two steps — `get_or_create_worktree` and `_push_agent_session`". `cmd_create` does substantially more before those calls: slug auto-derivation-or-reject (`tools/valor_session.py:520-536`), three-tier `project_key` resolution (`:546-568`), `_resolve_project_working_directory` (`:577`), `_derive_sdlc_metadata` producing `classification_type` and `issue_url` (`:578`), and `_validate_slug` (`:583`) — and `_push_agent_session` is called with `classification_type`, `issue_url`, `project_config`, `model`, and `requires_real_chrome` (`:593-608`). The plan's `create_session(*, project_key, slug, message, session_type, ...)` sketch names none of them, so the extraction as scoped either drops them (behavioral drift that directly violates the Success Criterion "`valor-session create` behaves identically after the `create_session` extraction") or leaves the builder to re-derive the boundary mid-build with no spec. | **Round 3** — adopted. "The create rung" now states the boundary as *everything from slug resolution onward*, with a nine-row table classifying each step of `cmd_create` as moves/stays, the full `create_session` signature plus a `CreateResult` dataclass, and a note that `return 1` paths become `success=False` results. Step 5 restated to match. `_derive_sdlc_metadata` was executed live against the real `valor` config on the actual steer text: it returns `('sdlc', 'https://github.com/tomcounsell/ai/issues/2696')` — correct, not a mis-classification — and the plan forbids any test asserting `(None, None)`. | State the extraction boundary as "everything from slug resolution onward" and name the full payload in `create_session`'s signature: `classification_type`, `issue_url`, `project_config`, `model`, `requires_real_chrome`. `cmd_create` keeps only argparse reads, `print`, and exit codes. Separately verify `_derive_sdlc_metadata(message, project_config)` degrades to `(None, None)` on the reflection's stall-instruction text rather than mis-classifying, since that text is not a normal create message. |
| CONCERN | Scope & Value | The create rung solves a scenario the reported incident never exhibited: spike-5 found five resumable eng sessions present at the sampled moment, so steer + resume + the separated escalation key already closes the observed defect. The rung the plan itself calls "the widest-blast-radius capability in this change" was an unresolved Open Question in round 1 and is now decided into scope, carrying a new cross-module core extraction, a five-row guard table, a new brake knob, three race rows, and five-plus new tests. | **Round 3** — COST observation adopted; the split-out recommendation is **overruled on the decision**. The owner explicitly decided the create rung stays after being shown this exact tradeoff, and separately confirmed that PM sessions may create and orchestrate as many eng sessions as they want. The guards on the rung are justified by "unbounded RETRY is dangerous" (a reflection minting a fresh session every 30 minutes because the last one did not move the PR), not by "session creation is dangerous". The cost half of the finding stands and is paid: appetite raised **Medium -> Large** with an itemized justification naming both extractions and the `cmd_create` rewrite. | If the rung stays, `appetite: Medium` must be re-justified against two cross-module extractions plus a new session-provisioning capability. If it is split out, delete "The create rung", the `SDLC_STALL_CREATE_MAX_PER_TICK` knob, Step 5, Step 8, the two create rows in Race Conditions, the four create rows in Failure Path Test Strategy, and the create bullets in Success Criteria and Test Impact, and reopen Q1 as the follow-up issue's subject; rung 3 then becomes escalate-once. |
| CONCERN | History & Consistency | The plan states "every threshold is a named module constant with an env override and a comment marking it tunable", but the attempts-key TTL is a bare "24 h" in the Redis-state table with no knob in Budgets or the Documentation tunables list (which enumerates exactly six knobs and omits it). | **Round 3** — adopted. `SDLC_STALL_ATTEMPTS_TTL_HOURS` (default 24) added to the Redis-state table, the Budgets table, and the Documentation tunables list, with the invariant restated under Budgets as a build-time rule: no bare literal thresholds survive in the module. | `_bump_attempts` reads its TTL via a `_DEFAULT_STALL_ATTEMPTS_TTL_HOURS = 24` module constant with a provisional-tunable comment and an `os.environ.get("SDLC_STALL_ATTEMPTS_TTL_HOURS", ...)` override, mirroring the existing `_threshold_seconds()` float-then-int-hours pattern at `reflections/sdlc_progress.py:56-61`. Add it to the Budgets table and the Documentation tunables list. |
| CONCERN | History & Consistency | The steer rung's benign-race branch ("re-read row exists and is non-terminal with a fresher `updated_at`") has no reachable failure path in the real `steer_session`, whose only `success=False` returns are empty message, session not found, terminal status (`agent/session_executor.py:827-832`), and `is_ledger` (`:841-849`). A still-live non-ledger target simply succeeds. The corresponding Failure Path row is therefore only satisfiable by mocking a failure shape the function never emits, and the one real steer race (target goes terminal) is already classified non-benign in the same plan. | **Round 3** — adopted, first option: the steer row is dropped from the benign-race table and the corresponding Failure Path row is gone. Verified against `main` — a live non-ledger steer target simply **succeeds**, so the branch is unreachable. The plan now says so explicitly and forbids mocking that shape; a `steer_session` failure always charges an attempt. Test Impact updated to assert exactly that. | Either drop the steer row from the benign-race table and the matching Failure Path row, keeping the classifier for the resume and create rungs only, or name the concrete mechanism that makes `steer_session` fail while the target stays non-terminal. The re-read itself is `AgentSession.query.filter(session_id=...).first()` mirroring `session_executor.py:827-832`; the builder should confirm the live `steer_session` against `main` before building a double for a dead branch. |
| NIT | Scope & Value | Appetite says "one shared helper extracted" (singular), but the round-2 revision performs two extractions across two modules: `machine_owns_project` out of `reflections/crash_recovery.py` (Step 1) and `create_session` out of `tools/valor_session.py::cmd_create` (Step 5), the latter rewriting an independently-tested CLI entry point. | **Round 3** — adopted, and superseded by the appetite raise. The Appetite section now names Extraction A (`machine_owns_project`) and Extraction B (`create_session`, "the expensive one") separately and calls out that Extraction B rewrites an independently-tested CLI entry point. | Text-only: amend Appetite to name both extractions and the `cmd_create` rewrite. |
| NIT | Risk & Robustness | `resume_session` pushes the steering message unconditionally (`tools/valor_session.py:856`) before the `transition_status` call that can fail, so on a benign-race resume the reflection's stall instruction has already landed on `steering:{session_id}` and is drained into the session another actor resumed. Harmless duplicate instruction, but "no attempt, no escalation, return early" reads as "no side effect". | **Round 3** — adopted as an accepted side effect. New Race Conditions row states that on a benign-race resume the stall instruction has already landed on `steering:{session_id}`, that "no attempt, no escalation" means no budget consumed and no human paged rather than no side effect, and that making the push conditional would change `resume_session`'s contract for all callers — outside this plan's boundary. | Note it in the Race Conditions table as an accepted side effect. Making the push conditional would require changing `resume_session`, which is outside this plan's boundary. |
| NIT | History & Consistency | Success Criteria asserts "`valor-session create` behaves identically after the `create_session` extraction", but the Documentation checklist only conditionally notes the new core in `docs/tools-reference.md` "if `valor-session` has an entry describing its internals", leaving a build-time judgment call with no fallback. | **Round 3** — adopted, resolved rather than deferred. `grep -n "valor-session" docs/tools-reference.md` returns nothing, so no entry exists; the Documentation item is now an unconditional "no change required" with that verification recorded, not a build-time judgment call. | Grep `docs/tools-reference.md` for a `valor-session create` section; make the item unconditional or state "if no entry exists, none is required". |


### Round 3 revision (applied 2026-08-10)

All eight round-2 findings are dispositioned in the "Addressed By" column above. Seven were adopted
as recommended. One — the Scope & Value CONCERN proposing the create rung be split out — had its
**cost observation adopted and its recommendation overruled by owner decision**, recorded here rather
than silently: the create rung stays, and the appetite was raised from Medium to Large instead.

Two facts verified against `main` during this revision, both of which changed the design rather than
merely confirming it:

1. **`touch_issue_lock` fails open, it does not raise.** The entire "unknown → skip" posture the
   design rests on was unreachable through it. Gate 5' now reads the lock key directly. This is the
   round-2 BLOCKER and the single largest change in this revision.
2. **Hollow sessions are real, not hypothetical.** Four `sdlc-local-*` rows were observed reporting
   `running` with no heartbeat and no fence. This constrains two places the earlier draft left open:
   Gate 5's secondary row check is now freshness-bounded, and the create rung carries an explicit
   requirement that sessions it creates be able to reach a terminal status.

### Round 3 (FULL depth)

Critics: Risk & Robustness, Scope & Value, History & Consistency. Roster gate 3/3 complete, 0
ungrounded. All eight round-2 findings were independently verified as genuinely addressed in the plan
**body** (not merely claimed in the table) by two of the three critics; none is re-raised. The owner's
overrule of the round-2 create-rung split-out recommendation was not re-litigated.

Five findings: **0 BLOCKERs**, 3 CONCERNs, 2 NITs. Every code claim below was re-verified by the
aggregator against `main` @ `f67a62f76` before recording. No finding blocks the build; each carries a
concrete Implementation Note the builder applies in place.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (filed BLOCKER; downgraded on verification — see note below the table) | The create-rung boundary table presents itself as an exhaustive read of `cmd_create` but never mentions the **#1633 child-session stopgap** at `tools/valor_session.py:481-497`: when `parent_id` is truthy it calls `child_sessions_allowed()` and `return 2`s, firing *before* slug resolution. Two consequences. (a) The prose rule "`cmd_create` keeps **only** argparse reads, printing, and exit codes" is factually wrong — a policy gate also remains. (b) `create_session`'s signature accepts `parent_id`, so the new programmatic core is an **ungated bypass** of an active stopgap: a caller passing `parent_id` skips a refusal the CLI enforces. The reflection itself never passes `parent_id`, so the live blast radius is zero today; the hole is latent. | pending | Add a row to the boundary table for `tools/valor_session.py:481-497` classified **stays in `cmd_create`** (it is already inside the table's existing `:423-504` "stays" range — the CLI path is NOT broken, which is why this is not a blocker), and amend the prose rule to "argparse reads, the #1633 parent gate, printing, and exit codes". Then close the bypass: in `create_session`, when `parent_id` is truthy, re-run `from models.child_session_gate import child_sessions_allowed, CHILD_SESSIONS_DISABLED_MESSAGE` and return `CreateResult(success=False, error=CHILD_SESSIONS_DISABLED_MESSAGE)` before any slug or project_key resolution. Add one Failure Path row asserting that. |
| CONCERN | Risk & Robustness | The attempts budget specifies exactly one helper — `_bump_attempts`, an `INCR` (Redis-state table; Step 4) — but the ladder's pre-action gate `attempts >= MAX -> escalate once, stop` runs **before** a rung is picked and must therefore read the counter **without** incrementing it. No read-only helper is specified anywhere (Redis-state table, Step 4, Step 9), and the single Failure Path row calls the check itself an `INCR`. Built literally, a lane sitting in cooldown charges an attempt every tick and exhausts `SDLC_STALL_RESUME_MAX_ATTEMPTS` from cooldown-skips alone, forcing a false escalation without the ladder ever dispatching a real action — the exact "escalate to a human for nothing" outcome this plan exists to delete. | pending | Split the two operations explicitly. `_attempts_count(slug, sha) -> int` is a bare `GET` on `sdlc:stall:resume:attempts:{slug}:{sha}` (absent key = 0), consulted only by the pre-action gate; `_bump_attempts(slug, sha)` stays the `INCR` + TTL-refresh charge, invoked only from the post-classification "charge an attempt" step. The two have different failure semantics and need separate Failure Path rows: a **read** failure maps to "cannot prove we are under budget -> decline to act this tick"; a **charge** failure cannot un-dispatch an action already taken, so it logs a `findings` marker and must NOT itself trigger an escalation. |
| CONCERN | History & Consistency | Under "The steer rung has no benign-race branch, deliberately", the plan asserts as a round-3-verified fact that "`steer_session`'s **only** `success=False` returns are empty message, session not found, terminal status, and `is_ledger`". Verified against `main`: there is a fifth. `agent/session_executor.py:884-886` is an outer `except Exception` wrapping both the `AgentSession.query.filter` at `:836` and `push_steering_message` at `:873`, and it returns `success=False` for a target that can remain **live and non-terminal** (e.g. a Redis flap during the push). The design conclusion is unaffected — that branch is correctly non-benign — but an inaccurate "only" enumeration is precisely the class of unverified-API assertion that produced both round-2 BLOCKERs on this plan, and a builder trusting it could assert unreachability in a test. | pending | Amend the sentence to enumerate five `success=False` paths, adding the outer `except Exception` at `agent/session_executor.py:884-886`, and state that it is still classified **non-benign** (a query or push error is a real failure, not "another actor got there first"). The existing Failure Path row "`steer_session` returns `success=False` (any cause) -> attempt charged" and the Test Impact assertion both stand unchanged — fold the fifth branch into that row rather than treating it as a gap. No test may assert this branch is unreachable. |
| NIT | Scope & Value, History & Consistency (both) | The `create_session` signature declares `chat_id: int = 0`, but `cmd_create` always carries a **string** (`chat_id = args.chat_id or "0"`, `tools/valor_session.py:472`) and `_push_agent_session` types the parameter `chat_id: str` (`agent/agent_session_queue.py:281`). This contradicts the plan's own boundary claim that the payload is "passed to `_push_agent_session` exactly as `cmd_create` passes them today". Harmless at the reflection's default call site (`0` and `"0"` format identically into `session_id = f"{chat_id}_{ts_suffix}"`), but a caller passing a real chat id as an int diverges from the CLI's always-string type. | pending | One-line signature change: `chat_id: str = "0"`. |
| NIT | Scope & Value | Gate 5' defines `_LOCK_KEY = "session:issuelock:{issue_number}"` as a module constant, then never uses it — the code sample directly below hardcodes the same pattern inline, and Data Flow, Risks, and Step 2 all repeat the literal string. The constant is dead illustrative code that visually promises to be the single source of truth for the key format while nothing references it. | pending | Either delete `_LOCK_KEY` from the sample and keep the single inline f-string, or make the sample use it: `_R.get(_LOCK_KEY.format(issue_number=issue_number))`. No other section needs to change either way, since every other mention already uses the literal rather than the constant name. |

**One severity deviation, named rather than applied silently.** Risk & Robustness filed the #1633
child-session gate as a BLOCKER on the reasoning that "nothing tells the builder to move it into the
core, and it cannot stay under the stated rule either", so the gate would be dropped entirely. On
verification that reasoning does not hold: the boundary table's first row already spans `:423-504`
and classifies that range as **stays in `cmd_create`**, and `:481-497` falls inside it — so
`valor-session create` keeps the gate and the Success Criterion "behaves identically" is not
violated. What survives is a prose inaccuracy plus a latent ungated `parent_id` path on the new core,
which is a CONCERN. Recorded here so the downgrade is auditable rather than invisible.
