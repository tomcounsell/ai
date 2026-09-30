# Autonomous SDLC pickup on `upvote`-labeled issues

Issue #2717. Start-half sibling to `sdlc-stall-auto-resume`
(`reflections/sdlc_progress.py`): that reflection unsticks lanes that
already exist; `reflections/sdlc_upvote_lanes.py` starts lanes that do not
exist yet, on a schedule, with no further human input beyond adding the
`upvote` label.

## The `upvote` contract

A human adds the `upvote` label to an issue (already documented as
"Pre-approved for autonomous SDLC pickup" in the GitHub label itself, and in
`CLAUDE.md`'s Issue Labels table). Within the next scheduled tick, the
reflection creates an Eng session in that project's `Eng: X` Room and lets
`/sdlc` route the appropriate stage. The session's brief tells it to announce
the pickup in its first message, which reaches the human through the persona
path.

The reflection never mutates the label and never closes the issue. `upvote`
is a human-owned signal in both directions, and it remains attached after
pickup as a record of what was auto-approved.

## Schedule

Registered as `sdlc-upvote-pickup`, `cron: 0 6-22/2 * * *; tz=America/Los_Angeles`
(every two hours, 06:00-22:00 Pacific), `priority: low`, an explicit
`timeout:` derived from `reflections.sdlc_upvote_lanes.UPVOTE_ENTRY_TIMEOUT_S`.
It is the first cron-scheduled entry in the registry — every prior entry
uses `every:`.

**Kill switch:** `SDLC_UPVOTE_PICKUP_ENABLED=false` disables the reflection
at the top of the entrypoint without touching config.

## Registration lives in code, not `config/reflections.yaml`

`config/reflections.yaml` is gitignored and clobbered from
`~/Desktop/Valor/reflections.yaml` (the vault) on every `/update`. A
hand-edit to it never ships and never survives the next sync. Registration
instead runs through `scripts/update/reflection_register.py`'s
`register_sdlc_upvote_pickup`, wired into `scripts/update/run.py`'s
registration block (Step 1.658) — before Step 1.66's vault→config copy, so
the appended entry propagates into every machine's `config/reflections.yaml`
on the same `/update` cycle. **`config/reflections.yaml` must never be
hand-edited for this reflection.**

**Post-`/update` operator check** (run on any machine after the first
`/update` that carries this change):

```bash
python -c "from agent.reflection_scheduler import load_registry; e=[x for x in load_registry() if x.name=='sdlc-upvote-pickup']; assert e, 'entry missing or skipped as invalid'; assert not e[0].validate(), e[0].validate(); print(e[0].schedule, e[0].effective_timeout())"
```

Expected: `cron: 0 6-22/2 * * *; tz=America/Los_Angeles 1500`. An empty list
means the entry was either never registered or was silently skipped as
invalid by `load_registry` (which logs a warning and skips, rather than
raising, on a malformed entry) — the most likely way this feature ships
silently inert.

## Scope gate

Per project, in order: `machine_owns_project(slug)` (single-machine
ownership, CLAUDE.md) → `resolve_eng_group(project)` (a project with no
properly-configured `Eng: X` group, e.g. `royop` at plan time, is skipped) →
a resolvable `github.org`/`github.repo`. Any miss returns a `skipped` status
with zero subprocess calls beyond what was needed to check.

## The six skip gates, in evaluation order

Cheapest and most decisive first; any gate that cannot answer confidently
skips (fail closed — a duplicate lane is strictly worse than a delayed one,
and the next tick is only two hours away).

| # | Gate | Skip when |
|---|------|-----------|
| 1 | Session exists | a non-terminal `AgentSession` with `slug == sdlc-{N}` **and** matching `project_key` exists |
| 1.5 | Recent create failure | `upvote:pickup:failed:{repo}:{N}` exists (this reflection's own clock-expiring backoff key) |
| 1.6 | Lane started then died | a terminal-FAILED `AgentSession` with the same slug+project_key, newer than `UPVOTE_FAILURE_BACKOFF_S` |
| 2 | Ledger written | `PipelineLedger.get(org/repo, N)` carries any recorded stage state |
| 3 | Lock live | the issue lock (`_lock_says_live`, shared with `sdlc_progress`) is `True` or unknown |
| 4 | Branch has a PR | `gh pr list --head session/sdlc-{N} --state all` is non-empty (`--state all` deliberately — a **merged** PR on a still-open issue means the implementation PR lacked `Closes #N`, and this gate reports it as a finding rather than restarting the lane forever) |

Gates 1 and 1.6 match on **both** `slug` and `project_key`, because
`AgentSession.slug` is a global key and two repos can share an issue
number. A non-terminal row with a different `project_key` is a
cross-project collision, not a reason to skip — the reflection reports it as
a finding and proceeds (the per-project and machine-wide ceilings below
already bound concurrency).

## Ordering — oldest first, server-side

Candidates are fetched with `gh issue list --search sort:created-asc`
(server-side sort, applied before `--limit`), not sorted client-side after
truncation — a client-side sort of an already-newest-first-truncated page
would starve the oldest issue above the scan cap. `UPVOTE_CANDIDATE_SCAN_MAX`
is the single truncation knob (default 10); there is no second post-sort
slice.

## Concurrency ceilings

- `UPVOTE_LANE_MAX_LIVE` (default 3, per project) — counted from open
  `session/sdlc-*` PRs only (`_count_live_lanes`); deliberately an
  undercount, not a lock-inclusive total.
- `UPVOTE_LANE_MAX_LIVE_MACHINE` (default 5, machine-wide) — accumulated
  from each project's already-computed live count as the sweep proceeds,
  at zero extra `gh` calls.

**Machine-wide implication, stated plainly for tuning:** with
`UPVOTE_LANE_MAX_LIVE=3` and `len(load_local_projects())` projects on a
machine, the per-project ceiling alone would permit up to
`3 × project_count` concurrent auto-started lanes — each a `claude -p`
subprocess plus a worktree plus a worktree-local `.venv`. The machine-wide
ceiling caps the aggregate at `UPVOTE_LANE_MAX_LIVE_MACHINE` regardless.
Both are provisional, env-overridable starting guesses
(`UPVOTE_LANE_MAX_LIVE`, `UPVOTE_LANE_MAX_LIVE_MACHINE`), not measured
optima — a project that wants a higher ramp raises the per-project number;
a machine that is memory-constrained lowers the machine-wide one.

## Per-tick time budget

The reflection worker's `_reflection_pool` cannot cancel a wedged sync
callable, so every wait is bounded and the uninterruptible `create_session`
call is gated before it starts rather than after:

- `UPVOTE_RUN_BUDGET_S` (default 1200s) bounds the whole run, enforced by
  **early return** from the per-project callable — `run_per_project_audit`
  owns the loop, so the callable can neither `break` nor abort by raising.
  `UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S < UPVOTE_ENTRY_TIMEOUT_S` (both
  asserted by test) keeps the scheduler's own timeout from ever firing
  against a tick that is still legitimately running.
- `UPVOTE_PICKUP_WORST_CASE_S` (740s with today's defaults) is the whole
  uninterruptible tail of one pickup: two `gh` calls
  and `create_session`'s cold-worktree `uv sync`
  (`UPVOTE_CREATE_WORST_CASE_S = settings.timeouts.uv_sync_s +
  settings.timeouts.git_subprocess_s` — **derived**, never a fresh literal,
  so a `TIMEOUTS__UV_SYNC_S` override cannot silently invalidate the
  arithmetic). A pickup is admitted only when the remaining budget covers
  the whole worst case (`UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S`,
  also asserted by test); otherwise the project defers with a finding and
  announces nothing.
- **Practical rate:** at 1200s of budget and 740s per pickup, a single tick
  realistically admits one cold pickup, occasionally two. Nine ticks a day
  still permits up to nine new lanes machine-wide — well above the
  machine-wide ceiling. This is an accepted rate limit; the lever is
  `UPVOTE_RUN_BUDGET_S` (and the entry timeout, which must stay above it),
  not removing the admission check.

## Create-then-announce through the persona

The reflection sends nothing to Telegram. It creates the Eng session in the
project's `Eng: X` Room (resolved by numeric `chat_id`) with no reply anchor;
the brief asks the session to announce the pickup in its first message. That
message goes through the drafter like every other agent message, so the
words the human reads are the persona's. See
[Reflection Agent Handoff](reflection-agent-handoff.md).

**Create failure** writes a clock-expiring backoff key
(`upvote:pickup:failed:{repo}:{N}`, `SETEX`/`GET` on a plain non-Popoto
string namespace, not a claim key) and records a finding for the operator
surface. There is no announcement to retract, so no message goes to the human.

## Scratch-issue dry run (merge-time verification record)

**DEFERRED as of 2026-08-11, tracked as #2722 — do not read this as "done"
or as an unfilled placeholder still awaiting an answer; it is a considered
deferral with a concrete reason and a concrete unblock path.**

**Machine-ownership check (performed):** this machine (`Valor the Cowboy`)
is the `valor`-owning machine per `projects.<key>.machine` in
`~/Desktop/Valor/projects.json`, so the dry run was *not* deferred for lack
of ownership.

**Reason it could not actually run here, pre-merge:** the anchoring feature
this dry run exists to verify is split across two runtime boundaries, and
only one of them can run this PR's code before merge.
`reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes()` can be invoked
directly from this worktree's own `.venv` (it has this branch's code), and
it *would* enqueue a real Telegram send — but the send itself, and the ack
write `publish_sent_message_id` in `bridge/outbox_ack.py`, only execute
inside the **already-running production `bridge/telegram_bridge.py`
process** (the sole owner of the Telethon session; `tools/valor_telegram.py`
deliberately never opens its own Telethon client, to avoid the SQLite
session-lock conflict this fact implies). That production process runs from
the separate main checkout at `/Users/valorengels/src/ai`, currently at
`main`'s HEAD, which does **not** contain this PR's `bridge/outbox_ack.py`
or the `telegram_relay.py` ack-write call (verified: neither file/hunk
exists on `main` as of this patch). Restarting the production bridge and
reflection-worker services (as `## Update System` / Task 7 describe) restarts
them on unmerged main -- it does not deploy this branch's code, because
neither launchd plist points at this worktree. So today, on this machine,
before merge: the announcement would send (the base send path is unchanged),
but `await_sent_message_id` would always time out and return `None`/`0`,
producing a false-negative result that looks like a broken feature rather
than an unexercised one. Running a second, independent Telethon client from
the worktree to sidestep this is not an option either -- it would conflict
on the single `data/*.session` file the production bridge already locks.
Deploying this PR's code into the live bridge pre-merge (checking out this
branch into the main checkout, or fast-forwarding main) is out of scope for
a PATCH-stage fix: it would put unreviewed code into the process actually
serving the real `Eng:` groups and is squarely a MERGE-stage action, not a
patch one.

**Exact steps to close this out (post-merge, on the `valor`-owning
machine):**
1. Merge this PR, then run `/update` (propagates the merged code) followed
   by `./scripts/valor-service.sh restart` (bridge/watchdog/worker) and a
   `com.valor.reflection-worker` restart — both processes must restart for
   anchoring to work at all (see "Restart required after merge" above).
2. Verify both processes are actually on the merged SHA (`tail -5
   logs/bridge.log` shows "Connected to Telegram"; check the reflection
   worker's log for its startup banner).
3. Open a throwaway issue in `tomcounsell/ai` and label it `upvote`.
4. Set `SDLC_UPVOTE_PICKUP_ENABLED=false` in the reflection worker's
   environment for the duration of the run (so the scheduled tick cannot
   race the manual invocation), then in a foreground shell where the var is
   unset: `.venv/bin/python -c "from reflections.sdlc_upvote_lanes import
   run_sdlc_upvote_lanes; print(run_sdlc_upvote_lanes())"`.
5. Assert by eye and by record: the announcement appears in `Eng: Valor`;
   the created session's persisted `telegram_message_id` is non-zero and
   equals that message's id; the lane's first outbound message renders as a
   reply under the announcement.
6. Remove the `upvote` label and close the scratch issue **before** killing
   the created session (kill-first re-opens the scratch issue as a live
   candidate for the next scheduled tick); delete the test `AgentSession`
   via the ORM (`instance.delete()`), not raw Redis.
7. Replace this section with the observed outcome: issue number, Telegram
   message id, session id, and the confirmed `telegram_message_id` value.

This defers the *verification record*, not the code: every mechanical proxy
row (the `telegram_message_id` → `TELEGRAM_REPLY_TO` plumbing tests,
`resolve_eng_group`, the ack primitive's own unit tests) is implemented and
passing, per `## Success Criteria`. What remains unverified until the steps
above run post-merge is the live, cross-process integration the plan's
own text warns the mechanical proxies cannot substitute for.

## No-Gos / deferred

- **No claim key, no label mutation, no reflection-side stage selection, no
  issue closing, no message editing or deletion.** See the module docstring
  and the plan's `## No-Gos` for the full rationale on each.
- **Adding `valor-session` to `[project.scripts]`.** Filed as #2724 — a
  repo-wide entrypoint decision with its own stale-shim exposure (#2566),
  out of scope here.
- **Retrofitting `agent/session_executor.py`'s bespoke drain-poll onto the
  new ack primitive.** Filed as #2723. Worth doing, deliberately not here —
  the primitive should prove itself on one low-traffic consumer first.

## Related

- [`reflections/sdlc_progress.py`](../../reflections/sdlc_progress.py) — the
  recovery-half sibling.
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — the outbox
  and relay this feature's ack primitive sits alongside.
- [Eng Session Architecture](eng-session-architecture.md) — this reflection
  is a third origin for Eng sessions (human message, `sdlc_progress`
  recovery, and now autonomous pickup).
