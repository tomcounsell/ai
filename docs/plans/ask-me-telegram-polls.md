---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2701
last_comment_id: 5237014662
revision_applied: true
revision_applied_at: 2026-08-10T07:17:53Z
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

**Verification baseline:** `95aba8187` — the single commit every `file:line` and symbol reference in
this document was last re-verified against. Where an older sha appears anywhere else in this plan's
history, this line supersedes it.
**Issue filed at:** 2026-08-10T04:47:24Z
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

**Active plans in `docs/plans/` overlapping this area:**
`docs/plans/flip-steering-writers-to-room-key.md` touches `agent/steering.py` writers (the
legacy `steering:{session_id}` key vs the Room key `steering:room:{room_id}`). This plan adds a
**new caller** of `push_steering_message`, not a new key writer, so it inherits whatever that
plan lands. Coordination note only, not a blocker: the vote translator must call
`push_steering_message(...)` and never write a steering key directly.

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
   — validates via a new drafter medium, records the outstanding-question expectation, builds the
   payload with a new `build_telegram_poll_outbox_payload(...)` next to
   `build_telegram_outbox_payload` (`:270`), rpushes to `telegram:outbox:{session_id}` with the
   same `OUTBOX_TTL` (`:431`).
5. **`bridge/telegram_relay.py::process_outbox`** (`:850`) — `"poll"` added to
   `KNOWN_MESSAGE_TYPES` (`:58`); new dispatch branch inside the `:917-931` if/elif chain calls
   `_send_queued_poll`, which **re-checks `poll_eligible(chat_id, session_id)` before the wire**
   (defense in depth: the CLI decided at ask time, the relay is the last writer before the send,
   and a payload can sit in the outbox across a session-type change). Ineligible → convert to the
   same plain-text payload the terminal-failure path uses and deliver that instead of dropping.
6. **`bridge/response.py::send_poll`** — raw MTProto `InputMediaPoll`; returns
   `(msg_id, server_poll_id)`.
7. **Registry write** — a provisional `telegram:poll:pending:{outbox_payload_id}` row is written
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
4. **`translate_poll_vote(poll_id)`** — idempotent. Claims `SET telegram:poll:answered:{poll_id}
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
   factored out of `:1951-2005`, called with the poll's own `msg_id` and no reply chain. `NONE` →
   log and return. See the Technical Approach for the full signatures and the per-branch contract.
6. **`agent/steering.py:85 push_steering_message(session_id, text, sender)`** — the sole inbox.
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
  - New module `bridge/poll_gating.py` holding the public `is_group_chat(chat_id)` (**moved** from
    `bridge/read_the_room.py:126 _is_group_chat`, which now imports it — one predicate, no copy)
    and `poll_eligible(chat_id, session_id) -> PollEligibility(ok, reason)`.
  - New CLI entry point `valor-ask-poll` in `pyproject.toml [project.scripts]`.
- **Coupling**: adds one new coupling — the bridge now holds inbound state (the poll registry)
  keyed on a Telegram-server-assigned id. Contained to two plain Redis keys with TTL, matching
  `bridge/job_router.py`'s deliberate non-Popoto posture.
- **Data ownership**: the relay becomes the writer of the poll registry (it is the only component
  that sees the server-assigned poll id). The bridge handler and reconciliation loop are readers
  plus claim-writers. No existing component's ownership changes.
- **Reversibility**: high. Removing the dispatch branch, the Raw handler, the reconciliation loop
  and the CLI reverts to today's prose behavior; the registry keys expire on their own. The
  `/ask-me` wording change is independent and separately revertible.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 — one is mandatory at **Task 2** (`gate-poll-human-tap`), which needs a human
  to tap a probe poll in the `Eng: Valor` group. That gate blocks **only the two inbound tasks**;
  an unavailable operator does not stall the outbound half.
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
  the result). Task 2 confirms a *different* human's tap is readable and attributable. Only the two
  inbound tasks depend on these.
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
- **`translate_poll_vote(poll_id)`**: the single idempotent translation function, reached by both
  the `events.Raw` fast path and the reconciliation loop.
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

  1. `is_group_chat(chat_id)` — the negative-id discriminator, **moved** out of
     `bridge/read_the_room.py:126 _is_group_chat` into this module and imported back there, so there
     is exactly one copy. A positive id (DM), a zero, an unparseable value, or `None` → ineligible,
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
     `:1951-2005`: `_build_completed_resume_text(completed, text, reply_chain_context=...)` then
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
  - `LIVE` / `PENDING` / `LIVE_GUARD` → `push_steering_message(session_id, steer_text, sender_name)`
    directly. It does **not** call `_ack_steering_routed`: there is no inbound message to react to,
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
- [ ] `is_group_chat` has exactly one definition in the repo — `bridge/read_the_room.py` imports it
  from `bridge/poll_gating.py` and its existing RTR behavior is unchanged
  (`tests/unit/` RTR suite is the regression net).

### Vote Selection Under Multiple Voters (group)

- [ ] Exactly one option with `voters >= 1` → that option is chosen.
- [ ] Two options each with `voters >= 1` → the higher-`voters` option is chosen and a warning
  naming the poll id and the tied options is emitted.
- [ ] Two options with **equal** `voters` → the lowest decoded option index wins, deterministically,
  on repeated runs.
- [ ] `GetPollVotesRequest` raising does not block translation; `sender_name` falls back through the
  session's `initial_telegram_message["sender_name"]` to `"Telegram poll"`.

### Claim Durability (the claim must not permanently swallow a question)

- [ ] An exception raised after `claim_poll_answer` (from the poll close, from
  `push_steering_message`, or from `resume_completed_session`) causes the claim key to be **deleted**
  and the next reconciliation tick to retry successfully.
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

- [ ] The `bridge/read_the_room.py` test suite (`grep -rln 'read_the_room' tests/`) — UPDATE only if
  moving `_is_group_chat` to `bridge/poll_gating.py::is_group_chat` changes an import a test
  patches; RTR behavior itself must be unchanged and those tests are the regression net.

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

Additional coverage required by **revision cycle 4** (the group-only + eng-only re-scope): every
bullet under **Eligibility Gate**, **Vote Selection Under Multiple Voters**, and **Claim
Durability** in the Failure Path Test Strategy above, in `tests/unit/test_poll_gating.py`,
`tests/unit/test_ask_poll_cli.py`, `tests/unit/test_bridge_relay.py`, and
`tests/unit/test_poll_vote_translation.py` respectively.

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
`bridge/telegram_bridge.py:1951-2005`. Explicitly covered by an integration test against a
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
**Mitigation:** two independent changes, both mandatory (Technical Approach, Task 9): everything
after the claim runs in `try/except Exception` with the handler **deleting the claim key** before
logging; and completion is recorded as a separate `steered_at` field written only after the steer
returns, with `iter_unanswered_polls()` re-yielding "claim present, no `steered_at`, older than one
reconcile interval" and `poll_expired_unanswered` keying on **missing `steered_at`**. Covered by the
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
1. **Provisional-row-first.** Before calling `send_poll`, write
   `telegram:poll:pending:{outbox_payload_id}` → `{chat_id, session_id, question, options,
   created_at}` with `SET NX EX <POLL_REGISTRY_TTL_S>`. After the send returns, write the real
   `telegram:poll:{server_poll_id}` row and delete the provisional one. A restart in the window
   leaves the provisional row behind as evidence that a send may have landed.
2. **Orphan adoption in the reconciliation loop (Task 10), matched on an exact embedded key — not
   on question text.** Question text is not a unique key: an agent re-asking after a first poll
   expired unanswered, or two sessions in the same chat asking the same standard question, produce
   two candidates with no tie-break. So the correlation id is **carried inside the poll itself**.
   `PollAnswer.option` is an arbitrary `bytes` blob that Telegram echoes back verbatim on read, so
   every option is encoded as `f"{index}:{outbox_payload_id}".encode()` instead of `bytes([index])`
   (still unique per option, which is the only constraint). Adoption then reads
   `MessageMediaPoll.poll.answers[0].option`, splits off the `outbox_payload_id`, and matches it
   exactly against the provisional row's id. `translate_poll_vote`'s option selection parses the
   same encoding to recover the index, so nothing else changes.
   Adoption procedure: for each surviving provisional row, scan a bounded window of recent outbound
   history in that chat for a `MessageMediaPoll` whose embedded `outbox_payload_id` equals the
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
- [ ] The feature doc must document the Risk 9 separation: `claim` is the one-shot lock, `steered_at`
  is the completion marker, and both `iter_unanswered_polls()` and `poll_expired_unanswered` key on
  `steered_at`.
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
- [ ] Update `docs/features/read-the-room.md` (or wherever `_is_group_chat` is described) to point at
  the new shared `bridge/poll_gating.py::is_group_chat` home. If no such doc exists, note the move in
  the feature doc instead.
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
  `GetPollVotesRequest`. Output in the PR description. UNRESOLVED (no tap within
  `POLL_PROBE_TAP_WAIT_S`) pauses **only** Tasks 9-10 and is recorded on #2701.
- [ ] **Neither gate opens a client with updates enabled on the bridge's auth key.** Both probes run
  against a temp copy of the session file with `receive_updates=False` and no writeback, and neither
  probes a DM. Asserted by reading the probe script, and stated in the PR description.
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
- [ ] `is_group_chat` exists in exactly one place; `bridge/read_the_room.py` imports it and its RTR
  behavior is unchanged.
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

The lead agent orchestrates and never builds directly.

### Team Members

- **Probe runner (vote-readback gates)**
  - Name: `poll-probe`
  - Role: Run the group vote-readback probe (Task 1) and the human-tap probe (Task 2), reporting
    PASS / FAIL / UNRESOLVED with verbatim MTProto output. **Never probes a DM, never opens a client
    with updates enabled on the bridge's auth key, always works from a temp session copy.**
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
  - Role: Registry reader, `translate_poll_vote`, the `bridge/answer_routing.py` extraction
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

**Gate topology (changed in revision cycle 4).** The *send* capability is settled by spike-6 and is
not gated. Two gates remain and they block **only the inbound tasks**: an unavailable operator no
longer stalls eleven tasks. Tasks 3-8 and 11-12 depend on neither gate.

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
- **PASS** → paste the probe output into the PR description; Tasks 9-10 are unblocked.
- **FAIL** → **STOP the build.** Report the verbatim error, record it on #2701, and hand back for
  a design revision — without readback there is no inbound half on this surface.

### 2. GATE — human tap readback and attribution (bounded; UNRESOLVED permitted)
- **Task ID**: gate-poll-human-tap
- **Depends On**: gate-poll-vote-readback
- **Validates**: manual probe; output pasted verbatim into the PR description
- **Informed By**: Research finding 5, Risk 2b
- **Assigned To**: `poll-probe`
- **Agent Type**: builder
- **Parallel**: false
- Under the same production-safety constraints as Task 1 (session copy, `receive_updates=False`,
  no writeback, group only, cleanup after).
- Send a probe poll into the same eng group and **leave it open**, with a caption naming exactly
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
  PASS and FAIL), record it on #2701, and **pause Tasks 9-10 only**. Every other task proceeds.
  Resume by re-running this task when the operator is available.
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
- **Move** `_is_group_chat` out of `bridge/read_the_room.py:126` into this module as the public
  `is_group_chat(chat_id)`, keeping its documented semantics verbatim (negative id → group;
  `None`, unparseable, or non-negative → `False`). Re-point `bridge/read_the_room.py` to import it.
  **One definition, no copy** — a second copy would silently drift from the RTR suppression rule.
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
  `{"type": "poll", chat_id, reply_to, question, options, session_id, timestamp}`.
  **Do not stamp `session_type` into the payload** — a queued payload would outlive a session's real
  type; the relay re-reads eligibility instead (Task 7).
- Add `TelegramRelayOutputHandler.send_poll(...)` as a sibling of `send` (`:611`): validate via the
  drafter, record the outstanding-question expectation, rpush with the existing `OUTBOX_TTL`
  (`:431`). It must not hit the `if not text` early return at `:673-674`.
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
- Write `telegram:poll:{poll_id}` →
  `{chat_id, msg_id, session_id, question, options, created_at, steered_at}` with
  `SET ... NX EX <POLL_REGISTRY_TTL_S>`, following `bridge/job_router.py:85-96` and
  `bridge/context.py:513-529`. `steered_at` starts absent.
- Plain Redis string keys. **No Popoto model, no `scripts/update/migrations.py` entry.** Comment the
  rationale pointing at `bridge/job_router.py:6-18`.
- Provide the Race-6 provisional row `telegram:poll:pending:{outbox_payload_id}`, written **before**
  `send_poll` and promoted to the real row (then deleted) once the server poll id is known. This
  write belongs **here and only here** — it is not duplicated in the relay task.
- Provide `register_poll(...)`, `lookup_poll(poll_id)`, `claim_poll_answer(poll_id)` (`SET NX EX`),
  `release_poll_claim(poll_id)`, `mark_poll_steered(poll_id)`, `register_pending_poll(...)`,
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
- **Re-check `poll_eligible(chat_id, session_id)` at the top of `_send_queued_poll`** (defense in
  depth: the CLI decided at ask time, the relay is the last writer before the wire, and a payload can
  sit in the outbox across a session-type change). Ineligible → **convert to the plain-text payload
  and deliver it**, log the reason, and do not send a poll. Never drop.
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
- Call `register_pending_poll(...)` before `send_poll` and `promote_pending_poll(...)` after it
  (functions from Task 6; this task calls them, it does not define them).
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

- `resolve_answer_target(session_id) -> AnswerTarget` — a state read over
  `bridge/telegram_bridge.py:1799-1866`. `AnswerTarget` is
  `(kind: AnswerTargetKind, session: AgentSession | None, matched_status: str | None,
  pending_age_s: float | None)`; `AnswerTargetKind` is `LIVE | PENDING | LIVE_GUARD | COMPLETED |
  NONE`. **This is a restructure, not a verbatim lift** — the source interleaves the ladder with
  `await _ack_steering_routed(...)` + `return` at every branch. Behavior-preservation checklist,
  all four mandatory:
  1. `matched_status` carried because the LIVE log embeds `matching_session.status` and the
     LIVE_GUARD log embeds `live_guard.status`.
  2. `pending_age_s` carried because the PENDING log embeds `age=%.1f` from
     `_pending_session_age_seconds(pending_session.created_at, time.time())` (`:1834`).
  3. `COMPLETED` returns the record chosen by the existing most-recent-`created_at` sort
     (`_completed_created_at`, `:1898`), **not** `completed_sessions[0]` — the wrong record silently
     degrades `_build_completed_resume_text`'s `context_summary`.
  4. `_steering_session_enqueued = True` (`:2006`) stays caller-side; `resume_completed_session`
     returns `None`.
  The `LIVE_GUARD` kind is the existing belt-and-suspenders re-check at `:1856-1866`.
- `resume_completed_session(*, completed, text, sender_name, telegram_chat_id,
  telegram_message_id, chat_title=None, sender_id=None, project=None, project_key=None,
  working_dir=None, telegram_message_key=None, reply_chain_context=None,
  extra_context_overrides=None) -> None` — lifted from `:1951-2005`:
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

**9b. Add `translate_poll_vote(client, poll_id)`:**
- `lookup_poll`; unknown → return quietly.
- Confirm with `messages.GetPollResultsRequest(peer=chat_id, msg_id=msg_id)` rather than trusting a
  possibly `min=True` update.
- **Deterministic group selection rule** (the DM rule is dead — several members can vote):
  filter `results.results` to `voters >= 1`; zero → return **without claiming**; exactly one → use
  it; more than one → log a warning naming the poll id and the tied options, then take the highest
  `voters`, breaking ties by **lowest decoded option index** (`decode_option`, Task 4).
- `claim_poll_answer(poll_id)`; lost claim → return.
- **Everything after the claim runs inside `try/except Exception`; the handler calls
  `release_poll_claim(poll_id)` before logging** so the next reconciliation tick retries (Risk 9).
- Close the poll by editing with `closed=True`. In a group this is the first-voter-wins boundary and
  is intentional.
- Resolve `sender_name`: `messages.GetPollVotesRequest` best-effort → target session's
  `initial_telegram_message["sender_name"]` → the literal `"Telegram poll"`. A failure here never
  blocks translation.
- Build the steer text: `Poll answer to your question "<question>": <chosen option>`; for the escape
  hatch, instruct a narrowed plain-text followup the human answers by reply-to.
- Branch on `resolve_answer_target(session_id)`, with a stated outcome for **every** kind:
  - `LIVE` / `PENDING` / `LIVE_GUARD` → `push_steering_message(session_id, steer_text, sender_name)`.
    No `_ack_steering_routed` (no inbound message to react to; closing the poll is the
    acknowledgment) and deliberately no abort-keyword detection (a poll option is never an abort).
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
- **Never write a steering key directly** — always via `agent/steering.py:85 push_steering_message`
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
- Register the repo's **first** `@client.on(events.Raw)` handler in `bridge/telegram_bridge.py` near
  `:1165`, filtering `UpdateMessagePoll` and calling `translate_poll_vote(client, poll_id)`. It must
  never raise into Telethon's update loop.
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
  surviving `telegram:poll:pending:{outbox_payload_id}` row, scan a bounded window of recent outbound
  history in that chat for a `MessageMediaPoll` whose `poll.answers[0].option` decodes (via
  `decode_option`, Task 4) to the same `outbox_payload_id` and which has no `telegram:poll:{poll.id}`
  row. Write the real row from the provisional data plus the discovered `msg_id`/`poll.id`, then
  delete the provisional. **If more than one candidate matches, log a warning and adopt nothing** —
  an ambiguous adoption steers a session with someone else's answer. A provisional row that reaches
  TTL with no match is dropped with a warning.
- Every interval, TTL, and warn-age is a named env-overridable constant with a grain-of-salt comment.

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

### 13. Tests
- **Task ID**: build-tests
- **Depends On**: build-vote-observation, build-catchup-transcript, build-ask-me-skill,
  build-ask-poll-cli
- **Validates**: all new and updated test files
- **Informed By**: Test Impact and Failure Path Test Strategy sections
- **Assigned To**: `poll-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_ask_poll_cli.py`, `test_poll_payload.py`, `test_poll_vote_translation.py`,
  `test_poll_registry.py`, `test_poll_gating.py`, `test_agent_catchup_poll_transcript.py`.
- Apply every UPDATE listed in **Test Impact**.
- Cover every bullet in **Failure Path Test Strategy**, including the whole **Eligibility Gate**,
  **Vote Selection Under Multiple Voters**, and **Claim Durability** blocks.
- Cover Races 2, 3, 5, and 6 explicitly (double translation, completed-session re-enqueue,
  registry-survives-restart, provisional-row orphan adoption).
- **No test may send a poll into a DM or otherwise re-probe the settled capability matrix.** DM
  behavior is asserted by testing that the CLI queues prose, not by hitting Telegram.
- Run with `scripts/pytest-clean.sh`, never bare `pytest`.

### 14. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Validates**: the Documentation section checklist
- **Assigned To**: `poll-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Complete every item in the **Documentation** section.

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: the Verification table
- **Assigned To**: `poll-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the **Verification** table.
- Confirm every **Success Criteria** box, including that both gate outputs are in the PR description.
- Report pass/fail.

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
| Poll expiry warning wired (inbound-half signal) | `grep -c 'poll_expired_unanswered' bridge/*.py` | output > 0 |
| Answer-routing seam extracted | `test -f bridge/answer_routing.py && grep -c 'def resolve_answer_target\|def resume_completed_session' bridge/answer_routing.py` | output == 2 |
| Completed branch reachable from the vote path (anti-criterion) | `grep -lc 'resume_completed_session' bridge/telegram_bridge.py bridge/answer_routing.py \| wc -l` | output == 2 |
| Eligibility gate exists | `test -f bridge/poll_gating.py && grep -c 'def poll_eligible\|def is_group_chat' bridge/poll_gating.py` | output == 2 |
| Eligibility read at both points | `grep -lc 'poll_eligible' tools/ask_poll.py bridge/telegram_relay.py \| wc -l` | output == 2 |
| One `is_group_chat` definition (anti-criterion) | `grep -rn 'def is_group_chat\|def _is_group_chat' bridge/ \| wc -l` | output == 1 |
| RTR imports the shared predicate | `grep -c 'poll_gating' bridge/read_the_room.py` | output > 0 |
| Eng-only gate compares the enum, not a literal (anti-criterion) | `grep -c "session_type == .eng." bridge/poll_gating.py` | match count == 0 |
| Claim released on failure (Risk 9) | `grep -c 'release_poll_claim' bridge/*.py` | output > 0 |
| Steered marker separate from the claim (Risk 9) | `grep -c 'steered_at' bridge/*.py` | output > 0 |
| Push question answerable in production, not by a second client | `grep -c 'poll_update_observed' bridge/telegram_bridge.py` | output > 0 |
| Public drafter seam used, not `draft_message` | `grep -c 'validate_poll_question' bridge/message_drafter.py agent/output_handler.py` | every file > 0 |
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

### Structural checks (cycle 3, re-run on `ecd5d1972`)

All four required sections (Documentation, Update System, Agent Integration, Test Impact) present and
substantive. Tasks 1-11, no numbering gaps; every `Depends On` resolves to a real Task ID; no cycles;
every task carries a `**Validates**` field. All 4 prerequisites PASS
(`scripts/check_prerequisites.py`). Every cited file exists except the four the plan intentionally
creates (`tools/ask_poll.py`, `bridge/answer_routing.py`, `.claude/skill-context/ask-me.md`,
`docs/features/telegram-poll-questions.md`). Every cited symbol re-verified present at its named
location. No Popoto model changes, so the no-migration claim holds. All Verification anti-criteria
re-run on a clean tree return their expected values, except the two multi-file `grep -c` rows noted
as a NIT above, which emit per-file output rather than a scalar.

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
