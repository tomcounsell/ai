# A task's context, written by the chat that started it

**Ask.** Tom, valor team chat, #have-way-add, 2026-11-03 04:58 UTC: when a chat
session starts a task, the task gets "at least a couple lines of environment
info", and that context "can be a custom written one by you". The case: a room
whose description says what Valor may and must not do (publish a staging prompt
version, open a prompt-text PR; never change prod, never merge), and the task
that does the work never sees that.

**Premise, checked.** `tasks.dispatch` renders `Room:` only when the Brief has
`chat` (`core/tasks.py`, the `if b.chat:` block); a task started by
`task.start` (`tools/chat_tasks.py`, `TaskStart.perform` → `tasks.start_child`)
has none, only `instruction` and `project.chat` (which chat it reads). The
SubagentStart hook (`skills/valor/hooks/subagent_start.py`) already gives every
subagent the dispatched text, so whatever the Brief renders reaches them.

## What is built

1. **The field.** `Brief.context: str | None = None` (`core/tasks.py`), the
   few lines the session wrote. Written once with the document, so it holds for
   every stage, revision round, fresh session (critique, review, advisor) and
   subagent. Old documents load unchanged (`Brief.load` defaults it).
2. **The render.** `dispatch` adds `Context: <text>` right after the
   `Instruction:` line whenever the Brief has one, for every session kind
   (a reviewer holds the work against it too).
3. **The effect.** `task.start` takes `"context"` in its payload, stripped,
   passed to `start_child`; blank or absent stores nothing. Its usage line
   says what to write: the chat and room the task came from, who asked, and
   what the room allows and forbids that bears on this work, from its `Room:`
   and role text, in a few lines.
4. **The guidance.** `skills/sdlc/converse.md` (the chat session's text): every
   task you start gets a `context` you write for it, as above. `persona/conduct.md`
   "In a team chat": the task you start carries a few lines of where it came
   from and what the room allows. "Instructions come from Tom and the Brief"
   already makes it binding: it is in the Brief. `docs/persona.md`'s Brief
   paragraph names the `Context:` line.

**Why not the Room line.** `Room:` is the spec's text, resolved live for the
chat a session belongs to. The context is the session's own words for one task,
fixed at start (Tom asked for exactly that). Different source, different
lifetime; reusing the label would make a child look like a chat session. No
second mechanism: nothing else carries per-task environment today.

**Out of scope.** No check that a task obeys its context, no refusal of a
`task.start` without one, no copying of the room text by the kernel (the
session chooses what bears on the work). Writing Tom's "may / must not" text
into a room's spec waits on his answer to which channel it is for (asked in
the thread). No new governance: guidance and a field.

## Tests (`tests/test_kernel.py`)

- A `task.start` with `"context"`: the child's Brief has it, and its dispatched
  text has `Instruction: …\nContext: <text>\nEffect ceiling:`; a fresh
  (review) session's text carries it too.
- Without `"context"`, or with only whitespace: no `Context:` line.
- With `"in"` a sibling chat: the context reaches that child as well.
- A stored document without the key loads (`context is None`).
- Full suite under 5 minutes.

**Done** when a task started from a room with limits shows that context in its
Brief and Tom sees a task respect it (recorded with the date).
