# A chat session wakes itself (#3654)

**Ask.** Tom, valor team chat, 2026-10-17: "you should have a natural timer
set whenever you ask a question to wake yourself and decide whether it's
worth waiting for an answer". His answers, 2026-10-29 07:17-07:24 UTC:
- A real question waits for an answer, or for his positive reaction. He
  "might be offline for a day", so the session judges whether waiting is safer.
- An open "anything missing?" waits 5 minutes, so he can say "let's chat
  about it".
- Only a message in the post's thread, or a reply to it, cancels the timer.
- Chat sessions only. Tasks wait on their questions indefinitely, and the
  session checks in on tasks that have gone quiet.

**Premise, checked.** A chat turn is a one-shot `claude -p --resume`, so a
ScheduleWakeup dies with it. The kernel already steps a task on a timer
(`router.retry_due`, every `serve_tick_s`). On Slack, Tom's reaction on a post
arrives as a reply to it (`bridges/slack/inbound.py`). Telegram reactions are
out of scope.

## What is built

1. **`.valor/wake.md`** (a text signal). Its first line is a delay (`90s`,
   `20m`, `2h`, minutes), optionally followed by `post: <this turn's effect
   file>` or `post: <an earlier post's message id>`. The lines after it are a
   note. It counts only from a finished chat turn and is recorded as the
   collected row's `wake`.
2. **`session.wakes(rows)`.**
   - **Its post.** A file is joined through the collected entry's
     `effect_id` to that effect's `effect.outcome` `result.sent` parts (their
     `message_id` and `thread_ts`). An id gets the thread of the part that
     sent it, so a re-judge can name its question's post.
   - **What cancels it.** A message replying to the post, or to its thread,
     drops it. Other messages and reports leave it.
   - **Replacing.** A new wake on the same post replaces the old one, and a
     no-post wake replaces the earlier no-post wake.
   - **Posts that didn't go out.** A refused or failed send still wakes and
     says so. So does a held one ("has not gone out yet").
3. **Due and spent.** A wake is owed once its delay has passed. It is due to
   be stepped while no turn has started since then. It is spent by a finished
   turn whose prompt gave it (`woke` on that turn's collected row), so a
   failed wake turn retries on `retry_due`'s backoff and keeps the wake.
4. **Scheduling.** `router.has_step` and `serve.Kernel._ready`'s no-new-rows
   gate both take `session.wake_due`.
5. **The prompt.** `# Your wake-up` gives each owed wake's delay, the end of
   the turn that set it in Tom's time from the persona's zone (13:40 Tom's
   time, 06:40 UTC), what became of its post, and the note.
6. **`skills/sdlc/converse.md`** covers the three cases:
   - **A real question:** silence is never go, and a positive reaction is
     yes. By default the session keeps waiting and re-sets the wake on the
     same post. When waiting is plainly less safe and the default is easy to
     undo, it posts the "Anything I missed?" line.
   - **The read-back:** a 5-minute wake, then go.
   - **A handed-off task:** a wake with no post to check on it. Its note
     lists every task still to check.

**Governance.** None added. A wake holds, refuses and redirects nothing.

**Tests** (`tests/test_wake.py`, `tests/test_serve.py`, `tests/test_kernel.py`):
the delays, each cancel and replace rule, and posts that didn't go out. The
due and spent timing and the prompt text. One run through `session.run`. The
kernel stepping a listening session when the wake falls due and not before.
The guidance.

**Done when** (unchanged): in the valor team chat a real session asks Tom a
question, he says nothing, it wakes on its own, posts the "anything I
missed?" line and goes ahead. The exchange that meets it:
- The session asks a question whose default is easy to undo.
- It wakes and judges that waiting is not worth it.
- It posts the line with a 5-minute wake.
- After five quiet minutes it starts.
