# A message handed on to another project's chat session, in the asker's words

Task f09b06da06b1. Tom, 2026-11-24 ("pls handle"): a chat session of one project can hand a message to another project's chat session, with the asker's words as written. This is item 1 of the backlog post. Items 2 and 3 (setups copied back, an owner per project) are out.

## The cases (observed in the Cowboy's ledger)

All three crossed Macs, and each Mac has its own ledger (docs/machine.md).
1. 2026-11-17: Tom's DM (Cowboy) asked for work on an app built in another project's dev room (Pirate). No session could reach that room, so the work ran under valor and merged without the check that room would have waited for.
2. 2026-11-21: Tom's email reply on a client project's plan reached the valor session (Cowboy), not the client room's session (Captain), and the work merged on the earlier scope. The thread routing has been fixed since (fb625a46495f). This case stands for any message that reaches the wrong project's session.
3. 2026-11-23: Tom's DM said "turn off ... Send a prompt to yudame-dev channel". The session posted its own rewrite ("remove") in that room, and the receiving session read it as Valor's.

## What is built

One mechanism, extended: `act steer` (`tools/chat_tasks.py` `TaskSteer`) gains a chat target. Today it steers a task.
- **CLI.** `valor-record act steer --in CHAT --from RECORD [--text NOTE] --by WHO`. `RECORD` is a `message.received` row in the asking project's reach, the same scope `valor-record messages` reads (`core/record.py`). Without `--from`, `--text --by` is the words, as `act steer` takes them today. `--text` with `--from` is the session's own note, kept apart and labelled as Valor's.
- **Performer.** It builds an `Inbound` for CHAT: channel and chat id from CHAT. Sender id, sender name, sent_at, text and attachments are copied from the record unchanged. `message_id` is `handon:<effect key>`, so the same request twice records once. A new field `handed_on` holds `{chat, message_id, project, session, machine, note}` of the origin.
- **Where it goes.** The owning Mac is the `machine` of the spec that takes CHAT (`Spec.all()`, `takes`). On this Mac, the performer calls `intake.receive`. On another Mac, it runs `ssh valorengels@<machine as host> 'cd ~/src/ai && .venv/bin/python -m core receive'` with the Inbound as JSON on stdin. The ssh options are the watch's (`BatchMode`, a 10 s connect timeout). `core receive` re-execs in the kernel plist's environment, as `health` does: `_health`'s re-exec becomes a helper both use. It needs only the database, since specs under `~/Desktop` are unreadable over ssh. A failed connection is the outcome's error, worded "no route from <this Mac> to <that Mac>: <ssh's message>". There is no relay and no new ssh access. The lookup reads the owning Mac's ledger for that `message_id` the same way (`core receive --lookup`).
- **Binding** (`core/intake.py`, on the owning Mac's kernel, which reads specs). A record with `handed_on` binds as `steer` to the chat's session, before the operator test. It wakes the session even in a `mentions` room. `by` is the asker (Tom as `tom`, when the copied sender is the operator on the origin's channel). `handed_on` is kept on the steer row. `reactions.on_bound` sets nothing on it, since there is no platform message, so nothing reaches the room.
- **Reading.** `chat_line` renders `[when] Tom Counsell (handed on from <origin chat>, message <id>, by your <project> session on <Mac>; not said in this chat): <verbatim words>`, then `Valor's note: ...` when there is one.
- **Guidance.** One place: the chat-session text, in `skills/sdlc/converse.md` beside `act start --in`. A message or ask for another project's room is handed on with `--from` rather than retyped or posted there; the asker is told where it went. A handed-on message is that person's, said elsewhere. The session decides what its room hears and starts any task itself. When the asker should hear the result where they asked, hand a short note back the same way.
- **Docs.** The binding table and the ownership paragraph (`core/intake.py`), `tools/chat_tasks.py`'s docstring, `valor-record`'s usage, docs/machine.md ("Exactly two things cross machines" becomes three), and core/README.md if it lists the CLI.

## Routes (Tom, 2026-11-24, no new ssh access)

Working: Cowboy to Pirate, Captain and Bald; Pirate to Cowboy; Captain to Cowboy. With no route: Pirate↔Captain, and anything to or from the Bald except Cowboy→Bald. All three cases use Cowboy→Pirate or Cowboy→Captain.

## Out of scope / no-gos

No posting, reacting or sending in either room. No relay through the Cowboy. No new ssh keys. No `act start --in` across projects: the receiving session starts its own task. Nothing always reports back. Item 2 (setups copied back) and item 3 (an owner per project) are not built. No check, gate, allowlist or refusal is added. The only errors are a record outside the project's existing read scope (`NotFound`, as `valor-record` gives today) and a failed connection.

## Tests (made-up names; the remote transport injected)

- **Case 3.** A record in Tom's DM with "turn off" is handed to a dev room owned by another Mac. On the receiving side, the steer row's text is byte-equal to the record's, and its sender, sent_at and origin are as received. No reaction or send row is written. `chat_line` names the origin and "not said in this chat".
- **Case 1.** The same, into a `mentions` room. The message wakes the session (`chat_waiting`) with no mention in it. The receiving session's own `act start` then makes a task that reports to that session (`tasks.reporting`).
- **Case 2.** An email record bound to the valor session is handed to a client room's session on another Mac. Its subject and body arrive verbatim, its sender is the email's From, and its rights are no greater than it had (email is unverified).
- **Edges.** The same request twice records once. A chat on this Mac goes through local receive with no ssh. A failed ssh gives the "no route" error and writes nothing. `--from` naming another project's record gives `NotFound`. `--text --by` without `--from` still works. A chat no spec takes gives a plain error. The handed-on row shows in the receiving chat's session history (`_chat_start`). The gap fill's id paging ignores the `handon:` id.
- **`core receive`.** It re-execs in the plist environment and prints the received id. `--lookup` writes nothing.
- **Rollout.** The full suite; merge; `core update --wait` on the four Macs, without killing a running turn. A live round trip from the Cowboy into the Pirate's and the Captain's `core receive --lookup`, read-only. One live hand-on of a test note from the Cowboy into a Pirate chat whose only member is Tom, if there is one, else the lookup alone. Done when Tom has used it on a real hand-on.
