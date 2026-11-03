# Meet: signing in a Mac's call profile in one step

A bug fix (CLAUDE.md rule 9): no critique or review round. No check, gate or
refusal is added.

## What the evidence says

- **Why the Captain's sign-in was lost.** Tom did not quit his sign-in window.
  At 07:22 UTC on 2026-11-03 another task testing the Meet fixes over ssh
  ended that window (pid 86970) with its new lock release, then started
  Chrome on the call profile from the ssh session, where the login keychain
  is locked (`security`: "User interaction is not allowed"). That Chrome
  could not read Chrome Safe Storage. By 07:24 the profile's Google cookies
  were gone (every cookie left was created after 07:24:36), and its last run
  wrote Preferences with an empty `account_info` at 07:35:29 (the file
  mtime). So: Chrome started on the profile outside the Mac's login session
  drops the sign-in.
- **`profile_ready()` can say yes when the profile is not signed in.** At
  07:28 Preferences still named the account while the cookies were gone, and
  Meet answered "You can't join this video call".
- **Not shown:** whether a call started by launchd can read the keychain. No
  call has started Chrome on the Captain yet. The done criterion shows it.

## What will be built

1. **`python -m core meet-sign-in`** (`call.sign_in()`, run by Tom in
   Terminal at the Mac). If a live Chrome holds the profile (its
   `SingletonLock` pid, started on this profile), it says so, with the pid,
   and starts nothing. It never ends that Chrome, which may be a live call.
   Otherwise it starts Google Chrome's binary as its own child on the
   profile at accounts.google.com, checks `profile_ready()` every 2 s, and
   once it is true ends the child with SIGTERM and waits for it to exit
   (Chrome writes its cookies on a clean exit). Then it checks again and
   prints "signed in as <email>; <machine> can join calls". If the window is
   closed before sign-in, it prints that the profile is not signed in and
   exits 1. Ctrl-C ends the child.
2. **`profile_ready()` reads the cookies too.** It also needs Google's
   session cookie (`SID` on `.google.com`) in `Default/Cookies`. It reads a
   copy, because cookie names are stored in plain text.
3. **`call.SIGN_IN` becomes `cd ~/src/ai && .venv/bin/python -m core
   meet-sign-in`.** So `meet.join`'s message for a profile that is not
   signed in names the new step, and so does the weekly notice if that has
   landed.
4. **A held profile is reported plainly.** `bridges.meet.holder(profile)`
   returns the lock's pid when that process is live on this profile; step 1
   uses it too. When `launch_persistent_context` fails and `holder()` names a
   pid, `Page.open` raises "this Mac's call profile is open in another
   Chrome (pid N); quit it" instead of Playwright's text. The call's
   existing failure report carries that message to the chat.
5. **Docs.** `docs/rollout.md` step 9 and `docs/bridges/meet.md` give the
   sign-in as that command, run at the Mac in Terminal. They also say never
   to start Chrome on the profile over ssh, and why (2026-11-03).

**Shared files with the Meet fixes task.** Task 26957dfe's lock release
(`free`, `_on_profile`) and weekly notice touch the same files and are not
merged yet. The build rebases on main first. If `free` has landed,
`holder()` is taken out of it and used by both, not written a second time.

## Out of scope

- Ending a Chrome that holds the profile: that is task 26957dfe's lock
  release.
- Signing in any Mac: only Tom can, with the passkey at that Mac.
- Refusing to run over ssh: that would be a gate. The docs say it instead.

## Tests

- `sign_in` with a fake Chrome (a script that writes `account_info` and an
  `SID` cookie after a delay, then exits on SIGTERM): exits 0, the child has
  exited, and `profile_ready()` is true.
- The fake exits before sign-in: exits 1, says not signed in.
- A profile held by a live process started with `--user-data-dir=<profile>`:
  says held with that pid, starts no child, and leaves the holder running. A
  stale lock (dead pid) or a lock held by a process on another profile: sign-in goes ahead.
- `profile_ready`: an email but no `SID` gives False; an `SID` but empty
  `account_info` gives False; an `SID` on another host gives False; no
  Cookies file gives False; both an email and an `SID` give True. The
  existing fixtures that build a signed-in profile gain the cookie.
- `Page.open` with a failing launch: with a live holder, the plain message;
  with no holder, Playwright's own error.
- Done: Tom runs the command at the Captain, then has a real call from a
  chat the Captain serves.
