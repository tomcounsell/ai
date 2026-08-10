---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2701
last_comment_id: none
revision_applied: false
revision_applied_at: null
---

# Render /ask-me questions as native Telegram polls

## Problem

When work happens over Telegram rather than a local terminal, a blocked agent asks its
question as prose. The human has to read a paragraph on a phone and compose a written
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

**Desired outcome:**

A question the agent poses at turn end is delivered to the chat as a native Telegram poll
with the recommended option first and `Other: wait for followup message` last. One tap
records the answer, the bridge converts the vote into a steering message, and the session
resumes on its next turn with the choice in hand. No new blocking primitive, no new inbound
message path, and every non-Telegram surface degrades to today's text behavior.

## Freshness Check

**Baseline commit:** `e051e95da` (Plan revision (sdlc-stall-auto-resume): address critique findings)
**Issue filed at:** 2026-08-10T04:47:24Z
**Disposition:** Unchanged

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
   aggregate counts. In a 1:1 DM where the sender never votes, any option with `voters >= 1` is
   unambiguously the human's choice.
   *Informs:* the translator's option-selection rule, and why the feature's semantics are
   cleanest in DMs. Group chats work but "who voted" is only available via
   `GetPollVotesRequest` / `recent_voters`.

6. **Closing a poll is an edit re-sending the same poll with `closed=True`.** The 2019 Telethon
   bug report about `EditMessageRequest.random_id` is long fixed on 1.42.
   Source: <https://github.com/LonamiWebs/Telethon/issues/1355>.
   *Informs:* closing the poll on first translation is the natural idempotency and
   "already answered" visual marker.

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
  (Task 4) — and makes `GetPollResultsRequest` reconciliation (Task 6) the durable mechanism
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
- **Impact on plan**: Task 7 is narrow and surgical (render `MessageMediaPoll` into the
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
   options as plain text and hands off to the existing `send_message` path. **Degradation happens
   here, once.**
4. **`TelegramRelayOutputHandler.send_poll(...)`** (new sibling of `send`, `agent/output_handler.py:611`)
   — validates via a new drafter medium, records the outstanding-question expectation, builds the
   payload with a new `build_telegram_poll_outbox_payload(...)` next to
   `build_telegram_outbox_payload` (`:270`), rpushes to `telegram:outbox:{session_id}` with the
   same `OUTBOX_TTL` (`:431`).
5. **`bridge/telegram_relay.py::process_outbox`** (`:850`) — `"poll"` added to
   `KNOWN_MESSAGE_TYPES` (`:58`); new dispatch branch inside the `:917-931` if/elif chain calls `_send_queued_poll`.
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
   `min=True` update. Selects the chosen option (`voters >= 1`, sender never votes). Closes the
   poll by editing it with `closed=True`.
5. **`route_answer_to_session(session_id, text, sender)`** — a factored helper that reproduces the
   reply-to routing ladder already in `bridge/telegram_bridge.py:1787-1893`:
   `running`/`active` → `push_steering_message`; `pending` → `push_steering_message`;
   `completed` → re-enqueue with prior context behind the existing live guard.
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
  - New `medium="telegram_poll"` validate-only branch in `bridge/message_drafter.py`.
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
- PM check-ins: 2-3 — one is mandatory at the **Task 1 gate** (real-DM poll capability), because
  a negative result forces a scope revision to group-chats-only.
- Review rounds: 2+ — a new inbound Telethon handler and a new outbox payload variant both touch
  load-bearing delivery paths.

The size is driven by breadth, not depth: eight touched subsystems (CLI, output handler, drafter,
relay, response, bridge handler, reconciliation loop, catchup transcript) plus a global skill and
its skill-context file.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telethon >= 1.42 with poll TL types | `.venv/bin/python -c "from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities, UpdateMessagePoll, PollResults"` | Poll construction and vote observation |
| Authenticated bridge Telegram session | `test -f "$HOME/Desktop/Valor/telegram_session.session"` | Task 1 probe needs a real user-account session |
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Outbox, steering, and the poll registry |
| A real operator DM peer in projects.json | `.venv/bin/python -c "import json,os; d=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json'))); assert d.get('dms')"` | Task 1 must target a real user DM, not Saved Messages |

Run via `python scripts/check_prerequisites.py docs/plans/ask-me-telegram-polls.md`.

## Solution

### Key Elements

- **Task-1 capability gate**: a one-shot probe that sends a poll from the bridge account into a
  **real user DM** (not Saved Messages) and deletes it. Everything downstream is conditional on it.
- **`tools/ask_poll.py` + `valor-ask-poll` CLI**: the single transport-aware entry point the agent
  calls. Owns degradation to text for email, local, and system surfaces.
- **`send_poll` on the output handler + a `"poll"` outbox variant**: extends the proven
  outbox → relay seam rather than inventing a second outbound path.
- **`bridge/response.py::send_poll`**: raw MTProto `InputMediaPoll`, returning both the message id
  and the **server-assigned poll id**.
- **Poll registry** (`telegram:poll:{poll_id}`): the piece the issue's sketch was missing.
  Without it a vote cannot be routed, because `UpdateMessagePoll` has no peer or message id.
- **`translate_poll_vote(poll_id)`**: the single idempotent translation function, reached by both
  the `events.Raw` fast path and the reconciliation loop.
- **`route_answer_to_session(...)`**: factored from the existing reply-to routing ladder so a vote
  reaches a `running`, `pending`, or `completed` session exactly the way a typed reply already does.
- **Transcript rendering of polls**: so the catchup judge sees the question instead of a blank line.
- **`/ask-me` relaxation + skill-context**: the one-at-a-time rule becomes a preference in the
  global body; the Telegram-specific mechanics live in `.claude/skill-context/ask-me.md`.

### Flow

Agent is blocked → runs `/ask-me` → detects headless Telegram surface → calls `valor-ask-poll` →
**poll appears in chat** (recommended option first, `Other: wait for followup message` last) →
human taps → **poll closes** → vote translated to a steering message → **agent resumes next turn**
with the choice in hand → (if `Other`) agent sends a narrowed plain-text followup → human replies
by reply-to → **same session continues**.

### Technical Approach

**Owner decisions are settled and are not reopened here:** no mid-turn blocking (turn-boundary
rendering plus vote→steering translation only); the final option is always the literal string
`Other: wait for followup message`; one-question-at-a-time becomes a stated preference.

> **Every `file:line` in this plan is approximate — locate by symbol.** All symbol *names* were
> re-verified against `5d9729ad8`, but offsets drift under refactors. Builders must find their edit
> site with `grep -n '<symbol>' <file>` and treat the cited line as a hint only. Offsets below were
> re-verified at revision time; the ones that had drifted are corrected in place.

- **Gate first.** Task 1 runs before any production code. The only evidence so far is a self-DM
  (Saved Messages) probe, which does not prove the general case. If MTProto rejects a poll into
  another user's DM, the build **stops** and the issue is re-scoped to group chats. No downstream
  task may assume the capability.
- **Reconciliation is primary, `events.Raw` is the fast path.** Spike-2 proved the update is not
  self-routing, and it is unverified whether a user account even receives `updateMessagePoll` for a
  poll it sent. Making `GetPollResultsRequest` reconciliation the guaranteed mechanism de-risks
  that unknown, and gives restart-survivability for free. Both paths call the same idempotent
  `translate_poll_vote(poll_id)`, so adding the fast path cannot introduce a second behavior.
- **Reuse the routing ladder; do not re-implement it.** `bridge/telegram_bridge.py:1787-1893`
  already knows how to deliver a human answer to a `running`, `pending`, or `completed` session
  including the completed-session live guard (`:1847-1880`) and the duplicate short-circuit
  (`:1884-1893`). Extract it as `route_answer_to_session(...)` and call it from both the message
  handler and the vote translator. A vote translator that only calls `push_steering_message`
  silently drops answers to sessions that finished while the human was deciding.
- **Steering text carries the question, not just the option.** The steer reads as
  `Poll answer to your question "<question>": <chosen option>` so the resumed turn has the
  binding without needing to re-derive it. For the escape hatch the steer explicitly instructs a
  narrowed plain-text followup.
- **Degrade once, at the CLI.** `_resolve_transport()` precedence is already the repo's single
  answer to "which surface am I on". Non-telegram → numbered-list text through the normal
  `send_message` path. `EmailOutputHandler` gets no `send_poll` and the capability probe
  (`hasattr`, mirroring `adapter.py:61`) keeps it valid.
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
  `_compose_structured_draft` (`:952`) is bypassed either way so the emoji prefix / stage line /
  link footer never mangle a poll question.
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

### Error State Rendering

- [ ] Poll send failure surfaces to the human as the plain-text fallback question rather than
  silence: on a terminal relay failure for a `"poll"` payload, the question is re-enqueued as text.
  This is the user-visible error path and is explicitly tested.
- [ ] `_dead_letter_message` (`telegram_relay.py:803`) must **not** treat `"poll"` as ephemeral
  (`:827`) — a dropped question is a stuck agent. Test asserts a poll payload dead-letters loudly
  instead of being discarded, **and separately** that it survives the `if chat_id and text` gate
  (`:836`), which a text-less payload would otherwise fall straight through.

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
  if `route_answer_to_session` extraction changes the call shape; otherwise no change.
- [ ] `tests/integration/test_steering.py::test_steering_push_only_after_session_match` (`:434`),
  `test_pending_session_within_window_receives_steering` (`:514`) — UPDATE: extend to cover a vote
  originated steer reaching `pending` and `completed` sessions via `route_answer_to_session`.
- [ ] `tests/unit/test_react_with_emoji.py::test_react_queues_reaction_payload` (`:61`) — no change;
  cited as the payload-shape assertion pattern the new poll payload test copies.

New test files (greenfield, no prior coverage — grep of `tests/` for `poll` returns only
`test_poll_interval_is_100ms`, an unrelated relay constant):
`tests/unit/test_ask_poll_cli.py`, `tests/unit/test_poll_payload.py`,
`tests/unit/test_poll_vote_translation.py`, `tests/unit/test_poll_registry.py`,
`tests/unit/test_agent_catchup_poll_transcript.py`.

Additional coverage required by this revision: Race 6 (provisional row survives a simulated restart
and is adopted) in `tests/unit/test_poll_registry.py`, and the `poll_expired_unanswered` warning
(emitted once, not re-emitted) in `tests/unit/test_poll_vote_translation.py`.

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
- **Identifying *which* human voted in a group.** `GetPollVotesRequest` and `recent_voters` exist,
  but the 1:1 DM case is the target and is unambiguous. In a group, translate the first vote and
  move on.
- **A general "structured outbound message" abstraction.** One concrete payload variant, following
  the `reaction` / `custom_emoji_message` precedent. Do not build a framework for the second one
  before it exists.
- **Reworking handled-detection.** Spike-4 showed `_has_valor_reply_after` already behaves
  correctly. Only transcript *rendering* changes.
- **Backfilling poll support into `bridge/catchup.py`'s inbound scan.** Humans do not send us
  polls, and a vote is not a message. The reconciliation loop is the catchup story for polls.

## Risks

### Risk 1: a poll into a real user DM is rejected by MTProto
**Impact:** The entire feature collapses to group chats only, which is not where `/ask-me`
conversations happen. Every downstream task is wasted work.
**Mitigation:** Task 1 is the first executable step and is a hard gate with an explicit STOP.
No downstream task may assume the capability. A negative result triggers a scope revision on
#2701 before any production code is written.

### Risk 2: `updateMessagePoll` never reaches the user account for its own poll
**Impact:** The `events.Raw` fast path silently never fires and votes are never observed.
**Mitigation:** Architected around it. The `GetPollResultsRequest` reconciliation loop (Task 6) is
the **primary** mechanism and works with zero update delivery; the Raw handler is a latency
optimization layered on the same idempotent translator. Task 1's probe records whether the update
arrives, and the answer only affects observed latency, not correctness.

### Risk 3: an answered poll never reaches its session because the session finished
**Impact:** The human taps, sees the poll close, and nothing happens. Worst-case UX: the affordance
looks broken and trust in it is gone after one occurrence.
**Mitigation:** `route_answer_to_session` reuses the existing completed-session re-enqueue ladder
(`bridge/telegram_bridge.py:1847-1893`) rather than calling `push_steering_message` blindly.
Explicitly covered by an integration test against a `completed` session.

### Risk 4: duplicate or repeated translation of one vote
**Impact:** The agent receives the same answer several times, or a retracted-and-changed vote
produces two contradictory steers.
**Mitigation:** `SET telegram:poll:answered:{poll_id} NX` one-shot claim shared by both paths, plus
closing the poll on first translation so a re-vote is impossible at the source.

### Risk 5: the poll question is dropped on relay failure and the agent stalls forever
**Impact:** A blocked agent with no visible question — the exact failure the feature exists to
prevent, made worse because the human sees nothing at all.
**Mitigation:** Two guards must both be handled, and missing the second is the likely mistake:
`"poll"` stays out of the ephemeral-discard tuple in `_dead_letter_message`
(`telegram_relay.py:827`), **and** the persistence branch's `if chat_id and text` gate (`:836`)
must receive the question as `text`, since a poll payload carries no `text` key and would
otherwise fall through both branches into silence. A terminal poll failure re-enqueues the
question as plain text. Tested as an error-rendering path.

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
(Task 6). When it observes a registry row at or past `POLL_EXPIRY_WARN_AGE_S` with no matching
`telegram:poll:answered:{poll_id}` claim, it emits a single
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
**Location:** `route_answer_to_session` (new, factored from `bridge/telegram_bridge.py:1787-1893`).
**Trigger:** The turn ends and the session finalizes before the tap.
**Data prerequisite:** The session must be re-read at translation time, not cached at send time.
**State prerequisite:** The completed-session re-enqueue must not double-enqueue.
**Mitigation:** Reuse the existing ladder verbatim, including the `pending/running/active` live
re-check guard (`:1847-1880`) and the duplicate short-circuit (`:1884-1893`).

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
2. **Orphan adoption in the reconciliation loop (Task 6).** For each surviving provisional row,
   scan a bounded window of recent outbound chat history in that chat for a `MessageMediaPoll`
   whose question matches and which has no `telegram:poll:{poll.id}` row; adopt it by writing the
   real registry row from the provisional data plus the discovered `msg_id`/`poll.id`, then delete
   the provisional row. A provisional row that reaches its TTL with no match is dropped and logged
   at warning (the send never landed).

## No-Gos (Out of Scope)

- `[EXTERNAL]` **The owner's physical tap in Task 1 Part B.** This is no longer deferred out of
  scope — it is a gate step and a Success Criterion. What remains external is only the human action
  itself: someone must tap. If the owner is unavailable, the build **waits** at the gate rather than
  proceeding, because Part B is what proves the tap is readable at all.
- `[EXTERNAL]` **Rolling the change out to other bridge machines.** Requires `/update` on each
  machine (see **Update System**).
- `[SEPARATE-SLUG #2701]` Nothing else is deferred to a follow-up. Group-chat voter attribution,
  quiz/multiple-choice polls, and free-text capture are **rabbit holes deliberately not built**
  (see **Rabbit Holes**), not deferred promises.

Everything else the issue's acceptance criteria name is in scope for this plan.

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
  the degradation matrix (telegram → poll; email/local/system → numbered text).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` to name the poll vote as a steering producer.
- [ ] The feature doc must name `poll_expired_unanswered` as *the* operator signal for a failing
  inbound half, and document the Race-6 provisional row / orphan-adoption mechanism.
- [ ] Update `docs/features/message-drafter.md` with the `telegram_poll` validate-only medium.
- [ ] Update `docs/features/bridge-worker-architecture.md` to list the new bridge handler and
  background loop.
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

- [ ] **Gate:** a poll sent from the bridge account into a real user DM is confirmed to work, with
  the probe output recorded in the PR description — or the limitation is documented and #2701 is
  re-scoped before further work.
- [ ] An agent question posed at turn end is delivered as a native Telegram poll with the
  recommended option first and `Other: wait for followup message` last.
- [ ] Tapping an option produces a steering message the worker consumes on its next turn, with the
  chosen option text present in the session's input.
- [ ] Voting `Other: wait for followup message` produces a plain-text followup question the human
  answers by reply-to, resuming the same session.
- [ ] **A real human tap is observed end-to-end at least once** (Task 1 Part B): the tap is read
  back through `GetPollResultsRequest`, the output is in the PR description, and it records whether
  `updateMessagePoll` reached the account and which path surfaced the vote first.
- [ ] A vote is translated exactly once even when both the Raw handler and the reconciliation loop
  observe it, and even across a bridge restart.
- [ ] A poll that reached the screen but lost its registry row to a restart between send and write
  is adopted by the reconciliation loop and still routable (Race 6).
- [ ] An unanswered poll past the warn age emits exactly one greppable
  `poll_expired_unanswered` warning naming poll id, chat id, session id, and age (Risk 7).
- [ ] A vote on a session that has already completed re-enqueues that session rather than being
  dropped.
- [ ] Catchup transcripts render an outbound poll as its question text rather than a blank line,
  so the judge never re-asks a question already on screen.
- [ ] Email, local, and system surfaces produce today's text behavior via the CLI's degradation
  path; `EmailOutputHandler` remains valid without `send_poll`.
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

- **Probe runner (capability gate)**
  - Name: `poll-probe`
  - Role: Run the real-DM poll probe and report a binary gate result with verbatim MTProto output.
  - Agent Type: builder
  - Resume: true

- **Builder (outbound path)**
  - Name: `poll-outbound-builder`
  - Role: CLI, payload builder, `send_poll` handler method, drafter medium, relay dispatch branch,
    `bridge/response.py::send_poll`, registry write.
  - Agent Type: builder
  - Resume: true

- **Builder (inbound path)**
  - Name: `poll-inbound-builder`
  - Role: Registry reader, `translate_poll_vote`, `route_answer_to_session` extraction,
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

### 1. TASK ZERO — real-DM poll capability gate (HARD GATE)
- **Task ID**: gate-real-dm-poll
- **Depends On**: none
- **Validates**: manual probe; output pasted verbatim into the PR description
- **Informed By**: spike-1 (Telethon 1.42 types verified), Research finding 1 and 2
- **Assigned To**: `poll-probe`
- **Agent Type**: builder
- **Domain**: MCP-tool/API integration
- **Parallel**: false
- **This runs before any production code. Nothing downstream may assume the capability.**
- Write a one-shot probe script under `scripts/` (deleted before the PR, or kept only if it is
  genuinely reusable) that opens a **copy** of the bridge Telethon session with updates disabled,
  exactly as the prior Saved-Messages probe did.
- Resolve a **real operator user DM peer** from `~/Desktop/Valor/projects.json`. Saved Messages
  does **not** count and must not be used — that is the whole point of this task.
- Send `InputMediaPoll(Poll(id=<random int>, question=TextWithEntities("probe"),
  answers=[PollAnswer(TextWithEntities("A"), b"0"), PollAnswer(TextWithEntities("B"), b"1")]))`.
- Read back the sent message and assert `MessageMediaPoll`; record the **server-assigned
  `poll.id`** and confirm it differs from the id supplied — this validates the registry design.
- Additionally record whether an `updateMessagePoll` is observed for the account's own poll (best
  effort; a negative here is acceptable and does not block, because reconciliation is primary).
- Delete the probe message.
- **Part B — human-tap observation (also part of this gate).** Send a *second* probe poll into the
  same real DM and **leave it open**. Ask the owner to tap one option once. With the probe client
  still connected, record:
  (a) whether an `updateMessagePoll` for the account's own poll is observed at all (this is Risk 2's
  open question — answer it here rather than "best effort");
  (b) the `messages.GetPollResultsRequest(peer, msg_id)` response after the tap, proving the
  reconciliation path can read the choice;
  (c) which of the two paths surfaced the vote first.
  Then close and delete the probe poll. A negative on (a) is **not** a failure — it only confirms
  reconciliation-primary was the right call and that the Raw fast path is dead weight on this
  account. A negative on (b) **is** a gate failure: without it the inbound half cannot work at all,
  and the build stops the same way a Part A failure stops it.
- **PASS** → paste both probe outputs (Part A and Part B) into the PR description and proceed to
  Task 2.
- **FAIL (MTProto rejects a poll into another user's DM)** → **STOP the build.** Do not write any
  production code. Report the verbatim error, record it on #2701, and hand back for a scope
  revision to group-chats-only. This is the documented alternative in the issue's Pre-requisites
  bucket.

### 2. Poll primitive in `bridge/response.py`
- **Task ID**: build-send-poll
- **Depends On**: gate-real-dm-poll
- **Validates**: `tests/unit/test_poll_payload.py` (create)
- **Informed By**: spike-1, gate-real-dm-poll (exact working construction)
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `send_poll(client, chat_id, question, options, *, reply_to=None) -> tuple[int, int] | None`
  after `set_reaction` (`bridge/response.py:384`), returning `(msg_id, server_poll_id)`.
- Wrap text in `TextWithEntities`; `option=bytes([i])`; single-choice, non-quiz only.
- Follow the local error idiom (log, return `None`, never raise) but keep the `None` return
  distinguishable so the relay retries rather than silently dropping.
- Docstring records the placeholder-id / server-assigned-id asymmetry.

### 3. Outbound payload, handler method, drafter medium, relay dispatch
- **Task ID**: build-outbound-path
- **Depends On**: build-send-poll
- **Validates**: `tests/unit/test_poll_payload.py`, `tests/unit/test_ask_poll_cli.py` (create),
  `tests/unit/test_bridge_relay.py` (update)
- **Informed By**: spike-3 (the three text-less guards), Prior Art #1802 template
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `build_telegram_poll_outbox_payload(chat_id, question, options, reply_to, session_id)`
  beside `build_telegram_outbox_payload` (`agent/output_handler.py:270`), emitting
  `{"type": "poll", chat_id, reply_to, question, options, session_id, timestamp}`.
- Add `TelegramRelayOutputHandler.send_poll(...)` as a sibling of `send` (`:611`): validate via the
  drafter, record the outstanding-question expectation, rpush with the existing `OUTBOX_TTL`
  (`:431`). It must not hit the `if not text` early return at `:673-674`.
- Add `medium="telegram_poll"` to `_validate_for_medium` (`bridge/message_drafter.py:561`) —
  **validate only, question text only**: `<= 300` chars and non-empty. **Do not change the
  signature** (`(text: str, medium: str)`) — it cannot see options, and widening it ripples to
  `:1100`, `:1148`, and `tests/unit/test_medium_validators.py`. Option-count and option-length
  validation lives in `tools/ask_poll.py` (see the Technical Approach split). Bypass
  `_compose_structured_draft` (`:952`) so no emoji prefix / stage line / link footer is prepended.
- Add `"poll"` to `KNOWN_MESSAGE_TYPES` (`bridge/telegram_relay.py:58`) and a dispatch branch in the
  `msg_type` if/elif chain (`:917-931`) calling a new `_send_queued_poll`. Locate by symbol, not line.
- Fix the dead-letter path for text-less payloads, which has **two** guards, not one:
  (a) `_dead_letter_message` (`:803`) must not add `"poll"` to the ephemeral discard tuple
  `if msg_type in ("reaction", "custom_emoji_message")` (`:827`); and (b) the persistence branch
  immediately below is gated on `if chat_id and text` (`:836`) — a poll payload has no `text` key,
  so simply keeping it out of the ephemeral tuple still drops it silently. The poll branch must
  supply the question as the dead-letter `text` (or persist the poll payload explicitly). A
  terminal failure then re-enqueues the question as plain text.
- Write the Race-6 provisional registry row **before** calling `send_poll`, and promote it to the
  real `telegram:poll:{server_poll_id}` row immediately after the send returns.
- On success, run the existing post-send bookkeeping (`_record_sent_message` `:984`,
  `_append_outbound_chat_log` `:988`, `_bind_outbound_message_to_job` `:994`) and add a history
  row with `message_type="poll"`, modelled on `_record_sent_reaction` (`:182-212`).
- Create `tools/ask_poll.py` with `main()`, reusing `_resolve_transport()` precedence
  (`tools/send_message.py:63`). Telegram → `send_poll`; every other transport → numbered-list text
  through the existing `send_message` path. Probe the handler with `hasattr(..., "send_poll")`
  mirroring `adapter.py:61` so `EmailOutputHandler` stays valid.
- Own **all option validation** here: 2..10 options, each non-empty and `<= 100` chars, exiting
  non-zero on violation. Append / de-duplicate the mandatory literal final option
  `Other: wait for followup message`.
- Register `valor-ask-poll = "tools.ask_poll:main"` in `pyproject.toml [project.scripts]`.

### 4. Poll registry
- **Task ID**: build-poll-registry
- **Depends On**: build-outbound-path
- **Validates**: `tests/unit/test_poll_registry.py` (create)
- **Informed By**: spike-2 (update cannot self-route), spike-5 (plain-Redis precedent)
- **Assigned To**: `poll-outbound-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Write `telegram:poll:{poll_id}` → `{chat_id, msg_id, session_id, question, options, created_at}`
  with `SET ... NX EX <POLL_REGISTRY_TTL_S>`, following `bridge/job_router.py:85-96` and
  `bridge/context.py:513-529`.
- Plain Redis string keys. **No Popoto model, no `scripts/update/migrations.py` entry.** Comment
  the rationale pointing at `bridge/job_router.py:6-18`.
- Also provide the Race-6 provisional row: `telegram:poll:pending:{outbox_payload_id}` written
  **before** `send_poll`, promoted to the real row (and deleted) after the server poll id is known.
- Provide `register_poll(...)`, `lookup_poll(poll_id)`, `claim_poll_answer(poll_id)` (`SET NX EX`),
  `register_pending_poll(...)`, `iter_pending_polls()`, `promote_pending_poll(...)`, and
  `iter_unanswered_polls()`.
- All TTLs are named env-overridable constants with grain-of-salt comments.

### 5. Extract `route_answer_to_session` and add `translate_poll_vote`
- **Task ID**: build-vote-translation
- **Depends On**: build-poll-registry
- **Validates**: `tests/unit/test_poll_vote_translation.py` (create),
  `tests/integration/test_steering.py` (update)
- **Informed By**: spike-2, Research findings 4 and 5, Race 2 / Race 3
- **Assigned To**: `poll-inbound-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Factor the existing reply-to routing ladder (`bridge/telegram_bridge.py:1787-1893`) into
  `route_answer_to_session(session_id, text, sender)` preserving `running`/`active` → steer,
  `pending` → steer, `completed` → re-enqueue with the live guard (`:1847-1880`) and duplicate
  short-circuit (`:1884-1893`). Repoint the existing message handler at it — behavior must not
  change (existing steering tests are the regression net).
- Add `translate_poll_vote(client, poll_id)`:
  - `lookup_poll`; unknown → return quietly.
  - Confirm with `messages.GetPollResultsRequest(peer=chat_id, msg_id=msg_id)` rather than trusting
    a possibly `min=True` update. `total_voters == 0` → return **without** claiming.
  - Select the option with `voters >= 1` (the sender never votes).
  - `claim_poll_answer(poll_id)`; lost claim → return.
  - Close the poll by editing with `closed=True`.
  - Build the steer text: `Poll answer to your question "<question>": <chosen option>`; for the
    escape hatch, instruct a narrowed plain-text followup the human answers by reply-to.
  - Call `route_answer_to_session(...)`. **Never write a steering key directly** — always via
    `agent/steering.py:85 push_steering_message` (coordination note with
    `docs/plans/flip-steering-writers-to-room-key.md`).

### 6. `events.Raw` fast path + reconciliation loop
- **Task ID**: build-vote-observation
- **Depends On**: build-vote-translation
- **Validates**: `tests/unit/test_poll_vote_translation.py`, `tests/unit/test_poll_registry.py`
- **Informed By**: Risk 2 (update delivery unverified), Race 1 / Race 5
- **Assigned To**: `poll-inbound-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Register the repo's **first** `@client.on(events.Raw)` handler in `bridge/telegram_bridge.py`
  near `:1165`, filtering `UpdateMessagePoll` and calling `translate_poll_vote(client, poll_id)`.
  It must never raise into Telethon's update loop.
- Add the reconciliation loop started alongside `relay_loop` (`:3231-3233`): iterate
  `iter_unanswered_polls()` only, call `translate_poll_vote` on each, adaptive interval
  (fast for `POLL_RECONCILE_FAST_WINDOW_S` after send, then slow), FloodWait backoff mirroring
  `telegram_relay.py:918-945`.
- This loop is the **primary** mechanism; the Raw handler is a latency optimization. Both call the
  same idempotent translator, so enabling either alone is correct.
- **Emit the inbound-half operator signal (Risk 7).** During the same scan, any registry row at or
  past `POLL_EXPIRY_WARN_AGE_S` with no `telegram:poll:answered:{poll_id}` claim emits exactly one
  `logger.warning("poll_expired_unanswered ...")` with poll id, chat id, session id, and age, and is
  marked so it is not re-emitted. Also warn on consecutive `GetPollResultsRequest` failures.
- **Adopt orphaned provisional rows (Race 6).** For each surviving `telegram:poll:pending:{...}`
  row, scan a bounded window of recent outbound history in that chat for a matching
  `MessageMediaPoll` with no registry row, write the real row from the provisional data plus the
  discovered `msg_id`/`poll.id`, and delete the provisional. A provisional row that reaches TTL with
  no match is dropped with a warning.
- Every interval, TTL, and warn-age is a named env-overridable constant with a grain-of-salt
  comment.

### 7. Catchup transcript rendering
- **Task ID**: build-catchup-transcript
- **Depends On**: build-outbound-path
- **Validates**: `tests/unit/test_agent_catchup_poll_transcript.py` (create)
- **Informed By**: spike-4 (blank-line finding)
- **Assigned To**: `poll-surfaces-builder`
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/agent_catchup.py::read_thread` (text extraction at `:371`; locate by symbol), render a message
  whose media is `MessageMediaPoll` as its question text plus options rather than `""`, so
  `_render_transcript` (`:419`) no longer emits a bare `"Valor: "` and `sweep_chat`'s empty-text
  skip (`:560-562`) no longer drops it.
- Leave `_has_valor_reply_after` (`:436-452`) and `_valor_reacted` (`:321`) alone — spike-4
  confirmed they already behave correctly for a text-less outbound message.
- Leave `bridge/catchup.py:333-337` alone; document in the feature doc that a vote is not a message
  and the reconciliation loop is the catchup story for polls.

### 8. `/ask-me` relaxation and skill-context
- **Task ID**: build-ask-me-skill
- **Depends On**: none
- **Validates**: manual read; `.claude/skill-context/README.md` table entry present
- **Informed By**: owner decisions 2 and 3
- **Assigned To**: `poll-surfaces-builder`
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/skills-global/ask-me/SKILL.md` step 5: change "Ask one question at a time" from a
  hard rule to a stated preference and remove "Never batch your whole blocker list into one
  multi-question call", replacing it with guidance that separate questions are acceptable only when
  genuinely independent. **Keep** the "Questionnaire mode" anti-pattern, reworded to match.
- Add the skill-context probe sentence: "If `.claude/skill-context/ask-me.md` exists, read it and
  honor its declarations; otherwise use the generic defaults described below."
- Keep the global body generic — no `valor-ask-poll`, no Telegram specifics in it.
- Create `.claude/skill-context/ask-me.md` declaring: the `valor-ask-poll` invocation, the
  local-interactive vs headless-bridge branch, recommended-option-first ordering, and the mandatory
  literal final option `Other: wait for followup message`.
- Add the row to the `.claude/skill-context/README.md` table.

### 9. Tests
- **Task ID**: build-tests
- **Depends On**: build-vote-observation, build-catchup-transcript, build-ask-me-skill
- **Validates**: all new and updated test files
- **Informed By**: Test Impact and Failure Path Test Strategy sections
- **Assigned To**: `poll-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_ask_poll_cli.py`, `test_poll_payload.py`, `test_poll_vote_translation.py`,
  `test_poll_registry.py`, `test_agent_catchup_poll_transcript.py`.
- Apply every UPDATE listed in **Test Impact**.
- Cover every bullet in **Failure Path Test Strategy**, including the poll-is-not-ephemeral
  dead-letter assertion and the text-fallback error rendering.
- Cover Races 2, 3, 5, and 6 explicitly (double translation, completed-session re-enqueue,
  registry-survives-restart, and provisional-row orphan adoption).
- Run with `scripts/pytest-clean.sh`, never bare `pytest`.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Validates**: the Documentation section checklist
- **Assigned To**: `poll-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Complete every item in the **Documentation** section.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: the Verification table
- **Assigned To**: `poll-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the **Verification** table.
- Confirm every **Success Criteria** box, including that the Task 1 gate output is in the PR
  description.
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

**Note on the steering anti-criterion.** The grep is deliberately narrowed to *key construction*
(`rpush`/`lpush`/`set` applied to a `steering:` literal). The bare-token form returns **2** on a
clean `main` — `bridge/message_drafter.py` contains the log string
`"requesting self-draft via steering: %s"` twice, which is unrelated prose. **Do not "fix" that by
editing those log lines.** Verified: the narrowed grep returns `0` on `5d9729ad8` with a clean tree.

## Critique Results

**Critique cycle 2 (post-revision), 2026-08-10.** Verified against `9de184a3e` with a clean tree.
Cycle 1's 9 findings were dispositioned and are superseded by this table; every cycle-1 anti-criterion
was re-run and now passes on a clean `main`.

War room depth: FULL (3 critics — `appetite: Large` plus doctrine path `.claude/skills-global/`).
Findings: 7 total (1 blocker, 3 concerns, 3 nits).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Task 5 mandates factoring the reply-to routing ladder into `route_answer_to_session(session_id, text, sender)` and asserts "behavior must not change", but the ladder is not a function — it is an inline block inside `handler(event)` (`bridge/telegram_bridge.py:1786-2010`) whose every branch consumes objects a poll vote does not have: `_ack_steering_routed(client, event, message, ...)` branches on `message.media` and calls `set_reaction(client, event.chat_id, message.id, ...)`; the completed branch calls `is_duplicate_message(event.chat_id, message.id)`, `fetch_reply_chain(client, event.chat_id, message.reply_to_msg_id)`, `react_if_worker_down(...)`, and `dispatch_telegram_session(project_key=..., working_dir=...)` built from `project` resolved earlier in the handler. A three-argument signature cannot carry any of that. | pending | Name the real extracted signature in the plan: `route_answer_to_session(session_id, text, sender, *, chat_id, source_msg_id=None, project_key=None, working_dir=None, ack=None)`. The reaction ack and the media branch of `_ack_steering_routed` become a caller-supplied callback (`ack=None` for the poll caller — a vote has no message to react to); completed-branch reply-chain hydration is skipped when `source_msg_id is None`, falling back to the summary-only preamble `_build_completed_resume_text` already handles; `is_duplicate_message(chat_id, source_msg_id)` gets the poll's own `msg_id` from the registry row as its dedup key. Without this, the builder's cheapest path is to drop the completed-session branch for votes — silently re-opening Risk 3, the exact failure the extraction exists to prevent. |
| CONCERN | Risk & Robustness | Risk 5 and the Error State Rendering bullet claim "A terminal failure then re-enqueues the question as plain text" and that a poll send failure "surfaces to the human as the plain-text fallback question rather than silence". The dead-letter path does not do that. `_dead_letter_message` (`bridge/telegram_relay.py:803`) calls `persist_failed_delivery` into the `DeadLetter` model; its only consumer, `replay_dead_letters`, is invoked from a single site in the bridge's connect sequence (`bridge/telegram_bridge.py:2842`). The fallback question therefore reaches the human only on the next bridge restart — for a blocked agent, hours away or never. | pending | Have the poll dispatch branch perform an explicit text re-enqueue on terminal failure (`_relay_attempts >= MAX_RELAY_RETRIES`): `rpush` a plain payload onto the same `telegram:outbox:{session_id}` key, shape `{"type": None, "chat_id": chat_id, "text": question + numbered options, "reply_to": reply_to, "session_id": session_id}` with `_relay_attempts` reset so the text send gets its own retry budget. The loop is `while processed < RELAY_BATCH_SIZE` over `r.lpop` of the same key, so a same-cycle rpush is picked up on a later cycle — no infinite loop. Keeping `"poll"` out of the ephemeral tuple at `:827` and supplying `text` at the `if chat_id and text` gate (`:836`) stays correct, but it is a durability backstop, not the user-visible path. Correct the Risk 5 wording accordingly. |
| CONCERN | Scope & Value | The headline Success Criterion is "An agent question posed at turn end is delivered as a native Telegram poll", but the only trigger built is an agent voluntarily invoking `/ask-me`. Nothing instructs a headless bridge session to run that skill — `grep` over `.claude/skills-global/roles/` and `config/personas/` returns zero references to `ask-me`. The common path is a bare `AskUserQuestion` call or prose, which fires the `needs_human` edge and delivers text exactly as today. Open Question #2 acknowledges this and defaults to "polls are opt-in via the skill", directly contradicting the criterion: all eight subsystems can ship and the criterion still fails in ordinary use. | pending | Cheapest honest fix is wording: narrow the criterion to "A question posed **via `/ask-me`** in a headless Telegram session is delivered as a native poll", and add a No-Go stating the `needs_human` / bare-`AskUserQuestion` turn-end path deliberately still delivers text. For real coverage instead, the hook point is `.claude/skills-global/roles/prime-*-role` (one line directing a blocked headless session to `/ask-me`) or `agent/session_runner/role_driver.py::_reconcile_turn_end`, which already classifies the `needs_human` edge and is the single place that knows a question is being surfaced at turn end. Pick one and name it; do not leave the criterion broader than the build. |
| CONCERN | History & Consistency | Three sections describe the drafter integration three different ways and none names a callable seam. Data Flow says `send_poll` "validates via a new drafter medium"; the Technical Approach says `_compose_structured_draft` "is bypassed either way"; Task 3 says add the medium to `_validate_for_medium` and bypass `_compose_structured_draft`. But `_validate_for_medium` is a private module function called only from inside `draft_message` (`bridge/message_drafter.py:1100`, `:1148`), and `draft_message` unconditionally runs `_compose_structured_draft` at `:1145` before validating at `:1148`. You cannot both "validate via the drafter" and bypass composition through the existing entry point — the plan is directing `agent/output_handler.py` across a module privacy boundary without saying so. | pending | Add a public wrapper in `bridge/message_drafter.py`: `def validate_poll_question(question: str) -> list[Violation]: return _validate_for_medium(question, "telegram_poll")`, and have `TelegramRelayOutputHandler.send_poll` call it directly. Do **not** route through `draft_message` — signature `draft_message(raw_response: str, ...) -> MessageDraft`, composes at `:1145` before validating at `:1148`, so a question passed through it returns with the emoji prefix / stage line / link footer attached. Leaving the private `_validate_for_medium(text, medium)` signature unchanged (as already decided) is compatible with this. |
| NIT | Risk & Robustness | Race 6's orphan adoption binds a surviving provisional row to a recent outbound `MessageMediaPoll` "whose question matches". Two outstanding polls with identical question text in one chat — an agent re-asking after a first poll expired unanswered, or two sessions in the same chat asking the same standard question — make the match ambiguous, with no tie-break given. | pending | Make the match key exact rather than fuzzy: embed the provisional row's `outbox_payload_id` in the poll itself (e.g. in the final `PollAnswer.option` byte payload). Failing that, require the adoption match to be `(question, chat_id, msg_id newer than provisional.created_at, no existing registry row)` AND to bail with a warning when more than one candidate matches, rather than adopting the first hit. |
| NIT | Scope & Value | Task 1 Part B makes the entire build wait on a human physically tapping a probe poll, and the plan says "the build **waits** at the gate" without specifying how the tap is requested, who is notified, or what happens if it never comes. In a headless SDLC run this is an indefinite stall with no producer driving the wait. | pending | Have the Part B probe send the poll with an explanatory caption naming what the operator must do, then surface a legitimate open question through the normal pause path — the nudge loop auto-continues past a status update with no question, so a bare "waiting for tap" note will be skipped. Record a bounded wait: if no vote is readable via `GetPollResultsRequest` after a stated interval, report the gate UNRESOLVED and stop, rather than blocking Tasks 2-11 silently. |
| NIT | History & Consistency | The plan cites #1955 as the precedent that a new outbound shape "needs matching drafter awareness rather than bypassing the comms layer", then designs a path that bypasses composition entirely — the poll question reaches the chat without the drafter's review step. That is the opposite of the cited lesson and conflicts with the recorded doctrine that the drafter review step is the load-bearing comms layer. | pending | One sentence in the Technical Approach: a poll question is a deliberate structured artifact with fixed options, so composition (emoji prefix, stage line, link footer) would corrupt it; the drafter's role for this medium is validation only, and the escape-hatch followup message still goes through the full `draft_message` path. Record it as a named deliberate departure so the #1955 citation is not carrying weight it does not bear. |

### Structural checks (cycle 2, re-run on `9de184a3e`)

All four required sections present and substantive. Tasks 1-11 with no numbering gaps; every `Depends On` resolves to a real Task ID; no cycles; every task now carries a `**Validates**` field. All four prerequisites PASS (`scripts/check_prerequisites.py`). Every cited symbol re-verified present; the three "missing" paths (`tools/ask_poll.py`, `.claude/skill-context/ask-me.md`, `docs/features/telegram-poll-questions.md`) are intentionally new. Every Verification-table anti-criterion re-run on a clean tree returns its expected value, including the narrowed steering grep (`0`) that was cycle 1's BLOCKER — that fix is confirmed good.

---

## Open Questions

**None blocking.** The three questions this feature would normally raise — blocking vs
non-blocking, the escape-hatch wording, and the one-at-a-time rule — are **already settled by
owner decision** and are recorded in the Technical Approach. They are not reopened.

Two judgment calls remain, and each has a chosen default recorded below so the build can proceed
without waiting on a human. They are listed for the critique stage to challenge, not as a gate:

1. **Poll registry TTL.** How long should an unanswered question stay live and reconcilable? A
   short TTL (hours) keeps the registry tiny but abandons a question the human gets to the next
   morning; a long one (7 days, matching `session_root:`) tolerates a slow human but keeps
   reconciling against sessions that are long gone. Under the assumption that an `/ask-me` question
   is answered same-day or not at all, the plan defaults to **24 hours** with the slow
   reconciliation interval, but this is a judgment call about how patient the system should be.

2. **Reaching the CLI when the agent is not running `/ask-me`.** The plan wires polls to `/ask-me`
   only. An agent that poses a question in ordinary prose at turn end (the far more common case,
   via the `needs_human` edge at `runner.py:1526-1531`) still sends text. Should a later pass teach
   the turn-end delivery path itself to detect a question-with-options and render it as a poll, or
   is `/ask-me` deliberately the only entry point so the affordance stays intentional? The plan
   assumes the latter — polls are opt-in via the skill.
