---
status: Built
type: bug
owner: Valor
created: 2026-11-03
revision: 2 (critique of revision 1)
tracking: docs/plans/meet.md
---

# Meet calls: the Captain's three failures

**Ask.** Tom, 2026-11-03: a call asked for on the Captain failed twice
(profile not signed in, then the sign-in window holding the profile), and
the first failure reached him the next morning. Asking for a call should
just work.

**What the record shows** (the Captain's ledger, `meet.log`, Slack).

1. *Sign-in.* The Captain's call profile was first made 2026-11-03 06:26
   UTC, when Tom ran the sign-in. `docs/rollout.md` makes it a step Tom
   does at each Mac and nothing asked him: only the Cowboy was done
   (2026-10-27). The Pirate and the Bald have no call profile.
2. *Lock.* 06:31 UTC: `launch_persistent_context` failed, "Failed to
   create a ProcessSingleton": the sign-in Chrome (pid 86970, still
   running) held `SingletonLock`.
3. *Delay, real: 15 h 41 min.* The one call ask in that channel is a Slack
   message sent 2026-11-02 04:19:25 UTC (11:19 in
   UTC+7); Slack's own record and the ledger's `message.steered` agree, and
   no message from Tom landed near 07:51 UTC, so the quoted "2:51 PM" is
   not that message's time. Its turn ran at once; `meet.join` failed at
   04:19:53 and wrote its `call` report to the asking session inside that
   session's step. `serve.Kernel._step` marks every row the step wrote as
   seen, so `_ready` skipped the session though `session.chat_waiting`
   was true, until the 20:00 UTC update's restart emptied `seen`. Today the
   same swallowed the link report (never posted); the 06:31:49 turn was
   woken by the call process's failure report, another writer's row.
   The only writers of a `child.reported` onto the session being stepped,
   inside its step, are `meet.join`'s two (`tools/meet.py`, the failure;
   `call.begin`, the link). The board digest and the kernel's notices are
   written from the kernel's tick, outside any step, and already wake it.

## What is built

1. **A chat's own report wakes it** (`core/serve.py`, `_step`). For a
   chat session, `seen` stops before a `child.reported` the step wrote
   itself, as at a row from another writer, so the next tick steps it once;
   a failed step keeps its backoff (`router.retry_due`).
2. **The joiner frees its profile** (`bridges/meet/__init__.py`, `free`,
   from `Page.open`). It reads `SingletonLock` (`host-pid`); a live pid
   with an argument equal to `--user-data-dir=<profile>` (as given or
   realpath'd, read with `psutil`, since a turn's sandbox cannot run `ps`)
   gets SIGTERM, then SIGKILL after 15 s, then the launch. A dead pid or a
   process on another profile is left to Chrome. The profile is Valor's
   alone; a window on it is only ever the sign-in window.
3. **The sign-in, before a call.** The nightly update (`update_runner`),
   when `call.profile_ready()` is false, tells Tom once per Mac per ISO
   week (`notices.request`, kind `rollout`, about_key
   `meet-profile:<machine>:<ISO week>`, as the low-disk notice does) that
   this Mac cannot take calls until its call profile is signed in, with
   `SIGN_IN`. `docs/rollout.md`: the sign-in is a named setup step, with
   which Macs are done; "quit that window with ⌘Q" goes (item 2).

**Roll out and confirm.** Merge; update the Cowboy, Captain, Pirate and
Bald. On the Captain: make sure a Chrome is open on the call profile
(`SIGN_IN`'s command on `about:blank` if none is), note its pid, run
`tests/test_live_meet.py` (`VALOR_LIVE=1 VALOR_MEET_LIVE=1`), and show that
pid gone (`ps`) and Valor joined. Nothing is posted in any chat.

**Out of scope.** Signing in the Pirate and the Bald (Tom's passkey at
each Mac; item 3 tells him); driving an open Chrome over CDP (a window Tom
opens has no debugging port).

## Tests

- `tests/test_serve.py`: a chat session whose step writes a `call`
  report to itself is stepped on the next tick, and no third time; a chat
  session whose turn fails is stepped once in six ticks, its backoff kept.
- `tests/test_meet.py`: a live child whose argv names a profile under a
  path with a space, holding `SingletonLock`, is ended before launch
  (SIGTERM; one ignoring SIGTERM is SIGKILLed); a lock naming a live pid
  not on this profile is left alive; a dead pid's lock and no lock leave
  nothing to do.
- `tests/test_rollout.py`, beside the disk notice's test: an unsigned
  profile gives one notice whose about_key names the machine; a second run
  that week gives none; another machine that week gets its own; a signed
  profile gives none.
- Full suite under 5 minutes; the live join on the Captain as above.
