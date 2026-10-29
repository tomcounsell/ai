# Each chat ("room") carries a description of its purpose on every turn

**Ask.** Tom, valor team chat, 2026-10-17: "at least the separate chat
sessions should understand the purpose of the chat env ... in my DM with
Valor, that chat will be about running the business"; "along with persona
and standing rules, each 'room' should have a description that anchors the
purpose"; "make an inventory of all the 'rooms' and propose descriptions".
The inventory was posted (message 1584) and he moved on without changes.

**Premise, checked.** `tasks.dispatch` renders the persona, then the Brief
head (`Task`, `Instruction`, ...), then the corrections, on every turn; the
text is Claude Code's `--append-system-prompt` (Pi's likewise), re-rendered
each resumed turn, so compaction never touches it. A chat session's
Instruction is its opening (`intake._chat_start`), fixed when the session
started: project and kind only. Specs list chats as strings
(`Spec.chats`); Tom's Slack DMs and the local chat are in no spec and are
resolved by `intake._toms_own`.

## What is built

1. **Where descriptions live.** A new spec key `rooms`, a table from a
   chat (keyed as in `chats`, normalised the same way) to its description,
   beside `chats` in each `projects/*.toml`. Parsed in `Spec.from_dict`
   like `mentions`, with no new refusal. The inventory's descriptions go in
   verbatim: operator group and Eng: Valor in `valor.toml`; popoto,
   tailspin, fabrikam, wingtip, orbit-os, home-lab, yudame in
   their own files.
2. **Tom's own chats and email.** Two constants in `core/intake.py`, where
   Tom's own chat is resolved: `TOMS_OWN_ROOM` ("running the business with
   Tom, Yudame first; engineering only when he starts it") and `EMAIL_ROOM`
   ("Valor's own correspondence for the business"). The DM `963835355`
   stays listed in `valor.toml` with no `rooms` entry, so one text covers
   his Telegram DM, Slack DMs and the local chat.
3. **`intake.room(channel, chat_id) -> str | None`.** First a `rooms` entry
   of a spec this machine owns (`_project_chats`), so the operator group,
   which is also Tom's own, gets its own text; else `TOMS_OWN_ROOM` when
   `_toms_own`; else `EMAIL_ROOM` on `email`; else None.
4. **Rendered every turn.** `tasks.dispatch`, for a Brief with `chat`, adds
   `Room: <description>` to the Brief head after `Instruction`, read now
   (as the persona is), so an open session gets it at its next turn and an
   edited description reaches the next turn. None adds nothing: the
   opening's project and kind stay the fallback, as today. `intake`
   imports `tasks`, so `dispatch` imports `room` inside the function.
5. **Docs.** `projects/README.md` (the key), `Spec` docstring,
   `docs/bridges/telegram.md` (team chat and Tom's own chat paragraphs),
   `skills/sdlc/converse.md` (the Room line says what the chat is for and
   anchors what you take up there), `docs/persona.md` and
   `docs/harnesses.md` where the dispatched text is described.

## Out of scope

The opening text itself; titles read from Telegram; a description for the
`skills` project (it lists no chat); per-mailbox email descriptions (one
mailbox today); anything about wake-ups (#3654).

## Tests (in `tests/test_kernel.py` or beside the intake tests)

- `Spec.from_dict` with `rooms`: a key in a non-normal form (`telegram:`
  id with spaces or Slack lowercase) matches its chat; a spec without
  `rooms` loads as before.
- `room`: a listed chat with a description gets it; the operator group
  gets its spec text, not `TOMS_OWN_ROOM`; Tom's Telegram DM (listed, no
  entry), his Slack DM and the local chat get `TOMS_OWN_ROOM`; an email
  thread gets `EMAIL_ROOM`; a listed group with no entry, and an unlisted
  group, get None; a chat listed by a spec of another machine gets None.
- `dispatch`: a chat session's text carries `Room: ...` after its
  Instruction, on the first and on a later turn; a session started before
  the description existed carries it once it does; a task with no `chat`
  carries no Room line; a chat with no description carries none.
- Every `projects/*.toml` loads, and each chat the inventory names
  resolves to its description (the real files, so a typo in an id fails).

## Done

Tom's words: this chat's and Tom's DM's next sessions carry their room
description on every turn, and Tom uses the DM for business with it. The
suite shows the first; the second is recorded when he has.
