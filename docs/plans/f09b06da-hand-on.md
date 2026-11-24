# A message handed on to another project's chat session, in the asker's words

Task f09b06da06b1. Tom, 2026-11-24 ("pls handle"): a chat session of one project can hand a message to another project's chat session, with the asker's words as written. This is item 1 of the backlog post. Items 2 and 3 (setups copied back, an owner per project) are out.

## The cases (observed in the Cowboy's ledger)

All three crossed Macs, and each Mac has its own ledger (docs/machine.md).
1. 2026-11-17: Tom's DM (Cowboy) asked for work on an app built in another project's dev room (Pirate). No session could reach that room, so the work ran under valor and merged without the check that room would have waited for.
2. 2026-11-21: Tom's email reply on a client project's plan reached the valor session (Cowboy), not the client room's (Captain), and the work merged on the earlier scope. The thread routing is fixed since (fb625a46495f). This case stands for any message that reaches the wrong project's session.
3. 2026-11-23: Tom's DM said "turn off ... Send a prompt to yudame-dev channel". The session posted its own rewrite ("remove") in that room, and the receiving session read it as Valor's.

## What is built

One mechanism, extended: `act steer` (`tools/chat_tasks.py` `TaskSteer`) gains a chat target.
- **CLI** (`tools/valor_record.py`). `valor-record act steer --in CHAT --from RECORD [--text NOTE] --by WHO`. With `--in`, no TASK is taken. `act_request` sends `target` = CHAT and payload `{in, from, text, by}`; `steer`'s names gain `in` and `from`. Without `--in`, it behaves as today.
- **`--by` on a hand-on** is who asked for it to be handed on. It is kept as `handed_on.asked_by` and is never the speaker's attribution, which is always taken from the origin record.
- **Naming CHAT.** No new listing surface: `valor-record chats` stays the session's own project's, so no session sees another client's rooms. The guidance says to read a Slack room's id with the Slack tools (`list_channels`) and pass `slack:<team>/<channel id>`; a Telegram room is named by the id the asker or the record gives. A chat no spec takes gives an error naming the chat given.
- **Performer.** `refuse`, `perform` and `lookup` branch on `payload.in`. With `in`, the task-child refusal does not apply, since it is about a task target; no refusal is added. `perform` reads RECORD, a `message.received` row, from this Mac's ledger, within the asking project's reach (`record.reach`, the scope `valor-record messages` reads; outside it, the existing `NotFound`). It builds an `Inbound` for CHAT:
  - **Channel and chat:** channel and chat id are CHAT's, and `chat_kind` is the target's (`channels.group`).
  - **Copied unchanged:** `sender_id`, `sender_name`, `sent_at` and `text`.
  - **Attachments:** name, mime and transcript only; the file stays on the origin Mac.
  - **Left empty:** `reply_to`, `topic_id`, `thread`, `posted` and `headers`, since they name posts in another room.
  - **`message_id`:** `handon:<effect key>`, so the same request twice records once.
  - **`handed_on`:** `{chat, message_id, channel, verified, by, project, session, machine, note}`. `by` and `verified` are decided here from the origin record, by the existing `intake._from_operator` (`tom` when it is the operator, else the sender's name). The receiving side never re-derives them from a sender id copied across channels.
- **Transactions.** The record is read and the Inbound built in one transaction, which closes before any ssh. No transaction or lock is held across the remote call (10 s connect, 300 s at most). The local path calls `intake.receive` in its own transaction.
- **Where it goes.** The owning Mac is the `machine` of the spec that takes CHAT (`Spec.all()`, `takes`).
  - **This Mac:** `intake.receive`.
  - **Another Mac:** the watch's `ssh(host, command)` moves from `routines/watch/runner.py` to `core/remote.py` with an optional stdin, and the watch and the hand-on both call it. It runs `cd ~/src/ai && .venv/bin/python -m core receive` with the Inbound as JSON on stdin. The host is the spec's `machine` used as an ssh host (`valorengels@<host>`), as `VALOR_WATCH` names hosts. A `VALOR_MACHINE` value that is no ssh hostname fails like any connection.
  - **Environment:** `core receive` re-execs in the kernel plist's environment, as `health` does; `_health`'s re-exec becomes a helper both use. It needs only the database, since `~/Desktop` is unreadable over ssh.
  - **Failures:** a failed connection is the outcome's error, "no route from <this Mac> to <that Mac>: <ssh's message>". There is no relay and no new ssh access.
  - **Lookup:** `core receive --lookup` reads the owning Mac's ledger for that `message_id` and writes nothing.
- **`receive`** keeps `handed_on.verified` as the row's `verified` instead of recomputing it from the target channel. An email handed into a Slack room stays unverified.
- **Binding** (`core/intake.py`, on the owning Mac's kernel). A record with `handed_on` binds as `steer` to the chat's session through `_chat`, ahead of the update, operator and `stop` branches:
  - **Never a stop:** a handed-on exact "stop", even into Tom's own chat, is a steer and never a stop.
  - **Wakes:** it wakes the session even in a `mentions` room.
  - **Attribution:** `by` is `handed_on.by`, and `handed_on` is kept on the steer row.
  - **No reaction:** `reactions.on_bound` sets nothing on it, since there is no platform message, so nothing reaches the room.
- **Reading.** `chat_line` renders `[when] Tom Counsell (handed on from <origin chat>, by your <project> session on <Mac>; not said in this chat): <verbatim words>`, then `Valor's note: ...` when there is one, and files as "kept on <Mac>". The line names no message id and no thread, since `handon:` is no platform id and offers nothing to reply to.
- **Guidance.** One place: `skills/sdlc/converse.md`, beside `act start --in`.
  - **Hand it on:** a message or ask for another project's room is handed on with `--from` rather than retyped or posted there, and the asker is told where it went.
  - **Which rooms:** hand on only within the asker's project or to a room whose people may hear it, never one client's words into another client's room. This is the existing rule that what a chat said stays that chat's.
  - **Receiving:** a handed-on message is that person's, said elsewhere, and its room did not see it. What the session posts there must make sense to the room's members without it. The session decides what its room hears and starts any task itself.
  - **Posting about it:** a post about a handed-on message goes top-level in the room, never as a thread reply to it.
  - **Reporting back:** when the asker should hear the result where they asked, hand a short note back the same way.
- **Docs.** Updated where they describe this:
  - `core/intake.py`'s binding table and ownership paragraph, and the docstrings of `tools/chat_tasks.py` and `routines/watch/runner.py`.
  - `valor-record`'s usage.
  - docs/machine.md: "Exactly two things cross machines" becomes three.
  - core/README.md.

## Routes (Tom, 2026-11-24: no new ssh access)

Expected: Cowboy→Pirate, Captain and Bald; Pirate→Cowboy; Captain→Cowboy. No route: Pirate↔Captain, and anything to or from the Bald except Cowboy→Bald. All three cases use Cowboy→Pirate or Cowboy→Captain. The delivery reports each pair as observed, not as expected.

## Out of scope / no-gos

- No posting, reacting or sending in either room.
- No relay through the Cowboy, and no new ssh keys.
- No `act start --in` across projects, and nothing always reports back.
- Items 2 and 3 are not built.
- No file is copied across Macs.
- No check, gate, allowlist or refusal is added. The only errors are the existing `NotFound` for a record out of reach, a failed connection, and a chat no spec takes.

## Tests (made-up names; the remote runner injected)

- **Case 3.** A Telegram DM record with "turn off" is handed to a Slack dev room owned by another Mac:
  - the received text is byte-equal to the record's, and its sender, sent_at and origin are as received;
  - `by` is `tom`, decided from the origin, even though the Telegram sender id is not in `operator_slack_ids`;
  - no reaction or send row is written, and `chat_line` says "not said in this chat".
- **Case 1.** The same, into a `mentions` room. It wakes the session (`chat_waiting`) with no mention. The receiving session's own `act start` makes a task reporting to it (`tasks.reporting`).
- **Case 2.** An email record is handed into a Slack room on another Mac:
  - the subject and body arrive verbatim, and the sender is the From address;
  - the row's `verified` is false and `by` is not `tom`.
- **Fields.** The received row's `reply_to`, `topic_id` and `thread` are empty, `chat_kind` is the target's, and an attachment carries no path. `thread_tag` gives none for it, and `chat_line` shows no message id to reply to.
- **Attribution.** `--by` that names someone else is kept as `asked_by`, and the row's `by` is still the origin's.
- **Naming.** A `slack:<team>/<channel id>` that another Mac's spec lists resolves to that Mac; one no spec lists gives an error naming it.
- **No lock across ssh.** With the remote runner blocked, another transaction can take the asking session's locks.
- **CLI.** `act steer --in CHAT --from R` with no TASK reaches the performer, unrefused.
- **Edges:**
  - a handed-on "stop" into Tom's own chat is a steer, and no task stops;
  - the same request twice records once;
  - a chat on this Mac uses local receive with no ssh;
  - a failed ssh gives "no route" and writes nothing;
  - `--from` naming another project's record gives `NotFound`;
  - `act steer TASK --text --by` is unchanged;
  - a chat no spec takes gives a plain error;
  - the row appears in a new session's history (`_chat_start`), and the gap fill's id paging ignores `handon:` ids.
- **`core receive`.** It re-execs in the plist environment and prints the received id; `--lookup` writes nothing. The watch's tests pass against the moved `ssh`.

## Rollout

- Run the full suite, then merge.
- `core update --wait` on the four Macs, without killing a running turn.
- From each Mac's kernel job environment (a one-off `gui/` launchd job, so the job's ssh keys, not an interactive shell), run a read-only `core receive --lookup handon:probe` against each other Mac. The delivery records each pair's result, and the printed error where there is no route.
- One live hand-on of a test note into a Pirate chat whose only member is Tom, if one exists; otherwise the lookups alone. Done when Tom has used it on a real hand-on.
