---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2701
last_comment_id: 5237664288
revision_applied: true
revision_applied_at: 2026-09-02T06:46:53Z
---

# Render /ask-me questions as native Telegram polls (group chats, eng sessions)

## Scope (settled after the Task 1 capability probes — read this first)

The original plan targeted 1:1 DMs. **That premise is dead.** The capability matrix was
settled by live probes on 2026-08-10 and is recorded on
[#2701](https://github.com/tomcounsell/ai/issues/2701#issuecomment-5237014662):

| Sender | Chat type | Polls | Inline keyboard buttons |
|--------|-----------|-------|-------------------------|
| User account (the bridge, Telethon/MTProto) | 1:1 DM | ❌ `MediaInvalidError` | ❌ bot-only primitive |
| User account | Group | ✅ verified live in `Eng: Valor` (`-1003449100931`) | ❌ bot-only primitive |
| Bot account (Bot API) | 1:1 DM | ✅ verified | ✅ verified |

**These are settled facts, not open questions. Do not re-probe them, and do not send test polls
into any chat to re-confirm them.**

The bot path was rejected on **identity**, not capability: a bot cannot post into a user-to-user
chat, so its question would land in a separate `@bot` conversation and break the thread scoping
sessions depend on. It would also add a second Telegram identity, a new inbound transport, and
`projects.json` contact-ownership churn.

**Owner decisions (Valor), not reopened by this plan or by critique:**

1. **Group chats only, via the existing user account.** DMs keep today's prose behavior.
2. **Engineering sessions only.** Delivery requires the destination chat to be a group **AND**
   `session_type == "eng"`. Either condition failing falls back to prose. `teammate` never gets a
   poll.
3. **No mid-turn blocking.** Turn-boundary rendering plus vote→steering translation only.
4. The final poll option is always the literal `Other: wait for followup message`, answered by a
   plain-text followup the human replies to.
5. `/ask-me`'s one-question-at-a-time rule becomes a **preference**; the hard prohibition is removed.
6. **Reaction-based answering is dropped.** Do not revive it. Telethon exposes no high-level
   reaction event, the bridge only ever sends reactions and never listens, and confirming push
   delivery would require a second client sharing the bridge's auth key.

**Anti-scope:** no bot identity; no new inbound transport (votes become steering messages on the
existing path); email and local-session surfaces keep today's prose behavior.

## Problem

When engineering work happens in a Telegram **group** rather than a local terminal, a blocked agent
asks its question as prose. The human has to read a paragraph on a phone and compose a written
answer. The interaction `/ask-me` was designed around — glance, tap, done — is lost, so
questions get answered slowly, partially, or not at all, and the agent stays blocked.

**Current behavior:**

- `AskUserQuestion` never prompts under headless `claude -p`. It fires a `needs_human` hook
  edge (`agent/session_runner/hook_edge.py:363-369`, matcher at `:114`), `_reconcile_turn_end`
  surfaces it (`agent/session_runner/role_driver.py:342-377`), and `runner.py:1526-1531`
  exits `ExitReason.PM_NEEDS_HUMAN` after delivering the question as plain text.
- The question reaches Telegram through the normal outbox → relay seam as prose.
- The bridge registers only `events.NewMessage` (`bridge/telegram_bridge.py:1165`) and
  `events.MessageEdited` (`:2523`). There are zero `events.Raw` handlers in the repo, so the
  bridge has no concept of a structured answer.
- There is zero Telegram-poll code anywhere in `bridge/` or `tools/`.
- Nothing on the outbound relay path knows a session's `session_type`.
  `bridge/telegram_relay.py` works from `session_id` and `chat_id`; the persona → `session_type`
  mapping lives at `bridge/routing.py:568 persona_to_session_type`, which only the inbound handler
  and the catchup scanners call.

**Desired outcome:**

A question an **eng** session poses via `/ask-me` in a **group** chat is delivered as a native
Telegram poll with the recommended option first and `Other: wait for followup message` last. One
tap records the answer, the bridge converts the vote into a steering message, and the session
resumes on its next turn with the choice in hand. No new blocking primitive, no new inbound
message path, and **every ineligible surface — DMs, `teammate` sessions, email, local sessions —
degrades to today's prose behavior at a single decision point.**

## Freshness Check

**Verification baseline:** `5021a40aa` — the single commit every `file:line` and symbol reference in
this document was last re-verified against. Where an older sha appears anywhere else in this plan's
history, this line supersedes it.
**Issue filed at:** 2026-08-10T04:47:24Z

### Re-verification 2026-09-02 (revision cycle 5) — **Minor drift, one substantive change**

Three weeks passed between cycle 4 and this pass. Fourteen commits on main touched files this plan
references. Every symbol the plan names still exists and still means what the plan says it means;
**every line offset has moved** (the reply-to ladder alone shifted ~+100 lines). The plan already
carries the "locate by symbol, the offset is a hint" instruction under Technical Approach — that
instruction is now doing real work and builders must obey it literally. Representative corrections,
re-verified at `5021a40aa`: `_ack_steering_routed` `:904`→`:943`; `_build_completed_resume_text`
→`:804`; `_completed_created_at` `:1898`→`:2017`; the steering ladder `:1799-1866`→`~:1899-1966`; the
completed-dispatch block `:1951-2005`→`~:2051-2124`; `_steering_session_enqueued` `:2006`→`:2124`;
`events.NewMessage` `:1165`→`:1261`; `events.MessageEdited` `:2523`→`:2681`; `replay_dead_letters`
`:2842`→`:3019`; `_dead_letter_message` `:803`→`:946`; `process_outbox` `:850`→`:1018`;
`_record_sent_message` `:655`→`:798`; `_bind_outbound_message_to_job` `:258`→`:389`;
`_record_sent_reaction` `:182`→`:357`; the unknown-type dispatch guard `:911-915`→`:1054`;
`set_reaction` `:320`→`:433`; `read_thread` `:371`→`:351`; `sweep_chat` `:560`→`:496`;
`draft_message` `:1016`→`:1028`; `_validate_for_medium` `:561`→`:560`; `session_type` field
`models/agent_session.py:156`→`:158`. Unchanged and re-confirmed: `KNOWN_MESSAGE_TYPES` at
`telegram_relay.py:58`, `build_telegram_outbox_payload` at `output_handler.py:270`, the `send`
capability at `:611` and `dropped_empty` at `:675`, `_is_group_chat` at `read_the_room.py:126`,
`claim_message` at `dedup.py:171`, `_send_cb_accepts_file_paths` at `adapter.py:61`, `MessageDraft`
at `message_drafter.py:193`, `_has_valor_reply_after`/`_render_transcript` in `agent_catchup.py`,
and **zero `events.Raw` handlers repo-wide**.

**The one substantive change — the steering overlap resolved against this plan, not for it.**
`docs/plans/flip-steering-writers-to-room-key.md` was an *active* plan at cycle 4 and this document
recorded it as a coordination note ("we add a new caller, so we inherit whatever it lands"). It
**landed** (`ac190fb26`) and is now archived under `docs/archive/plans-completed/`. Inheriting it is
no longer passive: `push_steering_message` gained a `room_id` parameter, the Room leg is live with
**no feature flag**, and the writer *deliberately never looks a session up* — so **the caller must
derive and pass `room_id` or the write silently falls back to the legacy key**. Every production
caller in the reply-to ladder now passes `room_id=room_id_for_session(<the session it selected>)`
(`telegram_bridge.py:1927`, `:1956`, `:1996`, `:2336`, `:2784`, `:2824`). A vote-originated steer
that omits it would land on the legacy leg while every peer writes the Room leg — a real defect, and
the reason this is called out rather than left to the builder. Corrected in **Technical Approach**
and **Task 9b**, with a fifth item added to the extraction's behavior-preservation checklist.

**Prior build work is gone; BUILD starts from zero.** Issue comment `5237664288` (the operator's
2026-08-10 PAUSE) records branch `session/ask-me-telegram-polls` at tip `80dcae8e9` with six
commits and a live worktree. **None of it survives**: no local branch, no remote ref, no worktree,
and all six SHAs fail `git cat-file` — the objects were garbage-collected. There is nothing to
rebase, audit, or recover. This is not a loss worth mourning: the operator's own note flags those
commits as produced by a build agent running outside its supervisor's loop and "unaudited until it
passes TEST and REVIEW". The one durable result is preserved as **spike-8** below.

**Disposition: proceed.** Minor drift on offsets, one substantive correction applied, no change to
scope, appetite, task topology, or the settled capability matrix.
**Disposition (revision cycle 4):** **Major drift — resolved by re-scope.** The plan's core premise
(polls into 1:1 DMs) was disproved by its own Task 1 gate and the issue was re-scoped by the owner
(see **Scope** above and issue comments `5236653597` / `5237014662`). This revision replaces the
dead premise rather than closing the issue: the feature remains buildable and worth building on the
group-chat surface, which is where eng sessions actually run.

**What the gate FAIL invalidated:** Risk 1 (now a settled constraint, see **Risks**), the old Task 1
DM-capability gate, the DM-only voter-disambiguation assumption in Research finding 5, and every
"1:1 DM" framing in Rabbit Holes and Success Criteria. All are rewritten below.

**What survived unchanged and is deliberately kept:** the outbound outbox → relay seam analysis
(spike-3), the catchup transcript-rendering finding (spike-4), the plain-Redis registry precedent
(spike-5), the `UpdateMessagePoll` cannot-self-route finding (spike-2), the dedup/idempotency
design, the message-drafter validate-only split, the handled-detection analysis, and the
`bridge/answer_routing.py` extraction. None of that work depended on the chat type.

**Original freshness evidence (still valid, verified at first planning):**

The issue was filed roughly six minutes before planning began and no commits landed on main
in that window. The `## Recon Summary` gate passes
(`python .claude/hooks/validators/validate_issue_recon.py 2701` → "Found 4 buckets with 7 items").
Every cited reference was still re-verified by hand:

**File:line references re-verified:**

- `bridge/telegram_bridge.py:1165` — issue claims the only handler registration is
  `events.NewMessage` — **still holds**; `@client.on(events.NewMessage)` is at `:1165`, its
  `handler(event)` at `:1166`. Correction worth recording: there is a *second* handler,
  `@client.on(events.MessageEdited)` at `:2523`. The issue's underlying claim ("no structured
  answer concept") is unaffected; a repo-wide grep for `events.Raw` returns nothing.
- `agent/session_runner/hook_edge.py` — the `needs_human` edge — **still holds**;
  `NEEDS_HUMAN = "needs_human"` at `:119`, `_ASK_USER_MATCHER` at `:114`, classification at
  `:342-369`.
- `bridge/response.py` performs raw MTProto calls — **still holds**; `SendReactionRequest`
  imported at `:34`, used at `:350/:363/:379` inside `set_reaction` (`:320`).
- `tools/send_message.py` → `telegram:outbox:{session_id}` → `bridge/telegram_relay.py::process_outbox`
  — **still holds, with a correction**: `tools/send_message.py` no longer writes the outbox on
  the happy path. It delegates to `TelegramRelayOutputHandler().send(...)`
  (`tools/send_message.py:271-282`); the canonical payload builder is
  `agent/output_handler.py:270 build_telegram_outbox_payload`. The raw rpush at
  `tools/send_message.py:102` is a diagnostic-only legacy fallback behind
  `ALLOW_LEGACY_RPUSH_FALLBACK=1`. Plan targets the canonical builder, not the legacy path.
- Telethon 1.42 installed and poll types available — **verified**:
  `telethon 1.42.0`, `Poll(id, question: TextWithEntities, answers: list[PollAnswer], closed,
  public_voters, multiple_choice, quiz, close_period, close_date)`,
  `PollAnswer(text: TextWithEntities, option: bytes)`,
  `InputMediaPoll(poll, correct_answers, solution, solution_entities)`.

**Cited sibling issues/PRs re-checked:**

- #1802 (PM file-capable send path) — closed. Its real shape is not "a new payload key" but
  **schema slot → adapter capability probe → payload key → relay dispatch branch**
  (`agent/session_runner/router.py:61-77`, `adapter.py:61`, `telegram_relay.py:436`). That is
  the template this plan follows.
- #1955 (drafter local-file-path awareness) — closed. Confirms the precedent that a new
  outbound message shape needs matching drafter awareness.
- #1688 / #1922 — established the `needs_human` edge; still the live trigger.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none as of `5021a40aa`. The one overlap
this plan carried — `flip-steering-writers-to-room-key.md` — **shipped** (`ac190fb26`) and is
archived. It is no longer a coordination note; it is a hard requirement on the vote translator. See
the 2026-09-02 re-verification above, and Technical Approach / Task 9b for the corrected call shape.

## Prior Art

`gh issue list --state closed --search "poll telegram"` and
`gh issue list --state all --search "ask-me AskUserQuestion"` found **no prior attempt to
render questions as polls**. #2701 is the first. Related work that shapes the approach:

- **#1802**: PM file-capable send path. Added `file_paths` to the outbox payload. Establishes
  the four-step template for a new outbound capability: optional schema slot → capability
  probe on the callback → optional payload key → relay dispatch branch. Succeeded.
- **#1955**: Message drafter local-file-path awareness. Precedent that a new outbound shape
  needs matching drafter awareness rather than bypassing the comms layer. Succeeded.
- **#1688 / #1922**: Established the `needs_human` hook edge and `ExitReason.PM_NEEDS_HUMAN`.
  This is the trigger the feature renders; no change to it is needed.
- **#1779**: Mid-run steering for granite PTY sessions (bridge→PM injection). Retired with the
  PTY thesis, but it confirms `agent/steering.py` as the settled injection seam.
- **#1574**: `valor-telegram` deterministic bridge loop-guard and bot registry — relevant to
  the integration-test story for a new bridge handler.
- **#2020**: Gate bridge Redis storage to machine-owned chats. Relevant because the poll
  registry this plan adds is a new bridge-side Redis key and must respect the same ownership
  posture (only machine-owned chats produce registry entries).

No prior fix failed, so there is no **Why Previous Fixes Failed** section.

## Research

**Queries used:**

- `Telethon send poll InputMediaPoll user account MTProto example 2025`
- Local introspection of the installed Telethon 1.42 TL types (authoritative over web results).

**Key findings:**

1. **Constructing a poll is a raw-MTProto one-liner in Telethon.**
   `InputMediaPoll(poll=Poll(id=<any int>, question=TextWithEntities(...),
   answers=[PollAnswer(text=TextWithEntities(...), option=bytes([i])) for i, ...]))`, sent via
   `client.send_message(chat, file=media)` or `messages.SendMediaRequest`. Each `PollAnswer.option`
   must be unique **bytes**; the client-supplied `Poll.id` is a placeholder — the **server assigns
   the real poll id**, readable back off the sent message's `MessageMediaPoll.poll.id`.
   Source: <https://tl.telethon.dev/constructors/input_media_poll.html>,
   <https://github.com/LonamiWebs/Telethon/issues/1356>.
   *Informs:* `bridge/response.py::send_poll` needs no new dependency and must **return the
   server-assigned poll id alongside the message id** so the vote registry can be keyed on it.

2. **On layer 1.42, question and answer text are `TextWithEntities`, not `str`.** Older
   examples (2019-era) that pass bare strings will `TypeError`. Verified locally against the
   installed signatures.
   *Informs:* a small `_text(s) -> TextWithEntities` shim in `bridge/response.py`.

3. **`UpdateMessagePoll` carries no peer and no message id.** Verified signature:
   `UpdateMessagePoll(poll_id: int, results: PollResults, poll: Poll | None)`. There is
   therefore **no way to route a vote back to a chat/session from the update alone**.
   *Informs:* the single most important design consequence in this plan — a persistent
   `poll_id → {chat_id, msg_id, session_id, question, options}` registry, written at send time,
   is mandatory. Without it the whole inbound half is impossible.

4. **`PollResults` can be partial.** `PollResults(min, results: list[PollAnswerVoters] | None,
   total_voters, recent_voters, ...)` and `PollAnswerVoters(option: bytes, voters: int, chosen,
   correct)`. When `min=True` the per-option breakdown may be absent or account-relative.
   `messages.GetPollResultsRequest(peer, msg_id)` returns the full results on demand, and
   `messages.GetPollVotesRequest` is **user-account-only** (a rare case where the user-account
   posture is an advantage).
   *Informs:* the translator must treat `UpdateMessagePoll` as a *hint* and confirm through
   `GetPollResultsRequest` before acting.

5. **Individual-vote updates (`updateMessagePollVote`) are bots-only.** A user account observes
   aggregate counts. **In a group — the only surface this plan targets — several options can each
   carry `voters >= 1`, and `PollResults` gives no ordering, so "the first vote" is not derivable
   from the aggregate.** The one accessible route to per-voter detail is
   `messages.GetPollVotesRequest`, which is **user-account-only** (a rare case where the
   user-account posture is an advantage) and returns `MessageUserVote` entries carrying the voting
   peer and their chosen option.
   *Informs:* the translator's option-selection rule must be **deterministic under multiple
   voters** (see Technical Approach: "Option selection in a group"), and `GetPollVotesRequest` is
   used **best-effort for attribution only** — to name the voter in the steer text — never as the
   correctness path, so a failure degrades to the aggregate rule plus a generic sender name.

6. **Closing a poll is an edit re-sending the same poll with `closed=True`.** The 2019 Telethon
   bug report about `EditMessageRequest.random_id` is long fixed on 1.42.
   Source: <https://github.com/LonamiWebs/Telethon/issues/1355>.
   *Informs:* closing the poll on first translation is the natural idempotency and
   "already answered" visual marker. **In a group this is also the first-voter-wins boundary** —
   see Technical Approach.

7. **`session_type` is reachable on the relay path for one query.** `bridge/telegram_relay.py`
   already runs `AgentSession.query.filter(session_id=session_id)` in `_record_sent_message`
   (`:655`), `_record_relay_sent_draft` (`:679`) and `_bind_outbound_message_to_job` (`:258`), and
   `session_type` is a first-class `KeyField` on the model (`models/agent_session.py:156`). So the
   eng-only gate costs one existing-shaped lookup, not a new plumbing path.
   *Informs:* Task 3 (`bridge/poll_gating.py`) rather than threading `session_type` through the
   outbox payload, which would let a stale payload outlive a session's real type.

8. **Group-vs-DM is already discriminated by the sign of `chat_id` in this repo.**
   `bridge/read_the_room.py:126 _is_group_chat` documents the rule (Telegram assigns negative ids
   to groups/supergroups/channels, positive to user peers) and is conservative on ambiguity —
   an unparseable or `None` id returns `False`.
   *Informs:* the group half of the eligibility gate reuses that exact predicate, promoted to a
   public shared home, rather than a second copy that could drift.

## Spike Results

### spike-1: Telethon 1.42 exposes everything needed with no new dependency
- **Assumption**: "Telethon 1.42 already provides poll construction and vote observation."
- **Method**: code-read (local introspection of installed package)
- **Finding**: Confirmed. `telethon 1.42.0`; `InputMediaPoll`, `Poll`, `PollAnswer`,
  `TextWithEntities`, `MessageMediaPoll`, `UpdateMessagePoll`, `PollResults`,
  `PollAnswerVoters` all import cleanly. Signatures recorded under **Research** above.
- **Confidence**: high
- **Impact on plan**: No dependency work. `bridge/response.py` is the correct home; it already
  imports from `telethon.tl.functions.messages` and `telethon.tl.types`.

### spike-2: `UpdateMessagePoll` cannot route a vote on its own
- **Assumption**: "An `events.Raw` handler on `updateMessagePoll` is sufficient to translate a
  vote into a steering message."
- **Method**: code-read (TL signature introspection)
- **Finding**: **Invalidated.** `UpdateMessagePoll(poll_id, results, poll)` has no `peer` and no
  `msg_id`. The issue's Solution Sketch is therefore incomplete as written.
- **Confidence**: high
- **Impact on plan**: Adds a mandatory component the issue did not name — the poll registry
  (Task 6) — and makes `GetPollResultsRequest` reconciliation (Task 10) the durable mechanism
  rather than an optional extra.

### spike-3: the outbound seam has three hard blockers for a text-less payload
- **Assumption**: "A poll payload can ride the existing outbox → relay seam unchanged."
- **Method**: code-read
- **Finding**: Partially invalidated. Three guards drop a text-less payload before it ships:
  `agent/output_handler.py:673-674` (`if not text: return DeliveryOutcome.dropped_empty`),
  `bridge/telegram_relay.py:58` + the dispatch guard at `:911-915` (`KNOWN_MESSAGE_TYPES = {None, "reaction",
  "custom_emoji_message"}`, unknown type discarded with no retry), and
  `bridge/telegram_relay.py:460-462` (`if not text and not file_paths: skip malformed`).
  Additionally `bridge/message_drafter.py:1016 draft_message(raw_response: str, ...) -> MessageDraft`
  takes and returns text only — `MessageDraft` (`:193`) has no field that can carry options.
- **Confidence**: high
- **Impact on plan**: Tasks 2, 3, 5 are scoped precisely around these three guards plus a
  validate-only drafter medium, rather than a vague "extend the payload".

### spike-4: an outbound poll is already counted as a reply but renders as a blank line
- **Assumption**: "Existing handled-detection will misfire on a text-less outbound poll."
- **Method**: code-read
- **Finding**: Split result. `_has_valor_reply_after` (`bridge/agent_catchup.py:436-452`) is
  position + `is_valor` based and never inspects text, so a poll **does** suppress the recovery
  enqueue — correct behavior, no change needed. But `read_thread` (`:371`) does `text = m.text or ""`
  with no media inspection, `_render_transcript` (`:419`) emits a bare `"Valor: "` line, and
  `sweep_chat` (`:560-562`) skips empty-text messages before judging. So the LLM judge sees a
  blank utterance where the question should be.
- **Confidence**: high
- **Impact on plan**: Task 11 is narrow and surgical (render `MessageMediaPoll` into the
  transcript) rather than a rework of handled-detection.

### spike-5: there is a settled precedent for a non-Popoto message→identity registry
- **Assumption**: "A new poll registry needs a Popoto model and therefore a migration."
- **Method**: code-read
- **Finding**: Invalidated. Two existing plain-Redis registries already do exactly this shape:
  `bridge/context.py:527` `SET session_root:{chat_id}:{msg_id} <root> NX EX 604800` and
  `bridge/job_router.py:85-96` `SET reply:{chat_id}:{msg_id} <json> NX` (no TTL, module docstring
  at `:6-18` explains the deliberate non-Popoto choice to stay outside index-drift/rebuild).
- **Confidence**: high
- **Impact on plan**: No Popoto model, therefore **no `scripts/update/migrations.py` entry**.
  The poll registry follows `bridge/job_router.py` naming with a TTL.

### spike-6: the surface question, answered by live probe (SETTLED — do not re-run)
- **Assumption**: "The bridge's user account can render a poll where `/ask-me` conversations happen."
- **Method**: prototype (live MTProto sends from a temp copy of the bridge session, updates
  disabled), run 2026-08-10 during the aborted build.
- **Finding**: **Half invalidated, half confirmed.** Into a real user DM (Tom, `179144806`,
  resolved from `projects.json` `dms.whitelist` — not Saved Messages) the identical
  `Poll`/`PollAnswer`/`InputMediaPoll`/`SendMediaRequest` construction was rejected verbatim with
  `MediaInvalidError('Media invalid (caused by SendMediaRequest)')`. The same construction into the
  `Eng: Valor` group (`-1003449100931`) **succeeded**, returned server-assigned
  `poll id 6325451705529927015` at `msg id 1318`, and was deleted. A follow-up bot-account probe
  confirmed a bot can send both polls and inline keyboard buttons into a private chat.
- **Confidence**: high (reproducible, verbatim MTProto errors on both sides)
- **Impact on plan**: the whole re-scope. Group-only via the user account; DMs keep prose; the bot
  path rejected on identity grounds; **Risk 1 has fired and is now a constraint, not a risk**; the
  old Task 1 DM-capability gate is deleted and replaced by the vote-readback gate.

### spike-7: `session_type` is not on the outbound relay path today
- **Assumption**: "The relay can already tell an eng session from a teammate session."
- **Method**: code-read
- **Finding**: **Invalidated.** `bridge/telegram_relay.py` carries only `session_id` and `chat_id`
  through the outbox; the persona → `session_type` mapping (`bridge/routing.py:568
  persona_to_session_type`) is called only from the inbound handler and the catchup/reconciler
  scanners. The relay *can* reach it cheaply — it already queries `AgentSession` by `session_id`
  in three places — but nothing does so today.
- **Confidence**: high
- **Impact on plan**: adds Task 3 (`bridge/poll_gating.py`), a shared eligibility predicate read at
  ask time by the CLI and re-read at send time by the relay. Rejects the alternative of stamping
  `session_type` into the outbox payload, which would let a queued payload outlive a session's real
  type.

### spike-8: a vote IS readable back by the sending user account in a group (Gate 1 PASS, salvaged)
- **Assumption**: "A user account can read back a vote cast on its own group poll" — i.e. Task 1.
- **Method**: prototype, run 2026-08-10 by the build that was later paused and whose commits are
  gone (commit `1b6c4c13a`, recorded in issue comment `5237664288`).
- **Finding**: **PASS.** Vote readback by a user account in a group works. That was the last open
  *design* risk after the DM rejection, and it is the finding that keeps this plan buildable.
- **Confidence**: medium — **lower than every other spike here, deliberately.** The code that
  produced it was written by an agent running outside its supervisor's loop, was never reviewed or
  tested, and no longer exists to re-read. The claim is credible and consistent with Research
  findings 4 and 5, but its provenance is not audit-grade.
- **Impact on plan**: **Task 1 is NOT deleted.** It stays a hard gate. The probe is cheap, needs no
  human, and re-establishes the result under supervision with output pasted into the PR — which is
  exactly what an unaudited PASS cannot do. Treat spike-8 as a strong prior that Task 1 will pass,
  never as a substitute for running it. Task 2 (the human-tap gate) was left **UNRESOLVED** by that
  same build and remains genuinely open.

## Data Flow

**Outbound (question → poll on screen):**

1. **Entry point** — an agent running `/ask-me` inside a headless bridge session decides it is
   blocked and needs a judgment call.
2. **Skill branch** — `/ask-me` detects the surface. Interactive local session → `AskUserQuestion`
   (unchanged). Headless bridge session (`TELEGRAM_CHAT_ID` + `VALOR_SESSION_ID` set) → invokes
   the new `valor-ask-poll` CLI via Bash.
3. **`tools/ask_poll.py`** — resolves transport with the same precedence as
   `tools/send_message.py:63 _resolve_transport()` (`VALOR_TRANSPORT` → `EMAIL_REPLY_TO` →
   `TELEGRAM_CHAT_ID` → `"telegram"`). Non-telegram transport → renders question + numbered
   options as plain text and hands off to the existing `send_message` path.
3a. **Eligibility gate — `bridge/poll_gating.py::poll_eligible(chat_id, session_id)`.** Telegram
   transport is necessary but not sufficient. A poll ships only when the chat is a **group**
   (`is_group_chat(chat_id)`, the negative-id discriminator) **and** the session's
   `session_type == "eng"`. Anything else — a DM, a `teammate` session, a missing session record,
   an unparseable chat id, or any exception — returns ineligible with a named reason and the CLI
   renders prose through the ordinary `send_message` path. **Degradation happens here, once**, and
   the reason is logged so a question that "should have been a poll" is diagnosable.
4. **`TelegramRelayOutputHandler.send_poll(...)`** (new sibling of `send`, `agent/output_handler.py:611`)
   — validates via a new drafter medium, builds the payload with a new
   `build_telegram_poll_outbox_payload(...)` next to `build_telegram_outbox_payload` (`:270`) —
   which mints the `poll_id_hint` correlation key — and rpushes to `telegram:outbox:{session_id}`
   with the same `OUTBOX_TTL` (`:431`). **It records no expectation**: its sibling `send` (`:611`)
   records none, and an expectation with no resolution path in any of Task 9b's four steering
   branches would be authored and never closed. Struck in revision cycle 5.
5. **`bridge/telegram_relay.py::process_outbox`** (`:850`) — `"poll"` added to
   `KNOWN_MESSAGE_TYPES` (`:58`); new dispatch branch inside the `:917-931` if/elif chain calls
   `_send_queued_poll`, which **re-checks `poll_eligible(chat_id, session_id)` before the wire**
   (defense in depth: the CLI decided at ask time, the relay is the last writer before the send,
   and a payload can sit in the outbox across a session-type change). Ineligible → convert to the
   same plain-text payload the terminal-failure path uses and deliver that instead of dropping.
6. **`bridge/response.py::send_poll`** — raw MTProto `InputMediaPoll`; returns
   `(msg_id, server_poll_id)`.
7. **Registry write** — a provisional `telegram:poll:pending:{poll_id_hint}` row is written
   *before* the send (Race 6) and promoted after it. The real row is
   `SET telegram:poll:{poll_id} <json> NX EX <ttl>` holding
   `{chat_id, msg_id, session_id, question, options, created_at}`. Plus the existing post-send
   bookkeeping (`_record_sent_message` `:984`, `_append_outbound_chat_log` `:988`,
   `_bind_outbound_message_to_job` `:994`) and a new `store_message(..., message_type="poll")`
   history row modelled on `_record_sent_reaction` (`:182-212`).
8. **Output** — a native poll in the chat.

**Inbound (tap → agent resumes):**

1. **Entry point** — the human taps an option. This produces **no Telegram message**; only an
   `updateMessagePoll` broadcast with aggregate counts.
2. **Fast path** — new `@client.on(events.Raw)` handler filters `UpdateMessagePoll`, reads
   `update.poll_id`, and calls `translate_poll_vote(poll_id)`.
3. **Durable path** — a reconciliation loop scans unanswered `telegram:poll:*` registry entries
   on an adaptive interval and calls the same `translate_poll_vote(poll_id)`. This is what makes
   the feature survive a bridge restart, a dropped update, and the Task-1 unknown about whether
   `updateMessagePoll` reaches a user account for its own poll at all.
4. **`bridge/poll_vote.py::translate_poll_vote(poll_id)`** — idempotent. Claims `SET telegram:poll:answered:{poll_id}
   1 NX EX <ttl>`; a lost claim returns immediately. Confirms results with
   `messages.GetPollResultsRequest(peer=chat_id, msg_id=msg_id)` rather than trusting a possibly
   `min=True` update. Selects the chosen option under the **deterministic group rule** (see
   Technical Approach: exactly one `voters >= 1` → use it; several → highest `voters`, ties broken
   by lowest decoded option index, with a warning). Attributes the voter best-effort via
   `GetPollVotesRequest`. Closes the poll by editing it with `closed=True` — in a group this is the
   **first-voter-wins** boundary, and it is deliberate: the poll exists to unblock one agent, not to
   take a vote of the room.
5. **`resolve_answer_target(session_id)` + branch** (new `bridge/answer_routing.py`) — the
   side-effect-free status ladder factored out of `bridge/telegram_bridge.py:1799-1866`, returning
   `LIVE` / `PENDING` / `LIVE_GUARD` / `COMPLETED` / `NONE`. Steer kinds →
   `push_steering_message`. `COMPLETED` → `resume_completed_session(...)`, the dispatch block
   factored out of `~:2051-2124`, called with the poll's own `msg_id` and no reply chain. `NONE` →
   log and return. See the Technical Approach for the full signatures and the per-branch contract.
6. **`agent/steering.py:176 push_steering_message(session_id, text, sender, room_id=...)`** — the
   sole inbox. `room_id` is derived by the caller via `models.room.room_id_for_session`; omitting it
   silently downgrades the write to the legacy key (see Technical Approach).
7. **Worker turn boundary** — `runner.py:1290 _drain_steering_boundary()` merges the steer into
   the next `claude --resume` turn's user message.
8. **Output** — the session resumes with the chosen option text in its input. If the choice was
   `Other: wait for followup message`, the steer instructs the agent to send a narrowed plain-text
   followup, which the human answers by reply-to — resuming the same session through the
   already-working `resolve_root_session_id` path (`bridge/context.py:536`).

## Architectural Impact

- **New dependencies**: none. Telethon 1.42 is already installed and already used for raw MTProto
  in `bridge/response.py`.
- **Interface changes**:
  - New optional outbox payload variant `type: "poll"` (additive; existing producers unaffected).
  - New `build_telegram_poll_outbox_payload(...)` in `agent/output_handler.py`.
  - New `OutputHandler.send_poll(...)` capability, discovered by probe (precedent
    `adapter.py:61 _send_cb_accepts_file_paths`) rather than added to the `OutputHandler`
    Protocol as a required method — `FileOutputHandler` and `EmailOutputHandler` must stay valid
    without it.
  - New `medium="telegram_poll"` validate-only branch in `bridge/message_drafter.py`, reached
    through a new public `validate_poll_question(question) -> list[Violation]` wrapper (the private
    `_validate_for_medium` signature is unchanged).
  - New module `bridge/answer_routing.py` holding `resolve_answer_target` and
    `resume_completed_session`, both factored out of the existing reply-to ladder.
  - New module `bridge/poll_gating.py` holding
    `poll_eligible(chat_id, session_id) -> PollEligibility(ok, reason)`. It **imports**
    `is_group_chat` from `bridge/read_the_room.py` (where the predicate is merely renamed from the
    private `_is_group_chat` to the public `is_group_chat`) rather than owning it — a generic
    Telegram peer-type predicate must not live in a feature module, and read-the-room must not end
    up importing from a poll module. One definition, natural dependency direction.
  - New module `bridge/poll_registry.py` holding the registry key constants and every registry
    helper (Task 6).
  - New module `bridge/poll_vote.py` holding `translate_poll_vote`, which imports
    `resolve_answer_target` / `resume_completed_session` from `bridge/answer_routing.py`. The
    translator is deliberately **not** housed in `answer_routing.py`: that module's whole purpose is
    to be a poll-independent seam shared with the reply-to ladder, landed as its own commit so
    `git revert <9a-sha>` stays a real option (Task 9a).
  - New CLI entry point `valor-ask-poll` in `pyproject.toml [project.scripts]`.
- **Coupling**: adds one new coupling — the bridge now holds inbound state (the poll registry)
  keyed on a Telegram-server-assigned id. Contained to two plain Redis keys with TTL, matching
  `bridge/job_router.py`'s deliberate non-Popoto posture.
- **Data ownership**: the relay becomes the writer of the poll registry (it is the only component
  that sees the server-assigned poll id). The bridge handler and reconciliation loop are readers
  plus claim-writers. No existing component's ownership changes.
- **Observability**: one new external liveness signal, `telegram:poll:reconcile:heartbeat`
  (`SET EX POLL_RECONCILE_HEARTBEAT_TTL_S` at the top of every reconciliation tick), read at one
  **existing** health surface — the dashboard health payload or the bridge health check. No new
  service and no second watchdog. It exists because the loop's own `poll_expired_unanswered` warning
  is emitted from inside the loop and is therefore blind to the loop dying (Risk 7).
- **Reversibility**: high **for the poll path** — removing the dispatch branch, the Raw handler, the
  reconciliation loop, the heartbeat and the CLI reverts to today's prose behavior, and the registry
  keys expire on their own. The `/ask-me` wording change is independent and separately revertible.
  **The `bridge/answer_routing.py` extraction (Task 9a) is explicitly *not* covered by that claim**:
  it restructures the reply-to steering ladder every typed Telegram reply already travels, and
  reverting the poll feature does not revert it. It therefore lands as its own commit, ahead of Task
  9b and touching only `bridge/answer_routing.py` and `bridge/telegram_bridge.py`, so it is
  independently revertible via `git revert <9a-sha>`.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 — **two taps are mandatory**: **Task 2** (`gate-poll-human-tap`, readback and
  attribution, before the inbound half exists) and **Task 10a** (`gate-poll-e2e`, the end-to-end
  assertion, after it does). Both are a single tap on a probe poll in the `Eng: Valor` group. They
  cannot be collapsed into one — nothing observes a vote at Task 2 time. An UNRESOLVED tap pauses
  the four-task inbound set (9, 10, 10a, 13b) and does not stall the outbound half.
- Review rounds: 2+ — a new inbound Telethon handler and a new outbox payload variant both touch
  load-bearing delivery paths.

The size is driven by breadth, not depth: nine touched subsystems (CLI, eligibility gate, output
handler, drafter, relay, response, bridge handler, reconciliation loop, catchup transcript) plus a
global skill and its skill-context file. The re-scope to group-only removed no subsystems — it
added the eligibility gate.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telethon >= 1.42 with poll TL types | `.venv/bin/python -c "from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities, UpdateMessagePoll, PollResults"` | Poll construction and vote observation |
| Authenticated bridge Telegram session | `test -f "$HOME/Desktop/Valor/telegram_session.session"` | Tasks 1-2 probe from a **temp copy** of this session, never the live file |
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Outbox, steering, and the poll registry |
| A machine-owned eng **group** in projects.json | `.venv/bin/python -c "import json,os; d=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json'))); assert any((p.get('telegram') or {}).get('groups') for p in d.get('projects',{}).values())"` | Tasks 1-2 probe a group. **DMs are out of scope and must not be probed** (spike-6 settled it) |

Run via `python scripts/check_prerequisites.py docs/plans/ask-me-telegram-polls.md`.

## Solution

### Key Elements

- **Vote-readback gate (Tasks 1-2)**: the send capability is settled (spike-6); the remaining
  gate-worthy unknown is whether a vote in a group poll is **readable back** by the sending user
  account. Task 1 answers it with no human (the probe account votes on its own probe poll and reads
  the result). Task 2 confirms a *different* human's tap is readable and attributable. Only the
  four-task inbound set (9, 10, 10a, 13b) depends on these.
- **End-to-end gate (Task 10a)**: the single assertion that the nine subsystems are wired together —
  a CLI-originated poll, a human tap, and the chosen option text arriving in the session's next turn.
  It sits after Task 10 because that is the first point at which any code observes a vote.
- **`bridge/poll_gating.py`**: the single eligibility predicate — group chat **AND**
  `session_type == "eng"` — read by the CLI at ask time and re-read by the relay at send time.
  Everything ineligible degrades to prose.
- **`tools/ask_poll.py` + `valor-ask-poll` CLI**: the single transport-aware entry point the agent
  calls. Owns degradation to text for DMs, teammate sessions, email, local, and system surfaces.
- **`send_poll` on the output handler + a `"poll"` outbox variant**: extends the proven
  outbox → relay seam rather than inventing a second outbound path.
- **`bridge/response.py::send_poll`**: raw MTProto `InputMediaPoll`, returning both the message id
  and the **server-assigned poll id**.
- **Poll registry** (`telegram:poll:{poll_id}`): the piece the issue's sketch was missing.
  Without it a vote cannot be routed, because `UpdateMessagePoll` has no peer or message id.
- **`bridge/poll_vote.py::translate_poll_vote(poll_id)`**: the single idempotent translation
  function, reached by both the `events.Raw` fast path and the reconciliation loop. It lives in its
  own module and imports the `answer_routing` seam, keeping the poll feature out of the
  poll-independent module Task 9a extracts.
- **`bridge/answer_routing.py`** (`resolve_answer_target` + `resume_completed_session`): the two
  seams factored out of the reply-to ladder that a vote can actually satisfy, so a vote reaches a
  `running`, `pending`, or `completed` session the same way a typed reply does — without pretending
  a vote has an `event`, a `message`, or a reply chain.
- **Transcript rendering of polls**: so the catchup judge sees the question instead of a blank line.
- **`/ask-me` relaxation + skill-context**: the one-at-a-time rule becomes a preference in the
  global body; the Telegram-specific mechanics live in `.claude/skill-context/ask-me.md`.

### Flow

Agent is blocked → runs `/ask-me` → detects headless Telegram surface → calls `valor-ask-poll` →
**eligibility gate: group chat AND eng session** (else prose, done) →
**poll appears in the group** (recommended option first, `Other: wait for followup message` last) →
human taps → **poll closes** → vote translated to a steering message → **agent resumes next turn**
with the choice in hand → (if `Other`) agent sends a narrowed plain-text followup → human replies
by reply-to → **same session continues**.

### Technical Approach

**Owner decisions are settled and are not reopened here:** group chats only via the existing user
account; eng sessions only (`session_type == "eng"`, never `teammate`); no mid-turn blocking
(turn-boundary rendering plus vote→steering translation only); the final option is always the
literal string `Other: wait for followup message`; one-question-at-a-time becomes a stated
preference; reaction-based answering is dropped and not revived. See **Scope** at the top.

> **Every `file:line` in this plan is approximate — locate by symbol.** All symbol *names* were
> re-verified against the **verification baseline** named in the Freshness Check, but offsets drift under refactors. Builders must find their edit
> site with `grep -n '<symbol>' <file>` and treat the cited line as a hint only. Offsets below were
> re-verified at revision time; the ones that had drifted are corrected in place.

- **Eligibility is one predicate, evaluated twice, and it fails closed.** `bridge/poll_gating.py`
  owns `poll_eligible(chat_id, session_id) -> PollEligibility(ok: bool, reason: str)`:

  1. `is_group_chat(chat_id)` — the negative-id discriminator, which **stays in
     `bridge/read_the_room.py` (`:126`)** and is merely promoted from the private `_is_group_chat`
     to a public name; `bridge/poll_gating.py` imports it. One definition, no copy, and the
     dependency arrow points from the feature module at the generic predicate rather than the
     reverse. A positive id (DM), a zero, an unparseable value, or `None` → ineligible,
     reason `not_a_group`.
  2. `session_type == SessionType.ENG` read off the `AgentSession` record found by
     `AgentSession.query.filter(session_id=session_id)`. **Exact match only.** A `teammate` record →
     `not_eng_session`. A missing record or a `null`/unknown `session_type` → `unknown_session_type`,
     which is **ineligible** — the field is `null=True` (`models/agent_session.py:156`), and a
     question rendered as prose to an eng session is a cosmetic loss while a poll rendered into a
     teammate chat is a scope violation. Fail closed.
  3. Any exception → ineligible, reason `eligibility_error`, logged at warning. Never raises.

  The predicate is read **twice on purpose**: at ask time in `tools/ask_poll.py` (so degradation
  happens once, at the single decision point, and the prose path is taken before a poll payload
  ever exists) and again at send time in the relay's poll branch (the relay is the last writer
  before the wire, and an outbox payload can sit across a session-type change). The relay's
  ineligible branch does **not** drop — it converts to the same plain-text payload the
  terminal-failure path builds, so the question still reaches the human.

- **Gate the unknown that is actually still unknown.** The *send* capability is settled by spike-6
  and must not be re-probed. What is not settled is the **inbound** half in a group:
  - **Task 1 (`gate-poll-vote-readback`, hard, no human required).** Can the sending user account
    read a vote back at all? The probe sends a poll into the machine-owned eng group, casts a vote
    **from the probe account itself** via `messages.SendVoteRequest`, then reads
    `messages.GetPollResultsRequest(peer, msg_id)` and asserts the chosen option is recoverable
    from `PollAnswerVoters` with the correlation-id option encoding intact. A FAIL here means the
    inbound half is impossible on this surface and the build **stops** — reconciliation is the
    primary mechanism, so there is no fallback behind it. Self-voting is a probe-only affordance;
    **production never calls `SendVoteRequest`**, which is what keeps "the sender never votes" true.
  - **Task 2 (`gate-poll-human-tap`, hard for the inbound tasks, UNRESOLVED permitted).** Is a
    *different* human's tap readable and attributable? Confirms `GetPollResultsRequest` after a real
    tap and whether `GetPollVotesRequest` names the voter.
  - **The `updateMessagePoll` push question is deliberately NOT gated, and here is why that is safe
    and how it avoids the production risk named in the scope decision.** Observing the push requires
    a client with **updates enabled** on the bridge's auth key; a second such client can consume
    updates the live bridge needs, which is a production hazard that outweighs the answer. Both
    probes therefore run on a **temp copy** of the session file with `receive_updates=False` and no
    writeback to the live `.session` — the same shape the settled spike-6 probes used. Correctness
    never depended on the push: `GetPollResultsRequest` reconciliation is primary and works with
    zero update delivery. The push question is answered **in production, after ship**, by a
    `poll_update_observed` log line inside the `events.Raw` handler itself (Task 10). If that signal
    never appears, the Raw handler is dead weight and is deleted in a follow-up — a scope reduction,
    never a correctness failure.
- **Reconciliation is primary, `events.Raw` is the fast path.** Spike-2 proved the update is not
  self-routing. Making `GetPollResultsRequest` reconciliation the guaranteed mechanism de-risks the
  un-gated push unknown above, and gives restart-survivability for free. Both paths call the same
  idempotent `translate_poll_vote(poll_id)`, so adding the fast path cannot introduce a second
  behavior.
- **Option selection in a group is deterministic, and first-voter-wins is a stated semantic.** The
  old DM rule ("any option with `voters >= 1` is unambiguously the human's choice") is dead: a group
  can produce several options each carrying `voters >= 1`, and `PollResults` gives no ordering, so
  "the first vote" is not derivable from the aggregate. The rule is therefore:
  1. Filter `results.results` to entries with `voters >= 1`.
  2. Exactly one → use it.
  3. More than one → log a warning naming the poll id and the tied options, then take the highest
     `voters`, breaking ties by **lowest decoded option index**. Deterministic and greppable.
  4. Zero → return **without claiming** (a spurious update must not consume the one-shot claim).

  Closing the poll on first translation makes this a **first-voter-wins** race in practice, and that
  is the intent: the poll exists to unblock one agent, not to take a vote of the room. The steer
  text names the voter when `GetPollVotesRequest` resolves them, so a human who disagrees can
  correct it with an ordinary reply-to message through the existing path.
- **Attribution is best-effort and never load-bearing.** `messages.GetPollVotesRequest` is
  user-account-only and returns the voting peer. Use it to fill `sender_name` in the steer.
  On any failure, fall back to the target session's
  `initial_telegram_message["sender_name"]`, then to the literal `"Telegram poll"`. A failure here
  never blocks translation.
- **Split the routing ladder at the seam that is actually shared; do not pretend it is one
  function.** The reply-to ladder is an inline block inside `handler(event)`
  (`bridge/telegram_bridge.py:1787-2012`), and most of it consumes objects a poll vote does not
  have: `_ack_steering_routed(client, event, message, ...)` (`:904`) branches on `message.media`
  and reacts on `message.id`; the completed branch calls `is_duplicate_message(event.chat_id,
  message.id)` (`:1890`), `fetch_reply_chain(client, event.chat_id, message.reply_to_msg_id)`
  (`:1918`), `react_if_worker_down(...)` (`:1987`), and `dispatch_telegram_session(...)` (`:1988`).
  A `(session_id, text, sender)` signature cannot carry any of that, and a builder handed one will
  take the cheap path and drop the completed branch for votes — re-opening Risk 3. So the
  extraction is **two functions in a new `bridge/answer_routing.py`**, not one:

  1. **`resolve_answer_target(session_id) -> AnswerTarget`** — a *pure state read*, no I/O beyond
     the `AgentSession.query.filter` calls. Returns
     `AnswerTarget(kind, session, matched_status, pending_age_s)` where `kind` is `LIVE`
     (running/active), `PENDING`, `LIVE_GUARD` (a completed record exists but a
     pending/running/active one appeared concurrently), `COMPLETED`, or `NONE`.

     **This is a restructure, not a verbatim lift, and the plan says so.** The source range
     `:1799-1866` interleaves the `query.filter` ladder with `await _ack_steering_routed(...)` +
     `return` at every branch, so the side effects must be pulled out to the caller. Its
     behavior-preservation checklist is four concrete items, not a hand-wave:
     - `matched_status` is carried because the LIVE log embeds `matching_session.status` and the
       LIVE_GUARD log embeds `live_guard.status`.
     - `pending_age_s` is carried because the PENDING log embeds `age=%.1f` computed by
       `_pending_session_age_seconds(pending_session.created_at, time.time())` (`:1834`).
     - `COMPLETED` returns the record chosen by the **existing most-recent-`created_at` sort**
       (`_completed_created_at`, `:1898`), **not** `completed_sessions[0]` — the wrong record
       silently degrades `_build_completed_resume_text`'s `context_summary`.
     - `_steering_session_enqueued = True` (`:2006`) stays **caller-side**;
       `resume_completed_session` returns `None` and does not own that flag.
  2. **`resume_completed_session(*, completed, text, sender_name, telegram_chat_id,
     telegram_message_id, chat_title=None, sender_id=None, project=None, project_key=None,
     working_dir=None, telegram_message_key=None, reply_chain_context=None,
     extra_context_overrides=None) -> None`** — the completed-session re-enqueue lifted from
     `~:2051-2124`: `_build_completed_resume_text(completed, text, reply_chain_context=...)` then
     `dispatch_telegram_session(...)`. Every `project`/`project_key`/`working_dir`/`session_type`
     argument left `None` falls back to the corresponding field on the `completed` **AgentSession
     record itself** (`project_key`, `working_dir`, `project_config`, `session_type` are all model
     fields — verified in `models/agent_session.py:154-390`), which is exactly why a caller with no
     `project` dict in hand can still use it.

  **Everything caller-specific stays in the caller.** The message handler keeps its own
  `_ack_steering_routed` calls, its `is_duplicate_message` short-circuit, its reply-chain hydration,
  and its `react_if_worker_down` — it just calls `resolve_answer_target` instead of inlining the
  three `query.filter` ladders, and `resume_completed_session` instead of inlining the dispatch. That
  is what makes "behavior must not change" a checkable claim rather than a wish.

- **What a vote does on each branch, stated explicitly.** `translate_poll_vote` calls
  `resolve_answer_target(session_id)` and then:
  - `LIVE` / `PENDING` / `LIVE_GUARD` → `push_steering_message(session_id, steer_text, sender_name,
    room_id=room_id_for_session(target.session))`. **The `room_id` argument is mandatory, not
    optional.** `agent/steering.py::push_steering_message` selects the Room key
    (`steering:room:{room_id}`) only when the caller supplies `room_id`; with none it silently
    writes the legacy `steering:{session_id}` key. The writer never looks the session up itself —
    `session_id` is unindexed and a lookup there would cost ~2.4s on the inbound fast path — so
    derivation is the caller's job, exactly as every reply-to-ladder caller now does
    (`telegram_bridge.py:1927`, `:1956`, `:1996`). The translator already holds the session record
    from `resolve_answer_target`, so `room_id_for_session(target.session)` is a pure attribute read
    with no extra query. `AnswerTarget.session` is `None` only on the `NONE` kind, which never
    steers; a session with no `project_key` yields `None` and correctly takes the legacy leg.
    Omitting this puts the vote steer on a leg no one drains alongside its peers — a silent
    regression, not a crash. It does **not** call `_ack_steering_routed`: there is no inbound message to react to,
    and closing the poll is already the visible acknowledgment. It also deliberately skips
    `_ack_steering_routed`'s abort-keyword detection — a poll option is never an abort keyword.
  - `COMPLETED` → `resume_completed_session(completed=target.session, text=steer_text,
    sender_name=..., telegram_chat_id=<registry chat_id>, telegram_message_id=<the poll's own
    msg_id from the registry row>, reply_chain_context=None)`. A vote has no reply chain, so the
    summary-only preamble `_build_completed_resume_text` already produces is the correct output —
    no hydration is skipped silently, it genuinely does not exist. Passing the poll's `msg_id` as
    `telegram_message_id` is safe and load-bearing: `dispatch_telegram_session` claims
    `(chat_id, telegram_message_id)` via `bridge/dedup.py:171 claim_message`
    (keyspace `bridge:msgclaim:`), which is inbound-only — outbound sends never claim it — so the
    poll's message id is an unused, unique, stable dedup key for exactly this re-enqueue.
  - `NONE` → log at info and return. Never create a session.

  `is_duplicate_message` is **not** called on the vote path; idempotency comes from
  `claim_poll_answer(poll_id)` (`SET NX`) upstream plus `claim_message` inside
  `dispatch_telegram_session`. Two independent claims, both atomic.

- **The one-shot claim must not become a permanent swallow (cycle-3 concern, adopted).** The claim
  is taken *before* closing the poll and *before* steering. If the bridge dies, `EditMessageRequest`
  raises, or the steering write throws after the claim, the claim survives its TTL and **both**
  recovery mechanisms are defeated: `iter_unanswered_polls()` skips a claimed row, so the
  reconciliation loop never retries; and Risk 7's `poll_expired_unanswered` signal — if defined as
  "no claim" — never fires. That is exactly the invisible permanently-blocked agent this feature
  exists to prevent. Two changes, both required:
  1. Everything after `claim_poll_answer(poll_id)` runs inside `try/except Exception`, and the
     handler **deletes the claim key** before logging, so the next reconciliation tick retries.
  2. Completion is recorded **separately from the claim**: write `steered_at` onto the
     `telegram:poll:{poll_id}` row only after `push_steering_message` / `resume_completed_session`
     returns. `iter_unanswered_polls()` treats "claim present, no `steered_at`, claim older than one
     reconcile interval" as **still unanswered**, and `poll_expired_unanswered` keys on **missing
     `steered_at`**, never on a missing claim — otherwise the operator signal is blind to precisely
     this state.

  `sender_name` for a vote is the `GetPollVotesRequest`-resolved voter when available, else the
  target session's `initial_telegram_message["sender_name"]`, else the literal `"Telegram poll"`.
- **Steering text carries the question, not just the option.** The steer reads as
  `Poll answer to your question "<question>": <chosen option>` so the resumed turn has the
  binding without needing to re-derive it. For the escape hatch the steer explicitly instructs a
  narrowed plain-text followup.
- **Degrade once, at the CLI.** `_resolve_transport()` precedence is already the repo's single
  answer to "which surface am I on"; `poll_eligible(...)` is the single answer to "does this surface
  take a poll". Non-telegram transport, non-group chat, or non-eng session → numbered-list text
  through the normal `send_message` path, with the ineligibility reason logged.
  `EmailOutputHandler` gets no `send_poll` and the capability probe (`hasattr`, mirroring
  `adapter.py:61`) keeps it valid.
- **The plain-text fallback is an explicit re-enqueue, not the dead-letter queue.** Dead-lettering
  is durability, not delivery: `_dead_letter_message` (`bridge/telegram_relay.py:803`) calls
  `persist_failed_delivery` into the `DeadLetter` model, and its only consumer,
  `replay_dead_letters`, runs from exactly one site — the bridge connect sequence
  (`bridge/telegram_bridge.py:2842`). A question routed only there reaches the human on the next
  bridge restart, which for a blocked agent is hours away or never. So on terminal failure of a
  `"poll"` payload (`_relay_attempts >= MAX_RELAY_RETRIES`, `telegram_relay.py:1029-1034`) the poll
  dispatch branch **rpushes a plain text payload onto the same `telegram:outbox:{session_id}` key**
  — `{"type": None, "chat_id": ..., "text": <question + numbered options>, "reply_to": ...,
  "session_id": ...}` with `_relay_attempts` reset so the text send gets its own retry budget. This
  cannot loop: `process_outbox` is `while processed < RELAY_BATCH_SIZE` over `r.lpop` of the same
  key (`:895-897`), so a same-cycle rpush is picked up on a later cycle, and the re-enqueued payload
  is plain text which never re-enters the poll branch. Keeping `"poll"` out of the ephemeral-discard
  tuple (`:827`) and supplying the question as `text` at the `if chat_id and text` gate (`:836`)
  both stay required — but as the durability backstop behind the re-enqueue, not as the
  user-visible path.
- **Teach the roles to reach `/ask-me`, or the feature never fires on the common path.** A `grep`
  over `.claude/commands/roles/` (the real location of the role primes — `role_driver.py:65-72`
  maps `pm`/`dev`/`teammate` to `prime-pm-role.md`, `prime-dev-role.md`, `prime-teammate-role.md`;
  there is no `.claude/skills-global/roles/`) and `config/personas/` returns **zero** references to
  `ask-me`. Without wiring, the only trigger is an agent that happens to invoke the skill, while the
  ordinary path — a bare `AskUserQuestion` or prose at turn end firing the `needs_human` edge —
  delivers text exactly as today. Task 12 therefore adds one generic line to each of the three role
  primes directing a blocked headless session to invoke `/ask-me` rather than posing a judgment call
  in prose. The line stays surface-agnostic (no poll, no Telegram, no CLI) — the poll rendering is
  `/ask-me`'s business via `.claude/skill-context/ask-me.md`.

  **The line must be a conditional phrased against the existing pause threshold, not a bare
  directive** (cycle-3 concern, adopted). These primes govern every eng and teammate session on
  every machine and surface, so a loose "when blocked, ask" would introduce a second, looser
  definition of "blocked" alongside the auto-continue doctrine's "legitimate open question" bar and
  could raise pause frequency for local, email, and non-blocked sessions. Required phrasing shape:
  *"when you have a legitimate open question that only the human can answer (the same bar the
  auto-continue nudge loop uses), invoke `/ask-me` rather than posing it in prose."* Two Success
  Criteria enforce it: the line contains the open-question precondition, and ineligible surfaces
  still degrade to text through the CLI. The `needs_human` turn-end path itself
  is **not** taught to auto-render polls; that is a named No-Go, and the Success Criteria are worded
  to match the build rather than to the broader ambition.
- **Drafter: validate, don't compose — and validation has two homes, decided explicitly.**
  `draft_message` is text-only by contract (`bridge/message_drafter.py:1016`, `MessageDraft` at
  `:193`). `_validate_for_medium(text: str, medium: str)` (`:561`) **never sees the options**, so
  it physically cannot carry option-count or option-length checks. The signature is **not**
  changed (it would ripple to `:1100`, `:1148`, and `tests/unit/test_medium_validators.py` for no
  gain). Split, once and for all:
  - **`_validate_for_medium`, `medium="telegram_poll"`** — question text only: `<= 300` chars,
    non-empty after strip. Nothing else.
  - **`tools/ask_poll.py`** — the sole owner of option validation: 2..10 options, each `<= 100`
    chars, non-empty, de-duplicated, mandatory final option appended. This is where the plan's
    **Failure Path Test Strategy** already requires these checks, so it is the existing home.

  **The callable seam is a new public wrapper, not `draft_message`.** `_validate_for_medium` is
  private and reachable only from inside `draft_message` (`:1100`, `:1148`), and `draft_message`
  runs `_compose_structured_draft` at `:1145` *before* validating at `:1148` — so routing a poll
  question through `draft_message` would return it with the emoji prefix, stage line and link
  footer attached. "Validate via the drafter" and "bypass composition" are only compatible through
  a new entry point. Add one:

  ```python
  # bridge/message_drafter.py — public, next to draft_message
  def validate_poll_question(question: str) -> list[Violation]:
      """Validate a poll question against the telegram_poll medium. No composition."""
      return _validate_for_medium(question, "telegram_poll")
  ```

  `TelegramRelayOutputHandler.send_poll` calls `validate_poll_question(...)` directly. The private
  `_validate_for_medium(text, medium)` signature is unchanged, so nothing ripples to `:1100`,
  `:1148`, or `tests/unit/test_medium_validators.py`. `_compose_structured_draft` (`:952`) is never
  reached on the poll path.

- **Deliberate departure from the #1955 precedent, named as such.** #1955 is cited above for the
  precedent that a new outbound shape needs matching *drafter awareness*, and the recorded doctrine
  is that the drafter review step is the load-bearing comms layer. A poll question departs from the
  composition half of that on purpose: it is a structured artifact with fixed options rendered by
  Telegram's own UI, and the drafter's composition (emoji prefix, stage line, link footer) would
  corrupt it — a stage line inside a poll question is not a message with a header, it is a broken
  question. The drafter's role for `telegram_poll` is therefore **validation only**. The comms layer
  is not bypassed overall: the escape-hatch followup message, which is ordinary prose, still goes
  through the full `draft_message` path. The #1955 citation supports "add a medium", not "compose".
- **No Popoto model, no migration.** Spike-5: `telegram:poll:{poll_id}` and
  `telegram:poll:answered:{poll_id}` are plain Redis string keys with TTL, following
  `bridge/job_router.py:85-96`. This keeps the change outside index-drift and `rebuild_indexes()`
  and avoids a `scripts/update/migrations.py` entry entirely.
- **All tunables are named env-overridable constants** with a grain-of-salt comment marking them
  provisional: registry TTL, answered-claim TTL, reconciliation fast interval, reconciliation slow
  interval, and the fast→slow crossover age.
- **Machine ownership.** The registry is written only for chats this machine already owns (the
  relay only ever sends into machine-owned chats), so no new ownership surface is created. The
  reconciliation loop must iterate only registry entries, never all chats.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `bridge/response.py::set_reaction` (`:320`) is the local idiom: log at debug and return
  `False`, never raise. `send_poll` follows it but must **return `None` distinguishably** so the
  relay's retry/dead-letter path (`telegram_relay.py:1002-1019`) still engages. Test asserts a
  raising Telethon client produces a logged warning and a relay retry, not a silent drop.
- [ ] The `events.Raw` handler must never raise into Telethon's update loop. Test asserts an
  exception inside `translate_poll_vote` is caught, logged at warning, and does not propagate.
- [ ] The reconciliation loop must survive a `FloodWaitError` from `GetPollResultsRequest` the way
  the relay does (`telegram_relay.py:918-945`). Test asserts the loop backs off and continues
  rather than dying.
- [ ] The reconciliation loop emits `poll_expired_unanswered` exactly once per expired-unanswered
  poll and does not re-emit on subsequent ticks. Tested.
- [ ] No `except Exception: pass` may be introduced. Every handler logs.

### Empty/Invalid Input Handling

- [ ] `valor-ask-poll` with an empty question, zero options, one option, or >10 options exits
  non-zero with a message on stderr. Tested per case.
- [ ] Question >300 chars or an option >100 chars is rejected before any network call.
- [ ] The mandatory final option is appended if the caller omitted it, and de-duplicated if the
  caller already supplied it. Tested both ways.
- [ ] `translate_poll_vote` with an unknown `poll_id` (a poll we did not send, or an expired
  registry entry) returns quietly without steering anything.
- [ ] `translate_poll_vote` where `GetPollResultsRequest` reports `total_voters == 0` returns
  without claiming — a spurious update must not consume the one-shot claim.
- [ ] `translate_poll_vote` where the registry entry's session no longer exists logs and returns;
  it must not create a session.

### Eligibility Gate (group-only + eng-only)

- [ ] `poll_eligible` returns ineligible with reason `not_a_group` for a positive `chat_id` (DM),
  `chat_id == 0`, an unparseable string, and `None`. Tested per case.
- [ ] `poll_eligible` returns ineligible `not_eng_session` for `session_type == "teammate"`, and
  `unknown_session_type` for a missing `AgentSession` record and for a `null` `session_type`.
  Fail-closed in every case.
- [ ] `poll_eligible` returns ineligible `eligibility_error` (never raises) when the
  `AgentSession.query.filter` call throws.
- [ ] `valor-ask-poll` in an ineligible chat/session sends the question as numbered prose through
  the ordinary `send_message` path and **queues no poll payload**. Tested for the DM case and the
  teammate case separately.
- [ ] The relay's poll branch re-checks eligibility and, when it fails, delivers the plain-text
  payload rather than dropping the message. Tested by queueing a poll payload whose session has
  since flipped to `teammate`.
- [ ] `is_group_chat` has exactly one definition in the repo — it stays in
  `bridge/read_the_room.py` (renamed from `_is_group_chat`), `bridge/poll_gating.py` imports it,
  and existing RTR behavior is unchanged (`tests/unit/` RTR suite is the regression net).
- [ ] **The relay branch never calls `poll_eligible` on the loop thread.** `_send_queued_poll` is
  driven with a `poll_eligible` stub that records `threading.current_thread()`; the assertion is that
  the recorded thread is **not** the one running the event loop. This is a correctness test about
  where the unindexed `AgentSession.query.filter` scan executes, not a timing test — do not write it
  as a duration assertion.

### Correlation Key (Race 6 producer/consumer contract)

- [ ] `build_telegram_poll_outbox_payload` stamps a `poll_id_hint` on every payload it emits, and two
  calls produce different hints.
- [ ] `_send_queued_poll` passes the payload's `poll_id_hint` to **both** `register_pending_poll` and
  `send_poll(correlation_id=...)` — asserted on the recorded call args, not by inspection.
- [ ] `encode_option(index, poll_id_hint)` round-trips through `decode_option` and the encoded bytes
  are `<= 100` bytes for a 32-char hex hint at every option index the CLI permits (2..10).
- [ ] A payload arriving at `_send_queued_poll` **without** `poll_id_hint` logs at error and delivers
  the plain-text fallback; it does not send a poll.
- [ ] `send_poll(correlation_id=None)` emits `poll_sent_without_correlation_id` at warning.
- [ ] Orphan adoption matches a provisional row to a discovered `MessageMediaPoll` by decoding
  `poll.answers[0].option` back to the same `poll_id_hint` — and adopts **nothing**, with a warning,
  when two candidates decode to the same hint.

### Vote Selection Under Multiple Voters (group)

- [ ] Exactly one option with `voters >= 1` → that option is chosen.
- [ ] Two options each with `voters >= 1` → the higher-`voters` option is chosen and a warning
  naming the poll id and the tied options is emitted.
- [ ] Two options with **equal** `voters` → the lowest decoded option index wins, deterministically,
  on repeated runs.
- [ ] `GetPollVotesRequest` raising does not block translation; `sender_name` falls back through the
  session's `initial_telegram_message["sender_name"]` to `"Telegram poll"`.

### Claim Durability (the claim must not permanently swallow a question)

- [ ] An exception raised after `claim_poll_answer` but **before** the steer (from the poll close,
  from `push_steering_message`, or from `resume_completed_session`) causes the claim key to be
  **deleted** and the next reconciliation tick to retry successfully.
- [ ] An exception raised from `mark_poll_steered` **after** a successful steer neither releases the
  claim nor re-dispatches: `dispatched` is present, the claim survives, and the next reconciliation
  tick re-attempts the `steered_at` write only. Asserted separately for the `LIVE` branch
  (`push_steering_message` called exactly once) and the `COMPLETED` branch
  (`dispatch_telegram_session` called exactly once — a second call would double-enqueue the session
  from one vote).
- [ ] `mark_poll_dispatched` is called before any post-steer bookkeeping that can throw; a test that
  makes `mark_poll_steered` raise still finds `dispatched` on the row.
- [ ] `steered_at` is written only after the steer/re-enqueue returns, and
  `iter_unanswered_polls()` re-yields a row with a claim but no `steered_at` once the claim is older
  than one reconcile interval.
- [ ] `poll_expired_unanswered` fires for a row with a claim but no `steered_at` — i.e. it keys on
  missing `steered_at`, not on a missing claim.

### Error State Rendering

- [ ] Poll send failure surfaces to the human as the plain-text fallback question rather than
  silence: on a terminal relay failure for a `"poll"` payload, the question is **rpushed back onto
  `telegram:outbox:{session_id}` as a plain-text payload with `_relay_attempts` reset**, and is
  delivered on a later relay cycle. The test asserts the re-enqueue, not the dead-letter row — a
  `DeadLetter` alone only reaches the human on the next bridge restart (`replay_dead_letters` is
  invoked from one site, `bridge/telegram_bridge.py:2842`). This is the user-visible error path.
- [ ] `_dead_letter_message` (`telegram_relay.py:803`) must **not** treat `"poll"` as ephemeral
  (`:827`) — a dropped question is a stuck agent. Test asserts a poll payload dead-letters loudly
  instead of being discarded, **and separately** that it survives the `if chat_id and text` gate
  (`:836`), which a text-less payload would otherwise fall straight through. This is the durability
  backstop assertion, tested independently of the text re-enqueue above.

## Test Impact

- [ ] `tests/unit/test_bridge_relay.py::test_known_message_types` (`:47`) — UPDATE: assert `"poll"`
  is a known type.
- [ ] `tests/unit/test_bridge_relay.py::test_unknown_message_type_discarded` (`:568`) — UPDATE:
  confirm it still uses a genuinely unknown type now that `"poll"` is known.
- [ ] `tests/unit/test_bridge_relay.py` class `TestProcessOutbox` (`:467`) — UPDATE: add poll
  dispatch cases alongside `test_reaction_failure_uses_bounded_retry` (`:626`) and
  `test_custom_emoji_failure_uses_bounded_retry` (`:656`).
- [ ] `tests/unit/test_bridge_relay.py::test_discards_reaction_without_persisting` (`:406`) —
  UPDATE: add the inverse assertion that a poll payload is **not** discarded on dead-letter.
- [ ] `tests/unit/test_telegram_relay_chat_log.py` — UPDATE: assert the poll branch appends a chat
  log entry with the question text.
- [ ] `tests/unit/test_relay_job_record.py` — UPDATE: assert `_bind_outbound_message_to_job` runs
  for poll sends too, so reply-to on a poll message still resolves a job.
- [ ] `tests/unit/test_send_message.py` — UPDATE: assert the poll CLI shares
  `_resolve_transport()` precedence and does not regress the text path.
- [ ] `tests/unit/test_steering_mechanism.py::test_steer_session_from_queue` (`:71`) — UPDATE only
  if the `bridge/answer_routing.py` extraction changes the call shape; otherwise no change.
- [ ] `tests/integration/test_steering.py::test_steering_push_only_after_session_match` (`:434`),
  `test_pending_session_within_window_receives_steering` (`:514`),
  `test_reply_to_completed_session_reenqueues_with_context` — UPDATE: these are the regression net
  for the `resolve_answer_target` / `resume_completed_session` extraction (the message handler's
  behavior must be unchanged), and are extended to cover a vote-originated steer reaching `pending`
  and `completed` sessions through the same two functions.
- [ ] `tests/unit/test_medium_validators.py` — UPDATE: add `telegram_poll` cases via the new public
  `validate_poll_question` wrapper; the private `_validate_for_medium` signature is unchanged so no
  existing case moves.
- [ ] `tests/unit/test_react_with_emoji.py::test_react_queues_reaction_payload` (`:61`) — no change;
  cited as the payload-shape assertion pattern the new poll payload test copies.
- [ ] The health-payload test that covers whichever surface reads
  `telegram:poll:reconcile:heartbeat` (the dashboard health payload or the bridge health check,
  Task 10) — UPDATE: assert the degraded signal appears when the key is absent and not when it is
  present. Locate the existing suite by `grep -rln 'dashboard.json\|health' tests/unit/`; adding a
  new field to a shipped health payload without a test is how that surface silently drifts.

- [ ] The `bridge/read_the_room.py` test suite (`grep -rln 'read_the_room' tests/`) — UPDATE only if
  renaming `_is_group_chat` to the public `bridge/read_the_room.py::is_group_chat` changes a name a
  test patches; RTR behavior itself must be unchanged and those tests are the regression net.

New test files (greenfield, no prior coverage — grep of `tests/` for `poll` returns only
`test_poll_interval_is_100ms`, an unrelated relay constant):
`tests/unit/test_ask_poll_cli.py`, `tests/unit/test_poll_payload.py`,
`tests/unit/test_poll_vote_translation.py`, `tests/unit/test_poll_registry.py`,
`tests/unit/test_poll_gating.py`, `tests/unit/test_agent_catchup_poll_transcript.py`.

Additional coverage required by this revision: Race 6 (provisional row survives a simulated restart
and is adopted) in `tests/unit/test_poll_registry.py`, and the `poll_expired_unanswered` warning
(emitted once, not re-emitted) in `tests/unit/test_poll_vote_translation.py`.

Additional coverage required by **revision cycle 2**, all in
`tests/unit/test_poll_vote_translation.py` unless noted:

- `translate_poll_vote` against a `COMPLETED` target calls `resume_completed_session` (not
  `push_steering_message`) and passes the poll's own `msg_id` as `telegram_message_id`.
- `translate_poll_vote` against each of `LIVE`, `PENDING`, `LIVE_GUARD`, `NONE` takes its stated
  branch, and the `NONE` case creates no session.
- Terminal poll relay failure rpushes a **plain-text** payload back onto
  `telegram:outbox:{session_id}` with `_relay_attempts` reset, and that payload does not re-enter
  the poll branch (`tests/unit/test_bridge_relay.py`).
- Race 6 adoption with **two** candidate polls carrying different correlation ids adopts the right
  one; with two candidates carrying the *same* id it adopts nothing and warns
  (`tests/unit/test_poll_registry.py`).
- `encode_option` / `decode_option` round-trip, including an option index recovered from a poll
  read back off the wire (`tests/unit/test_poll_payload.py`).

Additional coverage required by **revision cycle 5** (the landed Room-key flip), in
`tests/unit/test_poll_vote_translation.py`:

- `translate_poll_vote` against a `LIVE` target calls `push_steering_message` with
  `room_id == room_id_for_session(<that session>)` — asserted on the **keyword argument**, not just
  on the steer text, since omitting it is a silent legacy-key downgrade rather than an error.
  Repeated for `PENDING` and `LIVE_GUARD`.
- A target session with no `project_key` yields `room_id=None` and the steer still lands (legacy
  leg), rather than raising.

Additional coverage required by **revision cycle 4** (the group-only + eng-only re-scope): every
bullet under **Eligibility Gate**, **Vote Selection Under Multiple Voters**, and **Claim
Durability** in the Failure Path Test Strategy above, in `tests/unit/test_poll_gating.py`,
`tests/unit/test_ask_poll_cli.py`, `tests/unit/test_bridge_relay.py`, and
`tests/unit/test_poll_vote_translation.py` respectively.

Additional coverage required by **revision cycle 5**: every bullet under **Correlation Key** (in
`tests/unit/test_poll_payload.py` for the producer half and `tests/unit/test_bridge_relay.py` for the
threading half), the two new **Claim Durability** `dispatched`-marker bullets (in
`tests/unit/test_poll_vote_translation.py`), and the thread-offload assertion in the **Eligibility
Gate** block (in `tests/unit/test_bridge_relay.py`).

Additional coverage required by **revision cycle 6**, in `tests/unit/test_bridge_relay.py` (Task 13a):

- `_send_queued_poll` calls `register_pending_poll` **before** it calls `poll_eligible` and before
  any other await — asserted on call order with a recording stub, not on timing.
- Each decline branch (missing `poll_id_hint`, ineligible session, terminal relay failure) deletes
  the provisional row on its way to the plain-text path, so a declined send leaves no orphan
  adoption candidate behind.

## Rabbit Holes

- **Making the poll a blocking primitive.** Owner-settled: it is not one. Do not attempt to hold a
  turn open, poll for an answer inside a turn, or add a mid-turn input path. Turn boundaries only.
- **Teaching the message drafter to emit structured content.** `MessageDraft` is text-only by
  design. A `poll: PollSpec | None` field would ripple through every drafter consumer for no gain.
  Validate-only medium, nothing more.
- **Quiz polls, `correct_answers`, `solution`, multiple-choice.** `InputMediaPoll` supports them.
  None of them serve an `/ask-me` question. Single-choice, non-quiz, always.
- **Free-text capture inside the poll.** Telegram polls have fixed options. The issue already
  dropped this; the escape hatch is the followup message.
- **Re-probing the capability matrix.** spike-6 settled it with verbatim MTProto results. Do not
  send test polls into any chat to re-confirm what a DM or a bot can do, and do not re-open the bot
  path — it was rejected on identity, which no probe can change.
- **Building a bot identity, a `getUpdates`/webhook transport, or inline keyboard buttons.**
  Technically superior for DMs and firmly out of scope: a bot cannot post into a user-to-user chat,
  so it breaks the thread scoping sessions depend on.
- **Reaction-based answering.** Evaluated and dropped by owner decision. Telethon exposes no
  high-level reaction event, the bridge only ever sends reactions and never listens, and confirming
  push delivery would require a second client on the bridge's auth key.
- **Turning the group poll into a consensus mechanism.** Several members can vote; the poll closes
  on the first translation and the agent acts on one answer. Do not add quorum, weighting, vote
  windows, or an "authorized voter" allowlist. The room is already trusted to steer by typing.
- **Per-voter identification as a correctness mechanism.** `GetPollVotesRequest` is used
  **best-effort for attribution only** (naming the voter in the steer text). Do not make
  translation depend on it.
- **A general "structured outbound message" abstraction.** One concrete payload variant, following
  the `reaction` / `custom_emoji_message` precedent. Do not build a framework for the second one
  before it exists.
- **Reworking handled-detection.** Spike-4 showed `_has_valor_reply_after` already behaves
  correctly. Only transcript *rendering* changes.
- **Backfilling poll support into `bridge/catchup.py`'s inbound scan.** Humans do not send us
  polls, and a vote is not a message. The reconciliation loop is the catchup story for polls.

## Risks

### ~~Risk 1: a poll into a real user DM is rejected by MTProto~~ — **FIRED. Now a constraint.**
**This is no longer a risk.** It happened, exactly as written, on 2026-08-10: the Task 1 gate
returned `MediaInvalidError('Media invalid (caused by SendMediaRequest)')` for a poll from the
bridge's user account into a real user DM, while the identical construction into a group succeeded
(spike-6). The gate did its job — the build stopped with zero production code written.

**The settled constraint that replaces it:** *a user account cannot send a poll into a 1:1 DM.*
It is a fact of MTProto, not a condition to mitigate, monitor, or retry. It is enforced in code by
the `not_a_group` branch of `poll_eligible` and by the prose fallback, and it is the reason the
feature targets group chats only. Do not re-probe it and do not write retry logic against it.

### Risk 1 (new): a poll is rendered into a chat or session type that should not receive one
**Impact:** A `teammate` session or a DM gets a poll — a scope violation, and in the DM case a hard
MTProto rejection that consumes retries and delays a blocked agent's question.
**Mitigation:** One predicate, `poll_eligible(chat_id, session_id)`, evaluated at two points (CLI at
ask time, relay at send time) and **failing closed** on every ambiguity — a missing session record,
a `null` `session_type`, an unparseable chat id, or any exception all resolve to prose. The relay's
ineligible branch converts to plain text rather than dropping, so the worst case is a prose question
rather than a lost one. Covered by the **Eligibility Gate** test block.

### Risk 2: a vote cast in a group cannot be read back by the sending user account
**Impact:** The inbound half is impossible. Reconciliation is the primary mechanism and there is
nothing behind it, so every question would go unanswered.
**Mitigation:** This is the **only remaining gate-worthy unknown** and it is Task 1
(`gate-poll-vote-readback`) — a self-vote probe that needs no human, run on a temp copy of the
session with updates disabled. A FAIL stops the build. Task 2 then confirms a *different* human's
tap is readable. The separate `updateMessagePoll` push question is deliberately **not** gated: it
would require a second client with updates enabled on the bridge's auth key (a production hazard),
correctness never depended on it, and it is answered after ship by the `poll_update_observed` log
line in the Raw handler — a negative there deletes the handler as dead weight and changes nothing
else.

### Risk 2b: the wrong group member answers the agent's question
**Impact:** In a group, anyone can tap. The first vote wins and the agent proceeds on it.
**Mitigation:** Accepted, deliberately, and bounded. The polls only go into machine-owned eng group
chats, whose members can *already* steer a session by typing — a tap grants no authority a message
does not. The steer text names the voter when `GetPollVotesRequest` resolves them, so a disagreement
is visible and correctable by an ordinary reply-to message on the existing path. Quorum, weighting,
and voter allowlists are named Rabbit Holes.

### Risk 3: an answered poll never reaches its session because the session finished
**Impact:** The human taps, sees the poll close, and nothing happens. Worst-case UX: the affordance
looks broken and trust in it is gone after one occurrence.
**Mitigation:** the vote translator branches on `resolve_answer_target(session_id)` rather than
calling `push_steering_message` blindly, and the `COMPLETED` branch calls
`resume_completed_session(...)` — the same dispatch block a typed reply uses, factored out of
`bridge/telegram_bridge.py` ~`:2051-2124`. Explicitly covered by an integration test against a
`completed` session.

### Risk 4: duplicate or repeated translation of one vote
**Impact:** The agent receives the same answer several times, or a retracted-and-changed vote
produces two contradictory steers.
**Mitigation:** `SET telegram:poll:answered:{poll_id} NX` one-shot claim shared by both paths, plus
closing the poll on first translation so a re-vote is impossible at the source.

### Risk 5: the poll question is dropped on relay failure and the agent stalls forever
**Impact:** A blocked agent with no visible question — the exact failure the feature exists to
prevent, made worse because the human sees nothing at all.
**Mitigation, in delivery order:**
1. **The user-visible path is an explicit re-enqueue, not the dead-letter queue.** On terminal
   failure (`_relay_attempts >= MAX_RELAY_RETRIES`, `telegram_relay.py:1029-1034`) the poll branch
   rpushes a plain-text payload (`type: None`, `text` = question + numbered options,
   `_relay_attempts` reset) back onto `telegram:outbox:{session_id}`. `process_outbox` is
   `while processed < RELAY_BATCH_SIZE` over `r.lpop` (`:895-897`), so the re-enqueue is picked up
   on a later cycle and cannot loop (the new payload is plain text and never re-enters the poll
   branch).
2. **Dead-lettering is the durability backstop behind it, and is explicitly NOT prompt delivery.**
   `_dead_letter_message` (`:803`) persists a `DeadLetter`; its only consumer,
   `replay_dead_letters`, runs from one site in the bridge connect sequence
   (`bridge/telegram_bridge.py:2842`) — i.e. on the next bridge restart. Two guards must still both
   be handled or even that backstop is lost: `"poll"` stays out of the ephemeral-discard tuple
   (`:827`), **and** the persistence branch's `if chat_id and text` gate (`:836`) must receive the
   question as `text`, since a poll payload carries no `text` key and would otherwise fall through
   both branches into silence.

Tested as an error-rendering path: the assertion is that the *text re-enqueue* happens, with the
dead-letter persistence asserted separately as the backstop.

### Risk 6: registry growth or reconciliation flood
**Impact:** Unbounded `telegram:poll:*` keys, or `GetPollResultsRequest` calls tripping FloodWait.
**Mitigation:** TTL on every registry key; the reconciliation loop iterates only registry entries
(never all chats); adaptive interval (fast for the first couple of minutes after send, slow
thereafter); FloodWait backoff mirroring `telegram_relay.py:918-945`. All intervals are named
env-overridable constants.

### Risk 7: the inbound half fails systemically and presents only as silently blocked agents
**Impact:** A tap produces no Telegram message, so there is nothing in the chat to notice. If the
Raw handler is never registered, the reconciliation loop dies, or `GetPollResultsRequest` returns
stale results, every question simply goes unanswered and every asking agent stays blocked. Today
nothing in the plan reads registry state at expiry time, so this failure is invisible.
**Mitigation:** The reconciliation loop's own `iter_unanswered_polls()` scan is the hook point
(Task 10). When it observes a registry row at or past `POLL_EXPIRY_WARN_AGE_S` with **no
`steered_at`** — deliberately keyed on the completion marker, not on a missing
`telegram:poll:answered:{poll_id}` claim, or the signal would be blind to the Risk 9 swallow — it
emits a single
`logger.warning("poll_expired_unanswered ...")` carrying poll id, chat id, session id, and age —
one warning per poll, marked on the row so it is not re-emitted. The loop additionally logs a
warning on consecutive `GetPollResultsRequest` failures. `poll_expired_unanswered` is the named,
greppable signal an operator (or Sentry) watches; the feature doc names it explicitly.

**Second, external mitigation — the loop's own liveness.** `poll_expired_unanswered` is emitted from
*inside* `iter_unanswered_polls()`, so it is blind to the one failure mode Risk 7 names first: the
loop dying. Task 10 therefore writes `telegram:poll:reconcile:heartbeat`
(`SET EX POLL_RECONCILE_HEARTBEAT_TTL_S`, default 2x the slow interval) at the top of every tick, and
one **existing** external read site — the dashboard health payload or the bridge health check —
reports its absence as a degraded signal. A detector that lives inside the thing it detects is not a
detector.

### Risk 8: `/ask-me` relaxation produces questionnaire-mode spam
**Impact:** Removing the hard one-at-a-time prohibition invites the agent to fire five polls at
once, which is exactly the anti-pattern the skill was written against.
**Mitigation:** The rule becomes a *stated preference* with the "Questionnaire mode" anti-pattern
retained and reworded, and the skill's step-6 adapt-as-you-go guidance kept intact. The relaxation
is narrowly worded: separate polls are permitted only for genuinely independent questions.

### Risk 9: the one-shot claim permanently swallows a question
**Impact:** `claim_poll_answer` is taken before the poll close and before the steer. Any failure
after it — bridge death, a raising `EditMessageRequest`, a throwing steering write — leaves the
claim alive for its TTL. `iter_unanswered_polls()` skips claimed rows, so reconciliation never
retries, and if `poll_expired_unanswered` keyed on the missing claim it would never fire either. The
result is a silently, permanently blocked agent — the exact failure this feature exists to prevent.
**Mitigation:** three changes, all mandatory (Technical Approach, Task 9): everything after the claim
runs in `try/except Exception` with the handler **deleting the claim key** before logging;
completion is recorded as a separate `steered_at` field written only after the steer returns, with
`iter_unanswered_polls()` re-yielding "claim present, no `steered_at`, older than one reconcile
interval" and `poll_expired_unanswered` keying on **missing `steered_at`**; and a `dispatched`
marker written immediately after the steer/re-enqueue returns bounds the release, so a throw *after*
a successful side effect does not release the claim and re-run the dispatch. **The release and the
`dispatched` marker are a matched pair** — a blanket release re-opens the mirror-image failure
(one vote, two enqueues) that this plan's own `COMPLETED` branch is most exposed to. Covered by the
**Claim Durability** test block.

## Race Conditions

### Race 1: vote lands before the registry entry is written
**Location:** `bridge/telegram_relay.py` poll dispatch branch (new, after `:903`) — the window
between `send_poll` returning and the registry `SET`.
**Trigger:** An implausibly fast tap, or the relay process pausing between the send and the write.
**Data prerequisite:** `telegram:poll:{poll_id}` must exist before `translate_poll_vote` can route.
**State prerequisite:** The server-assigned poll id is only knowable after the send returns, so the
write genuinely cannot precede the send.
**Mitigation:** The Raw fast path finds no entry and returns quietly (never an error). The
reconciliation loop picks the vote up on its next tick. The window is milliseconds and the
consequence is bounded added latency, not loss.

### Race 2: Raw handler and reconciliation loop translate concurrently
**Location:** `translate_poll_vote` (new).
**Trigger:** An update arrives at the same instant a reconciliation tick reads the same entry.
**Data prerequisite:** Exactly one steering message per poll.
**State prerequisite:** The claim must be atomic across both async callers.
**Mitigation:** `SET telegram:poll:answered:{poll_id} 1 NX EX <ttl>`; the loser returns immediately
without steering or closing.

### Race 3: session transitions running → completed while the human is deciding
**Location:** `resolve_answer_target` / `resume_completed_session` (new `bridge/answer_routing.py`,
factored from `bridge/telegram_bridge.py:1799-2005`).
**Trigger:** The turn ends and the session finalizes before the tap.
**Data prerequisite:** The session must be re-read at translation time, not cached at send time —
`resolve_answer_target(session_id)` is called inside `translate_poll_vote`, never at send time.
**State prerequisite:** The completed-session re-enqueue must not double-enqueue.
**Mitigation:** `resolve_answer_target` carries the `pending/running/active` live re-check guard
verbatim from `:1856-1866` and returns `LIVE_GUARD` for it, so a session that came back to life
between the send and the tap is steered rather than re-enqueued. Double-enqueue is prevented by
`dispatch_telegram_session`'s own `claim_message(chat_id, poll_msg_id)` claim rather than by the
message handler's `is_duplicate_message` short-circuit (which the vote path does not use — see
Technical Approach).

### Race 4: human retracts and changes their vote
**Location:** Telegram client side; observed as a second `updateMessagePoll`.
**Trigger:** Non-quiz polls permit retract-and-revote until closed.
**Data prerequisite:** The agent must act on exactly one answer.
**State prerequisite:** The first translation must be final.
**Mitigation:** First claim wins, and the poll is closed as part of the first translation, so a
change of mind after the fact is not possible through the poll. The human can still correct
themselves with a normal reply-to message, which routes through the existing path.

### Race 5: bridge restart between send and vote
**Location:** Process lifecycle.
**Trigger:** `/update`, a crash, or a watchdog restart.
**Data prerequisite:** In-memory state must not be required to route a vote.
**State prerequisite:** The registry is the only source of truth.
**Mitigation:** All routing state lives in Redis with TTL. The reconciliation loop rescans the
registry on startup, so a vote cast while the bridge was down is still translated.

### Race 6: relay restart lands between a successful `send_poll` and the registry `SET`
**Location:** `bridge/telegram_relay.py::process_outbox` — the window between `send_poll`
returning and `register_poll(...)`.
**Trigger:** Not a freak crash. `process_outbox` `LPOP`s the queue entry **atomically before
dispatch**, so the work item is already consumed; and this plan's own **Update System** mandates
`./scripts/valor-service.sh restart` after merge. A restart in that window is routine.
**Consequence if unhandled:** a poll visibly on screen, no registry row, no outbox entry to retry.
Distinct from Race 1 (vote arrives before the write — self-healing once the write lands) and from
Race 5 (assumes the write already happened). This is *permanent* loss: the human taps and nothing
can ever route the vote.
**Data prerequisite:** a routable record must exist for every poll that reached the screen.
**State prerequisite:** the server-assigned poll id is only knowable after the send returns, so a
complete registry row genuinely cannot precede the send.
**Mitigation (both halves are required):**
1. **Provisional-row-first, and "first" means the first statement of `_send_queued_poll`** — not
   merely "before `send_poll`". `process_outbox` has already consumed the work item with an atomic
   `r.lpop` by the time the poll branch is entered, so the window between that LPOP and the
   provisional write is an **uncovered** one: a restart there loses the question with no
   provisional row at all, so orphan adoption has nothing to adopt and `poll_expired_unanswered`
   cannot fire. That is a silent total loss, strictly worse than the window this race documents.
   Nothing forces the ordering — `poll_id_hint` is on the payload and knowable immediately after
   the LPOP — so the write is pinned ahead of the eligibility re-check and every other await
   (Task 7), and every branch that declines to send deletes the provisional row on its way to the
   plain-text path. Write `telegram:poll:pending:{poll_id_hint}` → `{chat_id, session_id, question,
   options, created_at}` with `SET NX EX <POLL_REGISTRY_TTL_S>`. After the send returns, write the real
   `telegram:poll:{server_poll_id}` row and delete the provisional one. A restart in the window
   leaves the provisional row behind as evidence that a send may have landed.
2. **Orphan adoption in the reconciliation loop (Task 10), matched on an exact embedded key — not
   on question text.** Question text is not a unique key: an agent re-asking after a first poll
   expired unanswered, or two sessions in the same chat asking the same standard question, produce
   two candidates with no tie-break. So the correlation id is **carried inside the poll itself**.
   `PollAnswer.option` is an arbitrary `bytes` blob that Telegram echoes back verbatim on read, so
   every option is encoded as `f"{index}:{poll_id_hint}".encode()` instead of `bytes([index])`
   (still unique per option, which is the only constraint). Adoption then reads
   `MessageMediaPoll.poll.answers[0].option`, splits off the `poll_id_hint`, and matches it
   exactly against the provisional row's id. `translate_poll_vote`'s option selection parses the
   same encoding to recover the index, so nothing else changes.
   Adoption procedure: for each surviving provisional row, scan a bounded window of recent outbound
   history in that chat for a `MessageMediaPoll` whose embedded `poll_id_hint` equals the
   provisional row's id and which has no `telegram:poll:{poll.id}` row; write the real registry row
   from the provisional data plus the discovered `msg_id`/`poll.id`, then delete the provisional
   row. **If more than one candidate matches, bail with a warning and adopt nothing** rather than
   adopting the first hit — an ambiguous adoption steers a session with someone else's answer,
   which is worse than a dropped question. A provisional row that reaches its TTL with no match is
   dropped and logged at warning (the send never landed).

## No-Gos (Out of Scope)

- **1:1 DMs.** Settled by spike-6: a user account cannot send a poll into a DM, and the bot path
  that could was rejected on identity. `/ask-me` in a DM keeps today's prose behavior, permanently.
  Not deferred — **not being built**.
- **`teammate` sessions.** Owner decision: polls are an engineering affordance. A `teammate` session
  in an eligible group still gets prose. Not deferred — not being built.
- **A bot identity, a `getUpdates`/webhook inbound transport, and inline keyboard buttons.**
  Rejected on identity grounds, not capability. Not deferred — not being built.
- **Reaction-based answering.** Dropped by owner decision. Not deferred — not being built.
- `[EXTERNAL]` **The human's physical tap in Task 2 (`gate-poll-human-tap`).** It is a gate step and
  a Success Criterion; what remains external is only the human action itself. Because it gates
  **only the two inbound tasks**, an unavailable operator pauses those two and nothing else — the
  outbound path, eligibility gate, catchup rendering, and skill work all proceed.
- `[EXTERNAL]` **Rolling the change out to other bridge machines.** Requires `/update` on each
  machine (see **Update System**).
- **The `needs_human` / bare-`AskUserQuestion` turn-end path deliberately still delivers text.**
  `/ask-me` is the only poll trigger this plan builds. A question surfaced through the
  `needs_human` edge (`agent/session_runner/role_driver.py::_reconcile_turn_end`,
  `runner.py:1526-1531`) is not inspected for options and is not rendered as a poll. Task 12's role-
  prime wiring makes agents *reach* `/ask-me` rather than teaching that path to render polls; the
  Success Criteria are worded to match. Auto-detecting a question-with-options at turn end is Open
  Question 2 and is out of scope here.
- `[SEPARATE-SLUG #2701]` Nothing else is deferred to a follow-up. Voter quorum/authorization,
  quiz/multiple-choice polls, and free-text capture are **rabbit holes deliberately not built**
  (see **Rabbit Holes**), not deferred promises.

**The issue body predates the re-scope.** Where the issue body and the scope-decision comment
(`5237014662`) conflict, **the comment wins** and this plan follows the comment. Acceptance criteria
in the issue body that assume DM delivery are superseded by the Success Criteria below.

## Update System

- **`/update` changes required: yes, but only the standard steps.**
  - `pyproject.toml [project.scripts]` gains `valor-ask-poll = "tools.ask_poll:main"`. The new
    console script only materializes after a `uv sync` / editable reinstall, which
    `scripts/update/run.py` already performs. No new update step.
  - The bridge gains a new Telethon handler and a new background loop, so
    `./scripts/valor-service.sh restart` is **required** after merge — already part of `/update`.
    Verify with `tail -5 logs/bridge.log` showing "Connected to Telegram".
  - `.claude/skills-global/ask-me/SKILL.md` is hardlinked to `~/.claude/skills/` by
    `scripts/update/hardlinks.py`. Editing in place needs no registration change. The new
    `.claude/skill-context/ask-me.md` is repo-local and is **not** synced — that is the intent
    (the poll CLI only exists here).
  - No `RENAMED_REMOVALS` entry: nothing moves between `skills/` and `skills-global/`.
- **New dependencies to propagate:** none.
- **Migration steps for existing installations:** none. No Popoto model changes, therefore no
  `scripts/update/migrations.py` entry (spike-5). Registry keys are created on demand and expire
  on their own.
- **Config:** the new tunables are `TIMEOUTS__*`-style env-overridable constants with defaults, so
  no machine needs an `.env` edit. No new secrets.

## Agent Integration

- **New CLI entry point: required.** `valor-ask-poll = "tools.ask_poll:main"` in
  `pyproject.toml [project.scripts]`. This is the surface the agent reaches through Bash; a
  function in `tools/` alone would be invisible to it.
- **No new inbound transport.** Votes become steering messages on the existing path; the bridge
  gains a handler on its existing Telethon client, not a second identity or a second connection.
- **`/ask-me` must be taught to call it.** The global body
  (`.claude/skills-global/ask-me/SKILL.md`) gains the skill-context probe sentence and the
  relaxed one-at-a-time wording; `allowed-tools` already includes `Bash`. The repo-specific
  mechanics — the `valor-ask-poll` invocation, the mandatory literal final option
  `Other: wait for followup message`, the recommended-option-first ordering, and the
  local-vs-headless branch — go in `.claude/skill-context/ask-me.md`.
- **Bridge imports required.** `bridge/telegram_bridge.py` registers the new `events.Raw` handler
  and starts the reconciliation loop alongside `relay_loop` (`:3231-3233`).
  `bridge/telegram_relay.py` imports `send_poll` from `bridge/response.py` lazily, matching how it
  already imports `set_reaction` (`:184`).
- **Integration tests that the agent can actually invoke it:** a test executes
  `valor-ask-poll --help` through the installed console script and a test drives the CLI with a
  stubbed handler asserting the resulting outbox payload, mirroring
  `tests/unit/test_react_with_emoji.py::test_react_queues_reaction_payload` (`:61`).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/telegram-poll-questions.md` — the outbound payload variant, the poll
  registry keys and TTLs, the reconciliation-primary / Raw-fast-path design and why
  `UpdateMessagePoll` cannot self-route, the `Other: wait for followup message` escape hatch, and
  the degradation matrix (**telegram group + eng session → poll; telegram DM, teammate session,
  email, local, system → numbered text**).
- [ ] The feature doc must state the settled capability matrix (user account: DM ❌ / group ✅; bot:
  DM ✅ but rejected on identity) as a **constraint**, so a future reader does not re-probe it, and
  must document `bridge/poll_gating.py` as the single eligibility predicate read at two points.
- [ ] The feature doc must document the group semantics: anyone in the room can tap, first vote
  wins, the deterministic tie-break rule, and that attribution via `GetPollVotesRequest` is
  best-effort only.
- [ ] The feature doc must document `poll_update_observed` as the post-ship signal answering whether
  `updateMessagePoll` reaches a user account, and state that its permanent absence means deleting the
  Raw handler as dead weight — not a bug.
- [ ] The feature doc must document the Risk 9 separation as **three** markers, not two: `claim` is
  the one-shot lock, `dispatched` records that the steer/re-enqueue side effect has happened and
  bounds the claim release, and `steered_at` is the completion marker that both
  `iter_unanswered_polls()` and `poll_expired_unanswered` key on.
- [ ] The feature doc must document `poll_id_hint` as the Race-6 correlation key: minted once in
  `build_telegram_poll_outbox_payload`, carried on the outbox payload, embedded in the option bytes
  by `send_poll`, and matched exactly by orphan adoption — with the 100-byte `PollAnswer.option`
  ceiling stated as the reason the id is a 32-char hex and not a longer digest.
- [ ] The feature doc must document `telegram:poll:reconcile:heartbeat` as the loop's external
  liveness signal, name the health surface that reads it, and state why `poll_expired_unanswered`
  cannot serve that role (it is emitted from inside the loop it would be reporting on).
- [ ] The feature doc must carry the module map and say why each home was chosen:
  `bridge/poll_gating.py` (eligibility, importing `is_group_chat` from `bridge/read_the_room.py`
  rather than owning it), `bridge/poll_registry.py` (registry keys and helpers),
  `bridge/poll_vote.py` (`translate_poll_vote`), and `bridge/answer_routing.py` (the
  **poll-independent** seam shared with the reply-to ladder, which the translator deliberately
  imports rather than lives in, so it stays independently revertible).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` to name the poll vote as a steering producer.
- [ ] The feature doc must name `poll_expired_unanswered` as *the* operator signal for a failing
  inbound half, and document the Race-6 provisional row / orphan-adoption mechanism.
- [ ] Update `docs/features/message-drafter.md` with the `telegram_poll` validate-only medium, the
  public `validate_poll_question` entry point, and why the poll path deliberately does not go
  through `draft_message`'s composition step.
- [ ] The feature doc must document the plain-text re-enqueue on terminal relay failure as the
  user-visible fallback, and dead-lettering as the restart-scoped backstop behind it.
- [ ] Update `docs/features/session-isolation.md`? No — but **do** document the new
  `bridge/answer_routing.py` seam (`resolve_answer_target` / `resume_completed_session`) in
  `docs/features/session-steering.md` alongside the poll-vote steering producer, since the reply-to
  handler and the vote translator now share it.
- [ ] Update `docs/features/bridge-worker-architecture.md` to list the new bridge handler and
  background loop.
- [ ] Update `docs/features/read-the-room.md` (or wherever `_is_group_chat` is described) to record
  that the predicate is now the public `bridge/read_the_room.py::is_group_chat` with a second
  consumer (`bridge/poll_gating.py`). If no such doc exists, note the rename in the feature doc
  instead.
- [ ] Update `docs/tools-reference.md` with `valor-ask-poll`.
- [ ] Create `.claude/skill-context/ask-me.md` and add it to the table in
  `.claude/skill-context/README.md`.

### External Documentation Site
- [ ] None — this repo has no external docs site.

### Inline Documentation
- [ ] Docstring on `bridge/response.py::send_poll` recording that the caller-supplied `Poll.id` is
  a placeholder and the server-assigned id must be read back off `MessageMediaPoll`.
- [ ] Comment on the registry key constants explaining why this is plain Redis and not Popoto
  (points at `bridge/job_router.py:6-18`).
- [ ] Grain-of-salt comments marking every new interval/TTL constant as provisional and tunable.

## Success Criteria

- [ ] **Gate (Task 1):** a vote cast on a poll the bridge account sent into a machine-owned eng
  **group** is read back by that same account through `messages.GetPollResultsRequest`, with the
  chosen option recovered from `PollAnswerVoters` and the correlation-id option encoding intact.
  Probe output verbatim in the PR description. A FAIL stops the build.
- [ ] **Gate (Task 2):** a real human tap in that group is read back and, best-effort, attributed via
  `GetPollVotesRequest`, on a poll originated through the shipped `valor-ask-poll` chain. Output in
  the PR description. UNRESOLVED (no tap within `POLL_PROBE_TAP_WAIT_S`) pauses **only** the inbound
  set — Tasks 9, 10, 10a and 13b — and is recorded on #2701. This gate asserts readback and
  attribution; it deliberately does **not** assert the steer, because nothing observes a vote until
  Task 10 lands.
- [ ] **End-to-end, once, through the shipped chain (Task 10a).** A poll originated by invoking the
  real `valor-ask-poll` CLI from a **live** eng session bound to the machine-owned eng group — not by
  a direct `send_poll` call — exercises `tools/ask_poll.py`, `poll_eligible`,
  `build_telegram_poll_outbox_payload` (including the `poll_id_hint` mint), the outbox, the relay
  dispatch branch, the registry, the vote observer and `translate_poll_vote`. After the human tap the
  poll closes, the registry row gains `dispatched` then `steered_at`, and **the chosen option text
  appears in that session's next turn input.** Every other check in this plan is a unit test with a
  stubbed handler or a grep; this is the only assertion that the nine subsystems are actually wired
  together, and it is scheduled after Task 10 because that is the first point where it can pass.
- [ ] **The Race-6 correlation key has exactly one producer.** `poll_id_hint` is minted in
  `build_telegram_poll_outbox_payload` and nowhere else, carried on the outbox payload, and read by
  `_send_queued_poll` as both the `register_pending_poll` key and `send_poll(correlation_id=...)`.
  A poll sent with `correlation_id=None` emits `poll_sent_without_correlation_id` at warning.
- [ ] **One vote never produces two dispatches.** An exception thrown after a successful steer or
  re-enqueue leaves the `dispatched` marker set, does not release the claim, and the retry re-attempts
  only the `steered_at` write.
- [ ] **The reconciliation loop's death is detectable from outside the loop.**
  `telegram:poll:reconcile:heartbeat` is written every tick and its absence is surfaced at an existing
  health read site.
- [ ] **The eligibility re-check never runs on the bridge event loop.** Every `poll_eligible` call in
  `bridge/telegram_relay.py` goes through `asyncio.to_thread`.
- [ ] **The `answer_routing` extraction (Task 9a) is its own commit**, landed and green on the three
  named `tests/integration/test_steering.py` cases before Task 9b adds `translate_poll_vote`.
- [ ] **No probe opens a second client with updates enabled on the bridge's auth key.** The Task 1
  and Task 2 readback probes run against a temp copy of the session file with
  `receive_updates=False` and no writeback; Task 10a opens no client at all and observes through the
  live bridge. No probe touches a DM. Asserted by reading the probe scripts, and stated in the PR
  description.
- [ ] **The provisional registry row is written before anything else can fail.**
  `register_pending_poll(...)` is the first statement of `_send_queued_poll`, ahead of the
  eligibility re-check and every `await`, and each decline branch deletes it — so the
  LPOP-to-registry window carries no silent total-loss gap.
- [ ] **Module homes are the ones the Verification table greps.** The registry helpers live in
  `bridge/poll_registry.py`, `translate_poll_vote` lives in `bridge/poll_vote.py` (importing the
  `answer_routing` seam rather than living inside it), and `is_group_chat` stays in
  `bridge/read_the_room.py`.
- [ ] **An UNRESOLVED human-tap gate leaves the outbound half fully shippable.** Tasks 13a, 14 and
  15 all run with the inbound set paused, and Task 15 reports the inbound rows UNRESOLVED rather
  than passed. Checkable on the dependency graph, not only in prose: no outbound task and neither
  Task 14 nor Task 15 waits idle on `build-inbound-tests`.
- [ ] A question posed **via `/ask-me`** by an **eng** session in a **group** chat is delivered as a
  native Telegram poll with the recommended option first and `Other: wait for followup message` last.
  (Deliberately narrower than "any question posed at turn end" — see the No-Gos and Open Question 2.
  The build only wires the `/ask-me` trigger.)
- [ ] **The same question in a 1:1 DM is delivered as prose and queues no poll payload.** Verified by
  test, not by sending a poll into a DM.
- [ ] **The same question from a `teammate` session — even in an eligible group — is delivered as
  prose and queues no poll payload.**
- [ ] Eligibility fails closed: a missing `AgentSession` record, a `null` `session_type`, an
  unparseable or zero `chat_id`, and an exception inside the predicate all produce prose with a
  logged reason, never a poll and never a raise.
- [ ] `is_group_chat` exists in exactly one place — `bridge/read_the_room.py`, promoted from
  `_is_group_chat` — and `bridge/poll_gating.py` imports it; RTR behavior is unchanged.
- [ ] All three role primes (`.claude/commands/roles/prime-{dev,pm,teammate}-role.md`) direct a
  blocked headless session to invoke `/ask-me` instead of posing a judgment call in prose, so the
  trigger is reachable on the ordinary path and not only when an agent happens to remember the skill.
- [ ] **Each prime's added line is a conditional carrying the open-question precondition** (the same
  bar the auto-continue nudge loop uses), not a bare directive to pause when blocked.
- [ ] Tapping an option produces a steering message the worker consumes on its next turn, with the
  chosen option text present in the session's input.
- [ ] Voting `Other: wait for followup message` produces a plain-text followup question the human
  answers by reply-to, resuming the same session.
- [ ] With several group members having voted, option selection is deterministic: exactly one
  `voters >= 1` is used directly; ties resolve to highest `voters` then lowest decoded option index,
  with a warning naming the poll id and the tied options.
- [ ] A failure after `claim_poll_answer` deletes the claim so the next reconciliation tick retries,
  and `poll_expired_unanswered` keys on missing `steered_at` rather than on a missing claim.
- [ ] A vote is translated exactly once even when both the Raw handler and the reconciliation loop
  observe it, and even across a bridge restart.
- [ ] A poll that reached the screen but lost its registry row to a restart between send and write
  is adopted by the reconciliation loop and still routable (Race 6), matched on the correlation id
  embedded in the poll's option bytes — never on question text — and adopting nothing (with a
  warning) when the match is ambiguous.
- [ ] A vote whose target session has already completed reaches `resume_completed_session`, not
  `push_steering_message`; a vote whose target does not exist creates no session.
- [ ] A terminal poll relay failure puts the question in front of the human on a later relay cycle
  via a plain-text re-enqueue, not only in the `DeadLetter` table.
- [ ] An unanswered poll past the warn age emits exactly one greppable
  `poll_expired_unanswered` warning naming poll id, chat id, session id, and age (Risk 7).
- [ ] A vote on a session that has already completed re-enqueues that session rather than being
  dropped.
- [ ] Catchup transcripts render an outbound poll as its question text rather than a blank line,
  so the judge never re-asks a question already on screen.
- [ ] Email, local, and system surfaces produce today's text behavior via the CLI's degradation
  path; `EmailOutputHandler` remains valid without `send_poll`. This holds for **non-Telegram
  sessions specifically**, so the role-prime change cannot strand a question on a surface with no
  poll rendering.
- [ ] `/ask-me` states one-question-at-a-time as a preference with the hard prohibition removed,
  and the questionnaire-mode anti-pattern retained.
- [ ] Tests cover poll payload construction, vote → steering translation, and the `Other` fallback.
- [ ] Tests pass (`/do-test` via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `bridge/telegram_bridge.py` references the new Raw handler and the
  reconciliation loop, and that `pyproject.toml` declares `valor-ask-poll`.
- [ ] Not a bug fix — no xfail conversions apply.

## Team Orchestration

> **This section is a suggested division of labour, NOT a precondition for building.** Two
> independent builds of this plan hard-stopped here (issue comment `5237664288`): each read the
> named team below as mandatory, found no subagent-dispatch tool available to it, and halted —
> even though executing the tasks sequentially, itself, was available the whole time and is a
> perfectly good way to ship this plan. That was a plan defect, not an agent misjudgment, and this
> paragraph is the fix.
>
> **If you cannot spawn subagents, ignore the roster and work the Step by Step Tasks in dependency
> order yourself.** The names below exist to describe which tasks group naturally and may run in
> parallel; nothing in the Success Criteria or the Verification table checks who did the work. The
> only genuinely ordering-sensitive facts live in each task's `Depends On`.
>
> A related dispatch note from the same postmortem: when BUILD *is* dispatched to a child agent,
> dispatch it as agent type `builder`, not `general-purpose` — `builder` carries the full tool set.

The lead agent orchestrates and, when it has the tools to do so, delegates rather than building
directly.

### Team Members (suggested grouping)

- **Probe runner (vote-readback gates)**
  - Name: `poll-probe`
  - Role: Run the group vote-readback probe (Task 1), the human-tap probe (Task 2) and the
    end-to-end gate (Task 10a), reporting PASS / FAIL / UNRESOLVED with verbatim output. **Never
    probes a DM, never opens a client with updates enabled on the bridge's auth key, always works
    from a temp session copy** — Task 10a opens no client at all and rides the live bridge.
  - Agent Type: builder
  - Resume: true

- **Builder (outbound path)**
  - Name: `poll-outbound-builder`
  - Role: Eligibility gate (`bridge/poll_gating.py`), `bridge/response.py::send_poll`, payload
    builder, `send_poll` handler method, drafter medium, registry, relay dispatch branch, CLI.
  - Agent Type: builder
  - Resume: true

- **Builder (inbound path)**
  - Name: `poll-inbound-builder`
  - Role: Registry reader, `bridge/poll_vote.py::translate_poll_vote`, the
    `bridge/answer_routing.py` extraction
    (`resolve_answer_target` + `resume_completed_session`) and repointing the message handler at it,
    `events.Raw` handler, reconciliation loop.
  - Agent Type: builder
  - Resume: true

- **Builder (catchup + skill)**
  - Name: `poll-surfaces-builder`
  - Role: Catchup transcript rendering, `/ask-me` relaxation, `.claude/skill-context/ask-me.md`.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `poll-tester`
  - Role: All new test files and the Test Impact updates.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `poll-validator`
  - Role: Read-only verification against Success Criteria and the Verification table.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `poll-documentarian`
  - Role: Every item in the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

**Gate topology (cycle 4 split; Task 2 re-sequenced in cycle 5; E2E gate separated and Task 13 split
in cycle 6).** The *send* capability is settled by spike-6 and is not gated. Three gates remain —
Task 1 (automated), Task 2 (human tap, readback + attribution) and Task 10a (human tap, end to end).

The **inbound set** is Tasks 9, 10, 10a and 13b. An UNRESOLVED `gate-poll-human-tap` pauses exactly
that set. Tasks 3-8 and 11-12 depend on neither human gate, Task 13a depends on neither, and Tasks 14
and 15 run under the *satisfied-by-pause* rule stated in Task 14 — so an unavailable operator pauses
four tasks and leaves the outbound half fully built, tested, documented and validated, with the
inbound rows reported UNRESOLVED rather than green.

That property is why Task 13 is split. Before cycle 6 the single `build-tests` depended on
`build-vote-observation` and Tasks 14 and 15 chained behind it, so an UNRESOLVED gate blocked tests,
docs and validation — the whole outbound half — while the plan claimed in three places that it did
not. The dependency graph, not the prose, is now the thing that makes the claim true.

Task 2 runs **after** Task 8 so its probe poll originates through the shipped `valor-ask-poll` chain.
It asserts **readback and attribution only**: the end-to-end "the tapped option reaches the session's
next turn" assertion is **Task 10a**, which is the first point in the graph where any code observes a
vote. Two human taps are genuinely required — one before the inbound half exists (to prove a tap is
readable at all, which is what Task 9 is built against) and one after it exists (to prove the chain
is wired). Collapsing them into one is what made the cycle-5 criterion unsatisfiable.

**Commit sequencing (cycle 5).** Task 9a lands as its own commit before Task 9b — see Task 9a for
why reversibility depends on it.

### 1. GATE — vote readback in a group (HARD GATE, no human required)
- **Task ID**: gate-poll-vote-readback
- **Depends On**: none
- **Validates**: manual probe; output pasted verbatim into the PR description
- **Informed By**: spike-6 (send settled), Research findings 1, 2, 4, 5
- **Assigned To**: `poll-probe`
- **Agent Type**: builder
- **Domain**: MCP-tool/API integration
- **Parallel**: true (with Tasks 3, 4, 12)
- **This is the only remaining gate-worthy unknown.** Reconciliation is the primary inbound
  mechanism and there is nothing behind it, so if a vote cannot be read back the inbound half is
  impossible.
- **Expect a PASS, but run it anyway.** spike-8 records that the 2026-08-10 build already got a PASS
  here. That build's code is gone and was never reviewed or tested, so its result is a strong prior,
  not evidence. This gate is cheap, needs no human, and its whole value is producing a supervised
  result pasted into the PR. Do not skip it on the strength of spike-8.
- **Do NOT re-probe the send capability and do NOT send any poll into a 1:1 DM.** spike-6 settled
  both with verbatim MTProto results.
- **Production-safety constraints on the probe (mandatory, and the reason the push question is not
  gated):**
  - Open a **copy** of the bridge Telethon session file, never the live one, and do not write the
    session back.
  - Construct the client with `receive_updates=False`. A second client with updates *enabled* on the
    bridge's auth key can consume updates the live bridge needs — that is a production hazard, and
    it is precisely why "is `updateMessagePoll` pushed to a user account?" is **not** a gate here.
    That question is answered after ship by the `poll_update_observed` log line inside the Raw
    handler (Task 10); a negative there deletes the handler as dead weight and changes nothing else.
  - Everything the probe sends is deleted before the task reports.
- Resolve a **machine-owned eng group** peer from `~/Desktop/Valor/projects.json` (the verified
  reference group is `Eng: Valor`, `-1003449100931`).
- Send a two-option probe poll with options encoded as `encode_option(index, correlation_id)`
  (Task 4's encoding) rather than bare `bytes([i])`, so the gate exercises the real encoding.
- **Cast a vote from the probe account itself** via `messages.SendVoteRequest`. This is a
  probe-only affordance: production never votes, which is what keeps "the sender never votes" true.
- Read back `messages.GetPollResultsRequest(peer, msg_id)` and assert: the chosen option is
  recoverable from `PollAnswerVoters`, `total_voters >= 1`, and `decode_option(...)` recovers both
  the index and the correlation id from the option bytes echoed by the server.
- Record the **server-assigned `poll.id`** and confirm it differs from the id supplied — this
  re-validates the registry design against the group surface.
- Also exercise closing the poll by editing it with `closed=True`, then delete the message.
- **PASS** → paste the probe output into the PR description; the inbound set (Tasks 9, 10, 10a, 13b)
  is unblocked.
- **FAIL** → **STOP the build.** Report the verbatim error, record it on #2701, and hand back for
  a design revision — without readback there is no inbound half on this surface.

### 2. GATE — human tap readback and attribution (bounded; UNRESOLVED permitted)
- **Task ID**: gate-poll-human-tap
- **Depends On**: gate-poll-vote-readback, build-ask-poll-cli, build-relay-dispatch
- **Validates**: manual probe; output pasted verbatim into the PR description
- **Informed By**: Research finding 5, Risk 2b
- **Assigned To**: `poll-probe`
- **Agent Type**: builder
- **Parallel**: false
- **Depends On** is `gate-poll-vote-readback` for the *probe mechanics*, and `build-ask-poll-cli` +
  `build-relay-dispatch` for the origination path below. Sequence this task after Task 8 lands.
- **The probe poll is originated through the real outbound chain.** Do **not** call `send_poll`
  directly here. Invoke the shipped `valor-ask-poll` CLI from an eng session bound to the
  machine-owned eng group, so the probe exercises `tools/ask_poll.py` → `poll_eligible` →
  `build_telegram_poll_outbox_payload` (including the `poll_id_hint` mint) →
  `telegram:outbox:{session_id}` → `_send_queued_poll` → `send_poll` → the registry, rather than a
  direct MTProto call that touches none of them. **Zero extra operator cost** — this task already
  waits `POLL_PROBE_TAP_WAIT_S` for a tap; only the poll's origin changes.
- **This task asserts readback and attribution only — it does NOT assert the steer.** The
  end-to-end "the tapped option reaches the session's next turn" assertion lives in **Task 10a**
  (`gate-poll-e2e`), and it has to: nothing that observes a vote exists yet at this point in the
  graph. `translate_poll_vote` is Task 9 (which `Depends On` *this* task) and the `events.Raw`
  handler plus the reconciliation loop are Task 10, so a tap here emits an `updateMessagePoll` that
  **no code in the tree observes**. Asserting the steer here would be an impossible criterion — the
  build would either stall on it or ship green by quietly dropping it. Do **not** "fix" that by
  relaxing Task 9's `Depends On: gate-poll-human-tap`; that would build the whole inbound half
  against an unverified readback, which is exactly what this gate exists to prevent.
- The readback half runs under the same production-safety constraints as Task 1 (session copy,
  `receive_updates=False`, no writeback, group only, cleanup after). The **origination** half runs as
  the live bridge, which is the point.
- Leave the poll **open**, with a caption naming exactly
  what is being asked and the deadline: *"Build gate for #2701 — please tap either option on the
  poll above. If no tap arrives within 30 minutes the build reports this gate unresolved and
  continues with the outbound half."*
- Then **surface a legitimate open question through the normal pause path** — a real question, not
  a status update. A bare "waiting for tap" note is auto-continued past by the nudge loop (CLAUDE.md,
  Auto-continue) and would produce exactly the silent stall this guards against.
- **Bounded wait:** poll `messages.GetPollResultsRequest(peer, msg_id)` on a fixed interval for at
  most `POLL_PROBE_TAP_WAIT_S` (named constant, default **1800 s / 30 min**, grain-of-salt).
- On a tap, record: (a) the `GetPollResultsRequest` response, proving another user's vote is
  readable; (b) whether `messages.GetPollVotesRequest` resolves the voting peer, which decides
  whether the steer text can name the voter or must fall back.
- A negative on (b) is **not** a failure — attribution is best-effort by design. A negative on (a)
  **is** a gate failure for the inbound half.
- **On timeout:** close and delete the probe poll, report the gate **UNRESOLVED** (distinct from
  PASS and FAIL), record it on #2701, and **pause the inbound set only — Tasks 9, 10, 10a and 13b**.
  Every other task, including the outbound test suite (Task 13a), documentation (Task 14) and final
  validation (Task 15), proceeds. Resume by re-running this task when the operator is available.
- Close and delete the probe poll before reporting, in every outcome.

### 3. Poll eligibility gate — `bridge/poll_gating.py`
- **Task ID**: build-poll-eligibility
- **Depends On**: none
- **Validates**: `tests/unit/test_poll_gating.py` (create)
- **Informed By**: spike-7, Research findings 7 and 8, owner decisions 1 and 2
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: true (with Tasks 1, 4, 12)
- Create `bridge/poll_gating.py` as the single home for "does this surface take a poll".
- **Rename `_is_group_chat` to the public `is_group_chat(chat_id)` in place, in
  `bridge/read_the_room.py:126`**, keeping its documented semantics verbatim (negative id → group;
  `None`, unparseable, or non-negative → `False`), and have `bridge/poll_gating.py` **import** it.
  **One definition, no copy** — a second copy would silently drift from the RTR suppression rule —
  and the predicate stays where it generically belongs. Do **not** move it into `poll_gating.py`:
  it is a generic Telegram peer-type predicate whose existing consumer is read-the-room, and
  hosting it in a feature module would make read-the-room import from a poll module, which is the
  naming inversion that later invites a second copy.
- Add `PollEligibility(ok: bool, reason: str)` and
  `poll_eligible(chat_id, session_id) -> PollEligibility`:
  - not a group → `not_a_group`
  - `AgentSession.query.filter(session_id=session_id)` finds a record with
    `session_type == SessionType.ENG` → `ok=True`, reason `eligible`
  - record with `session_type == SessionType.TEAMMATE` → `not_eng_session`
  - no record, or a `null`/unrecognized `session_type` → `unknown_session_type` (**ineligible** —
    the field is `null=True`, `models/agent_session.py:156`, and prose to an eng session is a
    cosmetic loss while a poll to a teammate chat is a scope violation)
  - any exception → `eligibility_error`, logged at warning
- **Never raises.** Fail closed on every ambiguity.
- Compare against `SessionType` from `config/enums.py` — do not string-compare a bare literal in two
  places.

### 4. Poll primitive in `bridge/response.py`
- **Task ID**: build-send-poll
- **Depends On**: none
- **Validates**: `tests/unit/test_poll_payload.py` (create)
- **Informed By**: spike-1, spike-6 (the exact construction verified working in a group)
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: true (with Tasks 1, 3, 12)
- Add `send_poll(client, chat_id, question, options, *, reply_to=None, correlation_id=None)
  -> tuple[int, int] | None` after `set_reaction` (`bridge/response.py:384`), returning
  `(msg_id, server_poll_id)`.
- Wrap text in `TextWithEntities`; single-choice, non-quiz only.
- **Option bytes carry the correlation id (Race 6 exact-match key).**
  `option = f"{index}:{correlation_id}".encode()` when `correlation_id` is given, else
  `f"{index}".encode()`. Provide `encode_option(index, correlation_id)` / `decode_option(raw)`
  helpers next to it so the translator, the orphan-adoption scan, **and the Task 1 gate probe** parse
  the same encoding in one place. Uniqueness per option (Telegram's only constraint on `option`) is
  preserved by the index prefix.
- **The bare `else` branch is a probe-only affordance, never a production path.** Every production
  caller reaches `send_poll` through Task 7's `_send_queued_poll`, which reads the mandatory
  `poll_id_hint` off the outbox payload (minted by `build_telegram_poll_outbox_payload`, Task 5) and
  always passes it as `correlation_id`. Emit `logger.warning("poll_sent_without_correlation_id ...")`
  when `correlation_id is None` so a builder who wires the relay branch without threading the hint
  gets a loud signal instead of a silently dropped Race-6 mitigation. `decode_option` returns
  `(index, None)` for the bare form so the probe still parses.
- Follow the local error idiom (log, return `None`, never raise) but keep the `None` return
  distinguishable so the relay retries rather than silently dropping.
- Docstring records the placeholder-id / server-assigned-id asymmetry **and** that this primitive is
  group-only in practice (a DM send is rejected with `MediaInvalidError` — spike-6), pointing at
  `bridge/poll_gating.py` as the enforcement point.

### 5. Outbound payload, handler method, drafter medium
- **Task ID**: build-outbound-payload
- **Depends On**: build-send-poll, build-poll-eligibility
- **Validates**: `tests/unit/test_poll_payload.py`, `tests/unit/test_medium_validators.py` (update)
- **Informed By**: spike-3 (the three text-less guards), Prior Art #1802 template
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `build_telegram_poll_outbox_payload(chat_id, question, options, reply_to, session_id)` beside
  `build_telegram_outbox_payload` (`agent/output_handler.py:270`), emitting
  `{"type": "poll", chat_id, reply_to, question, options, session_id, timestamp, poll_id_hint}`.
  **Do not stamp `session_type` into the payload** — a queued payload would outlive a session's real
  type; the relay re-reads eligibility instead (Task 7).
- **`poll_id_hint` is minted here and nowhere else — this function is its sole producer.**
  `poll_id_hint = uuid.uuid4().hex` (32 lowercase hex chars, no dashes), stamped into the payload
  dict unconditionally. It is the correlation key the entire Race-6 mitigation is built on: Task 7
  passes it to both `register_pending_poll(...)` (as the registry key) and
  `send_poll(..., correlation_id=poll_id_hint)` (which embeds it in the option bytes), and Task 10's
  orphan adoption matches on it. **Size budget:** `f"{index}:{hex32}"` is at most 35 bytes, inside
  Telegram's 100-byte `PollAnswer.option` ceiling. Do not substitute a longer id (a payload digest,
  a composite `session_id:timestamp` string) — those can exceed the ceiling and the send fails at
  the wire with no local signal.
- Add `TelegramRelayOutputHandler.send_poll(...)` as a sibling of `send` (`:611`): validate via the
  drafter and rpush with the existing `OUTBOX_TTL` (`:431`). It must not hit the `if not text` early
  return at `:673-674`. It records **no** expectation — its sibling `send` (`:611`) records none
  either, and authoring an obligation with no resolution path was struck in revision cycle 5.
- Add `medium="telegram_poll"` to `_validate_for_medium` (`bridge/message_drafter.py:561`) —
  **validate only, question text only**: `<= 300` chars and non-empty. **Do not change the
  signature** (`(text: str, medium: str)`) — it cannot see options, and widening it ripples to
  `:1100`, `:1148`, and `tests/unit/test_medium_validators.py`. Option-count and option-length
  validation lives in `tools/ask_poll.py` (Task 8).
- Add the public wrapper `validate_poll_question(question: str) -> list[Violation]` in
  `bridge/message_drafter.py` (`return _validate_for_medium(question, "telegram_poll")`) and call
  **that** from `send_poll`. Do **not** call `draft_message` — it runs `_compose_structured_draft`
  at `:1145` before validating at `:1148`, so it would return the question with the emoji prefix /
  stage line / link footer attached. `_compose_structured_draft` is never reached on the poll path.

### 6. Poll registry
- **Task ID**: build-poll-registry
- **Depends On**: build-outbound-payload
- **Validates**: `tests/unit/test_poll_registry.py` (create)
- **Informed By**: spike-2 (update cannot self-route), spike-5 (plain-Redis precedent), Risk 9
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- **Create `bridge/poll_registry.py`** holding the registry key constants and every helper this
  task defines: `register_poll(...)`, `lookup_poll(poll_id)`, `claim_poll_answer(poll_id)`,
  `release_poll_claim(poll_id)`, `mark_poll_dispatched(poll_id)`, `mark_poll_steered(poll_id)`,
  `register_pending_poll(...)`, `iter_pending_polls()`, `promote_pending_poll(...)`, and
  `iter_unanswered_polls()`. This is the module the Verification table's `poll_id_hint` row greps;
  housing these helpers anywhere else fails a Verification row on an otherwise correct build.
- Write `telegram:poll:{poll_id}` →
  `{chat_id, msg_id, session_id, question, options, created_at, dispatched, steered_at}` with
  `SET ... NX EX <POLL_REGISTRY_TTL_S>`, following `bridge/job_router.py:85-96` and
  `bridge/context.py:513-529`. `dispatched` and `steered_at` both start absent.
- **`dispatched` and `steered_at` are two distinct markers, not one.** `dispatched` records "the
  steer/re-enqueue side effect has happened and must never be repeated"; `steered_at` records "the
  translation completed cleanly". `iter_unanswered_polls()` keys on missing `steered_at`;
  `translate_poll_vote`'s exception handler keys on missing `dispatched` (Task 9b). Collapsing them
  into one field re-opens either Risk 9 (a swallowed question) or the double-enqueue.
- Plain Redis string keys. **No Popoto model, no `scripts/update/migrations.py` entry.** Comment the
  rationale pointing at `bridge/job_router.py:6-18`.
- Provide the Race-6 provisional row `telegram:poll:pending:{poll_id_hint}`, written **before**
  `send_poll` and promoted to the real row (then deleted) once the server poll id is known. This
  write belongs **here and only here** — it is not duplicated in the relay task.
- Provide `register_poll(...)`, `lookup_poll(poll_id)`, `claim_poll_answer(poll_id)` (`SET NX EX`),
  `release_poll_claim(poll_id)`, `mark_poll_dispatched(poll_id)`, `mark_poll_steered(poll_id)`,
  `register_pending_poll(...)`,
  `iter_pending_polls()`, `promote_pending_poll(...)`, and `iter_unanswered_polls()`.
- **`iter_unanswered_polls()` treats a row as unanswered when it has no `steered_at`** — including a
  row whose claim exists but is older than one reconcile interval (Risk 9). It must not treat "claim
  present" alone as answered.
- All TTLs are named env-overridable constants with grain-of-salt comments.

### 7. Relay dispatch branch, eligibility re-check, dead-letter and fallback
- **Task ID**: build-relay-dispatch
- **Depends On**: build-poll-registry
- **Validates**: `tests/unit/test_bridge_relay.py` (update),
  `tests/unit/test_telegram_relay_chat_log.py` (update), `tests/unit/test_relay_job_record.py`
  (update)
- **Informed By**: spike-3, spike-7, Risk 5
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `"poll"` to `KNOWN_MESSAGE_TYPES` (`bridge/telegram_relay.py:58`) and a dispatch branch in the
  `msg_type` if/elif chain (`:917-931`) calling a new `_send_queued_poll`. Locate by symbol.
- **`register_pending_poll(...)` is the FIRST statement of `_send_queued_poll`** — before the
  eligibility re-check and before any `await`. `process_outbox` consumes the work item with an
  atomic `r.lpop` (`:895-897`), so from that instant until a row exists the question has **no**
  durable record anywhere: a restart in that window is a silent total loss with nothing for orphan
  adoption to adopt and no row for `poll_expired_unanswered` to fire on — strictly worse than the
  Race-6 window, which at least leaves a provisional row behind. Nothing forces the later ordering:
  `poll_id_hint` rides on the payload and is knowable the moment the LPOP returns. Every branch that
  then declines to send a poll (missing `poll_id_hint`, ineligible, terminal failure) **deletes the
  provisional row** as it converts to plain text, so a declined send never ages into a spurious
  orphan-adoption candidate.
- **Re-check `poll_eligible(chat_id, session_id)` immediately after that write** (defense in
  depth: the CLI decided at ask time, the relay is the last writer before the wire, and a payload can
  sit in the outbox across a session-type change). Ineligible → **convert to the plain-text payload
  and deliver it**, log the reason, and do not send a poll. Never drop.
- **The re-check MUST be thread-offloaded:
  `eligibility = await asyncio.to_thread(poll_eligible, chat_id, session_id)`.** `_send_queued_poll`
  runs on the bridge event loop, and `poll_eligible`'s second clause is
  `AgentSession.query.filter(session_id=session_id)` — `session_id` is a plain `Field()`
  (`models/agent_session.py:158`), not a `KeyField`, so that is an unindexed scan this plan itself
  costs at ~2.4s. Calling it inline stalls every other bridge coroutine for seconds per poll send.
  Mirror the existing precedent at `bridge/telegram_relay.py:1132`
  (`await asyncio.to_thread(_record_sent_message, ...)`) and the `_append_outbound_chat_log` comment
  at `:921` ("runs in a thread … and cannot await"). **`poll_eligible` itself stays synchronous**
  (Task 3 unchanged) so `tools/ask_poll.py`, which runs off the event loop, calls it directly.
- **Read `poll_id_hint` off the payload** (`message["poll_id_hint"]`, minted by Task 5) and thread it
  through as **both** the `register_pending_poll(...)` key and `send_poll(..., correlation_id=...)`.
  A payload arriving without the key is a producer bug: log at error and fall through to the
  plain-text delivery path rather than sending a poll no vote can ever be routed to.
- Fix the dead-letter path for text-less payloads, which has **two** guards, not one:
  (a) `_dead_letter_message` (`:803`) must not add `"poll"` to the ephemeral discard tuple
  `if msg_type in ("reaction", "custom_emoji_message")` (`:827`); and (b) the persistence branch
  immediately below is gated on `if chat_id and text` (`:836`) — a poll payload has no `text` key, so
  keeping it out of the ephemeral tuple alone still drops it silently. The poll branch must supply
  the question as the dead-letter `text`. This is the **durability backstop only** —
  `replay_dead_letters` runs from one site in the bridge connect sequence
  (`bridge/telegram_bridge.py:2842`), i.e. next restart.
- **Prompt user-visible fallback (the actual delivery path).** In the terminal-failure branch
  (`:1029-1034`, `attempts >= MAX_RELAY_RETRIES`) for a `"poll"` payload, rpush a plain payload back
  onto the same `telegram:outbox:{session_id}` key:
  `{"type": None, "chat_id": chat_id, "text": <question + numbered options>, "reply_to": reply_to,
  "session_id": session_id}` with `_relay_attempts` **reset to 0**. Safe against looping: the loop is
  `while processed < RELAY_BATCH_SIZE` over `r.lpop` of the same key (`:895-897`), so the rpush is
  consumed on a later cycle, and the payload is plain text which never re-enters the poll branch.
  **The ineligibility branch above reuses this same text-payload builder** — one plain-text
  rendering of a question, not two.
- Call `promote_pending_poll(...)` after `send_poll` returns (functions from Task 6; this task calls
  them, it does not define them). `register_pending_poll(...)` is already pinned as the first
  statement of the function, per the bullet above.
- On success, run the existing post-send bookkeeping (`_record_sent_message` `:984`,
  `_append_outbound_chat_log` `:988`, `_bind_outbound_message_to_job` `:994`) and add a history row
  with `message_type="poll"`, modelled on `_record_sent_reaction` (`:182-212`).

### 8. `valor-ask-poll` CLI
- **Task ID**: build-ask-poll-cli
- **Depends On**: build-poll-eligibility, build-outbound-payload
- **Validates**: `tests/unit/test_ask_poll_cli.py` (create), `tests/unit/test_send_message.py`
  (update)
- **Informed By**: owner decisions 1, 2, 4; Data Flow step 3a
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/ask_poll.py` with `main()`, reusing `_resolve_transport()` precedence
  (`tools/send_message.py:63`).
- **Degradation happens here, once.** Non-telegram transport → numbered-list text through the
  existing `send_message` path. Telegram transport → call `poll_eligible(chat_id, session_id)`;
  ineligible → the same numbered-list text path, with the reason logged so a question that "should
  have been a poll" is diagnosable; eligible → `send_poll`. Probe the handler with
  `hasattr(..., "send_poll")` mirroring `adapter.py:61` so `EmailOutputHandler` stays valid.
- Own **all option validation** here: 2..10 options, each non-empty and `<= 100` chars, exiting
  non-zero on violation. Append / de-duplicate the mandatory literal final option
  `Other: wait for followup message`.
- Register `valor-ask-poll = "tools.ask_poll:main"` in `pyproject.toml [project.scripts]`.

### 9. Extract `bridge/answer_routing.py` and add `translate_poll_vote`
- **Task ID**: build-vote-translation
- **Depends On**: build-relay-dispatch, gate-poll-human-tap
- **Validates**: `tests/unit/test_poll_vote_translation.py` (create),
  `tests/integration/test_steering.py` (update)
- **Informed By**: spike-2, Research findings 4 and 5, Races 2 and 3, Risks 3 and 9
- **Assigned To**: `poll-inbound-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false

**9a. Create `bridge/answer_routing.py` with two extractions — not one function.** The reply-to
ladder is an inline block in `handler(event)` and cannot be lifted at a `(session_id, text, sender)`
signature; every branch consumes `event`/`message`/`project`. Extract only the genuinely shared
parts and leave caller-specific side effects in the caller:

- `resolve_answer_target(session_id) -> AnswerTarget` — a state read over the ladder at
  `bridge/telegram_bridge.py` ~`:1899-1966` (**locate by symbol** — start at
  `_steering_session_enqueued = False` and read to the completed branch). `AnswerTarget` is
  `(kind: AnswerTargetKind, session: AgentSession | None, matched_status: str | None,
  pending_age_s: float | None)`; `AnswerTargetKind` is `LIVE | PENDING | LIVE_GUARD | COMPLETED |
  NONE`. **This is a restructure, not a verbatim lift** — the source interleaves the ladder with
  `await _ack_steering_routed(...)` + `return` at every branch. Behavior-preservation checklist,
  all four mandatory:
  1. `matched_status` carried because the LIVE log embeds `matching_session.status` and the
     LIVE_GUARD log embeds `live_guard.status`.
  2. `pending_age_s` carried because the PENDING log embeds `age=%.1f` from
     `_pending_session_age_seconds(pending_session.created_at, time.time())` (helper at `:153`,
     call site now `:1944`).
  3. `COMPLETED` returns the record chosen by the existing most-recent-`created_at` sort
     (`_completed_created_at`, now `:2017`), **not** `completed_sessions[0]` — the wrong record silently
     degrades `_build_completed_resume_text`'s `context_summary`.
  4. `_steering_session_enqueued = True` (now `:2124`) stays caller-side; `resume_completed_session`
     returns `None`.
  5. **`room_id` derivation stays with whoever calls `push_steering_message`.** The ladder now
     derives `room_id=room_id_for_session(<the session that branch selected>)` at each of its three
     steering branches (`:1927`, `:1956`, `:1996`), and the LIVE branch's `matching_session` is
     chosen by a newest-first `created_at` sort *specifically so the Room is derived from the live
     row*. `resolve_answer_target` must preserve that sort and return the session object itself —
     returning only a `kind` would strand both callers with no way to derive the Room.
  The `LIVE_GUARD` kind is the existing belt-and-suspenders re-check (locate by symbol; formerly
  `:1856-1866`, now ~`:1956-1966`).
- `resume_completed_session(*, completed, text, sender_name, telegram_chat_id,
  telegram_message_id, chat_title=None, sender_id=None, project=None, project_key=None,
  working_dir=None, telegram_message_key=None, reply_chain_context=None,
  extra_context_overrides=None) -> None` — lifted from `~:2051-2124`:
  `_build_completed_resume_text(completed, text, reply_chain_context=...)` then
  `dispatch_telegram_session(...)`. Any of `project` / `project_key` / `working_dir` /
  `session_type` left `None` falls back to the corresponding field on the `completed` record.
  `react_if_worker_down` stays in the message handler — it needs the inbound `message.id`.

Repoint the existing message handler at both: it keeps its `_ack_steering_routed` calls, its
`is_duplicate_message` short-circuit, its reply-chain hydration, and its `react_if_worker_down`, and
only replaces the inlined `query.filter` ladders and the inlined dispatch. **Behavior must not
change**; the existing steering tests plus
`tests/integration/test_steering.py::test_reply_to_completed_session_reenqueues_with_context` are the
regression net.

**9a lands as a standalone commit, before 9b (mandatory).** This task restructures the reply-to
steering ladder — the primary inbound path for every typed Telegram reply on every machine — and
repoints the live handler at it, and the plan itself concedes it is "a restructure, not a verbatim
lift". Commit it touching **only** `bridge/answer_routing.py` and `bridge/telegram_bridge.py`, green
on `tests/integration/test_steering.py::test_steering_push_only_after_session_match`,
`::test_pending_session_within_window_receives_steering`, and
`::test_reply_to_completed_session_reenqueues_with_context`, **before** 9b adds
`translate_poll_vote`. The reason is narrow and concrete: reverting the poll feature does not revert
the ladder restructure, so if 9a and 9b share a commit, a post-merge regression on ordinary typed
replies — a path with far more traffic than polls — has no narrow revert. With the split,
`git revert <9a-sha>` is a real option.

**9b. Add `translate_poll_vote(client, poll_id)` — in a new `bridge/poll_vote.py`, not in
`answer_routing.py`.** The translator imports `resolve_answer_target` / `resume_completed_session`
from `bridge/answer_routing.py` and the registry helpers from `bridge/poll_registry.py`. It must
**not** be housed inside `answer_routing.py`: 9a's entire justification for that module is that it
is a **poll-independent** seam shared with the reply-to ladder, landed as its own commit so
`git revert <9a-sha>` is real. Putting the poll translator inside it re-fuses what 9a split apart.
- `lookup_poll`; unknown → return quietly.
- Confirm with `messages.GetPollResultsRequest(peer=chat_id, msg_id=msg_id)` rather than trusting a
  possibly `min=True` update.
- **Deterministic group selection rule** (the DM rule is dead — several members can vote):
  filter `results.results` to `voters >= 1`; zero → return **without claiming**; exactly one → use
  it; more than one → log a warning naming the poll id and the tied options, then take the highest
  `voters`, breaking ties by **lowest decoded option index** (`decode_option`, Task 4).
- `claim_poll_answer(poll_id)`; lost claim → return.
- **Everything after the claim runs inside `try/except Exception`; the handler calls
  `release_poll_claim(poll_id)` before logging** so the next reconciliation tick retries (Risk 9) —
  **but only when the `dispatched` marker is absent** (next bullet).
- **`dispatched` marker — the claim-release must not span the side effect.** The moment
  `push_steering_message` / `resume_completed_session` returns, and **before anything else that can
  throw**, call `mark_poll_dispatched(poll_id)` (Task 6). The `except Exception` handler then reads
  the row and calls `release_poll_claim(poll_id)` **only if `dispatched` is absent**. With
  `dispatched` present the retry path re-attempts `mark_poll_steered(poll_id)` **only** — never the
  steer or the dispatch. Without this split, an exception thrown *after* a successful steer (e.g.
  from the `mark_poll_steered` write, which is deliberately last) releases the claim and the next
  reconcile tick re-runs the whole translation. The stated second guard cannot cover that window:
  `claim_message` inside `dispatch_telegram_session` uses `bridge/dedup.py:168 CLAIM_TTL_SECONDS`,
  which is deliberately short (its own comment: "keep `CLAIM_TTL_SECONDS` short. A long TTL here was
  a BLOCKER in an earlier critique round") and the slow reconcile interval outlives it — so on the
  `COMPLETED` branch one vote double-enqueues the session.
- Close the poll by editing with `closed=True`. In a group this is the first-voter-wins boundary and
  is intentional.
- Resolve `sender_name`: `messages.GetPollVotesRequest` best-effort → target session's
  `initial_telegram_message["sender_name"]` → the literal `"Telegram poll"`. A failure here never
  blocks translation.
- Build the steer text: `Poll answer to your question "<question>": <chosen option>`; for the escape
  hatch, instruct a narrowed plain-text followup the human answers by reply-to.
- Branch on `resolve_answer_target(session_id)`, with a stated outcome for **every** kind:
  - `LIVE` / `PENDING` / `LIVE_GUARD` → `push_steering_message(session_id, steer_text, sender_name,
    room_id=room_id_for_session(target.session))`. **`room_id` is mandatory** — without it the write
    silently lands on the legacy `steering:{session_id}` key while every peer caller writes the Room
    key (see Technical Approach). No `_ack_steering_routed` (no inbound message to react to; closing
    the poll is the acknowledgment) and deliberately no abort-keyword detection (a poll option is
    never an abort — and note `push_steering_message` force-routes any auto-detected abort to the
    legacy key regardless of `room_id`, which is why a poll option must never trip `ABORT_KEYWORDS`).
  - `COMPLETED` → `resume_completed_session(completed=target.session, text=steer_text,
    sender_name=..., telegram_chat_id=<registry chat_id>,
    telegram_message_id=<the poll's own msg_id from the registry row>, reply_chain_context=None)`.
    A vote has no reply chain, so the summary-only preamble is the correct output. The poll's
    `msg_id` is a safe dedup key: `dispatch_telegram_session` claims `(chat_id,
    telegram_message_id)` via `bridge/dedup.py:171 claim_message` (keyspace `bridge:msgclaim:`),
    which is inbound-only and therefore never already claimed for an outbound poll.
    **This branch is mandatory — silently dropping it re-opens Risk 3.**
  - `NONE` → log at info and return. Never create a session.
- **`mark_poll_steered(poll_id)` runs only after the steer / re-enqueue returns** (Risk 9), and is
  the field `iter_unanswered_polls()` and `poll_expired_unanswered` key on.
- `is_duplicate_message` is **not** called on the vote path — idempotency is `claim_poll_answer`
  (`SET NX`) plus `claim_message` inside `dispatch_telegram_session`.
- **Never write a steering key directly** — always via `agent/steering.py:176 push_steering_message`
  (coordination note with `docs/plans/flip-steering-writers-to-room-key.md`).

### 10. Reconciliation loop (primary) + `events.Raw` fast path
- **Task ID**: build-vote-observation
- **Depends On**: build-vote-translation
- **Validates**: `tests/unit/test_poll_vote_translation.py`, `tests/unit/test_poll_registry.py`
- **Informed By**: Risk 2 (push unverified by design), Risk 7, Risk 9, Races 1, 5, 6
- **Assigned To**: `poll-inbound-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Add the reconciliation loop started alongside `relay_loop` (`:3231-3233`): iterate
  `iter_unanswered_polls()` only, call `translate_poll_vote` on each, adaptive interval (fast for
  `POLL_RECONCILE_FAST_WINDOW_S` after send, then slow), FloodWait backoff mirroring
  `telegram_relay.py:918-945`. **This loop is the primary mechanism** and is correct on its own.
- **Loop heartbeat, readable from outside the loop (Risk 7).** At the top of **every** tick, before
  the scan, write `telegram:poll:reconcile:heartbeat` with
  `SET <iso timestamp> EX POLL_RECONCILE_HEARTBEAT_TTL_S` (named env-overridable constant, default
  **2x the slow interval**, grain-of-salt comment). Surface its **absence** at one existing external
  read site — the dashboard health payload or the bridge health check — as a degraded signal naming
  the loop. This is the only detector for "the loop itself died": `poll_expired_unanswered` is
  emitted from inside `iter_unanswered_polls()`, so if the loop is what died that signal cannot
  fire. One new Redis key plus one read site; **do not add a new service or a second watchdog**.
- Register the repo's **first** `@client.on(events.Raw)` handler in `bridge/telegram_bridge.py` near
  `:1165`, filtering `UpdateMessagePoll` and calling
  `bridge.poll_vote.translate_poll_vote(client, poll_id)`. It must never raise into Telethon's
  update loop.
- **Emit `poll_update_observed` at info from inside that handler on every `UpdateMessagePoll`.**
  This is how the un-gated push question gets answered in production rather than by a second client
  on the bridge's auth key. Document in the feature doc that if the signal never appears, the Raw
  handler is dead weight and should be deleted in a follow-up — a scope reduction, not a bug.
- **Emit the inbound-half operator signal (Risk 7).** During the same scan, any registry row at or
  past `POLL_EXPIRY_WARN_AGE_S` **with no `steered_at`** emits exactly one
  `logger.warning("poll_expired_unanswered ...")` with poll id, chat id, session id, and age, marked
  so it is not re-emitted. **Key on missing `steered_at`, never on a missing claim** — otherwise the
  signal is blind to the Risk 9 swallow. Also warn on consecutive `GetPollResultsRequest` failures.
- **Adopt orphaned provisional rows (Race 6), matched exactly — never on question text.** For each
  surviving `telegram:poll:pending:{poll_id_hint}` row, scan a bounded window of recent outbound
  history in that chat for a `MessageMediaPoll` whose `poll.answers[0].option` decodes (via
  `decode_option`, Task 4) to the same `poll_id_hint` and which has no `telegram:poll:{poll.id}`
  row. Write the real row from the provisional data plus the discovered `msg_id`/`poll.id`, then
  delete the provisional. **If more than one candidate matches, log a warning and adopt nothing** —
  an ambiguous adoption steers a session with someone else's answer. A provisional row that reaches
  TTL with no match is dropped with a warning.
- Every interval, TTL, and warn-age is a named env-overridable constant with a grain-of-salt comment.

### 10a. GATE — end to end through the shipped chain (bounded; UNRESOLVED permitted)
- **Task ID**: gate-poll-e2e
- **Depends On**: build-vote-observation
- **Validates**: manual probe; output pasted verbatim into the PR description
- **Informed By**: the cycle-4 and cycle-5 "nothing exercises the shipped pipeline" findings
- **Assigned To**: `poll-probe`
- **Agent Type**: builder
- **Parallel**: false
- **This is the plan's only end-to-end assertion, and it is scheduled here because here is the
  first point in the graph at which it can pass.** Task 2 originates through the real chain but
  cannot assert the steer: at Task 2 time nothing observes a vote. Only after Task 10 lands does a
  tap have an observer (`events.Raw` fast path and the reconciliation loop) and a translator
  (`translate_poll_vote`, Task 9).
- Originate **one** poll by invoking the shipped `valor-ask-poll` CLI from an eng session bound to
  the machine-owned eng group — not by a direct `send_poll` call — so the run exercises
  `tools/ask_poll.py` → `poll_eligible` → `build_telegram_poll_outbox_payload` (including the
  `poll_id_hint` mint) → `telegram:outbox:{session_id}` → `_send_queued_poll` → `send_poll` → the
  registry → the observer → `translate_poll_vote` → `push_steering_message` →
  `_drain_steering_boundary`.
- The session must be a **live eng session**, so the run takes the `LIVE` branch of
  `resolve_answer_target` — the `COMPLETED` and `NONE` branches are covered by unit tests, not here.
- Ask the operator for a tap with a caption naming exactly what is being asked and the deadline:
  *"End-to-end gate for #2701 — please tap either option on the poll above. If no tap arrives within
  30 minutes the build reports this gate unresolved."* Then surface a **legitimate open question**
  through the normal pause path, for the same reason Task 2 does: a bare "waiting for tap" status
  note is auto-continued past by the nudge loop.
- **Bounded wait:** at most `POLL_PROBE_TAP_WAIT_S` (the same named constant Task 2 uses, default
  1800 s), then report.
- **PASS** requires all four, in order: (a) the poll closes; (b) the registry row gains
  `dispatched` and then `steered_at`; (c) `push_steering_message` was called with a non-`None`
  `room_id` for that session; (d) **the chosen option text appears in that session's next turn
  input** — the steer merged by `runner.py:1290 _drain_steering_boundary()` (locate by symbol).
  (d) is the assertion this gate exists for; (a)-(c) localize a failure.
- Paste the observed steer text and the registry row verbatim into the PR description.
- **FAIL** (a tap arrives but no steer reaches the session) → **stop and report.** This is the one
  wiring break the rest of the plan cannot detect: everything else is a unit test with a stubbed
  handler or a grep, so a broken CLI→relay→observer→steering seam ships green across nine
  subsystems without this task.
- **On timeout → UNRESOLVED**, recorded on #2701 exactly as Task 2's timeout is. Unlike a FAIL,
  UNRESOLVED does not block Tasks 13a/13b/14/15; it is reported in the PR description as an
  unexercised end-to-end path so the reviewer decides, rather than the build deciding silently.
- Runs against the live bridge — that is the point — so it opens no second Telethon client and the
  `receive_updates=False` / temp-session-copy constraints that bind Tasks 1 and 2 do not apply here.
  Delete the probe poll and its question message when done.

### 11. Catchup transcript rendering
- **Task ID**: build-catchup-transcript
- **Depends On**: build-relay-dispatch
- **Validates**: `tests/unit/test_agent_catchup_poll_transcript.py` (create)
- **Informed By**: spike-4 (blank-line finding)
- **Assigned To**: `poll-surfaces-builder`
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/agent_catchup.py::read_thread` (text extraction at `:371`; locate by symbol), render a
  message whose media is `MessageMediaPoll` as its question text plus options rather than `""`, so
  `_render_transcript` (`:419`) no longer emits a bare `"Valor: "` and `sweep_chat`'s empty-text skip
  (`:560-562`) no longer drops it.
- Leave `_has_valor_reply_after` (`:436-452`) and `_valor_reacted` (`:321`) alone — spike-4 confirmed
  they already behave correctly for a text-less outbound message.
- Leave `bridge/catchup.py:333-337` alone; document in the feature doc that a vote is not a message
  and the reconciliation loop is the catchup story for polls.

### 12. `/ask-me` relaxation and skill-context
- **Task ID**: build-ask-me-skill
- **Depends On**: none
- **Validates**: manual read; `.claude/skill-context/README.md` table entry present
- **Informed By**: owner decisions 1, 2, 4, 5
- **Assigned To**: `poll-surfaces-builder`
- **Agent Type**: builder
- **Parallel**: true (with Tasks 1, 3, 4)
- In `.claude/skills-global/ask-me/SKILL.md` step 5: change "Ask one question at a time" from a hard
  rule to a stated preference and remove "Never batch your whole blocker list into one
  multi-question call", replacing it with guidance that separate questions are acceptable only when
  genuinely independent. **Keep** the "Questionnaire mode" anti-pattern, reworded to match.
- Add the skill-context probe sentence: "If `.claude/skill-context/ask-me.md` exists, read it and
  honor its declarations; otherwise use the generic defaults described below."
- Keep the global body generic — no `valor-ask-poll`, no Telegram specifics in it.
- Create `.claude/skill-context/ask-me.md` declaring: the `valor-ask-poll` invocation, the
  local-interactive vs headless-bridge branch, **the group-only + eng-only eligibility rule and that
  DMs and teammate sessions get prose**, recommended-option-first ordering, and the mandatory literal
  final option `Other: wait for followup message`.
- Add the row to the `.claude/skill-context/README.md` table.
- **Wire the role primes to reach `/ask-me` at all.** `grep` confirms zero `ask-me` references in
  `.claude/commands/roles/` or `config/personas/` today, so without this the feature only fires when
  an agent happens to invoke the skill. Add **one generic, surface-agnostic line** to each of
  `.claude/commands/roles/prime-dev-role.md`, `prime-pm-role.md`, and `prime-teammate-role.md` (the
  three primes `agent/session_runner/role_driver.py:65-72` dispatches).
- **The added line must be a conditional phrased against the existing pause threshold**, e.g. *"when
  you have a legitimate open question that only the human can answer (the same bar the auto-continue
  nudge loop uses), invoke `/ask-me` rather than posing it in prose."* A bare "when blocked, ask"
  introduces a second, looser definition of blocked across every session on every machine and can
  raise pause frequency for local, email, and non-blocked sessions. No mention of polls, Telegram, or
  `valor-ask-poll` in the primes — that is `.claude/skill-context/ask-me.md`'s job.
- Do **not** touch the `needs_human` / bare-`AskUserQuestion` turn-end path
  (`agent/session_runner/role_driver.py::_reconcile_turn_end`, `runner.py:1526-1531`). It keeps
  delivering text; that is a named No-Go.

**Task 13 is split along the gate boundary (cycle 6).** A single `build-tests` task depending on
`build-vote-observation` made an UNRESOLVED human-tap gate block tests, documentation and final
validation through the 13→14→15 chain — i.e. everything that makes the *outbound* half shippable —
which silently contradicted the "an unavailable operator does not stall the outbound half" property
the cycle-4 gate split was adopted to buy. The split restores it.

### 13a. Tests — outbound half
- **Task ID**: build-outbound-tests
- **Depends On**: build-catchup-transcript, build-ask-me-skill, build-ask-poll-cli
- **Validates**: `tests/unit/test_poll_gating.py`, `test_ask_poll_cli.py`, `test_poll_payload.py`,
  `test_poll_registry.py`, `test_agent_catchup_poll_transcript.py`, and the
  `tests/unit/test_bridge_relay.py` / `test_telegram_relay_chat_log.py` / `test_relay_job_record.py`
  / `test_send_message.py` / `test_medium_validators.py` updates
- **Informed By**: Test Impact and Failure Path Test Strategy sections
- **Assigned To**: `poll-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Depends on neither gate.** This task must remain runnable while `gate-poll-human-tap` is
  UNRESOLVED — that is the whole point of the split.
- Create `tests/unit/test_poll_gating.py`, `test_ask_poll_cli.py`, `test_poll_payload.py`,
  `test_poll_registry.py`, `test_agent_catchup_poll_transcript.py`.
- Apply every **Test Impact** UPDATE that does not concern vote translation, plus the health-payload
  test for `telegram:poll:reconcile:heartbeat`.
- Cover the **Eligibility Gate** and **Correlation Key** Failure-Path blocks in full, the
  `encode_option` / `decode_option` round-trip, the thread-offload assertion, the pinned
  provisional-row write (first statement of `_send_queued_poll`, and deleted on every decline
  branch), the plain-text terminal-failure re-enqueue, and Race 5 (the registry row survives a
  simulated restart). **Race 6's orphan *adoption* is Task 10 code and its tests belong to 13b** —
  13a covers only the write/promote/expire half of the provisional row.
- **No test may send a poll into a DM or otherwise re-probe the settled capability matrix.** DM
  behavior is asserted by testing that the CLI queues prose, not by hitting Telegram.
- Run with `scripts/pytest-clean.sh`, never bare `pytest`.

### 13b. Tests — inbound half
- **Task ID**: build-inbound-tests
- **Depends On**: build-vote-observation
- **Validates**: `tests/unit/test_poll_vote_translation.py` (create),
  `tests/integration/test_steering.py` (update), `tests/unit/test_steering_mechanism.py` (update if
  the extraction changes the call shape)
- **Informed By**: Test Impact and Failure Path Test Strategy sections
- **Assigned To**: `poll-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Paused alongside Tasks 9, 10 and 10a when `gate-poll-human-tap` is UNRESOLVED**, and resumed
  with them.
- Create `tests/unit/test_poll_vote_translation.py`; apply the `tests/integration/test_steering.py`
  updates listed in **Test Impact**.
- Cover the **Vote Selection Under Multiple Voters** and **Claim Durability** Failure-Path blocks in
  full (including both `dispatched`-marker bullets), the `room_id` keyword assertions added in
  cycle 5, every `resolve_answer_target` branch (`LIVE`, `PENDING`, `LIVE_GUARD`, `COMPLETED`,
  `NONE`), the `poll_expired_unanswered` warning emitted exactly once, Races 2 and 3 (double
  translation, completed-session re-enqueue), and **Race 6's orphan adoption** in
  `tests/unit/test_poll_registry.py` — the two-candidates-different-ids case adopts the right poll,
  the two-candidates-same-id case adopts nothing and warns.
- Run with `scripts/pytest-clean.sh`, never bare `pytest`.

### 14. Documentation
- **Task ID**: document-feature
- **Depends On**: build-outbound-tests, build-inbound-tests
- **Validates**: the Documentation section checklist
- **Assigned To**: `poll-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Complete every item in the **Documentation** section.
- **Pause semantics (the property the 13a/13b split protects).** When the inbound set is paused on
  an UNRESOLVED `gate-poll-human-tap`, `build-inbound-tests` counts as *satisfied-by-pause* for
  scheduling: this task documents the shipped outbound behavior and the inbound design as
  specified, and Task 15 follows immediately. Nothing sits idle waiting on the operator. The
  pause is recorded, not papered over — Task 15 reports the inbound rows UNRESOLVED.

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: the Verification table
- **Assigned To**: `poll-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the **Verification** table.
- Confirm every **Success Criteria** box, including that the Task 1, Task 2 **and Task 10a** gate
  outputs are in the PR description.
- **With the inbound set paused on an UNRESOLVED gate**, report the inbound Verification rows and
  Success Criteria as **UNRESOLVED**, never as passed, and say so in the PR description. An
  UNRESOLVED report is a reviewable state; a green report over an unexercised inbound half is not.
- Report pass/fail/unresolved per row.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Poll type registered in relay | `grep -c '"poll"' bridge/telegram_relay.py` | output > 0 |
| CLI entry point declared | `grep -c 'valor-ask-poll' pyproject.toml` | output > 0 |
| Raw handler registered | `grep -c 'events.Raw' bridge/telegram_bridge.py` | output > 0 |
| Mandatory final option present | `grep -rc 'Other: wait for followup message' tools/ask_poll.py` | output > 0 |
| Skill-context file exists | `test -f .claude/skill-context/ask-me.md` | exit code 0 |
| Skill probe sentence present | `grep -c 'skill-context/ask-me.md' .claude/skills-global/ask-me/SKILL.md` | output > 0 |
| Hard one-at-a-time prohibition removed | `grep -c 'Never batch your whole blocker list' .claude/skills-global/ask-me/SKILL.md` | match count == 0 |
| No blocking primitive introduced (anti-criterion) | `grep -rn 'await.*wait_for_vote\|block_until_answer\|poll_answer_future' tools/ bridge/ agent/ \| wc -l` | match count == 0 |
| No Popoto migration added (anti-criterion) | `git diff main --stat -- scripts/update/migrations.py \| wc -l` | match count == 0 |
| No raw steering-key write (anti-criterion) | `grep -rnE '(rpush\|lpush\|set)\([^)]*steering:' bridge/ --include='*.py' \| grep -v push_steering_message \| wc -l` | match count == 0 |
| Poll not treated as ephemeral on dead-letter (anti-criterion) | `grep -n 'if msg_type in (' bridge/telegram_relay.py \| grep -c 'poll'` | match count == 0 |
| Feature doc exists | `test -f docs/features/telegram-poll-questions.md` | exit code 0 |
| Feature doc indexed | `grep -c 'telegram-poll-questions' docs/features/README.md` | output > 0 |
| Poll expiry warning wired (inbound-half signal) | `grep -rn 'poll_expired_unanswered' bridge/ --include='*.py' \| wc -l` | output > 0 |
| Answer-routing seam extracted | `test -f bridge/answer_routing.py && grep -c 'def resolve_answer_target\|def resume_completed_session' bridge/answer_routing.py` | output == 2 |
| Completed branch reachable from the vote path (anti-criterion) | `grep -lc 'resume_completed_session' bridge/telegram_bridge.py bridge/answer_routing.py \| wc -l` | output == 2 |
| Eligibility gate exists | `test -f bridge/poll_gating.py && grep -c 'def poll_eligible' bridge/poll_gating.py` | output == 1 |
| Eligibility read at both points | `grep -lc 'poll_eligible' tools/ask_poll.py bridge/telegram_relay.py \| wc -l` | output == 2 |
| One `is_group_chat` definition, in read-the-room | `grep -rn 'def is_group_chat\|def _is_group_chat' bridge/ \| wc -l` | output == 1 |
| The one definition is the public name in `read_the_room.py` | `grep -c 'def is_group_chat' bridge/read_the_room.py` | output == 1 |
| Poll gating imports the generic predicate, not the reverse | `grep -c 'read_the_room import' bridge/poll_gating.py` | output > 0 |
| Read-the-room does not import from the poll module (anti-criterion) | `grep -c 'poll_gating' bridge/read_the_room.py` | match count == 0 |
| Eng-only gate compares the enum, not a literal (anti-criterion) | `grep -c "session_type == .eng." bridge/poll_gating.py` | match count == 0 |
| Claim released on failure (Risk 9) | `grep -rn 'release_poll_claim' bridge/ --include='*.py' \| wc -l` | output > 0 |
| Steered marker separate from the claim (Risk 9) | `grep -rn 'steered_at' bridge/ --include='*.py' \| wc -l` | output > 0 |
| Push question answerable in production, not by a second client | `grep -c 'poll_update_observed' bridge/telegram_bridge.py` | output > 0 |
| Public drafter seam used, not `draft_message` | `grep -lc 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py \| wc -l` | output == 2 |
| Correlation key has exactly one producer | `grep -c 'uuid4().hex' agent/output_handler.py` | output > 0 |
| Correlation key threaded to the relay | `grep -lc 'poll_id_hint' agent/output_handler.py bridge/telegram_relay.py bridge/poll_registry.py \| wc -l` | output == 3 |
| Eligibility re-check is thread-offloaded (anti-criterion) | `grep -n 'poll_eligible(' bridge/telegram_relay.py \| grep -v import \| grep -vc 'to_thread'` | match count == 0 |
| Eligibility re-check is thread-offloaded (positive form) | `grep -c 'to_thread(poll_eligible' bridge/telegram_relay.py` | output > 0 |
| Dispatch marker distinct from the steered marker | `grep -rn 'mark_poll_dispatched' bridge/ --include='*.py' \| wc -l` | output > 0 |
| Claim release is guarded by the dispatch marker | `grep -B4 'release_poll_claim' bridge/poll_vote.py \| grep -c 'dispatched'` | output > 0 |
| Vote translator has its own module, not `answer_routing` | `test -f bridge/poll_vote.py && grep -c 'def translate_poll_vote' bridge/poll_vote.py` | output == 1 |
| Translator stays out of the poll-independent seam (anti-criterion) | `grep -c 'def translate_poll_vote' bridge/answer_routing.py` | match count == 0 |
| Registry helpers have the grepped home | `test -f bridge/poll_registry.py && grep -c 'def register_pending_poll\|def release_poll_claim\|def iter_unanswered_polls' bridge/poll_registry.py` | output == 3 |
| Provisional row precedes the eligibility re-check (ordering is behavioral, so it is a test, not a grep) | `scripts/pytest-clean.sh tests/unit/test_bridge_relay.py -k provisional -q` | exit code 0 |
| Reconciliation heartbeat written | `grep -rc 'poll:reconcile:heartbeat' bridge/telegram_bridge.py` | output > 0 |
| Heartbeat read outside the loop | `grep -rln 'poll:reconcile:heartbeat' --include='*.py' . \| wc -l` | output >= 2 |
| No expectation authored on the poll path (anti-criterion) | `grep -rn 'expectation' agent/output_handler.py \| grep -ci poll` | match count == 0 |
| Poll path never calls `draft_message` (anti-criterion) | `grep -n 'draft_message' agent/output_handler.py \| grep -ci poll` | match count == 0 |
| Text fallback re-enqueue exists (not dead-letter-only) | `grep -c '_relay_attempts' bridge/telegram_relay.py` | output > 0 (and the poll terminal branch rpushes a `type: None` payload — assert by test, not grep) |
| Role primes reach `/ask-me` | `grep -lc 'ask-me' .claude/commands/roles/prime-dev-role.md .claude/commands/roles/prime-pm-role.md .claude/commands/roles/prime-teammate-role.md \| wc -l` | output == 3 |
| Role primes stay surface-agnostic (anti-criterion) | `grep -rn 'valor-ask-poll\|Telegram poll' .claude/commands/roles/prime-dev-role.md .claude/commands/roles/prime-pm-role.md .claude/commands/roles/prime-teammate-role.md \| wc -l` | output == 0 |
| Prime line is a conditional, not a bare directive | `grep -rn 'legitimate open question' .claude/commands/roles/prime-dev-role.md .claude/commands/roles/prime-pm-role.md .claude/commands/roles/prime-teammate-role.md \| wc -l` | output == 3 |

**Note on the steering anti-criterion.** The grep is deliberately narrowed to *key construction*
(`rpush`/`lpush`/`set` applied to a `steering:` literal). The bare-token form returns **2** on a
clean `main` — `bridge/message_drafter.py` contains the log string
`"requesting self-draft via steering: %s"` twice, which is unrelated prose. **Do not "fix" that by
editing those log lines.** Verified: the narrowed grep returns `0` on the verification baseline with a clean tree.

## Critique Results

**Column semantics:** *Implementation Note* is the critic's suggestion at the time the finding was
raised; *Addressed By* records what was actually adopted and supersedes it where the two differ.

**Critique cycle 7, 2026-09-02.** Run against the cycle-6 revision with the repo at `b2e15a9a0`
(clean tree). War room depth: FULL (`appetite: Large` plus the `.claude/skills-global/` doctrine
path). Roster gate: 3/3 complete, all grounded. Findings: **7 total (0 blockers, 4 concerns,
3 nits)**. Structural checks re-run on this tree: all four required sections present and
substantive; the 17-task graph (1, 2, 3-10, 10a, 11, 12, 13a, 13b, 14, 15) has no numbering gaps;
every `Depends On` resolves to a real Task ID with no cycles; every task carries `Validates`; all 4
prerequisites PASS (`scripts/check_prerequisites.py`); every cited symbol resolves
(`push_steering_message` at `agent/steering.py:176`, `room_id_for_session` at `models/room.py:98`,
`KNOWN_MESSAGE_TYPES` at `bridge/telegram_relay.py:58`, `_validate_for_medium` at
`bridge/message_drafter.py:560`, `MessageDraft` at `:193`, `set_reaction` at `bridge/response.py:433`,
`session_id = Field()` at `models/agent_session.py:157`, `session_type = KeyField(null=True)` at
`:158`, `_drain_steering_boundary` at `agent/session_runner/runner.py:1307`, zero `events.Raw`
handlers repo-wide); the only cited paths that do not exist are the seven modules and six test files
the plan intentionally creates. Every Verification anti-criterion re-run on the clean tree returns
its stated value **except** the "Prime line is a conditional" row, which is the subject of a CONCERN
below. Two findings (`gate-poll-e2e` as a graph leaf; the reconciliation loop's unnamed home)
are recurrences of failure classes cycle 6 named and fixed elsewhere in the document.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | `iter_unanswered_polls()` / `iter_pending_polls()` are specified over plain Redis string keys with **no index**, so the only enumeration available is `SCAN MATCH telegram:poll:*`, which walks the entire shared production keyspace on every reconciliation tick — at the fast interval, for the first minutes after every poll. Risk 6's mitigation ("the reconciliation loop iterates only registry entries, never all chats") is about which *chats* are touched, not the scan cost. Spike-5's precedent does not cover it: `bridge/job_router.py:85-96` and `bridge/context.py:491` (`_get_cached_root`, a bare `r.get(key)`) only ever **point-look-up** their keys and are never enumerated. This plan is the first to require enumeration and inherits a non-Popoto posture chosen for a lookup workload. | pending | Add `POLL_OPEN_INDEX = "telegram:poll:open"` and `POLL_PENDING_INDEX = "telegram:poll:pending:index"` (Redis SETs) to `bridge/poll_registry.py`. `register_poll(...)` `SADD`s the poll id in the same call that writes the row; `register_pending_poll` `SADD`s the hint; `mark_poll_steered`, the provisional-row delete on every decline branch, and the TTL-expiry drop all `SREM`. `iter_unanswered_polls()` `SSCAN`s the index and `GET`s each row, skipping (and `SREM`ing) ids whose row has expired — the index is a hint, the row stays authoritative, so a lost `SREM` costs one wasted `GET` rather than a missed poll. Do **not** use `scan_iter(match="telegram:poll:*")`: Redis SCAN filters server-side but still walks every key in the db, and this db is shared with production Popoto keys. |
| CONCERN | Risk & Robustness | The Verification row *Prime line is a conditional, not a bare directive* (`grep -rn 'legitimate open question' <the three primes> \| wc -l`, expected `3`) is **deterministically red on a correct build**. `grep -rn` counts lines, not files, and `.claude/commands/roles/prime-pm-role.md:89` already contains that exact phrase on a clean tree ("...when you have a legitimate open question for the user."). Verified: the row returns **1** today, so a correct Task 12 build adding one line per prime returns **4**. This is the same shape cycle 6 raised against the thread-offload anti-criterion ("A deterministically-red gate invites the builder to weaken or delete it"), reintroduced by a row cycle 6 itself added. | pending | Rewrite the row to `grep -lc 'legitimate open question' .claude/commands/roles/prime-dev-role.md .claude/commands/roles/prime-pm-role.md .claude/commands/roles/prime-teammate-role.md \| wc -l` expecting `3` — `-l` collapses each file to at most one output line, absorbing the pre-existing `prime-pm-role.md:89` sentence instead of double-counting it. Do **not** "fix" this by editing or deleting that existing line; it is unrelated `## Open Questions` routing guidance. |
| CONCERN | Risk & Robustness | Task 10 never names a home module for the reconciliation loop (only "started alongside `relay_loop`"), yet the Verification row `grep -rc 'poll:reconcile:heartbeat' bridge/telegram_bridge.py` expecting `> 0` silently pins the heartbeat write — and therefore the loop body — into `bridge/telegram_bridge.py`. The seven-path creation list contains no reconcile module, so a builder who houses the loop in its own module (the natural choice given how the rest of the feature is modularized) fails a Verification row on a correct build. This is verbatim the drift class cycle 6 raised for `bridge/poll_registry.py` ("a module named nowhere else in the plan"), left unfixed for the loop. Task 10's cited start site `:3231-3233` has also drifted: the relay task is created at `bridge/telegram_bridge.py:3406-3408` on `b2e15a9a0`. | pending | State in Task 10 that the loop lives in a new `bridge/poll_reconcile.py` exporting `poll_reconcile_loop(client)`, imported and started in `bridge/telegram_bridge.py` next to `from bridge.telegram_relay import relay_loop` / `asyncio.create_task(relay_loop(client))` (locate by symbol — now `:3406-3408`), and repoint the row to `grep -c 'poll:reconcile:heartbeat' bridge/poll_reconcile.py` expecting `> 0`. Keep the `events.Raw` handler in `bridge/telegram_bridge.py` (the `poll_update_observed` row greps that file correctly). Update the intentionally-created path list from seven to eight in the same edit — that list has gone stale in three consecutive cycles. |
| CONCERN | Scope & Value | Task 10a (`gate-poll-e2e`) is a **dependency-graph leaf**: no task lists it in `Depends On` (Task 14 depends on `build-outbound-tests, build-inbound-tests`; Task 15 on `document-feature`). The plan's self-described "only end-to-end assertion that the nine subsystems are actually wired together" can therefore be skipped and the graph still runs to completion — the only thing holding it is Task 15's prose. Cycle 6's own doctrine for this exact problem was "The dependency graph, not the prose, is now the thing that makes the claim true", applied to the 13a/13b split but not to the task that split produced. | pending | Add `gate-poll-e2e` to **Task 15's** `Depends On` (`document-feature, gate-poll-e2e`), not Task 14's — Task 14 does not consume the gate result and adding it there would re-block documentation on an operator tap, the property the 13a/13b split was bought to protect. Extend Task 15's existing *satisfied-by-pause* rule verbatim to `gate-poll-e2e`: with the inbound set paused or the gate UNRESOLVED, Task 15 runs and reports the E2E row **UNRESOLVED**, never passed. Add `gate-poll-e2e` to every place the inbound set is enumerated as `9, 10, 10a, 13b` so the pause semantics and the dependency edge agree. |
| NIT | Scope & Value | `build_telegram_poll_outbox_payload(chat_id, question, options, reply_to, session_id)` takes a `reply_to` argument and Task 8 never says where `tools/ask_poll.py` obtains it. Task 8 specifies reuse of `_resolve_transport()` (`tools/send_message.py:63`) only, but the sibling path also reads the reply target from the environment (`tools/send_message.py:187`: `reply_to = os.environ.get("TELEGRAM_REPLY_TO")`). A poll sent with `reply_to=None` still delivers but lands unthreaded and gives `_bind_outbound_message_to_job` nothing to bind — which the plan separately requires a test for. | pending | In `tools/ask_poll.py`, mirror `tools/send_message.py:187` exactly — `reply_to = os.environ.get("TELEGRAM_REPLY_TO")`, coerced with the same `int(reply_to) if reply_to else None` idiom at `tools/send_message.py:123` — and thread it into `build_telegram_poll_outbox_payload(..., reply_to=reply_to, ...)`. Do not introduce a `--reply-to` flag as the primary source; the env var is how a headless session already learns its reply target, and a flag lets an agent pass a stale id. |
| NIT | History & Consistency | The Freshness Check claims the drifted offsets were "corrected in place" and explicitly lists `_dead_letter_message` `:803`→`:946` and `process_outbox` `:850`→`:1018`, but the corrections were never propagated: Data Flow step 5 still says `process_outbox` (`:850`), and `_dead_letter_message` is still `:803` in the Technical Approach, in Risk 5 (twice), and in Task 7. Verified on `b2e15a9a0`: `_dead_letter_message` is at `bridge/telegram_relay.py:946`, `process_outbox` at `:1018`, and the terminal-failure branch cited as `:1029-1034` is at `:1196-1206`. The locate-by-symbol convention contains the damage, but the Freshness Check is the one place a builder is entitled to trust an offset. | pending | Either propagate the corrected offsets to their remaining sites (`:803`→`:946` in the Technical Approach fallback bullet, Risk 5 items 1 and 2, and Task 7's dead-letter bullet; `:850`→`:1018` in Data Flow step 5; `:1029-1034`→`~:1196-1206` in the Technical Approach, Risk 5 and Task 7), **or** soften the Freshness Check sentence to "representative corrections are recorded in this list; individual offsets elsewhere are hints only, per the locate-by-symbol convention." Do not leave the two halves contradicting each other, which is the state today. |
| NIT | History & Consistency | The Test Impact row for `bridge/read_the_room.py` hedges "UPDATE **only if** renaming `_is_group_chat` ... changes a name a test patches". It does, certainly: `tests/unit/test_read_the_room.py:40` imports the private name directly and `:153` asserts on it (`assert _is_group_chat(chat_id) is expected`). There is also a second in-module call site to repoint, `bridge/read_the_room.py:526`. An "only if" invites a builder to check nothing and skip, landing a red suite on an otherwise correct Task 3. | pending | Make the Test Impact row an unconditional UPDATE naming `tests/unit/test_read_the_room.py` line 40 (import) and line 153 (assertion), and add a Task 3 bullet noting the in-module call site at `bridge/read_the_room.py:526`. Do **not** leave a `_is_group_chat = is_group_chat` back-compat alias — the Verification row `grep -rn 'def is_group_chat\|def _is_group_chat' bridge/ \| wc -l` expecting `1` counts `def`s only and would pass with an alias, silently keeping two names alive, which is the second-copy drift Task 3 exists to prevent. |

**Critique cycle 6, 2026-09-02.** Run against the cycle-5 revision (plan text at `3bbefa49c`) with the
repo at `3bbefa49c` (working tree carries only an unrelated `docs/plans/nightly-autonomous-fix-before-alert.md`
edit). War room depth: FULL (`appetite: Large` plus the `.claude/skills-global/` doctrine path). Roster
gate: 3/3 complete, all grounded. Findings: **8 total (1 blocker, 4 concerns, 3 nits)**. Structural
checks re-run on this tree: all four required sections present and substantive; Tasks 1-15 with no
numbering gaps; every `Depends On` resolves to a real Task ID with no cycles; every task carries
`Validates`; all 4 prerequisites PASS (`scripts/check_prerequisites.py`); every cited symbol resolves
(`push_steering_message` carries `room_id` at `agent/steering.py:183`, `room_id_for_session` at
`models/room.py:98`, `session_id = Field()` at `models/agent_session.py:157`, `KNOWN_MESSAGE_TYPES` at
`bridge/telegram_relay.py:58`, zero `events.Raw` handlers repo-wide); the only cited paths that do not
exist are the modules and test files the plan intentionally creates.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Task 2's end-to-end assertion is unsatisfiable at its scheduled position. Cycle 5 moved the E2E check into Task 2 ("after the tap, the chosen option text appears in that session's next turn input"), but Task 2 depends only on Tasks 1, 7 and 8, while the entire inbound half that produces a steer — `translate_poll_vote` (Task 9, which itself `Depends On: gate-poll-human-tap`) and the `events.Raw` handler plus reconciliation loop (Task 10) — is built strictly after it. At Task 2 time a tap produces an `updateMessagePoll` no code observes, so the criterion the plan calls "the only assertion that the nine subsystems are actually wired together" can never pass; the build either stalls on an impossible gate or ships green by dropping it. | **ADOPTED (cycle 6).** New **Task 10a `gate-poll-e2e`**, `Depends On: build-vote-observation` — CLI-originated poll from a **live** eng session, operator tap, and a four-part PASS whose load-bearing clause is "the chosen option text appears in that session's next turn input" (`runner.py:1290 _drain_steering_boundary()`). Task 2 keeps its `valor-ask-poll` origination and its `Depends On`, loses the steer assertion, and now says in-task **why** it cannot carry it. Task 9's `Depends On: gate-poll-human-tap` is untouched, as instructed. The "End-to-end, once, through the shipped chain" Success Criterion is repointed at Task 10a; the Gate-topology note, the Key Elements list, Appetite/Interactions and Task 15's gate-output check all record three gates and **two genuinely distinct operator taps** (one before an observer exists, one after) — the collapse into one is what made the cycle-5 criterion unsatisfiable. | Add Task 10a `gate-poll-e2e` with `Depends On: build-vote-observation`: originate one poll via the shipped `valor-ask-poll` CLI from an eng session bound to the machine-owned eng group, have the operator tap, and assert the chosen option text appears in that session's next turn input (the steer merged by `runner.py:1290 _drain_steering_boundary()`). Leave Task 2 as the readback/attribution gate for Task 9 with its CLI origination intact, and repoint the "End-to-end, once, through the shipped chain" Success Criterion at the new task. Do **not** instead relax Task 9's `Depends On: gate-poll-human-tap` — that would build the inbound half against an unverified readback. |
| CONCERN | History & Consistency | Four Verification rows still use the unevaluable multi-file `grep -c` shape that cycle 5 twice recorded as eliminated ("This was the last per-file `grep -c` row"; "Every Verification anti-criterion uses the scalar `\| wc -l` shape"): *Poll expiry warning wired* (`grep -c 'poll_expired_unanswered' bridge/*.py`), *Claim released on failure*, *Steered marker separate from the claim*, and *Dispatch marker distinct from the steered marker*. Verified on this tree: `grep -c 'release_poll_claim' bridge/*.py` emits **42** `path:count` lines, so "output > 0" has no scalar to compare and every one of these rows passes vacuously. Three of the four guard the Risk 9 mitigation — the plan's own named "silently, permanently blocked agent" failure. | **ADOPTED (cycle 6).** All four rewritten to `grep -rn '<token>' bridge/ --include='*.py' \| wc -l` expecting `> 0`. The cycle-5 NIT *Addressed By* cell no longer claims it fixed the last per-file row (amended in place to name what it actually fixed and to record that four survived), and the structural-checks paragraph's closing sentence is rewritten to state the audited property — every row is scalar-comparable — rather than to assert a completed sweep. Ten rows added or changed in this cycle were checked against the same shape. | Rewrite all four to a scalar shape, e.g. `grep -rl 'release_poll_claim' bridge/ --include='*.py' \| wc -l` expecting `> 0` (or `grep -rn '<token>' bridge/ --include='*.py' \| wc -l`). Then amend the cycle-5 NIT *Addressed By* cell and the closing sentence of "Structural checks (current — cycle 5)" so neither still claims the last per-file row was fixed. |
| CONCERN | History & Consistency | The Verification row *Correlation key threaded to the relay* greps `bridge/poll_registry.py`, a module named nowhere else in the plan: Task 6 ("Poll registry") never names a file for `register_poll` / `claim_poll_answer` / `iter_unanswered_polls`, and the cycle-5 structural audit enumerates exactly five intentionally-created modules with `bridge/poll_registry.py` absent. A builder who houses the registry helpers elsewhere fails a Verification row on a correct build, and the audit rewritten in cycle 5 specifically to stop file lists going stale is already incomplete. | **ADOPTED (cycle 6).** Task 6 now opens by creating `bridge/poll_registry.py` and enumerating all ten helpers it holds, naming it as the module the Verification table greps. The intentionally-created module list is corrected in the same edit — it is now **seven**, not five: `tools/ask_poll.py`, `bridge/answer_routing.py`, `bridge/poll_gating.py`, `bridge/poll_registry.py`, `bridge/poll_vote.py`, `.claude/skill-context/ask-me.md`, `docs/features/telegram-poll-questions.md`. Architectural Impact gains matching module bullets and the feature doc gets a module-map item, so the file list has three consistent statements rather than one authoritative one. | In Task 6, open with "Create `bridge/poll_registry.py` holding the registry key constants and `register_poll(...)`, `lookup_poll(poll_id)`, `claim_poll_answer(poll_id)`, `release_poll_claim(poll_id)`, `mark_poll_dispatched(poll_id)`, `mark_poll_steered(poll_id)`, `register_pending_poll(...)`, `iter_pending_polls()`, `promote_pending_poll(...)`, `iter_unanswered_polls()`", and change the "five modules" sentence in the structural-checks paragraph to six. Fix the audit in the same edit as the Verification row — this is the same drift class the cycle-4 stale-audit finding raised. |
| CONCERN | Risk & Robustness | The Verification anti-criterion *Eligibility re-check is thread-offloaded* (`grep -n 'poll_eligible' bridge/telegram_relay.py \| grep -vc 'to_thread'`, expected 0) fails on a correct build: any implementation carries an import line (`from bridge.poll_gating import poll_eligible`) that matches `poll_eligible` and contains no `to_thread`. The local idiom the plan itself cites confirms the shape — `bridge/telegram_relay.py:184` imports `set_reaction` on a bare line inside the function. A deterministically-red gate invites the builder to weaken or delete it, losing the event-loop protection cycle 5 added. | **ADOPTED (cycle 6), both forms.** The anti-criterion is anchored on the call site — `grep -n 'poll_eligible(' bridge/telegram_relay.py \| grep -v import \| grep -vc 'to_thread'` expecting `0` — and a second **positive** row was added, `grep -c 'to_thread(poll_eligible' bridge/telegram_relay.py` expecting `> 0`, so the protection is asserted rather than only un-violated. The `(` is what separates a call from the import line the local idiom (`bridge/telegram_relay.py:184`) produces. | Anchor the grep on the call rather than the symbol: `grep -n 'poll_eligible(' bridge/telegram_relay.py \| grep -v import \| grep -vc 'to_thread'` expecting `0`, or positively `grep -c 'to_thread(poll_eligible' bridge/telegram_relay.py` expecting `> 0`. The `(` is what separates a call site from the import; `grep -vc` on stdin is already scalar. |
| CONCERN | Risk & Robustness | `translate_poll_vote` has no declared home module. Task 9's heading places it under the `bridge/answer_routing.py` task and the Verification row `grep -B4 'release_poll_claim' bridge/answer_routing.py` assumes it lands there — but Task 9a's entire justification for that module is that it is a **poll-independent** seam shared with the reply-to ladder, landed as its own commit so `git revert <9a-sha>` is real. Housing the poll translator inside it re-fuses what 9a split apart; housing it anywhere else makes the anti-criterion silently unevaluable. | **ADOPTED (cycle 6).** Task 9b now states that `translate_poll_vote` lives in a new `bridge/poll_vote.py` importing `resolve_answer_target` / `resume_completed_session` from `bridge/answer_routing.py` and the helpers from `bridge/poll_registry.py`, with the standalone-revert reason restated in-task. The dispatch-marker Verification row is repointed at `bridge/poll_vote.py`, and two rows are added: the translator has exactly one `def` in its own module, and **zero** in `answer_routing.py` (anti-criterion). Key Elements, Data Flow step 4, Task 10's Raw-handler bullet, Architectural Impact and the Team Orchestration role all name the new home. | State in Task 9b that `translate_poll_vote` lives in a new `bridge/poll_vote.py` which imports `resolve_answer_target` / `resume_completed_session` from `bridge/answer_routing.py`, and repoint the Verification row to `grep -B4 'release_poll_claim' bridge/poll_vote.py \| grep -c 'dispatched'` expecting `> 0`. Keeping the translator out of `answer_routing.py` is what preserves the standalone-revert property the separate 9a commit was bought for. |
| CONCERN | Scope & Value | The plan states three times that an UNRESOLVED human-tap gate "pauses **Tasks 9-10 only**. Every other task proceeds" and that "an unavailable operator does not stall the outbound half". The dependency graph contradicts this: Task 13 (`build-tests`) depends on `build-vote-observation` (Task 10), Task 14 on Task 13, and Task 15 on Task 14 — so UNRESOLVED blocks tests, documentation and final validation, i.e. everything that makes the outbound half shippable. The operator-availability property the cycle-4 gate split was adopted to buy does not hold. | **ADOPTED (cycle 6), with one named deviation.** Task 13 is split exactly as suggested: **13a `build-outbound-tests`** (`Depends On: build-catchup-transcript, build-ask-me-skill, build-ask-poll-cli`, gate-free) and **13b `build-inbound-tests`** (`Depends On: build-vote-observation`, paused with Tasks 9/10/10a). **Deviation:** pointing Task 14 at both, as the note suggests, would still leave docs and final validation blocked by an UNRESOLVED gate — the very property being restored. Task 14 keeps both dependencies but carries an explicit *satisfied-by-pause* rule: with the inbound set paused, Tasks 14 and 15 run anyway and Task 15 reports the inbound Verification rows and Success Criteria **UNRESOLVED**, never green. The Gate-topology note, Appetite/Interactions, Task 2's timeout clause and a new Success Criterion all state the inbound set as the four tasks 9/10/10a/13b, so the claim is now checkable on the graph rather than asserted in prose. | Split Task 13 rather than re-wording the claim: Task 13a `build-outbound-tests` (`Depends On: build-catchup-transcript, build-ask-me-skill, build-ask-poll-cli`) covering `test_poll_gating.py`, `test_ask_poll_cli.py`, `test_poll_payload.py`, `test_poll_registry.py`, the `test_bridge_relay.py` updates and `test_agent_catchup_poll_transcript.py`; Task 13b `build-inbound-tests` (`Depends On: build-vote-observation`) covering `test_poll_vote_translation.py` and the `tests/integration/test_steering.py` updates. Point Task 14 at both and mark 13b as paused alongside Tasks 9-10 on UNRESOLVED. |
| NIT | Risk & Robustness | Race 6 covers the window between `send_poll` returning and the registry write, but not the earlier window between `process_outbox`'s `r.lpop` (which atomically consumes the work item) and `register_pending_poll`. A restart there loses the question with no provisional row at all, so orphan adoption has nothing to adopt and `poll_expired_unanswered` cannot fire — a silent total loss, worse than the case Race 6 documents. Unlike Race 6 nothing forces the ordering: `poll_id_hint` is on the payload and knowable immediately after the LPOP. | **ADOPTED (cycle 6).** `register_pending_poll(...)` is pinned as the **first statement** of `_send_queued_poll`, ahead of the eligibility re-check and every await; the eligibility bullet is re-worded from "at the top" to "immediately after that write". Added beyond the note, because pinning it earlier creates a new case: every branch that then declines to send (missing `poll_id_hint`, ineligible, terminal failure) **deletes** the provisional row, so a decline never ages into a spurious orphan-adoption candidate. Race 6 records the uncovered LPOP window and why it is worse than the one it already documents; a Success Criterion, two Task 13a test bullets and a Verification row (behavioral, so a test rather than a grep) cover it. | Pin `register_pending_poll(...)` as the first statement of `_send_queued_poll`, before the eligibility re-check and any other await. |
| NIT | Scope & Value | `is_group_chat` — a generic Telegram peer-type predicate whose existing consumer is `bridge/read_the_room.py` — is relocated into a feature-specific `bridge/poll_gating.py`, so read-the-room ends up importing a general predicate from a poll module. The one-definition goal is right; the destination inverts the dependency arrow and is the kind of naming inversion that later invites a second copy. | **ADOPTED (cycle 6), second option.** `is_group_chat` **stays in `bridge/read_the_room.py`**, promoted in place from the private `_is_group_chat` to a public name, and `bridge/poll_gating.py` imports it. One definition, natural dependency direction, no rename of the generic module. Task 3, the Technical Approach clause, Architectural Impact, the Failure-Path bullet, Test Impact, the Documentation item and the Success Criterion all follow. Verification rows: the single `def` now lives in `read_the_room.py`; `poll_gating.py` imports it; and a new anti-criterion asserts `read_the_room.py` contains **no** `poll_gating` reference, so the inversion cannot be reintroduced. | Either name the new module for what it generically holds, or leave `is_group_chat` in `bridge/read_the_room.py` and have `bridge/poll_gating.py` import it — same one-definition property, natural dependency direction. |

**Revision cycle 6 (applied 2026-09-02).** All 8 cycle-6 findings are dispositioned above — zero rows
in this document carry `pending`. Three changes alter the shape of the plan rather than adding detail:

1. **The end-to-end assertion moved to a task where it can pass.** Cycle 5 put it on Task 2, which
   runs before anything in the tree observes a vote. It is now **Task 10a `gate-poll-e2e`**, after
   `build-vote-observation`. The honest cost is a second operator tap; the alternative was a
   criterion that could only stall the build or be quietly dropped.
2. **The operator-availability property is now a graph property.** Task 13 split into 13a (outbound,
   gate-free) and 13b (inbound, paused with the gate), and Tasks 14/15 carry an explicit
   satisfied-by-pause rule with UNRESOLVED reporting. The plan previously claimed in three places
   that an unavailable operator did not stall the outbound half while its own `Depends On` chain
   said otherwise.
3. **Every module the Verification table greps is now created by a task.** `bridge/poll_registry.py`
   is named in Task 6, `translate_poll_vote` gets its own `bridge/poll_vote.py` outside the
   poll-independent `answer_routing` seam, and `is_group_chat` stays in `bridge/read_the_room.py`
   with `poll_gating` importing it. The intentionally-created list is seven paths, stated in three
   consistent places.

**Revision cycle 5 (applied 2026-09-02).** All 8 cycle-5 findings and all 5 cycle-4 findings are
dispositioned — zero rows in this document carry `pending`. The two structural changes worth calling
out on their own, because they change the shape of the plan rather than adding detail to it:

1. **`poll_id_hint` has a named producer.** The Race-6 mitigation was keyed on an identifier with
   seven consumers and no origin. It is now minted in exactly one function
   (`build_telegram_poll_outbox_payload`, Task 5) and threaded through the payload; a missing hint at
   the relay is an error-logged fallback to prose, and a `correlation_id=None` send is a warning.
2. **Task 2 became the pipeline's only end-to-end assertion.** It previously called `send_poll`
   directly, touching none of the nine subsystems this plan wires together. It now originates its
   probe poll through the shipped `valor-ask-poll` CLI and asserts the tapped option reaches the
   session's next turn — using the human tap the plan was already waiting for, so the operator cost
   is unchanged and only the poll's origin moved.

**Critique cycle 5, 2026-09-02.** Run against the cycle-5 plan text (`ccb527c56`) with the repo at
`b87fb26de` (working tree carries only unrelated `pyproject.toml`/`uv.lock` edits). War room depth:
FULL (3 critics — `appetite: Large` plus the `.claude/skills-global/` doctrine path). Roster gate:
3/3 complete, all grounded. Findings: **8 total (2 blockers, 5 concerns, 1 nit)** — five of them are
the cycle-4 rows below, which the cycle-5 revision left `pending` and which are re-raised here rather
than re-numbered. Structural checks re-run on this tree: all four required sections present and
substantive; Tasks 1-15 with no numbering gaps; every `Depends On` resolves to a real Task ID with no
cycles; every task carries `Validates`; all 4 prerequisites PASS; every cited symbol resolves; zero
`events.Raw` handlers repo-wide (as claimed); the only cited paths that do not exist are the five
modules and six test files the plan intentionally creates.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The whole Race-6 mitigation is keyed on an identifier the plan never defines, generates, or carries. Task 5's payload schema is stated verbatim as `{"type": "poll", chat_id, reply_to, question, options, session_id, timestamp}` with **no id field**; Task 4's `send_poll(..., correlation_id=None)` receives one but no task mints it; Task 7 calls `register_pending_poll(...)` with nothing to key it on. `grep -n outbox_payload_id` over the plan returns seven consumers and zero producers, so a builder takes Task 4's `else` branch (`f"{index}".encode()`) and silently drops Race 6 — which the plan itself calls "*permanent* loss: the human taps and nothing can ever route the vote." | **ADOPTED (cycle 5).** `poll_id_hint` is now minted in exactly one place — `build_telegram_poll_outbox_payload` (Task 5), `uuid.uuid4().hex`, stamped unconditionally — and every consumer reads it from the payload. Task 7 threads it to both `register_pending_poll` and `send_poll(correlation_id=...)` and falls through to plain text when it is missing. Task 4's bare `else` branch is labelled probe-only and now emits `poll_sent_without_correlation_id` at warning. The 100-byte `PollAnswer.option` ceiling and the do-not-substitute-a-digest rule are stated in Task 5. Covered by a new **Correlation Key** Failure-Path block, two Success Criteria, and two Verification rows. | Add `"poll_id_hint": uuid.uuid4().hex` (32 chars) to the dict `build_telegram_poll_outbox_payload` emits in `agent/output_handler.py`, and rename every `outbox_payload_id` reference in the plan to that key. `_send_queued_poll` reads `message["poll_id_hint"]` and passes it as both the `register_pending_poll` key and `send_poll(correlation_id=...)`. `f"{index}:{hex32}"` is at most 35 bytes, inside Telegram's 100-byte `PollAnswer.option` limit; a longer id (a payload digest) is not. |
| BLOCKER | History & Consistency | All five cycle-4 findings still carry `pending` and none is addressed in the plan text. The plan's git history shows why: `4f15e8294` recorded the cycle-4 findings and the only later plan commit is `ccb527c56` ("re-baseline at `5021a40aa`, pass `room_id` on the vote steer") — a freshness pass, not a disposition pass. Verified by grep: `heartbeat`, `dispatched`, and `end to end` each appear exactly once, inside the finding rows themselves. Every prior cycle closed with `ADOPTED (cycle N)`; cycle 4 alone did not, so the plan is heading to BUILD with a recorded correctness gap and two robustness concerns unanswered. | **ADOPTED (cycle 5).** All five cycle-4 rows are dispositioned below and each has plan text behind it: (1) the `dispatched` marker is in Task 6 (`mark_poll_dispatched`), Task 9b, Risk 9, and the Claim Durability block; (2) `telegram:poll:reconcile:heartbeat` is scoped into Task 10, Risk 7, **and** Architectural Impact under a new *Observability* bullet, with two Verification rows; (3) Task 2 now originates its probe poll through the real `valor-ask-poll` chain and asserts the option text reaches the session's next turn, plus a matching Success Criterion; (4) the stale cycle-3 structural paragraph is deleted and replaced with a cycle-5 audit; (5) the multi-file `grep -c` row is rewritten to the scalar `\| wc -l` form. Historical *Implementation Note* cells left untouched. | Close the five cycle-4 rows: (1) Risk-9 `dispatched` marker into Task 9b + the Claim Durability test block; (2) reconciliation-loop heartbeat `telegram:poll:reconcile:heartbeat` (`SET EX <2x slow interval>` at the top of each tick) plus one external read site, scoped into Task 10 **and** Architectural Impact, where it currently appears in neither; (3) end-to-end origination through `valor-ask-poll`, folded into Task 2; (4) delete or replace the stale cycle-3 structural paragraph; (5) fix the multi-file `grep -c` Verification row. Do **not** rewrite historical *Implementation Note* cells — the column-semantics line already makes *Addressed By* authoritative. |
| CONCERN | Risk & Robustness | `poll_eligible(chat_id, session_id)` is a synchronous predicate called "at the top of `_send_queued_poll`", an async relay function, and its second clause runs `AgentSession.query.filter(session_id=session_id)` — but `session_id` is a plain `Field()` (`models/agent_session.py:157`), not a `KeyField`, so that is an unindexed scan. This plan quotes the cost itself: "`session_id` is unindexed and a lookup there would cost ~2.4s on the inbound fast path." Every existing relay call site of that shape is thread-offloaded (`await asyncio.to_thread(_record_sent_message, ...)` at `bridge/telegram_relay.py:1132`; the `_append_outbound_chat_log` comment at `:921` says it "runs in a thread … and cannot await"). Research finding 7 cites those sites as proof the gate is cheap but does not carry their threading requirement, so an inline call stalls the bridge event loop for seconds per poll send. | **ADOPTED (cycle 5).** Task 7 now mandates `eligibility = await asyncio.to_thread(poll_eligible, chat_id, session_id)`, citing the `bridge/telegram_relay.py:1132` precedent and the `:921` cannot-await comment. `poll_eligible` stays synchronous so `tools/ask_poll.py` calls it directly off the loop (Task 3 unchanged). Failure Path adds a thread-identity assertion under **Eligibility Gate** — written as a correctness test on `threading.current_thread()`, deliberately not a duration assertion — plus a Verification anti-criterion and a Success Criterion. | In `_send_queued_poll`: `eligibility = await asyncio.to_thread(poll_eligible, chat_id, session_id)`, mirroring `bridge/telegram_relay.py:1132`. Keep `poll_eligible` itself synchronous (Task 3 unchanged) so `tools/ask_poll.py`, which runs off the event loop, calls it directly. Add a Failure Path assertion that the relay branch never calls `poll_eligible` on the loop thread. |
| CONCERN | Risk & Robustness | Task 9b releases the claim on **any** exception after `claim_poll_answer`, including one raised after `push_steering_message` / `resume_completed_session` already succeeded (`mark_poll_steered` is explicitly last and can throw). The next reconcile tick re-runs the whole translation, and the stated second guard cannot cover it: `bridge/dedup.py:168 CLAIM_TTL_SECONDS` is deliberately short (its own comment: "keep `CLAIM_TTL_SECONDS` short. A long TTL here was a BLOCKER in an earlier critique round") and the slow reconcile interval outlives it. On the `COMPLETED` branch that double-enqueues a session from one vote. (Re-raised from cycle 4.) | **ADOPTED (cycle 5).** Task 6 gains `mark_poll_dispatched(poll_id)` and a `dispatched` field distinct from `steered_at`; Task 9b writes it immediately after the steer/re-enqueue returns and before anything else that can throw, and the `except` handler releases the claim only when `dispatched` is absent — with `dispatched` present the retry re-attempts `mark_poll_steered` only. Risk 9's mitigation is restated as three changes, naming the double-enqueue as the mirror-image failure of a blanket release. Two new Claim Durability bullets assert `push_steering_message` / `dispatch_telegram_session` are each called exactly once. | Write a `dispatched` marker onto the `telegram:poll:{poll_id}` row immediately after the steer/dispatch returns and **before** anything else that can throw; the `except Exception` handler calls `release_poll_claim(poll_id)` only when `dispatched` is absent. With `dispatched` present the retry re-attempts the `mark_poll_steered` write **only**. Add the Claim Durability bullet: an exception from `mark_poll_steered` after a successful steer neither releases the claim nor re-dispatches. |
| CONCERN | Scope & Value | Nothing in Success Criteria or Task 15 exercises the shipped pipeline end to end. Tasks 1-2 call `send_poll` / `GetPollResultsRequest` directly against a throwaway probe poll and never touch `tools/ask_poll.py`, `poll_eligible`, the payload builder, the outbox, or the registry; everything else is a unit test with a stubbed handler, and the Verification table is greps. A wiring break at the CLI→relay seam this plan adds across nine subsystems ships green. (Re-raised from cycle 4.) | **ADOPTED (cycle 5).** Task 2 no longer calls `send_poll` directly: it originates its probe poll by invoking the shipped `valor-ask-poll` CLI from an eng session in the machine-owned eng group, and after the tap asserts the chosen option text appears in that session's next turn input. Its `Depends On` gains `build-ask-poll-cli` and `build-relay-dispatch` (no cycle — neither transitively reaches `gate-poll-human-tap`), and the Gate-topology note records the re-sequencing. One new Success Criterion. Operator cost unchanged: the same tap the plan already waited for. | Change Task 2 so its probe poll is originated by invoking the real `valor-ask-poll` CLI from an eng session bound to the machine-owned eng group rather than by a direct `send_poll` call, then assert after the tap that the chosen option text appears in that session's next turn input. Add one Success Criterion to that effect. Zero extra operator cost — Task 2 already waits `POLL_PROBE_TAP_WAIT_S`; only the poll's origin changes. |
| CONCERN | Scope & Value | Task 9a restructures the reply-to steering ladder — the primary inbound path for every typed Telegram reply on every machine — and repoints the live handler at it, which the plan concedes is "a restructure, not a verbatim lift". Architectural Impact nonetheless claims "**Reversibility**: high. Removing the dispatch branch, the Raw handler, the reconciliation loop and the CLI reverts to today's prose behavior." That is false for Task 9a: reverting the poll feature does not revert the ladder restructure, and in one PR a post-merge regression on ordinary typed replies has no narrow revert. | **ADOPTED (cycle 5).** Task 9a now carries a mandatory standalone-commit clause: only `bridge/answer_routing.py` and `bridge/telegram_bridge.py`, green on the three named `tests/integration/test_steering.py` cases, landed **before** 9b. Architectural Impact's Reversibility bullet is amended to scope the high-reversibility claim to the poll path and to state explicitly that the `answer_routing` extraction is not covered by it and is separately revertible. A Success Criterion and the Commit-sequencing note under Step by Step Tasks make it checkable. | Land Task 9a as a standalone commit touching only `bridge/answer_routing.py` and `bridge/telegram_bridge.py`, green on `tests/integration/test_steering.py::test_steering_push_only_after_session_match`, `::test_pending_session_within_window_receives_steering`, and `::test_reply_to_completed_session_reenqueues_with_context`, **before** Task 9b adds `translate_poll_vote` — so `git revert <9a>` is real. Amend the Architectural Impact bullet to say reversibility is high for the poll path and that the `answer_routing` extraction is a separate, independently-revertible commit. |
| CONCERN | History & Consistency | Data Flow step 4 and Task 5 both require `send_poll` to "record the outstanding-question expectation", and that is the plan's only mention of it — no API, no model, no `Validates` coverage, no Test Impact row, no Success Criterion, no Documentation item. Its sibling `send` (`agent/output_handler.py:611`) records no expectation, so this is a new responsibility on a shipped subsystem (`tools/job_tool expectation-add`, `metrics:expectations_authored`, `bridge/promise_gate.py`). Worse, Task 9b's branch table never resolves it, so as specified every poll authors an expectation that is never closed. | **ADOPTED (cycle 5) — STRUCK, not implemented.** The clause is deleted from both of its only two sites in the same edit: Data Flow step 4 and Task 5. Both now say explicitly that `send_poll` records no expectation, matching its sibling `send` (`agent/output_handler.py:611`), because an obligation authored with no resolution in any of Task 9b's four steering branches is worse than none. A Verification anti-criterion (`grep -rn 'expectation' agent/output_handler.py \| grep -ci poll` == 0) keeps a builder from half-implementing it. | If kept: name the exact call in Task 5 (`tools/job_tool expectation-add`, the entry point `metrics:expectations_authored` counts — see `agent/output_handler.py:1322`), state the expectation key so it is resolvable, add the matching resolution to all four steering branches in Task 9b, and add a `test_poll_vote_translation.py` bullet asserting it is closed exactly once. If struck: delete the clause from Data Flow step 4 and Task 5 in the same edit so no builder half-implements it. |
| NIT | History & Consistency + structural check | The Verification row *"Public drafter seam used, not `draft_message`"* runs `grep -c 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py` expecting "every file > 0". Multi-file `grep -c` prints one `path:count` line per file, so there is no scalar to compare — the same unevaluable shape cycle 1 raised as a BLOCKER and cycle 3 fixed for two other rows. (Re-raised from cycle 4.) | **ADOPTED (cycle 5); the claim in this cell was wrong and is corrected here (cycle 6).** The row itself was rewritten to `grep -lc 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py \| wc -l` expecting `2`, and that fix stands. It was **not** the last per-file row: four `grep -c '<token>' bridge/*.py` rows survived and cycle 6 raised them as a CONCERN. All four are now scalar (`grep -rn ... --include='*.py' \| wc -l`). | Rewrite to the scalar form the other rows use: `grep -lc 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py \| wc -l` with expected `2`. |

**Critique cycle 4, 2026-08-10.** Run against the re-scoped plan at baseline `95aba8187` with a clean
tree (plan text at `2fac262e7`). War room depth: FULL (3 critics — `appetite: Large` plus the
`.claude/skills-global/` doctrine path). Roster gate: 3/3 complete, all grounded. Findings: **5 total
(0 blockers, 4 concerns, 1 nit)**. Structural checks re-run on the current 15-task graph: all four
required sections present and substantive; Tasks 1-15 with no numbering gaps; every `Depends On`
resolves to a real Task ID with no cycles; every task carries a `Validates` field; all 4 prerequisites
PASS; every cited symbol resolves (`_completed_created_at` is at `bridge/telegram_bridge.py:1899`, one
line off the cited `:1898` — within the plan's stated approximate-offset convention); the only cited
paths that do not exist are the five modules and six test files the plan intentionally creates. Every
Verification anti-criterion re-run on a clean tree returns its stated value.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The Risk 9 fix deletes the `claim_poll_answer` key on **any** exception after the claim, including one thrown *after* `push_steering_message` / `resume_completed_session` already succeeded (e.g. the `mark_poll_steered` write throws). The next reconciliation tick then re-dispatches, and the plan's stated second guard — `claim_message` inside `dispatch_telegram_session` — has a short TTL, so on the `COMPLETED` branch a retry landing after that TTL double-enqueues the session from one vote. | **ADOPTED (cycle 5)** — see the identical cycle-5 row above. `mark_poll_dispatched` (Task 6) plus the `dispatched`-guarded release in Task 9b. | Split the failure window: after `push_steering_message` / `resume_completed_session` returns, write a `dispatched` marker onto the `telegram:poll:{poll_id}` row **before** anything that can throw, and have the `except` handler release the claim only when `dispatched` is absent. When `dispatched` is present the retry path must re-attempt the `steered_at` write **only**, never the steer/dispatch. `claim_message`'s TTL (`bridge/dedup.py:168 CLAIM_TTL_SECONDS`, ~60s default) is shorter than the slow reconcile interval, so it cannot be relied on as the second claim in this window. |
| CONCERN | Risk & Robustness | Risk 7 names "the reconciliation loop dies" as an invisible-failure mode, but its only mitigation (`poll_expired_unanswered`) is emitted **from inside that same loop's** `iter_unanswered_polls()` scan. If the loop is what died, the signal cannot fire, so the plan has no detector external to the loop's own liveness. | **ADOPTED (cycle 5).** `telegram:poll:reconcile:heartbeat` (`SET EX POLL_RECONCILE_HEARTBEAT_TTL_S`, default 2x the slow interval) is written at the top of every tick in Task 10 and read at one existing health surface; Risk 7 gains a *second, external mitigation* paragraph and Architectural Impact gains an *Observability* bullet. Two Verification rows and a Success Criterion. No new service. | Add a loop heartbeat that is read outside the loop: write `telegram:poll:reconcile:heartbeat` with `SET EX <2x the slow interval>` at the top of every tick (named env-overridable constant, grain-of-salt comment), and surface its absence on an existing external read site — the dashboard health payload or the bridge health check — rather than adding a new service. This is one new Redis key plus one read site; it is currently scoped in neither Task 10 nor Architectural Impact. |
| CONCERN | Scope & Value | Nothing in Success Criteria or Task 15 exercises the shipped pipeline end to end. Tasks 1-2 call `send_poll` / `GetPollResultsRequest` directly against a throwaway probe poll and never touch `tools/ask_poll.py`, `poll_eligible`, the payload builder, the outbox, or the registry; everything else is a unit test with a stubbed handler. A wiring break between the CLI and the relay would ship green. | **ADOPTED (cycle 5)** — see the identical cycle-5 row above. Task 2 re-sequenced after Task 8 and re-originated through `valor-ask-poll`. | Add one Success Criterion plus a Task 15 step that originates a poll through the real chain once: invoke `valor-ask-poll` (or `/ask-me`) from an eng session bound to the machine-owned eng group, have a human tap, and assert the chosen option text appears in that session's next turn input. Reuse Task 2's operator window rather than adding a second human gate — Task 2 already waits `POLL_PROBE_TAP_WAIT_S` for a tap; making that tap land on a **CLI-originated** poll converts the existing gate into the E2E check at zero extra operator cost. |
| CONCERN | History & Consistency + structural check | The `### Structural checks (cycle 3, re-run on ecd5d1972)` paragraph was never re-run after the cycle-4 re-scope and is now false: it claims "Tasks 1-11, no numbering gaps" while the task list runs 1-15, and its "four files the plan intentionally creates" list omits `bridge/poll_gating.py`, a module cycle 4 added. A reader trusting it believes the post-re-scope task graph was verified when it was not. | **ADOPTED (cycle 5).** The cycle-3 paragraph is deleted; a *Structural checks (current — cycle 5)* audit replaces it, recording Tasks 1-15, the five intentionally-created modules including `bridge/poll_gating.py`, the six new test files, and the cycle-5 `Depends On` change to Task 2 with its no-cycle check. A one-line note records that the cycle-3 audit was removed rather than left to contradict it. | Replace that paragraph with the cycle-4 structural paragraph recorded directly above this table (Tasks 1-15, five intentionally-created modules — `tools/ask_poll.py`, `bridge/answer_routing.py`, `bridge/poll_gating.py`, `.claude/skill-context/ask-me.md`, `docs/features/telegram-poll-questions.md` — plus six new `tests/unit/` files), or delete it outright and label it as a closed cycle-3 artifact. Do not leave two structural audits in the document disagreeing about the task count. |
| NIT | Structural check | The Verification row *"Public drafter seam used, not `draft_message`"* runs `grep -c 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py` with expected value "every file > 0". Multi-file `grep -c` prints one `path:count` line per file, so there is no scalar to compare — the same unevaluable shape a cycle-3 NIT fixed for two other rows. | **ADOPTED (cycle 5)** — rewritten to `grep -lc ... \| wc -l` expecting `2`. Last per-file row in the table. | Rewrite to the scalar form the other rows now use: `grep -lc 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py \| wc -l` with expected `2`. |

**Revision cycle 4, 2026-08-10 — the re-scope revision.** Triggered not by a critique but by the
Task 1 gate FAIL and the owner's scope decision (issue comments `5236653597`, `5237014662`). The
dead DM premise was replaced with group-only + eng-only; the DM-capability gate was deleted and
replaced with the vote-readback gates; Risk 1 was retired as a fired risk and restated as a
constraint; the eligibility gate (`bridge/poll_gating.py`) was added along with the multi-voter
selection rule and Risk 9. **All nine cycle-3 findings were dispositioned in the same pass** — see
the *Addressed By* column. Work that did not depend on chat type (spikes 2-5, the outbox/relay seam,
catchup rendering, dedup/idempotency, the drafter split, the `answer_routing` extraction) was kept
deliberately, not rewritten.

**Critique cycle 3, 2026-08-10.** Verified against `ecd5d1972` with a clean tree. Cycles 1 and 2 are
closed: all 9 cycle-1 findings and all 7 cycle-2 findings were dispositioned and their fixes re-verified
on this tree (the `bridge/answer_routing.py` split, the plain-text re-enqueue, the public
`validate_poll_question` seam, the correlation-id option encoding, the bounded Part B wait, and the role-
prime wiring are all present in the plan text). Cycle 3 raises no blockers.

War room depth: FULL (3 critics — `appetite: Large` plus doctrine paths `.claude/skills-global/`).
Findings: 9 total (0 blockers, 5 concerns, 4 nits). Roster gate: 3/3 complete, all grounded.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Task 5a claims `resolve_answer_target` is "lifted verbatim" from `bridge/telegram_bridge.py:1799-1866` and that behavior preservation is "trivially checkable because it has no side effects". That range is not a state read: it interleaves the `AgentSession.query.filter` ladder with `await _ack_steering_routed(...)` + `return` at every branch, computes `age = _pending_session_age_seconds(pending_session.created_at, time.time())` (`:1834`) only to build the PENDING log string, and picks the completed record via a local most-recent-`created_at` sort (`_completed_created_at`, `:1898`) rather than `completed_sessions[0]`. As written the extraction is a restructure, and the claim that it is checkable is not earned. | ADOPTED (cycle 4) — Task 9a now states the extraction is a **restructure, not a verbatim lift**, and carries the four-item behavior-preservation checklist verbatim: `matched_status`, `pending_age_s`, the most-recent-`created_at` COMPLETED pick, and `_steering_session_enqueued` staying caller-side. `AnswerTarget` widened to 4 fields in the Technical Approach and in Task 9a. | `AnswerTarget` must be `(kind, session, matched_status: str \| None, pending_age_s: float \| None)` — `matched_status` because the LIVE log embeds `matching_session.status` and LIVE_GUARD embeds `live_guard.status`; `pending_age_s` because the PENDING log embeds `age=%.1f`. COMPLETED must return the record chosen by the existing most-recent-`created_at` sort, not `completed_sessions[0]` — the wrong record silently degrades `_build_completed_resume_text`'s `context_summary`. The handler sets `_steering_session_enqueued = True` after dispatch (`:2006`); `resume_completed_session` returns `None`, so that flag stays caller-side. Restate Task 5a as a restructure with these four items as its behavior-preservation checklist. |
| CONCERN | Risk & Robustness | `translate_poll_vote` takes the one-shot claim (`SET telegram:poll:answered:{poll_id} 1 NX EX`) before closing the poll and before `push_steering_message` / `resume_completed_session`. If the bridge dies, `EditMessageRequest` raises, or the steering write throws after the claim, the claim survives its TTL and both recovery mechanisms are defeated: `iter_unanswered_polls()` skips a claimed row so the reconciliation loop never retries, and Risk 7's `poll_expired_unanswered` signal — defined as a row with no matching claim — never fires. The result is the invisible permanently-blocked agent this feature exists to prevent, and no Race in the plan covers it. | ADOPTED (cycle 4) — promoted to **Risk 9** with its own Failure Path test block (**Claim Durability**). `release_poll_claim` + `try/except` around everything after the claim (Task 9b), a separate `steered_at` completion marker written by `mark_poll_steered` (Task 6), `iter_unanswered_polls()` re-yielding claim-without-`steered_at`, and `poll_expired_unanswered` keying on missing `steered_at` (Task 10). | Wrap everything after `claim_poll_answer(poll_id)` in `try/except Exception` and, in the handler, delete the claim key before logging so the next reconciliation tick retries. Separately, record completion apart from the claim: write `steered_at` onto the `telegram:poll:{poll_id}` row only after `push_steering_message` / `resume_completed_session` returns, and have `iter_unanswered_polls()` treat "claim present, no `steered_at`, claim older than one reconcile interval" as still unanswered. `poll_expired_unanswered` must key on missing `steered_at`, not on the missing claim, or the operator signal is blind to exactly this state. |
| CONCERN | Scope & Value | Every task depends transitively on the single Task ID `gate-real-dm-poll`, which now bundles two independent capability questions: Part A (can this account send a poll into a real user DM) and Part B (can a human tap be read back). Part B's UNRESOLVED outcome says "do not start Tasks 2-11", so an unavailable operator blocks Tasks 2, 3, 4, 7 and 8 — the whole outbound path, catchup rendering, and skill work — none of which consume the tap result. | ADOPTED (cycle 4), and superseded in scope by the re-scope. The gate is split into `gate-poll-vote-readback` (Task 1, automated self-vote, no human) and `gate-poll-human-tap` (Task 2). **Both gate only Tasks 9-10.** Tasks 3-8 and 11-12 depend on neither, so an unavailable operator pauses two tasks instead of eleven. The old real-DM send gate is deleted outright — spike-6 answered it FAIL. | Split into `gate-real-dm-poll-send` (Part A) and `gate-real-dm-poll-tap` (Part B, `Depends On: gate-real-dm-poll-send`). Repoint Tasks 2, 3, 4, 7, 8 at `gate-real-dm-poll-send`; only Tasks 5 and 6 (`build-vote-translation`, `build-vote-observation`) depend on `gate-real-dm-poll-tap`. UNRESOLVED then pauses two inbound tasks instead of eleven, while a Part A FAIL still stops everything. Task 9's `Depends On: build-vote-observation` keeps tests correctly gated behind whichever half built. |
| CONCERN | Scope & Value | Task 8 adds a "when blocked on a judgment call only the human can make, invoke `/ask-me`" line to all three role primes, which govern every eng and teammate session on every machine and surface — not just headless Telegram sessions posing poll-able questions. It introduces a second, looser definition of "blocked" alongside the auto-continue doctrine's "legitimate open question" bar, and the only validation is a grep that the string is present; nothing checks that pause frequency did not rise for local, email, or non-blocked sessions. | ADOPTED (cycle 4) — the required phrasing is now spelled out in the Technical Approach and in Task 12 (*"a legitimate open question that only the human can answer (the same bar the auto-continue nudge loop uses)"*), with two new Success Criteria (the line is a conditional; non-Telegram surfaces still degrade to text) and a Verification row asserting the precondition string is present in all three primes. | Phrase the added prime line against the existing threshold verbatim: "when you have a legitimate open question that only the human can answer (the same bar the auto-continue nudge loop uses), invoke `/ask-me` rather than posing it in prose" — so the primes cannot be read as licensing a pause on a status update the nudge loop would auto-continue past. Add one Success Criterion asserting the line is a conditional (it must contain the open-question precondition, not a bare directive) and one asserting non-Telegram sessions still degrade to text through the CLI. |
| CONCERN | History & Consistency | In the previous `## Critique Results` table, cycle 2's BLOCKER row was marked RESOLVED with the adopted design in "Addressed By", but its "Implementation Note" cell still spelled out the rejected design in imperative voice (`route_answer_to_session(session_id, text, sender, *, chat_id, source_msg_id=None, ...)`, `is_duplicate_message(chat_id, source_msg_id)`). The Implementation Note column is the implementable guidance a builder reads, while Task 5 deletes `route_answer_to_session` and states `is_duplicate_message` is not called on the vote path — a builder reading the table instead of Task 5 builds the rejected design. | ADOPTED (cycle 3) — the column-semantics line is present directly under this heading and is retained. Historical Implementation Notes are left intact on purpose; the audit trail is the point. | Add one line directly under the `## Critique Results` heading, before the table: "**Column semantics:** *Implementation Note* is the critic's original suggestion at the time the finding was raised; *Addressed By* records what was actually adopted and supersedes it where the two differ." Cheaper and less lossy than editing historical notes, and it protects future cycles. Do not silently rewrite an Implementation Note cell to match its disposition — that destroys the audit trail the table exists to keep. |
| NIT | Risk & Robustness | Task 5b's selection rule is "Select the option with `voters >= 1` (the sender never votes)", stated as if it always identifies exactly one option. In any chat with more than one voter — which the plan permits ("In a group, translate the first vote and move on") — several options can each carry `voters >= 1`, and `PollResults` gives no ordering, so "the first vote" is not derivable from the aggregate a user account can see. | ADOPTED and PROMOTED (cycle 4) — no longer a nit. The re-scope makes groups the *only* surface, so multi-voter ambiguity is the normal case. The deterministic rule (filter `voters >= 1`; exactly one → use; several → warn, highest `voters`, tie-break lowest decoded index; zero → return without claiming) is stated in the Technical Approach, in Task 9b, and in a dedicated **Vote Selection Under Multiple Voters** test block. | State the rule in Task 5b: filter `results.results` to `voters >= 1`; exactly one → use it; more than one → log a warning naming the poll id and the tied options and steer with the highest-`voters` option, breaking ties by lowest decoded option index, so the behavior is deterministic and greppable. |
| NIT | Scope & Value | Task 3 bundles ten distinct edits across five files under one `Validates` field and one assignee: payload builder, `send_poll` handler method, drafter medium, `validate_poll_question`, `KNOWN_MESSAGE_TYPES`, relay dispatch branch, both dead-letter guards, the terminal-failure text re-enqueue, the Race-6 provisional row, post-send bookkeeping, the CLI, and the `pyproject.toml` entry. A partial completion is indistinguishable from a complete one. | ADOPTED (cycle 4) — old Task 3 split three ways along the seam the critic named: Task 5 `build-outbound-payload`, Task 7 `build-relay-dispatch`, Task 8 `build-ask-poll-cli`, each with its own `Validates`. The Race-6 provisional-row write is owned solely by Task 6 (`build-poll-registry`) and only *called* from Task 7. | Split along the seam the plan already draws: `build-outbound-payload` (payload builder + `send_poll` handler method + `validate_poll_question` + drafter medium; validated by `test_poll_payload.py`), `build-relay-dispatch` (`KNOWN_MESSAGE_TYPES`, dispatch branch, the `:827`/`:836` dead-letter guards, the `:1029-1034` re-enqueue, post-send bookkeeping; validated by `test_bridge_relay.py`), and `build-ask-poll-cli` (`tools/ask_poll.py`, option validation, `pyproject.toml`; validated by `test_ask_poll_cli.py`). The provisional-row write belongs to Task 4, not duplicated across two tasks. |
| NIT | History & Consistency | The Verification row "Role primes stay surface-agnostic (anti-criterion)" runs `grep -c '...' .claude/commands/roles/prime-*-role.md` expecting "match count == 0", but with multiple file arguments `grep -c` prints one `path:count` line per file. Verified on a clean tree it emits three lines, so Task 11's validator has no scalar to compare — the same class of unevaluable anti-criterion that was cycle 1's BLOCKER for the steering grep. | ADOPTED (cycle 3/4) — both rows rewritten to `grep -rn ... \| wc -l` scalar form, and every Verification row added in cycle 4 uses the same scalar shape. | Replace with `grep -rn 'valor-ask-poll\\|Telegram poll' .claude/commands/roles/prime-dev-role.md .claude/commands/roles/prime-pm-role.md .claude/commands/roles/prime-teammate-role.md \| wc -l` expecting `0`. Apply the same treatment to the "Completed branch reachable from the vote path" row, whose "every file > 0" is likewise per-file: `grep -lc 'resume_completed_session' bridge/telegram_bridge.py bridge/answer_routing.py \| wc -l` expecting `2`. |
| NIT | History & Consistency | The document cites three different baselines for its own verification: the Freshness Check says "Baseline commit: `e051e95da`", the Technical Approach says symbols were "re-verified against `5d9729ad8`", and the previous Critique Results said "Verified against `9de184a3e`". Since the plan is line-number-approximate by its own admission, a builder cannot tell which tree the cited offsets belong to. | ADOPTED (cycle 4) — the Freshness Check now carries a single `**Verification baseline:** 95aba8187` line that explicitly supersedes any other sha mentioned in this document's history. | Set a single `**Verification baseline:**` line in the Freshness Check to the current plan-revision commit and change the other three mentions to "the verification baseline above". Symbol names themselves were re-confirmed present on `ecd5d1972`. |

### Structural checks (current — cycle 7)

**Cycle 7 delta (re-run on `b2e15a9a0`, clean tree).** The cycle-6 audit below still holds on every
point it asserts — 17 tasks, no numbering gaps, no dependency cycles, every task carries
`Validates`, all 4 prerequisites PASS, every cited symbol resolves — with **three corrections**.
(1) The intentionally-created path list is **eight**, not seven: the reconciliation loop needs a
declared home (`bridge/poll_reconcile.py`), which cycle 7's third CONCERN records. (2) The cycle-6
claim that "every row now yields a single comparable value" is true of the shape but not of the
*value*: the *Prime line is a conditional* row is deterministically red on a correct build because
`grep -rn` counts lines and `.claude/commands/roles/prime-pm-role.md:89` already carries the
phrase — cycle 7's second CONCERN. (3) `gate-poll-e2e` resolves and introduces no cycle, but no
task depends on it, so it is a graph leaf rather than a scheduled step — cycle 7's fourth finding.
Everything below is retained as the cycle-6 audit it was, amended only by these three items.

Superseded audits: the cycle-3 paragraph that used to sit here described an 11-task graph and a
four-file creation list, both invalidated by the cycle-4 re-scope; it was deleted in cycle 5. The
cycle-5 audit it replaced is itself superseded by this one — its five-module creation list and its
"every Verification row is scalar" closing claim were both incomplete, which is what cycle 6's second
and third CONCERNs recorded. One audit at a time, always the current one.

All four required sections (Documentation, Update System, Agent Integration, Test Impact) present and
substantive. **Task graph: 1, 2, 3-10, 10a, 11, 12, 13a, 13b, 14, 15 — 17 tasks, no numbering gaps**
(`10a` and the `13a`/`13b` split are cycle-6 additions; `9a`/`9b` remain sub-parts of one task, as
before). Every `Depends On` resolves to a real Task ID and there are no cycles: `gate-poll-e2e`
depends only on `build-vote-observation`; `build-outbound-tests` depends on
`build-catchup-transcript` / `build-ask-me-skill` / `build-ask-poll-cli`, none of which transitively
reaches either human gate; `build-inbound-tests` depends on `build-vote-observation`; `Task 14`
depends on both test tasks; Task 2's cycle-5 `build-ask-poll-cli` / `build-relay-dispatch`
dependencies still do not transitively reach `gate-poll-human-tap`. Every task carries a
`**Validates**` field, including the three tasks added or split in cycle 6. All 4 prerequisites PASS
(`scripts/check_prerequisites.py`).

Every cited file exists except the **seven** paths the plan intentionally creates —
`tools/ask_poll.py`, `bridge/answer_routing.py`, `bridge/poll_gating.py`,
`bridge/poll_registry.py`, `bridge/poll_vote.py`, `.claude/skill-context/ask-me.md`,
`docs/features/telegram-poll-questions.md` — plus the six new `tests/unit/` files. Every cited symbol
re-verified present at its named location (offsets are approximate by the plan's stated convention;
locate by symbol). No Popoto model changes, so the no-migration claim holds.

**Verification-table shape, re-audited row by row in cycle 6:** every row now yields a single
comparable value — either a single-file `grep -c`, a `\| wc -l`, a `test -f`, or a command whose exit
code is the assertion. The four surviving multi-file `grep -c '<token>' bridge/*.py` rows (which
emitted one `path:count` line per file and therefore passed vacuously) are rewritten to
`grep -rn '<token>' bridge/ --include='*.py' \| wc -l`. The thread-offload anti-criterion is anchored
on `poll_eligible(` with `grep -v import` so it is not red on a correct build, and is paired with a
positive form. One ordering property (`register_pending_poll` precedes the eligibility re-check) is
behavioral rather than textual and is asserted by a test, not a grep — deliberately, rather than
forcing it into a grep that could not evaluate it.

---

## Open Questions

**None blocking.** The three questions this feature would normally raise — blocking vs
non-blocking, the escape-hatch wording, and the one-at-a-time rule — are **already settled by
owner decision** and are recorded in the Technical Approach. They are not reopened.

The **surface** question that broke the original plan is also settled: group chats only, eng sessions
only, DMs and teammate sessions keep prose, no bot identity, no reactions. Recorded in **Scope** at
the top of this document, sourced from issue comment `5237014662`, and **not reopened by critique**.

Two judgment calls remain, and each has a chosen default recorded below so the build can proceed
without waiting on a human. They are listed for the critique stage to challenge, not as a gate:

1. **Poll registry TTL.** How long should an unanswered question stay live and reconcilable? A
   short TTL (hours) keeps the registry tiny but abandons a question the human gets to the next
   morning; a long one (7 days, matching `session_root:`) tolerates a slow human but keeps
   reconciling against sessions that are long gone. Under the assumption that an `/ask-me` question
   is answered same-day or not at all, the plan defaults to **24 hours** with the slow
   reconciliation interval, but this is a judgment call about how patient the system should be.

2. **Auto-rendering a prose question at turn end.** Polls are wired to `/ask-me` only, and this
   revision closes the *reachability* half of the gap: Task 12 adds one line to each of the three
   role primes so a blocked headless session is directed to `/ask-me` rather than posing a judgment
   call in prose. What remains genuinely open is whether a later pass should teach the turn-end
   delivery path itself (the `needs_human` edge at `runner.py:1526-1531`) to *detect* a
   question-with-options in free prose and render it as a poll without the skill. The plan says no —
   that path deliberately keeps delivering text (recorded as a No-Go), so the affordance stays an
   intentional act rather than an inference over prose. The Success Criteria are worded to the
   `/ask-me` trigger accordingly, so nothing in this plan can ship "green" on a criterion the build
   does not satisfy.
