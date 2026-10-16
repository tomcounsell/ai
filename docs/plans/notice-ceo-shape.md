---
tracking: backlog:notice-ceo-shape
slug: notice-ceo-shape
type: plan
status: planned
critique_rounds: 0
review_rounds: 1
---

# Notices read as a note from the deputy

Fixes the cause: the text builders. No filter; nothing adds a check, gate,
hook, approval step or guard. `head` below is `notices.headline`; `lead(head, s)`
opens with `On "<head>": ` (capitalised `s` when there is none).

**Premise, checked on the stripped main.** No delivered or governance-refusal
notice exists, so no grant command reaches Tom; the frozen branch's
approve-by-reply path is not carried. What is left:

| Where | After |
|---|---|
| `request` | no `[n:<id>]`; the id stays in the row. `tag`, `report_link` deleted |
| question | `lead(head, "I have a question for you.")`, the question, "Reply to this message to answer." |
| ended, any kind | no status link; a turn's own note stays; ends "Reply to this message if you want me to pick it up again or change course." (a reply is already feedback) |
| ended `step` | check in plain words (test: "its tests", review: "its review", docs: "its documentation check", else "one of its steps") "failed N times in a row, the same way each time"; raw error stays in the ledger |
| ended `provision` | "I could not set up a copy of the project for it to work in, after N tries" |
| ended `merge` | `verdicts.end` adds `why` = `merge_failure(last)` to the payload. moved: "the branch it merges into kept moving and I could not bring the work up to date with it". refused: "the merge was refused, and trying again would get the same answer". transient: "the merge failed N times in a row on errors from GitHub or the network". No `why` (old rows): "I could not merge it". All add "The work stays on its branch." |
| `credential(conn, task, sha, missing: bool)` | missing (`credential_missing` set): "no GitHub push token is set up on this machine". Refused (push error): "GitHub refused the push token on this machine; it may have expired or lost access to this repository." Then "Once a working token is in place I merge it on my own." No key-file path |
| send `report` | `lead(head, "I sent <what> to <who>:")`, body, file names. Email: the addresses. Reply-all: "a reply to everyone on the email thread". Telegram/Slack: the project whose `Spec.chats` holds `"<channel>:<target>"`, as "the <project> chat on Telegram/Slack", else "a Telegram chat" / "a Slack channel"; never the id |
| `_unstorable` | `lead(head, f"I had a {plain kind} message for you about this work that I could not store, so here is this note instead. The record of the work holds what it is about.")` |
| intake, merged/stopped | `lead(head, "this work is already merged/stopped, so your reply changed nothing.")` |
| intake, bind failed | `Your message "<short(text)>" was not acted on: something went wrong reading it. Send it again, or reply stop.`; with a task, led by head. Exception stays in `message.bound.error`; `headline` of a channel name returns None (KeyError) |
| intake, not a stop | replying channel: `lead(head, "that was not a stop, so your message steers the work. To stop it, reply stop.")`; email: `lead(head, f"stops come by reply {_where()}; your email steers the work.")` |
| serve rollout | `lead(head, f"it is merged, but I could not put it live on my own system: the step that failed is {plain step}, N times. I try again every S seconds.")`; mixed: `lead(head, "it is merged, but putting it live failed partway and I could not undo it, so my system runs a mix of old and new code. I try again every S seconds.")`. No task id, sha, raw reason |
| email in doubt | `lead(head, f"the email to {target} may or may not have gone ...")`; no effect id |

Docstrings: `core/notices.py` (no status link, no short id in text) and
`bridges/telegram/send.py:29-31`: the whole-chat lookup of a notice whose
record was lost relies on every earlier send being claimed by its recorded
`sent`; an unclaimed identical text is ambiguous and settles `Unknown`.

**Known edge, stated plainly.** Telegram recovery (`_scan`) matches a notice by
text; the tag made texts unique. Two identical notices in the same chat both in
flight across a crash, or one whose start record was lost beside an identical
unclaimed message, give `Unknown`: logged once, retried every wake, never
delivered, and nothing tells Tom. Texts that do not vary are where it can
happen; so every notice with a task leads with its headline, and bind-failed
quotes Tom's own message. Accepted.

**Out of scope.** Turn-written notes; approve-by-reply; the status page.

**Tests** (no new file; budget is 15), in `tests/test_telegram_pipeline.py`:
- One test, task with a headline, renders: question; ended `step`, `provision`,
  `merge` (each `why` and none), `rounds`, `decided`; credential missing and
  refused (different sentences); merge report; send reports for email,
  reply-all, Telegram to a project chat and an unknown chat, Slack; unstorable;
  intake merged, stopped, not-a-stop (replying and email), bind-failed with a
  task; rollout and mixed rollout; email in doubt. Each opens `On "<headline>":`;
  none contains the task id, notice id, effect id, sha, Telegram/Slack target
  id, `[n:`, `127.0.0.1`, `python -m`, a key-file path or a `/`-path.
- Bind-failed with no task (task id is the channel name): no raise, no `On ""`,
  quotes the message.
- `tests/test_telegram_outbox.py:64`: sent text equals requested text.
- Full suite under 5 minutes; test lines stay under production.

**Stakes.** Text, plus one additive `why` field on `task.ended`; reversible by
revert. A wrong word reaches Tom; no merge or send path changes.
