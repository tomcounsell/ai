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
   (<IANA zone>)" and returns the reset time in UTC. With no date, it takes the
   occurrence of that local time nearest to `at` (within ±12 h; a 5-hour window
   is always nearer), so a retry a few minutes after the reset reads the same
   time and does not jump a day. With a date, it uses that date in `at`'s year.
   Anything else returns None and is an ordinary failure.
   `_failed` writes `until` (ISO UTC) on `step.failed` when there is a match.
2. **Wait, don't burn tries.** `failures()` skips `step.failed` rows that carry
   `until`, so a limit never reaches `STEP_TRIES` or `task.ended`.
   `retry_due` is due at `max(until, at + STEP_BACKOFF_S)` for such a row. This
   holds for every task, so a chat session keeps its unanswered messages and
   held reports (the watch's low-disk report among them), and a build or plan
   resumes in place. A new message during the window still steps at once (the
   limit may have been lifted), fails free, and sends no second notice.
3. **One notice per chat per window.** In `router.step`, after a limit
   `step.failed` on a chat session with `session.chat_waiting` true, request one
   send on that session through `broker.request` with its own performers, as
   `core/call.py` `_effect` does. The request id is `limit/<until>`, and it is
   skipped when the session's rows already hold an effect with that request id.
   - Type: `{channel}.send_message`, a reply to the latest waiting
     `message.steered` (with its `topic_id`), or `email.send` replying in its
     thread (`session._reply_all`'s headers).
   - Text: internal readers get "I'm offline until {T}: I've hit my model usage
     limit. I'll pick this up then." Everyone else gets "I'm offline until {T}
     and will pick this up then." Internal means the operator
     (`intake._from_operator` or `_toms_own`), or a sender whose
     `sender_name` is an exact name or alias in `core.memory.people` with team
     `internal`. With memory unavailable, the sender is treated as external.
   - `{T}` ("2pm Bangkok time", with "on Nov 21" when the reset is on another
     day) is in the operator's zone (`session._zone`) for the operator. For
     everyone else it is in the zone the limit message names, since the record
     holds no other reader's zone.
   - The notice's effect rows must not count as the session's latest row:
     they leave `retry_due`, `tasks.failed_step` and `serve` scheduling as if
     only the `step.failed` were there (`ledger.quiet` reads a `quiet` flag the
     broker writes on that effect's rows).
4. **Unanswered survives any number of ended sessions** (the lost 709b fix,
   simplest form). `notices.unanswered` returns the session's latest owed row:
   its own `message.steered`, or an inherited `ended` report from an earlier
   session of the same chat (`child_id`, same Brief `chat`). For the inherited
   case it recurses to the original message. `owe` checks `_made` before
   computing the text. No 15-minute hold: with item 2, a limit no longer
   ends sessions, so there is no chain to slow.

**Not in scope.** The board summary that is not re-sent when its session ends:
`board.owe` dedupes by digest across sessions, which is a separate code path,
named in the delivery. Restarting the sessions that already ended is handled
elsewhere. No model fallback. No new check, gate or cap. Pre-11-21 rows carry
no `until` and are left as they are.

## Tests

- Parse: both observed forms, "2:30pm", and a zone other than Bangkok. A
  failure just after the reset gives today's reset, not tomorrow's. A date
  near New Year. A non-limit error gives None.
- 6 limit failures in a row: no `task.ended`; `retry_due` is false before
  `until` and true after; the next prompt still carries the waiting message.
  The same holds for a non-chat task.
- Ordinary failure: unchanged backoff, ending at 4 with the `ended` hand-on.
- Notice: once for 3 limit failures plus a new message in the same window. A
  second, later window sends again. The notice's effect rows do not make the
  session due before reset.
- Notice wording and channel types: operator, internal teammate, external,
  memory down; Telegram, Slack, local, email. No notice when nothing waits (a
  session holding only quiet reports) or for a background task.
- Hand-on: a message survives three ended sessions; the text names the
  original message.
