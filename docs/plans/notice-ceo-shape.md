---
tracking: backlog:notice-ceo-shape
slug: notice-ceo-shape
type: plan
status: planned
critique_rounds: 0
review_rounds: 1
---

# Notices read as a note from the deputy

Fixes the cause: the text builders. No filter, and nothing here adds a
check, gate, hook, approval step or guard.

**Premise, checked on the stripped main.** The delivered notice and the
governance refusal notices, with their `python -m core grant` command, are
gone. So the frozen branch's approve-by-reply path is not needed, and it is
not carried. What is left that puts internal detail in front of Tom:

| Where | Today | After |
|---|---|---|
| `notices.request` | appends `[n:<id>]` to every text | text as written; the id stays in the row |
| `notices.owe` question | `Task <id> asks:` | `lead(head, "I have a question for you.")`, the question, "Reply to this message to answer." |
| `ended_text`, merge `report` | end with the `127.0.0.1` status-page link; `step` kind names the stage and a raw error | no link; `step`/`provision`/`merge`/other say what happened in plain words with no stage name or raw error; a turn's own note stays; the ended notice says a reply is taken as feedback |
| `credential` | raw push error line | the error line dropped; the one ask stays |
| send `report` | `Task <id> sent email.send to ...` | `lead(head, "I sent an email to a@b:")` (Telegram / Slack message by name); a reply-all reads "a reply to everyone on the email thread" |
| `_unstorable` | task id, kind, about_key, jsonb wording | `lead(head, ...)`: I had a message for Tom about this work I could not store; the record of the work holds it |
| `intake` reply to merged/stopped work | `Task <id> is merged; nothing was done.` | `lead(head, "this work is already merged/stopped, so your reply changed nothing.")` |
| `intake` not-a-stop / bind-failed | message id, raw exception | plain: "Your message was not acted on." (exception stays in `message.bound`'s `error`) |
| `serve` rollout (both) | task id, sha, step name, raw reason | `lead(head, "it is merged, but I could not put it live ...")`, tries, retried every N seconds; mixed-code case said plainly |
| email `in_doubt` | `(effect <id>)` | id dropped, rest kept, opened with `lead(head, ...)` |

`report_link` and `tag` are deleted (no other caller). The module docstring
loses "links the status page" and "each text carries the short id".

**Out of scope.** Turn-written notes (`done.md`, `stop.md`): they are Valor's
own words. Approve-by-reply. The status page itself.

**Known edge.** Telegram's crash recovery (`_scan`) matches a notice's text in
the chat; the tag made two identical texts distinct. Without it, two
identical notices both in flight across a crash read as ambiguous (`Unknown`,
nothing sent twice). Headlines make identical texts rare; accepted.

**Tests** (no new file; budget is 15):
- `tests/test_telegram_pipeline.py`: one test builds a task with a
  headline and renders each builder (question, every `ended` kind, merge and
  send reports for email/Telegram/reply-all, credential, unstorable, the
  intake reply to merged and stopped work, rollout and mixed rollout, email in
  doubt); each opens with `On "<headline>":`, and none contains the task id,
  notice id, effect id, sha, `[n:`, `127.0.0.1`, `python -m`, or a `/` path.
- A task with no Brief: the sentence is capitalised, no `On ""`.
- `tests/test_telegram_outbox.py:64`: asserts the sent text equals the
  requested text (the tag assertion moves).
- Full suite under 5 minutes; test lines stay under production.

**Stakes.** Text only, reversible by revert; a wrong word reaches Tom, no data
or merge path changes.
