# Ontologies

Valor's own vocabulary: one name per thing, so code, docs and Tom's words
agree. Each term says what it is, where it lives, and the names it is not.

## Room

A chat Valor serves, and what it is for: a Telegram group, a Slack
channel or DM, an email thread, Tom's own chat. Its purpose is one line,
the spec's `rooms` entry (Tom's own chat's is `TOMS_OWN_ROOM` in
`core/intake.py`), rendered as `Room:` on every turn of the chat's
session. Tom sometimes says **hat** for the role a room asks of Valor, the
role-in-room.

Not: "channel" (Telegram, Slack, email, local: the transport a room is
on), "chat session" (the task that serves a room, Tom's top agent).

## Role

How Valor acts in a room: what it takes up there, what may start from it,
and the frame it brings. One per room, named in the spec's `chat_roles`
and written in `skills/sdlc/roles/<role>.md`, rendered after the chat
session's `converse.md`:

- `engineer`: an Eng group or dev channel; engineering work starts here.
- `teammate`: a team chat, someone's DM, client email; no engineering
  starts here.
- `customer-service`: a customer's room.
- `product`: a product room; a product owner keeping the project's
  product Sheet (the `product-sheet` skill), who approves each item that
  passes its readiness review into the sibling dev room.
- `business`: a business room (legal, services); a coworker owning that
  area's paperwork, whose documents start here as tasks and live in the
  business's knowledge base folder.

Tom's own chats carry none: there Valor is his deputy, as the persona
says. A role is what Tom calls a **hat**: one person wearing many hats,
one per environment and topic; the hat is the role-in-room.

Not: "persona" (Valor's one identity, the same in every room; the old
system called roles "personas"), `roles` in a project spec (the
project's Postgres roles), `mentions` (whether a room waits to be named
or replied to, directly or down a reply chain from Valor's post, with each
other message asked `intake.addressed`; separate from its role).

## Top agent

Tom's name for a chat session: the one agent per room that carries the
conversation, speaks to people as Valor, and delegates. There is a top
per room, not one overall; the tops of a project share its record and
memory. In code it stays "chat session": a task with a chat, whose text
is `skills/sdlc/converse.md`, on the chat seat (`core/settings.py`
`SEATS["chat"]`, Fable 5.2 since 2026-11-16).

Below it:

- **task**: started by a chat session (`task.start`), reports to it
  through `.valor/`, and speaks to no one.
- **subagent**: a helper Claude Code starts inside a turn, at any depth;
  it reports to that turn.

The top agent is not the only one with the persona. `core/tasks.py`
renders `persona/` first into every turn's text, chat session or task
(the advisor's and a call worker's too). A subagent does not get it: in a
Claude Code turn, `VALOR_SUBAGENT_START` (`harnesses/claude_code.py`) holds
a worker's cut, the Brief's head, the corrections and the working rules
(`persona.worker`), with no voice, delivery, `converse.md` or role.
`skills/valor/hooks/subagent_start.py` gives it to every subagent at every
depth, whatever the room's role, except `valor:web`, which gets only its
prompt (Tom, 2026-11-16). What only the top agent does is speak as Valor to
people.

Not: "persona" (Valor's one identity, rendered into every turn but not
a subagent, above), "kernel" (the code that runs sessions and sends what
they ask for; it has no model), "room" (the chat a top agent serves).
