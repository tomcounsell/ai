# The Meet link only once Valor is in; a lapsed Google sign-in renews itself

**Why.** On 2026-11-13 a chat got "The call is open: <link>" at 10:13 UTC, and a minute later "I couldn't
get in". Tom: "don't share a meet link if it doesn't work!" `call.begin` writes the `link` report before
the call's process has even opened Meet. The same morning both the Cowboy's and the Captain's call profiles
lost Google's session. The Cowboy landed on "Verify it's you", which task 68cb460c44f9 now detects
(`SignInNeeded`). The Captain's profile was signed out: Meet showed "You can't join this video call", which
no check sees, so the call waited 60 s and said "Meet showed no join button". Each lapse needed Tom at the
Mac with a passkey. He wants calls to keep working while he travels. valor@yuda.me also has a password and
an authenticator app, but neither is in 1Password yet. Tom has not answered, so the default stands: build it
inert until the item exists.

**Build.**
1. **Link after join** (`core/call.py`). `begin` writes no report. When `Call.joined()` returns None,
   `Call.run` appends `call.joined` to the call session and writes the `link` report ("Valor is in the
   Meet: <url>. Post the link in the thread ..."), both in one transaction. `end()` reads `call.joined`
   from the rows it already holds. A call that never joined reports "Valor could not join the Meet: <reason>.
   Do not post its link." The ended-call wording is unchanged for one that joined.
   `skills/sdlc/converse.md` **Calls** says: the link comes only in a `link` report, sent once Valor is in.
   Post it only from that report, and post no link from a failure report. The invited-call paragraph keeps
   its own pre-announced post.
2. **The signed-out face** (`bridges/meet/__init__.py`). `Page.join` raises `SignInNeeded` when Meet shows
   "You can't join this video call" and Google's sign-in, opened with `continue` set to the meeting, settles
   on a sign-in page (`settled`, `on_sign_in`). If it settles on Meet, the reason is "Meet would not let
   Valor in". The join therefore fails at once and names the Mac and `meet-sign-in`, as the Cowboy's case
   now does.
3. **Self-renewal** (new `bridges/meet/google.py`, about 80 lines). `renew(page) -> bool` drives Google's
   sign-in pages: Next on `confirmidentifier`, the email on `identifier`, "Try another way", "Enter your
   password", the password, then "Google Authenticator" and the code. It returns True once the page settles
   on Meet. The password comes from `credentials.op_read("op://m-valor/Google valor@yuda.me/password")`,
   and the code from the item's one-time-password field (`...?attribute=otp`), so 1Password computes the
   code and the setup key never leaves the vault. No new library. The values are read at that moment and
   never logged or stored. If the item is missing, `renew` returns False without driving anything, so
   today's behaviour stands. It is used in three places:
   - `Call.joined`, on `SignInNeeded`: renew, go back to the meeting, try the join once more. If renewal
     fails, the existing one-line reason stands.
   - `call.sign_in`: when the headless probe does not settle on Meet, renew headless before opening Chrome
     for Tom.
   - `tools/meet.py _open`: the "not signed in" refusal applies only while the item is unreadable. With
     the item, an unsigned Mac (the Pirate, the Bald) signs in on its first call.
4. **`meet-sign-in` waits for the whole sign-in.** For a profile with no account it stopped Chrome as soon
   as the `SID` cookie appeared, which can be mid-way through the passkey step. The Captain's 10:11 sign-in
   stopped at `challenge/pk`. It now waits for Tom to quit Chrome, then judges by `settled`, as the
   re-verify branch already does.
5. Docs: `docs/bridges/meet.md` (link timing, renewal, the item's name), `docs/rollout.md` (step 9: the
   call-profile sign-in is the 1Password item; correct "Tom signed in every Mac's on 2026-11-05": the Pirate
   and the Bald never were), `docs/data.md` (`call.joined` row).

**Out of scope.** The Workspace session-length setting (left alone). Detecting the verify page, already
merged from 68cb460c44f9. Invited calls' pre-announced post. Creating the 1Password item, which is Tom's.

**Rabbit holes.** Google may refuse a typed sign-in in an automated Chrome ("This browser or app may not
be secure"). The call's Chrome already runs as real Chrome with `AutomationControlled` off, and that cannot
be proven before the item exists, so it is named in the delivery as unverified. Google's page wording
varies, so `renew` matches stable URL paths (`/challenge/pwd`, `/challenge/totp`, `/challenge/selection`)
and input types, not labels, wherever it can.

**Tests** (fakes; no network):
- A failed join (`SignInNeeded`, a timeout, "denied") writes no `link` report and no `call.joined`. The
  ended report says not to post the link. A successful join writes `call.joined` and the `link` report
  after `page.join()`. `begin` alone writes no report. Update
  `test_a_join_opens_one_call_session_reports_its_link_once...` accordingly.
- A signed-out "can't join" page whose sign-in settles on a sign-in page raises `SignInNeeded`. One that
  settles on Meet gives "Meet would not let Valor in". A transient bounce does not count.
- `renew` with no item returns False and touches no page. With a fake `op_read` and a fake page walking
  confirmidentifier → selection → pwd → totp → Meet, it types the password and the code into the right
  fields and returns True. A wrong code (stays on totp) returns False. No secret appears in the log output.
- `Call.joined`: `SignInNeeded`, then a renewal that succeeds, then joined; a renewal that fails gives the
  existing reason.
- `sign_in` with no account waits for Chrome to exit even after `SID` appears.
- `_open` with no account refuses without the item and proceeds with it.

**Then.** Full suite, merge, then `core update --wait` on all four Macs (each kernel job's launchctl
environment, detached; the Cowboy via `ssh localhost` with `PATH=/opt/homebrew/bin`), heads confirmed. Then
a real join on the Captain through `python -m core call` on a session `call.begin` opens, with its link
report checked as written only after the join, and no Chrome left on any call profile. The report names
which Macs can take a call: the Cowboy and the Captain while signed in; the Pirate and the Bald once the item
exists.
