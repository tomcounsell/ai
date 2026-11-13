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
1. `Page.join` (`bridges/meet/__init__.py`): a try whose page is on a Google sign-in page (host
   `accounts.google.com`, path under `/signin` or `/v3/signin`) counts toward `SIGN_IN_TRIES` (6 tries, 3 s);
   any other page resets the count. Passive bounces through `ServiceLogin`/`CheckCookie`/`SetSID` on a healthy
   load pass, since they leave within a few tries. Once the count is reached, raise `SignInNeeded` (a
   `RuntimeError` in that module) carrying the URL. `Call.joined` (`core/call.py`) catches it, logs the URL
   and returns the reason, which `run()` ends the call with and `end()` posts to the chat that asked
   (Tom's answer 1): `f"Google wants Valor's sign-in on {settings.machine} verified again; at that Mac run:
   {SIGN_IN}"`. No new check: the join loop already reads the page each try, and this replaces a 60 s
   timeout with its cause. The plain `TimeoutError` names the last URL too.
2. `call.sign_in`: Tom's step stays in plain Chrome (a Popen, which works today and is not automation-
   controlled), now at `https://meet.google.com/`. "Signed in" is decided by `meet_probe(profile)`: a short
   headless Chrome with `Page`'s own launch, no init script, at `https://meet.google.com/`, true when it settles
   on `meet.google.com` and not on a sign-in page. Order: no account in the profile, open Chrome; else probe,
   and if it reaches Meet say "already signed in" and return 0. Otherwise open plain Chrome; poll `account()`
   as today, and once Chrome exits (⌘Q, Ctrl-C, or ended by us once `account()` holds) probe again and report:
   0 and "signed in as ..." when Meet is reached, else 1 naming the command. The probe sees what the headless
   call Chrome sees, and runs only while no Chrome holds the profile; the held-profile handling (`holder`,
   `a_call`) stays first, as is. Since `account()` already holds on a profile Google wants verified, the poll
   cannot end that Chrome: Tom quits it with ⌘Q once Meet's home page shows, and the message says so.
3. `docs/bridges/meet.md` and `docs/rollout.md`: the sign-in step also clears "Verify it's you" (quit with ⌘Q
   once Meet shows), and a call that hits that page names the command.

**Out of scope.** Why Google asked for re-verification (a Workspace session-length policy?): open, named in the
delivery (Tom's answer 2). The weekly update notice keeps `profile_ready()`, which cannot see a pending verify.
Pre-flight probing of Meet before a call: a second mechanism.

**Tests** (`tests/test_meet_sign_in.py`, with its fakes):
- join on a page that stays on `accounts.google.com/v3/signin/...` raises `SignInNeeded` after
  `SIGN_IN_TRIES`, not the 60 s timeout; a page that passes through `accounts.google.com/ServiceLogin` for two
  tries and returns to Meet with a join button still joins.
- `Call.joined`, with a page raising `SignInNeeded`, returns the reason naming the machine and `SIGN_IN`.
- sign_in, probe faked: a profile with an account whose probe reaches Meet returns 0 and starts no Chrome; one
  whose probe lands on sign-in starts plain Chrome at meet.google.com, and returns 0 when the probe after
  Chrome exits reaches Meet, 1 naming the command when it does not; a call-held profile is still refused and
  left running. `meet_probe` itself against a fake page: Meet settles true, a sign-in page settles false.
- Existing sign-in tests are updated where they assumed "already signed in opens nothing".
Then the full suite with `-n auto`.

**Live verification (part of done).** After Tom says he has re-verified: `meet_probe` on the Cowboy's profile
reports Meet reached, then a real join of meet.google.com/sax-cddx-oej on the Cowboy with the call's headless
Chrome, recording the joined button's label, or on failure `page.url` and a screenshot. No governance is added.
