# Google Meet

Valor in a Google Meet call as itself (docs/plans/meet.md): asked for in a
group chat, it joins first and reads up on the topic while it joins, posts
the link in that thread once it is in,
speaks from one mind (Gemini's transcriber hears, one text model writes
every word, Gemini's voice says it; P-042) when it is addressed, posts written answers in the call's chat with a copy in the
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
  call's worker reads up while Valor joins, and its reading becomes the
  first topics of the call's state (`Call._read_up`; Tom, 2026-11-16, P-033;
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
  `run:<session>`. Valor speaks from one mind (P-042; Tom, 2026-11-25:
  "Ideally the voice always comes from a single speaker"): Gemini's live
  transcriber hears, one text model writes every word, and Gemini's voice
  says it. Everyone's audio is mixed to 16 kHz and streamed to the
  transcriber (`settings.call_ears_model`, English, its own end of turn at
  high end-of-speech sensitivity; the key in `google-key`). Each person's
  sound reaches it only while they speak (`call.mixed`: from a frame over
  `SPEAKING` until `HOLD_S` after their last): faint talk under it from the
  others, summed in, kept an end of turn from ever coming (2026-11-18).
  Measured before the switch on Tom's recorded calls and a scripted Meet
  (2026-11-25): it never ended a turn inside a pause of 1.5 s or less that
  the person went on from before Valor could have spoken, Live did 5 times
  in 92; it ends a turn 1.3 to 1.4 s after the last word (median), and in
  about one pause in eight under 1.5 s, two in five between 1 and 1.5 s,
  it ends one the person goes on from. Its stream lasts ten minutes, so it
  is reopened at a quiet moment in its ninth minute (`EARS_S`), and on a
  drop; what is said while none is open is sent to the next. Two failed
  opens in a row post one line and leave.
  Each turn it ends is one `call.heard` row, said by whoever spoke most in
  it. A turn that is Valor's starts a turn of the mind
  (`settings.call_mind_model`, `gemini-3.8-flash` by default; Flash-Lite
  recorded a decision no one made in the live Meet test, 2026-11-25): with
  one other person here, all they say; in a group, what names him (as
  transcripts write it too, "Val"), what follows straight on from his
  answer, or the rest of a turn its person went on from; words the
  transcriber did not catch from the only person here are asked for again.
  The mind is told, each turn, who is here, the call's one live state
  (P-041; Tom, 2026-11-25: "I'd rather the interaction be slow than have to
  repeat myself"), the notes kept for it (the worker's answers and the
  call's steers, a quarter of `CALL_SO_FAR`), the call so far in the rest,
  and what to answer; its standing instruction is `call.system` with the
  persona's "In a call" and "How product decisions are made". The state
  is what the call decided, each decision the moment it is settled, then
  the worker's topics: each once under the label the call uses, open,
  decided or parked, what it touches and what a decision changed, with
  depth on the next two (`Call.state`); the mind speaks on a topic from it,
  in any order, and never asks a decided one again. For a call asked for
  in a chat, the worker's reading is the state's first topics, never added
  to the notes before the call (until it is in, each turn is told Valor is
  still reading up); until the state has topics it carries those notes,
  and a reading that fails keeps them. The worker updates the topics
  (`UPDATE`) after the reading (or as an invited call starts), after each
  turn Valor spoke, and after each `settle`, `backlog` or
  `open_decisions`, never on a timer, coalesced (`Call.prepare`; P-038);
  one under way when a decision is settled is dropped for the one that
  settle queues (2026-11-25: notes written before Tom's no relisted the
  item, and Valor asked it three more times); a question asked while one
  runs stops it, is answered, and the update runs again after.
  The mind's tools ask the worker (a fresh `call` turn in the session's
  checkout, on the call's gateway), post, read the open decisions, record
  each decision as it is made (`settle`: the read-back, `by` who decided,
  in a 1:1 the other person when left out, and its ids as
  `open_decisions` gives them, or a bare question id, or a task id with
  one open question; an id that matches none is said back to the mind;
  the decision is written at once as a dated `decision` fact of the
  project citing the open decisions it settles, about who decided, and one
  whose write fails is written at the end, with the same source), read the
  backlog Sheet where the project's spec names one in its rooms
  (`backlog`), look a person up (`person`, docs/memory.md, People), and
  leave. Lookups run as they are called; a question the call has settled
  is left out of `open_decisions` (it is answered only once the call
  ends, and the list raised one Tom had settled, the live tests,
  2026-11-25). The mind writes its reply in the same answer as `settle`,
  `ask`, `post` or `leave`, and then needs no round more; a lookup's
  answer comes back to it first (each round is a model call of 2 to 4 s). What a turn does in the world
  (`settle`, `ask`, `post`, `leave`) is acknowledged at once and done when
  the turn commits: at its first sound, a decision first, so the state has
  it before Valor speaks of it; or, saying nothing, when the mind is done.
  The person it answers speaking `BARGE_S` before then drops the turn and
  all it held, and their next words start another, so a turn ended in a
  pause costs a model call, never a second decision, post or question
  (2026-11-25). One call twice in a turn is done once, and the last of a
  turn's `MIND_ROUNDS` writes words only (Flash-Lite called `settle` round
  after round and said nothing, the live tests, 2026-11-25). A `leave`
  waits for the goodbye to be said. A newer turn drops one not yet said,
  and waits out one that is. An empty reply is silence.
  Each whole sentence goes to the voice as it is written
  (`settings.call_voice_model`, voice `settings.call_voice`); a turn is
  one `meet.say`, requested at its first sound, its audio held until
  released and settled with what played; the answer to a typed line is
  posted, never said. The call cuts Valor off when the person he answers
  speaks over him, and stops the rest of the turn; side talk does not.
  A worker's answer to an `ask` made aloud is posted and, once it is in the
  chat, given by the next reply, opening it, never as a turn of its own
  (said at a pause, it spoke over Tom answering Valor's own question,
  2026-11-19); one asked in the chat is posted and kept; one the chat fails
  is said as unposted, once. A steer of the call is posted in its chat and
  kept for every turn; one opening `look up:` goes to the call's worker
  instead, unposted, its answer given as an `ask` made aloud (the chat
  session's hand-off, on its stated default after five minutes with no
  objection from Tom, 2026-11-18). A post the chat fails (its
  `meet.send_message` `failed`, no thread copy) is one turn said aloud
  once Valor is quiet (every post of a call on the Captain failed,
  2026-11-18). Each model call is charged by modality (`spending.gemini_cost`).
  The step-1 test of hearing is the task's own (P-042); the time from a
  turn's end to Valor's first sound is in `tests/test_live_call_voice.py`
  and, through Meet, `tests/test_live_meet.py`. Nothing ends a call at a
  set time: Valor stays while anyone else is in it. "Valor, leave" from Tom
  is matched in what was heard; anyone else asking, or everyone saying
  goodbye, is the mind's `leave`, whose reason says which ("Valor was
  asked to leave" or "everyone wrapped up and said goodbye"). A call that
  fails, or any of its lanes (the mixer, the transcriber, the posts), ends
  and writes its traceback to `meet.log`. A restart of the database is not
  a failure (the Cowboy, 2026-11-13): the call's connections wait up to
  `DB_WAIT_S` (180 s) for it, its stop listener listens again, its two
  locks are taken again (`db.Held`), and a stop or steer written meanwhile
  is caught up; if another process took the locks, or the kernel's sweep
  ended the call meanwhile, the call leaves and writes no second end.
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
  over them; who is in the call does not. Valor's speech is streamed into
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
  report points at the transcript for them (P-034). `valor-record call <code>` reads the transcript; `valor-record call --search "<words>"` finds a line or summary across the project's calls. Gemini is told the decisions the call recorded as it went and lists only others (one that restates a recorded one gives its number, `restates`, and is left out); the recorded ones and the others are the decisions in `notes.md`, `summary.json` and the report, each a `decision` fact of the project citing the call, written once. With no summary, the report still lists what the call recorded.

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
