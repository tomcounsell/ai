# An email thread belongs to the Mac and session that started it

Bug fix (the plan stage's record; no critique or review round). Incident,
observed 2026-11-21: a project's chat session on one Mac sent an email
(the thread's root); Tom replied to all; the default Mac's email bridge took
the reply by its sender (Tom's address, owned only there) and bound it to a
new `valor` email-thread session. The sending Mac's ledger has no row for
the reply, so the work it changed never heard it.

## Cause

Two places decide by sender alone, never by the thread's root:
`bridges/email/imap.py` `poll` (searches `FROM` owned addresses, keeps
`intake.owns(sender)`) and `core/intake.py` `_project_for`. A thread is
known only by its root id (`References[0]`), and only the sending Mac's
ledger records who sent it.

## Fix: the root id names the Mac that sent it

1. **`core/mail.py` `message_id`**: `<valor.<hash32>.<mac>@domain>`, where
   `<mac>` is the first 8 hex of sha256 of `settings.machine` (opaque: no
   Mac or project name in a client's headers). `machine_of(root)` returns
   the tag of one of our ids, else None (an old `valor.<hash32>` id, or
   anyone else's). Every Mac reads the owner off any reply's root with no
   shared state; `lookup` recomputes the same id on the same Mac.
2. **Receiving (`intake.takes_email(sender, root)`, used by `poll`)**: a
   root tagged with this Mac is taken whoever sent the reply; one tagged
   with another Mac is never taken here, even from an owned sender; an
   untagged root keeps today's sender rule (`owns`). `poll` searches
   `UNSEEN SINCE` without the `FROM` tree (Gmail cannot search
   `References`), reads the three headers it already reads for each, and
   filters in Python. `sender_query` goes. Cost, measured: 424 unread in
   the inbox over 30 days, about 0.3 KB of headers each, per poll (polls
   follow new mail and the 29-minute IDLE re-issue).
3. **Binding (`intake._bind` / `_chat`, email only)**: the root's send is
   looked up in this ledger (`_SENT`, channel email, `message_id` = root).
   Found: the project is its task's project, and the email steers that
   task when it is a chat session still open (the incident's case: the
   Slack room's session hears Tom's reply and can steer the build reporting
   to it). Otherwise (no open chat session sent it), the thread's own
   email-thread session under that project, as today, with `replying_to`
   (a sending non-session task then reports to it, existing code). No
   local send: today's sender rule. The session is picked under the
   thread's chat lock and rechecked after the task locks, as now; the
   ended-on-recheck fallback goes through `open_session`, so it never
   makes a second email-thread session for the root.

## Tests (tests/test_kernel.py, made-up names, two Macs by `machine`)

- Incident: a client project on "Pirate"; its Slack-room session's send
  is the root; Tom replies. Pirate takes it (`takes_email`), Cowboy does
  not (though it owns Tom's address); bound `steer` to the Slack session;
  no email-thread session is made.
- A thread `valor` started on Cowboy: Cowboy takes Tom's reply, it steers
  the sending session.
- A thread nobody here started (untagged root): sender rule unchanged (Tom
  to `valor` on Cowboy, a client address to its project's Mac).
- A project sender's reply in its project's thread: steers the sending
  session; a second reply goes to the same session.
- The sending session has ended: the project's email-thread session takes
  it (one only, across two replies).
- `message_id`: same key and Mac give the same id; two Macs differ;
  `machine_of` reads ours and returns None for an old or foreign id.
- `poll` with a fake IMAP connection: unseen mail from an unowned sender
  in this Mac's thread is received; the other Mac's thread is left unseen.
- Existing tests whose assumptions move: `test_an_email_thread_is_a_chat_…`
  (untagged roots, unchanged) and any test asserting the `FROM` search.

## Out of scope

- Threads started before the fix (old untagged ids) on another Mac: their
  replies keep the sender rule; the sending Mac's own replies now bind to
  the sending session (step 3), which covers a project sender's reply.
- A client that drops `References` (root becomes its own id): unchanged.
- A send in doubt across the update keeps its old id, so its Sent Mail
  lookup misses; accepted (a window of seconds per Mac).
- Docs: `core/intake.py`'s module text and `bridges/email` text updated
  in the same commit; no new check, gate or guard.
