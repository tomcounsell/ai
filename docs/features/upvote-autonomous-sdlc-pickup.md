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
reflection announces the pickup in that project's `Eng: X` Telegram group,
creates an Eng session anchored to that announcement, and lets `/sdlc` route
the appropriate stage.

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
  `session/sdlc-*` PRs plus live issue locks on candidates.
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
  uninterruptible tail of one pickup: two `gh`/send calls, the anchor wait,
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

## Announce-then-create, and the ack primitive

**Ordering:** announce first, capture the sent message id via a new relay
ack, then create — chosen over create-then-announce (loses the anchor) and
create-then-patch (races the worker's session claim against the anchor
write). The only failure mode is a phantom promise (announcement lands,
`create_session` fails); it is handled explicitly with a threaded
retraction, not tolerated silently.

**The ack primitive (`bridge/outbox_ack.py`):** `tools/valor_telegram.py`'s
`send` never learned the Telegram message id it produced — it only enqueues
onto `telegram:outbox:{session_id}` and returns. `bridge/outbox_ack.py` is a
new **leaf** module (imports only the shared Redis client, no Telethon) that
owns `telegram:sent:{session_id}` as a single-consumer, delete-on-read Redis
list with a short TTL: `publish_sent_message_id` (relay-side writer,
opt-in — gated on the outbox payload's `ack_sent_id` flag, so ordinary
traffic is unaffected) and `await_sent_message_id` (producer-side reader, a
bounded blocking poll). It is a leaf specifically so the reflection process,
which per design has no Telegram client, never gains one transitively by
importing it.

**Sending goes through the one existing sender** —
`[sys.executable, "-m", "tools.valor_telegram", "send", …]`, invoked as a
module and never as the bare `valor-telegram` console script (the on-PATH
shim is stale on this machine, issue #2566, and crashes on import outside
the launchd-pinned PATH). This keeps the promise gate, linkify, and the
4096-char guard on the one send path that already owns them; the reflection
builds no `telegram:outbox:*` payload of its own. Every send carries
`--no-read-the-room` (an inherited `VALOR_SESSION_ID` would otherwise arm
Read-the-Room, whose `suppress` verdict enqueues a *reaction* instead of the
message and still exits 0) and an explicitly scrubbed subprocess `env`
(`VALOR_SESSION_ID`, `TELEGRAM_REPLY_TO`, `AGENT_SESSION_ID` all removed —
an inherited `TELEGRAM_REPLY_TO` would thread the announcement into an
unrelated message in a different chat). Exit 0 means "enqueued", never
"delivered"; only the ack confirms delivery.

**Two bounded degradations, both visible in the findings, never silent:**

- **Ack timeout** (`UPVOTE_ANCHOR_WAIT_S`, default 20s): the lane still
  starts, unanchored (`telegram_message_id=0`), and the finding reads as an
  *unconfirmed delivery*, not a cosmetic threading miss — the send may not
  have landed at all.
- **Create failure**: writes a clock-expiring backoff key
  (`upvote:pickup:failed:{repo}:{N}`, `SETEX`/`GET` on a plain non-Popoto
  string namespace — not the raw-Redis-on-Popoto-keys rule, and not a claim
  key) and posts a threaded retraction. The retraction argv is built
  unconditionally with `--reply-to` spliced in **only when the anchor is
  truthy** — both degradations can co-occur (ack timeout *and* create
  failure), and the anchor can legitimately be `0`. The retraction uses a
  distinct `{producer_id}-retract` id so its own ack bookkeeping never
  cross-talks with the announcement's.

## `create_session(telegram_message_id=...)`

`tools/valor_session.py::create_session` gained `telegram_message_id: int =
0`, replacing the literal `0` previously hardcoded at the
`_push_agent_session` call site. Purely additive — every existing caller's
behavior is unchanged. Once the worker starts the session,
`agent/sdk_client.py` exports it as `TELEGRAM_REPLY_TO`, so every outbound
message from the lane threads under the announcement.

**CLI debugging path** (no `valor-session` console script ships —
`pyproject.toml`'s `[project.scripts]` has no such entry despite CLAUDE.md's
docstring examples assuming one; see `## No-Gos` follow-up below): a human
reproduces an anchored start by hand with

```bash
.venv/bin/python -m tools.valor_session create --telegram-message-id <id> --chat-id <eng chat id> --role eng --message "…"
```

This exercises the `create_session` plumbing only — not `resolve_eng_group`,
not the send path, not the relay ack. It is not a substitute for the
scratch-issue end-to-end dry run below.

## Restart required after merge

Both processes must restart for anchoring to work at all: the bridge writes
the ack (`bridge/telegram_relay.py` → `bridge/outbox_ack.py`), the
reflection worker reads it. If only one restarts, anchoring silently
degrades to `telegram_message_id=0` — the lane still starts, but nothing
threads. After merge: `/update` → `./scripts/valor-service.sh restart` plus
a reflection-worker restart.

## Scratch-issue dry run (merge-time verification record)

At merge time there were zero open `upvote` issues in `tomcounsell/ai`
(Freshness Check), so the human-visible "one thread per issue" criterion
cannot be verified by waiting for a real pickup. It was instead verified by
a throwaway `upvote`-labeled scratch issue, both processes restarted,
`run_sdlc_upvote_lanes()` invoked directly on the machine owning `valor`.
*(Record the observed outcome and message id here once that run has
happened; do not leave this section unfilled at merge.)*

## No-Gos / deferred

- **No claim key, no label mutation, no reflection-side stage selection, no
  issue closing, no message editing or deletion.** See the module docstring
  and the plan's `## No-Gos` for the full rationale on each.
- **Adding `valor-session` to `[project.scripts]`.** Recorded as a
  follow-up issue at merge time — a repo-wide entrypoint decision with its
  own stale-shim exposure (#2566), out of scope here.
- **Retrofitting `agent/session_executor.py`'s bespoke drain-poll onto the
  new ack primitive.** Worth doing, deliberately not here — the primitive
  should prove itself on one low-traffic consumer first.

## Related

- [`reflections/sdlc_progress.py`](../../reflections/sdlc_progress.py) — the
  recovery-half sibling.
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — the outbox
  and relay this feature's ack primitive sits alongside.
- [Eng Session Architecture](eng-session-architecture.md) — this reflection
  is a third origin for Eng sessions (human message, `sdlc_progress`
  recovery, and now autonomous pickup).
