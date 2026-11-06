# Live stall-resume fires on a healthy session

A bug fix (hard rule 9): this plan is deleted in the fix commit; main gets
the fix and its tests only.

**Incident.** Tom's call on the Captain, 2026-11-06 06:21-06:30 UTC: the call
worked, but `meet.log` has 12 "Live heard nothing of 6 s of speech; resuming"
between 06:23:51 and 06:28:55, some 9-10 s after the previous open, and one
reopen refused at 06:26:17 with `1008 ... session is already connected to an
existing client` (recovered by the existing 1 s retry at 06:26:19).

**Cause, from the code.** `_receive` already zeroes `self.unheard` on every
server message, so "count anything Live sends as heard" is already true. The
timer is what is wrong: `_speakers` counts voiced seconds since Live's last
message and resumes at `STALL_S` = 6 s of speech, but a healthy Live sends
nothing for longer than that inside one long turn (its input transcript comes
in bursts, and a just-resumed session sends nothing until it has heard
enough). One stretch of the transcript runs about 80 s across four resumes.
What a healthy Live always does is answer the end of a turn: an input
transcript, a reply, or its turn of silence (`_audio`'s docstring; and with
one person present `_answer_alone` asks for an answer with `turn_complete`).
The deaf session of c101206e7 sent nothing even after people stopped.

## Fix (core/call.py)

1. **The stall is measured on the quiet, not the speech.** The resume fires
   when speech has gone to Live since its last message (`self.unheard >=
   CHANGE_S`, the existing minimum for real speech) and the room has then
   been quiet (`now - self.last_voice`) for `STALL_S` with still nothing
   back. Same timer, same event, same constant (6 s, its comment rewritten);
   no new check. A monologue of any length resumes nothing; a deaf session
   resumes 6 s after people stop talking.
2. **Close the old session before opening the next.** In `live()`, when the
   stall wins: set `self.session = None` first (the mix stops sending into
   the closing socket), cancel *and await* the receiver, then leave the
   `async with` (which closes the websocket), then connect. Today the
   receiver is cancelled but not awaited and the mix keeps sending to the old
   session while it closes.
3. Builder first checks, live on the Captain with the kernel's key, whether
   a resume with the handle straight after a clean close still gets 1008. If
   it does, that is Google releasing the handle late, and the existing retry
   (`failed` + 1 s) already covers it; nothing more is added for it, and the
   delivery says so.

## Tests (tests/test_call.py)

- A 20 s turn (STALL_S patched small relative to it) with input transcripts
  arriving only every few seconds and no model reply: one session, no resume.
- The same long turn with *nothing* back: no resume while speech continues.
- A deaf session: speech, then quiet past STALL_S with nothing back: exactly
  one resume, with the handle; the resumed session answers (the existing
  test, updated to the new rule).
- A brief blip (< CHANGE_S) then quiet with nothing back: no resume.
- Resume ordering: the fake Gemini refuses a `connect` with the 1008 error
  while an earlier fake session is still open; a stall-resume opens the next
  session with no "failed to open", and no audio is sent to the old session
  after the stall.
- Full suite under 5 minutes.

## Rollout and live check

Merge; `core update` on the four Macs over ssh with each kernel job's
launchctl environment (the Cowboy via `ssh localhost`, PATH=/opt/homebrew/bin).
On the Captain, the live Meet test through the kernel's call path with the
test device, including a 20 s+ monologue and a reply after it; `meet.log`
for that call shows no "resuming" and no 1008.

## Out of scope

Live's VAD and proactive-audio settings; the reconnect count; any new
logging; the call's content (a client's), which no test, commit or report
quotes. Tests use made-up names.
