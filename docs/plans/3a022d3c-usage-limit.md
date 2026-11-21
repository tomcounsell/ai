# A usage limit: the chat hears it, and the work resumes at reset

**Ask.** Tom, Telegram DM, 2026-11-21 12:33 UTC: when Valor hits its model
usage limit, send a canned "I'm offline until X" and start again by itself once
the reset passes. At 12:34 UTC he added that anything still unhandled should
be spun back up. Answers, 2026-11-21: only a chat where a person's message is
waiting gets the notice. Background tasks and routines wait and resume
silently. People outside the team are not told the reason. The notice is the
one exception to the 11-01 one-voice rule, because Tom asked for it by name.
It is a fixed sentence with no model: the only local model, Parakeet, turns
speech into text and generates nothing.

**Incident (observed).** Between 2026-11-20 00:24 and 11-21 00:37 UTC seven
chat sessions each failed 4 turns with "You've hit your Fable limit · resets
2pm (Asia/Bangkok)…" (also "resets Nov 21 at 2pm (Asia/Bangkok)") and ended.
Tom's 11-20 14:12 question went unanswered for 22 hours. Task 709b0d649ddb's
fix for unanswered hand-ons ended the same way, and its commit is lost.

## What is built

1. **Recognise and parse** (`core/router.py`). `limit_until(reason, at)`
   matches "hit your … limit · resets [<Mon> <D> at ]<h>[:<mm>]am|pm
   (<IANA zone>)" and returns the reset time in UTC. With no date, it takes
   the first occurrence of that local time after `at - 1 h`, so a retry just
   after the reset keeps the same time. The incident reads correctly: a
   failure at 21:24 Bangkok with "resets 2pm" means 14:00 Bangkok the next
   day (07:00 UTC). With a date, it uses that date: in `at`'s year, or the
   next year when that date is more than a day before `at`. Anything else
   returns None and is an ordinary failure. `_failed` writes `until` (ISO
   UTC) on `step.failed` when there is a match.
2. **Wait, don't burn tries.** In `failures()` a `step.failed` row that
   carries `until` is never counted, and it ends the run of ordinary
   failures before it, as a `PROGRESS` row does. A limit never reaches
   `STEP_TRIES` or `task.ended`, and ordinary failures on either side of a
   limit window never add up (3 before plus 1 after counts 1).
   Such a row is due at `max(until, at + backoff)`. The backoff is the
   existing doubling from `STEP_BACKOFF_S`, capped at `STEP_BACKOFF_MAX_S`,
   over the run of consecutive limit rows. A limit that keeps arriving after
   its parsed reset therefore spaces out (60, 120, 240 s … 15 min) instead
   of firing every minute. This holds for every task: a chat session keeps
   its unanswered messages and held reports (the watch's low-disk report
   among them), and a build or plan resumes in place. A new message during
   the window still steps at once (the limit may have lifted), fails free,
   and sends no second notice.
3. **One notice per chat per window.** In `router.step`, after a limit
   `step.failed` on a chat session, a notice goes out only when a person's
   message is waiting: the fold's pending `message.steered` (`f.steering`),
   or a pending inherited `ended` report from an earlier session of the
   same chat (the incident's second session held only that). The person's
   message is the one `notices.unanswered` resolves through the chain (item
   4). A session holding only other task reports sends none. The notice is one send
   on that session through `broker.request` with its own performers, as
   `core/call.py` `_effect` does. Its request id is `limit/<until>`, and it
   is skipped when the session's rows already hold an effect with that
   request id.
   - Type: `{channel}.send_message`, a reply to that message
     (with its `topic_id`), or `email.send` replying in its thread
     (`session._reply_all`'s headers).
   - Text: internal readers get "I'm offline until {T}: I've hit my model
     usage limit. I'll pick this up then." Everyone else gets "I'm offline
     until {T} and will pick this up then." Internal means the operator
     (`intake._from_operator` or `_toms_own`), or a sender whose
     `sender_name` is an exact name or alias in `core.memory.people` with
     team `internal`. With memory unavailable, the sender is treated as
     external.
   - `{T}` is "2pm Bangkok time", with "tomorrow" or "on Nov 21" added when
     the reset falls on a later local date than the reader's now. It is in
     the operator's zone (`session._zone`) for the operator. For everyone
     else it is in the zone the limit message names, since the record holds
     no other reader's zone.
   - **Every row the notice writes is quiet.** `broker.request` writes
     `effect.held` and `release.requested`; the bridge's `release` later
     writes `effect.intent`, `effect.outcome` and `effect.refused`. Each
     carries `quiet: true`, which `ledger.quiet` already reads. The broker
     does not carry such a flag today: `broker.request` gains a `quiet`
     argument written on `effect.held` and `release.requested`, and
     `_intent`, `_outcome`, `_refused_release` and `release`'s refusal copy
     it from the held row onto `effect.intent`, `effect.outcome` and
     `effect.refused`. None of them
     raises `latest` in `serve`, `retry_due` or `tasks.failed_step`, so
     the session stays asleep until its reset.
   - **The resumed turn knows.** `session.next_prompt` adds one line when a
     limit notice was sent since the last collected turn: "While your model
     usage limit held, the chat was told: <text> (to message N)". The turn
     answers the waiting message without repeating that notice.
4. **Unanswered survives any number of ended sessions** (the lost 709b fix,
   simplest form). `notices.unanswered` returns the session's latest owed
   row: its own `message.steered`, or an inherited `ended` report from an
   earlier session of the same chat (`child_id`, same Brief `chat`). For
   the inherited case it recurses to the original message. `owe` checks
   `_made` before computing the text. No 15-minute hold: with item 2, a
   limit no longer ends sessions, so there is no chain to slow.

**Not in scope.** The board summary that is not re-sent when its session ends:
`board.owe` dedupes by digest across sessions, which is a separate code path,
named in the delivery. Restarting the sessions that already ended is handled
elsewhere. No model fallback. No new check, gate or cap. Pre-11-21 rows carry
no `until` and are left as they are.

## Tests

- Parse: both observed forms. The incident case: a failure at 2026-11-20
  14:24 UTC with "resets 2pm (Asia/Bangkok)" gives 2026-11-21 07:00 UTC. A
  retry 10 minutes after a reset keeps that reset. "2:30pm", a zone other
  than Bangkok, a date across New Year, and a non-limit error (None).
- 6 limit failures in a row: no `task.ended`; `retry_due` is false before
  `until` and true after. Three limit failures after `until` are spaced 60,
  120 and 240 s. The same holds for a non-chat task.
- Ordinary failure: unchanged backoff, ending at 4 with the `ended`
  hand-on. 3 ordinary failures, a limit window, then 1 ordinary failure
  after the reset: the task does not end (the count is 1).
- Notice: once for 3 limit failures plus a new message in the same window.
  A later window sends again. No notice for a session holding only a
  non-quiet task report (not an inherited `ended`), or for a background
  task. A session holding only an inherited `ended` report (the incident's
  2a14f74c4cd6 shape) sends one notice, replying to the original message. The quiet test runs a
  bridge `release` through to `effect.outcome`, and to a refusal, and the
  session is still not due before the reset.
- Wording and channels: operator, internal teammate, external, memory
  down; "tomorrow" when the reset is on the next local day; Telegram,
  Slack, local, email.
- Resume: after `until`, the session is due, its prompt holds the waiting
  message and the line naming the sent notice, and a successful turn
  clears it.
- Hand-on: a message survives three ended sessions; the text names the
  original message.
