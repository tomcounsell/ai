# Google Meet

Valor in a Google Meet call as itself (docs/plans/meet.md): asked for in a
group chat, it joins first and reads up on the topic while it joins, posts
the link in that thread once it is in,
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
| Tom's read-backs answer his decisions through the asking chat's session, with no 👍 in chat, 1:1 or group | Tom, 2026-11-25: "Meets display name is trustworthy enough for our purposes" (after a 1:1 call's 👍 held a build 28 minutes) |

## The pieces

- **`meet.join`** (`act`), a group chat session's: target a Meet link or
  `new` (`gws meet spaces create`, open to anyone with the link), payload
  the thread asked in (`reply_to`, `topic_id`), `prep`, the session's
  notes on the topic, and optionally `account`, the Google account to join
  as (else the project's `google`, else Valor's own). The asking session first answers the ask in its
  thread with one line, "Starting a call, link in a moment", and requests
  `meet.join` in the same turn, its notes from what it already holds; the
  call's worker reads up while Valor joins, and the notes reach the voice
  session as a note (`Call._read_up`; Tom, 2026-11-16, P-033;
  `skills/sdlc/converse.md` **Calls.**). It opens the call session (channel `meet`, chat id
  the meeting code), reports to the asking session that Valor is joining
  (naming the call session, so the chat can stop or steer it, with no
  link) and starts the call's process. Neither that report nor the
  request's result carries the link: once Valor is in (`call.entered`,
  with `call.joined`) the call's process posts "I'm in: <link>" in the
  asking thread itself (an invited call's own post already has it) and
  writes the `link` report saying so, and a call Valor never got into
  ends saying why and not to post the link (Tom, 2026-11-13: "don't share
  a meet link if it doesn't work!").
- **The call's process** holds `meet:call:<session>` and
  `run:<session>`. Everyone's audio is mixed to 16 kHz and streamed to one
  Gemini Live session (`settings.call_model`, voice `settings.call_voice`,
  the key in `google-key`), with notes that ask for no reply: who is
  speaking, who is here (with one other person, all they say is to Valor),
  the worker's answers, the call's steers and, for a call asked for in a chat, the worker's
  reading once it is in (until then the instruction says it is still
  reading up, while asking the worker and taking tasks go on as usual;
  the note bringing the reading says it no longer is), and the worker's
  prep on the items still to come: one after the reading (or as an
  invited call starts) and one after each `settle`, `backlog` or
  `open_decisions`, never on a timer, a question asked meanwhile going
  first (`Call.prepare`; Tom, 2026-11-17, P-038). The prep leaves out what
  the call has settled, read-backs and catches on it included, and Live is
  told a settled item stays settled, whatever a note lists (twice a prep
  note's catch on a settled item had Valor raise it again, 2026-11-19). With one other person,
  Valor leads: a settled item's read-back opens the next; with more, the
  who-is-here note says the next item waits until someone asks for it. Live decides when Valor is addressed and
  speaks; its tools ask the worker (a fresh `call` turn in the session's
  checkout, on the call's gateway), post, read the open decisions,
  record Tom's read-backs and, when the project's spec names a backlog
  Sheet in its rooms, read it (`backlog`: `sheet.py lookup` on how
  someone described an item, or the whole Queue in order), and look a
person up (`person`: their dossier on the call's project, by name or loose
description, or the project's roster for "everyone", docs/memory.md, People; the instruction says to call it before
saying anything about someone). Each Live turn is one `meet.say`, its audio
  held until released and settled with Live's transcript of what played;
  a typed line is answered in the chat, not aloud (unless the chat
  fails that post), until someone speaks. A Live turn of silence ("<no speech>", "...", or near-silent
  audio) is never said. Live itself never cuts Valor off; the call does,
  when the person he answers speaks over him, and side talk does not.
  Live hears nothing sent until its turn is over, which runs on up to
  30 s after the call has cut it off, so what was said from the cut until
  then is sent again once it is (gaps with no transcript in Tom's calls,
  2026-11-19; heard and answered 2 of 2 in the live runs). Live's own
  interrupt was tried and refused: a short "Mm-hm." stopped Valor and he
  started his answer again from the top, 2 of 2.
  Live's own end of a turn is kept: it waits out a pause of a second
  inside a thought, and answers one of 1.5 s; no setting tried waited that
  out and still answered within 2 s (2026-11-17,
  `tests/test_live_call_voice.py`). Each person's sound reaches Live only
  while they speak (`call.mixed`: from a frame over `SPEAKING` until
  `HOLD_S` after their last): faint talk under it from the others, summed
  in, kept Live's end of turn from coming, and it answered no one, asked
  by name twice in a group call (0 of 8 live runs with it, 3 of 3 once
  it stopped; the notes sent mid-stream changed nothing, 12 of 12
  answered, 2026-11-18). With
  one other person in the call, a silence after their words asks Live for
  the answer, unless a reply already answered them; in a group, a stretch
  naming Valor with no turn of his `NAMED_S` (3 s) after it asks Live
  once whether it was to him. When to speak is
  the persona's "In a call"; in a group, the who-is-here note also says a
  question without Valor's name is to the person it was said to (the
  persona's line alone took side talk as his, 3 of 3, 2026-11-18). The
  recording keeps everyone's sound. The call's log has each note told to Live
  and each Live turn, begun and complete. A worker's answer to an `ask`
  made aloud is posted and, once it is in the chat, noted to Live to give
  in a sentence when Valor next speaks, never as a turn of its own (said
  at a pause, it spoke over Tom answering Valor's own question,
  2026-11-19); one asked in the chat is posted and noted; one the chat
  fails is said as unposted, once. A steer of the call is posted in
  its chat and noted to Live; one opening `look up:` goes to the call's
  worker instead, unposted, its answer posted and noted as an `ask` made
  aloud (the chat session's hand-off). The nudge and
  the hand-off rest on the chat session's stated default after five minutes
  with no objection from Tom (2026-11-18), his to reverse. `ask`, `post`, `settle`
  and `leave` don't block: a result that only acknowledges, in a turn that
  has already spoken, goes back silent, so it starts no second reply; any
  other result is said (`post`'s is "posting in the chat; you will be
  told if it fails"). A post the chat fails (its `meet.send_message`
  `failed`, no thread copy) goes back to Live as one turn to say aloud,
  once a session is open and Valor is not mid-reply, and that reply is
  spoken; a worker's answer is noted to Live as posted only once it is
  (every post of a call on the Captain failed, 2026-11-18, and Live, told
  each went, said nothing). Each turn's usage is charged by modality. A Live session
  that goes away, or sends nothing back through 6 s of speech and the 6 s
  of quiet after it, is resumed (a long turn, however little Live sends
  during it, is not; nor is a short utterance), the old session closed
  before the next opens. A resume that fails to open (Live refusing its
  handle as still connected, or not found or expired) opens a fresh
  session, told that Valor is already in the call,
  who is here, the newest of the worker's answers, the steers and the
  newest prep (a quarter of `CALL_SO_FAR`), and the newest of the
  transcript in the rest; one kept while no session was open is noted to
  the next to open.
  Two failed fresh opens post one line and leave. Nothing ends a call at a
  set time: Valor stays while anyone else is in it. "Valor, leave" from Tom
  is matched in Live's transcript; anyone else asking, or everyone saying
  goodbye, is the model's `leave`, whose reason says which ("Valor was
  asked to leave" or "everyone wrapped up and said goodbye"). A call that fails, or any of its lanes
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
  `127.0.0.1` is refused from Meet's page otherwise. The socket opens
  once the page's document has loaded: opened while Meet was still
  loading, it aborted the load and left the page blank (the Captain's
  calls, 2026-11-17), so the payload connects on `DOMContentLoaded` (a
  local edit to the vendored payload) and `page.js` says hello then. Each of Meet's media
  connections writes its state to `meet.log` ("media connection 2:
  connected"): audio, the call's chat and who sends which stream come
  over them; who is in the call does not. Live's speech is streamed into
  the virtual microphone a chunk at a time (`valorStream`) and stopped at
  once (`valorStop`, which says how much played). Meet may have Valor's
  microphone off (it mutes a joiner of a large call, and a profile last
  left muted joins muted: the Captain, 2026-11-18), so the payload's
  `ensureMicOn()` clicks "Turn on microphone" once in the call
  (`Page.ready()`) and again at the first chunk of each turn Valor speaks;
  with the microphone on, nothing is clicked. Meet adds the chat box to the page
  only once its panel has opened, and the payload's open of it at join can
  lose that race (the Captain, 2026-11-18), so a post with no box clicks
  "Chat with everyone" while it shows the panel shut (`aria-expanded`
  "false"), again if a pop-up ate the click, never while it shows it open,
  and waits up to 10 s for the box (a local edit to the vendored payload);
  a hidden box takes the post as it is.
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
  lines beside it. The summary's actions (each task given or taken in the
  call, with the one to do it) are listed in the call's report as tasks
  for the asking session to take, hand on or decline; with no summary, the
  report points at the transcript for them (P-034). `valor-record call <code>` reads the transcript; `valor-record call --search "<words>"` finds a line or summary across the project's calls. Each decision in the summary is written as a `decision` fact of the project, citing the call, and listed in the report.

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
once a week while it is not signed in and the item is not in 1Password, per
account: `meet-profile:<machine>[:<address>]:<week>`, own's key without the address).
To `call.account` and that notice, signed in means
the account in the profile's preferences and Google's session cookie in its
cookies, which a pending "Verify it's you" leaves in place; `meet-sign-in`
also needs Meet to settle on Meet. `meet.join` refuses a Mac whose profile
is not signed in only while the item is not in 1Password.
A project whose spec names `google` (another account of Valor's, a
client's) joins as it, on that account's own call profile beside own's,
`<meet profile>-<address>`, renewed from its own item, `Google <address>`
in `m-valor`; `meet-sign-in --account <address>` signs it in. An invited
call joins as the address that was invited (the invites routine's "Join
as", `meet.join`'s `account`). For either account, ready means signed in
as that address, not just any
([plans/9f4967d9-google-per-project.md](../plans/9f4967d9-google-per-project.md)).
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
