# One voice: only a chat session speaks as Valor

**Why.** On 2026-10-31 the nightly Sentry routine's report went straight to Tom's DM, written by the kernel, not by Valor's session there. Tom replied twice in its thread ("Why did you send me this big mess?", "Why are you telling me this? I didn't ask for this."); each reply was bound as feedback, reopened the routine's task, and it re-sent a shorter report. Nobody answered him. Tom, 2026-11-01: "No routine or system should be impersonating you." "It's truly a matter of trust. How can I trust anything coming from you if something else might be able to speak for you without your permission." He asked for this path rewritten from scratch.

**Causes, in the code.** (1) `notices.reports_to` sends a task's notices to a session only when its parent is one, or it is a root. A routine run is a child of its objective, so its `ended` notice fell to `home()` and the bridge posted kernel text to the operator chat. The same direct path carries `TOMS` (credential, binding, rollout), the disk-low notice, send-in-doubt, `/update` replies and a session's own `ended` notice. (2) In Tom's own chat, `intake._bind` routes a reply that resolves to a non-session task through the reply table, so a reply to an ended task's report is feedback.

**The rule.** Every message that reaches a person under Valor's name is one a chat session wrote and sent. The kernel and the tasks under it report to a session; the session decides what, if anything, a person hears.

## What is built

1. **One report path.** `notices.request` always writes `child.reported` on a session and never a row a bridge sends. The session is the nearest ancestor that is a chat session; else the operator session (`intake.operator_session`). A session's own notice goes to its chat's next session (`open_session`). A `/update` reply goes to the session of the room it was asked in. Stream notices (`updates`, binding) go to the operator session. With no session to take it, the report stays on the task's ledger only. `TOMS`, `home()`, `notice.requested`, `notice.sent` and `notice.undeliverable` go, with every bridge's notice sender (`NoticeDue`, `notice()` in telegram, slack, email, local; the local page's notice rows).
2. **Routine results are the record, not a message.** A report from background work (`tasks.background`) is written `quiet`: it wakes nothing (`session.CHAT_WAKES`) and is shown to the session at its next turn, labelled as a routine result held in the record, to answer from when Tom asks. A routine's `question` still wakes the session. An issue a routine files is never a message: it is in the record and on GitHub. The session still reads older results by searching the record (`valor-record memory`).
3. **Tom's own chat is the session's, whole.** Every message of Tom's there binds to the chat's session, a reply under any post included, so a question in a thread is answered. A revision request the session passes on with `task.steer` (feedback on a delivered task) and an answer with `task.answer`; `stop` stays immediate. The reply-table rows for notices (merge report, notice of an ended task, question notice) go; replies to a task's own send outside Tom's own chat keep their row.
4. **Words.** `routines/sentry/routine.toml` drops "Send Tom nothing." `skills/sdlc/converse.md` and the chat-session text: a routine result is not news; a reply in a thread is Tom talking to you. Docs: `core/notices.py`, `core/intake.py` docstrings, `docs/routines.md` (Attention, How results surface), `docs/bridges/*.md`, `docs/architecture.md`, `docs/data.md`.

## Out of scope, named in the delivery

A task's own send effects (`slack/telegram.send_message`, `email.send`, canvas) still let a non-session task post as Valor: the `invoices` routine emails clients, and any `act` task can. Removing them is a capability removal under rule 3 and needs Tom's grant; the delivery lists them for him. Kernel emoji reactions on received messages are not messages. The Sentry routine still cannot start its fix tasks (a turn cannot start a task); a product note.

## Tests (incident first)

- Incident replay: a sentry run (child of its objective) ends `Done: filed #N`; no bridge row exists, the operator session holds a quiet report, `chat_waiting` is false, and its next prompt shows it labelled. Then Tom's two thread replies under the legacy report (a `notice.sent` row) each bind `steer` to his DM's session; the run gets no feedback and does not re-deliver.
- A routine's question wakes the session; a foreground task's `ended` and a merge report wake it.
- A grandchild of a session reports to that session; a child of a non-session task under no session reports to the operator session.
- Credential, rollout, disk-low, send-in-doubt and a bind failure report to the operator session; a `/update` reply to its room's session; a session's own ended-unanswered notice to its chat's next session; no operator session leaves only the task's row.
- In Tom's own chat, an exact `stop` replying to a task's post still stops what reports to the session; a reply under a session's own send steers it.
- Each bridge's outbox yields no notice item; tests that asserted `notice.requested` move to `child.reported`.
- Full suite under 5 minutes; `tests/test_bloat.py` green with the removed code gone.

Stakes: kernel binding and the ledger's report rows, so 2 critique rounds and 1 review round. No governance is added: the change removes a sending path and a binding rule, and adds no check.
