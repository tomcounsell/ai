---
status: Planning
type: bug
owner: Valor
created: 2026-11-03
tracking: docs/plans/meet.md
---

# Meet calls: the Captain's three failures

**Ask.** Tom, 2026-11-03: a call asked for on the Captain failed twice
(profile not signed in, then the sign-in window holding the profile), and
the first failure reached him the next morning. Asking for a call should
just work.

**What the record shows** (the Captain's ledger and `meet.log`).

1. *Sign-in.* The call profile there was first made on 2026-11-03 06:26
   UTC, when Tom ran the sign-in. `docs/rollout.md` makes it a step Tom
   does at each Mac, and nothing asked him to: only the Cowboy was done
   (2026-10-27). The Pirate and the Bald have no call profile today.
2. *Lock.* 06:31 UTC: `launch_persistent_context` failed, "Failed to
   create a ProcessSingleton": the sign-in Chrome (still running, pid
   86970) held `SingletonLock`.
3. *Delay, real: 15 h 41 min.* The ask landed 2026-11-02 04:19 UTC;
   `meet.join` failed at 04:19:53 and wrote its `child.reported` to the
   asking session inside that session's own step. `serve.Kernel._step`
   marks every row the step wrote as seen, so `_ready` skipped the session
   although `session.chat_waiting` was true. Only the 20:00 UTC update's
   restart (which empties `seen`) woke it. The same swallowed today's
   link report: the link was never posted; the turn at 06:31:49 was woken
   by the call process's failure report, written by another writer.

## What is built

1. **Wake on a step's own report** (`core/serve.py`, `_step`). For a chat
   session, a `message.steered` or `child.reported` row the step wrote
   itself (not quiet) stops the advance of `seen`, as a row from another
   writer does, so the session is stepped again. Non-chat tasks unchanged.
2. **The joiner frees its profile** (`bridges/meet/__init__.py`,
   `Page.open`). Before launching, read `SingletonLock` (`host-pid`); if
   that pid is alive and its command line names this profile's
   `--user-data-dir`, SIGTERM it and wait (up to 15 s) for it to exit,
   then launch. A stale lock or a pid that is not Chrome on this profile
   is left to Chrome. The profile is Valor's alone; a window on it is only
   ever the sign-in window.
3. **The sign-in, before a call.** The nightly update (`update_runner`),
   when `call.profile_ready()` is false, tells Tom once a week per Mac
   (`notices.request`, kind `rollout`, about_key
   `meet-profile:<machine>:<ISO week>`, like the low-disk notice) that the
   Mac's call profile is not signed in, with the one command (`SIGN_IN`).
   `docs/rollout.md`: the sign-in is a named setup step, with which Macs
   are done; the "quit that window with ⌘Q" instruction goes, since item 2
   makes it unnecessary.

**Roll out and confirm.** Merge, update the Cowboy, Captain, Pirate and
Bald, and on the Captain (sign-in Chrome still open) run
`tests/test_live_meet.py` with `VALOR_LIVE=1 VALOR_MEET_LIVE=1`: Valor
joins a new Meet as itself. Nothing is posted in any chat.

**Out of scope.** Signing in the Pirate and the Bald (needs Tom's passkey
at each Mac; item 3 asks him); driving an open Chrome over CDP (a window
Tom opens has no debugging port); Tom's quoted times (the record's are
UTC above).

## Tests

- `tests/test_serve.py`: a chat session whose step writes a
  `child.reported` (kind `call`) to itself is stepped again on the next
  tick, once; a step writing only its turn rows is not; a non-chat task
  writing a report to itself is not re-stepped.
- `tests/test_meet.py`: a live process whose argv names the profile and
  holds `SingletonLock` is terminated before launch; a lock naming a pid
  that is not on this profile is left alive; a stale lock (dead pid) and no
  lock open without error.
- `tests/test_rollout.py`: the update reports an unsigned profile once a
  week per Mac, and nothing when it is signed in.
- Full suite under 5 minutes; the live join on the Captain as above.
