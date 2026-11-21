# Restart what the 2026-11-19..21 usage-limit outages left unhandled

Operations task 0b976535bda8, no code change. The valor project's own
losses are covered elsewhere and are out of scope.

## Known (observed 2026-11-21 12:38 UTC)

`task.ended` rows since 11-19 15:00 UTC naming a usage limit, exhausted
credits or ENOSPC (kernel DB port 5433; 5432 on the Cowboy):

- **Captain, 37 endings.** Five of 709b0d649ddb's six restarts were
  delivered; plan 399d9fc62dc6 ended again. The weekly limit (11-19 ~23:30
  to 11-21 07:00 UTC) ended psyoptimal and mastery chat sessions,
  email-thread sessions and two email-started tasks.
- **Pirate:** 3 yudame chat sessions ended on "out of usage credits", the
  last at 06:28 UTC on 11-21. **Bald:** 4 cuttlefish chat sessions ended
  on the weekly limit. **Cowboy:** valor only.
- **Accounts.** Every Mac's kernel holds the same `claude-token`
  (fingerprint 9bcabd03a4, login valor@yuda.me). The Captain also holds
  `claude-token.mastery` (2cb1ab9850). So there are two accounts: the
  shared one (all four Macs, including the Cowboy's own work) and mastery
  (the Captain's mastery and mastery-be work).
- **709b0d649ddb's fix never merged.** All four Macs run fcb4edd48. Its
  ops scripts are gone with its workspace.

## The exact calls (Python on each Mac, through Valor.app's `valor-run`, docs/rollout.md step 4)

- **Hand-on of a person's message.** `owe` already wrote `ended:<row>` and
  cannot fire again. A session that inherited the message as
  `child.reported` has no `message.steered`, so its report went quiet.
  The builder runs:
  `notices.request(conn, ENDED, kind="ended", about_key=f"handon:{MSG_ROW}", text=TEXT, session=await intake.session_for(conn, CHANNEL, CHAT_ID, spec))`.
  - ENDED is the last session that held the message, and MSG_ROW is the
    person's `message.received` row id, a key never used before.
  - TEXT is the person's name, their message(s) verbatim with the times,
    and "sessions X and Y ended on the usage limit before answering".
  - Proof: a `child.reported` row on the chat's open session with that
    key, then a `message.sent` in that chat after it.
- **A cut-off task.** `python -m core feedback TASK "<the limit cut this
  off at STEP; finish and commit where you were>" --by valor`, then
  `python -m core run TASK` (the kernel's two-at-a-time cap holds). Proof:
  a new `turn.started`.
- **A stranded report or question** (a `child.reported` on a session that
  had already ended, with nothing on an open session since): the same
  `notices.request` call, `about_key=f"handon:{REPORT_ROW}"`, with the
  report's text.
- **A reclaimed workspace.** Start fresh from the same instruction, project
  and chat with `python -m core start`, and say so.

## Order

1. **Limits.** Make one minimal call per account (shared, mastery).
   Nothing is restarted on an account that refuses it, and a Pirate
   credits refusal is reported to Tom as a blocker.
2. **Inventory.** List every ending as handled, task to restart, person
   waiting, or report stranded. A message counts as answered when any
   later `message.sent` in that chat or a teammate's later message in the
   room addresses it. That means reading the room
   (`valor-slack read` / `valor-telegram read`), not only later sessions'
   turns. Answered means left alone, with the evidence noted.
3. **Restarts, per account.**
   - People waiting go first.
   - The shared account runs at most two turns at once across all four
     Macs; the mastery account runs at most two.
   - The next restart starts only when one of them ends a turn.
   - Restarts on an account stop at its first refusal.
4. **Reports of restarted tasks.**
   - Before each feedback, check whether its reporting session (from
     `hears`) is still open.
   - If that session has ended, hand the report on with the call above
     once the task reports, if that happens during this turn.
   - If it does not report during this turn, list it in the delivery with
     its task, the chat it reports to and the ended session, so the DM
     session that reads the delivery knows to hand it on.
5. **The Captain's disk** (13 GB free of 228 GB).
   - Measure `~/valor-tasks`, `~/src` worktrees, Docker, local models,
     Postgres test databases and the Homebrew cache.
   - Removal comes only after the inventory is final and every restart
     has started.
   - Use `python -m core workspace remove` for stopped or merged tasks,
     and for ended tasks whose work was delivered.
   - Never remove one the inventory lists as unhandled, restarted or
     running.
   - Everything else is listed for Tom with its size.
6. **Report** in `.valor/done.md`:
   - per Mac and project: what was unhandled, what was restarted and its
     state, and what could not be restarted and why;
   - who still waits (name, chat, since when);
   - disk before and after, and the defects found.

## Out of scope

Any code change, including the unmerged fix, which is named as a defect.
Also out of scope: the valor project's losses, any message to a person,
and any new check or gate.

## Shown to work

The ledger rows named above for each item, and `df` before and after.
Cases checked beyond the obvious:
- a chat already answered by a later session, a later send or a
  teammate (no hand-on);
- a session that held only notices;
- an inherited `child.reported` message;
- an email-thread session;
- a reclaimed workspace;
- 399d9fc62dc6, which failed twice;
- an account still refused (the Pirate);
- a restarted task whose reporting session has ended.
