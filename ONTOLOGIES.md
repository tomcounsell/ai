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
on), "chat session" (the task that serves a room).

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

Tom's own chats carry none: there Valor is his deputy, as the persona
says. A role is what Tom calls a **hat**: one person wearing many hats,
one per environment and topic; the hat is the role-in-room.

Not: "persona" (Valor's one identity, the same in every room; the old
system called roles "personas"), `roles` in a project spec (the
project's Postgres roles), `mentions` (whether a room waits to be named,
which is separate from its role).
