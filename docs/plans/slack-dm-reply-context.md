# Plan: a top-level Slack DM message keeps the posts it answers

Task 32e9c97a0869, from backlog:slack-dm-reply-context. Restarts frozen
task 133f2ae0965b, whose plan passed critique and review; this is that
design, cut to what the stripped `main` still has.

## The cause

`_bind` (`core/intake.py`) starts any message without `reply_to`, and
`_start` builds the instruction from the message text alone. The Slack
bridge sets `reply_to`/`topic_id` only on a thread reply, but on Slack
people answer in a DM top-level. On 2026-10-15 Tom's "sounds good! don't
stop for me" (received 34513) started c88779f3b787 blind, which asked him
what he had approved.

## What will be built (all in `core/intake.py`)

1. **`_preceding(conn, p)`.** Only for a record with no `reply_to` in Tom's
   own DM (`_toms_own`, `chat_kind == "dm"`) on a threaded channel (Slack).
   Valor's top-level posts in that chat (`_SENT` entries, `thread_ts` null
   or equal to their own ts) older than this message and newer than Tom's
   previous top-level `message.received` there (`topic_id` null); ts
   compared `COLLATE "C"` as `_replied` does. One item per notice or send
   (a send in parts counts once, at its first ts), newest five, oldest
   first. Each item: task id, kind (send, or the notice's `kind` and
   `about_key`), ts, text (a notice's from `notice.requested`, a send's
   from its `effect.intent`/`effect.held` `payload.text`).
2. **A live ask** is a carried `question` notice that is its task's newest
   carried post, whose task is `waiting` with
   `about_key == question:<open_question>`. (Delivered notices were removed
   by the strip, so there is no feedback case.)
3. **Binding.** When `_replied` finds nothing and `_preceding` returns
   posts: with **exactly one live ask**, bind `answer` to that task under
   its tree and task locks, re-checking it is still live after locking
   (else fall through), and owe a binding notice in reply to Tom's
   message: `notices.lead(headline, "I took your message as your answer
   here. If it was new work, send it again and it will start on its
   own.")`. Otherwise **start**, and `_start` appends to Tom's text:
   "Tom wrote this in his Slack DM, not in a thread, right after these
   posts of mine there, oldest first:" and one line per post (UTC time,
   task id, "still waits on Tom" for an open question, text via
   `tasks.quoted`). A top-level message never binds `stop` or `steer`.
4. **Docstring.** In the first-match table, after the reply rows: "Not a
   reply, in Tom's Slack DM, after exactly one live ask → `answer`, with a
   notice"; the start row becomes "`start`, carrying the DM's preceding
   Valor posts". Drop the stale "reply to the delivered notice → feedback"
   row (its code went in the strip). A paragraph defines the preceding
   posts and a live ask. `docs/bridges/slack.md` gets the same in its
   inbound-binding section.

## Out of scope

Telegram and email (replies are explicit there); Slack group channels
(others post between Valor and Tom); Valor's threaded posts (thread
replies bind through `_replied`); a model-judged binding of plain messages.

## Stakes and rounds

Wrong binding would put Tom's words on the wrong task, but every adjacency
binding notifies him and a start only gains text; no data or schema
change. Rule 9 makes this a bug-fix commit, and the design already passed
critique and review in 133f2ae0965b: 0 critique, 0 review rounds. No
governance is added.

## Tests (in `tests/test_kernel.py`, real Postgres; the 15-file budget is full)

Helpers: a Slack `Inbound`, and a ledger `notice.sent`/`effect.outcome`
writer for Valor's posts, with `operator_slack_ids` configured.

- **The incident:** a send from A (not waiting), a notice from B, then
  Tom's top-level text: `start`, instruction holds Tom's text and both
  posts oldest first, each naming its task.
- **Question then unrelated notice:** A waits on a question, B's notice
  follows; Tom's message binds `answer` to A, no task starts, and the
  binding notice goes to his DM with `reply_to` his ts.
- **Two live asks:** starts; both marked waiting; neither answered.
- **Stale question** (answered or not waiting) and **a question followed
  by its own task's later send:** start with context.
- **Window:** P1, Tom, P2, Tom → only P2 carried; a post after the message
  is not carried; seven posts → newest five; a send in two parts → one line.
- **Not carried:** a threaded post, another DM's post, a group channel.
- **Top-level `stop`** after a running task's send starts a task and stops
  nothing.
- A thread reply still binds through `_replied` as before.

Suites: the full suite (under 5 minutes) and `tests/test_budget.py`;
failures are compared against the base commit.
