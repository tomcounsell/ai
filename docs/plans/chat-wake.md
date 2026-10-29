# A chat session wakes itself (#3654)

**Ask.** Tom, valor team chat, 2026-10-17: "you should have a natural timer
set whenever you ask a question to wake yourself and decide whether it's
worth waiting for an answer". His answers, 2026-10-29 07:17-07:24 UTC:
- A real question waits for an answer, or for his positive reaction on it.
  He "might be offline for a day", so the session judges whether waiting is
  safer.
- An open "anything missing?" waits 5 minutes, so he can say "let's chat
  about it".
- Only a message in the post's thread, or a reply to it, cancels the timer.
- Chat sessions only: "your sub agents should probably wait for you
  indefinitely and you should also check in on them when they've gone quiet".

**Premise, checked.** A chat turn is a one-shot `claude -p --resume`, so a
ScheduleWakeup or CronCreate dies with it. The kernel already steps a task
on a timer (`router.retry_due`, every `serve_tick_s`, 60s). A wake is one more
reason in the same two places. On Slack, Tom's reaction on Valor's post
already arrives as a reply to it (`bridges/slack/inbound.py` `reaction`), which
wakes the session and cancels a wake on that post. Telegram passes on no
reactions, and that stays out of scope.

## What is built

1. **The request.** `.valor/wake.md` (`core/signals.py` `TEXT_SIGNALS`):
   - First line: a delay, `90s`, `20m`, `2h` or bare minutes, optionally followed by
     `post: <effect file>` naming the same turn's send that the wake
     watches.
   - The lines after it: a note to itself.
   - It counts only from a finished chat turn (`_stage_verdict`). Elsewhere, or
     with an unreadable delay, it goes to `errors`.
   - `_collected` records `"wake": {"after_s", "post", "note"}`.
2. **Pending wakes.** `session.wakes(rows)` lists them, oldest first.
   - Each `turn.collected` with a wake adds one. It replaces an earlier
     wake on the same post, and a wake with no post replaces the earlier
     wake with no post.
   - The post's ids come from the `effect.outcome` whose `request_id` is
     `<turn_id>/<post file>` (the join `session.record` already writes,
     `core/session.py:819`): the `message_id` and `thread_ts` of each part
     it sent. A held send (no outcome yet) fires on time, and the prompt
     says the post has not gone out yet.
   - A `message.steered` or `message.heard` whose `reply_to` or `topic_id` is
     one of them cancels the wake. Nothing else does, a task's report
     included, and a wake outlives unrelated turns.
   - A send that was refused or failed leaves the wake with no ids. It
     fires as usual, and its prompt says the post never went out.
3. **Due, then spent.** A wake is due once `at + after_s` has passed and no
   turn has started since. It is spent once a turn that started after
   that time finishes (`turn.collected`). Its prompt is still owed until then,
   so a failed wake turn's retry (`retry_due` backoff) still carries it, and
   is not stepped every tick.
4. **Scheduling.** Both gates take `session.wake_due`:
   - `router.has_step` becomes `not chat or chat_waiting or wake_due or
     retry_due`.
   - `serve.Kernel._ready` (`core/serve.py:1012`) skips a task with no new
     rows `and not (retry_due or wake_due)`.
   - The `core/serve.py` docstring and the `has_step` line in
     `core/README.md` say so.
5. **The prompt.** For a chat, `next_prompt` adds `# Your wake-up`, one line
   per owed wake, for example: "You asked to be woken 5 minutes after your
   post 1793…, which went out at 13:40 Tom's time (06:40 UTC). Nothing in
   its thread since. Your note: …". The other messages since show above it,
   as now.
   - Tom's time comes from `persona.identity`'s `timezone`, through the zone
     named in its parentheses (`zoneinfo`).
6. **The guidance**, in `skills/sdlc/converse.md` item 2:
   - **A real question:** silence is never go. A positive reaction from
     Tom on it is yes/ok. Set a wake on the question's post to re-judge.
     On the wake, the default is to keep waiting, since he may be offline
     for a day, and to set a new wake on the same post so it judges again.
     When waiting is plainly the less safe choice and the default is easy
     to undo, post in its thread "Anything I missed? If not, I'll go ahead"
     with the default named, and a 5-minute wake on that post: the next case.
   - **"Anything I missed? If not, I'll go ahead":** set a 5-minute wake on
     that post. Silence then is go. Any reply in its thread ("let's chat
     about it") means don't go ahead.
   - **Work handed to a task:** a wake with no post can check on it later.
     On waking, if the task has gone quiet, read its record (`valor-record
     task <id>`) and steer it to ask how it's going or whether it's
     waiting. A new no-post wake replaces the old one, so its note lists
     every task still to check.
   - The signals line names `wake.md`. Docstrings in `core/signals.py` and
     `core/session.py`, and `docs/bridges/telegram.md`'s chat-session
     paragraph, say when a session is stepped.

**Out of scope.** Tasks: a task waiting on its question waits indefinitely,
as now. Bounds on the delay. Telegram reactions.

**Governance.** None added. A wake holds, refuses and redirects nothing.
It only adds one more reason the kernel steps a listening session.

## Tests (`tests/test_steering.py`, `tests/test_serve.py`)

- Delays: `90s`, `20m`, `2h`, `25` parse, and garbage goes to `errors`. A
  wake from a non-chat turn goes to `errors`, and one from an unfinished
  chat turn is not recorded.
- Timing: a wake is not due one second before its delay and is due at
  it. Once a turn has started, it is no longer due but its prompt is still
  owed. After a finished turn, it is spent.
- What cancels: a reply or thread message on its post (a reaction row
  included) cancels it. A message elsewhere in the chat and a
  `child.reported` do not, and that message shows in the wake prompt.
- Replacing: a new wake on the same post replaces the old one. Wakes on two
  posts are both kept. A no-post wake replaces the earlier no-post wake.
- A refused send: the wake still fires, and its prompt says the post never
  went out. A held send: it fires, saying the post has not gone out yet.
- Re-judging: a wake turn that sets a new wake on the same post leaves that
  wake pending after the first is spent.
- The kernel: in `test_serve.py`, a listening chat session with no rows
  since `seen` is started once its wake is due, and not one tick before.
  A failed wake turn is retried on backoff, and its prompt still carries
  the wake.
- The prompt pins 06:40 UTC as 13:40 Tom's time, and names the delay and
  the note.
- `converse.md` carries both cases, re-setting the wake, the 5 minutes, and checking in on a
  quiet task. The full suite runs in under 5 minutes.

**Done when** (unchanged, #3654): in the valor team chat a real session
asks Tom a question, he says nothing, it wakes on its own, posts the
"anything I missed?" line and goes ahead. The exchange that meets it is
item 6's first case running into its second, as in Tom's 2026-10-17 words:
- Valor asks a question with an easily undone default and sets a wake.
- Tom says nothing. Valor wakes, judges waiting is not worth it, and
  posts "Anything I missed? If not, I'll go ahead" with a 5-minute wake.
- Five quiet minutes later it wakes again and starts the work.

It needs the merge, so the delivery names it as open, and names this
reading.
