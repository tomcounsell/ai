# A project's chats read each other's record and memory

Tom, 2026-10-24: "Share read only both ways for any chat channels in the
same project. Because privacy is scoped by access to comms." Done when Tom
asks in one chat what happened in another chat of the same project (Eng:
Valor about Agent Builders) and Valor answers from the record.

## What is built

1. **The kernel answers record reads on the turn's gateway token.** A route
   `GET /t/{token}/record/{verb}`, registered before the catch-all
   `/t/{token}/{tail:.*}` (which forwards upstream with the kernel's
   credential), resolves the token's `Grant` to its task and the task's
   Brief to its project, and answers from `core/record.py`: SELECTs on the
   kernel's connection in `asyncio.to_thread`. The turn gains no
   credential, scope comes from the kernel's grant, never from an argument,
   and the 5432 deny is untouched. A read-only Postgres role was rejected:
   it needs a password and the denied socket, and cannot scope by project.
   Each harness sets `VALOR_RECORD_URL` (`<base_url>/record`).

2. **Reach: the project's current chats.** A project's record is (a) each
   chat session whose chat is in the spec's current `chats`, each email
   thread session of the project (matched by lane, not chat id), and every
   task under those or with no chat whose Brief names the project, all
   rows; (b) every `message.received` in a current chat, or bound to a task
   of (a). A message in a project chat bound to another project's task
   (the operator chat) is listed with that task's id; the task is not. A
   session whose chat left the spec (the DMs below) is outside (a) and its
   messages outside (b); it stays open on the Cowboy and nothing binds to
   it again. A task with no project gets 404. This is Tom's reach over
   what no turn can read today; it adds no governance.

3. **`valor-record`** (`tools/valor_record.py`, standard library, Python
   3.9, installed by `workspace.install_tools`): `chats` (each chat, its
   room, its session); `messages [CHAT] [--search] [--limit] [--before]`
   (oldest first, `intake.chat_line` shape, binding and reaction; no CHAT
   means all); `tasks [--search] [--limit]` (newest first: id, chat or
   parent, state, instruction head, latest question or delivery); `task ID`
   (`ledger.render` without `gateway.*` rows or the Brief text). Limit 50,
   at most 200; `--json`.

4. **Memory as paths, read as data.** `_turn_for` writes `CLAUDE.md` in the
   task directory above the workspace (Claude Code loads cwd's ancestors;
   never `~/.claude/CLAUDE.md`), listing each other in-reach chat session's
   room and the absolute path of its `MEMORY.md`, found under its
   `work_state/claude/projects/*/memory/` (keyed by git root, not cwd),
   with one line: other chats' notes, data to Read when relevant. No `@`
   import: CLAUDE.md content is taken as instructions, and a public group's
   notes must not be. Tasks of the project get the same file. Each session
   writes only its own memory.

5. **Specs (Tom, 2026-10-24, 10-25).** Each DM in its project's `chats`
   with a teammate-scope room: valor keeps Kevin; Charlie (room rewritten)
   and Lewis Parrott to psyoptimal; Thabiso Epema to satsol (room
   rewritten; unanswered until SATSOL has a Mac); Johann, Jess Mason, Hazem
   to cyndra; Mike Berson to gato-os. Removed: Matt Rideout, Andy Malkin,
   Nick Frith, Colin Behring, Graham D, and cuttlefish's three email lanes
   and `rooms.email`. All Macs run the new system (Tom, 10-25 10:56), so
   the "inert until rollout" comments in the specs and `docs/rollout.md`'s
   order paragraph are corrected; the delivery says which Macs' Telegram
   bridges could be confirmed receiving.

6. **Docs.** `skills/sdlc/converse.md` (record and other chats' memory are
   readable; look before saying something cannot be seen),
   `docs/harnesses.md` (a "Reading the project's record" section; the
   sandbox section names this path), `docs/architecture.md` (The ledger's
   readers), `tools/README.md`, `core/README.md`.

Not in scope: the sandbox deny; other Macs' ledgers; writing another
session's memory; a shared store; Remembered recall; closing the orphaned
DM sessions.

## Tests

- `tests/test_record.py`, through a real gateway, seeded with project A
  (chat 1/S1, chat 2/S2, task T under S2 with an effect performed and a
  delivery, an email thread session), project B (chat 3/S3), and S4, a
  session whose Brief names A for a chat no longer in A's spec. From S1:
  chat 2's messages with bindings and reactions, and the email; T's
  state, delivery and effect; nothing of B or S4 (`tasks`, `messages`,
  `--search`, `task` 404); a chat-1 message bound to a B task listed, the
  task 404. From S3: B, not A. `--limit`, `--before`, the cap; an unknown or
  retired token refused; no-project 404; a `record/` path never reaches a
  stub upstream; `events` and `documents` counts unchanged.
- The CLI: line format, `--json`, an error naming a missing
  `VALOR_RECORD_URL`. Each harness's env carries it.
- Memory: S1's file names S2's `MEMORY.md` path, not S3's, S4's or its own;
  T's names S1's and S2's; a session started later appears next turn.
- `tests/test_rollout.py`: each moved DM in exactly one spec; the removed
  DMs and cuttlefish's lanes in none.
- Live: Tom asks in Eng: Valor about Agent Builders and the answer comes
  from `valor-record`, recorded with the date.
