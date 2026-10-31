---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-10-26
tracking: https://github.com/tomcounsell/ai/issues/2902
revision: 10 (Tom's end-user experience, 2026-10-31 02:00; revision 9 was critiqued ready on 2026-10-27)
---

# Valor in Google Meet

**Ask.** Tom, 2026-10-26: Valor joins Meet "just like any other user would
join", answering when addressed; "full duplex is more important than voice
quality"; turn taking "as human-like as possible"; tools via "named worker
agents"; "a decision (Jev-like) model" on whether to speak; quick answers
typed in the chat. 2026-10-27: calls on demand to "discuss all open items",
never mixing clients; one request id per reply, speech never holding up
chat (05:23). His answers (05:58, 06:05): anyone in a group chat (not a
DM) can ask, and the link goes to that chat; the call has that chat's
access; legal and financial work avoided for now, as guidance; any call
covers open decisions once someone asks what is open, after what the
others want to discuss; Tom by Google account, else display name, with
others present his answers confirmed by 👍 in his DM; Meets made with the
Meet API. 2026-10-31 (Telegram team chat, 02:00): asked for a call about a
topic or project, Valor sends the link "and join[s] ready to discuss";
written answers go to the Meet chat "but also a copy of them in the same
chat thread, where the Google meet link was sent"; the full summary goes
"as an attached text document in that thread. You never want to drop a
large body of text into a comms channel".

**Premise, checked 2026-10-27.** `gws meet spaces create` returns the link
and code; Yudame's meetings refuse anonymous guests. Attendee's page
payloads (@ 5acfb99, ELv2, with protobuf.js and pako) give per-speaker
audio, chat, participants (no email decoded) and a virtual mic. The call
profile (`~/Library/Application Support/Valor/meet-chrome`) is signed in
by Tom once per Mac (passkey; the Cowboy's on 2026-10-27, its
`Preferences` naming valor@yuda.me on 2026-10-31). The spike's vendored
payloads and its `playwright`/`kokoro-onnx` deps are kept at the ref
`refs/spike/meet-stash` in this workspace (`^3` holds the vendor files).
A chat session is per chat; a message names its thread (`reply_to`,
`topic_id`), and Slack and Telegram sends take `reply_to` and `files`. Kokoro and
Playwright run on 3.14; Smart Turn v3 (BSD-2) is copied from Pipecat.

## What is built

1. **`meet.join`** (`act`, `tools/meet.py`, offered to group-chat sessions
   only, per Tom): target a Meet URL or `new` (`gws meet spaces create`,
   access open, no invitees: anyone with the link, which goes only to the
   thread asked in, joins without knocking, so no one waits on an admit). Under the advisory lock `meet:<machine>`
   it fails if this Mac's profile is missing (naming the sign-in step) or
   in a call (naming it); else it opens the call session, a root chat
   session (channel `meet`, chat id the meeting code, the asking chat's
   project) with marker `{"sdlc": 1, "call": {"asked_by": {channel,
   chat_id, reply_to, topic_id}, "prep", "effect_key"}}`, writes its first
   `call` report (with the URL) to the asking session, which wakes it to
   post the link in that thread and puts
   the call in its `tasks.reporting`, spawns `python -m bridges.meet join`
   detached, and returns `{url, session_id}`. No report owed. `lookup`
   finds the session by `effect_key`; a space made before a crash is left
   unused (it has no invitees). The payload names the thread (`reply_to`:
   the Slack thread's root, or the Telegram message asked in, with its
   `topic_id`) and `prep`: the asking turn's notes on the topic or project,
   up to 4,000 characters, written after reading the thread, the project
   and its open decisions, so Valor joins ready to discuss.
2. **The call session never runs a turn**: `router.has_step` is false for
   a `meet` chat, and `notices.owe` owes it nothing. A `task.steer` of it
   is posted in the Meet chat by `core/call.py` while the call is open,
   and fails ("the call has ended") after. A `task.stop`, or "Valor,
   leave" heard from Tom (`core/call.py` calls `tasks.stop`), makes the
   bridge leave, seen at once through `LISTEN`. Its Brief's `Room` is the
   asking chat's, so its worker has that chat's access. Its reports go to
   the asking chat's open session, opened if none is (`intake.open_session`
   and `tasks.start`, as `board.owe` does), so none is lost.
3. **The bridge, `bridges/meet/`**: Playwright on the call profile, camera
   off; on joining it posts "Valor here, keeping a transcript for notes",
   and a short line on leaving; the vendored payloads plus one page file of ours (no audio to the
   Mac's speakers). The page's socket goes to the voice process: audio per
   speaker, chat lines and participants in (Valor's own dropped), reply
   audio out. Host for `new`: open access, so anyone signed in with
   the link joins without knocking; as a guest in another's Meet, someone
   else admits Valor. Once it holds the lock `meet:call:<session>` it writes
   `call.started`; it leaves on a stop, removal, lobby denial or 10 min, 2 min
   after the last other person left, 10 min with no one, or the voice
   socket down 30 s. Then it closes Chrome and writes `ended`. A kernel
   wake writes `ended` for an open `meet` session whose lock is free
   after `call.started` (reason crash), or with no `call.started` after
   90 s (reason the bridge never started). `ended` is one transaction:
   `call.ended`, `task.ended` unless the session was stopped, its answers
   (item 6), `effect.refused` for its releases not yet delivered, and the
   report. Its outbox takes only its own session's releases (`Outbox`
   gains a task filter). **Two lanes**: `meet.say` and `meet.send_message` each have
   their own worker, so speech never holds up a post. `DECLARED` gains
   both (`act`, `{"text"}`, `usage` None so turns are not offered them,
   500 characters a post); `_own_chat` gains `meet.say`; `lookup` answers
   None once the call ended, so a dangling one settles `failed`.
4. **Hearing and deciding, `core/call.py`**, in the voice process (one
   cached `_asr()` with a worker of its own for calls, Kokoro, Smart
   Turn). Per speaker: VAD, Smart Turn at each pause, Parakeet; a chat
   line is a finished turn; each finished turn is appended as a quiet
   `call.heard` row, so the transcript survives a restart. At each turn's
   end the `pick` task `meet.speak` (the judgement port as is) asks: asked
   Valor, useful to post, someone else's turn, nothing (floor 0.60;
   abstain and failure are silence). Turn ids are `ledger.new_id()`; an
   answer or draft made stale by newer speech is dropped, a slow fallback
   answer included.
5. **Answering.** A front model (Haiku 5.5, streamed through a
   `core.gateway.Gateway` the voice process runs, holding `run:<session>`
   for the call so a kernel restart charges nothing twice) drafts while Jev
   decides. Its instructions open with the call's `prep`, which the first
   worker question also carries; then: answer who addresses Valor; follow what the
   others want to discuss; avoid legal and financial work, pricing, terms
   and people for now; give the open decisions when someone asks what is
   open. Its tool asks the worker: each question is one
   `runs.run_turn` on the call session, `fresh="call"` with a new
   `skills/sdlc/call.md` (answer in prose, no effects; no `verdict.md`,
   as for `advice`), resuming the
   previous question's harness session, taking no turn slot, in the
   session's checkout (asked once `workspace` is set; before that, Valor
   says it needs a moment), with the asking chat's room; a late answer is posted
   naming its question, or after `ended` goes to the asking thread. Each
   sentence is one `meet.say` (or the reply one `meet.send_message`) with
   `request_id` `meet/<turn id>/<n>/say|post`; a retry resends the same
   payload, so `broker._prior` returns the first. The voice process plays
   a `meet.say` only while its turn is current (else `failed`, stale);
   the say lane holds one waiting. Barge-in by the addressed speaker stops
   playback within 200 ms, settling `done` with `spoken_text`; side talk
   does not. **Thread copies** (Tom, 2026-10-31): each `meet.send_message`
   of an answer or read-back is followed, in the post lane, by its copy, a
   `<channel>.send_message` on the call session to the asking chat with
   the thread's `reply_to` (and `topic_id`), `request_id`
   `meet/<turn id>/<n>/copy`; a copy that fails does not hold up the
   next post. `notices._own_chat` counts the asking chat as a `meet`
   session's own, so a copy owes no report. Speech and the join and leave
   lines are not copied: Tom asked for the written answers.
6. **Open decisions in any call.** Asked what is open, the front model reads
   the project's `board.snapshot` then, and the board's last canvas
   markdown (`board.canvas_markdown`, the `slack.write_canvas` intent's
   payload); with clients present it skips client-relationship items, as
   the board does. When Tom settles items (one idea may settle several),
   Valor reads the answer back and posts it, tagged `{"settles": [question
   ids]}`, split per question past 500 characters; a correction posts
   again and the latest `done` read-back per question counts. At `ended`,
   if Tom was told apart by account, the kernel answers each:
   `session.answer` with the read-back's text, `by` tom, `via` `meet
   call`, `question_id` (refused unless it is the open question). Told
   apart by display name with others present, the read-backs go to Tom's
   Slack DM session (`intake.operator_session` with `operator_channel`
   `slack`, in `docs/rollout.md`; only Slack records reactions),
   which posts them; on his 👍 it requests `task.answer`
   with `question_id`, accepted for a question that call reported to it.
   Refusals are named in the report. The board wake rewrites the canvas.
7. **After the call** (replacing revision 9's short text summary), the
   `call` report carries the read-backs, refusals and the call's chat;
   `valor-record messages meet:<code>` reads its transcript
   (`core/record.py` reads a `meet` chat's `call.heard` rows as its
   messages). The asking session's turn writes the full summary to
   `call-<code>.md` in its workspace (who joined, what was discussed, what
   was settled, what Valor answered and promised, what is still open) and
   sends it as `files` in the thread, with at most two lines of text
   beside it, never the summary as text. The transcript goes to Tom's DM
   if he asks; a promise becomes a task only on his word.
8. **Skill and rollout.** `skills/sdlc/converse.md`: when to request
   `meet.join` (in the thread asked in, after reading the topic for
   `prep`), what to send on a `call` report, how to stop a call.
   `docs/rollout.md`: the profile's sign-in per Mac. Video is its own plan.

**No-Gos.** Joining from the calendar unasked; calls from DMs; Zoom, Teams;
people sharing one mic; two calls at once on one Mac.

**Risks.** Meet page changes break the payloads (re-vendored); crosstalk
mislabels the quieter speaker; Google may challenge the profile.

**Governance.** None added. Group chats only is Tom's scope (05:58), given
by which sessions are offered `meet.join`; the `question_id` match, the
ended-call steer and the refused leftover releases fix misplaced answers
and speech; the DM confirmation is Tom's answer (05:58).

**Stakes.** Valor speaks and posts as itself to people in live calls and
their threads, and the kernel's notices, outbox and answering change, so a
mistake is said aloud or settles a decision wrongly: critique may send the
plan back twice, review the work once.

## Tests and verification

A local page with two `RTCPeerConnection`s stands in for Meet behind our
page file. Hearing: turn ends on recorded speech; three talkers over fewer
streams; Valor's own lines dropped; `meet.speak` routes (speak, post,
silent, abstain, failed, stale, another speaker since); call speech heard
while a voice note transcribes. Replies: a retry is one effect; a say and a
post of the same text are two; a post released during a 5 s say lands
before it ends; a stale queued say is not played; barge-in by the addressed
speaker, not side talk; no re-speak after a voice restart; a late worker
answer posted, or to the asking thread after `ended`. Lifecycle: the call
session runs no turn, a steer is posted in Meet, a stop leaves within 5 s,
also before joining; crash, removal, alone and stop each write `ended`
once; a bridge still starting is not swept, one that never starts is
after 90 s; a steer after `ended` fails and reopens nothing; a stop then
`ended` folds; a kernel restart mid-call charges once; a worker question
waits for the checkout and takes no slot; `ended` reaches the asking
chat after its session closed; no notice goes to a `meet` chat; a release
of an earlier call is not taken. `meet.join`: offered in a group chat, not
a DM; a second call fails naming the first; a missing profile names the
sign-in; a retry after a kill makes one session; the link is posted once, in the
thread asked in (a Slack thread's root; a Telegram reply in its topic);
`prep` reaches the front model and the first worker question. Thread:
an answer post and its copy are two effects and a retry of either is one;
a read-back is copied; speech and the join line are not; a failed copy
does not delay the next post; a copy owes no report. After `ended`: the
transcript reads through `valor-record messages meet:<code>`, and the
asking turn sends one `.md` file in the thread with at most two lines of
text.
Decisions: only that project's questions (another project's never); no
board or canvas still answers; one read-back settling three answers three;
a correction wins; a failed read-back settles nothing; q1 answered by
reaction and q2 asked mid-call: q1 refused and named, q2 open; the Slack DM path
answers on 👍 only (a reaction recorded on the operator DM); `via` `meet call` lands with a new board row. Evals,
not unit tests: the front model follows the others first, skips legal and
financial work, and a client's words settle nothing. Live (`VALOR_LIVE=1`): the profile opens Meet as valor@yuda.me; the account field, lobby and removal signals, an open
Meet joined without knocking, the chat limit, memory, the reply gap.
`uv run pytest -q -n auto`, ruff, `tests/test_bloat.py`.

**Done** when Tom has a real Meet call with Valor in it, Valor joins as
itself and speaks or posts when it is its turn, with a tool where needed,
and Tom records the date with what he saw: barge-in, no unprompted speech
in side talk, the reply delay, which posts were worth reading. For the
board part, when Tom asks for a call, Valor joins, talks through that
project's open decisions, and the waiting tasks get the answers. And
(Tom, 2026-10-31) when Tom asks for a call in a real chat, Valor joins and
discusses, and he gets the written answers copied in that thread and the
summary document there. The first live run reports at once whether Meet
gives Tom's Google account or only his display name, and whether the
profile's sign-in gets Valor in.
