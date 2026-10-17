# Revision workers: a chat's word on a delivered task reopens it

Task 5f113916825f. Tom, valor team chat, 2026-10-17: "For each presentation
job it would be a new worker created, but reusable for revisions. Go ahead
and implement it that way."

## The gap (checked in the code)

- `tools/chat_tasks.py` `TaskSteer.perform` only appends `message.steered`.
  On a task in `merged` or `ended` nothing reads it; the task never runs again.
- `core/session.py` `feedback` already reopens a delivered task (`merge`,
  `merged`, `ended` → `patch`, new loop window, same workspace). Only
  `core/intake.py` `_bind` calls it, for Tom's direct reply to the task's
  report (`merged`) or notice (`ended`).
- A deliverable task that finishes with `.valor/stop.md` ("Done: ...") ends
  in `ended` (`core/session.py` verdict `ended`), so feedback reaches it.
- The worker keeps its conversation: the fold keeps `f.session` across
  `feedback.given`, and `next_prompt` resumes it (`--resume`), as
  docs/architecture.md says ("a patch is that session resumed"). The
  workspace is removed only by Tom's `workspace remove`. So the record, the
  workspace and the Claude Code conversation all carry over. Nothing to build
  for that; a test pins it.
- A second report reaches the chat session: an `ended` notice is keyed
  `ended:<row>`, and delivered/report notices per candidate/merge, so each
  round reports again.

## What will be built

1. **`task.steer` binds like a reply does.** In `TaskSteer.perform`, under the
   tree and task locks it already takes, fold the task; in `merged` or
   `ended` call `session.feedback(conn, task, text, by=<by>, via="chat",
   effect_key=key)` instead of appending `message.steered`, and return
   `{"task_id", "feedback_id"}`. Every other state (running, `merge`,
   `waiting`, `stopped`) stays a steer, as today. A stopped task stays
   stopped: feedback is never called on it, and an ended task under a stopped
   ancestor fails with feedback's own error, which the session sees as the
   effect's outcome.
   *Why routing in `task.steer`, not a new `task.feedback`:* the states map
   exactly onto `_bind`'s rows for a direct reply (a reply to a `merged` or
   `ended` task is feedback, otherwise steer), so one effect means "what the
   chat said about this task" and the kernel picks by state, the same way
   for both surfaces. A second effect would be a second way to pass the
   chat's words to a task, and choosing the wrong one (a steer on an ended
   task) would lose the words silently, which is today's bug.
2. **`session.feedback` takes an optional `effect_key`**, stored on
   `feedback.given`, so `TaskSteer.lookup` reconciles a dangling intent by
   key on either row. Matching by text (as `task.answer` does) would skip a
   second round that repeats the first round's words.
3. **The patch prompt names who gave the feedback.** `_entry_prompt` says
   "# Tom's feedback on the delivery" for every `feedback.given`; it becomes
   "# Feedback on the delivery from <by>, via <via>" from the row's
   provenance, so a teammate's word in a team chat is not read as Tom's.
4. **Text.** `TaskSteer.usage`: "gives that task what the chat added; to a
   task that has delivered (merged or ended) it is feedback, and the task
   reopens in its own workspace and conversation to revise and report again."
   `skills/sdlc/converse.md` item 3: when the chat comments on or asks to
   change something a task delivered, `task.steer` that task rather than
   starting a new one; a new task only for new work. Module docstring of
   `tools/chat_tasks.py`, docs/architecture.md (inbound binding paragraph)
   and the `core/intake.py` binding docstring note that a chat session's
   steer of a delivered task is feedback.

## Out of scope

- A steer on a task in `merge` (delivered, merge pending) stays a steer, as a
  direct reply does today.
- Reaching tasks that report to an *earlier* session of the same chat. If the
  live check in Done shows bde12e594236 does not report to the current team
  chat session, the done run uses a task the current session started, and
  the gap is named in the delivery.
- No new state, effect, check or refusal. No governance is added.

## Stakes

A chat's word on finished work now reopens it instead of vanishing; a wrong
state mapping would reopen the wrong task or lose words, and it is reversible
in one commit.

## Tests (tests/test_kernel.py, beside the existing chat-task test)

- A `task.steer` on a child in `ended`: a `feedback.given` row with `by`, `via
  chat` and the effect key, no `message.steered`, the task folds to `patch`,
  and `next_prompt` resumes the same harness session with the feedback
  prompt naming the sender.
- The same on a child in `merged` (with a merge outcome).
- `stopped`: the steer stays `message.steered`, state stays `stopped`.
- Running (`build`) and `merge`: unchanged, `message.steered`.
- Ended child under a stopped parent: the perform raises feedback's error,
  nothing appended.
- `lookup` finds the feedback by key; a second round with identical text and
  a new key is a second `feedback.given`, not found by the first key.
- A task that does not report to the session is refused, as now.
- Second round end to end with the scripted harness: ended → steer → patch →
  `stop.md` → `ended` again, and the session gets a second `child.reported`.

## Done

In the valor team chat, Tom asks for a revision of something a task
delivered (the bde12e594236 video if it reports to the current session),
the same task id revises it in its workspace, and Tom receives the revised
file. Recorded with the date when he has.
