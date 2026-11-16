# "Top agent" in the vocabulary

Tom (Slack, valor team chat, 2026-11-16 12:37 UTC) accepted: "chat session"
stays the code's word; **top agent** is his word for it, as "hat" is his word
for a role. Docs only; no code renames, no new check.

## What is built

1. **`ONTOLOGIES.md`, a `## Top agent` entry** after `## Role`, in the form of
   the existing ones (what it is, where it lives, the names it is not):
   - What: Tom's name for a chat session, the one agent per room that carries
     the conversation, speaks to people as Valor, and delegates. One per room,
     not one overall; the tops of a project share its record and memory.
   - Where: a task with a chat (`b.chat`), its text `skills/sdlc/converse.md`,
     its model the chat seat (`core/settings.py` `SEATS["chat"]`, Fable 5.2
     since 2026-11-16).
   - The levels below it: a **task** (started by a chat session, reports to
     it through `.valor/`, never speaks to people), then a **subagent** (a
     helper Claude Code starts inside a turn, reports to that turn).
   - Who gets the persona, as the code does it: `core/tasks.py` renders
     `persona/` first into every turn's text, chat session or task (the
     advisor's and a call worker's too); that whole text, persona included,
     is what `harnesses/claude_code.py` puts in `VALOR_SUBAGENT_START`, which
     `skills/valor/hooks/subagent_start.py` prints for every subagent at every
     depth except `valor:web`, which gets only its prompt. So the top agent is
     not the only one with the persona; it is the only one that speaks as
     Valor to people.
   - Not: "persona" (rendered into every turn and subagent above), "kernel"
     (runs the sessions and has no model), "room" (the chat it serves).
2. **`skills/sdlc/converse.md`, one phrase** in its opening sentence: "This
   session is a chat's, the one Tom calls the top agent, and lasts as long as
   the chat". Dropped if a test asserts that sentence.

## Out of scope

No rename in code or other docs; no new check, test or mechanism.

## Tests

None added (docs only). No test reads `ONTOLOGIES.md` (grep of `tests/`).
If `converse.md` changes, run `tests/` for anything asserting its text, and
the full suite before merge; separate pre-existing failures.
