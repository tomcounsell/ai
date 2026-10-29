---
status: built (2026-10-27); boards, chat and reaction answers waiting on Tom's use; calls wait on Meet
type: feature
appetite: Medium
owner: Valor
created: 2026-10-27
revision: 2 (critique rounds 1 and 2; Tom 03:28)
---

# Decision board: a canvas per project channel

**Ask.** Tom, valor team chat (Slack), 2026-10-27 03:02-03:28 UTC: each
project's channel holds a canvas, "more like a todo board that can be
reorganized and evolve", of the decisions open for him; "don't mix decisions
across clients"; Meet calls on demand. **The bar moved in his words** (03:24,
ts 1793071445.378019): "In practice, I won't do work on the board. I'll chat
with you or call you, or leave emoji reactions." So the request's "answers or
reorganises a real decision on a project's canvas" is dropped by Tom for the
done line below, and reading canvas edits is not built. 03:26: the ledger is
the record, the canvas a view. 03:28: "confirm that you can get a push
notification handled by my emoji reaction ... valuable if we can have it in
Slack."

**Premise, checked.** A question is `question.asked`; its answer
`question.answered` (`session.answer`, `by`, `via`). Its notice goes to the
parent session as `child.reported` (`core/notices.py`); `task.answer`
(`tools/chat_tasks.py`) answers only a task in `tasks.reporting`. Each dev
channel is a team chat with one session on its project's Mac, each Mac on its
own Slack app (`docs/bridges/slack.md`). The Slack bridge writes reactions but
receives none: no `reactions:read`, no `reaction_added`, no `canvases:write`.
Not checked live: that the Yudame plan allows `conversations.canvases.create`.

## What is built

**First, live, before the rest:** add `reactions:read`, `canvases:write` and
`reaction_added` to the manifest, update and reinstall the Cowboy's app from
Valor's account, then (a) a real 👍 from Tom on a post of Valor's in
`#valor-dev` reaches the bridge (log line, recorded row), and (b)
`conversations.canvases.create` makes a canvas in `#valor-dev` (a private
channel). Both results lead the delivery; if (b) fails, the build stops there
and the delivery says what the plan allows.

1. **`board` in each spec**: `board = "slack:<team>/<channel>"`, its dev
   channel, in the ten specs that list one (litware waits on a Mac). The board
   session is that chat's session, on the Mac owning it.
2. **The wake, `core/board.py`**, beside `notices.owe` (`core/serve.py`). The
   snapshot: each open question of the project's tasks only (task, request,
   question, asked at, and the ts of the board post made for it, if any) and
   answers of the last 14 days (text, by, via, at). Its digest leaves out the
   canvas link and post ts. When it differs from the digest on the **latest**
   `board` row, write `child.reported` on the board session (started when none
   is open, as `operator_session` does), kind `board`, no `child_id`,
   `about_key` `board:<row id>`, payload `digest`, text the snapshot plus the
   last canvas link. `tasks.reporting` skips rows with no `child_id`;
   `session.py` renders it as "The decision board changed:" and the snapshot.
3. **One post per question, one place.** A question whose report goes to the
   board session (a task started in the board channel) gets no `question`
   report there; the board wake carries it, and the session posts one
   top-level message for it: the question and Valor's recommendation, with
   `slack.send_message` payload `about: "question:<id>"` (stored, ignored by
   the bridge), so the snapshot knows the post, unless the session is already
   asking Tom that question in the chat (a thread or a reply): then it gets
   no board post, the canvas line points there, and a 👍 on that message
   answers it (Tom, 2026-10-29: "you're double asking me here and in the
   thread"). A question relayed elsewhere
   (Tom's DM, the operator session) gets no board post: its relay there is its
   one message and reaction target; the canvas lists it, marked "asked in your
   DM".
4. **The canvas, `slack.write_canvas`** (`act`, target `<team>/<channel>`,
   payload `{"markdown"}`): `conversations.info`; no canvas, then
   `conversations.canvases.create`; else `canvases.edit` replacing the whole.
   Result `{canvas_id, url}`; lookup re-performs; no report owed (`_own_chat`).
5. **Reactions in.** `reaction_added` by Tom (`VALOR_OPERATOR_SLACK_IDS`) on
   Valor's own post in an owned chat is a record: `message_id`
   `<item ts>/<name>`, `reply_to` and `topic_id` the post's ts, text
   `:<name>:`, `reaction` the name. It binds by the table (a team chat: steer).
   `reactions.on_bound` owes none for a record with `reaction`; `chat_line`
   renders it "Tom reacted :+1: to your post <ts> (about question <id> of task
   <t>)", the post being the anchor. Its thread is the post's, a real one. It
   wakes the session wherever it lands (DM, team chat, a delivery); a reaction
   on a post that is no open question gets no reply and no effect.
6. **`task.answer` from the board session.** It also takes a task of the
   session's project with an open question, only when the session is that
   project's board session; payload gains `via` (default `chat`), e.g.
   `slack reaction :+1:`.
7. **`skills/sdlc/converse.md`, a board section.** Rebuild the canvas on each
   `board` report: Open, most blocking first, related items grouped, each with
   what is decided, why now, context, recommendation, options; Decided; the
   legend: 👍 (`+1`, palette `thumbs_up`) on a question's post answers with
   that post's recommendation, quoted ("Accepted: <recommendation as
   posted>", `via` `slack reaction :+1:`), never the canvas's later wording;
   👎 (`-1`) rejects it and keeps it open for chat or a call; a chat reply
   answers. Everyone in the channel reads the canvas and the posts: on a
   channel with anyone but Tom and Valor, a client-relationship question or
   answer (pricing, terms, its people), on the canvas, in a post or under
   Decided, is one line pointing to Tom's DM, and so is anything asked or
   answered in his DM. A decision only Tom holds
   with no task waiting becomes a task whose question it is.
8. **Rollout and docs.** Scope `canvases:write` too; the other three apps
   updated and reinstalled, noted in `docs/rollout.md` beside the
   `search:read` reinstall; `docs/bridges/slack.md` updated. Before the first
   write the builder lists each board channel's canvas and its members other
   than Tom and Valor, and names in the delivery any canvas with others'
   content (not overwritten) and any channel with other members.

**Calls wait on Meet** (`docs/plans/meet.md`, planning). Once Valor can join,
the board session starts a call task (Brief chat `meet`, one project) with its
snapshot as the agenda; answers go back by `task.answer`, `via` `meet call`.
Nothing of the call part is built here.

**Out of scope.** Canvas edits (dropped, above); #728's wiki; the memory
Postgres; Telegram reactions. In the doc's Gaps: reactions made while the
bridge is down (gap fill reads no reaction events), and `reaction_removed`
(Tom undoing a 👍 after it was passed on is not seen; he says so in chat).

**Governance.** None added. Item 6 widens a refusal for one session; item 3
moves where a notice goes; nothing new refuses.

## Tests

- Snapshot: only its project's questions (northwind's never on valor's); answered
  or stopped leave Open, answers enter Decided; open, then stop, back to the
  earlier digest writes a row each time; a new canvas link or post ts alone
  writes none; no `board`, or another Mac's: nothing; no session: started.
- A board row renders as the board line, not "Your task None"; `reporting`
  holds no None.
- A dev-channel task's question: no `question` report on the board session,
  the board row lists it with no post; a DM-started task's still reports to
  the DM session, and the board row lists it marked as asked in the DM.
  A send with `about` puts its ts on that question in the next snapshot.
- `task.answer`: the board session answers a same-project task not reporting,
  `via` recorded; another chat session of the same project is refused; another
  project's task, or one with no open question, is refused.
- Skill examples in `converse.md`: a 👍 answer quoting the post's
  recommendation; a DM-asked pricing question shown as a pointer line on a
  channel with a client member.
- Reactions: Tom's on Valor's post records once (a redelivery lands once),
  steers the session, owes no `reaction.requested`, and `threads` lists only
  the post's ts; anyone else's, on another's post, in an unowned chat: none.
- `slack.write_canvas`: creates, replaces, Slack's refusal `Failed`, lookup
  re-performs, no report. The manifest has the scopes and event.

**Done when** Tom answers a real decision by chat or reaction (a reaction with
no message from him), the waiting task gets it, and the board shows it; for
calls, when he asks for one and Valor joins and talks through that client's
open items (after Meet joining lands).
