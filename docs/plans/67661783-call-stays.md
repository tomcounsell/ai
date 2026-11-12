---
status: Built
type: bug
owner: Valor
created: 2026-11-12
tracking: docs/plans/meet.md
---

# A call stays while it goes on, and leaves when it is over

**Ask.** Tom, 2026-11-12: Valor left an invited client call when it was scheduled to end, while the call ran on
for another 20 minutes. Valor should stay for as long as the call goes on, and leave early if someone asks it to
or everyone wraps up.

**What the record shows** (the Captain, `meet.log` and the ledger). The scheduled end was not the cause. One
Live session ran from the join. Live said it was going away about three minutes before the hour, and the resume
worked. About three minutes later the resumed session dropped with `1008 Requested entity already exists`. Two
reopens with the stored handle were then refused (`1008 session not found or expired`). After `RECONNECTS = 2`,
`live()` posted `LOST_LINE` and left ("Valor lost its voice connection"). Nothing else ends a call at a set time:
`routines/invites` uses the invite's end only for its wake window (`start - WAKE <= now < end`). The call's own
reasons to leave (`watch`) are removal, denial, the meeting ending, no one coming, everyone else having left
(`EMPTY_S`), and lost audio. None is tied to a clock.

## The fix (`core/call.py`, one commit with its tests)

1. **A refused handle opens a fresh session.** In `live()`, an open that fails while `self.handle` is set
   clears the handle. The next try is a fresh session, and that try does not count against `RECONNECTS`. Only
   fresh opens that fail count, so a call still leaves when Live itself is down. A fresh session after the
   call has begun carries the call so far. `live_config` gets a new section after the prep, "The call so far",
   with three parts: a line saying the call is under way and Valor has already joined and greeted, so it does
   not greet again; who is here now (the same text `_here` gives, so "only one person is here" still holds);
   and the tail of `self.lines`, cut to `CALL_SO_FAR` characters. `CALL_SO_FAR` is `CALL_CONTEXT` characters,
   about a quarter of the compression trigger at about 4 characters per token, so the instruction stays well under the
   compression window. The prep is kept as it is. A session that drops mid-call and then has its handle
   refused, exactly what happened at 14:59, takes the same path.
2. **Why "already exists".** The log between the 14:56:15 resume and the 14:59:08 drop has no stall resume
   and no second open, so no session of Valor's raced the resumed one. In build, run one experiment against
   real Live with the paid key: open a session, take its handle, then (a) resume it while the first session
   is still open, and (b) resume the same handle twice. Record which of these errors matches the incident's.
   If one points at Valor's code, fix that. Otherwise say it is Google's error, with the log and the
   experiment as evidence. This step adds no handling beyond step 1.
3. **Leaving is the model's call, from the conversation.** `INSTRUCTIONS` and the `leave` tool's description
   change from "only when Tom asks you to leave" to: call `leave` when anyone in the call asks you to leave,
   or when everyone has wrapped up (the goodbyes are said); never because a scheduled end time has passed
   while people are still talking. The existing Tom-only `LEAVE` regex stays as it is (Tom, 2026-10-27).
   Anyone else's request goes through the model's `leave`. "The others have left" already exists
   (`EMPTY_S`), so no new code is added for it.

No new check, gate or timer. Docs: `docs/bridges/meet.md`'s Live paragraph and leave line follow the code.

## Tests (`tests/test_call.py`, the fake Gemini)

- The incident: a session that has a handle drops with the 1008 error, and its handle is then refused. The next
  config has no handle, and its instruction holds the earlier heard and said lines, the prep, who is here
  ("only one person"), and the line that Valor has already joined. No `LOST_LINE` is posted and the call
  carries on (a reply is said).
- A refused handle followed by a fresh open that fails twice still posts `LOST_LINE` once and leaves. This
  updates `test_two_failed_reconnects_post_one_line_and_leave`, which counts configs.
- A fresh session at the very start (no lines yet) has no "call so far" section. A long transcript is cut to its
  tail, at most `CALL_SO_FAR` characters, keeping the newest line.
- The instruction and tool text say anyone may ask Valor to leave and that a goodbye from everyone means
  leaving. This extends `test_the_live_session_is_told_valors_voice_tools_and_how_to_wait_its_turn`. A `leave`
  tool call while someone other than Tom is speaking leaves the call.
- The existing regex tests stay as they are: from anyone else, "Valor, leave" in the transcript alone does not
  leave.

**Live** (`tests/test_live_meet.py` on the Captain, through `_call`): already runs past one session's life
(`LONG_S`). It gains two things. First, a forced refusal: the test's Gemini wrapper refuses the first resume's
handle, and the call must carry on and still answer afterwards. Second, at the end, the asking device says a
wrap-up to everyone that the `LEAVE` regex does not match ("Thanks everyone, that's all for today, goodbye"; the
devices carry the operator's name, so a "Valor ... leave" phrase would end the call through the regex). The
call must end with exactly "Valor was asked to leave", the model's `leave`.

**Out of scope.** Changing `EMPTY_S`, widening the regex to anyone, any end-time logic in the invites routine
(there is none to remove), and the Live session's audio settings.

No more plan revisions after this one; the fix lands as commits with their tests.

**Done** when the full suite passes, the change is merged and rolled out to the four Macs, the live test passes
on the Captain as above, and Tom's next call that runs over keeps Valor in it.
