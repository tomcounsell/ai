# The Meet link only once Valor is in; a lapsed Google sign-in renews itself

**Why.** On 2026-11-13 a chat got "The call is open: <link>" at 10:13 UTC and "I couldn't get in" a
minute later. Tom: "don't share a meet link if it doesn't work!" Two things put the link out before the
join. `call.begin` writes the `link` report before the call's process has opened Meet. `meet.join`'s usage
text and its result (`{"url", "session_id"}`) also hand the session the link at once. That same morning both
the Cowboy's and the Captain's call profiles lost Google's session. The Cowboy's case, "Verify it's you", is
now detected by task 68cb460c44f9 (`SignInNeeded`). The Captain's profile still listed the account in its
cookies but was signed out. Meet showed "You can't join this video call", which nothing caught, so the call
waited 60 s. Each lapse needed Tom at the Mac with a passkey, and Tom wants calls to keep working while he
travels. valor@yuda.me has a password and an authenticator, but neither is in 1Password yet. Tom left the
question open, so the default stands: build it inert until the item exists.

**Build.**
1. **Link after join** (`core/call.py`, `tools/meet.py`).
   - `begin` writes a `joining` report, not `link`. It carries `child_id`, so the asking session can still
     stop or steer the call in the lobby (`tasks.reporting`). It has no URL. Its text: "Valor is joining
     the call (session <id>); its link comes once Valor is in. Post nothing yet."
   - When `Call.joined()` returns None, `Call.run` appends `call.joined` and writes the `link` report
     ("Valor is in the Meet: <url>. Post the link in the thread ..."), both in one transaction.
     `to_session` dedupes per `about_key`, so the report is written once.
   - `end()` checks `call.joined` in the rows it already reads. A call that never joined ends with "Valor
     could not join the Meet: <reason>. Do not post its link." A call that joined keeps today's wording.
   - `MeetJoin.perform` drops `url` from its result, which becomes `{"session_id"}`; `lookup` does the same.
     Its usage text ends: "...your next prompt says Valor is joining; the link comes only in a `link`
     report once Valor is in, so post it only from that report."
   - `skills/sdlc/converse.md` **Calls** says the same: post the link only from the `link` report, never
     from a `joining` or failure report. The invited-call paragraph keeps its own pre-announced post.
2. **The signed-out face** (`bridges/meet/__init__.py`, inside `Page.join`, on the call's own page with no
   second Chrome). When the page shows "You can't join this video call", the call's page goes to
   `https://accounts.google.com/ServiceLogin?continue=<meeting>` and the existing `SETTLE_TRIES` and
   `on_sign_in` logic runs there. Staying on a sign-in page raises `SignInNeeded`. Settling back on Meet
   gives "Meet would not let Valor in". Renewal and the single retry continue from that same page.
3. **Self-renewal** (new `bridges/meet/google.py`, about 80 lines).
   - `secret(field) -> str | None` runs `op item get "Google valor@yuda.me" --vault m-valor --fields
     label=password --reveal`, or `--otp` for the code. It looks the item up by name, because an `op://`
     reference cannot hold the `@`. It runs through `credentials.op_read`'s environment, factored into
     `credentials.op(args)`. 1Password computes the code, so the setup key never leaves the vault. Nothing
     is logged or stored.
   - `renew(page) -> bool` returns False at once, touching no page, when the password cannot be read.
     Otherwise it walks Google's pages by URL path and input type: Next on `confirmidentifier`, the email
     on `identifier`, "Try another way", then `challenge/pwd` with the password, then the authenticator
     on `challenge/selection`, then `challenge/totp` with the code. It returns True once the page settles
     on Meet.
   - `Call.joined`, on `SignInNeeded`, renews, goes back to the meeting and tries the join once more. If
     renewal fails, the existing one-line reason stands.
   - **Unsigned profile** (no account; the Pirate, the Bald). `Call.run` signs in before it opens the
     meeting, because Valor's own open Meets show a signed-out Chrome the guest "Ask to join" screen. It
     uses ServiceLogin with `continue` set to the meeting, then `renew`, then the join. `tools/meet.py
     _open` refuses a profile with no account only while the item is unreadable.
   - `call.sign_in`: when its headless probe does not settle on Meet, it renews headless before opening
     Chrome for Tom.
4. **`meet-sign-in` waits for the whole sign-in.** For a profile with no account it ended Chrome as soon
   as `SID` appeared, which can be mid-passkey (the Captain stopped at `challenge/pk` at 10:11). It now
   waits for Chrome to exit, then judges by `settled`.
5. **Docs.** `docs/bridges/meet.md` covers link timing, renewal and the item's name. `docs/rollout.md`
   step 9 says the sign-in is the item, and corrects "every Mac's on 2026-11-05": the Pirate and the Bald
   never were. `docs/data.md` gets a `call.joined` row.

**Out of scope.** The Workspace session length. Detecting the verify page (68cb460c44f9). Creating the
item (Tom's).

**Rabbit hole.** Google may refuse a typed sign-in in an automated Chrome. The call's Chrome is real
Chrome with `AutomationControlled` off. This cannot be shown before the item exists, so the delivery says
so.

**Tests** (fakes; no network):
- `begin` writes only `joining` (with `child_id`, no URL), and the asking session can `task.stop` a call
  still in the lobby.
- A successful join writes `call.joined` and `link` once, after `page.join()`. A second write is a no-op.
- Each failed join writes no `link` and no `call.joined`, and its end says not to post the link: a
  timeout, "denied", and `SignInNeeded` with `renew` False, whose end also names the Mac and
  `meet-sign-in`. A call that joined and later failed keeps the joined wording.
- `perform` and `lookup` results carry no `url`, and `usage` says the link comes only in the `link`
  report. Update `test_a_join_opens_one_call_session_reports_its_link_once...` to match.
- "Can't join", then ServiceLogin staying on sign-in, raises `SignInNeeded`; settling on Meet gives the
  other reason; a transient bounce does not count.
- `secret` builds the exact `op` argv for the password and for `--otp`.
- `renew` with no item returns False with no page call. A fake page walking confirmidentifier, selection,
  pwd, totp to Meet gets the password and code in the right fields and returns True. A code that stays on
  totp returns False. No secret appears in the logs.
- `Call.joined`: `SignInNeeded`, then a renewal that succeeds, then joined. An unsigned profile with the
  item signs in before opening the meeting.
- `sign_in` with no account waits for Chrome to exit even after `SID` appears. `_open` with no account
  refuses without the item and proceeds with it.

**Then.** Full suite, merge, `core update --wait` on all four Macs (each kernel job's launchctl
environment, detached; the Cowboy via `ssh localhost`, `PATH=/opt/homebrew/bin`), heads confirmed. A real
join on the Captain through `python -m core call` on a session `call.begin` opens, with `link` checked as
written only after `call.joined`, and no Chrome left on a call profile. The report says which Macs take a
call.
