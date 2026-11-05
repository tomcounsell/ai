# Google Meet

Valor in a Google Meet call as itself (docs/plans/meet.md): asked for in a
group chat, it posts the link in that thread, joins ready on the topic,
speaks through Gemini Live (docs/plans/gemini-live.md) when it is
addressed, posts written answers in the call's chat with a copy in the
thread, and when the call ends Gemini summarises the recording and the
asking chat sends that summary there as a file.

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
  `run:<session>`. Everyone's audio is mixed to 16 kHz and streamed to one
  Gemini Live session (`settings.call_model`, voice `settings.call_voice`,
  the key in `google-key`), with notes that ask for no reply: who is
  speaking, who is here (with one other person, all they say is to Valor),
  and the worker's answers. Live decides when Valor is addressed and
  speaks; its tools ask the worker (a fresh `call` turn in the session's
  checkout, on the call's gateway), post, read the open decisions and
  record Tom's read-backs. Each Live turn is one `meet.say`, its audio
  held until released and settled with Live's transcript of what played;
  a typed line is answered in the chat, never aloud, until someone
  speaks. A Live turn of silence ("<no speech>", "...", or near-silent
  audio) is never said. Live itself never cuts Valor off; the call does,
  when the person he answers speaks over him, and side talk does not. With
  one other person in the call, a silence after their words asks Live for
  the answer. Each turn's usage is charged by modality. A Live session
  that goes away, or sends nothing back for 6 s of speech, is resumed; two
  failed reconnects post one line and leave. "Valor, leave" from Tom is
  matched in Live's transcript. A call that fails, or any of its lanes
  (the mixer, Live, the posts), ends and writes its traceback to
  `meet.log`.
- **The page** (`bridges/meet/`): headless Chrome on the call profile
  (a plain Chrome left open on it is quit first; another call's ends this
  one, naming its pid), muted,
  with Attendee's payloads (vendored, ELv2) and `page.js`. Chrome
  runs with its local network checks off, since the payloads' socket to
  `127.0.0.1` is refused from Meet's page otherwise. Each of Meet's media
  connections writes its state to `meet.log` ("media connection 2:
  connected"): audio, the call's chat and who sends which stream come
  over them; who is in the call does not. Live's speech is streamed into
  the virtual microphone a chunk at a time (`valorStream`) and stopped at
  once (`valorStop`, which says how much played).
- **After the call**, the recording (two channels, Valor and the others)
  goes to Gemini (`settings.summary_model`, through the Files API) with
  the transcript; the call is moved whole into Yudame Tools' meetings
  (`settings.meetings_store`) in the app's own files, which the app backs
  up to Drive, and `call-<code>.md`, Gemini's summary, beside them. The
  asking session sends that file unchanged in the thread with at most two
  lines beside it. `valor-record call <code>` reads the transcript.

## Setup per Mac

The call profile signed in once, by Tom at the Mac, in Terminal:
`cd ~/src/ai && .venv/bin/python -m core meet-sign-in` opens Chrome on it at
Google's sign-in, closes that Chrome itself once valor@yuda.me is signed in,
and says so (⌘Q or Ctrl-C gives up; a profile already signed in is left
alone). A sign-in window may be left open, however it was opened: a call
quits a plain Chrome holding the profile before starting its own ([rollout.md](../rollout.md), step 9; the nightly update tells Tom
once a week until it is). Signed in means the account in the profile's
preferences and Google's session cookie in its cookies (`call.account`).
Never start Chrome on the profile over ssh: the keychain is locked there,
and Chrome drops the profile's sign-in (the Captain, 2026-11-03). A call
whose profile another call's Chrome holds (Playwright's
`--remote-debugging-pipe` on its command line) ends saying so, and leaves
it running.

Also per Mac: `google-key` (`python -m core google-key`); `gws` signed in
as Valor; and Yudame Tools signed in to Google, which backs the calls up.

## Not here

- Joining from the calendar unasked; calls from DMs; Zoom and Teams;
  video; two calls at once on one Mac.
