---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-10-26
tracking: https://github.com/tomcounsell/ai/issues/2902
revision: 11 (critique of revision 10; revision 9 critiqued ready 2026-10-27)
---

# Valor in Google Meet

**Ask.** Tom: Valor joins Meet like any user, answers when addressed, full
duplex over voice quality, a Jev-style decision on whether to speak, tools
via workers, quick answers typed in the chat (2026-10-26). Calls from group
chats only, with that chat's access; legal and financial work avoided, as
guidance; open decisions once someone asks what is open; Tom told apart by
Google account, else by 👍 in his Slack DM (2026-10-27). Asked for a call,
Valor posts the link in that thread and joins ready; written answers go to
the Meet chat and a copy to the thread; the full summary is an attached
document, never a large text in the channel (2026-10-31).

**Premise.** `gws meet spaces create` makes an open space. Attendee's page
payloads (@5acfb99, ELv2) give per-speaker audio, chat, participants and a
virtual mic; they sit at `refs/spike/meet-stash^3`. The call profile
(`~/Library/Application Support/Valor/meet-chrome`) names valor@yuda.me.

## What is built

1. **`meet.join`** (`act`, `tools/meet.py`, offered to group-chat sessions
   only). Target a Meet URL or `new` (open access, no invitees). Payload:
   `reply_to` (the Slack thread's root, or the asking message's own ts
   when it was top-level, so link, copies and summary share one thread;
   the Telegram message asked in) with `topic_id`, and `prep`: the asking
   turn's notes on the topic, its project and open decisions, up to 4,000
   characters, written after reading them. Under lock `meet:<machine>` it
   fails naming a missing profile or the call in progress; else it opens
   the call session (root chat session, channel `meet`, chat id the
   meeting code, the asking chat's project and room, marker
   `{"call": {"asked_by": {channel, chat_id, reply_to, topic_id}, "prep",
   "effect_key"}}`), reports the URL to the asking session, which posts it
   in the thread, spawns `python -m bridges.meet join`, and returns
   `{url, session_id}`. `lookup` finds the session by `effect_key`.
2. **The call session runs no turn** (`router.has_step` false for `meet`;
   `notices.owe` owes it nothing). A steer is posted in the Meet chat while
   the call is open and fails after; `task.stop`, or "Valor, leave" from
   Tom, makes the bridge leave at once (`LISTEN`). Its reports go to the
   asking chat's session, opened if closed.
3. **The bridge, `bridges/meet/`**: Playwright on the call profile, camera
   off, posting "Valor here, keeping a transcript for notes" on joining.
   Vendored: the two JS payloads, pako, protobuf and the LICENSE only;
   Attendee's Python is read for selectors, not committed. One page file of
   ours sends per-speaker audio, chat lines and participants (Valor's own
   dropped) to the voice process and plays reply audio. Holding lock
   `meet:call:<session>` it writes `call.started`; it leaves on stop,
   removal, lobby denial or 10 min, 2 min after the last other person, 10
   min alone, or the voice socket down 30 s, then writes `ended`. A kernel
   wake writes `ended` for a session whose lock is free after
   `call.started`, or with none after 90 s. `ended` is one transaction:
   `call.ended`, `task.ended` unless stopped, the answers (item 6),
   `effect.refused` for its undelivered releases, the report. `Outbox`
   takes a task filter. `meet.say` and `meet.send_message` (`act`,
   `{"text"}`, not offered to turns, 500 characters a post) each have a
   worker, so speech never holds up a post; `lookup` is None after ended.
4. **Hearing and deciding, `core/call.py`**, in the voice process: per
   speaker VAD, Smart Turn v3 (copied from Pipecat), Parakeet on its own
   worker; a chat line is a finished turn. Each turn is a `call.heard`
   row. At each turn's end the `pick` task `meet.speak` chooses: asked
   Valor, useful to post, someone else's turn, nothing (floor 0.60;
   abstain and failure are silence). A draft made stale by newer speech
   is dropped.
5. **Answering.** A front model (Haiku 5.5 through the voice process's own
   `Gateway`, holding `run:<session>`) drafts while Jev decides. Its
   instructions open with `prep`; then: answer who addresses Valor, follow
   what the others want, avoid legal and financial work, pricing and
   people, give open decisions when asked. Its tool asks the worker: one
   `runs.run_turn` per question, `fresh="call"` with `skills/sdlc/call.md`
   (prose, no effects, no `verdict.md`), resuming the last, no turn slot,
   in the checkout once set, the first carrying `prep`; a late answer is
   posted naming its question, or after `ended` goes to the thread. Each
   sentence is a `meet.say`, a written reply a `meet.send_message`,
   `request_id` `meet/<turn>/<n>/say|post`, a retry resending the same
   payload. A say plays only while its turn is current (else `failed`,
   stale); barge-in by the addressed speaker stops it within 200 ms,
   `done` with `spoken_text`. **Thread copy:** `core/call.py`, once a
   `meet.send_message` it requested settles `done`, requests on the call
   session a `<channel>.send_message` to the asking chat with the thread's
   `reply_to` and `topic_id`, `request_id` `meet/<turn>/<n>/copy`, and
   does not wait on the copy before the next post. `notices._own_chat`
   counts the asking chat as a `meet` session's own, so a copy owes no
   report. Speech and the join line are not copied.
6. **Open decisions.** Asked what is open, the front model reads the
   project's `board.snapshot` and the last canvas markdown, skipping
   client-relationship items with clients present. Tom's answers are read
   back and posted tagged `{"settles": [question ids]}`; the latest `done`
   read-back per question counts. At `ended`, with Tom told apart by
   account, the kernel answers each (`session.answer`, `by` tom, `via`
   `meet call`, refused unless the question is open); by display name
   with others present, the read-backs go to his Slack DM session and a 👍
   there requests `task.answer`. Refusals are named in the report.
7. **After the call.** `valor-record call <code>` (a `core/record.py` verb)
   reads the call in order: heard turns, Valor's `done` says
   (`spoken_text` when cut short) and posts. `reach` counts a `meet`
   session as inside when its `asked_by` chat is listed for the project.
   The `call` report names the code, read-backs and refusals; the asking
   turn writes `call-<code>.md` (who joined, what was discussed, settled,
   answered and promised, what is open) and sends it as `files` in the
   thread with at most two lines of text. A promise becomes a task only on
   Tom's word.
8. **Skill and rollout.** `skills/sdlc/converse.md`: reading the topic for
   `prep`, requesting `meet.join` in the thread, the summary file, stopping
   a call. `docs/rollout.md`: the profile's sign-in per Mac.

**No-Gos.** Joining unasked; calls from DMs; Zoom, Teams; video; two calls
at once on one Mac.

**Risks.** Meet page changes break the payloads; crosstalk mislabels the
quieter speaker; Google may challenge the profile.

**Stakes.** Valor speaks and posts as itself in live calls and threads, and
notices, outbox and answering change; a mistake is said aloud or settles a
decision wrongly. Critique may return the plan twice, review once.

**Governance.** None added (Tom's scope and answers; fixes, not guards).

## Tests and verification

A local page with two `RTCPeerConnection`s stands in for Meet. Hearing:
turn ends on speech; three talkers over fewer streams; own lines dropped;
`meet.speak` routes (speak, post, silent, abstain, failed, stale). Replies:
a retry is one effect; a post lands during a 5 s say; a stale say is not
played; barge-in by the addressed speaker, not side talk; no re-speak after
a voice restart; a late answer to the thread after `ended`. Copies: a copy
follows only a `done` post, never before it, in the asking thread; a failed
post has none; a retry of a copy is one; a failed copy does not delay the
next post; a read-back is copied; speech and the join line are not; a copy
owes no report. Lifecycle: no turn runs on the call session; a steer is
posted in Meet, fails after `ended`; a stop leaves within 5 s, also before
joining; crash, removal, alone and stop each write `ended` once; a bridge
never started is swept after 90 s; a kernel restart charges once; a worker
question takes no slot; `ended` reaches a closed asking session; another
call's release is not taken. `meet.join`: refused in a DM; a second call
names the first; a missing profile names the sign-in; a retry makes one
session; the link is posted once, in a Slack thread's root, a top-level
Slack message's own ts, or a Telegram reply in its topic; `prep` reaches
the front model and the first worker question. Record: the asking chat's
turn reads `call <code>` with heard turns and a spoken answer in order;
another project's turn does not. The `call` report reaches the asking
session; a send with `files` and `reply_to` in that thread passes. Decisions:
only that project's; no board still answers; one read-back settles three;
a correction wins; a stale question is refused and named; the DM path
answers on 👍 only. Evals: the front model follows the others first, skips
legal and financial work; a client's words settle nothing; the summary
turn sends one `.md` with at most two lines. Live (`VALOR_LIVE=1`): the
profile opens Meet as valor@yuda.me; account field, lobby and removal
signals, open join, chat limit, reply gap. `uv run pytest -q -n auto`,
ruff, `tests/test_bloat.py`.

**Done** when Tom has a real Meet call with Valor in it, Valor joins as
itself and speaks or posts when it is its turn, with a tool where needed,
and Tom records the date with what he saw: barge-in, no unprompted speech
in side talk, the reply delay, which posts were worth reading. For the
board part, when Tom asks for a call, Valor joins, talks through that
project's open decisions, and the waiting tasks get the answers. And (Tom,
2026-10-31) when Tom asks for a call in a real chat, Valor joins and
discusses, and he gets the written answers copied in that thread and the
summary document there. The first live run reports at once whether Meet
gives Tom's account or only his name, and whether the sign-in gets Valor in.
