---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3270
last_comment_id: 5601939922
revision_applied: true
revision_applied_at: 2026-09-09T13:20:00Z
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

**The stamp must be mirrored onto the caller's in-memory object, or it does not survive.** `flush_deferred_self_draft_sync` is fresh-reading by design (`models/session_lifecycle.py:384-386`): it re-reads `get_authoritative_session()` and writes that object. But `finalize_session` calls the flush at `:404` and then, at `:595-607`, runs a bare `session.save()` on the **caller's** possibly-stale object — a full popoto HSET that rewrites `response_delivered_at` back to `None`. Stamping only the fresh object therefore ships a fix that is erased microseconds later, on exactly the `bridge/session_transcript.py:343` path that produced this incident.

So in the same block where the flush sets `delivered = True`, also mutate the caller's object:

```python
if session is not None and session is not _clear_target:
    session.response_delivered_at = <the same value written to _clear_target>
```

This is not a new pattern. It is the identical workaround already applied to `extra_context` a few lines below, whose comment records that the trailing full save "silently resurrects the just-cleared flag". Follow that precedent's shape exactly, including the identity check, so a future reader sees one convention rather than two.

The **A1c** test is a **`finalize_session` end-to-end** test, never a `flush_deferred_self_draft_sync`-in-isolation test. An isolated test passes against the fresh read while the production bug survives intact; that discrepancy is the whole finding.

**Both stamp sites need this treatment, and the second one is not hypothetical.** A1 stamps in two places: the flush, and the deferred-self-draft redraft path in `agent/output_handler.py::send`. The mirror above protects the flush path only. Whether the second stamp is equally exposed depends on the caller: `bridge/session_transcript.py:316` re-reads via `get_authoritative_session()` immediately before its `:343` `finalize_session` call and therefore self-heals, while `agent/session_health.py:4659` passes a loop-variable `entry` that was read earlier in a batch and does not. So begin A1 by **enumerating every `finalize_session` caller** and classifying each as fresh-reading or stale-passing. Every stale-passing caller reachable from the `output_handler.py::send` stamp needs its own red-first test; if the enumeration finds none, narrow the Success Criteria claim to the flush path explicitly rather than leaving the claim broader than the test. Do not leave that gap implicit either way — the enumeration's result goes in the PR body.

### A2. Make the turn-end finalize observable when it is skipped

`finalize_session`'s idempotency check (`models/session_lifecycle.py:459,472`) treats "already terminal" as success and returns silently. When the caller is a turn-end finalize and a live runner PID is bound to the row, that silence is exactly what strands the session. Log at **WARNING** (not DEBUG) in that case, naming the session id, the status found, and the status requested.

This is deliberately observability, not a behavior change. Changing the idempotency semantics is out of scope and belongs to #3253's area. A WARNING here would have made this incident visible in minutes instead of days.

### A3a. Narrow the two evidenced `save()` sites (ships here)

Narrow to `update_fields` at the two sites the #3270 investigation actually implicates as writers on the incident path:

- `agent/output_handler.py:1468` (`_persist_routing_fields`) → `save(update_fields=["context_summary", "updated_at"])`
- `agent/output_handler.py:1526` (`_append_rtr_event`) → `save(update_fields=["session_events", "updated_at"])`

Ship the stale-snapshot regression test alongside these. That test — not the site count — is what encodes the architectural rule, and it guards every future site.

### A3b. Enumerate the rest; fix them under a separate issue

The remaining bare-`save()` sites (`agent/sdk_client.py:413`, `agent/health_check.py:654`, `tools/session_tags.py:64/:82/:281`, `agent/pipeline_state.py:495`, `tools/valor_telegram.py:791`, plus anything the grep surfaces) are a **latent hazard class**, not this bug. `sdk_client.py:413` was explicitly refuted as this incident's writer during recon; the rest were listed for consistency and carry no evidence at all. Fixing nine sites behind one review pass spends this plan's entire blast-radius budget on theoretical completeness.

Run the grep as an **enumeration** step in this PR and record its full output in a new issue, "AgentSession `save()` hardening sweep", citing this plan's Architectural Impact section and the `docs/features/agent-session-lifecycle-writes.md` doc that D1 creates. Fix them there, one commit per site, on their own review pass.

Per this repo's sweep discipline the *enumeration* closes on a clean grep over AgentSession `.save()` call sites, never on an enumerated list — that discipline is preserved; only the *fix* is scoped to the evidenced sites.

### A4. Both rungs of the stall ladder: require a lane match

`_pick_steer_target` has two rungs and **both** rank by the same `_rank` closure, in which the slug is only a tiebreaker. Rung 2 (resume) is the one that produced this incident; rung 1 (steer) has the identical defect and produces the identical symptom — `reflections/sdlc_progress.py:856-857` reads `if live: return ("steer", max(live, key=_rank))`, so when no same-lane *live* row exists the stall check steers whichever unrelated live session was updated most recently, and an unprompted message lands in a stranger's thread. Fixing only rung 2 would close the path the logs happened to record and leave its twin open.

So: in **both** buckets, a row is eligible only if its `slug` is non-`None` **and** equal to `lane_slug`. A slugless conversation thread becomes structurally ineligible for either rung. This is one predicate applied twice, not two fixes, and it implements the rule the function's own docstring already states.

Two ordering details the implementation must respect:

- Rung 1 falls through to **rung 2**, not to `create`, when no same-lane live row exists. Falling straight to `create` would skip a legitimate same-lane resumable row.
- The `failed` exclusion belongs to rung 2 only. `failed` is not in `NON_TERMINAL_STATUSES`, so it can never reach the live bucket, and writing the check in both places would be dead code.

Excluding `failed` from rung 2 is a separate judgement worth stating: a row that failed once will fail the same way again, so re-resuming it on a timer is a loop, not a recovery.

### A5. Session-scope the resume steer

`resume_session` targets one specific row; pushing to `steering:room:{room_id}` lets an unrelated session drain it. Push to the session-scoped key instead. The existing comment at `tools/valor_session.py:1153-1154` argues for room scope on the grounds that a resume is "a conversation-level instruction" — that reasoning is what allows the cross-talk, and the comment is replaced along with the behavior.

### A6. Dedupe the timeout notice (and defer the rate-limit routing)

**A6b ships here.** Give `TIMEOUT_NEEDS_ATTENTION_MESSAGE` (`agent/session_runner/runner.py:244`, emitted at `:861` with only a `break` after it) a per-session dedupe so it is delivered at most once per session rather than once per run. This is mechanical, it is on the duplicate-message path this issue is about, and the missing dedupe is directly observable in the code.

**A6a is deferred to the follow-up hardening issue.** Routing rate-limit / usage-limit termination to the operator instead of the room is a blanket routing-policy change to `agent/session_runner/harness/claude.py` with **no detector** — the recon grep for `session limit`, `usage limit`, `rate limit`, `quota`, `429`, `529`, and `overload` returned zero matches, which proves the path is unhandled but not that it ever fired in this incident, and Open Question 1 established that no structured signal exists to key off. Shipping it here would apply exactly the "theoretical completeness" standard that A3b was carved out to avoid. It goes into the same follow-up issue A3b files, and lands there if and when a log occurrence of harness failure text reaching a room is produced or a structured signal appears.

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

## Failure Path Test Strategy

Every guard here certifies an absence, so **every test must be proven RED against the known-bad behavior before the fix lands.** A guard that was never red proves nothing. Concretely, for each item below: write the test, run it on the unfixed tree and record the failure, then apply the fix and confirm green.

- **A4 rung 2 red-first:** build a project with one stalled lane (slug `X`) and one slugless, recently-updated, `completed` eng session carrying a `claude_session_uuid`. Assert `_pick_steer_target(project, lane_slug="X")` returns `("create", None)`. On current code it returns `("resume", <the slugless row>)`.
- **A4 rung 1 red-first:** same fixture but the slugless row is in a status inside `NON_TERMINAL_STATUSES`. Assert the function never returns `("steer", <the slugless row>)`. On current code it does — this is the twin defect, and it is the one nothing in the incident logs happened to record.
- **A4 fall-through:** one slugless live row plus one same-lane resumable row. Assert the result is `("resume", <the same-lane row>)`, proving rung 1 falls through to rung 2 rather than to `create`.
- **A4 failed-row red-first:** same fixture with the candidate in `failed`. Assert it is never selected for the resume rung.
- **A5 red-first:** call `resume_session` and assert the steering write lands on the session-scoped key. On current code it lands on `steering:room:{room_id}`.
- **A1 red-first:** drive a deferred-self-draft delivery through `flush_deferred_self_draft_sync` and assert `response_delivered_at` is set afterward. On current code it stays `None`.
- **A1 caller-object red-first:** call `finalize_session` end-to-end with a **stale caller object** whose `response_delivered_at` starts `None`, and assert the field survives `finalize_session`'s own trailing save. Against a fresh-object-only fix this test is RED while an isolated flush test is green — that gap is the point of the test.
- **A1 integration:** with the stamp in place, assert the health check finalizes the row `completed` instead of requeuing it to `pending` — this is the orphan-bounce reproduction, and it is the test that proves the loop is actually broken rather than merely narrowed.
- **A3a red-first:** the highest-value test in the set. Load a row, mutate an unrelated field on a stale in-memory copy while a *different* status has been written to Redis, call the production save path, and assert the Redis status is unchanged. On current code the stale status wins. This is the test that encodes the architectural rule from Architectural Impact. **Parametrize it over both call sites** — `_persist_routing_fields` (`:1468`, narrowing to `["context_summary", "updated_at"]`) and `_append_rtr_event` (`:1526`, narrowing to `["session_events", "updated_at"]`). The field sets differ, so a single test written against one site proves nothing about the other.
- **A6b:** assert the timeout notice is delivered once across two runs of the same session.
- **B1:** assert both prime files contain the new section, and that `tests/unit/test_pm_progress_updates.py` still passes unmodified.

## Test Impact

- [ ] `tests/unit/reflections/test_reflections_progress_check.py` — UPDATE: this is the existing `_pick_steer_target` coverage. Its current cases encode the "most recently updated wins" fallback that A4 removes. Re-read every case and update the ones that assert the old ranking; add the new red-first cases beside them.
- [ ] `tests/unit/test_pm_progress_updates.py` — NO CHANGE EXPECTED, and this is an acceptance criterion, not an assumption. It asserts exact substrings of `prime-pm-role.md` and bans the four `PHRASING_WORKAROUND_STRINGS` (`:80-85`). B1 adds a section; it must not rewrite existing paragraphs or headers. If this file needs to change, that is a signal B1 went out of bounds.
- [ ] `tests/unit/test_session_health_*.py` (the `deferred_backstop`, `orphan_reap`, and `fence_guards` modules in particular) — REVIEW: A1 changes when `response_delivered_at` is set, which is an input to `_delivery_belongs_to_current_run`. Any case that relies on the field being `None` on a delivered row is asserting the bug and must be updated.
- [ ] Tests covering the two narrowed `save()` sites in `agent/output_handler.py` — REVIEW: narrowing to `update_fields` changes which fields persist. Any test asserting an incidental field write through one of these paths must be updated to reflect the narrowed contract.
- [ ] `tests/unit/test_pm_progress_updates.py::TestPromiseGateFallbackAllowsTaughtPhrasings` — REVIEW: new prompt text must not introduce forward-deferral phrasing of the `TAUGHT_BLOCKED` shape (`:76`).

## Rabbit Holes

- **The `save()` sweep is the budget risk, which is why the fix is scoped to two sites.** Narrowing a save changes which fields persist, and a site that was accidentally relying on the full write will break in a way unit tests may not catch. Mitigation: A3a fixes only the two evidenced sites, one commit each, naming in the commit message which fields are now written; A3b enumerates the rest into a follow-up issue. Do not let the grep's output pull unevidenced sites back into this PR.
- **Rate-limit detection invites string matching, which is why A6a is not in this PR at all.** `harness/claude.py:658-659` already recorded the stance that stderr substring matching is brittle across CLI versions and locales, and that stance is correct. No structured signal exists to replace it, and no logged occurrence of harness failure text reaching a room was found. Building a keyword list here would be the fragile detector the codebase already argued against; shipping a blanket routing change instead would be the theoretical completeness A3b was carved out to avoid. It moves to the follow-up issue.
- **Do not fix the `identity.md` injection gap.** Wiring `compose_system_prompt` into the runner is a real and separate problem with its own blast radius. This plan corrects identity.md's false claims in place and fixes the file that is actually operative.
- **Do not rewrite the primes.** B1 adds a section. The temptation to tidy neighbouring paragraphs while in the file is exactly what breaks `test_pm_progress_updates.py`'s exact-substring assertions.
- **Do not expand into #3253's territory.** `finalize_session`'s idempotency semantics and `agent_session_queue.py`'s terminal-write handling belong to that issue. A2 adds a log line and nothing else.

## Risks

- **A4 could starve legitimate recovery.** Requiring a slug match in both rungs means a stalled lane whose session lost its slug now falls to the `create` rung instead of being steered or resumed. That is the correct trade — creating a fresh session is recoverable, resuming a stranger's conversation is not — but it will change stall-recovery behavior in production and should be watched after deploy.
- **A1 could surface latent duplicate delivery.** Stamping `response_delivered_at` on a path that never stamped it will start firing `#918`'s guard on rows where it previously stayed silent. That is the intent, but it makes a previously-dead code path live; the A1 integration test exists specifically to characterize it before it ships.
- **A3a is still the highest-blast-radius code change in this PR**, though scoping it to two evidenced sites in one file cuts that radius substantially. Per-site commits keep it bisectable. The residual risk moves to A3b's follow-up issue, where it gets its own review pass.
- **B1 changes agent behavior through prompt text**, which cannot be fully verified by unit tests. The real verification is observational: read the room after deploy.

## Race Conditions

The defect *is* a race: a read-modify-write on an AgentSession with no compare-and-swap, where the field being clobbered (`status`) is not the field being written. A3a does not add locking; it removes the write. Narrowing to `update_fields` means the concurrent lifecycle transition and the incidental field update no longer contend for the same key, because they no longer write the same fields.

Two ordering facts the implementation must respect:

- `transition_status` and `finalize_session` both save **the caller's object, not the fresh re-read** (documented at `models/session_lifecycle.py:836-837`). So a long-held session object passed into a lifecycle call carries its own stale snapshot. Narrowing the incidental saves reduces the window but does not eliminate this; do not assume A3a makes lifecycle writes safe in general. A1's caller-object mirror is the concrete instance of this hazard that this plan must handle head-on.
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
- No new dependency, config file, or migration is introduced. No Popoto schema change: A1 stamps an existing field and A3a narrows existing writes, so `scripts/update/migrations.py` needs no entry.

After merge, `/update` propagates the change to running services, and the bridge/worker need `./scripts/valor-service.sh restart` for the prompt and runtime changes to take effect.

## Agent Integration

No new CLI entry point and no new bridge import. Every change modifies code already on the live path:

- A1/A3a run inside the worker's existing session-execution path.
- A4/A5 run inside the reflections stall-check job.
- A6b runs inside the session runner.
- B1 changes a prompt file the runner already reads on turn 1 (`role_driver.py:379-394`).

The behavior is therefore reachable by the agent immediately after a service restart, with no wiring step. B1's guidance references `valor-telegram read --chat-id <id>`, which is an existing entry point (`pyproject.toml:81`) already available to the agent's Bash tool.

## Documentation

- [ ] Update `docs/features/pm-voice-refinement.md` — remove the `DRAFTER_SYSTEM_PROMPT` / LLM-drafter claims (`:9`, `:17`, `:21`, `:29`, `:52`) and fix the two wrong symbol references (`_parse_summary_and_questions` → `_parse_draft_and_questions`; `_truncate_at_sentence_boundary` location).
- [ ] Update `config/personas/segments/identity.md` — remove the stale HTML comment (`:39-40`) and the Haiku-condensing claim (`:57-59`).
- [ ] Create `docs/features/agent-session-lifecycle-writes.md` — document the architectural rule that a bare `save()` on an AgentSession is a lifecycle write (popoto full-HSET semantics, `status` as `IndexedField`, no audit trail), and the `update_fields` convention that A3a establishes. This is the durable knowledge from this investigation and the thing that prevents the next instance.
- [ ] Add the new doc to the `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` to reflect session-scoped resume steers (A5).

## Success Criteria

- Zero `resume (sdlc-stall)` transitions **and zero stall-check steers** on slugless sessions, verified by red-first unit tests on both rungs and by log inspection after deploy.
- `_pick_steer_target` never returns a `failed` row from the resume rung.
- `resume_session` writes to the session-scoped steering key.
- `response_delivered_at` is set after a deferred-self-draft delivery, and the health check finalizes such a row `completed` rather than requeuing it.
- A stale in-memory AgentSession copy can no longer overwrite a concurrently-written `status` through either narrowed `output_handler.py` save site, proven by the stale-snapshot regression test.
- `response_delivered_at` survives a full `finalize_session` call made with a stale caller object, on every stamp site the A1 caller enumeration classified as exposed. Where the enumeration found no stale-passing caller for a stamp site, this criterion is explicitly scoped to the flush path and says so in the PR body.
- The remaining bare-`save()` sites are enumerated by grep and filed as a follow-up issue, with that issue number recorded in the PR body.
- A turn-end finalize skipped as idempotent while a runner PID is bound emits a WARNING.
- The timeout notice is delivered at most once per session.
- Both prime files carry the room-matching guidance; `tests/unit/test_pm_progress_updates.py` passes unmodified; prime file inodes are unchanged.
- `docs/features/pm-voice-refinement.md` and `identity.md` no longer describe a condensing drafter, and every symbol they name resolves to real code.
- Narrow-scope tests pass via `scripts/pytest-clean.sh` on the touched modules only — never a full-suite run from this worktree.

## Team Orchestration

Two independent tracks, disjoint file sets, one branch (`session/dev-1aafae58`) and one worktree.

**Track A — defect #3270** (`reflections/sdlc_progress.py`, `tools/valor_session.py`, `agent/session_health.py`, `agent/output_handler.py`, `agent/session_runner/runner.py`, `models/session_lifecycle.py`)

This set lists only files a remaining task actually modifies. `agent/session_runner/harness/claude.py` left it when A6a was deferred; `agent/sdk_client.py`, `agent/health_check.py`, `tools/session_tags.py`, `agent/pipeline_state.py`, and `tools/valor_telegram.py` left it when A3b was deferred — A3b greps and files an issue, it changes no code. `tools/valor_telegram.py` in particular is a No-Go (its flags are asserted by an integration test, #2694) and must not be edited by this plan at all.

**Track B — defect #3271** (`.claude/commands/roles/prime-pm-role.md`, `.claude/commands/roles/prime-teammate-role.md`, `config/personas/segments/identity.md`, `docs/features/pm-voice-refinement.md`)

The file sets do not intersect, so the tracks can run concurrently as separate builders in the same worktree. Sequence within Track A is load-bearing (A1 before the A1 integration test; A3a site-by-site); Track B has no internal ordering constraint.

**Commit ordering is load-bearing.** Track B commits **first** and stays independently revertable — its commits touch no file Track A touches, so if Track A stalls in review the prompt-only fix for #3271 can be cherry-picked out, or the Track A commits reverted, without disturbing it. This is the adopted mitigation for shipping both defects in one PR; see the Critique Results table for why one branch is a lane-identity constraint rather than a choice.

## Step by Step Tasks

### Track A

- [ ] A0. Write the red-first tests for A4 (both rungs, plus the fall-through case) and A5 in `tests/unit/reflections/test_reflections_progress_check.py`; run them and **record the failures** before writing any fix.
- [ ] A1. Enumerate every `finalize_session` caller and classify each as fresh-reading or stale-passing; record the result for the PR body. Then stamp `response_delivered_at` in `flush_deferred_self_draft_sync` and the deferred-self-draft redraft path in `output_handler.send`, with a narrow `save(update_fields=[...])`, **including the caller-object mirror in this same task** — the mirror is part of the fix, not a follow-on. Red-first test proves the field stays `None` today.
- [ ] A1c. Red-first test: `finalize_session` end-to-end with a stale caller object whose `response_delivered_at` is `None`; assert the stamp survives the trailing save. **Runs before A1b** — A1b depends on the mirror, so the checkbox order here matches the real dependency. Add a sibling case for the `output_handler.py::send` stamp for each stale-passing caller A1's enumeration found.
- [ ] A1b. Integration test: with the stamp and mirror present, the health check finalizes the row `completed` instead of requeuing to `pending`. This is the orphan-bounce reproduction.
- [ ] A2. Add the WARNING in `finalize_session` when an idempotent skip coincides with a bound live runner PID. No behavior change.
- [ ] A3a. Narrow `agent/output_handler.py:1468` and `:1526` to `update_fields`, one commit each, naming the fields now written. Include the stale-snapshot regression test, parametrized over both call sites.
- [ ] A3b. Run the grep enumeration over AgentSession `.save()` call sites and file "AgentSession `save()` hardening sweep" with the full output, citing this plan's Architectural Impact section. Record the issue number in the PR body. **No code fix for those sites in this PR.**
- [ ] A4. Narrow **both** rungs of `_pick_steer_target` to require a non-`None` slug equal to `lane_slug`; exclude `failed` in rung 2 only; keep rung 1 falling through to rung 2. Update the docstring to describe a filter rather than a preference. Turn A0's tests green.
- [ ] A5. Session-scope the resume steer in `resume_session`; replace the `:1153-1154` comment along with the behavior.
- [ ] A6b. Dedupe `TIMEOUT_NEEDS_ATTENTION_MESSAGE` to once per session. (A6a is deferred to the A3b follow-up issue; fold it into that issue's body rather than filing a third.)
- [ ] A7. Review the `tests/unit/test_session_health_*.py` modules flagged in Test Impact; update any case asserting the pre-fix contract.

### Track B

- [ ] B1. Add the "Match the room" section under `# Persona behaviors to keep` in `prime-pm-role.md` and under `# Teammate persona` in `prime-teammate-role.md`. Edit in place; verify inodes unchanged. Do not touch existing paragraphs or headers.
- [ ] B1b. Run `tests/unit/test_pm_progress_updates.py` and confirm it passes **unmodified**.
- [ ] B2. Correct `config/personas/segments/identity.md` (`:39-40`, `:57-59`) and `docs/features/pm-voice-refinement.md` (`:9`, `:17`, `:21`, `:29`, `:52`), including both wrong symbol references. Re-verify each line number against the file at build time; the doc may have drifted. Describe only the current status quo.

### Shared

- [ ] D1. Write `docs/features/agent-session-lifecycle-writes.md` and add it to the `docs/features/README.md` index.
- [ ] D2. Update `docs/features/session-steering.md` for session-scoped resume steers.
- [ ] V1. Run narrow-scope tests on touched modules only via `scripts/pytest-clean.sh`.
- [ ] V2. `python -m ruff check` and `python -m ruff format`.
- [ ] V3. Open the PR with `Closes #3270` and `Closes #3271`, Track B's commits first, and the A3b follow-up issue number in the body.

## Verification

1. Each red-first test was observed failing on the unfixed tree, and the failure is recorded in the PR body. This is the single most important verification step in the plan; a guard that was never red proves nothing.
2. `scripts/pytest-clean.sh` green on the touched modules only. Never a full-suite run from this worktree — parallel lanes collide on Redis state.
3. `python -m ruff check` and `python -m ruff format` clean.
4. Prime file inodes unchanged after editing (`ls -i` before and after), confirming the fleet hardlinks survived.
5. `tests/unit/test_pm_progress_updates.py` passes with no modification to that file.
6. Every symbol named in the corrected docs resolves to real code (grep each one).
7. Post-deploy observation, which is the only verification that closes the actual complaint: no unprompted `resume (sdlc-stall)` transitions on slugless rows in `logs/reflection_worker_error.log`, and replies in the room that read at human length.
8. Re-run the room-register census that produced the Problem section's numbers — median words per message, count of replies using `#` headers, count using numbered lists — against a post-deploy sample of the same chat, and record the before/after delta. The Problem section opened this measurement; without re-running it, B1 has no criterion an eroding edit could trip.

## Critique Results

Round 1 — FULL depth, independent roster (3 critics: Risk & Robustness, Scope & Value, History & Consistency). Verdict: **NEEDS REVISION**.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | A1's stamp site is clobbered by `finalize_session`'s own trailing full save. `models/session_lifecycle.py:404` calls `flush_deferred_self_draft_sync(session, status)`, which is documented at `:384-386` as "fresh-reading: re-reads get_authoritative_session() internally, so it is unaffected by the caller's possibly-stale session". `:607` then runs a bare `session.save()` (no `update_fields`) on the caller's stale object. Stamping only the fresh object writes `response_delivered_at` and has it immediately reset to `None` on the very save that finalizes the transition — on exactly the `bridge/session_transcript.py:343` path that produced the incident. A1 would ship, tests could pass against the fresh read, and the #918 guard would stay dead in production. | **Accepted.** A1 rewritten to require the caller-object mirror with the `extra_context` precedent's exact shape; new task A1c adds the `finalize_session` end-to-end red-first test, and a matching bullet was added to Failure Path Test Strategy. | Mirror the existing caller-object precedent: in the same block where the flush sets `delivered = True`, also mutate the caller's object, i.e. `if session is not None and session is not _clear_target: session.response_delivered_at = <same value written to _clear_target>`. This is the identical workaround already applied to `extra_context` a few lines below, whose comment states the full save "silently resurrects the just-cleared flag". The A1b integration test MUST drive the real `finalize_session` chain with a stale caller object whose `response_delivered_at` starts `None`, not `flush_deferred_self_draft_sync` in isolation — an isolated test passes while the production bug remains. |
| CONCERN | Scope & Value | The A3 sweep lists nine sites but the evidence backs at most two. The #3270 investigation names `agent/output_handler.py:1468` and `:1526` as PLAUSIBLE, explicitly REFUTES `agent/sdk_client.py:413` ("latent hazard, not this bug"), and has zero evidence for `health_check.py:654`, `session_tags.py:64/82/281`, `pipeline_state.py:495`, `valor_telegram.py:791` — included only "for consistency". The plan itself calls A3 its highest-blast-radius change and budget risk, so most of that radius is theoretical completeness rather than the demonstrated defect. | **Accepted.** A3 split into A3a (the two evidenced `output_handler.py` sites plus the stale-snapshot regression test, ships here) and A3b (grep enumeration filed as a separate hardening issue, no code fix here). Rabbit Holes, Risks, Test Impact, Success Criteria, and the task list all follow the split. | Split A3 into A3a (the two evidenced `output_handler.py` sites plus the stale-snapshot regression test) shipped in this PR, and A3b (everything else) filed as a separate "AgentSession save() hardening sweep" issue citing this plan's Architectural Impact section. Retain the grep as an *enumeration* step so no site is missed, but scope the *fix* in this PR to the evidenced sites. |
| CONCERN | Scope & Value | The plan bundles the low-risk, prompt-only #3271 fix with the highest-blast-radius lifecycle sweep for #3270 in one PR, behind one set of red-first tests and one review pass. The human's literal complaint ("I'm not your log file") is independently fixable and mergeable, but per V3 it ships only when the riskier fix is done. | **Deviation, named for the decider.** Lane identity gives this session exactly one worktree and one branch, so a two-PR split is not available without a second lane. Mitigation adopted: Track B commits first and stays independently revertable, recorded in Team Orchestration and in V3. The PM may overrule and authorize a second lane. | Constrained by lane identity: this session owns exactly one worktree and branch (`session/dev-1aafae58`), so one branch means one PR. Mitigation adopted instead of a split: commit Track B first and keep it independently revertable, so Track B can be cherry-picked out if Track A stalls in review. Recorded as a named deviation for the decider to override. |
| NIT | Scope & Value | `SELF_DRAFT_INSTRUCTION` fires only when the delivery validator rejects a message — a narrow corrective path, not the one that produced the 1029-word reply the human complained about. Changing this tested constant chases consistency rather than the demonstrated defect. | **Accepted.** Open Question 3 resolved as option (a); section B3 and task B3 deleted, `bridge/message_drafter.py` removed from Track B's file set, and the `tests/unit/test_message_drafter.py` row removed from Test Impact. | Resolve Open Question 3 in favor of option (a): leave the bullet mandate alone. Drop task B3 and remove the `tests/unit/test_message_drafter.py:284-287` row from Test Impact. |


Round 2 — FULL depth, independent roster (3 critics: Risk & Robustness, Scope & Value, History & Consistency), run against the revised plan. Verdict: **READY TO BUILD (with concerns)**. No blockers; the round-1 blocker was independently confirmed closed by the Risk critic.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | A1 stamps `response_delivered_at` at **two** sites — `flush_deferred_self_draft_sync` and the deferred-self-draft redraft path in `agent/output_handler.py::send` — but the caller-object mirror and task A1c cover only the first. Success Criteria nonetheless claims, unqualified, that the field "survives a full `finalize_session` call made with a stale caller object". A caller holding an object captured before the `output_handler` stamp landed hits the same `models/session_lifecycle.py:607` clobber, untested. | **Accepted.** A1 now opens with an enumeration of every `finalize_session` caller classified fresh-reading vs stale-passing, requires a sibling A1c test per stale-passing caller reachable from the `output_handler.py::send` stamp, and the Success Criteria bullet is now conditional on that enumeration instead of claiming more than the tests prove. | The gap is live only for `finalize_session` callers that do not freshly re-read immediately before the call. `bridge/session_transcript.py:316` re-reads via `get_authoritative_session()` before `:343` and self-heals; `agent/session_health.py:4659` passes a loop-variable `entry` and does not. Enumerate the `finalize_session` callers first, then either add a second red-first test for the `output_handler.py::send` stamp or narrow the Success Criteria claim to the flush path explicitly. Do not leave the claim broader than the test. |
| CONCERN | Scope & Value | The plan applies an evidence bar to A3 (defer unevidenced sites to a follow-up issue) but not to A6a. Problem A's own grep for rate-limit markers returns zero matches, which proves the path is unhandled, not that it ever fired in this incident; Open Question 1 confirms no structured signal exists to detect the condition. A6a ships here while A3b's comparably unevidenced sites are carved out. | **Accepted.** A6 split: A6b (timeout-notice dedupe) ships here; A6a (rate-limit routing) is deferred into the A3b follow-up issue. Failure Path Test Strategy, Rabbit Holes, Success Criteria, Agent Integration, Track A's file set, and Open Question 1 all updated; `harness/claude.py` is no longer touched by this plan. | After OQ1's resolution A6a is a blanket routing-policy change with no detector: "never relay harness failure text to the room". Either cite a concrete log occurrence of harness failure text reaching a room, or keep A6b (the evidenced timeout-notice dedupe, which has a named constant and a reproduced double-send) in this PR and move A6a to the same follow-up issue A3b files. |
| CONCERN | History & Consistency | Solution A1's prose still says "The A1b test is a `finalize_session` end-to-end test", but the revision moved that test to task **A1c**; A1b is now the separate orphan-bounce integration test. The stale label also creates a real ordering hazard: the task list reads A1 → A1b → A2 → A1c, so a builder working in checkbox order runs A1b before the mirror A1c adds, and A1b fails for the wrong reason. | **Accepted.** The A1 prose now names A1c, the caller-object mirror is folded into task A1's own text (the option the finding preferred), and the task order is now A1 → A1c → A1b → A2 so the checkboxes match the dependency. | Two edits, both required. First, change the A1 prose reference from A1b to A1c. Second, resolve the ordering: either reorder to A1, A1c, A1b, A2, or fold the caller-object mirror into A1's own task text so the checkbox order matches the real dependency. Folding into A1 is preferable because the Solution prose already describes the mirror as part of A1 itself. |
| NIT | Risk & Robustness | Success Criteria says the stale-snapshot guarantee holds "through either narrowed `output_handler.py` save site", but A3a and the Failure Path Test Strategy both say "the stale-snapshot regression test" in the singular and describe one generic scenario. | **Accepted.** A3a's task text and the Failure Path Test Strategy bullet both now require the stale-snapshot test to be parametrized over `:1468` and `:1526`, naming their differing `update_fields` sets. | `:1468` narrows to `["context_summary", "updated_at"]` and `:1526` to `["session_events", "updated_at"]` — different field sets, so a single test against one site says nothing about the other. State in A3a that the test is parametrized over both call sites. |
| NIT | Scope & Value | The Problem section computes exact room-register metrics (median 8 words vs ~247; 17 of 70 replies with headers; 18 with numbered lists) but no Success Criterion re-measures them after deploy, so an edit that erodes the B1 guidance has nothing to trip. | **Accepted.** Verification gained step 8: re-run the same census (median words, `#`-header count, numbered-list count) against a post-deploy sample of the same chat and record the before/after delta. | Verification step 7 currently says only "replies in the room that read at human length". Add the same word-count / header / list census that produced the Problem-section numbers, run against a post-deploy sample of the same room, with the before/after delta recorded. |


Round 3 — FULL depth, independent roster (3 critics), run against the twice-revised plan. Verdict: **READY TO BUILD (with concerns)**. No blockers. This round reaches `MAX_CONCERN_RECRITIQUE_ROUNDS` (3), so the remaining concerns are answered in the plan text and accepted on the record; the next stage is build.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | A4 fixes only rung 2 (resume) and leaves rung 1 (steer) with the identical defect. `reflections/sdlc_progress.py:856-857` reads `if live: return ("steer", max(live, key=_rank))`, and `_rank` treats the slug as a tiebreaker, so with no same-lane live candidate the stall check still steers an unrelated live session — Problem A's exact symptom through a different rung. Nothing in Race Conditions or A0's test list covers a live-bucket cross-lane pick. | **Accepted and verified independently against `reflections/sdlc_progress.py:844-858` before adoption.** A4 now covers both rungs: rung 1 requires the same non-`None` slug equality as rung 2, and A0 gains a red-first case for the live bucket. | Both buckets are ranked by the same `_rank` closure, so the fix is one predicate applied twice, not two fixes. Rung 1 must fall through to rung 2 (not to `create`) when no same-lane live row exists, or a resumable same-lane row would be skipped. The `failed`-row exclusion belongs to rung 2 only: `failed` is not in `NON_TERMINAL_STATUSES`, so it cannot reach the live bucket. |
| CONCERN | History & Consistency | Track A's file set still lists `agent/sdk_client.py`, `agent/health_check.py`, `tools/session_tags.py`, `agent/pipeline_state.py`, and `tools/valor_telegram.py`, but A3b — the only task naming them — does no code fix there; it greps and files a follow-up issue. `tools/valor_telegram.py` is additionally in No-Gos as a file not to touch, which directly contradicts its presence in the touched-files set. | **Accepted.** Track A's file set now lists only files a remaining task actually modifies, matching the pattern already applied when A6a's deferral dropped `harness/claude.py`. | The same edit was already made once on that line for the A6 split; extend it rather than inventing a second convention. `tools/valor_telegram.py` must leave the set outright — the No-Gos entry (its flags are asserted by an integration test, #2694) is the authoritative statement. |
| NIT | Scope & Value | B2 corrects `identity.md` and `pm-voice-refinement.md`, files the plan itself confirms have no production caller, so it is documentation hygiene rather than complaint-serving work. | **Kept, deliberately.** Both files make false claims about a condensing drafter that does not exist; leaving them is exactly the stale-doc trap that sent this investigation down a wrong path. Cost is a few lines. | No change required. |
| NIT | History & Consistency | Task B2 cites `docs/features/pm-voice-refinement.md:25` in addition to `:9, :17, :21, :29, :52`, but Solution B2 and the Documentation checklist list only the latter five. | **Accepted.** The stray `:25` is removed from the task so all three lists agree. | Line-level citation mismatch only; the builder should re-verify each line number against the file at build time regardless, since the doc may have drifted. |


---

## Open Questions

All three are resolved as of the round-1 revision. None remain open; nothing here blocks build.

1. **A6a rate-limit detection — is there a structured signal?** **Resolved: no, and A6a is scoped to the routing half only.** The harness deliberately avoids stderr substring matching (`harness/claude.py:658-659`) and no structured exit-code or result-field signal for a usage-limit stop exists. A6a therefore implements one rule: never relay harness failure text to the room, route it to the operator instead. No keyword detector is built. Round 2 took this one step further: with no detector and no logged occurrence, A6a itself is deferred to the follow-up hardening issue and only A6b ships here.

2. **A4's `failed`-row exclusion — any legitimate caller?** **Resolved: the exclusion stays.** No caller was found that depends on resuming a genuinely `failed` lane session from the stall rung, and the ladder's `create` rung is a strictly safer fallback. This is a production recovery behavior change and is listed under Risks for post-deploy observation.

3. **B3 scope — how far to reconcile `SELF_DRAFT_INSTRUCTION`?** **Resolved as option (a): leave it alone.** It fires only when the delivery validator rejects a message — a narrow corrective path, not the one that produced the 1029-word reply. Section B3 and task B3 are deleted; `bridge/message_drafter.py` is untouched by this plan.
