# Google Meet

Valor in a Google Meet call as itself (docs/plans/meet.md): asked for in a
group chat, it joins ready on the topic, posts the link in that thread once
it is in,
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
  the meeting code), reports to the asking session that Valor is joining
  (naming the call session, so the chat can stop or steer it, with no
  link) and starts the call's process. Neither that report nor the
  request's result carries the link: it reaches the asking session only in
  the `link` report the call's process writes once Valor is in
  (`call.entered`, with `call.joined`), and a call Valor never got into
  ends saying why and not to post the link (Tom, 2026-11-13: "don't share
  a meet link if it doesn't work!").
- **The call's process** holds `meet:call:<session>` and
  `run:<session>`. Everyone's audio is mixed to 16 kHz and streamed to one
  Gemini Live session (`settings.call_model`, voice `settings.call_voice`,
  the key in `google-key`), with notes that ask for no reply: who is
  speaking, who is here (with one other person, all they say is to Valor),
  and the worker's answers. Live decides when Valor is addressed and
  speaks; its tools ask the worker (a fresh `call` turn in the session's
  checkout, on the call's gateway), post, read the open decisions,
  record Tom's read-backs and, when the project's spec names a backlog
  Sheet in its rooms, read it (`backlog`: `sheet.py lookup` on how
  someone described an item, or the whole Queue in order). Each Live turn is one `meet.say`, its audio
  held until released and settled with Live's transcript of what played;
  a typed line is answered in the chat, never aloud, until someone
  speaks. A Live turn of silence ("<no speech>", "...", or near-silent
  audio) is never said. Live itself never cuts Valor off; the call does,
  when the person he answers speaks over him, and side talk does not. With
  one other person in the call, a silence after their words asks Live for
  the answer. Each turn's usage is charged by modality. A Live session
  that goes away, or sends nothing back through 6 s of speech and the 6 s
  of quiet after it, is resumed (a long turn, however little Live sends
  during it, is not; nor is a short utterance), the old session closed
  before the next opens. A resume that fails to open (Live refusing its
  handle as still connected, or not found or expired) opens a fresh
  session, told that Valor is already in the call,
  who is here, and the newest of the transcript (`CALL_SO_FAR` characters).
  Two failed fresh opens post one line and leave. Nothing ends a call at a
  set time: Valor stays while anyone else is in it. "Valor, leave" from Tom
  is matched in Live's transcript; anyone else asking, or everyone saying
  goodbye, is the model's `leave`. A call that fails, or any of its lanes
  (the mixer, Live, the posts), ends and writes its traceback to
  `meet.log`. A restart of the database is not a failure (the Cowboy,
  2026-11-13): the call's connections wait up to `DB_WAIT_S` (180 s) for
  it, its stop listener listens again, its two locks are taken again
  (`db.Held`), and a stop or steer written meanwhile is caught up; if
  another process took the locks, or the kernel's sweep ended the call
  meanwhile, the call leaves and writes no second end.
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
  up to Drive, and Gemini's summary beside them. The call's title is its
  topic (Gemini's, from the prep and what was said) and the first names
  of those in it, Valor's last: the folder, the files' headings, and the
  summary, `<date> <title>.md`, which names the Meet code under its
  heading. PR and issue numbers in the summary link to the project's
  GitHub repository, and its spec's `links` prefixes to theirs. The
  asking session sends that file unchanged in the thread with at most two
  lines beside it. `valor-record call <code>` reads the transcript.

## Setup per Mac

The call profile is signed in as valor@yuda.me, by Valor itself when its
login is in 1Password: the item "Google valor@yuda.me" in the `m-valor`
vault, with its password and the authenticator's one-time password
(`bridges/meet/google.py`, read by name with `op item get`, since an `op://`
reference cannot hold the `@`; 1Password computes the code, so the
authenticator's setup key never leaves the vault). With it, Valor walks
Google's sign-in on the call's own page: "Verify it's you" (Next), the
account or the email, "Try another way" from a passkey, the password, then
the authenticator's code. A call does that before opening the meeting on a
profile with no account (a signed-out Chrome gets Meet's guest screen,
never Google's sign-in), and once more when its join meets Google's
sign-in: a join page that stays on Google's sign-in for three seconds, or
Meet's "You can't join this video call" whose Google sign-in for the
meeting stays put (a signed-out profile, the Captain, 2026-11-13). The
join is then tried once more; without the login, or when Google keeps it
out, the call ends at once naming the Mac and `meet-sign-in`. Meet's "You
can't join" for a profile Google lets through ends the call as "Meet would
not let Valor in".

Without the item, Tom signs it in at the Mac, in Terminal:
`cd ~/src/ai && .venv/bin/python -m core meet-sign-in`. It first asks where
Meet settles for a call's own headless Chrome (`meet.settled`, which signs
in from 1Password itself when it can): on Meet, it says "signed in" and
opens nothing. Otherwise it opens Chrome on the profile at Meet, where Tom
signs in, or clicks Next and verifies when Google wants the sign-in
verified again ("Verify it's you", the Cowboy, 2026-11-13), then quits that
Chrome with ⌘Q once Meet shows (Ctrl-C gives up). It never ends Chrome
itself: Google writes its cookies before its passkey step is done (the
Captain, 2026-11-13). A sign-in window may be left open, however it was opened: a call
quits a plain Chrome holding the profile before starting its own ([rollout.md](../rollout.md), step 9; the nightly update tells Tom
once a week while it is not signed in and the item is not in 1Password).
To `call.account` and that notice, signed in means
the account in the profile's preferences and Google's session cookie in its
cookies, which a pending "Verify it's you" leaves in place; `meet-sign-in`
also needs Meet to settle on Meet. `meet.join` refuses a Mac whose profile
is not signed in only while the item is not in 1Password.
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
