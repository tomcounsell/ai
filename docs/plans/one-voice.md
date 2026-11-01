# One voice: only a chat session speaks as Valor

**Why.** On 2026-10-31 the nightly Sentry routine's report went to Tom's Slack DM as kernel text, not from Valor's session there. Tom replied twice in its thread ("Why did you send me this big mess?", "Why are you telling me this? I didn't ask for this."); each reply bound as feedback, reopened the run, and it re-sent a shorter report. Nobody answered him. Tom, 2026-11-01: "No routine or system should be impersonating you." "It's truly a matter of trust. How can I trust anything coming from you if something else might be able to speak for you without your permission." He asked for this path rewritten from scratch.

**Causes.** (1) `notices.reports_to` gives a session only a session's child or a root; a routine run is a child of its objective, so its `ended` fell to `home()` and the bridge posted it. The same direct path carries `TOMS` (credential, binding, rollout), disk-low, send-in-doubt, `/update` replies and a session's own `ended`. (2) In Tom's own chat `intake._bind` sends a reply that resolves to a non-session task through the reply table: under an ended task's notice, feedback. (3) A session never sees the post a reply is under: `chat_line` shows only "replying to <ts>".

**The rule.** Every message reaching a person under Valor's name is one a chat session wrote and sent. Everything else reports to a session, which decides what anyone hears.

## What is built

1. **One report path.** `notices.request` writes one `child.reported` row on a session; no row a bridge sends. Payload `from` (the task or stream id), `kind`, `about_key`, `text`, and `child_id` = `from` only for the kinds that reached sessions before (question, ended, report); the former direct kinds and streams carry `child_id` null, so `tasks.reporting` and `stop` reach exactly what they reach today (a task waiting on a push credential is not newly stoppable; nothing calls `tasks.stop` on `updates` or a channel id). Dedupe is one row per (`from`, `about_key`) across all sessions, read in the row's own transaction; `serve.UNREPLIED` keys on it, so each `/update` gets one report. The session: nearest ancestor that is a chat session; a `/update` reply, the session of the room asked in; else the operator session (`intake.operator_session`). A session's own `ended` goes to its chat's next session only when it left a message unanswered (`unanswered`); the next session holds that as a report, not a message, so if it too ends its notice stays on its ledger. With no session to take it, the row stays on the reporting task only. Deleted: `TOMS`, `home()`, writes of `notice.requested`/`notice.sent`/`notice.undeliverable`, `NoticeDue` and every bridge's `notice()` (telegram, slack, email, local). Old rows stay readable as history: `_replied`/`_SENT` and the local page keep reading `notice.sent`.
2. **Routine results are the record.** A report from background work (`tasks.background`) other than a `question` carries `quiet: true`. `ledger.quiet` and `session.chat_waiting` read that flag, as they treat `message.heard`: no new row type, it wakes no turn and does not unpark (`serve._ready` reads `ledger.quiet`). The session's next prompt shows it as "a routine result, held in the record". Every routine with a task is under `valor`, Tom's DM session's project, so any later session finds it with `valor-record tasks --search` (the run's latest delivery summary); the chat-session text says so. A routine's question wakes the session. An issue a routine files is never a message to Tom.
3. **Tom's own chat is the session's, whole.** Every message of his there binds to its session, thread replies included; `stop` stays immediate. Revision requests reach the task through `task.steer` (feedback on a delivered task) and answers through `task.answer`. The table's notice rows (merge report, ended task, question notice) go for Tom's own chat. At bind, `_replied`'s post text (the notice's, or the send's from `effect.held`) is stored on the steered row as `replying_to_text`, and `chat_line` shows it quoted, cut to 500 characters.
4. **Words.** `routines/sentry/routine.toml` drops "Send Tom nothing."; `skills/sdlc/converse.md` and the chat-session text: a routine result is not news; a thread reply is Tom talking to you. Docstrings of `core/notices.py`, `core/intake.py`; `docs/routines.md`, `docs/bridges/*.md`, `docs/architecture.md`, `docs/data.md`.

## Out of scope, named in the delivery

A task's own send effects still let a non-session task post as Valor (the `invoices` routine's client emails; any `act` task); removing them is a capability removal under rule 3, listed for Tom. Kernel emoji reactions are not messages. The Sentry routine still cannot start fix tasks.

## Decisions Tom may want to change

Disk-low, mixed-code rollout, push credential and send-in-doubt now reach Tom only through a session turn. They wake the session, but the faults most likely to stop turns (full disk, half-rolled kernel) can silence them.

## Tests (incident first)

- Incident: a sentry run ends `Done: filed #N`; no bridge item, a quiet report on the operator session, `chat_waiting` false, the scheduler starts no step, the next prompt labels it. Tom's two thread replies under the legacy report (`notice.sent` row) bind `steer` to his DM's session, whose prompt carries the report's text; the run gets no feedback, no re-delivery.
- After the session ends, a new session's `record.task_list` search finds the run's summary.
- A routine's question, a foreground `ended`, a merge report, credential, rollout, disk-low, send-in-doubt each wake the session.
- Grandchild of a session reports to it; `/update` reports once to its room's session over several ticks.
- Chain: a session ended with a message unanswered reports to the next; that one ending on the report alone writes no further report.
- `stop` after a stream report and a credential report: no raise, stops only what it stopped before. A reply under a session's own send steers it with that send's text.
- No bridge yields a notice item; tests asserting `notice.requested` move to `child.reported`. Suite under 5 minutes; `test_bloat` green.

Stakes: kernel binding and report rows, so 2 critique rounds and 1 review round. No governance added: it removes a sending path and a binding rule, and adds no check.
