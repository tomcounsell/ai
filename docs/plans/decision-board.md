---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-10-27
---

# Decision board: a canvas per project channel

**Ask.** Tom, valor team chat (Slack), 2026-10-27 03:02-03:26 UTC: each
project's Slack channel holds a canvas, "more like a todo board that can be
reorganized and evolve", of the decisions open for him on that project; "don't
mix decisions across clients"; Meet calls on demand to talk them through.
03:24: "I won't do work on the board. I'll chat with you or call you, or leave
emoji reactions." 03:26: the ledger is the record; the canvas is a view.

**Premise, checked.** A task's question is a `question.asked` row; its answer
a `question.answered` (`session.answer`, with `by` and `via`). The notice goes
to the task's parent session as `child.reported` (`core/notices.py`), which
relays it in its chat; `task.answer` (`tools/chat_tasks.py`) answers only a
task in `tasks.reporting`. Every Slack dev channel is a team chat with one
session on its project's Mac, each Mac on its own Slack app
(`docs/bridges/slack.md`), so the per-Mac question is settled. No bridge
receives reactions today; the Slack manifest has no `reactions:read`, no
`canvases:write`, no `reaction_added`. Not yet checked live: that the Yudame
plan lets `conversations.canvases.create` make a channel canvas (Slack's error
`free_team_canvas_tab_already_exists` suggests free teams get one per channel).

## What is built

1. **`board` in each spec.** `board = "slack:<team>/<channel>"`, the project's
   dev channel, in the ten specs that list one (satsol's waits on a Mac).
2. **The wake, `core/board.py`.** Beside `notices.owe` (`core/serve.py`): when
   the project's set of open questions or its last 14 days of answers changes,
   write a `child.reported` row, kind `board`, `about_key` the snapshot's
   digest, on the board chat's session (started when none is open, as
   `operator_session` does), only on the Mac that owns the board chat. Its text
   is the snapshot: each open question (task, its request, the question, asked
   at) and each answer (text, by, via, at), from that project's tasks only, and
   the canvas link from the last write. No new row type, no store.
3. **The canvas, `slack.write_canvas`** (`act`, target `<team>/<channel>`,
   payload `{"markdown"}`). The bridge reads `conversations.info`: no canvas,
   `conversations.canvases.create`; else `canvases.edit` `replace` of the whole
   document. Result `{canvas_id, url}`. Lookup re-performs (a whole replace is
   idempotent). A session's write to its own chat owes no report (`_own_chat`).
4. **Reactions in.** `reaction_added` from Tom (`VALOR_OPERATOR_SLACK_IDS`) on
   Valor's own post in an owned chat is a record: text `:<name>:`, `reply_to`
   the post, `message_id` the event's `ts`. It binds by the existing table
   (steer to a team chat's session; in Tom's DM, an answer to the notice).
5. **Answering from any chat of the project.** `task.answer` also takes a task
   of the session's own project with an open question; payload gains `via`
   (default `chat`), e.g. `slack reaction :+1:`.
6. **`skills/sdlc/converse.md`, a board section.** On a `board` report:
   rebuild the canvas from the snapshot (Open, most blocking first, related
   items grouped, each with what is decided, why now, context, recommendation,
   options, status; Decided; the legend); for each newly opened question post
   one short top-level message in the channel linking the canvas. 👍 (`+1`,
   palette `thumbs_up`) on it answers "accept the recommendation"; 👎 (`-1`)
   marks it rejected and it stays open for a chat or a call; a chat reply is
   the answer. A decision only Tom holds with no task waiting becomes a task
   whose question it is.
7. **Manifest and rollout.** Scopes `canvases:write`, `reactions:read`; event
   `reaction_added`; the four apps updated and reinstalled from Valor's
   account; `docs/bridges/slack.md` updated.

**Calls wait on Meet** (`docs/plans/meet.md`, planning). Once Valor can join,
the board session starts the call task (Brief chat `meet`, one project) with
its snapshot as the agenda, and answers go back by `task.answer`, `via`
`meet call`. Nothing of the call part is built here.

**Out of scope.** Reading Tom's canvas edits; #728's wiki; the memory
Postgres; Telegram reactions; reactions made while the bridge is down (gap
fill reads no reaction events; named in the doc's Gaps).

**Governance.** None added. Item 5 widens a refusal; nothing new refuses.
A channel whose canvas already holds someone else's content is not
overwritten: the builder lists the board channels' canvases first and names any
such channel in the delivery.

## Tests

- Snapshot holds only its project's questions (cyndra's never on valor's);
  unchanged set writes nothing; answered and stopped questions leave Open,
  answers enter Decided; no `board`, or a board chat owned by another Mac:
  nothing; no session: one is started.
- `task.answer` on a same-project task not reporting works, with `via`; another
  project's task is refused; a task with no open question is refused.
- Reactions: Tom's on Valor's post is a record that steers the session; anyone
  else's, on someone else's post, in an unowned chat, or Valor's own: none.
- `slack.write_canvas`: creates when the channel has none, replaces when it
  has one, Slack's refusal is `Failed`, lookup re-performs, no report owed.
- The manifest carries the scopes and the event; the session prompt renders the
  `board` report.

**Done when** Tom answers a real decision by chat or reaction, the waiting task
gets it, and the board shows it; for calls, when he asks for one and Valor
joins and talks through that client's open items (after Meet joining lands).
