# A project's chats read each other's record and memory

Tom, 2026-10-24, valor team chat: "Share read only both ways for any chat
channels in the same project. Because privacy is scoped by access to comms."
Done when Tom asks in one chat what happened in another chat of the same
project (here, Eng: Valor about Agent Builders) and Valor answers from the
record.

## What is built

1. **The kernel answers record reads, on the turn's gateway token.** The
   gateway already gives every turn a per-turn token (`gateway.issue`, a
   `Grant(task_id, turn_id)` held only in the kernel's process). A second
   route on the same app, `GET /t/{token}/record/{verb}`, resolves the
   token to its task, the task to its Brief's project, and answers from
   `core/record.py` with SELECTs on the kernel's connection, run in
   `asyncio.to_thread` so a slow query never stalls a streaming call. It
   is registered before the catch-all `/t/{token}/{tail:.*}`, which
   forwards any other tail upstream with the kernel's credential. Why this path:
   the turn already holds the token, so it gains no credential; the grant
   is the only turn identity the kernel issues, so scope comes from the
   kernel, never from an argument the turn sends; the 5432 deny is
   untouched. A read-only Postgres role was rejected: the turn would need
   a password and a socket the deny closes, and Postgres alone cannot
   scope rows by project. Each harness sets `VALOR_RECORD_URL`
   (`<base_url>/record`) beside its base URL.

2. **Scope: the asker's project.** A project's record is (a) every task
   whose Brief names the project, with all its rows, and (b) every
   `message.received` whose chat is in the project's spec `chats`, or that
   was bound (`message.bound`) to one of those tasks (email threads, whose
   chat ids are thread roots). A message in a chat of A bound to a task of
   B (the operator chat is valor's, and every project's tasks report
   there) is shown, since it is that chat's content, with its binding's
   task id; B's task rows are not. A task with no project is answered 404,
   naming why. This is the reach Tom set, not a limit on anything readable
   today, so it adds no governance and needs no grant; nor does anything
   else here (the 5432 deny stays as it is).

3. **`valor-record`, a workspace tool** (`tools/valor_record.py`, standard
   library, Python 3.9, installed by `workspace.install_tools`, like
   `valor-telegram`):
   - `valor-record chats`: the project's chats, each room's description,
     its chat session's task id;
   - `valor-record messages [CHAT] [--search T] [--limit N] [--before ID]`:
     received messages oldest first in `intake.chat_line`'s shape, each
     with its binding (task, `steer`/`heard`/new task/answer/stop) and its
     reaction; no CHAT means every chat of the project;
   - `valor-record tasks [--search T] [--limit N]`: the project's tasks
     newest first: id, chat or parent, state (`machine.fold`), instruction
     head, latest question or delivery;
   - `valor-record task ID`: that task's rows (`ledger.render`) without
     `gateway.*` rows and with `turn.started`'s Brief text dropped, so
     effects requested, refused and performed read in order.
   Default limit 50, at most 200; `--json` prints the kernel's JSON.

4. **Memory: Claude Code's own CLAUDE.md loading.** Before each turn,
   `_turn_for` writes one CLAUDE.md the turn's Claude Code loads, holding a
   heading per other chat session of the project (its room) and an
   `@<its memory dir>/MEMORY.md` import, its own excluded. The build first
   measures, with one real turn, which location loads it in `-p`: the
   turn's config dir (`CLAUDE_CONFIG_DIR/CLAUDE.md`) or the task directory
   above the workspace (outside git); and whether `@` imports load there.
   If imports do not load, the file holds the indexes' text instead. The
   topic files are already readable (the sandbox denies only the kernel's
   paths); the index gives their paths. Each session still writes only its
   own memory. Tasks of the project get the same file, so a task reads
   its chats' memory too. Memory is shared at exactly the record's reach.

## Each DM under its project (Tom, 2026-10-24 08:53 UTC)

"DMs should have the same teammate scope as teammate groups. And
specifically whitelisted per project. ... moving each person to the
project they belong to." So each DM is listed in its own project's `chats`
with its room description, which already gives the team group's scope (no
engineering or SDLC work; web search and GitHub issues fine). Kevin, Matt
Rideout, Nick Frith and Andy Malkin stay under valor; so do Tom's DM and
the operator group. Graham D is removed entirely (08:54): his DM and room
in `valor.toml`, and `email:grahamhderry@gmail.com` in `cuttlefish.toml`.

A chat is received only by the bridge of the Mac its spec names. Today the
Cowboy receives every DM. The Pirate runs the new Telegram bridge
(`com.valor.kernel.telegram`, checked 2026-10-24), so Mike Berson moves to
`gato-os.toml`. The Captain (psyoptimal) and the Bald (cyndra) still run
the old system, so moving Lewis Parrott and the six Cyndra DMs leaves them
unreceived by the new kernel until those rollouts. That is put to Tom
(`.valor/question.md`); the build does what he answers, default: move them
(each DM then waits with its project's team groups, already unreceived
until the same rollout).

5. **Docs.** `skills/sdlc/converse.md`: the record and the other chats'
   memory are readable; look there before saying something cannot be
   seen. `docs/harnesses.md`: a "Reading the project's record" section
   beside "Reading a chat's history", and the sandbox section names this
   read path. `docs/architecture.md`, The ledger: its readers include
   turns, through the kernel, scoped to their project. `tools/README.md`,
   `core/README.md` (gateway, `_turn_for`).

## Not in scope

Removing or narrowing the sandbox deny; other Macs' ledgers (each Mac's
ledger is its own); writing to another session's memory; a shared memory
store; the persona's Remembered recall, which stays as it is.

## Tests

- `tests/test_record.py`, a seeded ledger with project A (chat 1 with
  session S1, chat 2 with session S2, a task T started by S2 with an
  effect requested and performed and a delivery, an email thread bound
  to S2) and project B (chat 3, session S3), read through a real gateway
  with a grant for a turn of S1:
  - `messages` shows chat 2's messages with each binding (steer, heard,
    the task started) and reaction, and the email; none of chat 3's,
    with or without `--search`, and naming chat 3 returns nothing;
  - `tasks` lists S2 and T with their states and T's delivery, not S3;
    `task T` shows the effect's intent and outcome; `task S3` is 404;
  - a grant for a turn of S3 sees B and not A (both ways);
  - `--search`, `--limit`, `--before` and the 200 cap;
  - a message in chat 1 bound to a task of B is listed with that task id,
    and `task <B's task>` is 404;
  - a `record/` path is answered by the kernel and never reaches a stub
    upstream;
  - an unknown or retired token is refused; a task with no project is 404;
  - nothing writes: `events` and `documents` counts unchanged after every
    call.
- The CLI against that gateway: line format, `--json`, a clear error with
  no `VALOR_RECORD_URL`.
- Memory: S1's turn's CLAUDE.md imports S2's MEMORY.md, not S3's or its
  own; a session started after it appears at the next turn; T gets it.
- Each harness's env carries `VALOR_RECORD_URL` for its turn.
- `tests/test_rollout.py`, beside its spec checks: each moved DM is
  in exactly one spec; Graham D's DM and email are in none.
- Live: Tom asks in Eng: Valor about Agent Builders' last messages and the
  answer comes from `valor-record`, recorded with the date.
