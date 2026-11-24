# A message handed on to another project's chat session, in the asker's words

Task f09b06da06b1. Tom, 2026-11-24 ("pls handle"): a chat session of one project can hand a message to another project's chat session, with the asker's words as written. Item 1 of the backlog post; items 2 and 3 (setups copied back, an owner per project) are out.

## The cases (observed in the Cowboy's ledger; each a test in `tests/test_handon.py`)

All three crossed Macs, and each Mac has its own ledger.
1. 2026-11-17: work asked in Tom's DM (Cowboy) was for another project's dev room (Pirate). No session could reach that room, so it ran under valor and merged without the check that room would have waited for.
2. 2026-11-21: Tom's email reply on a client project's plan reached the valor session, not the client room's (Captain). The thread routing is fixed since (fb625a46495f); the case stands for a message that reached the wrong project.
3. 2026-11-23: Tom's DM said "turn off ... Send a prompt to yudame-dev channel". The session posted its own "remove" there, which the room's session read as Valor's.

## As built

`act steer` (one mechanism, extended) takes a chat target: `valor-record act steer --in CHAT --from RECORD --by WHO [--text NOTE]`.
- **Sender** (`tools/chat_tasks.py` `TaskSteer.hand_on`):
  - It reads RECORD (the `#N` `messages` prints, through `record.messages`, so only the project's own reach).
  - It finds CHAT's owner with `intake._taking(..., anywhere=True)`.
  - It builds an `Inbound`:
    - **copied unchanged:** sender, sent_at and text;
    - **taken from the target:** `chat_kind`;
    - **left empty:** reply, topic, thread and posts;
    - **files:** named, with `kept_on`, and no path;
    - **`message_id`:** `handon:<key>`;
    - **`handed_on`:** the first chat it was said in, its `verified`, `by` (decided there by `_from_operator`, and kept when a record is handed on again), project, session, machine, `asked_by` (the CLI's `--by`, never the speaker) and `note`.
  - It records the message here with `intake.receive`, or on the owning Mac with `python -m core receive` over ssh (`core/remote.py`, the watch's runner moved there with stdin; the host is the Mac's Tailscale name).
  - The record is read before the ssh, and the broker holds no session lock while it performs.
  - ssh's own failure is `failed`: "no route from <this Mac> to <that Mac>: <ssh>".
  - `lookup` asks `core receive --lookup`.
- **`core receive`** (`core/__main__.py`) re-execs in the kernel plist's environment through `_kernel_env`, shared with `health`.
- **Receiver** (`core/intake.py`):
  - `receive` keeps `handed_on.verified`.
  - `_bind` sends `handed_on` to `_handed_on`. It is a `steer` of the chat's session by `handed_on.by`, waking even a `mentions` room. It is never a stop or an update.
  - No project there taking the chat raises, so `_bind_failed` reports it.
  - `reactions.on_bound` sets none.
  - `chat_line` shows "handed on from <chat>, by your <project> session on <Mac>; not said in this chat", with no id or thread, then the note.
  - `valor-record messages` marks it the same way.
- **Guidance**, in one place: `skills/sdlc/converse.md`, beside the other `act` commands.
  - Hand on rather than retype or post.
  - Name a Slack room by its id from the Slack tools.
  - Never put one client's words into another client's room.
  - Tell the asker where it went.
  - A handed-on message is that person's and unseen by the room; post top-level, start any task yourself, and hand a note back when the asker should hear the result.
- **Docs:** `core/intake.py` (binding table), docs/machine.md and docs/tech-stack.md (three things cross machines), and core/README.md.

## Routes (Tom, 2026-11-24: no new ssh access, no relay)

Observed 2026-11-24 from each Mac's `gui/` launchd domain:
- **Connect:** Cowboy→Pirate, Cowboy→Captain and Cowboy→Bald; Pirate→Cowboy and Pirate→Captain; Captain→Cowboy.
- **Fail** ("Host key verification failed"): Pirate→Bald, Captain→Pirate, Captain→Bald, and the Bald to every other Mac.

All three cases go Cowboy→Pirate or Cowboy→Captain.

## No-gos

- No post, reaction or send in either room.
- No new ssh keys, and no relay through the Cowboy.
- No cross-project `act start --in`.
- No always-report-back.
- No file copied between Macs.
- No check, gate or refusal. The errors are the record out of reach, an unknown chat, and a failed connection.

## Rollout

- Run the full suite, then merge.
- `core update --wait` on the four Macs.
- Run a read-only `core receive --lookup` probe for each ordered pair of Macs, from a one-off job in that Mac's own `gui/` launchd domain.
- A Mac still on old code fails with its own error (no `receive` command), named apart from no route.
- Done when Tom has used it on a real hand-on.
