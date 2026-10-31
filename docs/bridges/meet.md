# Google Meet

Valor in a Google Meet call as itself (docs/plans/meet.md): asked for in a
group chat, it posts the link in that thread, joins ready on the topic,
listens to each person apart, answers aloud when it is addressed, posts
written answers in the call's chat with a copy in the thread, and when the
call ends the asking chat sends the full summary there as a file.

**Status.** Built: `meet.join` (`tools/meet.py`), the call session and its
end (`core/call.py`), the call's process (`python -m core call SESSION`),
and the page (`bridges/meet/`). Live-checked on the Cowboy on 2026-10-31
against a new Meet with a second device of Valor's account
(`tests/test_live_meet.py`).

## What it serves

| Mechanism | Serves |
|---|---|
| `meet.join` offered to group chats only; the call has the asking chat's project, room and ceiling | Tom's scope (2026-10-27): anyone in a chat group, never a DM, with that chat's access |
| Every word said or posted is a `meet.say` or `meet.send_message` effect on the call's session; each written answer's thread copy is the chat's own send | Constraint "Bounded authority, metered spending"; Tom (2026-10-31): written answers in the call and in the thread |
| The call's end written once, by its process or by the kernel's sweep | Constraint "Reliable stop, recovery, and correction" |
| Tom's read-backs answer his decisions on his 👍 in his Slack DM, since Meet gives display names only (live run, 2026-10-31) | Tom's answers of 2026-10-27 |

## The pieces

- **`meet.join`** (`act`), a group chat session's: target a Meet link or
  `new` (`gws meet spaces create`, open to anyone with the link), payload
  the thread asked in (`reply_to`, `topic_id`) and `prep`, the session's
  notes on the topic. It opens the call session (channel `meet`, chat id
  the meeting code) and reports the link to the asking session, which
  posts it in the thread, then starts the call's process.
- **The call's process** holds `meet:call:<session>` and
  `run:<session>`; its gateway meters the front model and the worker on
  the call session. It hears each person apart (Silero VAD, Smart Turn
  v3, Parakeet through the voice process's `/hear`), asks `meet.speak`
  whether to answer each turn, drafts with the front model (a Claude Code
  session on the light seat, streaming, no tools: `ASK:` and `DECIDED:`
  lines ask the worker and record Tom's decision), voices with Kokoro,
  and asks the worker (a fresh `call` turn in the session's checkout) what
  needs looking up. The person answered talking over Valor stops it.
- **The page** (`bridges/meet/`): headless Chrome on the call profile,
  muted, with Attendee's payloads (vendored, ELv2) and `page.js`. Chrome
  runs with its local network checks off, since the payloads' socket to
  `127.0.0.1` is refused from Meet's page otherwise.
- **After the call**, `valor-record call <code>` reads its transcript a
  page at a time; the asking session writes `call-<code>.md` and sends it
  in the thread with at most two lines beside it.

## Setup per Mac

The call profile signed in once, at the Mac ([rollout.md](../rollout.md),
step 9); the voice job running with `/hear` (kickstart `com.valor.voice` once
after this change); `gws` signed in as Valor; and Smart Turn v3
fetched with the voice models (`python -m tools.voice fetch`).

## Not here

- Joining from the calendar unasked; calls from DMs; Zoom and Teams;
  video; two calls at once on one Mac.
