---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3270
last_comment_id: 5601939922
---

# Telegram: stop the unprompted repeat replies, and make replies match the room

## Problem

Two independent defects make Valor a bad chat participant, and a human named both of them directly.

**A. Extra messages nobody asked for (#3270).** Finished Telegram conversation threads get re-run on a ~30-minute cadence and each re-run posts a new reply. Two auto-recovery loops feed each other: the SDLC stall check resumes a slugless conversation thread on behalf of an unrelated stalled engineering lane, and the session-health orphan net re-queues the resulting row because the row's status was silently corrupted by a full-row write.

**B. Replies that don't fit the room (#3271).** In chat `-1003449100931` (241 rows) human messages run a median of 8 words on 1 line with no headers or lists. Valor's agent-composed replies run a median of ~247 words; 17 of 70 use `#` headers, 18 use numbered lists, and the longest is 1029 words in reply to a 17-word question. The human's words: *"Don't send me messages like this. I'm not your log file."*

The two are related only in symptom — both make Valor look like it is talking at the room rather than with it. They touch disjoint file sets and are planned together so they ship as one behavior fix.

### Current behavior, A

The chain, established from the `session_events` ledger of `tg_valor_-1003449100931_1450` (append-only, and therefore independent of the corrupted `status` field):

```
13:23:32  running→completed: transcript completed        <-- the ONLY terminal transition ever recorded
14:21:33  completed→pending: resume (sdlc-stall)
14:21:33  pending→running
15:02:47  exit_summary turns=3                           <-- run ended, NO terminal transition
15:07:33  running→pending: health check (attempt 1, kind=no_progress)
15:08:15  exit_summary turns=1                           <-- NO terminal transition
15:12:33  running→failed: 2 recovery attempts
15:21:41  failed→pending: resume (sdlc-stall)
15:28:44  exit_summary turns=1                           <-- NO terminal transition
15:32:34  running→failed: 3 recovery attempts
16:21:46  failed→pending: resume (sdlc-stall)
16:43:40  exit_summary turns=1                           <-- NO terminal transition
16:47:38  running→failed: 4 recovery attempts
```

Every resumed run finishes its turn and is never finalized. Four distinct defects combine:

1. **Rung 2 of the stall ladder resumes anything.** `reflections/sdlc_progress.py:808-858` `_pick_steer_target` collects every row with `status in RESUMABLE_STATUSES` and a `claude_session_uuid`. `_rank` treats a slug match as a *tiebreaker*, not a filter, so when no session carries the stalled lane's slug the most-recently-updated eng session wins — routinely a slugless human conversation thread. `RESUMABLE_STATUSES` includes `failed`, so a row that failed once is re-resumed forever.

2. **A silent full-row save corrupts `status`.** `models/agent_session.py:1014` `save()` delegates to popoto, whose `save(update_fields=None)` path (`popoto/models/base.py:1514`) does `hset_mapping = encode_popoto_model_obj(self)` — an HSET of the entire encoded instance — plus `field.on_save()` for every field (`:1565`). `status` is an `IndexedField` (`models/agent_session.py:167`), so a bare `save()` from a stale in-memory instance rewrites both the hash and the index, and emits **no LIFECYCLE log and no `session_events` entry**. Witnessed in both directions by two independent processes:

   ```
   15:02:31.714 monitoring.session_watchdog  pushed 1 reclaim-request(s) for terminal-owner lease(s): 35a92d78...
   15:02:33.278 agent.session_health  bridge-requested reclaim: freed leaked slot for terminal owner=35a92d78... (status=completed)
   15:02:36      SDK heartbeat: running 2460s          <-- harness mid-turn while the row reads 'completed'
   15:07:33.520 [session-health] Recovering session 35a92d78... kind=no_progress: orphaned running row (#944), 2759s
   ```

3. **The turn-end finalize is skipped as idempotent.** With `completed` already on the row, `bridge/session_transcript.py:316` does a fresh `get_authoritative_session()` read, sees terminal, and `finalize_session(s, "completed")` at `:343` hits the idempotency skip (`models/session_lifecycle.py:459,472`) — returning with no side effects, no lifecycle event, and **no `completed_at`**. A later stale save rewrites `running`, and the row is now an orphan.

4. **The `#918` delivery guard is starved of input.** `_delivery_belongs_to_current_run` (`agent/session_health.py:411-424`) would finalize such a row as `completed` instead of requeuing it — but it returns `False` at `:419` because `response_delivered_at` is `None`. That field has exactly two non-test writers (`agent/session_executor.py:1736-1737`, reachable only under `elif action == "deliver":`; `agent/session_completion.py:1117-1118`, gated on `delivery_attempted`) and **neither fired once across the entire retained log window**. Real delivery flows through `flush_deferred_self_draft_sync`, which RPUSHes straight to `telegram:outbox:` without stamping:

   ```
   13:23:36.817 agent.output_handler  Delivery deferred to agent self-draft (steering injected)
   13:27:29.028 agent.session_health flush_deferred_self_draft_sync  flushed deferred self-draft on terminal path (6570 chars, transport=telegram)
   13:27:29.467 bridge.telegram_relay  Relay: sent oversized text as .txt attachment ... msg_id=1451
   ```

Three amplifiers turn a stranded row into visible spam:

5. **The resume steer is room-scoped.** `tools/valor_session.py:1155-1157` pushes with `room_id=room_id_for_session(session)`; `agent/steering.py:313` therefore writes `steering:room:{room_id}` (`:82`). A resume aimed at one row is drained by whichever session next serves that room.
6. **Every re-run is forced to speak.** `agent/session_runner/runner.py:1794-1797` emits `OPERATOR_TERMINAL_MESSAGE` whenever a turn routed nothing user-facing.
7. **Raw CLI limit text reaches the human.** `agent/session_runner/harness/claude.py` has no rate-limit detection at all — grep for `session limit`, `usage limit`, `rate limit`, `quota`, `429`, `529`, `overload` returns zero matches across the whole `agent/session_runner/` tree. And `TIMEOUT_NEEDS_ATTENTION_MESSAGE` (`runner.py:244`, emitted `:861`) has no dedupe key, only a `break`, so a re-enqueued session re-delivers the identical text.

### Current behavior, B

Nothing in the operative prompt says how long a chat reply should be. `prime-pm-role.md` is the only operative style text — `role_driver.py:379-394` prepends it on turn 1, and `runner.py:557` selects it for every non-teammate session. Its closest guidance (`:78` "Concise", about *developer instructions*; `:79` trivial-message acks) does not cover substantive replies, and there is no formatting rule anywhere in the file.

The file that says the right thing is not injected: `config/personas/segments/identity.md:46-49` ("direct... concise (short; longer only when requested)") is assembled only by `compose_system_prompt` (`agent/sdk_client.py:890`), whose sole callers `load_system_prompt` (`:1082`) and `load_eng_system_prompt` (`:1127`) have **no production caller** in `agent/`, `bridge/`, or `tools/`.

And two docs assert a safety net that was removed by #1680: `identity.md:57-59` claims "Long outputs are condensed by the message drafter (`bridge/message_drafter.py`, Haiku)", and `docs/features/pm-voice-refinement.md` (`:9`, `:17`, `:21`, `:29`, `:52`) describes a `DRAFTER_SYSTEM_PROMPT` that exists nowhere in the codebase. The drafter's own docstring (`bridge/message_drafter.py:1-17`) is explicit: "No LLM rewriting of the agent's output... validation + structural composition, not summarization." Over-length output is attached as a file, never shortened.

### Desired outcome

A finished conversation thread is never re-run on behalf of unrelated engineering work; a run that ends is reliably finalized; a re-run with nothing to say does not manufacture a message; and replies read like they belong in the room — a few sentences of prose, length proportional to the ask, long-form analysis attached as a file.

## Freshness Check

Baseline: `main` @ `e73e25581` (2026-09-09), fetched at plan time. Both issues were filed within the hour, immediately after the investigation that produced their evidence.

**Disposition: Unchanged, with one Overlap to coordinate.**

- Every file:line cited in #3270 and #3271 was re-read at plan time and resolves exactly. No drift.
- The bug is still present on current main: the live row `tg_valor_-1003449100931_1450` reads `status='failed'`, `response_delivered_at=None`, `recovery_attempts=4`.
- Cited prior work re-checked: #2710 MERGED (introduced the stall ladder), #944 CLOSED (the orphan net), #918 (the delivery guard), #1680 CLOSED (repositioned the drafter — this is what removed the condensing step the docs still describe), #377 / `a7bf22431` (introduced the bare save at `sdk_client.py:413`).
- **Overlap:** #3253 (open; skeleton plan at `docs/plans/worker-loop-terminal-status-conflict.md`, no content yet) addresses `StatusConflictError` escaping `_worker_loop` at `agent/agent_session_queue.py:3019`/`:3028`. Different call sites, different primary files. The one file both plans may touch is `models/session_lifecycle.py`. This plan's only change there is **additive observability** (a WARNING on an idempotent skip), which cannot conflict semantically with #3253's terminal-skip work. Coordinate at review; do not merge the plans.

## Prior Art

- **#2710** (`6a9e2b66c`) introduced `_pick_steer_target`'s three-rung ladder. Its docstring already argues that resuming another lane's session is harmful ("makes work for issue A land as commits on lane B's branch") — but it only implemented the slug preference as a *ranking* tiebreaker, not a filter. This plan finishes the intent the docstring already states.
- **#944** added the `no_progress` orphan net. It fired correctly here.
- **#918** added `_delivery_belongs_to_current_run` to prevent duplicate delivery. Correct logic, never fed.
- **#1680** repositioned the message drafter from rewriting summarizer to pass-through validator. Correct change; the docs were never cascaded, which is defect B's documentation half.
- **#1803** and **#2088 / PR #2099** both fixed `StatusConflictError` crashes in `agent_session_queue.py`, each time site-locally at the *pop* site. Relevant as evidence that lifecycle-write hazards in this codebase have historically been patched one site at a time rather than swept.

## Why Previous Fixes Failed

#918's guard is the instructive one. It was written against the delivery path that existed at the time (`session_executor` / `session_completion`), and it is still correct code. What changed underneath it is that PM/eng sessions now deliver through `flush_deferred_self_draft_sync` — a path added later that writes to the outbox directly and never stamps `response_delivered_at`. The guard was never wrong; it silently stopped being reachable. This is why this plan fixes the **stamp** rather than loosening the guard: loosening it to bare field-presence would re-open the duplicate-delivery bug #918 was built to close.

The same shape explains #2710. Its rung-2 docstring states the correct rule; the implementation encoded it as a preference. A preference degrades silently to "any row" the moment no same-lane candidate exists, which is the common case.

## Research

No relevant external findings — this is entirely internal: a Redis-backed ORM's save semantics, this repo's own session lifecycle, and this repo's own prompt files. Proceeding on codebase evidence. The one external fact relied on (popoto's `save(update_fields=None)` writing the full encoded instance and running `on_save()` for every field) was verified by reading the vendored source at `.venv/.../popoto/models/base.py:1136,1514,1565` rather than from documentation.

## Data Flow

The corruption path, entry point to storage:

```
worker picks up session        agent/session_pickup.py::_pop_agent_session   -> status=running (logged)
  harness runs the turn        agent/session_runner/harness/claude.py
  output routed to human       agent/output_handler.py::send
      validator flags output   -> _inject_self_draft_steering  (delivery DEFERRED, not stamped)
      terminal-path flush      agent/session_health.py::flush_deferred_self_draft_sync
                               -> RPUSH telegram:outbox:      (human receives it; row records nothing)
  concurrent bare save         agent/output_handler.py:1468 _persist_routing_fields  session.save()
                               agent/output_handler.py:1526 _append_rtr_event        session.save()
                               -> popoto full HSET, rewrites status IndexedField from a stale snapshot
                               -> NO lifecycle log, NO session_events entry
  turn ends                    bridge/session_transcript.py:316 get_authoritative_session()  -> reads 'completed'
                               :343 finalize_session(s, "completed")
                               -> models/session_lifecycle.py:459,472 idempotency skip, returns
                               -> completed_at NEVER SET
  another bare save            -> status back to 'running'
  health check                 agent/session_health.py:4649 _delivery_belongs_to_current_run
                               -> :419 rd is None -> False  (guard cannot fire)
                               :4734-4749 no_progress -> requeue to pending
  attempts exhausted           :3698-3716 finalize 'failed'
  stall check                  reflections/sdlc_progress.py:808-858 _pick_steer_target
                               -> rung 2 picks this slugless row (failed IS resumable)
                               :942 -> tools/valor_session.py:1082 resume_session
                               :1155-1157 push to steering:room:{room_id}   (room-scoped)
                               :1162-1164 transition pending, reject_from_terminal=False
  -> LOOP, one visible reply per iteration
```

The fix layers, in the order the data flows: stamp at delivery (4), finalize reliably at turn end (3), stop corrupting status (2), and stop resuming the wrong row (1). Fixing only the last would leave stranded rows; fixing only the first would leave the stall check resuming conversation threads. Both ends are required.

## Architectural Impact

No new components, no schema change, no new dependency. The changes are: one predicate narrowed (`_pick_steer_target` rung 2), one steering key changed (session-scoped instead of room-scoped), a set of bare `save()` calls narrowed to `update_fields`, one field stamped on a path that already delivers, and additive observability on an existing skip branch. Defect B is prompt text and documentation only.

The one durable architectural statement worth recording: **a bare `save()` on an AgentSession is a lifecycle write.** Because `status` is an `IndexedField` and popoto's default save path is a full HSET, any code that reads a row, mutates one unrelated field, and calls `save()` is silently authorized to rewrite the session's lifecycle state with no audit trail. That is the root hazard, and the sweep below is what closes it.

## Appetite

**Medium.** Defect A is six small, well-localized changes plus the tests that prove each one red first; the investigation that would normally dominate the budget is already done and recorded on #3270. Defect B is prompt text and doc corrections. The budget risk is entirely in the `save()` sweep — see Rabbit Holes.

## Prerequisites

None. All evidence is gathered; both issues pass the recon gate.

## Solution

Ordered by data flow, not by severity.

### A1. Stamp `response_delivered_at` on the path that actually delivers

Stamp the field in `flush_deferred_self_draft_sync` (`agent/session_health.py`, immediately after `delivered = True`) and in the deferred-self-draft redraft path in `agent/output_handler.py::send`. Use a narrow `save(update_fields=[...])`. Without this, `#918`'s guard stays dead no matter what else changes, and the orphan net keeps requeuing rows that already answered the human.

### A2. Make the turn-end finalize observable when it is skipped

`finalize_session`'s idempotency check (`models/session_lifecycle.py:459,472`) treats "already terminal" as success and returns silently. When the caller is a turn-end finalize and a live runner PID is bound to the row, that silence is exactly what strands the session. Log at **WARNING** (not DEBUG) in that case, naming the session id, the status found, and the status requested.

This is deliberately observability, not a behavior change. Changing the idempotency semantics is out of scope and belongs to #3253's area. A WARNING here would have made this incident visible in minutes instead of days.

### A3. Sweep the bare `save()` calls that race lifecycle transitions

Narrow to `update_fields` at each site. Ordered by demonstrated risk:

- `agent/output_handler.py:1468` (`_persist_routing_fields`) → `save(update_fields=["context_summary", "updated_at"])`
- `agent/output_handler.py:1526` (`_append_rtr_event`) → `save(update_fields=["session_events", "updated_at"])`
- `agent/sdk_client.py:413` (`_store_claude_session_uuid`) → `save(update_fields=["claude_session_uuid", "updated_at"])` — its sibling at `:389` already does exactly this, so this is bringing one site in line with its neighbour
- `agent/health_check.py:654` (per-tool-call counter; hottest path of the set)
- `tools/session_tags.py:64`, `:82`, `:281` — auto-tagging runs adjacent to `finalize_session`
- `agent/pipeline_state.py:495`, `tools/valor_telegram.py:791` — lower risk, same treatment for consistency

Per this repo's sweep discipline, the sweep closes on a clean `grep` over AgentSession `.save()` call sites, not on this enumerated list. Any site the grep surfaces that is not listed here gets the same judgement applied.

### A4. Rung 2 of the stall ladder: require a lane match

In `_pick_steer_target`, a row is eligible for the resume rung only if its `slug` is non-`None` **and** equal to `lane_slug`, and its status is not `failed`. Otherwise fall through to the `create` rung. A slugless conversation thread becomes structurally ineligible. This implements the rule `_pick_steer_target`'s own docstring already states.

Excluding `failed` is a separate judgement worth stating: a row that failed once will fail the same way again, so re-resuming it on a timer is a loop, not a recovery.

### A5. Session-scope the resume steer

`resume_session` targets one specific row; pushing to `steering:room:{room_id}` lets an unrelated session drain it. Push to the session-scoped key instead. The existing comment at `tools/valor_session.py:1153-1154` argues for room scope on the grounds that a resume is "a conversation-level instruction" — that reasoning is what allows the cross-talk, and the comment is replaced along with the behavior.

### A6. Contain what a re-run can say

- Detect rate-limit / usage-limit termination in the Claude harness and route it to the **operator once**, never into the room as a chat reply. Note the existing design stance at `harness/claude.py:658-659` against brittle stderr substring matching: prefer exit-code and structured-result signals, and if a text signal is unavoidable, anchor it to text that cannot occur in normal output.
- Give `TIMEOUT_NEEDS_ATTENTION_MESSAGE` a per-session dedupe so it is delivered at most once per session rather than once per run.

### B1. Add a "Match the room" section to the primes

Add a new section under the existing `# Persona behaviors to keep` heading in `.claude/commands/roles/prime-pm-role.md` (`:76`), and an equivalent under `# Teammate persona` in `.claude/commands/roles/prime-teammate-role.md` (`:39`). Substance:

- Chat replies are a few sentences of prose. No headers, no bold, no numbered lists unless the human asked for a list.
- Length is proportional to the ask; a one-line question gets a one-to-three-line answer.
- Long-form analysis goes to a file via `file_paths`, with a two-sentence caption in the message.
- Before a non-trivial group reply, read the recent room history with `valor-telegram read --chat-id <id>` and write at the humans' length and register.
- Keep the load-bearing specifics — commit hashes, PR and issue numbers, verdicts. Drop process narration.

Prefer placeholders over hard numbers where the exact figure is not load-bearing.

### B2. Cascade the stale documentation

Correct `config/personas/segments/identity.md` (`:39-40` HTML comment referencing the non-existent `DRAFTER_SYSTEM_PROMPT`; `:57-59` the Haiku-condensing claim) and `docs/features/pm-voice-refinement.md` (`:9`, `:17`, `:21`, `:29`, `:52`), including its two wrong symbol references: `_parse_summary_and_questions()` → `_parse_draft_and_questions` (`bridge/message_drafter.py:1073`), and `_truncate_at_sentence_boundary()` attributed to `bridge/response.py` → `bridge/message_drafter.py:64`.

Per the repo's no-legacy rule: describe only the current status quo. Do not write "this used to condense output."

### B3. Reconcile `SELF_DRAFT_INSTRUCTION`

`bridge/message_drafter.py:1025-1033` currently mandates "2-4 bullet points", which pushes directly against prose-in-chat. Reconcile it with B1. `tests/unit/test_message_drafter.py:284-287` asserts the instruction contains "outcome", "narration", and "bullet" and stays under 1000 chars — that assertion must be updated deliberately and the change justified in the PR body, not worked around.

## Failure Path Test Strategy

Every guard here certifies an absence, so **every test must be proven RED against the known-bad behavior before the fix lands.** A guard that was never red proves nothing. Concretely, for each item below: write the test, run it on the unfixed tree and record the failure, then apply the fix and confirm green.

- **A4 red-first:** build a project with one stalled lane (slug `X`) and one slugless, recently-updated, `completed` eng session. Assert `_pick_steer_target(project, lane_slug="X")` returns `("create", None)`. On current code it returns `("resume", <the slugless row>)`.
- **A4 failed-row red-first:** same fixture with the candidate in `failed`. Assert it is never selected for the resume rung.
- **A5 red-first:** call `resume_session` and assert the steering write lands on the session-scoped key. On current code it lands on `steering:room:{room_id}`.
- **A1 red-first:** drive a deferred-self-draft delivery through `flush_deferred_self_draft_sync` and assert `response_delivered_at` is set afterward. On current code it stays `None`.
- **A1 integration:** with the stamp in place, assert the health check finalizes the row `completed` instead of requeuing it to `pending` — this is the orphan-bounce reproduction, and it is the test that proves the loop is actually broken rather than merely narrowed.
- **A3 red-first:** the highest-value test in the set. Load a row, mutate an unrelated field on a stale in-memory copy while a *different* status has been written to Redis, call the production save path, and assert the Redis status is unchanged. On current code the stale status wins. This is the test that encodes the architectural rule from Architectural Impact.
- **A6:** assert rate-limit termination produces an operator route and **no** user-facing chat payload; assert the timeout notice is delivered once across two runs of the same session.
- **B1:** assert both prime files contain the new section, and that `tests/unit/test_pm_progress_updates.py` still passes unmodified.

## Test Impact

- [ ] `tests/unit/reflections/test_reflections_progress_check.py` — UPDATE: this is the existing `_pick_steer_target` coverage. Its current cases encode the "most recently updated wins" fallback that A4 removes. Re-read every case and update the ones that assert the old ranking; add the new red-first cases beside them.
- [ ] `tests/unit/test_message_drafter.py:284-287` — UPDATE: the `SELF_DRAFT_INSTRUCTION` content assertion, if B3 changes the bullet mandate. Justify the change in the PR body.
- [ ] `tests/unit/test_pm_progress_updates.py` — NO CHANGE EXPECTED, and this is an acceptance criterion, not an assumption. It asserts exact substrings of `prime-pm-role.md` and bans the four `PHRASING_WORKAROUND_STRINGS` (`:80-85`). B1 adds a section; it must not rewrite existing paragraphs or headers. If this file needs to change, that is a signal B1 went out of bounds.
- [ ] `tests/unit/test_session_health_*.py` (the `deferred_backstop`, `orphan_reap`, and `fence_guards` modules in particular) — REVIEW: A1 changes when `response_delivered_at` is set, which is an input to `_delivery_belongs_to_current_run`. Any case that relies on the field being `None` on a delivered row is asserting the bug and must be updated.
- [ ] Tests covering the swept `save()` sites (`output_handler`, `sdk_client`, `health_check`, `session_tags`, `pipeline_state`, `valor_telegram`) — REVIEW: narrowing to `update_fields` changes which fields persist. Any test asserting an incidental field write through one of these paths must be updated to reflect the narrowed contract.
- [ ] `tests/unit/test_pm_progress_updates.py::TestPromiseGateFallbackAllowsTaughtPhrasings` — REVIEW: new prompt text must not introduce forward-deferral phrasing of the `TAUGHT_BLOCKED` shape (`:76`).

## Rabbit Holes

- **The `save()` sweep is the budget risk.** Narrowing a save changes which fields persist, and a site that was accidentally relying on the full write will break in a way unit tests may not catch. Mitigation: sweep by grep, change one site per commit, and for each site name in the commit message which fields are now written. If a site's correct field set is genuinely unclear, leave it and file a follow-up rather than guessing.
- **Rate-limit detection invites string matching.** `harness/claude.py:658-659` already recorded the stance that stderr substring matching is brittle across CLI versions and locales, and that stance is correct. Do not build a keyword list. Prefer exit codes and structured result fields; if no structured signal exists, say so and scope A6 to the routing half (never relay harness failure text to the room) rather than inventing a fragile detector.
- **Do not fix the `identity.md` injection gap.** Wiring `compose_system_prompt` into the runner is a real and separate problem with its own blast radius. This plan corrects identity.md's false claims in place and fixes the file that is actually operative.
- **Do not rewrite the primes.** B1 adds a section. The temptation to tidy neighbouring paragraphs while in the file is exactly what breaks `test_pm_progress_updates.py`'s exact-substring assertions.
- **Do not expand into #3253's territory.** `finalize_session`'s idempotency semantics and `agent_session_queue.py`'s terminal-write handling belong to that issue. A2 adds a log line and nothing else.

## Risks

- **A4 could starve legitimate recovery.** Requiring a slug match means a stalled lane whose session lost its slug now falls to the `create` rung instead of resuming. That is the correct trade — creating a fresh session is recoverable, resuming a stranger's conversation is not — but it will change stall-recovery behavior in production and should be watched after deploy.
- **A1 could surface latent duplicate delivery.** Stamping `response_delivered_at` on a path that never stamped it will start firing `#918`'s guard on rows where it previously stayed silent. That is the intent, but it makes a previously-dead code path live; the A1 integration test exists specifically to characterize it before it ships.
- **A3 is the highest-blast-radius change** and touches files owned by other concerns. Per-site commits keep it bisectable.
- **B1 changes agent behavior through prompt text**, which cannot be fully verified by unit tests. The real verification is observational: read the room after deploy.

## Race Conditions

The defect *is* a race: a read-modify-write on an AgentSession with no compare-and-swap, where the field being clobbered (`status`) is not the field being written. A3 does not add locking; it removes the write. Narrowing to `update_fields` means the concurrent lifecycle transition and the incidental field update no longer contend for the same key, because they no longer write the same fields.

Two ordering facts the implementation must respect:

- `transition_status` and `finalize_session` both save **the caller's object, not the fresh re-read** (documented at `models/session_lifecycle.py:836-837`). So a long-held session object passed into a lifecycle call carries its own stale snapshot. Narrowing the incidental saves reduces the window but does not eliminate this; do not assume A3 makes lifecycle writes safe in general.
- `agent/session_health.py:3841` sets `entry.started_at = None`, which is **not** in `_requeue_fields` (`:3927-3934`) but is persisted anyway by the full save inside the following `transition_status`. It is load-bearing for requeue semantics. If that path is narrowed, make the `None` write explicit rather than incidental — this is a real trap, and it also explains the bogus `duration_in_prev_state=6698.9s` observed at 15:07:33 (the `created_at` fallback at `models/agent_session.py:2268`).

## No-Gos (Out of Scope)

- Loosening `_delivery_belongs_to_current_run` to bare field-presence. Re-opens #918.
- Suppressing or weakening the `#944` orphan net at `session_health.py:4734-4749`. It fired correctly; suppressing it hides corruption instead of fixing it.
- Changing `finalize_session`'s idempotency *behavior* (as opposed to its logging). Belongs to #3253.
- Wiring `compose_system_prompt` / `identity.md` into the runtime prompt path.
- Any edit to `CLAUDE.md` — its headings are regex-parsed into worker prompts and asserted byte-for-byte.
- Changing `valor-telegram read`'s flags; `tools/valor_telegram.py:1310-1315` warns they are asserted by an integration test (#2694).
- Rewriting existing paragraphs or headers in either prime file.

## Update System

No update-script changes required. Two propagation facts the implementer must honor:

- The prime files under `.claude/commands/roles/` are **hardlinked fleet-wide**. Edit them in place with `Edit`; never replace-and-rename, which breaks the hardlink. Verify the inode is unchanged after editing.
- No new dependency, config file, or migration is introduced. No Popoto schema change: A1 stamps an existing field and A3 narrows existing writes, so `scripts/update/migrations.py` needs no entry.

After merge, `/update` propagates the change to running services, and the bridge/worker need `./scripts/valor-service.sh restart` for the prompt and runtime changes to take effect.

## Agent Integration

No new CLI entry point and no new bridge import. Every change modifies code already on the live path:

- A1/A3 run inside the worker's existing session-execution path.
- A4/A5 run inside the reflections stall-check job.
- A6 runs inside the session runner.
- B1 changes a prompt file the runner already reads on turn 1 (`role_driver.py:379-394`).

The behavior is therefore reachable by the agent immediately after a service restart, with no wiring step. B1's guidance references `valor-telegram read --chat-id <id>`, which is an existing entry point (`pyproject.toml:81`) already available to the agent's Bash tool.

## Documentation

- [ ] Update `docs/features/pm-voice-refinement.md` — remove the `DRAFTER_SYSTEM_PROMPT` / LLM-drafter claims (`:9`, `:17`, `:21`, `:29`, `:52`) and fix the two wrong symbol references (`_parse_summary_and_questions` → `_parse_draft_and_questions`; `_truncate_at_sentence_boundary` location).
- [ ] Update `config/personas/segments/identity.md` — remove the stale HTML comment (`:39-40`) and the Haiku-condensing claim (`:57-59`).
- [ ] Create `docs/features/agent-session-lifecycle-writes.md` — document the architectural rule that a bare `save()` on an AgentSession is a lifecycle write (popoto full-HSET semantics, `status` as `IndexedField`, no audit trail), and the `update_fields` convention that A3 establishes. This is the durable knowledge from this investigation and the thing that prevents the next instance.
- [ ] Add the new doc to the `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` to reflect session-scoped resume steers (A5).

## Success Criteria

- Zero `resume (sdlc-stall)` transitions on slugless sessions, verified by a red-first unit test and by log inspection after deploy.
- `_pick_steer_target` never returns a `failed` row from the resume rung.
- `resume_session` writes to the session-scoped steering key.
- `response_delivered_at` is set after a deferred-self-draft delivery, and the health check finalizes such a row `completed` rather than requeuing it.
- A stale in-memory AgentSession copy can no longer overwrite a concurrently-written `status` through any of the swept save sites.
- A turn-end finalize skipped as idempotent while a runner PID is bound emits a WARNING.
- Rate-limit termination reaches the operator and never the room; the timeout notice is delivered at most once per session.
- Both prime files carry the room-matching guidance; `tests/unit/test_pm_progress_updates.py` passes unmodified; prime file inodes are unchanged.
- `docs/features/pm-voice-refinement.md` and `identity.md` no longer describe a condensing drafter, and every symbol they name resolves to real code.
- Narrow-scope tests pass via `scripts/pytest-clean.sh` on the touched modules only — never a full-suite run from this worktree.

## Team Orchestration

Two independent tracks, disjoint file sets, one branch (`session/dev-1aafae58`) and one worktree.

**Track A — defect #3270** (`reflections/sdlc_progress.py`, `tools/valor_session.py`, `agent/session_health.py`, `agent/output_handler.py`, `agent/sdk_client.py`, `agent/health_check.py`, `tools/session_tags.py`, `agent/pipeline_state.py`, `tools/valor_telegram.py`, `agent/session_runner/runner.py`, `agent/session_runner/harness/claude.py`, `models/session_lifecycle.py`)

**Track B — defect #3271** (`.claude/commands/roles/prime-pm-role.md`, `.claude/commands/roles/prime-teammate-role.md`, `config/personas/segments/identity.md`, `docs/features/pm-voice-refinement.md`, `bridge/message_drafter.py`)

The file sets do not intersect, so the tracks can run concurrently as separate builders in the same worktree. Sequence within Track A is load-bearing (A1 before the A1 integration test; A3 site-by-site); Track B has no internal ordering constraint beyond B1 before B3.

## Step by Step Tasks

### Track A

- [ ] A0. Write the red-first tests for A4 and A5 in `tests/unit/reflections/test_reflections_progress_check.py`; run them and **record the failures** before writing any fix.
- [ ] A1. Stamp `response_delivered_at` in `flush_deferred_self_draft_sync` and the deferred-self-draft redraft path in `output_handler.send`, with a narrow `save(update_fields=[...])`. Red-first test proves the field stays `None` today.
- [ ] A1b. Integration test: with the stamp present, the health check finalizes the row `completed` instead of requeuing to `pending`. This is the orphan-bounce reproduction.
- [ ] A2. Add the WARNING in `finalize_session` when an idempotent skip coincides with a bound live runner PID. No behavior change.
- [ ] A3. Sweep the bare `save()` sites, one commit per site, each commit message naming the fields now written. Close the sweep on a clean grep over AgentSession `.save()` call sites, not on the enumerated list. Include the stale-snapshot regression test.
- [ ] A4. Narrow `_pick_steer_target` rung 2 to require a non-`None` slug equal to `lane_slug`, and exclude `failed`. Update the docstring to describe a filter rather than a preference. Turn A0's tests green.
- [ ] A5. Session-scope the resume steer in `resume_session`; replace the `:1153-1154` comment along with the behavior.
- [ ] A6a. Route rate-limit / usage-limit termination to the operator once; never emit it as a chat payload. If no structured signal exists, scope to the routing half and say so.
- [ ] A6b. Dedupe `TIMEOUT_NEEDS_ATTENTION_MESSAGE` to once per session.
- [ ] A7. Review the `tests/unit/test_session_health_*.py` modules flagged in Test Impact; update any case asserting the pre-fix contract.

### Track B

- [ ] B1. Add the "Match the room" section under `# Persona behaviors to keep` in `prime-pm-role.md` and under `# Teammate persona` in `prime-teammate-role.md`. Edit in place; verify inodes unchanged. Do not touch existing paragraphs or headers.
- [ ] B1b. Run `tests/unit/test_pm_progress_updates.py` and confirm it passes **unmodified**.
- [ ] B2. Correct `config/personas/segments/identity.md` (`:39-40`, `:57-59`) and `docs/features/pm-voice-refinement.md` (`:9`, `:17`, `:21`, `:25`, `:29`, `:52`), including both wrong symbol references. Describe only the current status quo.
- [ ] B3. Reconcile `SELF_DRAFT_INSTRUCTION` with B1; update `tests/unit/test_message_drafter.py:284-287` deliberately and justify in the PR body.

### Shared

- [ ] D1. Write `docs/features/agent-session-lifecycle-writes.md` and add it to the `docs/features/README.md` index.
- [ ] D2. Update `docs/features/session-steering.md` for session-scoped resume steers.
- [ ] V1. Run narrow-scope tests on touched modules only via `scripts/pytest-clean.sh`.
- [ ] V2. `python -m ruff check` and `python -m ruff format`.
- [ ] V3. Open the PR with `Closes #3270` and `Closes #3271`.

## Verification

1. Each red-first test was observed failing on the unfixed tree, and the failure is recorded in the PR body. This is the single most important verification step in the plan; a guard that was never red proves nothing.
2. `scripts/pytest-clean.sh` green on the touched modules only. Never a full-suite run from this worktree — parallel lanes collide on Redis state.
3. `python -m ruff check` and `python -m ruff format` clean.
4. Prime file inodes unchanged after editing (`ls -i` before and after), confirming the fleet hardlinks survived.
5. `tests/unit/test_pm_progress_updates.py` passes with no modification to that file.
6. Every symbol named in the corrected docs resolves to real code (grep each one).
7. Post-deploy observation, which is the only verification that closes the actual complaint: no unprompted `resume (sdlc-stall)` transitions on slugless rows in `logs/reflection_worker_error.log`, and replies in the room that read at human length.

## Critique Results

Round 1 — FULL depth, independent roster (3 critics: Risk & Robustness, Scope & Value, History & Consistency). Verdict: **NEEDS REVISION**.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | A1's stamp site is clobbered by `finalize_session`'s own trailing full save. `models/session_lifecycle.py:404` calls `flush_deferred_self_draft_sync(session, status)`, which is documented at `:384-386` as "fresh-reading: re-reads get_authoritative_session() internally, so it is unaffected by the caller's possibly-stale session". `:607` then runs a bare `session.save()` (no `update_fields`) on the caller's stale object. Stamping only the fresh object writes `response_delivered_at` and has it immediately reset to `None` on the very save that finalizes the transition — on exactly the `bridge/session_transcript.py:343` path that produced the incident. A1 would ship, tests could pass against the fresh read, and the #918 guard would stay dead in production. | pending | Mirror the existing caller-object precedent: in the same block where the flush sets `delivered = True`, also mutate the caller's object, i.e. `if session is not None and session is not _clear_target: session.response_delivered_at = <same value written to _clear_target>`. This is the identical workaround already applied to `extra_context` a few lines below, whose comment states the full save "silently resurrects the just-cleared flag". The A1b integration test MUST drive the real `finalize_session` chain with a stale caller object whose `response_delivered_at` starts `None`, not `flush_deferred_self_draft_sync` in isolation — an isolated test passes while the production bug remains. |
| CONCERN | Scope & Value | The A3 sweep lists nine sites but the evidence backs at most two. The #3270 investigation names `agent/output_handler.py:1468` and `:1526` as PLAUSIBLE, explicitly REFUTES `agent/sdk_client.py:413` ("latent hazard, not this bug"), and has zero evidence for `health_check.py:654`, `session_tags.py:64/82/281`, `pipeline_state.py:495`, `valor_telegram.py:791` — included only "for consistency". The plan itself calls A3 its highest-blast-radius change and budget risk, so most of that radius is theoretical completeness rather than the demonstrated defect. | pending | Split A3 into A3a (the two evidenced `output_handler.py` sites plus the stale-snapshot regression test) shipped in this PR, and A3b (everything else) filed as a separate "AgentSession save() hardening sweep" issue citing this plan's Architectural Impact section. Retain the grep as an *enumeration* step so no site is missed, but scope the *fix* in this PR to the evidenced sites. |
| CONCERN | Scope & Value | The plan bundles the low-risk, prompt-only #3271 fix with the highest-blast-radius lifecycle sweep for #3270 in one PR, behind one set of red-first tests and one review pass. The human's literal complaint ("I'm not your log file") is independently fixable and mergeable, but per V3 it ships only when the riskier fix is done. | pending | Constrained by lane identity: this session owns exactly one worktree and branch (`session/dev-1aafae58`), so one branch means one PR. Mitigation adopted instead of a split: commit Track B first and keep it independently revertable, so Track B can be cherry-picked out if Track A stalls in review. Recorded as a named deviation for the decider to override. |
| NIT | Scope & Value | `SELF_DRAFT_INSTRUCTION` fires only when the delivery validator rejects a message — a narrow corrective path, not the one that produced the 1029-word reply the human complained about. Changing this tested constant chases consistency rather than the demonstrated defect. | pending | Resolve Open Question 3 in favor of option (a): leave the bullet mandate alone. Drop task B3 and remove the `tests/unit/test_message_drafter.py:284-287` row from Test Impact. |


---

## Open Questions

1. **A6a rate-limit detection — is there a structured signal?** The harness deliberately avoids stderr substring matching (`harness/claude.py:658-659`), and I have not found a structured exit-code or result-field signal for a usage-limit stop. If none exists, the honest scope for A6a is the routing half only: never relay harness failure text to the room, and let the operator route carry whatever the harness produced. Confirm this narrowing is acceptable rather than building a keyword detector the codebase has already argued against.

2. **A4's `failed`-row exclusion — any legitimate caller?** Excluding `failed` from the resume rung is the right call for conversation threads, but if some SDLC recovery flow depends on resuming a genuinely failed *lane* session, this narrows it. I found no such caller, but this is a behavior change to production recovery and worth a second opinion.

3. **B3 scope — how far to reconcile `SELF_DRAFT_INSTRUCTION`?** It currently mandates "2-4 bullet points" for *validator-rejected* messages, which is a narrower context than ordinary chat replies. Options: (a) leave the bullet mandate alone since it only fires on rejection, (b) soften it to match B1. I lean (b) for consistency, but (a) is defensible and avoids touching a tested constant.
