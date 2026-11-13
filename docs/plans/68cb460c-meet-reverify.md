# A call that lands on Google's sign-in says so, and meet-sign-in can re-verify

**Defect.** On 2026-11-13 the Cowboy's calls ce29ae4d0654 (10:04) and 61d87c8ba290 (10:06) failed with
`TimeoutError('Meet showed no join button')` (`bridges/meet/__init__.py:258`). When the call profile opened
Meet, the page was `accounts.google.com/v3/signin/confirmidentifier?...continue=https://meet.google.com/...`:
"Verify it's you, valor@yuda.me, please sign in again", with one **Next** button (the screenshot is in the
task workspace). Google wants the session re-verified. The cookies, including `SID`, are still there, so
`call.account()` and `profile_ready()` say signed in, and `python -m core meet-sign-in` answers "already
signed in" and opens nothing. A restarted Chrome shows the same page. The third failure (e778e29bd958, 10:09)
was pid 33312, the task's own diagnostic Chrome, not a stale call Chrome: `Call.run` closes the page on every
exit. Only Tom can clear the prompt (passkey). The code has two faults: the join waits 60 s and gives a reason
nobody can act on, and the one sign-in command cannot do the re-verify.

**Fix.**
1. `Page.join` (`bridges/meet/__init__.py`): each try, if the page's host is `accounts.google.com`, raise
   `SignInNeeded` (a `RuntimeError` in that module) at once. `Call.joined` (`core/call.py`) catches it and
   returns the reason, so `run()` leaves with it as the one line in the chat that asked for the call (Tom's
   answer 1): `f"Google wants Valor's sign-in on {settings.machine} verified again; at that Mac run:
   {SIGN_IN}"`. There is no new check: the join loop already reads the page, and this replaces a 60 s timeout
   with its cause.
2. `call.sign_in`: drop the "already signed in, open nothing" shortcut. Start Chrome headed on the profile
   through Playwright with the call's own launch (`CHROME_ARGS`, the same `ignore_default_args`), at
   `https://meet.google.com/`. It is done once the page is on `meet.google.com` and not `accounts.google.com`,
   and `account()` holds. Then it closes that Chrome itself and prints "signed in as ...". On a healthy profile
   it says so within seconds. Chrome closed early, or Ctrl-C: report from `account()`, as today. The
   held-profile handling (`holder`, `a_call`) stays as is. Reuse `Page`'s launch, without its init script,
   rather than a second launch path.
3. `docs/bridges/meet.md` and `docs/rollout.md`: the sign-in step also clears "Verify it's you", and a call
   that hits that page names the command.

**Out of scope.** Why Google asked for re-verification (a Workspace session-length policy?): open, named in the
delivery (Tom's answer 2). The weekly update notice keeps `profile_ready()`, which cannot see a pending verify.
Pre-flight probing of Meet before a call: a second mechanism.

**Tests** (`tests/test_meet_sign_in.py`, with its fakes):
- join on a page at an `accounts.google.com` URL raises `SignInNeeded` on the first try and does not wait out
  the timeout; a page on `meet.google.com` with a join button still joins.
- `Call.joined`, with a page raising `SignInNeeded`, returns the reason naming the machine and `SIGN_IN`, and
  the call leaves with it (no "call process failed").
- sign_in on a profile whose cookies say signed in but whose page sits on accounts.google.com waits, then
  ends once the page reaches Meet, closes its Chrome, and returns 0; a healthy profile returns 0 at once; a
  Chrome closed on the verify page with no account returns 1 and names the command; a call-held profile is
  still refused and left running.
- Existing sign-in tests are updated where they assumed "already signed in opens nothing".
Then the full suite with `-n auto`. **Live:** once Tom has re-verified at the Cowboy, a real join of
meet.google.com/sax-cddx-oej on the Cowboy, with the joined button label as evidence. No governance is added.
